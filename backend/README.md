# Backend Folder

Repository-level documentation now lives at:

- `README.md`
- `docs/architecture.md`
- `docs/api.md`
- `docs/feature-log.md`
- `docs/development-log.md`
- `docs/ingestion.md`
- `docs/rag-pipeline.md`
- `docs/providers-and-models.md`
- `docs/redis-and-caching.md`
- `docs/deployment.md`
- `docs/runbook.md`

Use the root `README.md` as the primary entry point.

The Docker-first startup path is:

```bash
copy backend\.env.example backend\.env
docker compose -f backend/docker-compose.yml up --build -d
```

The Compose app service reads `backend/.env`. The `.env.example` file is only the template you copy from.

Embedding selection is profile-based and stored in PostgreSQL:

- set `DEFAULT_EMBEDDING_PROVIDER`, `DEFAULT_EMBEDDING_MODEL`, and `DEFAULT_EMBEDDING_DIMENSION` to seed the startup default
- edit `backend/app/core/defaults.py` if you want to add or override named provider/model/dimension combinations
- use `GET /admin/model-selection` and `PUT /admin/model-selection` to change the active embedding profile after startup
- when a new dimension is used, the app creates the matching Qdrant collection automatically

Default Docker-exposed API port is `9010`.

If host port `9010` is blocked, set:

```bash
set HOST_PROXY_PORT=8010
```

In the default setup, Ollama is not containerized. Run Ollama on the host machine.

Optional override for containerized Ollama:

```bash
copy backend\.env.example backend\.env
docker compose -f backend/docker-compose.yml -f backend/docker-compose.ollama.yml up --build -d
```
