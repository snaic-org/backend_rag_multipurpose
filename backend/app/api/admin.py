from fastapi import APIRouter, Depends, Request

from app.core.security import require_admin_user
from app.models.schemas import AuthenticatedUser
from app.models.schemas import ResetResponse
from app.services.reset_service import ResetService

router = APIRouter()


def _build_reset_service(request: Request) -> ResetService:
    return ResetService(
        postgres_pool=request.app.state.postgres.pool,
        redis_manager=request.app.state.redis,
    )


@router.delete("/reset", response_model=ResetResponse)
async def reset_backend_state(
    request: Request,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> ResetResponse:
    service = _build_reset_service(request)
    return await service.reset_all()
