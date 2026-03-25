from app.core.config import Settings
from app.models.schemas import ProviderHealth
from app.providers.base import ProviderAdapter
from app.providers.gemini_provider import GeminiProvider
from app.providers.nim_provider import NimProvider
from app.providers.ollama_provider import OllamaProvider
from app.providers.openai_provider import OpenAIProvider


class ProviderRegistry:
    def __init__(self, providers: dict[str, ProviderAdapter]) -> None:
        self._providers = providers

    @classmethod
    def from_settings(cls, settings: Settings) -> "ProviderRegistry":
        providers: dict[str, ProviderAdapter] = {
            "openai": OpenAIProvider(settings),
            "gemini": GeminiProvider(settings),
            "ollama": OllamaProvider(settings),
            "nim": NimProvider(settings),
        }
        return cls(providers)

    def get(self, provider_name: str) -> ProviderAdapter:
        try:
            return self._providers[provider_name]
        except KeyError as exc:
            raise ValueError(f"Unsupported provider: {provider_name}") from exc

    async def healthcheck_all(self) -> dict[str, ProviderHealth]:
        results: dict[str, ProviderHealth] = {}
        for name, provider in self._providers.items():
            results[name] = await provider.healthcheck()
        return results

    def supported_provider_names(self) -> list[str]:
        return list(self._providers.keys())
