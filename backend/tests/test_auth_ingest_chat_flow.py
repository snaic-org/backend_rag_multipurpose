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

docx_module = types.ModuleType("docx")
docx_module.Document = object
sys.modules.setdefault("docx", docx_module)

openpyxl_module = types.ModuleType("openpyxl")
openpyxl_module.load_workbook = lambda *args, **kwargs: None
sys.modules.setdefault("openpyxl", openpyxl_module)

multipart_module = types.ModuleType("multipart")
multipart_module.__version__ = "0.0.0"
multipart_submodule = types.ModuleType("multipart.multipart")
multipart_submodule.parse_options_header = lambda *args, **kwargs: (b"", {})
multipart_module.multipart = multipart_submodule
sys.modules.setdefault("multipart", multipart_module)
sys.modules.setdefault("multipart.multipart", multipart_submodule)

import jwt
import httpx
from fastapi import FastAPI

import app.api.chat as chat_api
import app.api.ingest as ingest_api
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.ingest import router as ingest_router
from app.core.config import EmbeddingProfileSpec
from app.services.assistant_copy import SAFE_FALLBACK_TEXT
from app.models.schemas import (
    AccessTokenResponse,
    AuthenticatedUser,
    ChatCitation,
    ChatRequest,
    ChatServiceResult,
    EmbeddingSelection,
    IngestFileResult,
    IngestTextRequest,
    IngestTextResponse,
)


INGESTED_CONTENT = (
    "The SIT Centre for AI offers comprehensive end-to-end services with co-supervision by experts from both SIT and NVIDIA It serves as a gateway for increased AI adoption across industries, better development of students’ competency in AI and a boost in the AI talent pipeline"
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
    default_llm_provider = "ollama"
    default_llm_model = "llama3.2"
    default_embedding_profile = "ollama_4096"
    embedding_profiles = {
        "ollama_4096": EmbeddingProfileSpec(
            provider="ollama",
            model="qwen3-embedding",
            dimension=4096,
        ),
        "ollama_1536": EmbeddingProfileSpec(
            provider="ollama",
            model="rjmalagon/gte-qwen2-1.5b-instruct-embed-f16",
            dimension=1536,
        ),
        "openai_small_1536": EmbeddingProfileSpec(
            provider="openai",
            model="text-embedding-3-small",
            dimension=1536,
        ),
    }

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

class FakeIngestService:
    def __init__(self, settings: StubSettings, corpus: list[dict[str, str]]) -> None:
        self._settings = settings
        self._corpus = corpus

    async def ingest_text_items(self, payload: IngestTextRequest, current_user) -> IngestTextResponse:
        selection = self._resolve_selection(None, None, None)

        results: list[IngestFileResult] = []
        for item in payload.items:
            self._corpus.append(
                {
                    "title": item.title,
                    "content": item.content,
                    "embedding_profile": selection.profile_name,
                    "embedding_provider": selection.provider,
                    "embedding_model": selection.model,
                }
            )
            results.append(
                IngestFileResult(
                    filename=item.title,
                    detected_type="text",
                    success=True,
                    chunks_created=1,
                    document_id=UUID("22222222-2222-2222-2222-222222222222"),
                )
            )

        return IngestTextResponse(
            documents_inserted=len(payload.items),
            chunks_inserted=len(payload.items),
            embedding_provider=selection.provider,
            embedding_model=selection.model,
            results=results,
        )

    def _resolve_selection(
        self,
        profile_name: str | None,
        provider: str | None,
        model: str | None,
    ) -> EmbeddingSelection:
        selected = profile_name or self._settings.default_embedding_profile
        profile = self._settings.embedding_profiles[selected]
        if provider is not None and provider != profile.provider:
            raise ValueError("embedding_provider does not match embedding_profile")
        if model is not None and model != profile.model:
            raise ValueError("embedding_model does not match embedding_profile")
        return EmbeddingSelection(
            profile_name=selected,
            provider=profile.provider,
            model=profile.model,
            dimension=profile.dimension,
        )


class FakeChatService:
    def __init__(self, settings: StubSettings, corpus: list[dict[str, str]]) -> None:
        self._settings = settings
        self._corpus = corpus

    async def prepare_chat(
        self,
        payload: ChatRequest,
        rate_limit_key: str,
        session_id: str | None = None,
    ) -> ChatServiceResult:
        answer = (
            self._corpus[-1]["content"]
            if self._corpus
            else SAFE_FALLBACK_TEXT
        )
        profile_name = self._settings.default_embedding_profile
        profile = self._resolve_profile(profile_name)
        return ChatServiceResult(
            answer=answer,
            citations=[
                ChatCitation(
                    document_id=UUID("22222222-2222-2222-2222-222222222222"),
                    chunk_id=UUID("33333333-3333-3333-3333-333333333333"),
                    title="SNAIC Overview",
                    url=None,
                    source_type="text",
                    snippet=answer[:200],
                    metadata={},
                )
            ],
            provider=self._settings.default_llm_provider,
            model=self._settings.default_llm_model,
            embedding_profile=profile_name,
            embedding_provider=profile.provider,
            embedding_model=profile.model,
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

    def _resolve_profile(self, profile_name: str | None) -> EmbeddingProfileSpec:
        selected = profile_name or self._settings.default_embedding_profile
        return self._settings.embedding_profiles[selected]


def build_app() -> FastAPI:
    settings = StubSettings()
    corpus: list[dict[str, str]] = []
    app = FastAPI()
    app.include_router(auth_router, prefix="/auth")
    app.include_router(ingest_router, prefix="/ingest")
    app.include_router(chat_router, prefix="/chat")
    app.state.settings = settings
    app.state.auth_service = FakeAuthService(settings)
    app.state.fake_corpus = corpus
    return app


def test_auth_then_ingest_then_chat_returns_ingested_content(monkeypatch) -> None:
    app = build_app()

    monkeypatch.setattr(
        ingest_api,
        "_build_ingest_service",
        lambda request: FakeIngestService(request.app.state.settings, request.app.state.fake_corpus),
    )
    monkeypatch.setattr(
        chat_api,
        "_build_chat_service",
        lambda request: FakeChatService(request.app.state.settings, request.app.state.fake_corpus),
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
            assert token_response.status_code == 200
            token = token_response.json()["access_token"]

            ingest_response = await client.post(
                "/ingest/text",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "items": [
                        {
                            "title": "SNAIC Overview",
                            "content": INGESTED_CONTENT,
                        }
                    ],
                },
            )
            assert ingest_response.status_code == 200
            ingest_payload = ingest_response.json()
            assert ingest_payload["documents_inserted"] == 1
            assert ingest_payload["chunks_inserted"] == 1
            assert ingest_payload["embedding_model"] == "qwen3-embedding"

            chat_response = await client.post(
                "/chat",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "message": "what is snaic",
                },
            )
            assert chat_response.status_code == 200
            chat_payload = chat_response.json()
            assert INGESTED_CONTENT in chat_payload["answer"]
            assert chat_payload["session_id"]
            assert chat_response.headers["x-chat-session-id"] == chat_payload["session_id"]
            assert chat_response.cookies.get("rag_chat_session") == chat_payload["session_id"]
            assert chat_payload == {
                "answer": INGESTED_CONTENT,
                "citations": [
                    {
                        "document_id": "22222222-2222-2222-2222-222222222222",
                        "chunk_id": "33333333-3333-3333-3333-333333333333",
                    }
                ],
                "session_id": chat_payload["session_id"],
            }

    asyncio.run(run_flow())
