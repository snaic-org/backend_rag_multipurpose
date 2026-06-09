from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
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

import app.api.chat as chat_api
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.models.schemas import (
    AccessTokenResponse,
    AuthenticatedUser,
    ChatActivityOverview,
    ChatActivityQueryResponse,
    ChatActivityResponse,
    ChatCitation,
    ChatRequest,
    ChatServiceResult,
)


class StubSettings:
    app_name = "backend-rag-multipurpose"
    auth_enabled = True
    auth_require_https = False
    auth_jwt_secret = "test-secret"
    auth_jwt_algorithm = "HS256"
    auth_access_token_ttl_seconds = 3600
    auth_bootstrap_admin_username = "admin"
    auth_bootstrap_admin_password = "change-me-immediately"
    chat_top_k = 5
    chat_debug_enabled = False


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

class FakeActivityService:
    def __init__(self, *, should_fail: bool = False) -> None:
        self._records: list[ChatActivityResponse] = []
        self._next_id = 1
        self._should_fail = should_fail

    async def record(self, payload) -> ChatActivityResponse:
        if self._should_fail:
            raise RuntimeError("activity storage unavailable")
        record = ChatActivityResponse(
            id=self._next_id,
            created_at=datetime.now(timezone.utc),
            **payload.model_dump(),
        )
        self._next_id += 1
        self._records.insert(0, record)
        return record

    async def search(
        self,
        *,
        limit: int = 100,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        keyword: str | None = None,
    ) -> ChatActivityQueryResponse:
        items = self._records
        if start_at is not None:
            items = [record for record in items if record.created_at >= start_at]
        if end_at is not None:
            items = [record for record in items if record.created_at <= end_at]
        if keyword:
            lowered = keyword.lower()
            items = [
                record
                for record in items
                if lowered in record.request_message.lower()
                or (record.response_answer and lowered in record.response_answer.lower())
                or lowered in record.username.lower()
            ]
        items = items[:limit]
        return ChatActivityQueryResponse(
            overview=ChatActivityOverview(
                total_activities=len(items),
                completed_activities=sum(1 for item in items if item.status == "completed"),
                failed_activities=sum(1 for item in items if item.status == "failed"),
                fallback_activities=sum(1 for item in items if item.used_fallback),
                unique_users=len({item.user_id for item in items}),
                unique_client_ips=len({item.client_ip for item in items if item.client_ip}),
                first_activity_at=min((item.created_at for item in items), default=None),
                last_activity_at=max((item.created_at for item in items), default=None),
                top_providers=[],
                top_models=[],
            ),
            activities=items,
        )


class FakeChatService:
    def __init__(self, *, should_fail: bool = False) -> None:
        self._should_fail = should_fail

    async def prepare_chat(
        self,
        payload: ChatRequest,
        rate_limit_key: str,
        session_id: str | None = None,
    ) -> ChatServiceResult:
        if self._should_fail:
            raise ValueError("provider unreachable")

        return ChatServiceResult(
            answer="Recorded answer",
            citations=[
                ChatCitation(
                    document_id=UUID("22222222-2222-2222-2222-222222222222"),
                    chunk_id=UUID("33333333-3333-3333-3333-333333333333"),
                    title="KB Doc",
                    url=None,
                    source_type="text",
                    snippet="snippet",
                    metadata={},
                )
            ],
            provider="ollama",
            model="llama3.2",
            embedding_profile="ollama_4096",
            embedding_provider="ollama",
            embedding_model="qwen3-embedding",
            used_fallback=False,
            session_id=session_id,
            retrieved_chunks=[],
        )

    async def start_stream(
        self,
        payload: ChatRequest,
        rate_limit_key: str,
        session_id: str | None = None,
    ):
        raise NotImplementedError

    async def finalize_stream(self, stream_state, answer: str) -> None:
        return None


def build_app(activity_service: FakeActivityService) -> FastAPI:
    settings = StubSettings()
    app = FastAPI()
    app.include_router(auth_router, prefix="/auth")
    app.include_router(admin_router, prefix="/admin")
    app.include_router(chat_router, prefix="/chat")
    app.state.settings = settings
    app.state.auth_service = FakeAuthService(settings)
    app.state.activity_service = activity_service
    return app


def test_chat_activity_is_recorded_and_visible_to_admin(monkeypatch) -> None:
    activity_service = FakeActivityService()
    app = build_app(activity_service)
    monkeypatch.setattr(chat_api, "_build_chat_service", lambda request: FakeChatService())

    async def run_flow() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            token_response = await client.post(
                "/auth/token",
                json={"username": "admin", "password": "change-me-immediately"},
            )
            token = token_response.json()["access_token"]
            headers = {
                "Authorization": f"Bearer {token}",
                "User-Agent": "pytest-agent",
                "X-Forwarded-For": "203.0.113.9, 10.0.0.5",
            }

            chat_response = await client.post(
                "/chat",
                headers=headers,
                json={"message": "Is this monitored?"},
            )
            assert chat_response.status_code == 200, chat_response.text

            activity_response = await client.get("/admin/chat-activity", headers=headers)
            assert activity_response.status_code == 200, activity_response.text
            payload = activity_response.json()
            assert payload["overview"]["total_activities"] == 1
            assert payload["overview"]["completed_activities"] == 1
            assert payload["overview"]["unique_client_ips"] == 1
            assert len(payload["activities"]) == 1
            assert payload["activities"][0]["client_ip"] == "203.0.113.9"
            assert payload["activities"][0]["forwarded_for"] == ["203.0.113.9", "10.0.0.5"]
            assert payload["activities"][0]["user_agent"] == "pytest-agent"
            assert payload["activities"][0]["request_message"] == "Is this monitored?"
            assert payload["activities"][0]["response_answer"] == "Recorded answer"
            assert payload["activities"][0]["session_id"] == chat_response.json()["session_id"]
            assert payload["activities"][0]["citations_count"] == 1
            assert payload["activities"][0]["status"] == "completed"
            assert payload["activities"][0]["metadata"] == {"debug": False, "top_k": 5}

    asyncio.run(run_flow())


def test_failed_chat_activity_is_recorded(monkeypatch) -> None:
    activity_service = FakeActivityService()
    app = build_app(activity_service)
    monkeypatch.setattr(chat_api, "_build_chat_service", lambda request: FakeChatService(should_fail=True))

    async def run_flow() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            token_response = await client.post(
                "/auth/token",
                json={"username": "admin", "password": "change-me-immediately"},
            )
            token = token_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            chat_response = await client.post(
                "/chat",
                headers=headers,
                json={"message": "Trigger failure"},
            )
            assert chat_response.status_code == 503, chat_response.text

            activity_response = await client.get("/admin/chat-activity", headers=headers)
            payload = activity_response.json()
            assert payload["overview"]["total_activities"] == 1
            assert payload["overview"]["failed_activities"] == 1
            assert len(payload["activities"]) == 1
            assert payload["activities"][0]["status"] == "failed"
            assert payload["activities"][0]["error_message"] == "provider unreachable"
            assert payload["activities"][0]["response_answer"] is None

    asyncio.run(run_flow())


def test_chat_still_succeeds_when_activity_recording_fails(monkeypatch) -> None:
    activity_service = FakeActivityService(should_fail=True)
    app = build_app(activity_service)
    monkeypatch.setattr(chat_api, "_build_chat_service", lambda request: FakeChatService())

    async def run_flow() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            token_response = await client.post(
                "/auth/token",
                json={"username": "admin", "password": "change-me-immediately"},
            )
            token = token_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            chat_response = await client.post(
                "/chat",
                headers=headers,
                json={"message": "Do not fail chat when logging fails"},
            )
            assert chat_response.status_code == 200, chat_response.text
            assert chat_response.json()["answer"] == "Recorded answer"

    asyncio.run(run_flow())


def test_chat_activity_supports_keyword_and_date_filters(monkeypatch) -> None:
    activity_service = FakeActivityService()
    now = datetime.now(timezone.utc)
    activity_service._records = [
        ChatActivityResponse(
            id=2,
            user_id=UUID("11111111-1111-1111-1111-111111111111"),
            username="admin",
            auth_type="bearer",
            request_path="/chat",
            client_ip="203.0.113.9",
            forwarded_for=["203.0.113.9"],
            user_agent="pytest-agent",
            session_id="recent-session",
            request_message="safety escalation request",
            response_answer="recent answer",
            provider="ollama",
            model="llama3.2",
            embedding_profile="ollama_4096",
            embedding_provider="ollama",
            embedding_model="qwen3-embedding",
            used_fallback=False,
            citations_count=1,
            retrieved_chunks_count=0,
            status="completed",
            error_message=None,
            metadata={},
            created_at=now,
        ),
        ChatActivityResponse(
            id=1,
            user_id=UUID("11111111-1111-1111-1111-111111111111"),
            username="admin",
            auth_type="bearer",
            request_path="/chat",
            client_ip="203.0.113.10",
            forwarded_for=["203.0.113.10"],
            user_agent="pytest-agent",
            session_id="old-session",
            request_message="general question",
            response_answer="old answer",
            provider="ollama",
            model="llama3.2",
            embedding_profile="ollama_4096",
            embedding_provider="ollama",
            embedding_model="qwen3-embedding",
            used_fallback=False,
            citations_count=1,
            retrieved_chunks_count=0,
            status="completed",
            error_message=None,
            metadata={},
            created_at=now - timedelta(days=3),
        ),
    ]
    app = build_app(activity_service)
    monkeypatch.setattr(chat_api, "_build_chat_service", lambda request: FakeChatService())

    async def run_flow() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            token_response = await client.post(
                "/auth/token",
                json={"username": "admin", "password": "change-me-immediately"},
            )
            token = token_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            start_at = (now - timedelta(days=1)).isoformat()

            activity_response = await client.get(
                "/admin/chat-activity",
                headers=headers,
                params={"start_at": start_at, "keyword": "safety"},
            )
            assert activity_response.status_code == 200, activity_response.text
            payload = activity_response.json()
            assert payload["overview"]["total_activities"] == 1
            assert payload["activities"][0]["session_id"] == "recent-session"
            assert payload["activities"][0]["request_message"] == "safety escalation request"

    asyncio.run(run_flow())


def test_chat_activity_accepts_dd_mm_yyyy_filters(monkeypatch) -> None:
    activity_service = FakeActivityService()
    activity_service._records = [
        ChatActivityResponse(
            id=2,
            user_id=UUID("11111111-1111-1111-1111-111111111111"),
            username="admin",
            auth_type="bearer",
            request_path="/chat",
            client_ip="203.0.113.9",
            forwarded_for=["203.0.113.9"],
            user_agent="pytest-agent",
            session_id="match-session",
            request_message="question on 24 march",
            response_answer="answer",
            provider="ollama",
            model="llama3.2",
            embedding_profile="ollama_4096",
            embedding_provider="ollama",
            embedding_model="qwen3-embedding",
            used_fallback=False,
            citations_count=1,
            retrieved_chunks_count=0,
            status="completed",
            error_message=None,
            metadata={},
            created_at=datetime(2025, 3, 24, 12, 0, tzinfo=timezone.utc),
        ),
        ChatActivityResponse(
            id=1,
            user_id=UUID("11111111-1111-1111-1111-111111111111"),
            username="admin",
            auth_type="bearer",
            request_path="/chat",
            client_ip="203.0.113.10",
            forwarded_for=["203.0.113.10"],
            user_agent="pytest-agent",
            session_id="outside-session",
            request_message="question on 25 march",
            response_answer="answer",
            provider="ollama",
            model="llama3.2",
            embedding_profile="ollama_4096",
            embedding_provider="ollama",
            embedding_model="qwen3-embedding",
            used_fallback=False,
            citations_count=1,
            retrieved_chunks_count=0,
            status="completed",
            error_message=None,
            metadata={},
            created_at=datetime(2025, 3, 25, 12, 0, tzinfo=timezone.utc),
        ),
    ]
    app = build_app(activity_service)
    monkeypatch.setattr(chat_api, "_build_chat_service", lambda request: FakeChatService())

    async def run_flow() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            token_response = await client.post(
                "/auth/token",
                json={"username": "admin", "password": "change-me-immediately"},
            )
            token = token_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            activity_response = await client.get(
                "/admin/chat-activity",
                headers=headers,
                params={"start_at": "24/03/2025", "end_at": "24/03/2025"},
            )
            assert activity_response.status_code == 200, activity_response.text
            payload = activity_response.json()
            assert payload["overview"]["total_activities"] == 1
            assert payload["activities"][0]["session_id"] == "match-session"

    asyncio.run(run_flow())
