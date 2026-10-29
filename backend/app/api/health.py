import asyncio

from fastapi import APIRouter, Request, Response, status

from app.core.logging import get_logger
from app.models.schemas import HealthCheckResponse, ModelHealthResponse

router = APIRouter()
logger = get_logger(__name__)
_NO_SELECTION_SERVICE = object()


@router.get(
    "/health",
    response_model=HealthCheckResponse,
    description=(
        "Cheap liveness check used by container health checks; always returns 200. "
        "`status` is `degraded` when a datastore or a provider the active model selection "
        "uses is unhealthy. Providers that are not in use are reported with `in_use: false` "
        "and do not affect `status`. Provider checks only confirm configuration; use "
        "`/health/models` to confirm the models actually respond."
    ),
)
async def health_check(request: Request) -> HealthCheckResponse:
    # Run concurrently: external uptime checkers time out after a couple of seconds.
    postgres_status, redis_status, qdrant_status, provider_status, selection = await asyncio.gather(
        request.app.state.postgres.healthcheck(),
        request.app.state.redis.healthcheck(),
        request.app.state.qdrant.healthcheck(),
        request.app.state.providers.healthcheck_all(),
        _read_model_selection(request),
    )

    active_providers = _active_provider_names(request, selection, set(provider_status))
    for name, provider_health in provider_status.items():
        provider_health.in_use = active_providers is None or name in active_providers

    overall_ok = (
        active_providers is not None
        and postgres_status.ok
        and redis_status.ok
        and qdrant_status.ok
        and all(provider_health.ok for provider_health in provider_status.values() if provider_health.in_use)
    )

    return HealthCheckResponse(
        status="ok" if overall_ok else "degraded",
        app=request.app.state.settings.app_name,
        postgres=postgres_status,
        redis=redis_status,
        qdrant=qdrant_status,
        providers=provider_status,
    )


@router.get(
    "/health/models",
    response_model=ModelHealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ModelHealthResponse}},
    description=(
        "Readiness check that calls the active generation, embedding, and (when enabled) "
        "rerank models with a tiny request. Returns 503 when any of them fails, e.g. after "
        "a hosted model is retired. Results are cached for HEALTH_MODEL_PROBE_TTL_SECONDS. "
        "Use this for uptime monitoring, not for container health checks."
    ),
)
async def model_health_check(request: Request, response: Response) -> ModelHealthResponse:
    result = await request.app.state.model_health_service.check()
    if result.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result


async def _read_model_selection(request: Request):
    model_selection_service = getattr(request.app.state, "model_selection_service", None)
    if model_selection_service is None:
        return _NO_SELECTION_SERVICE
    try:
        return await model_selection_service.get_model_selection()
    except Exception as exc:
        logger.warning("health_model_selection_failed error=%r", exc)
        return None


def _active_provider_names(request: Request, selection, all_providers: set[str]) -> set[str] | None:
    """Providers the current model selection depends on; None if it cannot be read."""
    if selection is _NO_SELECTION_SERVICE:
        return all_providers
    if selection is None:
        return None

    active = {selection.generation_provider, selection.embedding_provider}
    if request.app.state.settings.rerank_enabled:
        active.add("nim")
    return active
