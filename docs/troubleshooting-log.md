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

### Admin login kept rejecting the new bootstrap password after editing `backend/.env`

Symptoms:

- `POST /auth/token` returned `{"detail":"Invalid username or password"}`
- the password in `backend/.env` looked correct
- the issue showed up on an existing local Docker volume

Cause:

- bootstrap admin credentials are only used when the initial admin user is first created
- once PostgreSQL already contains that user, changing `AUTH_BOOTSTRAP_ADMIN_PASSWORD` in `backend/.env` does not overwrite the stored password hash

Solution:

- use the original bootstrap password that was active when the admin row was first created, or
- reset the admin user in PostgreSQL, or
- recreate the local Postgres volume if you want a fresh local bootstrap state

Relevant files:

- `backend/.env`
- `backend/app/services/auth_service.py`
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

### ECS HTTPS task cannot pull SSM secrets after disabling public IP

Error:

```text
ResourceInitializationError: unable to pull secrets or registry auth:
unable to retrieve secrets from ssm:
The task cannot pull secrets from AWS Systems Manager.
operation error SSM: GetParameters ... context deadline exceeded
```

Cause:

- the ECS service network config had `assignPublicIp=DISABLED`
- the task was still in a subnet without NAT Gateway or SSM/ECR/CloudWatch VPC endpoints
- Fargate could start the task ENI but could not reach SSM to fetch secrets

Solution:

- for the simple single-task HTTPS deployment, keep `assignPublicIp=ENABLED`
- rely on the ALB and security groups for inbound access control
- only disable task public IP if the VPC has NAT or the required AWS VPC endpoints
- update `scripts/redeploy-ecs.ps1` so the default HTTPS path actively sets `assignPublicIp=ENABLED`

Relevant files:

- `scripts/redeploy-ecs.ps1`
- `deploy/ecs/README.md`

### ECS target group health checks fail because the ALB is missing the task Availability Zone

Error:

```text
(service backend-rag-multipurpose) (task ...) (port 80) is unhealthy
in (target-group ... backend-rag-tg ...)
due to (reason Target is in an Availability Zone that is not enabled for the load balancer).
```

Cause:

- the ECS task launched in an AZ such as `ap-southeast-1c`
- the ALB was created with subnets that did not include that AZ
- ALB target groups cannot health check targets in an AZ that is not enabled on the load balancer

Solution:

- include the ECS service subnet/AZ in the ALB subnet list
- update existing ALB subnets when the deployment script reruns
- keep at least two ALB subnets enabled
- update `scripts/redeploy-ecs.ps1` to add the ECS service subnet to the ALB subnet set and call `elbv2 set-subnets`

Relevant files:

- `scripts/redeploy-ecs.ps1`
- `deploy/ecs/README.md`

### ECS target group health checks use the wrong path

Symptoms:

- ECS tasks start and then stop with:

```text
Task failed ELB health checks in target-group backend-rag-tg
```

Cause:

- an existing target group was reused with an older or default health check path
- the nginx container exposes a lightweight health endpoint at `/nginx-health`

Solution:

- configure the target group health check as:
  - protocol: `HTTP`
  - path: `/nginx-health`
  - success code: `200`
- update `scripts/redeploy-ecs.ps1` to call `elbv2 modify-target-group` for reused target groups

Relevant files:

- `scripts/redeploy-ecs.ps1`
- `backend/nginx/nginx.conf.template`

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

### Thinking block still appeared in chat output when it should have been hidden

Symptom:

- chat responses sometimes surfaced `<think>` or `<thinking>` text even when `CHAT_SHOW_THINKING_BLOCK=false`
- the issue showed up most clearly on streamed NIM output

Cause:

- the stream filter only handled one thinking-tag variant in part of the code path
- provider output could include either `<think>` or `<thinking>`, so the hidden path was not fully normalized

Solution:

- strip both `<think>` and `<thinking>` blocks in the chat formatting layer
- apply the same tag handling in provider stream filters
- keep `CHAT_SHOW_THINKING_BLOCK` separate from `CHAT_THINKING_ENABLED` so reasoning can be enabled without forcing the block visible

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
- the app uses the DB-backed active embedding profile when the request does not specify `embedding_profile`
- changing the task definition file alone does not update an already-running ECS service

Checks:

- call `GET /admin/model-selection` and inspect the active embedding profile
- verify the live task revision matches the revision you registered
- verify the service was updated with `--force-new-deployment`

Solution:

```powershell
aws --region ap-southeast-1 ecs register-task-definition --cli-input-json file://deploy/ecs/task-definition.json --query 'taskDefinition.taskDefinitionArn' --output text
aws --region ap-southeast-1 ecs update-service --cluster snaic_website_cluster --service backend-rag-multipurpose --task-definition <new-task-definition-arn> --force-new-deployment
```

If you want OpenAI to be the default for ingestion, make sure the active model-selection record is seeded or updated to:

- generation profile: `openai_gpt41_mini`
- embedding profile: `openai_small_1536`
- the catalog contains the matching OpenAI profiles
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
- the embedding default is controlled by the DB-backed model-selection record

Fix:

- push the latest code to the repo
- rebuild and push the updated backend image
- confirm the configured catalog includes the OpenAI embedding profile
- redeploy the service with a new task definition revision
- force a new ECS deployment so the service pulls the latest image and env
- verify `GET /admin/model-selection` shows:
  - `embedding_provider="openai"`
  - `embedding_model="text-embedding-3-small"`
  - `embedding_dimension=1536`

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
- the active model-selection record still points at the previous generation profile
- ECS has not been forced to deploy the new revision

Solution:

- update the active model-selection record through `PUT /admin/model-selection`
- register a new task definition revision
- update the ECS service with `--force-new-deployment`
- verify `GET /admin/model-selection` shows the expected generation profile and resolved provider/model

Relevant files:

- `deploy/ecs/task-definition.json`
- `deploy/ecs/README.md`
- `docs/deployment.md`

### NIM reasoning still appears in chat responses

Symptoms:

- the model emits visible reasoning or `<think>`-style output
- chat responses look more verbose than expected

Cause:

- `CHAT_THINKING_ENABLED` is enabled for the active provider
- the live ECS task or local `.env` is using a model that emits `<think>` or `<thinking>` content

Solution:

- set `CHAT_THINKING_ENABLED=false` if you want no visible reasoning output
- rebuild or redeploy the app
- confirm the live task definition includes the current chat thinking flag value

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

- the rerank endpoint value was never written into `backend/.env`
- the NVIDIA API key is missing from `NIM_API_KEY`
- the rerank URL is wrong or unreachable

Solution:

- ensure `RERANK_ENABLED=true`
- run `.\scripts\sync-provider-urls.ps1` if you want an explicit `RERANK_INVOKE_URL` in `backend/.env`
- reuse `NIM_API_KEY` for the reranker
- confirm the endpoint responds outside the app first

Relevant files:

- `backend/app/services/rerank.py`
- `deploy/ecs/task-definition.json`
- `backend/.env.example`

### Chat responses sound too technical or too expressive

Symptoms:

- the assistant sounds robotic, stiff, or overly verbose
- fallback responses mention the knowledge base or expose internal wording
- the assistant feels too cheerful or too casual for the product tone

Cause:

- the active backend image was built before the latest persona update
- the live ECS service is still running an older task definition revision
- the prompt instructions in `backend/app/services/prompt_builder.py` have not been deployed yet

Solution:

- update `backend/app/services/prompt_builder.py` with the desired tone
- rebuild and push the `rag-backend` image
- register a new ECS task definition revision
- update the ECS service with `--force-new-deployment`

Relevant files:

- `backend/app/services/prompt_builder.py`
- `deploy/ecs/task-definition.json`
- `scripts/redeploy-ecs.ps1`

### Prompt file changes did not affect live chat wording

Symptoms:

- editing `backend/app/services/prompt_builder.py` did not change the live chat output
- the assistant still used phrasing such as `Based on the knowledge base provided`
- prompt wording looked correct in code but not in real responses

Cause:

- the active system prompt is loaded from PostgreSQL through `system_prompt_settings`
- a previously saved admin-managed prompt was still active
- changing the default prompt file alone does not override an existing stored prompt

Solution:

- check the live prompt with `GET /admin/system-prompt`
- update the stored prompt with `PUT /admin/system-prompt` if the database value is outdated
- restart or redeploy only if you also changed the built-in default and want startup sync to apply to managed built-in prompt variants

Relevant files:

- `backend/app/services/prompt_builder.py`
- `backend/app/services/system_prompt_service.py`
- `backend/app/db/repositories/system_prompt.py`
- `backend/app/api/admin.py`

### Default prompt existed in more than one place

Symptoms:

- the default system prompt was defined in `prompt_builder.py`
- the same prompt text also existed in the SQL schema seed
- prompt updates required editing more than one file

Cause:

- the default prompt had been copied into startup SQL instead of being imported from the code constant

Solution:

- keep the canonical default prompt only in `backend/app/services/prompt_builder.py`
- seed the database from the startup service using that imported constant
- remove the duplicated prompt text from `backend/app/db/schema.sql`

Relevant files:

- `backend/app/services/prompt_builder.py`
- `backend/app/services/system_prompt_service.py`
- `backend/app/db/schema.sql`

### DOCX ingest missed collaboration steps because heading-only sections were dropped

Symptoms:

- chat responses only retrieved the introductory sections of the uploaded DOCX
- the collaboration steps existed in the source file but did not appear in the ingested chunks
- retrieval favored older or unrelated chunks instead of the intended section

Cause:

- the DOCX parser skipped sections that had a heading but little or no body text under that heading
- heading-only sections were not retained, so the ingestion pipeline lost important content during normalization

Solution:

- update the DOCX parser to keep plain-text headings and heading-only sections
- re-ingest the document so the database and vector store contain the corrected chunks

Relevant files:

- `backend/app/parsers/docx_parser.py`
- `backend/tests/test_docx_parser.py`

### Duplicate ingest reused stale chunks instead of replacing the document

Symptoms:

- `POST /ingest/files` reported `deduplicated: true`
- `chunks_created: 0` even after uploading the DOCX again
- chat kept retrieving an older chunk set with the wrong content

Cause:

- the content hash matched an existing stored document
- the ingest path treated the upload as a duplicate and skipped chunking
- the stale document record remained active in PostgreSQL and Qdrant

Solution:

- use `force_reingest=true` when uploading a file that should replace the stored version
- delete the existing document and its chunk embeddings before inserting the fresh one

Relevant files:

- `backend/app/services/ingest_service.py`
- `backend/app/db/repositories/documents.py`
- `backend/app/db/repositories/chunks.py`
- `scripts/test-login-chat.py`

### NIM stream crashed on empty provider events

Symptoms:

- `/chat/stream` failed with `IndexError: list index out of range`
- the failure happened while streaming from NVIDIA NIM
- normal `/chat` still worked

Cause:

- the NIM streaming parser assumed every SSE event had `choices[0]`
- NIM sometimes emits empty or non-content events during the stream

Solution:

- skip empty events before reading the first choice
- continue streaming until a real content delta arrives

Relevant files:

- `backend/app/providers/nim_provider.py`
- `backend/app/api/chat.py`

### Provider response handling failed when content was null

Symptoms:

- `/chat` returned `400 Bad Request`
- the backend exception mentioned `'NoneType' object has no attribute 'lower'`
- the failure appeared after a provider response with a null content field

Cause:

- the response formatting path assumed completion text was always a string
- a provider returned `None` for content, which reached a lowercase check

Solution:

- normalize provider content to an empty string before formatting
- make the chat service tolerate missing completion text

Relevant files:

- `backend/app/services/chat_service.py`
- `backend/app/providers/nim_provider.py`

### NIM chat or rerank fails because the URL values were never written into `.env`

Symptoms:

- NIM generation returns a missing-base-url error
- reranking is disabled or errors because `RERANK_INVOKE_URL` is empty and the code default is not being used
- local `.env` no longer contains the NVIDIA URL values after manual cleanup

Cause:

- the runtime config uses built-in defaults unless you write explicit URL values into `backend/.env`
- `scripts/sync-provider-urls.ps1` was not run after changing `RERANK_MODEL`
- the app is using an older `.env` that no longer has the explicit NIM URL entries

Solution:

- run `.\scripts\sync-provider-urls.ps1`
- confirm it writes `NIM_BASE_URL=https://integrate.api.nvidia.com/v1`
- confirm it writes `RERANK_INVOKE_URL=https://ai.api.nvidia.com/v1/retrieval/nvidia/<RERANK_MODEL>/reranking`
- restart the app container or reload the process so it picks up the updated `.env`

Relevant files:

- `scripts/sync-provider-urls.ps1`
- `backend/.env`
- `backend/app/core/config.py`
- `backend/app/services/rerank.py`

### Admin chat-activity endpoint returned HTTP 500 during audit rollout

Symptoms:

- `GET /admin/chat-activity` returned HTTP `500`
- `/chat` sometimes succeeded but logged `chat_activity_record_failed`
- the failure showed up with or without date filters in some deployments

Causes:

- the first rollout converted one Pydantic model into another without enabling attribute-based validation on the response model
- the initial SQL search used nullable-parameter checks inline, which was more fragile than needed on the real PostgreSQL-backed path
- audit writes originally ran on the request critical path, so a logging failure could also surface as a chat failure

Solution:

- enable `from_attributes=True` on the chat activity response model
- make activity logging best-effort so audit failures do not break `/chat`
- build the audit search `WHERE` clause dynamically instead of relying on `NULL` checks in SQL
- verify the live container path after rebuild with:

```powershell
$token = (Invoke-RestMethod -Method Post -Uri 'http://localhost:9010/auth/token' -ContentType 'application/json' -Body '{"username":"admin","password":"YOUR_PASSWORD"}').access_token
$headers = @{ Authorization = "Bearer $token" }
Invoke-RestMethod -Method Get -Uri 'http://localhost:9010/admin/chat-activity' -Headers $headers
Invoke-RestMethod -Method Get -Uri 'http://localhost:9010/admin/chat-activity?start_at=24/03/2025&end_at=29/03/2025' -Headers $headers
```

Relevant files:

- `backend/app/api/chat.py`
- `backend/app/api/admin.py`
- `backend/app/models/schemas.py`
- `backend/app/db/repositories/chat_activity.py`

### Chat feedback exists but `full_chat_text` is empty or incomplete

Symptoms:

- `POST /chat/feedback` succeeds
- `GET /admin/chat-feedback` returns the rating and comment
- `full_chat_text` is empty, partial, or does not match the expected conversation

Cause:

- feedback is joined to recorded chat activity by `session_id` when server-side session identifiers are available
- if no activity records exist for that session, the backend has no reliable way to group the conversation into one feedback transcript

Solution:

- confirm the server is recording chat activity for the relevant feedback session
- reuse that same server-side session identifier when calling `POST /chat/feedback`

Relevant files:

- `backend/app/api/chat.py`
- `backend/app/services/chat_feedback_service.py`
- `backend/app/db/repositories/chat_activity.py`

### Chat default profile fails because the named generation profile is missing

Symptoms:

- `/chat` returns an error about an unknown default generation profile
- `GET /admin/model-selection` shows the wrong provider/model pair
- updating the active generation profile to a name that does not exist in the catalog breaks startup or chat routing

Cause:

- the active generation profile points at a name that does not exist in the catalog
- the app validates the selected profile against the configured catalog

Solution:

- make sure the selected generation profile exactly matches a key in the catalog
- update it through `PUT /admin/model-selection` or correct the catalog entry in `backend/app/core/config.py`

Relevant files:

- `backend/app/core/config.py`
- `backend/.env`
- `backend/.env.example`

### Chat answered `No` even though the retrieved chunk clearly contained the evidence

Symptoms:

- `/chat` returned `No.` for a yes/no question
- debug output showed a retrieved chunk containing the missing fact
- example: the retrieved chunk included `Monash University`, but the answer still said `No.`

Cause:

- prompt building truncated each evidence excerpt to a hard `700` characters even when the configured chat chunk budget was larger
- the retrieved chunk could therefore look correct in debug output while the decisive line never reached the model
- an older binary-adjudication path also made diagnosis harder because yes/no questions could bypass the normal answer path

Solution:

- remove the hard `700` character excerpt cap and use the configured `max_chunk_chars` budget instead
- remove the binary adjudication shortcut so the model always answers from the normal grounded prompt
- restart the backend after deploying the fix so the running process picks up the updated prompt builder and chat flow

Relevant files:

- `backend/app/services/prompt_builder.py`
- `backend/app/services/chat_service.py`
- `backend/tests/test_query.py`

### Chat said a technology/sector question was unsupported even though the KB listed both facts

Symptoms:

- `/chat` returned an unsupported-style answer such as:
  - `The knowledge base does not provide specific details on how LLMs help manufacturing companies.`
- debug output showed the retrieved chunks already contained both:
  - `LLMs` under AI technology pillars or expertise areas
  - `Manufacturing` or `Industry and Manufacturing` under supported sectors or domain areas

Cause:

- the model was still classifying some indirect questions as `[B]` unsupported before it reached the later partial-answer guidance
- the prompt allowed conservative fact-combination in one section, but the classification step did not state strongly enough that these direct multi-fact connections should count as supported

Solution:

- strengthen the classification rule in the system prompt so `[A]` explicitly includes answers formed by directly combining closely related listed facts
- add explicit examples such as `technology + supported sector`, `capability + solution area`, and `programme + listed outcome`
- verify the live prompt through `GET /admin/system-prompt` instead of assuming the runtime picked up the latest prompt text

Verification:

- after rebuilding and restarting the Docker app with the updated prompt, the live backend answered:
  - `LLMs are listed as an AI Technology Pillar and Industry and Manufacturing is listed as a Domain Area under AI Expertise Areas. This indicates that LLMs are part of SNAIC's AI capabilities for the manufacturing sector.`

Relevant files:

- `backend/app/services/prompt_builder.py`
- `backend/app/services/system_prompt_service.py`
- `backend/tests/test_query.py`

## Usage

When a new setup or deployment issue appears, add:

1. the exact error message
2. the likely cause
3. the fix
4. the related file or AWS resource
