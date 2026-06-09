# ECS Fargate deployment

This repository now includes a pragmatic single-task ECS on Fargate deployment path.

Before using this guide, replace the repository-specific values from the examples with your own AWS account ID, region, cluster name, subnet IDs, security groups, ECR repositories, SSM parameter ARNs, and database password values.

What it deploys in one ECS task:

- `nginx` reverse proxy on port `80`
- FastAPI app on port `8000`
- PostgreSQL for document/auth metadata on port `5432`
- Qdrant for chunk vectors and similarity search on port `6333`
- Redis on port `6379`

This keeps the current local Compose architecture inside one ECS task.

Current ECS template defaults:

- generation provider: `nim`
- generation model: `nvidia/nemotron-3-super-120b-a12b`
- embedding provider: `nim`
- embedding model: `nvidia/llama-nemotron-embed-1b-v2`
- embedding dimension: `2048`
- the selectable catalog and config defaults live in `backend/app/core/config.py`
- the active generation and embedding profiles are seeded from `deploy/ecs/task-definition.json` on startup
- reasoning visibility is controlled by `CHAT_THINKING_ENABLED`
- public chat responses are minimal because `CHAT_DEBUG_ENABLED=false`
- reranking is enabled and reuses `NIM_API_KEY`

Chat safety defaults are enforced by the app and can be overridden in the task definition if needed:

- burst rate limit: `CHAT_RATE_LIMIT_REQUESTS`
- daily quota: `CHAT_DAILY_LIMIT_REQUESTS`
- prompt and context caps: `CHAT_MAX_MESSAGE_CHARS`, `CHAT_MAX_INPUT_TOKENS`, `CHAT_MAX_CONTEXT_CHARS`, `CHAT_MAX_CONTEXT_TOKENS`
- retrieval clamp: `CHAT_MIN_TOP_K`, `CHAT_MAX_TOP_K`
- output cap: `CHAT_MAX_RESPONSE_CHARS`, `CHAT_MAX_RESPONSE_TOKENS`

The `nginx` image uses one shared config template for both local Docker and ECS.

- local Docker sets `NGINX_UPSTREAM_HOST=app`
- ECS/Fargate sets `NGINX_UPSTREAM_HOST=127.0.0.1`

## Important constraint

This is a single-task Fargate design with stateful sidecars.

- It is not highly available.
- PostgreSQL, Qdrant, and Redis use Fargate ephemeral task storage.
- Restarting or replacing the task can lose PostgreSQL, Qdrant, and Redis data.
- This is acceptable only for demos, dev, or disposable environments.
- For real production, move PostgreSQL to RDS and use a managed or persistent Qdrant/Redis setup.
- An AWS Application Load Balancer is still optional. `nginx` can remain the public entry point inside the task.

## Images to build

Build from the repository root:

```powershell
docker build -f backend/Dockerfile -t rag-backend:latest backend
docker build -f backend/nginx/Dockerfile -t rag-nginx:latest backend/nginx
docker build -f backend/postgres/Dockerfile -t rag-postgres:latest backend
```

Verify the local images exist before tagging:

```powershell
docker images
docker images | findstr rag-
```

If you do not see `rag-backend`, `rag-nginx`, and `rag-postgres`, build them first with the commands above.

Push the app images to ECR:

Replace the example AWS account ID, region, and repository prefix in the commands below before running them.

```powershell
aws ecr get-login-password --region ap-southeast-1 | docker login --username AWS --password-stdin 961341555117.dkr.ecr.ap-southeast-1.amazonaws.com

aws ecr create-repository --repository-name snaic_website/rag-backend
aws ecr create-repository --repository-name snaic_website/rag-nginx
aws ecr create-repository --repository-name snaic_website/rag-postgres

docker tag rag-backend:latest 961341555117.dkr.ecr.ap-southeast-1.amazonaws.com/snaic_website/rag-backend:latest
docker tag rag-nginx:latest 961341555117.dkr.ecr.ap-southeast-1.amazonaws.com/snaic_website/rag-nginx:latest
docker tag rag-postgres:latest 961341555117.dkr.ecr.ap-southeast-1.amazonaws.com/snaic_website/rag-postgres:latest

docker push 961341555117.dkr.ecr.ap-southeast-1.amazonaws.com/snaic_website/rag-backend:latest
docker push 961341555117.dkr.ecr.ap-southeast-1.amazonaws.com/snaic_website/rag-nginx:latest
docker push 961341555117.dkr.ecr.ap-southeast-1.amazonaws.com/snaic_website/rag-postgres:latest
```

If Docker returns `no basic auth credentials`, run the `aws ecr get-login-password ... | docker login ...` command again and retry the push.

Redis can stay on the public `redis:7.4-alpine` image.

## AWS resources

Create:

1. One ECS cluster for Fargate tasks.
2. One or more public or private subnets for the Fargate service.
3. One Application Load Balancer for the public HTTPS endpoint.
4. One ACM public certificate for `multiragapi.snaic.net`.
5. One Route 53 alias record for `multiragapi.snaic.net` pointing to the ALB.
6. One ALB security group allowing inbound `443/tcp` and optional `80/tcp` for HTTP-to-HTTPS redirects.
7. One ECS task security group allowing inbound `80/tcp` only from the ALB security group.
8. One CloudWatch log group: `/ecs/backend-rag-multipurpose`.
9. One IAM task execution role: `ecsTaskExecutionRole`.
10. One IAM task role: `ecsTaskRole`.
11. SSM Parameter Store entries for secrets used by the app.
   - `/backend-rag/NIM_API_KEY`
   - `/backend-rag/AUTH_JWT_SECRET`
   - `/backend-rag/AUTH_BOOTSTRAP_ADMIN_USERNAME`
   - `/backend-rag/AUTH_BOOTSTRAP_ADMIN_PASSWORD`
   - `/backend-rag/POSTGRES_PASSWORD`

## HTTPS domain setup

Use `multiragapi.snaic.net` as the public API hostname. Do not point Route 53 at the ECS task public IP because Fargate task IPs change when the task is replaced.

Recommended public flow:

```text
multiragapi.snaic.net
  -> Route 53 A/AAAA alias
  -> Application Load Balancer HTTPS :443
  -> ALB target group HTTP :80
  -> ECS task nginx :80
  -> FastAPI app :8000
```

### 1. Request the certificate

In AWS Certificate Manager, in the same region as the ALB:

- Fully qualified domain name: `multiragapi.snaic.net`
- Allow export: `Disable export`
- Validation method: `DNS validation`
- Key algorithm: `RSA 2048`

After requesting the certificate, create the DNS validation record in Route 53. Wait until ACM shows the certificate status as `Issued`.

### 2. Create the target group

Create an ALB target group:

- Target type: `IP`
- Protocol: `HTTP`
- Port: `80`
- VPC: same VPC as the ECS service
- Health check path: `/nginx-health`
- Success codes: `200`

Fargate with `awsvpc` networking must use target type `IP`.

### 3. Create the ALB

Create an internet-facing Application Load Balancer:

- Scheme: `internet-facing`
- Listeners:
  - `HTTPS :443`
  - optional `HTTP :80`
- Subnets: at least two public subnets in different Availability Zones
- Security group inbound:
  - `443/tcp` from the internet
  - optional `80/tcp` from the internet only to redirect to HTTPS

Configure listeners:

- `HTTPS :443`: use the ACM certificate for `multiragapi.snaic.net` and forward to the ECS target group.
- `HTTP :80`: redirect to `HTTPS :443`.

### 4. Point Route 53 to the ALB

In the `snaic.net` public hosted zone, create:

- Record name: `multiragapi`
- Record type: `A`
- Alias: `Yes`
- Alias target: the Application Load Balancer

This creates `multiragapi.snaic.net`. Route 53 tracks the ALB DNS name, so no static ECS IP is needed.

### 5. Attach ECS service to the target group

Use the ALB service template as a starting point:

- `deploy/ecs/service-definition.alb.example.json`

Replace:

- `targetGroupArn`
- subnet IDs
- ECS task security group ID
- cluster and service names if yours differ

The ALB-backed service should usually use:

- `assignPublicIp`: `DISABLED`
- ECS task security group inbound `80/tcp` from the ALB security group only

The checked-in direct-public-IP service template remains at:

- `deploy/ecs/service-definition.json`

Use it only for temporary HTTP testing. If you use the direct HTTP template, set `AUTH_REQUIRE_HTTPS=false` in `deploy/ecs/task-definition.json`; the HTTPS domain deployment should keep it `true`.

## CloudWatch setup

Create the log group used by the ECS task:

1. Open `CloudWatch` in the AWS Console.
2. Go to `Log groups`.
3. Click `Create log group`.
4. Name it:
   - `/ecs/backend-rag-multipurpose`
5. Save it.

This is where logs from these containers will appear:

- `nginx`
- `app`
- `postgres`
- `redis`

## IAM setup

You need two IAM roles for ECS tasks:

- `ecsTaskExecutionRole`
- `ecsTaskRole`

Even if you deploy with the AWS root account, these roles still must exist.

### Trust relationship for both roles

Both roles must trust `ecs-tasks.amazonaws.com`.

Use this trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

### Create `ecsTaskExecutionRole`

In the AWS Console:

1. Open `IAM`.
2. Go to `Roles`.
3. Click `Create role`.
4. Trusted entity type: `AWS service`.
5. Use case: `Elastic Container Service Task`.
6. Attach the managed policy:
   - `AmazonECSTaskExecutionRolePolicy`
7. Name the role:
   - `ecsTaskExecutionRole`
8. Create the role.

This role lets ECS:

- pull images from ECR
- write logs to CloudWatch
- read secrets from SSM

If your SSM SecureString values use a customer-managed KMS key, also allow:

- `kms:Decrypt`

### Create `ecsTaskRole`

In the AWS Console:

1. Open `IAM`.
2. Go to `Roles`.
3. Click `Create role`.
4. Trusted entity type: `AWS service`.
5. Use case: `Elastic Container Service Task`.
6. Do not add broad permissions unless the app actually needs AWS API access.
7. Name the role:
   - `ecsTaskRole`
8. Create the role.

For the current repository, this role can start minimal if the app itself is not calling AWS APIs directly.

## SSM setup

The ECS task definition reads secrets from AWS Systems Manager Parameter Store.

Create these parameters as `SecureString`:

- `/backend-rag/NIM_API_KEY`
- `/backend-rag/AUTH_JWT_SECRET`
- `/backend-rag/AUTH_BOOTSTRAP_ADMIN_USERNAME`
- `/backend-rag/AUTH_BOOTSTRAP_ADMIN_PASSWORD`
- `/backend-rag/POSTGRES_PASSWORD`

### Create the parameters in the AWS Console

1. Open `Systems Manager`.
2. Go to `Parameter Store`.
3. Click `Create parameter`.
4. For each parameter:
   - set `Name` to the exact value above
   - set `Tier` to `Standard`
   - set `Type` to `SecureString`
   - paste the real secret value
5. Save it.

Suggested values:

- `/backend-rag/NIM_API_KEY`: your NVIDIA API key
- `/backend-rag/AUTH_JWT_SECRET`: a long random secret
- `/backend-rag/AUTH_BOOTSTRAP_ADMIN_USERNAME`: your admin username
- `/backend-rag/AUTH_BOOTSTRAP_ADMIN_PASSWORD`: your admin password
- `/backend-rag/POSTGRES_PASSWORD`: the password used by both the app container and the postgres container

You can generate the JWT secret outside ECS from your local PowerShell terminal or from AWS CloudShell with:

```powershell
[Convert]::ToBase64String((1..64 | ForEach-Object { Get-Random -Maximum 256 } | ForEach-Object { [byte]$_ }))
```

or:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Then store that generated value in the `/backend-rag/AUTH_JWT_SECRET` SecureString parameter.

Example one-command SSM write:

```powershell
aws ssm put-parameter --region YOUR_REGION --name /backend-rag/AUTH_JWT_SECRET --type SecureString --overwrite --value ([Convert]::ToBase64String((1..64 | ForEach-Object { Get-Random -Maximum 256 } | ForEach-Object { [byte]$_ })))
```

The current ECS task template is NIM-based by default.

### Permissions note

Your `ecsTaskExecutionRole` must be able to read these parameters.

At minimum it needs:

- `ssm:GetParameters`

Example policy scope for the current NIM-based task:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameters"
      ],
      "Resource": [
        "arn:aws:ssm:ap-southeast-1:961341555117:parameter/backend-rag/NIM_API_KEY",
        "arn:aws:ssm:ap-southeast-1:961341555117:parameter/backend-rag/AUTH_JWT_SECRET",
        "arn:aws:ssm:ap-southeast-1:961341555117:parameter/backend-rag/AUTH_BOOTSTRAP_ADMIN_USERNAME",
        "arn:aws:ssm:ap-southeast-1:961341555117:parameter/backend-rag/AUTH_BOOTSTRAP_ADMIN_PASSWORD",
        "arn:aws:ssm:ap-southeast-1:961341555117:parameter/backend-rag/POSTGRES_PASSWORD"
      ]
    }
  ]
}
```

If you use a customer-managed KMS key for `SecureString`, it also needs:

- `kms:Decrypt`

## Task definition

Template file:

- `deploy/ecs/task-definition.json`

Replace:

- `<AWS_ACCOUNT_ID>`
- `<AWS_REGION>`
- `<CHANGE_ME_DB_PASSWORD>`
- example SSM parameter ARNs and any repository-specific image URIs

Recommended production edits before registering:

- Set `AUTH_REQUIRE_HTTPS=true` if TLS is terminated before traffic reaches `nginx`.
- Set `DEFAULT_GENERATION_PROVIDER`, `DEFAULT_GENERATION_MODEL`, `DEFAULT_EMBEDDING_PROVIDER`, `DEFAULT_EMBEDDING_MODEL`, and `DEFAULT_EMBEDDING_DIMENSION` in `deploy/ecs/task-definition.json` to the startup defaults you want.
- Update `backend/app/core/config.py` when you add or change the catalog of allowed generation or embedding models.
- Keep `CHAT_DEBUG_ENABLED=false` for public deployments unless you intentionally want provider/model details, retrieved chunks, prompt messages, fallback state, and full citations exposed in `/chat` and `/chat/stream` responses.
- Tune the chat guardrail env vars above if you need a different safety envelope.
- Keep the `nginx` env vars aligned with your task networking model.
- Adjust task `cpu`, `memory`, and `ephemeralStorage` to your workload.

Important:

- `CHAT_THINKING_ENABLED=true` allows providers to expose reasoning output when supported.
- `/ingest/text` accepts only `title` and `content`; `/ingest/files` accepts only file uploads. Source type, file metadata, `created_by`, `created_at`, and embedding selection are populated by the backend.
- `/chat` and `/chat/stream` accept only `message`; generation profile, embedding profile, retrieval limits, session behavior, and debug behavior are server-side settings.
- With `CHAT_DEBUG_ENABLED=false`, `/chat` returns only `answer`, compact citation IDs, and `session_id`; `/chat/stream` sends answer chunks plus the same compact final payload.
- `GET /admin/model-selection` shows the active generation and embedding profiles.
- `PUT /admin/model-selection` changes them without editing the task definition.
- ECS will keep running the old task definition until you register a new revision and update the service.
- The app container and postgres container both read `POSTGRES_PASSWORD` from SSM Parameter Store.

Then register:

```powershell
aws ecs register-task-definition --cli-input-json file://deploy/ecs/task-definition.json
```

## Service definition

Template file:

- `deploy/ecs/service-definition.alb.example.json` for the HTTPS `multiragapi.snaic.net` deployment
- `deploy/ecs/service-definition.json` only for temporary direct-task HTTP testing

Replace:

- `snaic_website_cluster` with your ECS cluster name
- subnet ids
- ECS task security group id
- target group ARN

Then create or update the service.

If the service does not exist yet:

```powershell
aws ecs create-service --cluster snaic_website_cluster --cli-input-json file://deploy/ecs/service-definition.alb.example.json
```

If the service already exists, register a new task definition revision and update the running service:

```powershell
aws ecs register-task-definition --cli-input-json file://deploy/ecs/task-definition.json --query 'taskDefinition.taskDefinitionArn' --output text
aws ecs update-service --cluster snaic_website_cluster --service backend-rag-multipurpose --task-definition <new-task-definition-arn> --force-new-deployment
```

## One-command redeploy

If you want a repeatable local command that builds, pushes, registers, and updates the ECS service, use:

```powershell
.\\scripts\\redeploy-ecs.ps1
```

That script:

- builds `rag-backend`, `rag-nginx`, and `rag-postgres`
- pushes them to ECR
- registers a new task definition revision from `deploy/ecs/task-definition.json`
- updates the ECS service with `--desired-count 1` and `--force-new-deployment`
- waits for the service to become stable

For the HTTPS ALB deployment, use the default command:

```powershell
.\scripts\redeploy-ecs.ps1
```

The script will:

- look up the issued ACM certificate for `multiragapi.snaic.net`
- discover public ALB subnets from the ECS service VPC
- create or reuse ALB and ECS task security groups
- create or reuse the target group
- create or reuse the ALB
- create HTTPS `443` and HTTP-to-HTTPS redirect listeners when missing
- attach the ECS service to the target group
- upsert the Route 53 alias record
- keep task public IP assignment enabled so the task can reach SSM/ECR/CloudWatch without NAT or VPC endpoints

Override defaults if needed:

```powershell
.\scripts\redeploy-ecs.ps1 -Region ap-southeast-1 -AccountId 961341555117 -Cluster snaic_website_cluster -Service backend-rag-multipurpose
```

The script defaults in `scripts/redeploy-ecs.ps1` are repository-specific examples. Override them for your own AWS environment instead of assuming they are portable defaults.

If you already pushed the images and only need to recycle ECS:

```powershell
.\scripts\redeploy-ecs.ps1 -SkipBuild -SkipPush
```

If you want a shorter or longer wait before the script gives up:

```powershell
.\scripts\redeploy-ecs.ps1 -TimeoutMinutes 10 -PollSeconds 10
```

## Traffic flow

Requests hit:

- `task ENI:80` or `ALB:80/443` -> `nginx` -> FastAPI app

Container-local dependencies:

- app -> `127.0.0.1:5432` PostgreSQL
- app -> `127.0.0.1:6333` Qdrant
- app -> `127.0.0.1:6379` Redis

Simple diagram:

```text
client
  |
  +--> ALB:80/443 (optional)
  |       |
  |       v
  +----> task ENI:80
           |
           v
         nginx
           |
           v
      FastAPI app:8000
         |        |        |
         |        |        +--> 127.0.0.1:6379   Redis
         |        |
         |        +---------> 127.0.0.1:6333   Qdrant
         |
         +------------------> 127.0.0.1:5432   PostgreSQL
```

## Operational notes

- This design preserves the all-in-one task shape, but stateful containers on Fargate remain disposable.
- Ollama is disabled in the ECS template because local models are not realistic in this deployment shape.
- The ECS task template seeds NIM as the initial generation and embedding default, but the catalog still includes OpenAI and Ollama profiles in code if you switch them later through the admin API.
- Chat activity and chat feedback are stored in the task-local PostgreSQL container, so replacing the task can clear admin monitoring history.
- Public chat response details are controlled by `CHAT_DEBUG_ENABLED`; leave it disabled unless the endpoint is restricted to trusted operators.
- If you want Fargate to be production-ready, the next step is: app + nginx on Fargate, PostgreSQL on RDS, Redis on ElastiCache.
- Chat persona wording lives in `backend/app/services/prompt_builder.py`, so any tone change requires rebuilding and pushing the `rag-backend` image, then registering a new task definition revision and updating the ECS service.

## Post-deploy verification

After a new task revision is live, verify:

1. `GET /health` returns `200`
2. `POST /chat` returns `200` with only `answer`, compact `citations`, and `session_id` when `CHAT_DEBUG_ENABLED=false`
3. `POST /chat/stream` still streams answer chunks and a compact final payload
4. `GET /admin/chat-activity` returns `200`
5. `GET /admin/chat-feedback` returns `200`
6. Swagger shows `/chat` public and debug response schemas, and `/chat/stream` `text/event-stream` examples

Related troubleshooting:

- `docs/troubleshooting-log.md`
