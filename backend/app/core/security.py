from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.models.schemas import AuthenticatedUser

bearer_scheme = HTTPBearer(auto_error=False)
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


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
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    x_api_key: str | None = Security(api_key_scheme),
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

    if bearer_credentials is not None:
        if bearer_credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authorization header must use the Bearer scheme",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = _normalize_secret_value(bearer_credentials.credentials)
        if token is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bearer token is missing or malformed",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            return await auth_service.authenticate_bearer_token(token)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

    api_key = _normalize_secret_value(x_api_key)
    if api_key is not None:
        try:
            return await auth_service.authenticate_api_key(api_key)
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


def _normalize_secret_value(raw: str | None) -> str | None:
    if raw is None:
        return None

    normalized = raw.strip()
    if not normalized:
        return None

    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {'"', "'"}:
        normalized = normalized[1:-1].strip()

    return normalized or None
