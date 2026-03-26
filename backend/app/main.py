import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.qdrant import QdrantManager
from app.db.postgres import PostgresManager
from app.db.redis import RedisManager
from app.providers.registry import ProviderRegistry
from app.services.auth_service import AuthService
from app.services.model_selection_service import ModelSelectionService
from app.services.system_prompt_service import SystemPromptService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    postgres = PostgresManager(settings)
    redis = RedisManager(settings)
    qdrant = QdrantManager(settings)
    providers = ProviderRegistry.from_settings(settings)
    auth_service = AuthService(settings, postgres.pool)
    prompt_service = SystemPromptService(postgres.pool)
    model_selection_service = ModelSelectionService(settings, postgres.pool)

    app.state.settings = settings
    app.state.postgres = postgres
    app.state.redis = redis
    app.state.qdrant = qdrant
    app.state.providers = providers
    app.state.auth_service = auth_service
    app.state.prompt_service = prompt_service
    app.state.model_selection_service = model_selection_service

    await postgres.connect()
    await redis.connect()
    await prompt_service.ensure_default_system_prompt()
    await model_selection_service.ensure_default_model_selection()
    model_selection = await model_selection_service.get_model_selection()
    embedding_profile = settings.embedding_profiles[model_selection.embedding_profile]
    await _wait_for_qdrant(qdrant, embedding_profile.dimension)
    await auth_service.ensure_bootstrap_admin()

    logger.info("application_startup_complete")

    try:
        yield
    finally:
        await qdrant.close()
        await redis.close()
        await postgres.close()
        logger.info("application_shutdown_complete")


async def _wait_for_qdrant(qdrant: QdrantManager, dimension: int, retries: int = 30) -> None:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            await qdrant.ensure_collection(dimension)
            return
        except Exception as exc:  # pragma: no cover - startup retry path
            last_error = exc
            if attempt == retries:
                break
            await asyncio.sleep(min(2 * attempt, 10))

    if last_error is not None:
        raise RuntimeError(f"Qdrant is not ready: {last_error}") from last_error


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Backend RAG API built by Isfaque Tuhin. "
            "Portfolio: https://www.linkedin.com/in/iatuhin/ | "
            "GitHub: https://github.com/iahin | "
            "Contact: shioktech@gmail.com"
        ),
        lifespan=lifespan,
    )
    app.include_router(api_router)
    return app


app = create_app()
