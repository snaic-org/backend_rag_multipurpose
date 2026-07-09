# Providers and Models

## Generation providers

Implemented generation providers:

- OpenAI
- Gemini
- Ollama
- NVIDIA NIM via the `nim` provider alias

Chat requests only accept `message`. Generation selection is profile-based through:

- the selectable catalog is defined in `backend/app/core/config.py`
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

Chat and ingestion requests do not accept embedding provider/model fields.

## Important constraint

Embedding providers are implemented through named profiles. That means:

- generation provider/model are selected by the active generation profile
- embedding provider/model/dimension are selected by the active embedding profile
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

- the selectable generation and embedding catalogs are defined in `backend/app/core/config.py`
- the active generation and embedding selections are stored in PostgreSQL

Current repository catalog:

- catalog entries are seeded in code from `backend/app/core/config.py`
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

- `nvidia/nemotron-3-super-120b-a12b` - active default generation model (`nim_3super120`); most articulate/warm tone, best for this RAG chatbot
- `nvidia/nemotron-3-nano-30b-a3b` - faster/smaller generation option (`nim_nano30`); noticeably terser tone, follows the warm persona less faithfully
- `nvidia/llama-3.3-nemotron-super-49b-v1.5`
- `nvidia/llama-nemotron-embed-1b-v2`
- `nvidia/llama-nemotron-rerank-1b-v2`

The embed model uses a `2048`-dimensional vector space, so its Qdrant profile should declare `dimension=2048`.

### Reasoning (thinking) and generation controls

- Nemotron 3 models default to reasoning ON. The provider sends `chat_template_kwargs: {"enable_thinking": <CHAT_THINKING_ENABLED>}`; the Nemotron 3 NIM API honors this exact key.
- For this grounded RAG chatbot, keep `CHAT_THINKING_ENABLED=false`. Accuracy here comes from retrieval and clean data, not model reasoning. Reasoning ON added ~2s latency and produced terser, more robotic answers with no accuracy gain on grounded questions.
- `CHAT_SHOW_THINKING_BLOCK=false` strips any `<think>` trace from user-facing output.
- If reasoning is ever enabled, raise `CHAT_MAX_RESPONSE_TOKENS` (the hidden trace shares that budget and can truncate the answer).
- `CHAT_TEMPERATURE` (default `0.0`, deployed `0.2`) trades warmth for factual determinism; keep it low for a public factual bot to reduce hallucination risk.
- Observed end-to-end chat latency is ~3-6s and is dominated by the retrieval pipeline (multi-query embeddings + rerank), not by the generation model choice.

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
