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
- `DEFAULT_LLM_PROVIDER`
- `DEFAULT_LLM_MODEL`
- `DEFAULT_EMBEDDING_PROFILE`
- `EMBEDDING_PROFILES`
- `AUTH_ENABLED`
- `AUTH_JWT_SECRET`
- `AUTH_BOOTSTRAP_ADMIN_USERNAME`
- `AUTH_BOOTSTRAP_ADMIN_PASSWORD`

Chat guardrail defaults can also be overridden through environment variables if you want to change the safety envelope without editing code:

- `CHAT_RATE_LIMIT_REQUESTS`
- `CHAT_RATE_LIMIT_WINDOW_SECONDS`
- `CHAT_DAILY_LIMIT_REQUESTS`
- `CHAT_MAX_MESSAGE_CHARS`
- `CHAT_MAX_INPUT_TOKENS`
- `CHAT_MAX_HISTORY_MESSAGES`
- `CHAT_MAX_CONTEXT_CHARS`
- `CHAT_MAX_CONTEXT_TOKENS`
- `CHAT_MAX_CONTEXT_CHUNK_CHARS`
- `CHAT_MIN_TOP_K`
- `CHAT_MAX_TOP_K`
- `CHAT_MAX_RESPONSE_CHARS`
- `CHAT_MAX_RESPONSE_TOKENS`

Current repository default embedding settings:

- `DEFAULT_EMBEDDING_PROFILE=ollama_1536`
- `EMBEDDING_PROFILES={"ollama_1536":{"provider":"ollama","model":"rjmalagon/gte-qwen2-1.5b-instruct-embed-f16","dimension":1536},"openai_small_1536":{"provider":"openai","model":"text-embedding-3-small","dimension":1536},"nim_nemotron_2048":{"provider":"nim","model":"nvidia/llama-nemotron-embed-1b-v2","dimension":2048}}`
- `SIMILARITY_THRESHOLD=0.35`

Depending on provider usage:

- `OPENAI_API_KEY`
- `NIM_API_KEY`
- `NIM_BASE_URL`
- `NIM_NO_THINK`
- `GEMINI_API_KEY`
- `OLLAMA_BASE_URL`
- `RERANK_INVOKE_URL`

Authentication-related settings:

- `AUTH_JWT_ALGORITHM`
- `AUTH_ACCESS_TOKEN_TTL_SECONDS`
- `AUTH_REQUIRE_HTTPS`

## Database initialization

Schema file:

- `backend/app/db/schema.sql`

The Docker Compose setup mounts this file into PostgreSQL init scripts. If your local volume predates schema changes, recreate the volume or run an explicit migration.

Auth table note:

- the application now creates `app_users` and `api_keys` on startup if they are missing
- this avoids startup failure on older local volumes that were initialized before the auth schema was added
- chunk vectors now live in Qdrant collections, which are created automatically on first use

## Authentication deployment notes

Implemented auth:

- bootstrap admin user stored in PostgreSQL
- password hashing with `scrypt`
- signed JWT bearer tokens
- hashed API keys

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

## Production notes

Implemented:

- async FastAPI app
- Redis-backed rate limiting
- daily per-user chat quotas
- JWT bearer auth
- hashed API keys
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

For repeatable redeploys, use the PowerShell helper:

```powershell
.\scripts\redeploy-ecs.ps1
```

It builds the images, pushes them to ECR, registers a new task definition revision, and updates the ECS service.

Important constraint:

- PostgreSQL and Redis become ephemeral in this Fargate shape
- for durable production, move them out of the task

Entry point:

- `deploy/ecs/README.md`
