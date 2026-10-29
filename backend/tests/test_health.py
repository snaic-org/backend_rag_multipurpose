import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from app.api.health import router as health_router
from app.models.schemas import DependencyHealth, ModelHealthResponse, ModelProbeResult, ProviderHealth


class StubPostgres:
    async def healthcheck(self) -> DependencyHealth:
        return DependencyHealth(ok=True, detail="connected")


class StubRedis:
    async def healthcheck(self) -> DependencyHealth:
        return DependencyHealth(ok=True, detail="connected")


class StubQdrant:
    async def healthcheck(self) -> DependencyHealth:
        return DependencyHealth(ok=True, detail="connected")


class StubProviders:
    async def healthcheck_all(self) -> dict[str, ProviderHealth]:
        return {
            "openai": ProviderHealth(
                ok=True,
                detail="configuration_present",
                enabled=True,
                provider="openai",
                capabilities=["chat", "embeddings"],
                configuration_present=True,
            ),
            "gemini": ProviderHealth(
                ok=True,
                detail="configuration_present",
                enabled=True,
                provider="gemini",
                capabilities=["chat"],
                configuration_present=True,
            ),
            "ollama": ProviderHealth(
                ok=True,
                detail="reachable",
                enabled=True,
                provider="ollama",
                capabilities=["chat", "embeddings"],
                configuration_present=True,
            ),
        }


class StubSettings:
    app_name = "backend-rag-multipurpose"


def build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(health_router)
    app.state.postgres = StubPostgres()
    app.state.redis = StubRedis()
    app.state.qdrant = StubQdrant()
    app.state.providers = StubProviders()
    app.state.settings = StubSettings()
    return app


def test_health_endpoint_returns_ok() -> None:
    async def run_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=build_test_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/health")

    response = asyncio.run(run_request())

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["postgres"]["ok"] is True
    assert payload["redis"]["ok"] is True
    assert payload["qdrant"]["ok"] is True
    assert payload["providers"]["ollama"]["detail"] == "reachable"


class StubModelSelectionService:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def get_model_selection(self):
        if self._fail:
            raise RuntimeError("database unavailable")
        return SimpleNamespace(generation_provider="nim", embedding_provider="nim")


class StubNimDownProviders:
    async def healthcheck_all(self) -> dict[str, ProviderHealth]:
        return {
            "openai": ProviderHealth(ok=False, detail="missing OPENAI_API_KEY", provider="openai"),
            "nim": ProviderHealth(ok=self.nim_ok, detail="configuration_present", provider="nim"),
        }

    def __init__(self, nim_ok: bool) -> None:
        self.nim_ok = nim_ok


class StubModelHealthService:
    def __init__(self, status: str) -> None:
        self._status = status

    async def check(self) -> ModelHealthResponse:
        probe = ModelProbeResult(ok=self._status == "ok", detail="responding", provider="nim", model="m")
        return ModelHealthResponse(
            status=self._status,
            generation=probe,
            embedding=probe,
            checked_at=datetime.now(timezone.utc),
        )


def _get(app: FastAPI, path: str) -> httpx.Response:
    async def run_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(run_request())


def _build_selection_app(*, nim_ok: bool, selection_fails: bool = False) -> FastAPI:
    app = build_test_app()
    app.state.providers = StubNimDownProviders(nim_ok=nim_ok)
    app.state.model_selection_service = StubModelSelectionService(fail=selection_fails)
    app.state.settings = SimpleNamespace(app_name="backend-rag-multipurpose", rerank_enabled=False)
    return app


def test_health_ignores_unused_providers() -> None:
    payload = _get(_build_selection_app(nim_ok=True), "/health").json()

    assert payload["status"] == "ok"
    assert payload["providers"]["openai"]["in_use"] is False
    assert payload["providers"]["nim"]["in_use"] is True


def test_health_degrades_when_active_provider_fails() -> None:
    response = _get(_build_selection_app(nim_ok=False), "/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_health_degrades_when_model_selection_unreadable() -> None:
    response = _get(_build_selection_app(nim_ok=True, selection_fails=True), "/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_model_health_endpoint_status_codes() -> None:
    app = build_test_app()

    app.state.model_health_service = StubModelHealthService("ok")
    assert _get(app, "/health/models").status_code == 200

    app.state.model_health_service = StubModelHealthService("degraded")
    response = _get(app, "/health/models")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
