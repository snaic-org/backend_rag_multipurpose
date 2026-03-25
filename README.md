# Backend RAG Multipurpose

Backend-only RAG chatbot MVP built with FastAPI, PostgreSQL, Qdrant, Redis, and multi-provider generation support across OpenAI, Gemini, Ollama, and a dedicated NVIDIA NIM alias for embeddings, generation, and reranking.

## TODO

1. Add a backend user message logger to monitor from misuse and jailbreaking
2. To including metrics to compute service performance and usage

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
- PostgreSQL storage for users, API keys, and documents
- Qdrant storage for chunk embeddings and similarity search
- Qdrant similarity search
- Redis rate limiting, retrieval caching, embedding caching, and optional session storage
- Request-level generation provider/model selection
- Optional reranking plugin for retrieval quality
- Multipart ingestion for `txt`, `md`, `docx`, `csv`, and `xlsx`
- JWT bearer authentication and hashed API keys
- Chat guardrails for spam, quota, prompt-injection phrases, and output limits
- Exact duplicate knowledge-base uploads are deduplicated by normalized content hash plus embedding profile
- Grounded SNAIC chat behavior with a friendly, cheerful assistant style

## Important MVP constraint

The generation provider is switchable per request. Embeddings are selected through named profiles in `backend/.env`, and the app creates or reuses a dimension-specific Qdrant collection for that profile automatically.

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

The assistant is instructed to stay friendly, cheerful, and grounded to retrieved context. When context is missing, it falls back to a safe "I couldn't find that in the knowledge base." response instead of improvising.

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
