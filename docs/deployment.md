# Deployment

## Local container stack

The repository includes:

- `backend/docker-compose.yml` for the FastAPI app, PostgreSQL, and Redis
- `backend/docker-compose.ollama.yml` for optional Ollama-in-Docker mode
- `backend/Dockerfile` for the FastAPI service image

## Start the full stack

```bash
copy backend\.env.example backend\.env
docker compose -f backend/docker-compose.yml up --build -d
```

If host port `9010` is unavailable, override it before starting:

```bash
set HOST_APP_PORT=8010
docker compose -f backend/docker-compose.yml up --build -d
```

This starts:

- FastAPI app on `http://localhost:9010` by default
- PostgreSQL with pgvector image `pgvector/pgvector:pg16`
- Redis `redis:7.4-alpine`

Ollama is not containerized in the default stack.

Default behavior:

- run Ollama on the host machine
- the app container connects to `http://host.docker.internal:11434`

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

If you run the app container outside Compose, make sure `POSTGRES_DSN`, `REDIS_URL`, and `OLLAMA_BASE_URL` point to reachable hosts.

## Required env vars for deployment

- `POSTGRES_DSN`
- `REDIS_URL`
- `DEFAULT_LLM_PROVIDER`
- `DEFAULT_LLM_MODEL`
- `DEFAULT_EMBEDDING_PROVIDER`
- `DEFAULT_EMBEDDING_MODEL`
- `CANONICAL_EMBEDDING_DIMENSION`
- `AUTH_ENABLED`
- `AUTH_JWT_SECRET`
- `AUTH_BOOTSTRAP_ADMIN_USERNAME`
- `AUTH_BOOTSTRAP_ADMIN_PASSWORD`

Current repository default embedding settings:

- `DEFAULT_EMBEDDING_PROVIDER=ollama`
- `DEFAULT_EMBEDDING_MODEL=qwen3-embedding`
- `CANONICAL_EMBEDDING_DIMENSION=4096`
- `SIMILARITY_THRESHOLD=0.35`

Depending on provider usage:

- `OPENAI_API_KEY`
- `GEMINI_API_KEY`
- `OLLAMA_BASE_URL`

Authentication-related settings:

- `AUTH_JWT_ALGORITHM`
- `AUTH_ACCESS_TOKEN_TTL_SECONDS`
- `AUTH_REQUIRE_HTTPS`

## Database initialization

Schema file:

- `backend/app/db/schema.sql`

The Docker Compose setup mounts this file into PostgreSQL init scripts. If your local volume predates schema changes, recreate the volume or run an explicit migration.

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
- JWT bearer auth
- hashed API keys
- health endpoint
- provider abstraction

Not yet implemented:

- migrations framework
- background workers
- TLS termination in the app itself
- structured observability stack
- secrets manager integration
- multi-replica coordination
