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

import jwt
import httpx
from fastapi import FastAPI

import app.api.chat as chat_api
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.models.schemas import (
    AccessTokenResponse,
    AuthenticatedUser,
    ChatCitation,
    ChatRequest,
    ChatServiceResult,
    SystemPromptResponse,
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

class FakePromptService:
    def __init__(self, initial_prompt: str) -> None:
        self._state = SimpleNamespace(
            system_prompt=initial_prompt,
            updated_at=datetime.now(timezone.utc),
        )

    async def ensure_default_system_prompt(self) -> None:
        return None

    async def get_system_prompt(self) -> SystemPromptResponse:
        return SystemPromptResponse(
            system_prompt=self._state.system_prompt,
            updated_at=self._state.updated_at,
        )

    async def update_system_prompt(self, system_prompt: str) -> SystemPromptResponse:
        self._state.system_prompt = system_prompt
        self._state.updated_at = datetime.now(timezone.utc)
        return await self.get_system_prompt()


class FakeChatService:
    def __init__(self, prompt_service: FakePromptService) -> None:
        self._prompt_service = prompt_service

    async def prepare_chat(
        self,
        payload: ChatRequest,
        rate_limit_key: str,
        session_id: str | None = None,
    ) -> ChatServiceResult:
        prompt = await self._prompt_service.get_system_prompt()
        return ChatServiceResult(
            answer=prompt.system_prompt,
            citations=[
                ChatCitation(
                    document_id=UUID("22222222-2222-2222-2222-222222222222"),
                    chunk_id=UUID("33333333-3333-3333-3333-333333333333"),
                    title="System Prompt",
                    url=None,
                    source_type="text",
                    snippet=prompt.system_prompt[:200],
                    metadata={},
                )
            ],
            provider="ollama",
            model="llama3.2",
            embedding_profile="ollama_4096",
            embedding_provider="ollama",
            embedding_model="qwen3-embedding",
            used_fallback=False,
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


def build_app() -> FastAPI:
    settings = StubSettings()
    prompt_service = FakePromptService("Original prompt")
    app = FastAPI()
    app.include_router(auth_router, prefix="/auth")
    app.include_router(admin_router, prefix="/admin")
    app.include_router(chat_router, prefix="/chat")
    app.state.settings = settings
    app.state.auth_service = FakeAuthService(settings)
    app.state.prompt_service = prompt_service
    return app


def test_admin_can_update_system_prompt_and_chat_uses_it(monkeypatch) -> None:
    app = build_app()

    monkeypatch.setattr(
        chat_api,
        "_build_chat_service",
        lambda request: FakeChatService(request.app.state.prompt_service),
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

            update_response = await client.put(
                "/admin/system-prompt",
                headers=headers,
                json={"system_prompt": "Updated prompt from admin"},
            )
            assert update_response.status_code == 200, update_response.text
            assert update_response.json()["system_prompt"] == "Updated prompt from admin"

            chat_response = await client.post(
                "/chat",
                headers=headers,
                json={"message": "what is the prompt?"},
            )
            assert chat_response.status_code == 200, chat_response.text
            assert chat_response.json()["answer"] == "Updated prompt from admin"

    asyncio.run(run_flow())
