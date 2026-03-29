from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncio
import sys
import types

redis_module = types.ModuleType("redis")
redis_asyncio_module = types.ModuleType("redis.asyncio")
redis_asyncio_module.Redis = object
redis_module.asyncio = redis_asyncio_module
sys.modules.setdefault("redis", redis_module)
sys.modules.setdefault("redis.asyncio", redis_asyncio_module)

psycopg_pool_module = types.ModuleType("psycopg_pool")
psycopg_pool_module.AsyncConnectionPool = object
sys.modules.setdefault("psycopg_pool", psycopg_pool_module)

psycopg_module = types.ModuleType("psycopg")
psycopg_rows_module = types.ModuleType("psycopg.rows")
psycopg_rows_module.dict_row = object
psycopg_types_module = types.ModuleType("psycopg.types")
psycopg_types_json_module = types.ModuleType("psycopg.types.json")
psycopg_types_json_module.Jsonb = object
psycopg_module.rows = psycopg_rows_module
psycopg_module.types = psycopg_types_module
sys.modules.setdefault("psycopg", psycopg_module)
sys.modules.setdefault("psycopg.rows", psycopg_rows_module)
sys.modules.setdefault("psycopg.types", psycopg_types_module)
sys.modules.setdefault("psycopg.types.json", psycopg_types_json_module)

import httpx
import jwt
from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.models.schemas import AccessTokenResponse, AuthenticatedUser, ChatFeedbackResponse


class StubSettings:
    app_name = "backend-rag-multipurpose"
    auth_enabled = True
    auth_require_https = False
    auth_jwt_secret = "test-secret"
    auth_jwt_algorithm = "HS256"
    auth_access_token_ttl_seconds = 3600
    auth_bootstrap_admin_username = "admin"
    auth_bootstrap_admin_password = "change-me-immediately"


class FakeAuthService:
    def __init__(self, settings: StubSettings) -> None:
        self._settings = settings
        self._user = AuthenticatedUser(
            id=UUID("11111111-1111-1111-1111-111111111111"),
            username=settings.auth_bootstrap_admin_username,
            is_admin=True,
            auth_type="bearer",
        )

    async def issue_access_token(self, username: str, password: str) -> AccessTokenResponse:
        if (
            username != self._settings.auth_bootstrap_admin_username
            or password != self._settings.auth_bootstrap_admin_password
        ):
            raise ValueError("Invalid username or password")

        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=self._settings.auth_access_token_ttl_seconds
        )
        token = jwt.encode(
            {
                "sub": str(self._user.id),
                "username": self._user.username,
                "is_admin": self._user.is_admin,
                "exp": expires_at,
            },
            self._settings.auth_jwt_secret,
            algorithm=self._settings.auth_jwt_algorithm,
        )
        return AccessTokenResponse(
            access_token=token,
            expires_in_seconds=self._settings.auth_access_token_ttl_seconds,
            user=self._user,
        )

    async def authenticate_bearer_token(self, token: str) -> AuthenticatedUser:
        payload = jwt.decode(
            token,
            self._settings.auth_jwt_secret,
            algorithms=[self._settings.auth_jwt_algorithm],
        )
        if payload.get("sub") != str(self._user.id):
            raise ValueError("Invalid bearer token payload")
        return self._user

    async def authenticate_api_key(self, raw_api_key: str) -> AuthenticatedUser:
        raise ValueError("API key auth is not used in this test")


class FakeFeedbackService:
    def __init__(self) -> None:
        self._next_id = 1
        self._items: list[ChatFeedbackResponse] = []

    async def submit_feedback(self, payload, current_user: AuthenticatedUser) -> ChatFeedbackResponse:
        response = ChatFeedbackResponse(
            id=self._next_id,
            session_id=payload.session_id,
            rating=payload.rating,
            full_chat_text="User: hello\n\nAssistant: hi there",
            comments=payload.comments,
            date=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        self._next_id += 1
        self._items.insert(0, response)
        return response

    async def list_feedback(
        self,
        *,
        limit: int = 100,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[ChatFeedbackResponse]:
        items = self._items
        if start_at is not None:
            items = [item for item in items if item.created_at >= start_at]
        if end_at is not None:
            items = [item for item in items if item.created_at <= end_at]
        return items[:limit]


def build_app() -> FastAPI:
    settings = StubSettings()
    app = FastAPI()
    app.include_router(auth_router, prefix="/auth")
    app.include_router(admin_router, prefix="/admin")
    app.include_router(chat_router, prefix="/chat")
    app.state.settings = settings
    app.state.auth_service = FakeAuthService(settings)
    app.state.feedback_service = FakeFeedbackService()
    return app


def test_submit_chat_feedback() -> None:
    app = build_app()

    async def run_flow() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            token_response = await client.post(
                "/auth/token",
                json={"username": "admin", "password": "change-me-immediately"},
            )
            token = token_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            response = await client.post(
                "/chat/feedback",
                headers=headers,
                json={"session_id": "session-123", "rating": 5, "comments": "Very helpful"},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["session_id"] == "session-123"
            assert payload["rating"] == 5
            assert payload["comments"] == "Very helpful"
            assert "User: hello" in payload["full_chat_text"]
            assert payload["date"] is not None

    asyncio.run(run_flow())


def test_admin_can_list_chat_feedback_with_date_filter() -> None:
    app = build_app()
    feedback_service = app.state.feedback_service
    feedback_service._items = [
        ChatFeedbackResponse(
            id=2,
            session_id="session-new",
            rating=5,
            full_chat_text="User: new\n\nAssistant: recent",
            comments="Great",
            date=datetime(2025, 3, 24, 10, 0, tzinfo=timezone.utc),
            created_at=datetime(2025, 3, 24, 10, 0, tzinfo=timezone.utc),
        ),
        ChatFeedbackResponse(
            id=1,
            session_id="session-old",
            rating=3,
            full_chat_text="User: old\n\nAssistant: older",
            comments="Okay",
            date=datetime(2025, 3, 20, 10, 0, tzinfo=timezone.utc),
            created_at=datetime(2025, 3, 20, 10, 0, tzinfo=timezone.utc),
        ),
    ]

    async def run_flow() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            token_response = await client.post(
                "/auth/token",
                json={"username": "admin", "password": "change-me-immediately"},
            )
            token = token_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            response = await client.get(
                "/admin/chat-feedback",
                headers=headers,
                params={"start_at": "24/03/2025", "end_at": "24/03/2025"},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert len(payload) == 1
            assert payload[0]["session_id"] == "session-new"
            assert payload[0]["rating"] == 5
            assert "Assistant: recent" in payload[0]["full_chat_text"]

    asyncio.run(run_flow())


def test_submit_chat_feedback_validates_rating_range() -> None:
    app = build_app()

    async def run_flow() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            token_response = await client.post(
                "/auth/token",
                json={"username": "admin", "password": "change-me-immediately"},
            )
            token = token_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            response = await client.post(
                "/chat/feedback",
                headers=headers,
                json={"session_id": "session-123", "rating": 6},
            )
            assert response.status_code == 422, response.text

    asyncio.run(run_flow())
