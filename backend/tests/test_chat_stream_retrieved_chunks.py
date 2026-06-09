from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import asyncio
import json
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
from starlette.requests import Request

import app.api.chat as chat_api
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.models.schemas import (
    AccessTokenResponse,
    AuthenticatedUser,
    ChatCitation,
    ChatRequest,
    ChatResponse,
    ChatServiceResult,
    ChatStreamState,
    RetrievedChunk,
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
    chat_max_response_chars = 5000
    chat_debug_enabled = True
    chat_thinking_enabled = False
    chat_show_thinking_block = False


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

class FakeChatService:
    def __init__(self) -> None:
        self._retrieved_chunks = [
            RetrievedChunk(
                chunk_id=UUID("33333333-3333-3333-3333-333333333331"),
                document_id=UUID("22222222-2222-2222-2222-222222222222"),
                title="SNAIC Overview",
                url=None,
                source_type="text",
                content="Title: SNAIC Overview\nSection: Collaboration Models with SNAIC\nStep 1: Pre-engagement questionnaire.",
                metadata={"section_title": "Collaboration Models with SNAIC"},
                similarity_score=0.91,
            )
        ]

    async def prepare_chat(
        self,
        payload: ChatRequest,
        rate_limit_key: str,
        session_id: str | None = None,
    ) -> ChatServiceResult:
        citation = ChatCitation(
            document_id=self._retrieved_chunks[0].document_id,
            chunk_id=self._retrieved_chunks[0].chunk_id,
            title=self._retrieved_chunks[0].title,
            url=None,
            source_type=self._retrieved_chunks[0].source_type,
            snippet=self._retrieved_chunks[0].content[:200],
            metadata=self._retrieved_chunks[0].metadata,
        )
        return ChatServiceResult(
            answer="final answer",
            citations=[citation],
            provider="ollama",
            model="llama3.2",
            embedding_profile="openai_small_1536",
            embedding_provider="openai",
            embedding_model="text-embedding-3-small",
            used_fallback=False,
            session_id=session_id,
            retrieved_chunks=self._retrieved_chunks,
        )

    async def start_stream(
        self,
        payload: ChatRequest,
        rate_limit_key: str,
        session_id: str | None = None,
    ) -> ChatStreamState:
        async def stream():
            yield "final "
            yield "answer"

        citation = ChatCitation(
            document_id=self._retrieved_chunks[0].document_id,
            chunk_id=self._retrieved_chunks[0].chunk_id,
            title=self._retrieved_chunks[0].title,
            url=None,
            source_type=self._retrieved_chunks[0].source_type,
            snippet=self._retrieved_chunks[0].content[:200],
            metadata=self._retrieved_chunks[0].metadata,
        )
        return ChatStreamState(
            provider="ollama",
            model="llama3.2",
            embedding_profile="openai_small_1536",
            embedding_provider="openai",
            embedding_model="text-embedding-3-small",
            citations=[citation],
            retrieved_chunks=self._retrieved_chunks,
            thinking=None,
            stream=stream(),
            used_fallback=False,
            fallback_text="",
            session_id=session_id,
            user_message="hello there",
        )

    async def finalize_stream(self, stream_state, answer: str) -> None:
        return None

    def finalize_answer(self, text: str) -> str:
        return text


def build_app() -> FastAPI:
    settings = StubSettings()
    app = FastAPI()
    app.include_router(auth_router, prefix="/auth")
    app.include_router(chat_router, prefix="/chat")
    app.state.settings = settings
    app.state.auth_service = FakeAuthService(settings)
    return app


def test_chat_stream_exposes_same_retrieved_chunks_as_chat(monkeypatch) -> None:
    app = build_app()

    monkeypatch.setattr(
        chat_api,
        "_build_chat_service",
        lambda request: FakeChatService(),
    )

    async def run_flow() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            token_response = await client.post(
                "/auth/token",
                json={
                    "username": "admin",
                    "password": "change-me-immediately",
                },
            )
            assert token_response.status_code == 200, token_response.text
            token = token_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            chat_response = await client.post(
                "/chat",
                headers=headers,
                json={"message": "hello there"},
            )
            assert chat_response.status_code == 200, chat_response.text
            chat_payload = chat_response.json()
            assert len(chat_payload["retrieved_chunks"]) == 1
            assert chat_payload["session_id"]

            async with client.stream(
                "POST",
                "/chat/stream",
                headers=headers,
                json={"message": "hello there"},
            ) as stream_response:
                assert stream_response.status_code == 200
                body = await stream_response.aread()

            stream_text = body.decode("utf-8")
            done_lines = [
                line.removeprefix("data: ")
                for line in stream_text.splitlines()
                if line.startswith("data: ") and "\"retrieved_chunks\"" in line
            ]
            assert done_lines, stream_text
            parsed_events = [json.loads(line) for line in done_lines]
            assert parsed_events[-1]["retrieved_chunks"] == chat_payload["retrieved_chunks"]
            assert parsed_events[0]["session_id"] == chat_payload["session_id"]
            assert parsed_events[-1]["session_id"] == chat_payload["session_id"]

    asyncio.run(run_flow())


def test_chat_stream_public_output_is_minimal(monkeypatch) -> None:
    app = build_app()
    app.state.settings.chat_debug_enabled = False

    monkeypatch.setattr(
        chat_api,
        "_build_chat_service",
        lambda request: FakeChatService(),
    )

    async def run_flow() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            token_response = await client.post(
                "/auth/token",
                json={
                    "username": "admin",
                    "password": "change-me-immediately",
                },
            )
            assert token_response.status_code == 200, token_response.text
            token = token_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            async with client.stream(
                "POST",
                "/chat/stream",
                headers=headers,
                json={"message": "hello there"},
            ) as stream_response:
                assert stream_response.status_code == 200
                response_session_id = stream_response.headers["x-chat-session-id"]
                body = await stream_response.aread()

            stream_text = body.decode("utf-8")
            assert "event: metadata" not in stream_text

            done_lines = [
                line.removeprefix("data: ")
                for line in stream_text.splitlines()
                if line.startswith("data: ")
            ]
            done_payload = json.loads(done_lines[-1])
            assert done_payload["session_id"]
            assert done_payload["session_id"] == response_session_id
            assert done_payload == {
                "answer": "final answer",
                "citations": [
                    {
                        "document_id": "22222222-2222-2222-2222-222222222222",
                        "chunk_id": "33333333-3333-3333-3333-333333333331",
                    }
                ],
                "session_id": done_payload["session_id"],
            }

    asyncio.run(run_flow())


def test_chat_openapi_documents_public_and_debug_outputs() -> None:
    app = build_app()
    schema = app.openapi()

    chat_response = schema["paths"]["/chat"]["post"]["responses"]["200"]
    response_schema = chat_response["content"]["application/json"]["schema"]
    assert any(
        option.get("$ref", "").endswith("/ChatPublicResponse")
        for option in response_schema["anyOf"]
    )
    assert any(
        option.get("$ref", "").endswith("/ChatResponse")
        for option in response_schema["anyOf"]
    )

    stream_response = schema["paths"]["/chat/stream"]["post"]["responses"]["200"]
    assert "text/event-stream" in stream_response["content"]
    examples = stream_response["content"]["text/event-stream"]["examples"]
    assert {"default", "debug"} <= set(examples)


def test_chat_session_resolver_uses_stable_server_side_fallback() -> None:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/chat/stream",
        "headers": [
            (b"user-agent", b"pytest-browser"),
            (b"origin", b"http://localhost:3000"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
    }

    first_session_id = chat_api._resolve_chat_session_id(Request(scope))
    second_session_id = chat_api._resolve_chat_session_id(Request(scope))

    assert first_session_id.startswith("public-")
    assert first_session_id == second_session_id
