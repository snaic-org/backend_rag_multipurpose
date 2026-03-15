from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.security import require_admin_user
from app.models.schemas import (
    AuthenticatedUser,
    ResetResponse,
    UserCreateRequest,
    UserResponse,
    UserUpdateRequest,
)
from app.services.reset_service import ResetService

router = APIRouter()


def _build_reset_service(request: Request) -> ResetService:
    return ResetService(
        postgres_pool=request.app.state.postgres.pool,
        redis_manager=request.app.state.redis,
    )


def _build_auth_service(request: Request):
    return request.app.state.auth_service


@router.delete("/reset", response_model=ResetResponse)
async def reset_backend_state(
    request: Request,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> ResetResponse:
    service = _build_reset_service(request)
    return await service.reset_all()


@router.post("/users", response_model=UserResponse)
async def create_user(
    request: Request,
    payload: UserCreateRequest,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> UserResponse:
    try:
        return await _build_auth_service(request).create_user(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    request: Request,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> list[UserResponse]:
    return await _build_auth_service(request).list_users()


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    request: Request,
    user_id: UUID,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> UserResponse:
    try:
        return await _build_auth_service(request).get_user(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    request: Request,
    user_id: UUID,
    payload: UserUpdateRequest,
    current_user: AuthenticatedUser = Depends(require_admin_user),
) -> UserResponse:
    try:
        return await _build_auth_service(request).update_user(user_id, payload, current_user)
    except ValueError as exc:
        message = str(exc)
        status_code = status.HTTP_400_BAD_REQUEST
        if "not found" in message.lower():
            status_code = status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    request: Request,
    user_id: UUID,
    current_user: AuthenticatedUser = Depends(require_admin_user),
) -> None:
    try:
        await _build_auth_service(request).delete_user(user_id, current_user)
    except ValueError as exc:
        message = str(exc)
        status_code = status.HTTP_400_BAD_REQUEST
        if "not found" in message.lower():
            status_code = status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=status_code, detail=message) from exc
