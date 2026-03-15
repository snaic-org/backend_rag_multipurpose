from fastapi import APIRouter, Depends

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.ingest import router as ingest_router
from app.core.security import require_authenticated_user

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(
    ingest_router,
    prefix="/ingest",
    tags=["ingest"],
    dependencies=[Depends(require_authenticated_user)],
)
api_router.include_router(
    chat_router,
    prefix="/chat",
    tags=["chat"],
    dependencies=[Depends(require_authenticated_user)],
)
api_router.include_router(admin_router, prefix="/admin", tags=["admin"])
