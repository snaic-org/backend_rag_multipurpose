import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("live-api")
    group.addoption("--live-api-base-url", action="store", default=None)
    group.addoption("--live-api-username", action="store", default=None)
    group.addoption("--live-api-password", action="store", default=None)
    group.addoption("--live-api-ingest-text", action="store", default=None)
    group.addoption("--live-api-ingest-title", action="store", default="SNAIC Overview")
    group.addoption("--live-api-chat-message", action="store", default="what is snaic")
    group.addoption("--live-api-generation-provider", action="store", default=None)
    group.addoption("--live-api-generation-model", action="store", default=None)
    group.addoption("--live-api-embedding-profile", action="store", default=None)
    group.addoption("--live-api-embedding-provider", action="store", default=None)
    group.addoption("--live-api-embedding-model", action="store", default=None)


@pytest.fixture
def live_api_config(request: pytest.FixtureRequest) -> dict[str, str | None]:
    options = {
        "base_url": request.config.getoption("--live-api-base-url"),
        "username": request.config.getoption("--live-api-username"),
        "password": request.config.getoption("--live-api-password"),
        "ingest_text": request.config.getoption("--live-api-ingest-text"),
        "ingest_title": request.config.getoption("--live-api-ingest-title"),
        "chat_message": request.config.getoption("--live-api-chat-message"),
        "generation_provider": request.config.getoption("--live-api-generation-provider"),
        "generation_model": request.config.getoption("--live-api-generation-model"),
        "embedding_profile": request.config.getoption("--live-api-embedding-profile"),
        "embedding_provider": request.config.getoption("--live-api-embedding-provider"),
        "embedding_model": request.config.getoption("--live-api-embedding-model"),
    }
    if not options["base_url"]:
        pytest.skip("live API options were not provided")
    return options


@pytest.fixture(autouse=True)
def _fresh_http_clients():
    """Each test runs in its own event loop, so pooled HTTP clients must not leak between tests."""
    from app.core.http_client import forget_shared_http_clients

    forget_shared_http_clients()
    yield
    forget_shared_http_clients()
