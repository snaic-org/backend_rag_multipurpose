from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.security import require_authenticated_user
from app.models.schemas import (
    AccessTokenRequest,
    AccessTokenResponse,
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
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
