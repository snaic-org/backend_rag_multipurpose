from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings


def configure_cors(app: FastAPI, settings: Settings) -> None:
    """Allow browser clients on CORS_ALLOWED_ORIGINS; no-op when none are configured."""
    if not settings.cors_allowed_origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Chat-Session-Id"],
        max_age=600,
    )
