# Backend RAG Multipurpose

Backend-only RAG chatbot MVP built with FastAPI, PostgreSQL, Qdrant, Redis, and multi-provider generation support across OpenAI, Gemini, Ollama, and a dedicated NVIDIA NIM alias for embeddings, generation, and reranking.

## TODO

1. Add a backend user message logger to monitor from misuse and jailbreaking
2. Add metrics to compute service performance and usage

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
- `GET /admin/system-prompt`
- `PUT /admin/system-prompt`
- PostgreSQL storage for users, API keys, and documents
- PostgreSQL storage for the editable system prompt
- Qdrant storage for chunk embeddings and similarity search
- Qdrant similarity search
- Redis rate limiting, retrieval caching, embedding caching, and optional session storage
- Request-level generation provider/model selection
- Optional reranking plugin for retrieval quality
- Multipart ingestion for `txt`, `md`, `docx`, `csv`, and `xlsx`
- JWT bearer authentication and hashed API keys
- Admin-only system prompt management through JWT bearer auth
- Chat guardrails for spam, quota, prompt-injection phrases, and output limits
- Exact duplicate knowledge-base uploads are deduplicated by normalized content hash plus embedding profile
- Grounded SNAIC chat behavior with a friendly, cheerful assistant style

## Important MVP constraint

The generation provider is switchable per request. The active generation and embedding profiles are stored in PostgreSQL and managed through the admin model-selection endpoints, while the selectable profile catalog lives in `backend/app/core/defaults.py`.

If you want to point the app at NVIDIA NIM, use OpenAI-compatible endpoints with models like:

- `nvidia/llama-3.3-nemotron-super-49b-v1.5`
- `nvidia/llama-nemotron-embed-1b-v2`
- `nvidia/llama-nemotron-rerank-1b-v2`

Example profiles:

- `ollama_1536`
- `openai_small_1536`
- `ollama_4096`
- `nim_nemotron_2048`

If you add a new dimension, the app will create the matching Qdrant collection on first use. Existing collections remain untouched.

## Change Map

Use this when you want to update the system without guessing which file owns what.

- Code behavior: edit `backend/app/`
- API shapes and response models: edit `backend/app/models/schemas.py`
- Runtime config and defaults: edit `backend/app/core/config.py`
- Provider wiring and API calls: edit `backend/app/providers/`
- Embedding, retrieval, prompt building, reranking, and chat orchestration: edit `backend/app/services/`
- Local Docker defaults: edit `backend/.env.example`
- Local runtime values: edit `backend/.env`
- ECS runtime defaults and secrets: edit `deploy/ecs/task-definition.json`
- ECS deployment instructions: edit `deploy/ecs/README.md`
- User-facing deployment guide: edit `docs/deployment.md`
- Behavior history and release notes: edit `docs/feature-log.md`
- Troubleshooting history: edit `docs/troubleshooting-log.md`

Typical change flow:

- If you change a model name or provider, update `backend/app/core/defaults.py`, then update the active startup defaults in `backend/.env` for local Docker and `deploy/ecs/task-definition.json` for ECS, plus the examples and docs.
- If you change a request or response field, update `backend/app/models/schemas.py` first, then adjust the services and any tests that depend on it.
- If you change how NIM works, keep `NIM_API_KEY` and the NIM embedding profile in sync across local env, ECS, and docs. Use `scripts/sync-provider-urls.ps1` to write the NIM base URL and rerank URL into `backend/.env` when you want explicit values there. Use `GET /admin/model-selection` and `PUT /admin/model-selection` to change the active generation or embedding profile without editing code.
- If you add a new deployment secret, add it to `backend/.env.example`, `deploy/ecs/task-definition.json`, and the ECS README / deployment docs together.
- If you change retrieval behavior, update `backend/app/services/retrieval.py`, `backend/app/services/rerank.py`, and the RAG pipeline docs together.

## Chat guardrails

Default chat safety controls are enforced in code and can be overridden through `backend/.env` or the ECS task definition:

- burst rate limit: `20` requests per `60` seconds per authenticated user
- daily quota: `1000` chat requests per user
- input size: `4000` characters and about `1000` tokens
- retrieval scope: `top_k` is clamped to `3..8`
- retrieval context: `8000` characters and about `2500` tokens per request
- response size: `2000` characters and about `700` tokens
- exact duplicate uploads are skipped when the content hash and embedding profile already exist
- blocked phrases include:
  - `ignore previous instructions`
  - `dump all data`
  - `show full document`
  - `export everything`
- `print full source`
- `return exact text`
- `which document you used`
- `which sources did you use`

The assistant is instructed to stay friendly, cheerful, and grounded to retrieved context. When context is missing, it falls back to a natural, user-friendly "I don't have enough information to answer that confidently yet. If you'd like, I can help with a related question." response instead of improvising.

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
set HOST_PROXY_PORT=8010
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

To run the live ingest/chat API flow with custom inputs, use the PowerShell wrapper:

```powershell
.\scripts\run-live-api-test.ps1 -Password YOUR_PASSWORD -IngestText "your text here"
```

You can also override `-BaseUrl`, `-Username`, `-ChatMessage`, `-GenerationProvider`, `-GenerationModel`, `-EmbeddingProfile`, `-EmbeddingProvider`, and `-EmbeddingModel` on the same command.

## Documentation

- [Architecture](docs/architecture.md)
- [API](docs/api.md)
- [Feature Log](docs/feature-log.md)
- [Development Log Pointer](docs/development-log.md)
- [Ingestion](docs/ingestion.md)
- [RAG Pipeline](docs/rag-pipeline.md)
- [Providers and Models](docs/providers-and-models.md)
- [Redis and Caching](docs/redis-and-caching.md)
- [Deployment](docs/deployment.md)
- [ECS Fargate Deployment](deploy/ecs/README.md)
- [Troubleshooting Log](docs/troubleshooting-log.md)
- [Load Testing](loadtest/README.md)
- [Runbook](docs/runbook.md)

# Ownership

Copyright (c) 2026 Isfaque Tuhin. Licensed under the Isfaque Tuhin Attribution License.
Built by Isfaque Tuhin for portfolio use.
Attribution links: https://www.linkedin.com/in/iatuhin/ | https://github.com/iahin | shioktech@gmail.com
