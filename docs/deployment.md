# Deployment

## Local container stack

The repository includes:

- `backend/docker-compose.yml` for local `nginx`, the FastAPI app, PostgreSQL, Qdrant, and Redis
- `backend/docker-compose.ollama.yml` for optional Ollama-in-Docker mode
- `backend/Dockerfile` for the FastAPI service image
- `deploy/ecs/README.md` for the ECS on Fargate deployment path with `nginx`

## Start the full stack

```bash
copy backend\.env.example backend\.env
docker compose -f backend/docker-compose.yml up --build -d
```

In local Docker mode, the Compose app service reads `backend/.env`. The `.env.example` file is only the starter template.

Before starting the stack, replace the placeholder and environment-specific values in `backend/.env`:

- `AUTH_BOOTSTRAP_ADMIN_USERNAME`
- `AUTH_BOOTSTRAP_ADMIN_PASSWORD`
- `AUTH_JWT_SECRET`
- `OPENAI_API_KEY`, `GEMINI_API_KEY`, or `NIM_API_KEY` depending on provider
- `POSTGRES_DSN`, `REDIS_URL`, `QDRANT_URL`, and `OLLAMA_BASE_URL` if your services are not using the default local endpoints

`backend/.env` is gitignored and should never be committed.

To generate a secure JWT secret for `AUTH_JWT_SECRET`, use one of these commands:

```powershell
[Convert]::ToBase64String((1..64 | ForEach-Object { Get-Random -Maximum 256 } | ForEach-Object { [byte]$_ }))
```

```powershell
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

If host port `9010` is unavailable, override it before starting:

```bash
set HOST_PROXY_PORT=8010
docker compose -f backend/docker-compose.yml up --build -d
```

This starts:

- `nginx` on `http://localhost:9010` by default
- FastAPI app behind `nginx` on the Docker network
- PostgreSQL for document/auth metadata
- Qdrant for chunk vectors and similarity search
- Redis `redis:7.4-alpine`

PostgreSQL also stores chat activity audit records and chat feedback records for the admin monitoring endpoints.

Ollama is not containerized in the default stack.

Default behavior:

- run Ollama on the host machine
- the app container connects to `http://host.docker.internal:11434`

Local traffic flow:

- `http://localhost:9010` -> `nginx` -> app

## Optional Ollama-in-Docker mode

```bash
copy backend\.env.example backend\.env
docker compose -f backend/docker-compose.yml -f backend/docker-compose.ollama.yml up --build -d
```

This adds:

- an `ollama` service
- an app override so `OLLAMA_BASE_URL` becomes `http://ollama:11434`

Pull models in that mode with:

```bash
docker exec -it rag_ollama ollama pull llama3.2
docker exec -it rag_ollama ollama pull qwen3-embedding
```

## Stop the full stack

```bash
docker compose -f backend/docker-compose.yml down
```

To remove named volumes too:

```bash
docker compose -f backend/docker-compose.yml down -v
```

## Application container build

```bash
docker build -f backend/Dockerfile -t rag-backend backend
```

## Application container run

```bash
docker run --rm -p 9010:8000 --env-file backend/.env rag-backend
```

If you run the app container outside Compose, make sure `POSTGRES_DSN`, `REDIS_URL`, `QDRANT_URL`, and `OLLAMA_BASE_URL` point to reachable hosts.

## Required env vars for deployment

- `POSTGRES_DSN`
- `REDIS_URL`
For ECS, keep the startup defaults in `deploy/ecs/task-definition.json`:

- `DEFAULT_GENERATION_PROVIDER`
- `DEFAULT_GENERATION_MODEL`
- `DEFAULT_EMBEDDING_PROVIDER`
- `DEFAULT_EMBEDDING_MODEL`
- `DEFAULT_EMBEDDING_DIMENSION`
- `AUTH_ENABLED`
- `AUTH_JWT_SECRET`
- `AUTH_BOOTSTRAP_ADMIN_USERNAME`
- `AUTH_BOOTSTRAP_ADMIN_PASSWORD`
- `CHAT_DEBUG_ENABLED`

Chat guardrail defaults now use code values, so they are not expected in `backend/.env`.

Current repository default model-selection seed:

- the startup seed uses the provider/model/dimension values from `backend/.env` for local Docker and from `deploy/ecs/task-definition.json` for ECS
- the catalog entries themselves are seeded in code from `backend/app/core/config.py`
- the selectable catalog is seeded in code on startup, then can be changed through the admin APIs
- `SIMILARITY_THRESHOLD` uses the code default

Depending on provider usage:

- `OPENAI_API_KEY`
- `NIM_API_KEY`
- `NIM_BASE_URL` defaults to the NVIDIA integrate endpoint in code and can be overridden with `scripts/sync-provider-urls.ps1`
- `CHAT_THINKING_ENABLED`
- `CHAT_TEMPERATURE` - deployed at `0.35` for a more natural voice (code default is `0.0`)
- `GEMINI_API_KEY`
- `OLLAMA_BASE_URL`
- `RERANK_INVOKE_URL` can be written into `backend/.env` with `scripts/sync-provider-urls.ps1`

Contentful CMS ingestion (`POST /ingest/cms`):

- `CONTENTFUL_SPACE_ID` - Contentful space id (stored in SSM Parameter Store for ECS)
- `CONTENTFUL_DELIVERY_TOKEN` - read-only Content Delivery API token (stored in SSM Parameter Store for ECS)
- `CONTENTFUL_ENVIRONMENT` - defaults to `master`
- `CONTENTFUL_SITE_BASE_URL` - public site base for citation URLs, e.g. `https://www.snaic.net`

For ECS, the two Contentful credentials are referenced as SSM secrets in
`deploy/ecs/task-definition.json` (never committed as plaintext). Create them once and
grant the execution role read access to them:

```
aws ssm put-parameter --region ap-southeast-1 --name "/backend-rag/CONTENTFUL_SPACE_ID"       --type SecureString --value "<space id>"
aws ssm put-parameter --region ap-southeast-1 --name "/backend-rag/CONTENTFUL_DELIVERY_TOKEN"  --type SecureString --value "<cda token>"
```

The `ecsTaskExecutionRole` inline policy `ssm_snaicwebsite` must list both parameter
ARNs (alongside the existing `/backend-rag/*` secrets) or the task fails to start with
`ResourceInitializationError: unable to retrieve secrets from ssm`.

Authentication-related settings:

- `AUTH_JWT_ALGORITHM`
- `AUTH_ACCESS_TOKEN_TTL_SECONDS`
- `AUTH_REQUIRE_HTTPS`

To change the active chat or embedding profile after deployment, use the admin endpoints:

- `GET /admin/model-catalog`
- `GET /admin/model-selection`
- `PUT /admin/model-selection`

To review chatbot usage after deployment, use:

- `GET /admin/chat-activity`
- `GET /admin/chat-feedback`

Important behavior notes after rollout:

- chat activity and chat feedback are stored in PostgreSQL, so they persist only as long as the active database volume persists
- in the single-task ECS shape, replacing the task can wipe these records because PostgreSQL is still task-local
- `GET /admin/chat-feedback` stores submitted ratings and comments; full chat transcript reconstruction depends on server-side session/activity data
- with `CHAT_DEBUG_ENABLED=false`, `/chat` and `/chat/stream` expose only `answer`, compact citation IDs, and `session_id`; keep debug disabled for public deployments unless operators need retrieved chunks and prompt payloads
- `/ingest/text` accepts only `title` and `content`; `/ingest/files` accepts only file uploads. Source type, metadata, `created_by`, `created_at`, and embedding selection are populated by the backend.
- `/chat` and `/chat/stream` accept only `message`; model selection, retrieval limits, session behavior, and debug behavior are server-side settings.

Supported chat-activity query params:

- `limit`
- `start_at`
- `end_at`
- `keyword`

Date filters accept `DD/MM/YYYY` such as `24/03/2025` as well as ISO 8601 timestamps.

Feedback capture endpoints:

- `POST /chat/feedback`
- `GET /admin/chat-feedback`

Feedback list filters:

- `limit`
- `start_at`
- `end_at`

Feedback date filters accept `DD/MM/YYYY` and ISO 8601 timestamps.

## Database initialization

Schema file:

- `backend/app/db/schema.sql`

The Docker Compose setup mounts this file into PostgreSQL init scripts. If your local volume predates schema changes, recreate the volume or run an explicit migration.

Auth table note:

- the application now creates `app_users` on startup if it is missing
- the application also creates `chat_activity_logs` on startup if it is missing
- the application also creates `chat_feedback` on startup if it is missing
- this avoids startup failure on older local volumes that were initialized before the auth schema was added
- chunk vectors now live in Qdrant collections, which are created automatically on first use

Bootstrap admin note:

- changing `AUTH_BOOTSTRAP_ADMIN_PASSWORD` in `backend/.env` does not overwrite an already-created admin user on an existing Postgres volume
- if the old password still works and the new one does not, you are running against persisted database state rather than a fresh bootstrap

## Post-deploy smoke checks

After redeploying the backend image or replacing the task, verify:

1. `GET /health` returns `200`
2. admin login still works against the current persisted database state
3. `POST /chat` returns `200` with only `answer`, compact `citations`, and `session_id` when `CHAT_DEBUG_ENABLED=false`
4. `POST /chat/stream` still streams answer chunks and a compact final payload
5. `GET /admin/chat-activity` returns `200`
6. `GET /admin/chat-feedback` returns `200`
7. Swagger shows `/chat` public and debug response schemas, and `/chat/stream` `text/event-stream` examples

If chat works but admin monitoring fails, the likely causes are:

- the running container is still on an older image
- the Postgres volume predates the current bootstrap logic

## Authentication deployment notes

Implemented auth:

- bootstrap admin user stored in PostgreSQL
- password hashing with `scrypt`
- signed JWT bearer tokens

For secure deployment:

1. Replace `AUTH_JWT_SECRET` with a long random secret.
2. Change `AUTH_BOOTSTRAP_ADMIN_PASSWORD`.
3. Terminate TLS at a reverse proxy or load balancer.
4. Set `AUTH_REQUIRE_HTTPS=true` when your proxy forwards `X-Forwarded-Proto: https`.

Example:

```env
AUTH_ENABLED=true
AUTH_JWT_SECRET=replace-with-a-long-random-secret
AUTH_BOOTSTRAP_ADMIN_USERNAME=admin
AUTH_BOOTSTRAP_ADMIN_PASSWORD=replace-with-a-long-random-password
AUTH_ACCESS_TOKEN_TTL_SECONDS=3600
AUTH_REQUIRE_HTTPS=true
```

Generate the JWT secret before setting `AUTH_JWT_SECRET`:

```powershell
[Convert]::ToBase64String((1..64 | ForEach-Object { Get-Random -Maximum 256 } | ForEach-Object { [byte]$_ }))
```

## Production notes

Implemented:

- async FastAPI app
- Redis-backed rate limiting
- daily per-user chat quotas
- JWT bearer auth
- health endpoint
- provider abstraction
- Qdrant-backed chunk storage and retrieval
- input filtering and output truncation for chat safety

Not yet implemented:

- migrations framework
- background workers
- TLS termination in the app itself
- structured observability stack
- secrets manager integration
- multi-replica coordination

## ECS on Fargate path

If you want everything running inside ECS on Fargate:

- use the ECS assets under `deploy/ecs/`
- use `backend/nginx/Dockerfile` with `NGINX_UPSTREAM_HOST` and `NGINX_UPSTREAM_PORT` for the public reverse proxy
- use `backend/postgres/Dockerfile` so the database schema is baked into the image

This deployment keeps `nginx`, the FastAPI app, PostgreSQL, and Redis in one ECS task and exposes port `80`.

HTTPS domain path:

- use ACM to issue a public certificate for `multiragapi.snaic.net`
- use an internet-facing ALB with an HTTPS `443` listener and the ACM certificate
- forward the ALB target group to ECS task `nginx` on port `80`
- create a Route 53 alias record for `multiragapi.snaic.net` pointing to the ALB
- use `deploy/ecs/service-definition.alb.example.json` as the ALB-backed ECS service template

Current limitation:

- the checked-in ECS deployment assets do not yet implement custom DNS beyond the `multiragapi.snaic.net` example
- if you need fixed edge IPs instead of an ALB DNS alias, add AWS Global Accelerator or use an NLB with Elastic IPs

Before using the ECS assets, replace the environment-specific values in `deploy/ecs/task-definition.json`, `deploy/ecs/service-definition.alb.example.json`, `deploy/ecs/service-definition.json`, and the AWS setup commands:

- AWS account IDs, region names, subnet IDs, and security group IDs
- ECR image URIs and repository prefixes
- SSM parameter ARNs and secret names for auth and provider credentials

For PostgreSQL in the ECS task definition:

- the ECS task now reads `POSTGRES_PASSWORD` from SSM Parameter Store instead of committing the database password into the task definition
- `deploy/ecs/task-definition.json` provides `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, and `POSTGRES_USER` to the app container
- the app builds its effective Postgres DSN from those values plus the secret `POSTGRES_PASSWORD`
- the redeploy script no longer rewrites the `POSTGRES_PASSWORD` secret reference and leaves the task definition as the source of truth

Generate the JWT secret outside ECS first, typically from your local PowerShell terminal or AWS CloudShell, then store that generated value in SSM Parameter Store for `AUTH_JWT_SECRET`.

Example command:

```powershell
aws ssm put-parameter --region YOUR_REGION --name /backend-rag/AUTH_JWT_SECRET --type SecureString --overwrite --value ([Convert]::ToBase64String((1..64 | ForEach-Object { Get-Random -Maximum 256 } | ForEach-Object { [byte]$_ })))
```

For repeatable redeploys, use the PowerShell helper:

```powershell
.\scripts\redeploy-ecs.ps1
```

Any change to the chat persona or fallback wording lives in `backend/app/services/prompt_builder.py`, so redeploying ECS must include a fresh `rag-backend` build and a new task definition revision.

It builds the images, pushes them to ECR, registers a new task definition revision, and updates the ECS service.

Important constraint:

- PostgreSQL and Redis become ephemeral in this Fargate shape
- for durable production, move them out of the task

Entry point:

- `deploy/ecs/README.md`
