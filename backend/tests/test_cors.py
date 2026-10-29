import asyncio

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.core.cors import configure_cors


def _preflight(app, origin: str) -> httpx.Response:
    async def run_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.options(
                "/chat",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization,content-type",
                },
            )

    return asyncio.run(run_request())


def _build_app(monkeypatch, origins: str) -> FastAPI:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", origins)
    app = FastAPI()

    @app.post("/chat")
    async def chat() -> dict:
        return {}

    configure_cors(app, Settings())
    return app


def test_cors_preflight_allows_configured_origin(monkeypatch) -> None:
    app = _build_app(monkeypatch, "https://www.snaic.net,https://snaic.net/")

    response = _preflight(app, "https://www.snaic.net")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://www.snaic.net"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_cors_preflight_rejects_unknown_origin(monkeypatch) -> None:
    app = _build_app(monkeypatch, "https://www.snaic.net")

    response = _preflight(app, "https://evil.example")

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.snaic.net,https://snaic.net/", ["https://www.snaic.net", "https://snaic.net"]),
        ('["https://www.snaic.net/"]', ["https://www.snaic.net"]),
        (" https://www.snaic.net ", ["https://www.snaic.net"]),
        ("", []),
    ],
)
def test_cors_allowed_origins_parses_env_formats(monkeypatch, raw: str, expected: list[str]) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", raw)

    assert Settings().cors_allowed_origins == expected
