# Architecture

## Overview

The project is a backend-only RAG chatbot service with these runtime dependencies:

- FastAPI application layer
- PostgreSQL as the primary data store
- pgvector for embedding storage and similarity search
- Redis for rate limiting, caching, and optional session state
- Provider adapters for OpenAI, Gemini, and Ollama
- JWT bearer authentication and hashed API keys

## High-level flow

1. Documents are ingested through `POST /ingest/text` or `POST /ingest/files`.
2. Protected routes require a bearer token or `X-API-Key`.
3. Inputs are normalized into a shared internal document model.
4. Text is chunked.
5. Chunks are embedded using the canonical configured embedding provider/model.
6. Documents and chunks are stored in PostgreSQL.
7. A chat request embeds the user query with the same canonical embedding pair.
8. pgvector retrieves top matching chunks above the configured similarity threshold.
9. A grounded prompt is built from retrieved context.
10. The selected generation provider produces either a full answer or a streaming answer.

## Code layout

```text
backend/app/
|- api/         # FastAPI routes
|- core/        # config, logging, rate limiting, security
|- db/          # connection managers, schema, repositories
|- models/      # Pydantic schemas
|- parsers/     # file parsing and normalization
|- providers/   # provider abstraction and implementations
`- services/    # auth, chunking, embeddings, retrieval, prompting, chat, ingest
```

## Separation of concerns

- Route handlers stay thin and delegate to services.
- Provider-specific logic is isolated under `backend/app/providers/`.
- PostgreSQL access is isolated under `backend/app/db/repositories/`.
- File-type-specific parsing stays under `backend/app/parsers/`.
- RAG orchestration lives in `backend/app/services/`.
- Auth token issuance and API key verification live in `backend/app/services/auth_service.py`.

## Data model

Primary tables:

- `app_users`
- `api_keys`
- `documents`
- `document_chunks`

Important fields:

- `app_users.username`
- `app_users.password_hash`
- `api_keys.key_prefix`
- `api_keys.key_hash`
- `documents.title`
- `documents.url`
- `documents.source_type`
- `documents.metadata`
- `documents.original_filename`
- `documents.mime_type`
- `document_chunks.content`
- `document_chunks.metadata`
- `document_chunks.embedding`

## Authentication model

Implemented authentication is:

- local bootstrap admin user stored in PostgreSQL
- password hashing with `hashlib.scrypt`
- JWT access tokens signed with `AUTH_JWT_SECRET`
- optional `X-API-Key` auth for service clients
- API keys stored as SHA-256 hashes, never in plaintext

Protected routes:

- `GET /auth/me`
- `POST /auth/api-keys`
- `POST /ingest/text`
- `POST /ingest/files`
- `POST /chat`
- `POST /chat/stream`
- `DELETE /admin/reset`

Unprotected routes:

- `GET /health`
- `POST /auth/token`

## Transport security

The application signs tokens and hashes credentials, but HTTP encryption itself must still be provided by TLS at the deployment layer. If `AUTH_REQUIRE_HTTPS=true`, authenticated requests are rejected unless the request scheme is `https` or the proxy sends `X-Forwarded-Proto: https`.

## Current architectural limitations

- One canonical embedding provider/model is enforced for indexed data.
- Request payloads expose `embedding_provider` and `embedding_model`, but they must match the canonical configured pair in this MVP.
- Provider streaming is implemented, but integration tests against live providers are not included.
- TLS termination is not implemented in the app itself.

Current repository default canonical embedding pair:

- provider: `ollama`
- model: `qwen3-embedding`
- dimension: `4096`
