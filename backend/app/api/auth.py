from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.logging import get_logger
from app.core.security import require_authenticated_user
from app.models.schemas import (
    AccessTokenRequest,
    AccessTokenResponse,
    AuthenticatedUser,
)

router = APIRouter()
logger = get_logger(__name__)

# Failed sign-ins allowed per client address before /auth/token refuses for a
# while: slows password guessing against the admin dashboard's login form.
LOGIN_FAILURE_LIMIT = 10
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60


@router.post("/token", response_model=AccessTokenResponse)
async def create_access_token(
    request: Request,
    payload: AccessTokenRequest,
) -> AccessTokenResponse:
    failures_key = f"login_failures:{request.client.host if request.client else 'unknown'}"
    redis = _redis_client(request)
    if await _failure_count(redis, failures_key) >= LOGIN_FAILURE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed sign-in attempts. Try again in 15 minutes.",
        )
    try:
        return await request.app.state.auth_service.issue_access_token(
            username=payload.username,
            password=payload.password,
        )
    except ValueError as exc:
        await _record_failure(redis, failures_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc


@router.get("/me", response_model=AuthenticatedUser)
async def get_current_user(
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> AuthenticatedUser:
    return current_user


def _redis_client(request: Request):
    manager = getattr(request.app.state, "redis", None)
    return getattr(manager, "client", None)


# Throttling must never lock everyone out: if Redis is unavailable, sign-in
# works without it.
async def _failure_count(redis, key: str) -> int:
    if redis is None:
        return 0
    try:
        return int(await redis.get(key) or 0)
    except Exception as exc:
        logger.warning("login_throttle_unavailable error=%r", exc)
        return 0


async def _record_failure(redis, key: str) -> None:
    if redis is None:
        return
    try:
        if await redis.incr(key) == 1:
            await redis.expire(key, LOGIN_FAILURE_WINDOW_SECONDS)
    except Exception as exc:
        logger.warning("login_throttle_unavailable error=%r", exc)
