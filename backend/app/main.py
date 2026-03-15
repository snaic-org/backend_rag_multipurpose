from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.postgres import PostgresManager
from app.db.redis import RedisManager
from app.providers.registry import ProviderRegistry
from app.services.auth_service import AuthService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    postgres = PostgresManager(settings)
    redis = RedisManager(settings)
    providers = ProviderRegistry.from_settings(settings)
    auth_service = AuthService(settings, postgres.pool)

    app.state.settings = settings
    app.state.postgres = postgres
    app.state.redis = redis
    app.state.providers = providers
    app.state.auth_service = auth_service

    await postgres.connect()
    await redis.connect()
    await auth_service.ensure_bootstrap_admin()

    logger.info("application_startup_complete")

    try:
        yield
    finally:
        await redis.close()
        await postgres.close()
        logger.info("application_shutdown_complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(api_router)
    return app


app = create_app()
