# Feature Overview

This document summarizes the current implemented capabilities of the backend RAG platform in one place without the dated release history from the feature log.

## API endpoints

- `GET /health`
- `POST /auth/token`
- `GET /auth/me`
- `POST /auth/api-keys`
- `POST /ingest/text`
- `POST /ingest/files`
- `POST /chat`
- `POST /chat/stream`
- `POST /chat/feedback`
- `DELETE /admin/reset`
- `GET /admin/chat-activity`
- `GET /admin/chat-feedback`
- `GET /admin/system-prompt`
- `PUT /admin/system-prompt`

## Core platform capabilities

- PostgreSQL storage for users, API keys, and documents
- PostgreSQL storage for the editable system prompt
- PostgreSQL storage for chat activity audit records
- PostgreSQL storage for chat feedback records
- Qdrant storage for chunk embeddings and similarity search
- Redis rate limiting, retrieval caching, embedding caching, and optional session storage
- Request-level generation provider and model selection
- Heuristic multi-query retrieval for multi-clause questions
- Source-diversity-aware retrieved-context selection
- Optional reranking for retrieval quality
- Multipart ingestion for `txt`, `md`, `docx`, `csv`, and `xlsx`
- JWT bearer authentication and hashed API keys
- Admin-only system prompt management through JWT bearer auth
- Admin-only chat activity monitoring with overview analytics and filtered search
- Chat feedback capture with 1 to 5 rating and optional comments
- Chat guardrails for spam, quota, prompt-injection phrases, and output limits
- Exact duplicate knowledge-base uploads are deduplicated by normalized content hash plus embedding profile
- Grounded chatbot behavior tuned for retrieved-context responses

## Chat guardrails

Default chat safety controls are enforced in code and can be overridden through `backend/.env` or the ECS task definition.

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

The assistant is instructed to stay grounded to retrieved context. When context is missing, it falls back to a user-friendly low-confidence response instead of improvising.

## Supported integration use cases

- website chatbots
- internal knowledge assistants
- support and helpdesk assistants
- document-based Q&A tools
- domain-specific AI assistant backends

## Admin monitoring

The admin activity endpoint returns:

- an `overview` block with aggregate counts and top providers/models
- an `activities` list containing the matching chat audit records

Supported filters:

- `limit`
- `start_at`
- `end_at`
- `keyword`

Accepted date formats:

- `DD/MM/YYYY`
- ISO 8601 timestamps

## Feedback capture

Users can submit chat-session feedback through:

- `POST /chat/feedback`

Admins can review submitted feedback through:

- `GET /admin/chat-feedback`

Feedback records include:

- `session_id`
- `rating`
- `date`
- `full_chat_text`
- `comments`

When a client sends `session_id` on `/chat` or `/chat/stream`, the backend now echoes that same session id back in the response path.

## Related documentation

- [Architecture](architecture.md)
- [RAG Pipeline](rag-pipeline.md)
- [Providers and Models](providers-and-models.md)
- [Development Notes](development-log.md)
- [Feature Log](feature-log.md)

## Known deployment gaps

- HTTPS termination is not yet implemented in the checked-in ECS deployment assets
- Custom DNS/domain routing is not yet implemented in the checked-in ECS deployment assets
