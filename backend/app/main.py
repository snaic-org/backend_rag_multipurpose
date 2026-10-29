import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.cors import configure_cors
from app.core.http_client import close_shared_http_clients
from app.core.logging import configure_logging, get_logger
from app.db.qdrant import QdrantManager
from app.db.postgres import PostgresManager
from app.db.redis import RedisManager
from app.providers.registry import ProviderRegistry
from app.services.auth_service import AuthService
from app.services.chat_activity_service import ChatActivityService
from app.services.chat_feedback_service import ChatFeedbackService
from app.services.chat_service import ChatService
from app.services.document_inspection_service import DocumentInspectionService
from app.services.ingest_service import IngestService
from app.services.model_health_service import ModelHealthService
from app.services.model_selection_service import ModelSelectionService
from app.services.reset_service import ResetService
from app.services.system_prompt_service import SystemPromptService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)
    for warning in settings.insecure_setting_warnings():
        logger.warning("insecure_setting: %s", warning)

    postgres = PostgresManager(settings)
    redis = RedisManager(settings)
    qdrant = QdrantManager(settings)
    providers = ProviderRegistry.from_settings(settings)

    auth_service = AuthService(settings, postgres.pool)
    chat_activity_service = ChatActivityService(postgres.pool)
    chat_feedback_service = ChatFeedbackService(postgres.pool)
    prompt_service = SystemPromptService(postgres.pool)
    model_selection_service = ModelSelectionService(settings, postgres.pool)
    model_health_service = ModelHealthService(settings, providers, model_selection_service)

    app.state.settings = settings
    app.state.postgres = postgres
    app.state.redis = redis
    app.state.qdrant = qdrant
    app.state.providers = providers
    app.state.auth_service = auth_service
    app.state.activity_service = chat_activity_service
    app.state.feedback_service = chat_feedback_service
    app.state.prompt_service = prompt_service
    app.state.model_selection_service = model_selection_service
    app.state.model_health_service = model_health_service

    await postgres.connect()
    await postgres.apply_schema()
    await redis.connect()
    await auth_service.ensure_bootstrap_admin()
    await prompt_service.ensure_default_system_prompt()
    await model_selection_service.ensure_default_model_selection()
    model_selection = await model_selection_service.get_model_selection()
    embedding_profile = settings.embedding_profiles[model_selection.embedding_profile]
    await _wait_for_qdrant(qdrant, embedding_profile.dimension)

    # Request services are stateless over the shared clients: build them once
    # instead of on every request.
    app.state.chat_service = ChatService(
        settings=settings,
        qdrant_manager=qdrant,
        redis_manager=redis,
        provider_registry=providers,
        system_prompt_service=prompt_service,
        model_selection_service=model_selection_service,
    )
    app.state.ingest_service = IngestService(
        settings=settings,
        redis_manager=redis,
        qdrant_manager=qdrant,
        postgres_pool=postgres.pool,
        model_selection_service=model_selection_service,
    )
    app.state.document_inspection_service = DocumentInspectionService(
        settings=settings,
        postgres_pool=postgres.pool,
        qdrant_manager=qdrant,
    )
    app.state.reset_service = ResetService(
        postgres_pool=postgres.pool,
        qdrant_manager=qdrant,
        redis_manager=redis,
    )

    logger.info("application_startup_complete")

    try:
        yield
    finally:
        await qdrant.close()
        await redis.close()
        await postgres.close()
        await close_shared_http_clients()
        logger.info("application_shutdown_complete")


async def _wait_for_qdrant(qdrant: QdrantManager, dimension: int, retries: int = 30) -> None:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            await qdrant.ensure_collection(dimension)
            return
        except Exception as exc:
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
        version=settings.app_version,
        description=settings.app_description,
        lifespan=lifespan,
    )
    configure_cors(app, settings)
    app.include_router(api_router)
    return app


app = create_app()
