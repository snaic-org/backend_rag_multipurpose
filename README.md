# Backend RAG Multipurpose

A production-oriented RAG chatbot backend API service built as a secure and highly customizable foundation for AI assistant solutions across websites, internal company tools, support platforms, and domain-specific business applications. It enables organizations to ingest documents, retrieve relevant knowledge, and generate grounded conversational responses through a scalable architecture designed for integration into real-world products. With support for authentication, semantic search, caching, rate limiting, configurable model selection, and multi-provider orchestration across OpenAI, Gemini, Ollama, and NVIDIA NIM, it is well suited for internal knowledge assistants, website chatbots, enterprise helpdesks, and other retrieval-driven AI experiences.

## Feature overview

- Secure authenticated API for document ingestion, semantic retrieval, and grounded chat
- Multi-provider orchestration across OpenAI, Gemini, Ollama, and NVIDIA NIM
- Built for website chatbots, internal assistants, support tools, and domain-specific AI products
- Production-oriented backend features including caching, rate limiting, guardrails, streaming, and admin controls
- Admin activity monitoring for chatbot usage review and safety auditing

## Getting started

### Prerequisites and setup

- Python 3.11+
- Docker Desktop with Docker Compose
- a copy of `backend/.env.example` saved as `backend/.env`
- provider credentials for the models you plan to use
- for authenticated local use, change the bootstrap admin password and JWT secret before exposing the API outside local development
- `backend/.env` is gitignored, so put real secrets there and never commit it

Authentication defaults in `backend/.env.example`:

- `AUTH_ENABLED=true`
- `AUTH_BOOTSTRAP_ADMIN_USERNAME=admin`
- `AUTH_BOOTSTRAP_ADMIN_PASSWORD=change-me-immediately`
- `AUTH_JWT_SECRET=change-me-immediately`

Replace these values in `backend/.env` before real use:

- `AUTH_BOOTSTRAP_ADMIN_USERNAME`
- `AUTH_BOOTSTRAP_ADMIN_PASSWORD`
- `AUTH_JWT_SECRET`
- `OPENAI_API_KEY`, `GEMINI_API_KEY`, or `NIM_API_KEY` depending on provider
- `POSTGRES_DSN` if you are not using the default local Docker database
- `REDIS_URL`, `QDRANT_URL`, and `OLLAMA_BASE_URL` if your services run on different hosts

### Local setup with external model providers or host Ollama

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r backend/requirements.txt
copy backend\.env.example backend\.env
docker compose -f backend/docker-compose.yml up --build -d
```

This starts the local stack behind `nginx` and exposes the API on port `9010` by default.

Use this mode when:

- you are using OpenAI, Gemini, or NVIDIA NIM
- you want Ollama running on the host machine instead of inside Docker

Default Ollama behavior in this setup:

- host Ollama listens on `http://localhost:11434`
- the Dockerized app connects to it through `http://host.docker.internal:11434`

If host port `9010` is blocked, set a different one before starting Compose:

```bash
set HOST_PROXY_PORT=8010
docker compose -f backend/docker-compose.yml up --build -d
```

### Local setup with Ollama in Docker

```bash
copy backend\.env.example backend\.env
docker compose -f backend/docker-compose.yml -f backend/docker-compose.ollama.yml up --build -d
```

Use this mode when you want the entire local stack, including Ollama, running inside Docker.

### ECS deployment

For the ECS on Fargate path, use the dedicated deployment guide in [deploy/ecs/README.md](deploy/ecs/README.md).

The current ECS setup:

- runs `nginx`, FastAPI, PostgreSQL, Qdrant, and Redis in one ECS task
- uses NVIDIA NIM as the default hosted provider in the task template
- requires task-role, execution-role, CloudWatch, SSM, ECR, task-definition, and service-definition setup
- is suitable for demos and single-task deployments, but not as a highly available stateful production architecture

Before deploying to ECS, replace the environment-specific values in `deploy/ecs/task-definition.json` and related AWS assets:

- database password placeholders such as `<CHANGE_ME_DB_PASSWORD>`
- AWS account IDs, regions, cluster names, subnet IDs, and security group IDs
- ECR image URLs and repository prefixes
- SSM parameter ARNs and secret names for `NIM_API_KEY`, `AUTH_JWT_SECRET`, `AUTH_BOOTSTRAP_ADMIN_USERNAME`, and `AUTH_BOOTSTRAP_ADMIN_PASSWORD`

Use [docs/deployment.md](docs/deployment.md) for the broader deployment notes and [deploy/ecs/README.md](deploy/ecs/README.md) for the step-by-step ECS instructions.

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

# Ownership and License

Built by [Isfaque AL Kaderi Tuhin](https://github.com/iahin) | [LinkedIn](https://www.linkedin.com/in/iatuhin/) | [Email](mailto:shioktech@gmail.com)

Licensed under Apache License 2.0.

