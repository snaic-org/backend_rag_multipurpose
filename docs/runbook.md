# Runbook

## Start the system

```bash
copy backend\.env.example backend\.env
docker compose -f backend/docker-compose.yml up --build -d
```

Local Docker reads values from `backend/.env`. `backend/.env.example` is only the template.

## What To Change

Use these rules when you update the app:

- Change a model or provider name:
  - update `backend/.env`
  - update `backend/.env.example`
  - update `deploy/ecs/task-definition.json` if ECS should match
  - update `README.md` and `docs/providers-and-models.md`
- Change chat behavior or reasoning:
  - update `backend/app/services/prompt_builder.py`
  - update `backend/app/services/chat_service.py`
  - update `backend/app/providers/nim_provider.py` if the change is NIM-specific
  - update `backend/.env.example` for any new toggle
- Change embeddings or retrieval:
  - update `backend/app/services/embeddings.py`
  - update `backend/app/services/retrieval.py`
  - update `backend/app/services/rerank.py` if reranking changes
  - update `backend/app/core/config.py` for new defaults
- Change API request or response fields:
  - update `backend/app/models/schemas.py`
  - update the dependent service code
  - update tests that build or assert those payloads
- Change ECS deployment behavior:
  - update `deploy/ecs/task-definition.json`
  - update `deploy/ecs/README.md`
  - update `docs/deployment.md`
  - add or rename SSM parameters in AWS if the secret list changes

If `9010` is blocked on the host:

```bash
set HOST_PROXY_PORT=8010
docker compose -f backend/docker-compose.yml up --build -d
```

## Start the system with Ollama in Docker

```bash
copy backend\.env.example backend\.env
docker compose -f backend/docker-compose.yml -f backend/docker-compose.ollama.yml up --build -d
```

## Verify the stack

```bash
curl http://localhost:9010/health
```

This goes through `nginx` first and then reaches the app container.

## Get a bearer token

Use the bootstrap admin credentials from `backend/.env`:

```bash
curl -X POST http://localhost:9010/auth/token ^
  -H "Content-Type: application/json" ^
  -d "{\"username\":\"admin\",\"password\":\"change-me-immediately\"}"
```

Use the returned token for protected routes:

```bash
curl http://localhost:9010/auth/me ^
  -H "Authorization: Bearer YOUR_JWT"
```

## Create, list, and revoke API keys

Create:

```bash
curl -X POST http://localhost:9010/auth/api-keys ^
  -H "Authorization: Bearer YOUR_JWT" ^
  -H "Content-Type: application/json" ^
  -d "{\"name\":\"local-client\"}"
```

List:

```bash
curl http://localhost:9010/auth/api-keys ^
  -H "Authorization: Bearer YOUR_JWT"
```

Revoke:

```bash
curl -X DELETE http://localhost:9010/auth/api-keys/API_KEY_UUID ^
  -H "Authorization: Bearer YOUR_JWT"
```

The revoke route returns HTTP `204 No Content`.

## Admin user CRUD

Create a user:

```bash
curl -X POST http://localhost:9010/admin/users ^
  -H "Authorization: Bearer YOUR_JWT" ^
  -H "Content-Type: application/json" ^
  -d "{\"username\":\"analyst\",\"password\":\"replace-with-a-strong-password\",\"is_active\":true,\"is_admin\":false}"
```

List users:

```bash
curl http://localhost:9010/admin/users ^
  -H "Authorization: Bearer YOUR_JWT"
```

Get one user:

```bash
curl http://localhost:9010/admin/users/USER_UUID ^
  -H "Authorization: Bearer YOUR_JWT"
```

Update a user:

```bash
curl -X PATCH http://localhost:9010/admin/users/USER_UUID ^
  -H "Authorization: Bearer YOUR_JWT" ^
  -H "Content-Type: application/json" ^
  -d "{\"is_active\":false}"
```

Delete a user:

```bash
curl -X DELETE http://localhost:9010/admin/users/USER_UUID ^
  -H "Authorization: Bearer YOUR_JWT"
```

## Pull Ollama models on the host

If you use Ollama, pull the generation and embedding models on the host machine:

```bash
ollama pull llama3.2
ollama pull rjmalagon/gte-qwen2-1.5b-instruct-embed-f16
```

If you use the Docker override instead:

```bash
docker exec -it rag_ollama ollama pull llama3.2
docker exec -it rag_ollama ollama pull rjmalagon/gte-qwen2-1.5b-instruct-embed-f16
```

## Ingest sample content

```bash
curl -X POST http://localhost:9010/ingest/text ^
  -H "Authorization: Bearer YOUR_JWT" ^
  -H "Content-Type: application/json" ^
  -d "{\"items\":[{\"title\":\"Overview\",\"content\":\"We offer AI chatbot implementation.\",\"source_type\":\"text\"}]}"
```

## Run a chat request

```bash
curl -X POST http://localhost:9010/chat ^
  -H "Authorization: Bearer YOUR_JWT" ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"What do we offer?\",\"provider\":\"ollama\",\"model\":\"llama3.2\"}"
```

## Chat guardrails

Default chat safety behavior:

- burst limit: `20` requests per `60` seconds per authenticated user
- daily limit: `1000` chat requests per authenticated user
- input caps: about `4000` characters and `1000` tokens
- retrieval caps: `top_k` is clamped to `3..8`
- prompt and output caps are enforced before the model response is returned

Blocked examples include:

- `ignore previous instructions`
- `dump all data`
- `show full document`
- `export everything`
- `print full source`
- `return exact text`
- `which document you used`
- `which sources did you use`

If the request is blocked, the API returns a validation error instead of passing the prompt to the model.

## Run a streaming chat request

```bash
curl -N -X POST http://localhost:9010/chat/stream ^
  -H "Authorization: Bearer YOUR_JWT" ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"What do we offer?\",\"provider\":\"ollama\",\"model\":\"llama3.2\"}"
```

## Run tests

If host-installed pytest plugins interfere with collection, disable plugin autoload:

```bash
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest backend/tests
```

## Run load tests

Use the `k6` scripts under `loadtest/` against the local Nginx entrypoint:

```bash
k6 run loadtest/health.js
k6 run loadtest/auth-token.js
k6 run -e JWT_TOKEN=YOUR_TOKEN loadtest/chat.js
```

Without installing `k6` on the host:

```bash
docker compose -f backend/docker-compose.yml -f backend/docker-compose.loadtest.yml run --rm k6 run /scripts/health.js
docker compose -f backend/docker-compose.yml -f backend/docker-compose.loadtest.yml run --rm k6 run /scripts/auth-token.js
docker compose -f backend/docker-compose.yml -f backend/docker-compose.loadtest.yml run --rm -e JWT_TOKEN=YOUR_TOKEN k6 run /scripts/chat.js
```

See:

- `loadtest/README.md`

## Embedding profiles

The app now supports named embedding profiles.

Examples:

- `ollama_1536`
- `openai_small_1536`
- `nim_nemotron_2048`

Use `DEFAULT_EMBEDDING_PROFILE` in `backend/.env` or the ECS task definition to switch the active embedding profile without editing code.

The active profile controls the provider/model/dimension. If you choose a new dimension, the app creates the matching Qdrant collection automatically on first use.

For per-request overrides, send `embedding_profile` on `/ingest/text`, `/ingest/files`, or `/chat` instead of mixing raw provider/model fields.

NIM-specific defaults used by this repository:

- `DEFAULT_LLM_PROVIDER=nim`
- `DEFAULT_LLM_MODEL=nvidia/llama-3.3-nemotron-super-49b-v1.5`
- `NIM_BASE_URL=https://integrate.api.nvidia.com/v1`
- `NIM_NO_THINK=true`
- `DEFAULT_EMBEDDING_PROFILE=nim_nemotron_2048`
- `EMBEDDING_PROFILES` contains the `nim_nemotron_2048` profile
- `RERANK_ENABLED=true`
- `RERANK_INVOKE_URL` points at NVIDIA reranking

When using NIM, you do not need to pull local models on the host or inside Docker because the app talks to NVIDIA-hosted endpoints through `NIM_BASE_URL`.

## Reset the backend state

To delete all indexed documents and chunk embeddings from PostgreSQL and clear this app's Redis cache/session/rate-limit keys:

```bash
curl -X DELETE http://localhost:9010/admin/reset ^
  -H "Authorization: Bearer YOUR_JWT"
```

## Common issues

### Startup fails with `relation "app_users" does not exist`

Current behavior:

- the app now creates `app_users` and `api_keys` automatically on startup

If you still see this error, restart the app container after pulling the latest code:

```bash
docker compose -f backend/docker-compose.yml up --build -d app
```

### Auth returns HTTP 401

Check:

- `AUTH_ENABLED=true`
- `backend/.env` has the expected bootstrap admin credentials
- the bearer token is valid and not expired
- the `X-API-Key` value is complete
- in Swagger UI, use `Authorize` and paste only the raw token without quotes

### Admin user CRUD returns HTTP 400

Check:

- the username is unique
- passwords are at least 12 characters
- you are not trying to delete your own account
- you are not trying to remove your own admin access

### Auth returns HTTP 403 on deployed HTTPS

Check:

- `AUTH_REQUIRE_HTTPS=true` only when TLS is terminated upstream
- the reverse proxy forwards `X-Forwarded-Proto: https`

### Embedding dimension mismatch

If you intentionally ingest with a different profile, the app will create a new dimension-specific Qdrant collection automatically. If you still see a mismatch, the running container is likely stale and needs a restart.

Current default setup expects:

- profile: `ollama_1536`
- provider: `ollama`
- model: `rjmalagon/gte-qwen2-1.5b-instruct-embed-f16`
- dimension: `1536`

If you switch to NIM, expect:

- profile: `nim_nemotron_2048`
- provider: `nim`
- model: `nvidia/llama-nemotron-embed-1b-v2`
- dimension: `2048`

### Chat returns fallback unexpectedly

Current retrieval order:

- Qdrant cosine similarity search
- lexical fallback against stored titles and chunk content
- best available semantic matches without applying the threshold
- safe fallback response only if no chunks exist for the active embedding pair

## Stop the system

```bash
docker compose -f backend/docker-compose.yml down
```
