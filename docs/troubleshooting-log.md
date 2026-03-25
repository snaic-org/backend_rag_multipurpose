# Troubleshooting Log

This file records concrete errors encountered during setup and deployment, with the likely cause and the fix that resolved them.

## Docker and local runtime

### Nginx startup error: `"worker_processes" directive is not allowed here`

Error:

```text
/docker-entrypoint.sh: Configuration complete; ready for start up
2026/03/19 16:00:56 [emerg] 1#1: "worker_processes" directive is not allowed here in /etc/nginx/conf.d/default.conf:1
nginx: [emerg] "worker_processes" directive is not allowed here in /etc/nginx/conf.d/default.conf:1
```

Cause:

- a full top-level `nginx.conf` was rendered into `/etc/nginx/conf.d/default.conf`
- `conf.d/default.conf` only accepts `server`-level config, not top-level directives like `worker_processes`

Solution:

- render the template into `/etc/nginx/nginx.conf` instead
- start Nginx with a custom `CMD` after `envsubst`

Relevant file:

- `backend/nginx/Dockerfile`

### Compose startup failure: `dependency app failed to start: container rag_app is unhealthy`

Error:

```text
Container rag_app Error dependency app failed to start
dependency failed to start: container rag_app is unhealthy
```

Cause:

- the app container was given `APP_PORT=9010`
- the app healthcheck and Nginx upstream were still targeting `8000`

Solution:

- set container `APP_PORT` to `8000` inside `backend/docker-compose.yml`
- keep host exposure on Nginx via `HOST_PROXY_PORT`

Relevant file:

- `backend/docker-compose.yml`

### Local Docker ignored edited `backend/.env`

Symptom:

- local config changes in `backend/.env` did not take effect
- the container still behaved as if `.env.example` values were active

Cause:

- `backend/docker-compose.yml` loaded `.env.example` as the app `env_file`
- editing `backend/.env` therefore had no effect on the container

Solution:

- change the Compose app service to load `.env`
- keep `.env.example` only as the template to copy from

Relevant file:

- `backend/docker-compose.yml`

### Docker tag failure: `No such image: rag-backend:latest`

Error:

```text
Error response from daemon: No such image: rag-backend:latest
```

Cause:

- the local image had not been built yet under that tag

Solution:

1. Build the image first:

```powershell
docker build -f backend/Dockerfile -t rag-backend:latest backend
```

2. Verify it exists:

```powershell
docker images | findstr rag-
```

3. Then tag and push it to ECR.

Relevant file:

- `deploy/ecs/README.md`

### Docker push failure: `no basic auth credentials`

Error:

```text
no basic auth credentials
```

Cause:

- Docker was not logged in to the ECR registry
- `aws login` does not authenticate Docker for `docker push`

Solution:

Authenticate Docker to ECR first:

```powershell
aws ecr get-login-password --region ap-southeast-1 | docker login --username AWS --password-stdin 961341555117.dkr.ecr.ap-southeast-1.amazonaws.com
```

Then push the image again.

Relevant file:

- `deploy/ecs/README.md`

## ECS and Fargate deployment

### ECS service launch failure: unable to assume `ecsTaskRole`

Error:

```text
(service backend-rag-multipurpose) failed to launch a task with
(error ECS was unable to assume the role
'arn:aws:iam::...:role/ecsTaskRole' ...)
```

Cause:

- `ecsTaskRole` did not exist, or
- its trust relationship did not allow `ecs-tasks.amazonaws.com`, or
- the deploying identity did not have permission to pass the role

Solution:

- create `ecsTaskRole`
- set its trust policy to allow `ecs-tasks.amazonaws.com`
- if deploying with a non-root IAM identity, also allow `iam:PassRole`

Trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

Relevant file:

- `deploy/ecs/README.md`

### Fargate task startup failure: `AccessDeniedException` for `ssm:GetParameters`

Error:

```text
ResourceInitializationError: unable to pull secrets or registry auth:
unable to retrieve secrets from ssm ...
AccessDeniedException:
... is not authorized to perform: ssm:GetParameters ...
```

Cause:

- `ecsTaskExecutionRole` did not have permission to read SSM parameters

Solution:

- add `ssm:GetParameters` permission to `ecsTaskExecutionRole`
- if `SecureString` uses a customer-managed KMS key, also add `kms:Decrypt`

Example policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameters"
      ],
      "Resource": [
        "arn:aws:ssm:ap-southeast-1:961341555117:parameter/backend-rag/NIM_API_KEY",
        "arn:aws:ssm:ap-southeast-1:961341555117:parameter/backend-rag/AUTH_JWT_SECRET",
        "arn:aws:ssm:ap-southeast-1:961341555117:parameter/backend-rag/AUTH_BOOTSTRAP_ADMIN_USERNAME",
        "arn:aws:ssm:ap-southeast-1:961341555117:parameter/backend-rag/AUTH_BOOTSTRAP_ADMIN_PASSWORD"
      ]
    }
  ]
}
```

Relevant file:

- `deploy/ecs/README.md`

### ECS service stuck at `0 Running`: service was running the wrong task definition revision

Error:

```text
(service backend-rag-multipurpose) was unable to place a task. Reason: CannotPullContainerError:
pull image manifest has been retried 7 time(s): failed to resolve ref
961341555117.dkr.ecr.ap-southeast-1.amazonaws.com/rag-nginx:latest: not found.
```

Observed service state:

- `desiredCount = 1`
- `runningCount = 0`
- `pendingCount = 0`
- service deployment pointed at `backend-rag-multipurpose:3`
- revision `3` was inactive/stopped
- the working task was on `backend-rag-multipurpose:6`

Cause:

- the ECS service was still attached to the wrong task definition revision
- the service kept trying to start the stale revision instead of the working revision
- because the service was not pointed at the live revision, it could not keep a running task alive, so the public IP never became reachable

Solution:

- register the current task definition as a new revision
- update the ECS service to use the live revision
- force a new deployment so the service stops trying to run the stale revision

Example:

```powershell
aws --region ap-southeast-1 ecs register-task-definition --cli-input-json file://deploy/ecs/task-definition.json --query 'taskDefinition.taskDefinitionArn' --output text
aws --region ap-southeast-1 ecs update-service --cluster snaic_website_cluster --service backend-rag-multipurpose --task-definition backend-rag-multipurpose:6 --force-new-deployment
```

Relevant files:

- `deploy/ecs/task-definition.json`
- `deploy/ecs/service-definition.json`
- `deploy/ecs/README.md`

### ECS ingest still defaults to Ollama after switching env vars

Symptoms:

- `POST /ingest/files` returns `embedding_provider: "ollama"`
- `embedding_model` stays on an Ollama model such as `qwen3-embedding`
- changing the task definition in the console does not change the live behavior

Cause:

- embedding selection is profile-based
- the app uses `DEFAULT_EMBEDDING_PROFILE` when the request does not specify `embedding_profile`
- changing the task definition file alone does not update an already-running ECS service

Checks:

- call `GET /health` and inspect `assumptions.default_embedding_profile`
- verify the live task revision matches the revision you registered
- verify the service was updated with `--force-new-deployment`

Solution:

```powershell
aws --region ap-southeast-1 ecs register-task-definition --cli-input-json file://deploy/ecs/task-definition.json --query 'taskDefinition.taskDefinitionArn' --output text
aws --region ap-southeast-1 ecs update-service --cluster snaic_website_cluster --service backend-rag-multipurpose --task-definition <new-task-definition-arn> --force-new-deployment
```

If you want OpenAI to be the default for ingestion, make sure the live task definition sets:

- `DEFAULT_EMBEDDING_PROFILE=openai_small_1536`
- `EMBEDDING_PROFILES` contains the matching OpenAI profile
- `OPENAI_API_KEY` is injected into the app container

### ECS health showed Ollama defaults even though the task definition was OpenAI-based

Symptoms:

- `GET /health` reported `default_embedding_provider="ollama"`
- `GET /health` reported `default_embedding_model="qwen3-embedding"`
- `GET /health` reported `canonical_embedding_dimension=4096`
- `/ingest/files` defaulted to `ollama` when no `embedding_profile` was sent

Cause:

- the live app was not running the latest repo state
- the ECS deployment was still on an older image/task revision
- the embedding default is controlled by `DEFAULT_EMBEDDING_PROFILE`, not `OPENAI_ENABLED`

Fix:

- push the latest code to the repo
- rebuild and push the updated backend image
- confirm the running ECS task definition revision is the one with:
  - `DEFAULT_EMBEDDING_PROFILE=openai_small_1536`
  - `EMBEDDING_PROFILES` containing the OpenAI profile
  - `OLLAMA_ENABLED=false`
  - `OPENAI_ENABLED=true`
- redeploy the service with a new task definition revision
- force a new ECS deployment so the service pulls the latest image and env
- verify `GET /health` shows:
  - `default_embedding_provider="openai"`
  - `default_embedding_model="text-embedding-3-small"`
  - `canonical_embedding_dimension=1536`

Relevant files:

- `deploy/ecs/task-definition.json`
- `backend/app/core/config.py`
- `backend/app/services/embeddings.py`

### ECS task stopped on startup because `POSTGRES_DSN` did not match the Postgres container password

Symptoms:

- the ECS service deployed successfully, then the task stopped
- the app container could not stay healthy
- the live task definition showed `POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/ragdb`
- the Postgres container was started with `POSTGRES_PASSWORD=admin`

Cause:

- the app tried to connect to Postgres with the wrong password
- the database container and app container were configured with different credentials
- ECS stopped the task after the app failed startup/health checks

Fix:

- update `POSTGRES_DSN` in `deploy/ecs/task-definition.json` to use the same password as the Postgres container
- in this case, change it to `postgresql://postgres:admin@127.0.0.1:5432/ragdb`
- register a new task definition revision
- update the ECS service and force a new deployment

Relevant files:

- `deploy/ecs/task-definition.json`
- `deploy/ecs/service-definition.json`

### PostgreSQL init failure: `column cannot have more than 2000 dimensions for ivfflat index`

Error:

```text
ERROR: column cannot have more than 2000 dimensions for ivfflat index
STATEMENT: CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding
ON document_chunks
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
```

Cause:

- the schema used `VECTOR(4096)`
- `ivfflat` in pgvector does not support more than `2000` dimensions
- the deployment path was switched to a `1536`-dimension canonical embedding model, so the schema and runtime config were inconsistent

Solution:

- change the schema to `VECTOR(1536)`
- align the default embedding configuration to a `1536`-dimension canonical embedding model
- rebuild and push the Postgres image again
- redeploy the ECS service

Relevant files:

- `backend/app/db/schema.sql`
- `backend/app/core/config.py`
- `backend/.env.example`

### ECS task still uses OpenAI after switching to NIM

Symptoms:

- `GET /health` still reports `default_generation_provider="openai"`
- `/chat` continues calling OpenAI instead of NVIDIA NIM
- the task definition update appears to have had no effect

Cause:

- the live ECS service is still running an older task definition revision
- `DEFAULT_LLM_PROVIDER`, `NIM_BASE_URL`, or `NIM_API_KEY` were updated in the file but not in the running service
- ECS has not been forced to deploy the new revision

Solution:

- update `deploy/ecs/task-definition.json` with the NIM defaults
- register a new task definition revision
- update the ECS service with `--force-new-deployment`
- verify `GET /health` shows `default_generation_provider="nim"`

Relevant files:

- `deploy/ecs/task-definition.json`
- `deploy/ecs/README.md`
- `docs/deployment.md`

### NIM reasoning still appears in chat responses

Symptoms:

- the model emits visible reasoning or `<think>`-style output
- chat responses look more verbose than expected

Cause:

- `NIM_NO_THINK` is false or missing
- the live ECS task or local `.env` does not include the reasoning-off toggle

Solution:

- set `NIM_NO_THINK=true`
- rebuild or redeploy the app
- confirm the live task definition includes `NIM_NO_THINK=true`

Relevant files:

- `backend/app/services/prompt_builder.py`
- `backend/app/providers/nim_provider.py`
- `backend/app/core/config.py`
- `deploy/ecs/task-definition.json`

### Reranker fails even though generation and embeddings work

Symptoms:

- chat generation succeeds
- embeddings succeed
- retrieval fails only when reranking is enabled

Cause:

- the rerank endpoint is configured separately with `RERANK_INVOKE_URL`
- the NVIDIA API key is missing from `NIM_API_KEY`
- the rerank URL is wrong or unreachable

Solution:

- ensure `RERANK_ENABLED=true`
- set `RERANK_INVOKE_URL` to NVIDIA’s rerank endpoint
- reuse `NIM_API_KEY` for the reranker
- confirm the endpoint responds outside the app first

Relevant files:

- `backend/app/services/rerank.py`
- `deploy/ecs/task-definition.json`
- `backend/.env.example`

## Usage

When a new setup or deployment issue appears, add:

1. the exact error message
2. the likely cause
3. the fix
4. the related file or AWS resource
