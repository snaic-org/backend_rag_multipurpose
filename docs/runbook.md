# Runbook

## Start the system

```bash
copy backend\.env.example backend\.env
docker compose -f backend/docker-compose.yml up --build -d
```

If `9010` is blocked on the host:

```bash
set HOST_APP_PORT=8010
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
ollama pull qwen3-embedding
```

If you use the Docker override instead:

```bash
docker exec -it rag_ollama ollama pull llama3.2
docker exec -it rag_ollama ollama pull qwen3-embedding
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

Current default local Ollama setup expects:

- provider: `ollama`
- model: `qwen3-embedding`
- dimension: `4096`

If you changed the schema or old volumes still contain a `VECTOR(1536)` table definition, recreate the database volume and reinitialize the schema.

### Chat returns fallback unexpectedly

Current retrieval order:

- pgvector cosine similarity search
- lexical fallback against stored titles and chunk content
- best available semantic matches without applying the threshold
- safe fallback response only if no chunks exist for the active embedding pair

## Stop the system

```bash
docker compose -f backend/docker-compose.yml down
```
