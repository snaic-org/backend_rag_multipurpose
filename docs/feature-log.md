# Feature Log

This file tracks feature additions, major fixes, and operational changes using dated entries and internal version labels.

## v0.1.0 - 2026-03-14

Initial backend MVP delivered.

Added:

- FastAPI backend skeleton
- PostgreSQL plus pgvector persistence
- Redis connectivity
- provider abstraction for OpenAI, Gemini, and Ollama
- `GET /health`
- initial project docs

## v0.2.0 - 2026-03-14

Core RAG ingestion and chat pipeline delivered.

Added:

- `POST /ingest/text`
- `POST /ingest/files`
- normalized document model
- file parsers for:
  - raw text
  - markdown
  - `.txt`
  - `.md`
  - `.docx`
  - `.csv`
  - `.xlsx`
- chunking and canonical embedding pipeline
- pgvector retrieval
- grounded prompt building
- `POST /chat`
- `POST /chat/stream`
- citations
- Redis-backed rate limiting and caching
- optional session storage

## v0.3.0 - 2026-03-14

Local runtime and portfolio polish added.

Added:

- Dockerfile
- Docker Compose stack for app, PostgreSQL, and Redis
- test coverage for core services
- root documentation set for architecture, API, ingestion, pipeline, providers, Redis, deployment, and runbook

## v0.3.1 - 2026-03-14

Container runtime refined.

Changed:

- default host API port moved to `9010`
- default runtime uses Ollama on the host, not inside Docker
- optional `backend/docker-compose.ollama.yml` added for containerized Ollama

## v0.3.2 - 2026-03-14

Ingestion hardening fixes.

Added and fixed:

- tolerant multipart parsing for `tags`
- tolerant multipart parsing for `metadata`
- ignore Swagger placeholder `string` for optional multipart fields
- JSONB insert handling fixed with `Jsonb(...)`
- partial-success ingest behavior kept intact

## v0.3.3 - 2026-03-14

Canonical embedding configuration aligned to Ollama.

Changed:

- default embedding provider set to `ollama`
- default embedding model set to `qwen3-embedding`
- canonical embedding dimension set to `4096`
- local similarity threshold tuned for Ollama
- schema updated for `VECTOR(4096)`

## v0.3.4 - 2026-03-14

Retrieval quality improved.

Added:

- lexical fallback retrieval over stored titles and chunk text
- best-available semantic fallback when thresholded retrieval returns nothing

Result:

- generic questions over small corpora are less likely to fall straight to the safe fallback response

## v0.3.5 - 2026-03-15

Reset and maintenance tooling added.

Added:

- `DELETE /admin/reset`

Behavior:

- deletes indexed documents and chunk embeddings from PostgreSQL
- clears app-owned Redis keys for retrieval cache, embedding cache, session storage, and rate limiting

## v0.4.0 - 2026-03-15

Authentication and authorization introduced.

Added:

- bootstrap admin account on startup
- JWT bearer authentication
- hashed API keys
- `POST /auth/token`
- `GET /auth/me`
- `POST /auth/api-keys`
- optional HTTPS enforcement using `AUTH_REQUIRE_HTTPS`

## v0.4.1 - 2026-03-15

Admin and key-management features expanded.

Added:

- `GET /auth/api-keys`
- `DELETE /auth/api-keys/{api_key_id}`
- admin-only user CRUD:
  - `POST /admin/users`
  - `GET /admin/users`
  - `GET /admin/users/{user_id}`
  - `PATCH /admin/users/{user_id}`
  - `DELETE /admin/users/{user_id}`

Added validation and guardrails:

- minimum password length for user create/update
- duplicate username protection
- current admin cannot delete their own account
- current admin cannot remove their own admin access

## v0.4.2 - 2026-03-15

Auth startup resilience improved.

Added:

- automatic creation of `app_users` and `api_keys` tables on startup if they are missing

Result:

- older local PostgreSQL volumes no longer fail startup just because they predate the auth schema

## v0.4.3 - 2026-03-15

Auth header handling and Swagger behavior improved.

Changed:

- switched to FastAPI security schemes for bearer auth and API keys
- OpenAPI/Swagger now understands auth more cleanly
- clearer validation for malformed bearer values
- quoted secret values are normalized more safely

## v0.5.0 - 2026-03-19

Local proxying and ECS deployment assets added.

Added:

- `nginx` as the default local Docker entrypoint in front of the app
- shared Nginx image and config template under `backend/nginx/`
- forwarded-header support in the app container runtime
- ECS deployment templates under `deploy/ecs/`
- PostgreSQL image variant that bakes in `schema.sql`

Changed:

- local Docker traffic now flows through `http://localhost:9010` -> `nginx` -> app
- direct host exposure for the app container was removed from the default local Compose path
- ECS template defaults switched away from Ollama toward hosted model providers for micro-instance practicality

Updated later:

- ECS deployment path was moved from EC2-oriented templates to Fargate-oriented templates

## v0.5.1 - 2026-03-20

Load-testing tooling expanded.

Added:

- `k6` load-test scripts for health, auth token, and chat flows under `loadtest/`
- Docker Compose override for running `k6` locally in Docker without host installation
- load-testing usage docs linked from the runbook and root README

## v0.5.2 - 2026-03-20

Troubleshooting documentation added.

Added:

- dedicated troubleshooting log for local Docker and ECS/Fargate errors
- documented fixes for Nginx config rendering, app port mismatch, ECR image tagging, IAM role assumption, and SSM secret access

## v0.5.3 - 2026-03-20

Canonical embedding defaults updated for deployment compatibility.

Changed:

- default canonical embedding pair switched to Ollama `rjmalagon/gte-qwen2-1.5b-instruct-embed-f16`
- canonical embedding dimension changed from `4096` to `1536`
- schema updated to `VECTOR(1536)` so pgvector `ivfflat` indexing works in the deployment path

## v0.5.4 - 2026-03-22

Chat guardrails and request budgeting hardened.

Added:

- authenticated-user scoped burst rate limiting
- daily chat quota of `1000` requests per user
- prompt filtering for jailbreak and data-dumping phrases
- repeated-prompt detection
- prompt length limits
- retrieval `top_k` clamping to `3..8`
- context and response size caps
- SNAIC-friendly grounded assistant style in the system prompt

Behavior:

- blocked chat requests return clear validation errors
- streaming responses are capped before the client receives them
- chat history is trimmed before prompt construction

## v0.5.5 - 2026-03-22

Ingestion deduplication added.

Added:

- exact duplicate knowledge-base uploads are deduplicated by normalized content hash plus embedding profile
- duplicate uploads skip chunking, embedding, and vector upsert work

Behavior:

- the same content can still be ingested separately under a different embedding profile
- duplicate uploads return a successful ingest result marked as deduplicated

## v0.5.6 - 2026-03-25

NVIDIA NIM support added as an explicit provider alias.

Added:

- `nim` provider alias for generation and embeddings
- `NIM_BASE_URL` and `NIM_API_KEY` env wiring
- NIM embedding profile example for `nvidia/llama-nemotron-embed-1b-v2`
- reranking path using `RERANK_INVOKE_URL`
- reranker reuses `NIM_API_KEY`

Changed:

- OpenAI provider remains fixed to the public OpenAI endpoint internally
- `OPENAI_BASE_URL` was removed from the env surface
- NIM embedding requests now send `input_type=query|passage` and `truncate=NONE`

## v0.5.7 - 2026-03-25

Reasoning-off controls and ingest compatibility fixes added.

Added:

- NIM-only `/no_think` system directive through `NIM_NO_THINK`
- greedy decoding defaults for NIM chat when reasoning is disabled

Fixed:

- document ingest `ON CONFLICT` handling now works with the existing partial unique index
- local Docker ingest no longer fails on `ON CONFLICT` target inference

## v0.5.8 - 2026-03-25

ECS deployment synced to the NIM-based runtime contract.

Changed:

- ECS task definition defaults moved from OpenAI to NVIDIA NIM
- ECS now uses `NIM_API_KEY` for generation, embeddings, and reranking
- ECS task definition includes `NIM_BASE_URL`, `NIM_NO_THINK`, and `RERANK_INVOKE_URL`
- ECS docs now describe the NIM-based deployment path

## Current feature set - 2026-03-22

The repository currently includes:

- backend-only RAG API with FastAPI
- document ingestion for text, markdown, DOCX, CSV, and XLSX
- PostgreSQL plus pgvector storage and retrieval
- Redis-backed rate limiting, caching, and optional sessions
- multi-provider chat generation
- explicit NVIDIA NIM alias support
- optional reranking
- chat guardrails for abuse, prompt injection, and output caps
- Dockerized local runtime behind `nginx`
- JWT auth, API keys, and admin user management
- detailed operational documentation
- ECS on Fargate deployment templates for a single-task setup
- `k6`-based load-testing scripts with optional Docker runner

## Notes

- Version labels in this log are internal project markers, not published package tags.
- TLS termination is still expected to be handled by deployment infrastructure rather than by the FastAPI app itself.
