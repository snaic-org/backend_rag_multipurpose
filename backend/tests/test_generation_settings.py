from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import sys
import types
from types import SimpleNamespace

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
psycopg_module.rows = psycopg_rows_module
sys.modules.setdefault("psycopg", psycopg_module)
sys.modules.setdefault("psycopg.rows", psycopg_rows_module)

from app.models.schemas import ChatMessage
from app.core.config import Settings
from app.core.defaults import (
    CHAT_FREQUENCY_PENALTY,
    CHAT_MAX_RESPONSE_TOKENS,
    CHAT_PRESENCE_PENALTY,
    OPENAI_REASONING_EFFORT,
    CHAT_TOP_P,
)
from app.models.schemas import ModelSelectionRecord
from app.services.chat_service import ChatService
from app.services.model_selection_service import ModelSelectionService
from app.providers.gemini_provider import GeminiProvider
from app.providers.nim_provider import NimProvider
from app.providers.ollama_provider import OllamaProvider
from app.providers.openai_provider import OpenAIProvider


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _RetryingOpenAIResponse:
    def __init__(self, payload: dict, *, status_code: int = 200, text: str = "", reason_phrase: str = "OK") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.reason_phrase = reason_phrase

    def raise_for_status(self) -> None:
        if self.status_code < 400:
            return None
        module = __import__("app.providers.openai_provider", fromlist=["httpx"])
        request = module.httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        raise module.httpx.HTTPStatusError(
            "simulated openai thinking rejection",
            request=request,
            response=self,
        )

    def json(self) -> dict:
        return self._payload


class _FakeModelSelectionRepository:
    def __init__(self) -> None:
        self.record: ModelSelectionRecord | None = None
        self.generation_profiles = []
        self.embedding_profiles = []

    async def ensure_model_catalog_tables(self) -> None:
        return None

    async def seed_default_model_catalog(
        self,
        generation_profiles,
        embedding_profiles,
    ) -> None:
        self.generation_profiles = list(generation_profiles)
        self.embedding_profiles = list(embedding_profiles)

    async def ensure_model_selection_table(
        self,
        default_generation_profile: str,
        default_embedding_profile: str,
    ) -> None:
        if self.record is None:
            self.record = ModelSelectionRecord(
                id=1,
                generation_profile=default_generation_profile,
                embedding_profile=default_embedding_profile,
                updated_at=datetime.now(timezone.utc),
            )

    async def ensure_default_model_selection(
        self,
        default_generation_profile: str,
        default_embedding_profile: str,
    ) -> None:
        if self.record is None:
            self.record = ModelSelectionRecord(
                id=1,
                generation_profile=default_generation_profile,
                embedding_profile=default_embedding_profile,
                updated_at=datetime.now(timezone.utc),
            )

    async def get_model_selection(self) -> ModelSelectionRecord | None:
        return self.record

    async def update_model_selection(
        self,
        generation_profile: str,
        embedding_profile: str,
    ) -> ModelSelectionRecord:
        self.record = ModelSelectionRecord(
            id=1,
            generation_profile=generation_profile,
            embedding_profile=embedding_profile,
            updated_at=datetime.now(timezone.utc),
        )
        return self.record

    async def list_generation_profiles(self):
        return list(self.generation_profiles)

    async def list_embedding_profiles(self):
        return list(self.embedding_profiles)

    async def upsert_generation_profiles(self, generation_profiles):
        by_name = {profile.profile_name: profile for profile in self.generation_profiles}
        for profile in generation_profiles:
            by_name[profile.profile_name] = profile
        self.generation_profiles = list(by_name.values())

    async def upsert_embedding_profiles(self, embedding_profiles):
        by_name = {profile.profile_name: profile for profile in self.embedding_profiles}
        for profile in embedding_profiles:
            by_name[profile.profile_name] = profile
        self.embedding_profiles = list(by_name.values())


def _build_settings() -> SimpleNamespace:
    return SimpleNamespace(
        chat_temperature=0.15,
        chat_thinking_enabled=False,
        chat_show_thinking_block=False,
        openai_api_key="test-openai-key",
        gemini_api_key="test-gemini-key",
        ollama_base_url="http://localhost:11434",
        nim_base_url="http://localhost:8000/v1",
        nim_api_key=None,
    )


def _patch_async_client(monkeypatch, module, response_payload: dict, captured: dict) -> None:
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["init"] = {"args": args, "kwargs": kwargs}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, headers=None, json=None, params=None):
            captured["post"] = {
                "url": url,
                "headers": headers,
                "json": json,
                "params": params,
            }
            return _FakeResponse(response_payload)

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)


def test_openai_payload_uses_env_generation_settings(monkeypatch) -> None:
    settings = _build_settings()
    captured: dict = {}
    _patch_async_client(
        monkeypatch,
        __import__("app.providers.openai_provider", fromlist=["httpx"]),
        {"choices": [{"message": {"content": "ok"}}]},
        captured,
    )
    provider = OpenAIProvider(settings)

    result = asyncio.run(provider.complete_chat([ChatMessage(role="user", content="Hi")], "gpt-4o-mini"))

    payload = captured["post"]["json"]
    assert payload["temperature"] == settings.chat_temperature
    assert payload["top_p"] == CHAT_TOP_P
    assert payload["frequency_penalty"] == CHAT_FREQUENCY_PENALTY
    assert payload["presence_penalty"] == CHAT_PRESENCE_PENALTY
    assert payload["max_tokens"] == CHAT_MAX_RESPONSE_TOKENS
    assert "reasoning_effort" not in payload
    assert result.thinking is None


def test_gemini_payload_uses_env_generation_settings(monkeypatch) -> None:
    settings = _build_settings()
    settings.chat_thinking_enabled = True
    captured: dict = {}
    _patch_async_client(
        monkeypatch,
        __import__("app.providers.gemini_provider", fromlist=["httpx"]),
        {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
        captured,
    )
    provider = GeminiProvider(settings)

    asyncio.run(provider.complete_chat([ChatMessage(role="user", content="Hi")], "gemini-2.0-flash"))

    payload = captured["post"]["json"]
    generation_config = payload["generationConfig"]
    assert generation_config["temperature"] == settings.chat_temperature
    assert generation_config["topP"] == CHAT_TOP_P
    assert generation_config["maxOutputTokens"] == CHAT_MAX_RESPONSE_TOKENS
    assert generation_config["candidateCount"] == 1
    assert generation_config["thinkingConfig"]["thinkingBudget"] == -1
    assert generation_config["thinkingConfig"]["includeThoughts"] is True


def test_ollama_payload_uses_env_generation_settings(monkeypatch) -> None:
    settings = _build_settings()
    settings.chat_thinking_enabled = True
    captured: dict = {}
    _patch_async_client(
        monkeypatch,
        __import__("app.providers.ollama_provider", fromlist=["httpx"]),
        {"message": {"content": "ok"}},
        captured,
    )
    provider = OllamaProvider(settings)

    asyncio.run(provider.complete_chat([ChatMessage(role="user", content="Hi")], "llama3.2"))

    payload = captured["post"]["json"]
    options = payload["options"]
    assert options["temperature"] == settings.chat_temperature
    assert options["top_p"] == CHAT_TOP_P
    assert options["num_predict"] == CHAT_MAX_RESPONSE_TOKENS
    assert payload["think"] is True


def test_nim_payload_uses_env_generation_settings(monkeypatch) -> None:
    settings = _build_settings()
    settings.chat_thinking_enabled = True
    captured: dict = {}
    _patch_async_client(
        monkeypatch,
        __import__("app.providers.nim_provider", fromlist=["httpx"]),
        {"choices": [{"message": {"content": "ok"}}]},
        captured,
    )
    provider = NimProvider(settings)

    asyncio.run(provider.complete_chat([ChatMessage(role="user", content="Hi")], "meta/llama"))

    payload = captured["post"]["json"]
    assert payload["temperature"] == settings.chat_temperature
    assert payload["top_p"] == CHAT_TOP_P
    assert payload["frequency_penalty"] == CHAT_FREQUENCY_PENALTY
    assert payload["presence_penalty"] == CHAT_PRESENCE_PENALTY
    assert payload["max_tokens"] == CHAT_MAX_RESPONSE_TOKENS
    assert payload["chat_template_kwargs"]["enable_thinking"] is True


def test_openai_payload_uses_reasoning_effort_when_enabled(monkeypatch) -> None:
    settings = _build_settings()
    settings.chat_thinking_enabled = True
    captured: dict = {}
    _patch_async_client(
        monkeypatch,
        __import__("app.providers.openai_provider", fromlist=["httpx"]),
        {"choices": [{"message": {"content": "ok"}}]},
        captured,
    )
    provider = OpenAIProvider(settings)

    asyncio.run(provider.complete_chat([ChatMessage(role="user", content="Hi")], "gpt-4o-mini"))

    payload = captured["post"]["json"]
    assert payload["reasoning_effort"] == OPENAI_REASONING_EFFORT
    assert payload["max_completion_tokens"] == CHAT_MAX_RESPONSE_TOKENS


def test_openai_retries_without_thinking_when_model_rejects_it(monkeypatch) -> None:
    settings = _build_settings()
    settings.chat_thinking_enabled = True
    captured: dict = {"posts": []}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["init"] = {"args": args, "kwargs": kwargs}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, headers=None, json=None, params=None):
            captured["posts"].append(
                {
                    "url": url,
                    "headers": headers,
                    "json": json,
                    "params": params,
                }
            )
            if "reasoning_effort" in (json or {}):
                return _RetryingOpenAIResponse(
                    {},
                    status_code=404,
                    text="reasoning_effort is not supported",
                    reason_phrase="Not Found",
                )
            return _RetryingOpenAIResponse({"choices": [{"message": {"content": "ok"}}]})

    module = __import__("app.providers.openai_provider", fromlist=["httpx"])
    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
    provider = OpenAIProvider(settings)

    result = asyncio.run(provider.complete_chat([ChatMessage(role="user", content="Hi")], "gpt-4o-mini"))

    assert result.text == "ok"
    assert len(captured["posts"]) == 2
    assert "reasoning_effort" in captured["posts"][0]["json"]
    assert "reasoning_effort" not in captured["posts"][1]["json"]


def test_chat_service_formats_thinking_block_based_on_setting() -> None:
    service = object.__new__(ChatService)

    service._settings = SimpleNamespace(chat_show_thinking_block=False)  # type: ignore[attr-defined]
    assert service._format_answer("<think>reasoning</think> final", "reasoning") == "final"

    service._settings = SimpleNamespace(chat_show_thinking_block=True)  # type: ignore[attr-defined]
    assert service._format_answer("final", "reasoning") == "<thinking>\nreasoning\n</thinking>\n\nfinal"


def test_model_selection_service_seeds_nim_defaults() -> None:
    settings = Settings(
        default_generation_provider="nim",
        default_generation_model="nvidia/nemotron-3-super-120b-a12b",
        default_embedding_provider="nim",
        default_embedding_model="nvidia/llama-nemotron-embed-1b-v2",
        default_embedding_dimension=2048,
    )

    service = ModelSelectionService(settings, postgres_pool=SimpleNamespace())
    service._repository = _FakeModelSelectionRepository()  # type: ignore[attr-defined]

    asyncio.run(service.ensure_default_model_selection())
    selection = asyncio.run(service.get_model_selection())
    catalog = asyncio.run(service.get_catalog())
    assumptions = settings.phase_one_assumptions()

    assert selection.generation_profile == "nim_3super120"
    assert selection.embedding_profile == "nim_nemotron_2048"
    assert selection.generation_provider == "nim"
    assert selection.embedding_provider == "nim"
    assert catalog.generation_profiles[1].profile_name == "nim_3super120"
    assert assumptions["model_selection_source"] == "database"
    assert assumptions["configured_generation_profiles"]["nim_3super120"]["provider"] == "nim"


def test_guardrails_truncate_response_ends_cleanly() -> None:
    from app.services.guardrails import GuardrailService

    service = object.__new__(GuardrailService)
    service._settings = SimpleNamespace()  # type: ignore[attr-defined]

    text = (
        "SNAIC helps industry partners build practical AI solutions. "
        "This part should be trimmed mid-sentence. "
        * 80
    )
    truncated = service.truncate_response(text)

    assert truncated.endswith(("...", ".", "!", "?"))
    assert len(truncated) < len(text)
