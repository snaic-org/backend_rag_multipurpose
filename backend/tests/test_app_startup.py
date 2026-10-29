import asyncio
from types import SimpleNamespace

from fastapi import FastAPI

import app.main as main_module
from app.core.config import Settings


class FakePostgres:
    def __init__(self, settings) -> None:
        self.pool = object()

    async def connect(self) -> None: ...
    async def apply_schema(self) -> None: ...
    async def close(self) -> None: ...


class FakeRedis:
    def __init__(self, settings) -> None:
        self.client = SimpleNamespace()

    async def connect(self) -> None: ...
    async def close(self) -> None: ...


class FakeQdrant:
    def __init__(self, settings) -> None: ...

    async def ensure_collection(self, dimension: int) -> str:
        return f"rag_chunks_{dimension}"

    async def close(self) -> None: ...


class FakeStartupService:
    def __init__(self, *args, **kwargs) -> None: ...

    async def ensure_bootstrap_admin(self) -> None: ...
    async def ensure_default_system_prompt(self) -> None: ...
    async def ensure_default_model_selection(self) -> None: ...

    async def get_model_selection(self):
        return SimpleNamespace(embedding_profile="nim_nemotron_2048")


def test_startup_builds_shared_request_services(monkeypatch) -> None:
    settings = Settings(
        default_generation_provider="nim",
        default_generation_model="nvidia/nemotron-3.5-super-vl-120b-a12b",
        default_embedding_provider="nim",
        default_embedding_model="nvidia/nemotron-3-embed-1b",
        default_embedding_dimension=2048,
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "PostgresManager", FakePostgres)
    monkeypatch.setattr(main_module, "RedisManager", FakeRedis)
    monkeypatch.setattr(main_module, "QdrantManager", FakeQdrant)
    for name in ("AuthService", "ChatActivityService", "ChatFeedbackService", "SystemPromptService", "ModelSelectionService"):
        monkeypatch.setattr(main_module, name, FakeStartupService)

    app = FastAPI()

    async def run() -> None:
        async with main_module.lifespan(app):
            from app.api import admin, chat, ingest

            request = SimpleNamespace(app=app)
            assert chat._build_chat_service(request) is app.state.chat_service
            assert ingest._build_ingest_service(request) is app.state.ingest_service
            assert admin._build_ingest_service(request) is app.state.ingest_service
            assert admin._build_reset_service(request) is app.state.reset_service
            assert admin._build_document_inspection_service(request) is app.state.document_inspection_service
            assert app.state.model_health_service is not None

    asyncio.run(run())
