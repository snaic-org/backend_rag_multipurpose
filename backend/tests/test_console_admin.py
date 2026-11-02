"""Admin endpoints behind the SNAIC Console: playground, model switching guard, prompt reset."""

from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import admin as admin_module
from app.core.config import Settings
from app.core.security import require_admin_user
from app.models.schemas import ChatCitation, ChatServiceResult

READY = {"nim": True, "openai": False}


class FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name

    async def healthcheck(self):
        ok = READY.get(self.name, False)
        return SimpleNamespace(ok=ok, detail="configuration_present" if ok else "missing API key")


class FakeChatService:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = []
        self.error = error

    async def preview_answer(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        citation = dict(document_id="00000000-0000-0000-0000-000000000001", chunk_id="00000000-0000-0000-0000-000000000002",
                        title="SNAIC Projects Directory", url="https://www.snaic.net/projects", source_type="cms", snippet="...")
        return ChatServiceResult(
            answer="SNAIC has 6 showcased projects.",
            citations=[ChatCitation(**citation), ChatCitation(**citation)],
            provider=kwargs["provider"], model=kwargs["model"],
            embedding_profile="nim_nemotron_2048", embedding_provider="nim", embedding_model="embed",
        )


class FakeSelection:
    def __init__(self) -> None:
        self.updates = []

    async def get_model_selection(self):
        return SimpleNamespace(generation_profile="nim_35super120")

    async def update_model_selection(self, generation_profile, embedding_profile):
        self.updates.append((generation_profile, embedding_profile))
        from datetime import datetime, timezone
        return {
            "generation_profile": generation_profile, "generation_provider": "nim", "generation_model": "m",
            "embedding_profile": embedding_profile, "embedding_provider": "nim", "embedding_model": "e",
            "embedding_dimension": 2048, "updated_at": datetime.now(timezone.utc),
        }


def _client(chat_service=None):
    app = FastAPI()
    app.include_router(admin_module.router, prefix="/admin")
    app.state.settings = Settings()
    app.state.providers = SimpleNamespace(get=FakeProvider)
    app.state.chat_service = chat_service or FakeChatService()
    app.state.model_selection_service = FakeSelection()
    app.dependency_overrides[require_admin_user] = lambda: SimpleNamespace(id="admin-1", is_admin=True)
    return TestClient(app), app


def test_playground_answers_with_chosen_model_and_draft_prompt() -> None:
    client, app = _client()

    response = client.post("/admin/playground", json={
        "message": "How many projects?", "generation_profile": "nim_35lightning30", "system_prompt": "Be brief.",
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model"] == "nvidia/nemotron-3.5-lightning-30b-a3b" and body["generation_profile"] == "nim_35lightning30"
    assert body["sources"] == [{"title": "SNAIC Projects Directory", "url": "https://www.snaic.net/projects"}]
    call = app.state.chat_service.calls[0]
    assert call["system_prompt"] == "Be brief." and call["rate_limit_key"] == "playground:admin-1"


def test_playground_defaults_to_live_model_and_reports_failures() -> None:
    client, _ = _client()
    assert client.post("/admin/playground", json={"message": "Hi there"}).json()["generation_profile"] == "nim_35super120"

    request = httpx.Request("POST", "https://example.test")
    failing = FakeChatService(httpx.HTTPStatusError("x", request=request, response=httpx.Response(404, request=request)))
    client, _ = _client(failing)
    response = client.post("/admin/playground", json={"message": "Hi there"})
    assert response.status_code == 502
    assert response.json()["detail"] == "nvidia/nemotron-3.5-super-vl-120b-a12b did not answer (HTTP 404)."


def test_playground_and_switch_refuse_unconfigured_providers() -> None:
    client, app = _client()

    assert client.post("/admin/playground", json={"message": "Hi", "generation_profile": "openai_gpt41_mini"}).status_code == 400
    assert client.post("/admin/playground", json={"message": "Hi", "generation_profile": "nope"}).status_code == 400
    refused = client.put("/admin/model-selection", json={"generation_profile": "openai_gpt41_mini", "embedding_profile": "nim_nemotron_2048"})
    assert refused.status_code == 400 and "not ready" in refused.json()["detail"]
    assert app.state.model_selection_service.updates == []

    switched = client.put("/admin/model-selection", json={"generation_profile": "nim_35lightning30", "embedding_profile": "nim_nemotron_2048"})
    assert switched.status_code == 200
    assert app.state.model_selection_service.updates == [("nim_35lightning30", "nim_nemotron_2048")]


def test_prompt_reset_endpoint() -> None:
    client, app = _client()

    class Prompts:
        async def reset_system_prompt(self):
            from datetime import datetime, timezone
            return {"system_prompt": "built-in", "updated_at": datetime.now(timezone.utc), "is_custom": False}

    app.state.prompt_service = Prompts()
    response = client.post("/admin/system-prompt/reset")
    assert response.status_code == 200 and response.json()["is_custom"] is False


def test_switching_models_expires_the_health_result() -> None:
    client, app = _client()
    calls = []
    app.state.model_health_service = SimpleNamespace(invalidate=lambda: calls.append("invalidate"))

    response = client.put("/admin/model-selection", json={"generation_profile": "nim_35lightning30", "embedding_profile": "nim_nemotron_2048"})

    assert response.status_code == 200 and calls == ["invalidate"]
