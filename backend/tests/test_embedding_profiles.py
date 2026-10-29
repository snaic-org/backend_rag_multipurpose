

from app.core.config import EmbeddingProfileSpec
from app.services.embeddings import EmbeddingService


class StubSettings:
    default_embedding_profile = "ollama_4096"
    embedding_profiles = {
        "ollama_4096": EmbeddingProfileSpec(
            provider="ollama",
            model="qwen3-embedding",
            dimension=4096,
        ),
        "openai_small_1536": EmbeddingProfileSpec(
            provider="openai",
            model="text-embedding-3-small",
            dimension=1536,
        ),
    }
    openai_api_key = "test"
    gemini_api_key = None
    ollama_base_url = "http://localhost:11434"
    ollama_health_timeout_seconds = 3.0


def test_embedding_service_resolves_4096_profile() -> None:
    service = EmbeddingService(StubSettings())

    selection = service.resolve_selection("ollama_4096", None, None)

    assert selection.provider == "ollama"
    assert selection.model == "qwen3-embedding"
    assert selection.dimension == 4096
