# API

Base URL during local development:

```text
http://localhost:9010
```

When using the provided Docker Compose stack, this base URL is exposed by the `app` container on the host.

If `HOST_APP_PORT` is set to a different value, replace `9010` in the examples with that host port.

## GET /health

Checks:

- API availability
- PostgreSQL connectivity
- Redis connectivity
- Ollama reachability
- OpenAI config presence
- Gemini config presence

### Example

```bash
curl http://localhost:9010/health
```

### Response shape

```json
{
  "status": "ok",
  "app": "backend-rag-multipurpose",
  "postgres": {
    "ok": true,
    "detail": "connected"
  },
  "redis": {
    "ok": true,
    "detail": "connected"
  },
  "providers": {
    "ollama": {
      "ok": true,
      "detail": "reachable",
      "enabled": true,
      "provider": "ollama",
      "capabilities": ["chat", "embeddings"],
      "configuration_present": true
    }
  },
  "assumptions": {
    "default_generation_provider": "ollama",
    "default_generation_model": "llama3.2",
    "default_embedding_provider": "ollama",
    "default_embedding_model": "qwen3-embedding"
  }
}
```

## POST /auth/token

Exchanges username/password credentials for a bearer token.

### Request

```json
{
  "username": "admin",
  "password": "change-me-immediately"
}
```

### Example

```bash
curl -X POST http://localhost:9010/auth/token ^
  -H "Content-Type: application/json" ^
  -d "{\"username\":\"admin\",\"password\":\"change-me-immediately\"}"
```

### Response shape

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in_seconds": 3600,
  "user": {
    "id": "...",
    "username": "admin",
    "is_admin": true,
    "auth_type": "bearer"
  }
}
```

## GET /auth/me

Returns the authenticated principal.

### Example

```bash
curl http://localhost:9010/auth/me ^
  -H "Authorization: Bearer YOUR_JWT"
```

## POST /auth/api-keys

Creates a hashed API key for the authenticated user. The plaintext key is returned once.

### Request

```json
{
  "name": "backend-client"
}
```

### Example

```bash
curl -X POST http://localhost:9010/auth/api-keys ^
  -H "Authorization: Bearer YOUR_JWT" ^
  -H "Content-Type: application/json" ^
  -d "{\"name\":\"backend-client\"}"
```

### Response shape

```json
{
  "api_key": "rag_ab12cd34_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "key_prefix": "ab12cd34",
  "name": "backend-client",
  "created_at": "2026-03-15T12:00:00Z"
}
```

## POST /ingest/text

Accepts one or more raw text items in JSON.

Authentication:

- `Authorization: Bearer YOUR_JWT`
- or `X-API-Key: YOUR_API_KEY`

### Request

```json
{
  "items": [
    {
      "title": "Company Overview",
      "content": "# Services\nWe offer AI chatbot implementation.",
      "source_type": "markdown",
      "url": "https://example.com/overview",
      "metadata": {
        "team": "sales"
      }
    }
  ],
  "embedding_provider": "ollama",
  "embedding_model": "qwen3-embedding"
}
```

### Example

```bash
curl -X POST http://localhost:9010/ingest/text ^
  -H "Authorization: Bearer YOUR_JWT" ^
  -H "Content-Type: application/json" ^
  -d "{\"items\":[{\"title\":\"Company Overview\",\"content\":\"# Services\nWe offer AI chatbot implementation.\",\"source_type\":\"markdown\"}]}"
```

### Response shape

```json
{
  "documents_inserted": 1,
  "chunks_inserted": 1,
  "embedding_provider": "ollama",
  "embedding_model": "qwen3-embedding",
  "results": [
    {
      "filename": "Company Overview",
      "detected_type": "markdown",
      "success": true,
      "chunks_created": 1,
      "error": null,
      "document_id": "..."
    }
  ]
}
```

## POST /ingest/files

Accepts multipart form uploads.

Authentication:

- `Authorization: Bearer YOUR_JWT`
- or `X-API-Key: YOUR_API_KEY`

### Multipart fields

- `files`: one or many files
- `source_type`: optional override
- `tags`: optional JSON array string, JSON string, or comma-separated string
- `metadata`: optional JSON object string or plain string
- `embedding_provider`: optional canonical embedding provider
- `embedding_model`: optional canonical embedding model

Notes for Swagger UI:

- leave optional multipart text inputs empty if you are not using them
- the backend ignores the Swagger placeholder value `string` for optional multipart text fields

### Example

```bash
curl -X POST http://localhost:9010/ingest/files ^
  -H "X-API-Key: YOUR_API_KEY" ^
  -F "files=@C:\path\to\overview.md" ^
  -F "files=@C:\path\to\services.csv" ^
  -F "tags=[\"portfolio\",\"demo\"]" ^
  -F "metadata={\"team\":\"solutions\"}"
```

Also valid:

```bash
curl -X POST http://localhost:9010/ingest/files ^
  -H "X-API-Key: YOUR_API_KEY" ^
  -F "files=@C:\path\to\overview.md" ^
  -F "tags=portfolio,demo"
```

Also valid:

```bash
curl -X POST http://localhost:9010/ingest/files ^
  -H "X-API-Key: YOUR_API_KEY" ^
  -F "files=@C:\path\to\overview.md" ^
  -F "metadata=uploaded-from-swagger"
```

### Response shape

```json
{
  "total_files": 2,
  "succeeded": 2,
  "failed": 0,
  "total_chunks_inserted": 5,
  "embedding_provider": "ollama",
  "embedding_model": "qwen3-embedding",
  "results": [
    {
      "filename": "overview.md",
      "detected_type": "md",
      "success": true,
      "chunks_created": 2,
      "error": null,
      "document_id": "..."
    }
  ]
}
```

## POST /chat

Returns a full JSON response.

Authentication:

- `Authorization: Bearer YOUR_JWT`
- or `X-API-Key: YOUR_API_KEY`

### Request

```json
{
  "message": "What services do we offer?",
  "session_id": "demo-session",
  "chat_history": [],
  "top_k": 5,
  "provider": "ollama",
  "model": "llama3.2",
  "embedding_provider": "ollama",
  "embedding_model": "qwen3-embedding"
}
```

### Example

```bash
curl -X POST http://localhost:9010/chat ^
  -H "Authorization: Bearer YOUR_JWT" ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"What services do we offer?\",\"provider\":\"ollama\",\"model\":\"llama3.2\"}"
```

### Response shape

```json
{
  "answer": "We offer AI chatbot implementation.",
  "citations": [
    {
      "document_id": "...",
      "chunk_id": "...",
      "title": "Company Overview",
      "url": null,
      "source_type": "markdown",
      "snippet": "We offer AI chatbot implementation.",
      "metadata": {
        "chunk_index": 0
      }
    }
  ],
  "provider": "ollama",
  "model": "llama3.2",
  "embedding_provider": "ollama",
  "embedding_model": "qwen3-embedding",
  "used_fallback": false
}
```

## POST /chat/stream

Returns Server-Sent Events.

Authentication:

- `Authorization: Bearer YOUR_JWT`
- or `X-API-Key: YOUR_API_KEY`

### Example

```bash
curl -N -X POST http://localhost:9010/chat/stream ^
  -H "Authorization: Bearer YOUR_JWT" ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"Summarize our offerings.\",\"provider\":\"ollama\",\"model\":\"llama3.2\"}"
```

### Event sequence

- `metadata`
- `chunk`
- `chunk`
- `done`

### `metadata` event payload

```json
{
  "provider": "ollama",
  "model": "llama3.2",
  "embedding_provider": "ollama",
  "embedding_model": "qwen3-embedding",
  "used_fallback": false
}
```

### `chunk` event payload

```json
{
  "delta": "partial text"
}
```

### `done` event payload

```json
{
  "answer": "final answer",
  "citations": [],
  "used_fallback": false
}
```

## DELETE /admin/reset

Deletes all indexed documents and chunk embeddings from PostgreSQL, then clears this app's Redis keys for:

- retrieval cache
- embedding cache
- session storage
- rate limiting

This is a backend reset endpoint for local development and demos. It does not call Redis `FLUSHDB`.

Authentication:

- `Authorization: Bearer YOUR_JWT`

### Example

```bash
curl -X DELETE http://localhost:9010/admin/reset ^
  -H "Authorization: Bearer YOUR_JWT"
```

### Response shape

```json
{
  "status": "ok",
  "documents_deleted": 3,
  "chunks_deleted": 14,
  "redis_keys_deleted": 9
}
```

## Error behavior

- Missing authentication returns HTTP `401`
- Invalid or expired bearer tokens return HTTP `401`
- Invalid API keys return HTTP `401`
- Non-admin requests to admin-only routes return HTTP `403`
- `AUTH_REQUIRE_HTTPS=true` rejects authenticated non-HTTPS requests with HTTP `403`
- Invalid provider values return HTTP `400`
- Missing provider credentials return HTTP `400`
- Provider reachability failures return HTTP `503`
- Chat rate limit failures return HTTP `429`
