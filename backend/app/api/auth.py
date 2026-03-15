from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.security import require_authenticated_user
from app.models.schemas import (
    AccessTokenRequest,
    AccessTokenResponse,
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyResponse,
    AuthenticatedUser,
)

router = APIRouter()


@router.post("/token", response_model=AccessTokenResponse)
async def create_access_token(
    request: Request,
    payload: AccessTokenRequest,
) -> AccessTokenResponse:
    try:
        return await request.app.state.auth_service.issue_access_token(
            username=payload.username,
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc


@router.get("/me", response_model=AuthenticatedUser)
async def get_current_user(
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> AuthenticatedUser:
    return current_user


@router.post("/api-keys", response_model=ApiKeyCreateResponse)
async def create_api_key(
    request: Request,
    payload: ApiKeyCreateRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> ApiKeyCreateResponse:
    return await request.app.state.auth_service.create_api_key(
        current_user=current_user,
        name=payload.name,
    )


@router.get("/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(
    request: Request,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> list[ApiKeyResponse]:
    return await request.app.state.auth_service.list_api_keys_for_user(current_user.id)


@router.delete("/api-keys/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    request: Request,
    api_key_id: UUID,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> None:
    try:
        await request.app.state.auth_service.revoke_api_key(
            api_key_id=api_key_id,
            user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
