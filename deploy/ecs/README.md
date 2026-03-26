# ECS Fargate deployment

This repository now includes a pragmatic single-task ECS on Fargate deployment path.

What it deploys in one ECS task:

- `nginx` reverse proxy on port `80`
- FastAPI app on port `8000`
- PostgreSQL for document/auth metadata on port `5432`
- Qdrant for chunk vectors and similarity search on port `6333`
- Redis on port `6379`

This keeps the current local Compose architecture inside one ECS task.

Current ECS template defaults:

- generation provider: `openai`
- generation model: `gpt-4.1-mini`
- embedding provider: `openai`
- embedding model: `text-embedding-3-small`
- embedding dimension: `1536`
- the selectable catalog lives in `backend/app/core/defaults.py`
- the active generation and embedding profiles are seeded from the task definition on startup
- reasoning visibility is controlled by `CHAT_THINKING_ENABLED`
- reranking is enabled and reuses `NIM_API_KEY`

Chat safety defaults are enforced by the app and can be overridden in the task definition if needed:

- burst rate limit: `CHAT_RATE_LIMIT_REQUESTS`
- daily quota: `CHAT_DAILY_LIMIT_REQUESTS`
- prompt and context caps: `CHAT_MAX_MESSAGE_CHARS`, `CHAT_MAX_INPUT_TOKENS`, `CHAT_MAX_CONTEXT_CHARS`, `CHAT_MAX_CONTEXT_TOKENS`
- retrieval clamp: `CHAT_MIN_TOP_K`, `CHAT_MAX_TOP_K`
- output cap: `CHAT_MAX_RESPONSE_CHARS`, `CHAT_MAX_RESPONSE_TOKENS`

The `nginx` image uses one shared config template for both local Docker and ECS.

- local Docker sets `NGINX_UPSTREAM_HOST=app`
- ECS/Fargate sets `NGINX_UPSTREAM_HOST=127.0.0.1`

## Important constraint

This is a single-task Fargate design with stateful sidecars.

- It is not highly available.
- PostgreSQL, Qdrant, and Redis use Fargate ephemeral task storage.
- Restarting or replacing the task can lose PostgreSQL, Qdrant, and Redis data.
- This is acceptable only for demos, dev, or disposable environments.
- For real production, move PostgreSQL to RDS and use a managed or persistent Qdrant/Redis setup.
- An AWS Application Load Balancer is still optional. `nginx` can remain the public entry point inside the task.

## Images to build

Build from the repository root:

```powershell
docker build -f backend/Dockerfile -t rag-backend:latest backend
docker build -f backend/nginx/Dockerfile -t rag-nginx:latest backend/nginx
docker build -f backend/postgres/Dockerfile -t rag-postgres:latest backend
```

Verify the local images exist before tagging:

```powershell
docker images
docker images | findstr rag-
```

If you do not see `rag-backend`, `rag-nginx`, and `rag-postgres`, build them first with the commands above.

Push the app images to ECR:

```powershell
aws ecr get-login-password --region ap-southeast-1 | docker login --username AWS --password-stdin 961341555117.dkr.ecr.ap-southeast-1.amazonaws.com

aws ecr create-repository --repository-name snaic_website/rag-backend
aws ecr create-repository --repository-name snaic_website/rag-nginx
aws ecr create-repository --repository-name snaic_website/rag-postgres

docker tag rag-backend:latest 961341555117.dkr.ecr.ap-southeast-1.amazonaws.com/snaic_website/rag-backend:latest
docker tag rag-nginx:latest 961341555117.dkr.ecr.ap-southeast-1.amazonaws.com/snaic_website/rag-nginx:latest
docker tag rag-postgres:latest 961341555117.dkr.ecr.ap-southeast-1.amazonaws.com/snaic_website/rag-postgres:latest

docker push 961341555117.dkr.ecr.ap-southeast-1.amazonaws.com/snaic_website/rag-backend:latest
docker push 961341555117.dkr.ecr.ap-southeast-1.amazonaws.com/snaic_website/rag-nginx:latest
docker push 961341555117.dkr.ecr.ap-southeast-1.amazonaws.com/snaic_website/rag-postgres:latest
```

If Docker returns `no basic auth credentials`, run the `aws ecr get-login-password ... | docker login ...` command again and retry the push.

Redis can stay on the public `redis:7.4-alpine` image.

## AWS resources

Create:

1. One ECS cluster for Fargate tasks.
2. One or more public or private subnets for the Fargate service.
3. One security group allowing inbound `80/tcp` from the internet or from your ALB.
4. One CloudWatch log group: `/ecs/backend-rag-multipurpose`.
5. One IAM task execution role: `ecsTaskExecutionRole`.
6. One IAM task role: `ecsTaskRole`.
7. SSM Parameter Store entries for secrets used by the app.
   - `/backend-rag/NIM_API_KEY`
   - `/backend-rag/AUTH_JWT_SECRET`
   - `/backend-rag/AUTH_BOOTSTRAP_ADMIN_USERNAME`
   - `/backend-rag/AUTH_BOOTSTRAP_ADMIN_PASSWORD`

## CloudWatch setup

Create the log group used by the ECS task:

1. Open `CloudWatch` in the AWS Console.
2. Go to `Log groups`.
3. Click `Create log group`.
4. Name it:
   - `/ecs/backend-rag-multipurpose`
5. Save it.

This is where logs from these containers will appear:

- `nginx`
- `app`
- `postgres`
- `redis`

## IAM setup

You need two IAM roles for ECS tasks:

- `ecsTaskExecutionRole`
- `ecsTaskRole`

Even if you deploy with the AWS root account, these roles still must exist.

### Trust relationship for both roles

Both roles must trust `ecs-tasks.amazonaws.com`.

Use this trust policy:

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

### Create `ecsTaskExecutionRole`

In the AWS Console:

1. Open `IAM`.
2. Go to `Roles`.
3. Click `Create role`.
4. Trusted entity type: `AWS service`.
5. Use case: `Elastic Container Service Task`.
6. Attach the managed policy:
   - `AmazonECSTaskExecutionRolePolicy`
7. Name the role:
   - `ecsTaskExecutionRole`
8. Create the role.

This role lets ECS:

- pull images from ECR
- write logs to CloudWatch
- read secrets from SSM

If your SSM SecureString values use a customer-managed KMS key, also allow:

- `kms:Decrypt`

### Create `ecsTaskRole`

In the AWS Console:

1. Open `IAM`.
2. Go to `Roles`.
3. Click `Create role`.
4. Trusted entity type: `AWS service`.
5. Use case: `Elastic Container Service Task`.
6. Do not add broad permissions unless the app actually needs AWS API access.
7. Name the role:
   - `ecsTaskRole`
8. Create the role.

For the current repository, this role can start minimal if the app itself is not calling AWS APIs directly.

## SSM setup

The ECS task definition reads secrets from AWS Systems Manager Parameter Store.

Create these parameters as `SecureString`:

- `/backend-rag/NIM_API_KEY`
- `/backend-rag/AUTH_JWT_SECRET`
- `/backend-rag/AUTH_BOOTSTRAP_ADMIN_USERNAME`
- `/backend-rag/AUTH_BOOTSTRAP_ADMIN_PASSWORD`

### Create the parameters in the AWS Console

1. Open `Systems Manager`.
2. Go to `Parameter Store`.
3. Click `Create parameter`.
4. For each parameter:
   - set `Name` to the exact value above
   - set `Tier` to `Standard`
   - set `Type` to `SecureString`
   - paste the real secret value
5. Save it.

Suggested values:

- `/backend-rag/NIM_API_KEY`: your NVIDIA API key
- `/backend-rag/AUTH_JWT_SECRET`: a long random secret
- `/backend-rag/AUTH_BOOTSTRAP_ADMIN_USERNAME`: your admin username
- `/backend-rag/AUTH_BOOTSTRAP_ADMIN_PASSWORD`: your admin password

The current ECS task template is NIM-based by default.

### Permissions note

Your `ecsTaskExecutionRole` must be able to read these parameters.

At minimum it needs:

- `ssm:GetParameters`

Example policy scope for the current NIM-based task:

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

If you use a customer-managed KMS key for `SecureString`, it also needs:

- `kms:Decrypt`

## Task definition

Template file:

- `deploy/ecs/task-definition.json`

Replace:

- `<AWS_ACCOUNT_ID>`
- `<AWS_REGION>`
- `<CHANGE_ME_DB_PASSWORD>`

Recommended production edits before registering:

- Set `AUTH_REQUIRE_HTTPS=true` if TLS is terminated before traffic reaches `nginx`.
- Set `DEFAULT_GENERATION_PROVIDER`, `DEFAULT_GENERATION_MODEL`, `DEFAULT_EMBEDDING_PROVIDER`, `DEFAULT_EMBEDDING_MODEL`, and `DEFAULT_EMBEDDING_DIMENSION` to the startup defaults you want.
- Update `backend/app/core/defaults.py` when you add or change the catalog of allowed generation or embedding models.
- Tune the chat guardrail env vars above if you need a different safety envelope.
- Keep the `nginx` env vars aligned with your task networking model.
- Adjust task `cpu`, `memory`, and `ephemeralStorage` to your workload.

Important:

- `CHAT_THINKING_ENABLED=true` allows providers to expose reasoning output when supported.
- `/ingest/files` and `/ingest/text` use the active embedding profile from the admin-managed model-selection record when the request does not send `embedding_profile`.
- `GET /admin/model-selection` shows the active generation and embedding profiles.
- `PUT /admin/model-selection` changes them without editing the task definition.
- ECS will keep running the old task definition until you register a new revision and update the service.

Then register:

```powershell
aws ecs register-task-definition --cli-input-json file://deploy/ecs/task-definition.json
```

## Service definition

Template file:

- `deploy/ecs/service-definition.json`

Replace:

- `snaic_website_cluster` with your ECS cluster name
- subnet ids
- security group id

Then create or update the service.

If the service does not exist yet:

```powershell
aws ecs create-service --cluster snaic_website_cluster --cli-input-json file://deploy/ecs/service-definition.json
```

If the service already exists, register a new task definition revision and update the running service:

```powershell
aws ecs register-task-definition --cli-input-json file://deploy/ecs/task-definition.json --query 'taskDefinition.taskDefinitionArn' --output text
aws ecs update-service --cluster snaic_website_cluster --service backend-rag-multipurpose --task-definition <new-task-definition-arn> --force-new-deployment
```

## One-command redeploy

If you want a repeatable local command that builds, pushes, registers, and updates the ECS service, use:

```powershell
.\scripts\redeploy-ecs.ps1
```

That script:

- builds `rag-backend`, `rag-nginx`, and `rag-postgres`
- pushes them to ECR
- registers a new task definition revision from `deploy/ecs/task-definition.json`
- updates the ECS service with `--desired-count 1` and `--force-new-deployment`
- waits for the service to become stable

Override defaults if needed:

```powershell
.\scripts\redeploy-ecs.ps1 -Region ap-southeast-1 -AccountId 961341555117 -Cluster snaic_website_cluster -Service backend-rag-multipurpose
```

If you already pushed the images and only need to recycle ECS:

```powershell
.\scripts\redeploy-ecs.ps1 -SkipBuild -SkipPush
```

If you want a shorter or longer wait before the script gives up:

```powershell
.\scripts\redeploy-ecs.ps1 -TimeoutMinutes 10 -PollSeconds 10
```

## Traffic flow

Requests hit:

- `task ENI:80` or `ALB:80/443` -> `nginx` -> FastAPI app

Container-local dependencies:

- app -> `127.0.0.1:5432` PostgreSQL
- app -> `127.0.0.1:6333` Qdrant
- app -> `127.0.0.1:6379` Redis

Simple diagram:

```text
client
  |
  +--> ALB:80/443 (optional)
  |       |
  |       v
  +----> task ENI:80
           |
           v
         nginx
           |
           v
      FastAPI app:8000
         |        |        |
         |        |        +--> 127.0.0.1:6379   Redis
         |        |
         |        +---------> 127.0.0.1:6333   Qdrant
         |
         +------------------> 127.0.0.1:5432   PostgreSQL
```

## Operational notes

- This design preserves the all-in-one task shape, but stateful containers on Fargate remain disposable.
- Ollama is disabled in the ECS template because local models are not realistic in this deployment shape.
- The ECS task template seeds OpenAI as the initial generation and embedding default, but the catalog still includes NIM and Ollama profiles in code if you switch them later through the admin API.
- If you want Fargate to be production-ready, the next step is: app + nginx on Fargate, PostgreSQL on RDS, Redis on ElastiCache.
- Chat persona wording lives in `backend/app/services/prompt_builder.py`, so any tone change requires rebuilding and pushing the `rag-backend` image, then registering a new task definition revision and updating the ECS service.

Related troubleshooting:

- `docs/troubleshooting-log.md`
