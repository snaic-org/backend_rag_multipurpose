# Development Notes

This document captures developer-facing ownership notes and change guidance that do not belong on the root README.

## Change map

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

## Typical change flow

- If you change a model name or provider, update `backend/app/core/config.py`, then update the active startup defaults in `backend/.env` for local Docker and `deploy/ecs/task-definition.json` for ECS, plus the examples and docs.
- If you change a request or response field, update `backend/app/models/schemas.py` first, then adjust the services and any tests that depend on it.
- If you change how NIM works, keep `NIM_API_KEY` and the NIM embedding profile in sync across local env, ECS, and docs. Use `scripts/sync-provider-urls.ps1` to write the NIM base URL and rerank URL into `backend/.env` when you want explicit values there. Use `GET /admin/model-selection` and `PUT /admin/model-selection` to change the active generation or embedding profile without editing code.
- If you add a new deployment secret, add it to `backend/.env.example`, `deploy/ecs/task-definition.json`, and the ECS README / deployment docs together.
- If you change retrieval behavior, update `backend/app/services/retrieval.py`, `backend/app/services/rerank.py`, and the RAG pipeline docs together.

## Related docs

- [Feature Overview](feature-overview.md)
- [Feature Log](feature-log.md)

## Open deployment TODOs

- move PostgreSQL/Redis/Qdrant out of the single ECS task before treating the deployment as production durable
- if task public IPs should be disabled, add NAT Gateway or VPC endpoints for SSM, ECR, and CloudWatch first
- define any additional custom DNS names beyond `multiragapi.snaic.net`

## Recent ECS HTTPS Deployment Update

- `scripts/redeploy-ecs.ps1` now defaults to the HTTPS ALB deployment path for `multiragapi.snaic.net`
- the script creates or reuses:
  - an internet-facing ALB
  - an IP target group for `nginx:80`
  - HTTPS `443` and HTTP-to-HTTPS redirect listeners
  - Route 53 alias records
  - ALB and ECS task security groups
- the target group health check is normalized to `/nginx-health` on every run
- the script keeps Fargate task public IP assignment enabled by default so the task can reach SSM/ECR/CloudWatch without NAT or VPC endpoints
- the script updates an existing ALB subnet set to include the ECS service subnet/AZ so targets do not fail health checks because the task AZ is not enabled on the load balancer

## Recent prompt-builder simplification

- `backend/app/services/prompt_builder.py` now builds a simpler payload shape:
  - `system`: stored system prompt
  - optional single `assistant` message containing combined rolling history
  - `user`: current question
  - `user`: retrieved context
- `backend/app/models/schemas.py` `PromptContext` now carries:
  - `messages`
  - `citations`
  - `retrieved_chunks`
- `PromptContext.system_prompt` was removed because it duplicated the system prompt already present in `messages[0]`
- prompt-builder cleanup also removed:
  - managed system prompt hash helpers
  - unused token-pattern / stopword helper constants

When editing this area:

- keep prompt payload structure changes in sync with `backend/tests/test_prompt_builder.py`
- if you change `PromptContext`, search callers under `backend/app/services/` before assuming the schema is local-only
- if live behavior does not match code changes, check the stored admin system prompt first before assuming `DEFAULT_SYSTEM_PROMPT` is active

## Eval tooling update

- `eval/main.py` is now documented as DeepEval-first for runtime scoring
- DeepEval metrics currently used:
  - answer relevancy
  - faithfulness
  - contextual precision
  - contextual recall
  - contextual relevancy
  - G-Eval answer correctness
- custom `hit_rate` and `mrr` remain in the eval runner as judge-based retrieval metrics
- `eval/README.md` reflects this split explicitly:
  - Ragas is still used for offline testset generation (`eval/create_domain_test.py`)
  - DeepEval is used for online answer-quality scoring (`eval/main.py`)

## Recent prompt-debugging fixes

- `backend/app/services/prompt_builder.py` now chooses the top chunks by `similarity_score` within each document group before building the retrieved-context message
- prompt context formatting was reduced to answer-relevant content only:
  - source label
  - KB excerpt labels
  - excerpt text
- `backend/app/core/config.py` now exposes `CHAT_MAX_EXCERPTS_PER_DOCUMENT`
- `backend/app/api/chat.py` now applies a server-side debug gate through `chat_debug_enabled` before returning:
  - provider/model details
  - fallback state
  - `retrieved_chunks`
  - `prompt_messages`
- public `/chat` and `/chat/stream` responses return only `answer`, compact citation IDs, and `session_id`
- local `backend/.env` currently sets `CHAT_DEBUG_ENABLED=false`

When editing this area:

- if debug output appears empty, check `CHAT_DEBUG_ENABLED` first
- if retrieval looks correct in raw debug output but the answer still misses the fact, inspect the formatted retrieved-context message before blaming the model
