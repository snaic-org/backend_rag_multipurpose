# Providers and Models

## Generation providers

Implemented generation providers:

- OpenAI
- Gemini
- Ollama
- NVIDIA NIM via the `nim` provider alias

These are switchable per request on:

- `POST /chat`
- `POST /chat/stream`

Relevant request fields:

- `provider`
- `model`

Default generation selection can also be profile-based through:

- the selectable catalog is defined in `backend/app/core/defaults.py`
- the active profile is stored in PostgreSQL and managed through the admin model-selection endpoints

Startup defaults come from these env values:

- `DEFAULT_GENERATION_PROVIDER`
- `DEFAULT_GENERATION_MODEL`
- `DEFAULT_EMBEDDING_PROVIDER`
- `DEFAULT_EMBEDDING_MODEL`
- `DEFAULT_EMBEDDING_DIMENSION`

## Embedding providers

Implemented embedding providers:

- OpenAI
- Gemini
- Ollama
- NVIDIA NIM via the `nim` provider alias

Relevant request fields:

- `embedding_provider`
- `embedding_model`

## Important constraint

Embedding providers are implemented through named profiles. That means:

- generation providers are fully switchable per request
- embedding provider/model/dimension are selected together through `embedding_profile`
- each embedding dimension is stored in its own Qdrant collection
- new dimensions are created automatically on first use

## Provider config env vars

- `OPENAI_API_KEY`
- `NIM_API_KEY`
- `GEMINI_API_KEY`
- `OLLAMA_BASE_URL`
- `RERANK_ENABLED`
- `RERANK_MODEL`
- `CHAT_THINKING_ENABLED`

## Defaults

- the selectable generation and embedding catalogs are defined in `backend/app/core/defaults.py`
- the active generation and embedding selections are stored in PostgreSQL

Current repository catalog:

- catalog entries are seeded in code from `backend/app/core/defaults.py`
- `SIMILARITY_THRESHOLD` uses the code default
- `RERANK_ENABLED=false`

Generation profile registry:

- `GET /admin/model-catalog` returns the available generation profiles
- `GET /admin/model-selection` returns the active generation profile
- `PUT /admin/model-selection` updates it

Embedding profile registry:

- `GET /admin/model-catalog` returns the available embedding profiles
- `GET /admin/model-selection` returns the active embedding profile
- `PUT /admin/model-selection` updates it

## OpenAI

Generation route implementation:

- `POST https://api.openai.com/v1/chat/completions`

Embedding route implementation:

- `POST https://api.openai.com/v1/embeddings`

OpenAI is implemented and can be selected with a profile. The startup seed uses the OpenAI generation and embedding profiles unless an admin changes them later.

OpenAI uses the fixed public API endpoint internally, so there is no `OPENAI_BASE_URL` setting to manage.

NIM is implemented as a dedicated alias so the config stays explicit:

- generation can be selected through the admin model-selection endpoint
- generation and embeddings use the built-in NVIDIA base URL unless you override it in env
- embeddings use a profile with `provider="nim"`
- NIM thinking follows `CHAT_THINKING_ENABLED` and falls back automatically if the model rejects reasoning mode

Relevant NIM model IDs:

- `nvidia/llama-3.3-nemotron-super-49b-v1.5`
- `nvidia/llama-nemotron-embed-1b-v2`
- `nvidia/llama-nemotron-rerank-1b-v2`

The embed model uses a `2048`-dimensional vector space, so its Qdrant profile should declare `dimension=2048`.

## Gemini

Generation route implementation:

- `:generateContent`
- `:streamGenerateContent`

Embedding route implementation:

- `:embedContent`

Note:

- Gemini embeddings are implemented in code
- the current MVP stores each embedding dimension in its own Qdrant collection

## Ollama

Generation route implementation:

- `POST /api/chat`

Embedding route implementation:

- `POST /api/embeddings`

Health check route:

- `GET /api/tags`

Default runtime mode:

- Ollama runs outside Docker on the host machine
- the app container reaches it through `http://host.docker.internal:11434`

Optional runtime mode:

- Ollama can be added with `backend/docker-compose.ollama.yml`
- in that mode the app uses `http://ollama:11434`

Current default Ollama embedding model:

- `rjmalagon/gte-qwen2-1.5b-instruct-embed-f16`

Current default Ollama embedding dimension:

- `1536`

## Reranking

Reranking is optional and disabled by default.

When enabled, retrieval will over-fetch candidates and send them through the configured reranker before the prompt is built.

Implementation route:

- `POST /v1/ranking`

Default NVIDIA rerank model:

- `nvidia/llama-nemotron-rerank-1b-v2`

## Error conditions

Examples:

- missing `OPENAI_API_KEY` returns a clear error
- missing `GEMINI_API_KEY` returns a clear error
- missing `NIM_API_KEY` returns a clear error when the configured NIM endpoint requires one
- unreachable Ollama returns a clear error
- missing `NIM_API_KEY` returns a clear error when the configured rerank endpoint requires one
- unsupported provider names return HTTP `400`
