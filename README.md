# Backend RAG Multipurpose

Backend-only RAG chatbot MVP built with FastAPI, PostgreSQL plus pgvector, Redis, and multi-provider generation support across OpenAI, Gemini, and Ollama.

## What is implemented

- `GET /health`
- `POST /auth/token`
- `GET /auth/me`
- `POST /auth/api-keys`
- `POST /ingest/text`
- `POST /ingest/files`
- `POST /chat`
- `POST /chat/stream`
- `DELETE /admin/reset`
- PostgreSQL storage for users, API keys, documents, and chunk embeddings
- pgvector similarity search
- Redis rate limiting, retrieval caching, embedding caching, and optional session storage
- Request-level generation provider/model selection
- Multipart ingestion for `txt`, `md`, `docx`, `csv`, and `xlsx`
- JWT bearer authentication and hashed API keys

## Important MVP constraint

The generation provider is switchable per request, but indexed embeddings are pinned to one canonical embedding provider/model pair because the current schema uses a single fixed-dimension pgvector column.

Current default canonical pair:

- Provider: `ollama`
- Model: `qwen3-embedding`
- Dimension: `4096`

If you change the canonical indexed embedding model to one with a different vector size, you must recreate the `document_chunks.embedding` column accordingly.

## Ollama runtime mode

Default behavior:

- Ollama runs outside Docker on the host machine
- the Dockerized app connects to it through `http://host.docker.internal:11434`

If you run the app outside Docker too, the default `.env.example` keeps Ollama at:

- `http://localhost:11434`

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r backend/requirements.txt
copy backend\.env.example backend\.env
docker compose -f backend/docker-compose.yml up --build -d
```

If you need to override defaults for local non-Docker runs, copy `backend/.env.example` to `backend/.env`.

Authentication defaults in `backend/.env.example`:

- `AUTH_ENABLED=true`
- `AUTH_BOOTSTRAP_ADMIN_USERNAME=admin`
- `AUTH_BOOTSTRAP_ADMIN_PASSWORD=change-me-immediately`
- `AUTH_JWT_SECRET=change-me-immediately`

Change the bootstrap password and JWT secret before exposing the API outside local development.

Default Docker-exposed API port:

- `9010`

If host port `9010` is blocked, set a different one before starting Compose:

```bash
set HOST_APP_PORT=8010
docker compose -f backend/docker-compose.yml up --build -d
```

Optional Ollama-in-Docker mode:

```bash
copy backend\.env.example backend\.env
docker compose -f backend/docker-compose.yml -f backend/docker-compose.ollama.yml up --build -d
```

## Test

```bash
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest backend/tests
```

## Documentation

- [Architecture](docs/architecture.md)
- [API](docs/api.md)
- [Ingestion](docs/ingestion.md)
- [RAG Pipeline](docs/rag-pipeline.md)
- [Providers and Models](docs/providers-and-models.md)
- [Redis and Caching](docs/redis-and-caching.md)
- [Deployment](docs/deployment.md)
- [Runbook](docs/runbook.md)
