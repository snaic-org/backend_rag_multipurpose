from fastapi import Depends, Header, HTTPException, Request, status

from app.models.schemas import AuthenticatedUser


def _resolve_request_scheme(request: Request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto:
        return forwarded_proto.split(",")[0].strip().lower()
    return request.url.scheme.lower()


def _enforce_https_if_required(request: Request) -> None:
    settings = request.app.state.settings
    if settings.auth_require_https and _resolve_request_scheme(request) != "https":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="HTTPS is required for authenticated API calls",
        )


async def require_authenticated_user(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> AuthenticatedUser:
    settings = request.app.state.settings
    if not settings.auth_enabled:
        return AuthenticatedUser(
            id="00000000-0000-0000-0000-000000000000",
            username="anonymous",
            is_admin=True,
            auth_type="bearer",
        )

    _enforce_https_if_required(request)
    auth_service = request.app.state.auth_service

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", maxsplit=1)[1].strip()
        try:
            return await auth_service.authenticate_bearer_token(token)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

    if x_api_key:
        try:
            return await auth_service.authenticate_api_key(x_api_key.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
            ) from exc

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing bearer token or X-API-Key header",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_admin_user(
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> AuthenticatedUser:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges are required",
        )
    return current_user
