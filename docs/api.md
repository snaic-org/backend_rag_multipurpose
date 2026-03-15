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

## GET /auth/me

Returns the authenticated principal.

### Example

```bash
curl http://localhost:9010/auth/me ^
  -H "Authorization: Bearer YOUR_JWT"
```

Swagger UI note:

- use the `Authorize` button
- for bearer auth, paste only the token value, not the surrounding quotes
- Swagger adds the `Bearer` prefix automatically for the HTTP bearer scheme

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

## GET /auth/api-keys

Lists API keys for the authenticated user.

### Example

```bash
curl http://localhost:9010/auth/api-keys ^
  -H "Authorization: Bearer YOUR_JWT"
```

## DELETE /auth/api-keys/{api_key_id}

Revokes one API key belonging to the authenticated user.

### Example

```bash
curl -X DELETE http://localhost:9010/auth/api-keys/API_KEY_UUID ^
  -H "Authorization: Bearer YOUR_JWT"
```

Response:

- HTTP `204 No Content`

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

### Example

```bash
curl -X POST http://localhost:9010/ingest/files ^
  -H "X-API-Key: YOUR_API_KEY" ^
  -F "files=@C:\path\to\overview.md" ^
  -F "files=@C:\path\to\services.csv" ^
  -F "tags=[\"portfolio\",\"demo\"]" ^
  -F "metadata={\"team\":\"solutions\"}"
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

## DELETE /admin/reset

Deletes all indexed documents and chunk embeddings from PostgreSQL, then clears this app's Redis keys for:

- retrieval cache
- embedding cache
- session storage
- rate limiting

Authentication:

- `Authorization: Bearer YOUR_JWT`

### Example

```bash
curl -X DELETE http://localhost:9010/admin/reset ^
  -H "Authorization: Bearer YOUR_JWT"
```

## Admin user CRUD

All user-management routes require an admin bearer token.

### POST /admin/users

Creates a user.

```json
{
  "username": "analyst",
  "password": "replace-with-a-strong-password",
  "is_active": true,
  "is_admin": false
}
```

### GET /admin/users

Lists users.

### GET /admin/users/{user_id}

Returns one user.

### PATCH /admin/users/{user_id}

Updates one or more of:

- `username`
- `password`
- `is_active`
- `is_admin`

### DELETE /admin/users/{user_id}

Deletes a user. The current admin cannot delete their own account through this endpoint.

### Examples

```bash
curl -X POST http://localhost:9010/admin/users ^
  -H "Authorization: Bearer YOUR_JWT" ^
  -H "Content-Type: application/json" ^
  -d "{\"username\":\"analyst\",\"password\":\"replace-with-a-strong-password\",\"is_active\":true,\"is_admin\":false}"
```

```bash
curl http://localhost:9010/admin/users ^
  -H "Authorization: Bearer YOUR_JWT"
```

```bash
curl -X PATCH http://localhost:9010/admin/users/USER_UUID ^
  -H "Authorization: Bearer YOUR_JWT" ^
  -H "Content-Type: application/json" ^
  -d "{\"is_active\":false}"
```

```bash
curl -X DELETE http://localhost:9010/admin/users/USER_UUID ^
  -H "Authorization: Bearer YOUR_JWT"
```

## Error behavior

- Missing authentication returns HTTP `401`
- Malformed bearer headers return HTTP `401`
- Invalid or expired bearer tokens return HTTP `401`
- Invalid API keys return HTTP `401`
- Non-admin requests to admin-only routes return HTTP `403`
- Duplicate usernames return HTTP `400`
- Missing users or API keys return HTTP `404`
- `AUTH_REQUIRE_HTTPS=true` rejects authenticated non-HTTPS requests with HTTP `403`
- Invalid provider values return HTTP `400`
- Missing provider credentials return HTTP `400`
- Provider reachability failures return HTTP `503`
- Chat rate limit failures return HTTP `429`
