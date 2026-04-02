from abc import ABC, abstractmethod
import asyncio
from urllib.parse import urlparse

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.schemas import EmbeddingSelection, ProviderName
from app.services.cache_service import CacheService

logger = get_logger(__name__)
OPENAI_API_BASE_URL = "https://api.openai.com/v1"


class EmbeddingProvider(ABC):
    provider_name: str

    @abstractmethod
    async def embed(
        self,
        texts: list[str],
        model: str,
        input_type: str | None = None,
    ) -> list[list[float]]:
        raise NotImplementedError


class OpenAIEmbeddingProvider(EmbeddingProvider):
    provider_name = "openai"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def embed(
        self,
        texts: list[str],
        model: str,
        input_type: str | None = None,
    ) -> list[list[float]]:
        if not self._settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI embeddings")

        payload = {"input": texts, "model": model}
        if input_type and self._supports_nim_input_type(model):
            payload["input_type"] = input_type
            payload["truncate"] = "NONE"

        headers = {"Content-Type": "application/json"}
        if self._settings.openai_api_key:
            headers["Authorization"] = f"Bearer {self._settings.openai_api_key}"

        async with httpx.AsyncClient(
            base_url=OPENAI_API_BASE_URL,
            timeout=30.0,
        ) as client:
            response = await client.post(
                "/embeddings",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        return [item["embedding"] for item in data["data"]]

    def _supports_nim_input_type(self, model: str) -> bool:
        return model.startswith("nvidia/")


class NimEmbeddingProvider(EmbeddingProvider):
    provider_name = "nim"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def embed(
        self,
        texts: list[str],
        model: str,
        input_type: str | None = None,
    ) -> list[list[float]]:
        if not self._settings.nim_base_url.strip():
            raise ValueError("NIM_BASE_URL is required for NIM embeddings")
        if self._requires_api_key() and not self._settings.nim_api_key:
            raise ValueError("NIM_API_KEY is required for NIM embeddings")

        headers = {"Content-Type": "application/json"}
        if self._settings.nim_api_key:
            headers["Authorization"] = f"Bearer {self._settings.nim_api_key}"

        payload = {"input": texts, "model": model}
        payload_with_input_type = dict(payload)
        if input_type and self._supports_nim_input_type(model):
            payload_with_input_type["input_type"] = input_type
            payload_with_input_type["truncate"] = "NONE"

        async with httpx.AsyncClient(base_url=self._settings.nim_base_url, timeout=30.0) as client:
            data = await self._request_with_retry(
                client=client,
                headers=headers,
                primary_payload=payload_with_input_type,
                fallback_payload=payload,
            )

        return [item["embedding"] for item in data["data"]]

    async def _request_with_retry(
        self,
        *,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        primary_payload: dict[str, object],
        fallback_payload: dict[str, object],
    ) -> dict[str, object]:
        attempts = 3
        last_exc: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = await client.post(
                    "/embeddings",
                    headers=headers,
                    json=primary_payload,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status_code = exc.response.status_code

                if status_code >= 500:
                    if primary_payload != fallback_payload:
                        try:
                            fallback_response = await client.post(
                                "/embeddings",
                                headers=headers,
                                json=fallback_payload,
                            )
                            fallback_response.raise_for_status()
                            return fallback_response.json()
                        except httpx.HTTPStatusError as fallback_exc:
                            last_exc = fallback_exc
                        except httpx.HTTPError as fallback_exc:
                            last_exc = fallback_exc

                    if attempt < attempts:
                        await asyncio.sleep(0.75 * attempt)
                        continue

                raise RuntimeError(
                    f"NIM embeddings request failed with status {status_code}: {exc.response.text[:300]}"
                ) from exc
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < attempts:
                    await asyncio.sleep(0.75 * attempt)
                    continue
                raise RuntimeError(f"NIM embeddings request failed: {exc}") from exc

        raise RuntimeError(f"NIM embeddings request failed after {attempts} attempts: {last_exc}")

    def _requires_api_key(self) -> bool:
        host = urlparse(self._settings.nim_base_url).netloc.lower()
        return "api.openai.com" in host

    def _supports_nim_input_type(self, model: str) -> bool:
        return model.startswith("nvidia/")


class GeminiEmbeddingProvider(EmbeddingProvider):
    provider_name = "gemini"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def embed(
        self,
        texts: list[str],
        model: str,
        input_type: str | None = None,
    ) -> list[list[float]]:
        if not self._settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required for Gemini embeddings")

        embeddings: list[list[float]] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for text in texts:
                response = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent",
                    params={"key": self._settings.gemini_api_key},
                    json={"content": {"parts": [{"text": text}]}},
                )
                response.raise_for_status()
                data = response.json()
                embeddings.append(data["embedding"]["values"])
        return embeddings


class OllamaEmbeddingProvider(EmbeddingProvider):
    provider_name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def embed(
        self,
        texts: list[str],
        model: str,
        input_type: str | None = None,
    ) -> list[list[float]]:
        embeddings: list[list[float]] = []
        async with httpx.AsyncClient(
            base_url=self._settings.ollama_base_url,
            timeout=60.0,
        ) as client:
            for text in texts:
                response = await client.post(
                    "/api/embeddings",
                    json={"model": model, "prompt": text},
                )
                response.raise_for_status()
                data = response.json()
                embeddings.append(data["embedding"])
        return embeddings


class EmbeddingService:
    def __init__(self, settings: Settings, cache_service: CacheService | None = None) -> None:
        self._settings = settings
        self._cache = cache_service
        self._providers: dict[ProviderName, EmbeddingProvider] = {
            "openai": OpenAIEmbeddingProvider(settings),
            "gemini": GeminiEmbeddingProvider(settings),
            "ollama": OllamaEmbeddingProvider(settings),
            "nim": NimEmbeddingProvider(settings),
        }

    def resolve_selection(
        self,
        profile_name: str | None,
        provider: str | None,
        model: str | None,
        default_profile_name: str | None = None,
    ) -> EmbeddingSelection:
        if profile_name is not None:
            selected_profile_name = profile_name
            profile = self._settings.embedding_profiles.get(selected_profile_name)
            if profile is None:
                raise ValueError(f"Unknown embedding profile '{selected_profile_name}'")
            if provider is not None and profile.provider != provider:
                raise ValueError(
                    f"embedding_profile '{selected_profile_name}' does not match embedding_provider '{provider}'"
                )
            if model is not None and profile.model != model:
                raise ValueError(
                    f"embedding_profile '{selected_profile_name}' does not match embedding_model '{model}'"
                )
        elif provider is not None or model is not None:
            selected_profile_name = self._find_profile_name(provider, model)
            profile = self._settings.embedding_profiles.get(selected_profile_name)
            if profile is None:
                raise ValueError(f"Unknown embedding profile '{selected_profile_name}'")
        else:
            selected_profile_name = default_profile_name or self._settings.default_embedding_profile
            profile = self._settings.embedding_profiles.get(selected_profile_name)
            if profile is None:
                raise ValueError(f"Unknown embedding profile '{selected_profile_name}'")

        if profile.provider not in self._providers:
            raise ValueError(f"Unsupported embedding provider '{profile.provider}'")

        return EmbeddingSelection(
            profile_name=selected_profile_name,
            provider=profile.provider,
            model=profile.model,
            dimension=profile.dimension,
        )

    async def embed_texts(
        self,
        texts: list[str],
        profile_name: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        input_type: str | None = None,
        default_profile_name: str | None = None,
    ) -> tuple[EmbeddingSelection, list[list[float]]]:
        selection = self.resolve_selection(profile_name, provider, model, default_profile_name=default_profile_name)
        if not texts:
            return selection, []

        cache_key = None
        if self._cache is not None:
            cache_key = self._cache.make_key(
                "embeddings",
                {
                    "provider": selection.provider,
                    "model": selection.model,
                    "input_type": input_type,
                    "texts": texts,
                },
            )
            cached = await self._cache.get_json(cache_key)
            if isinstance(cached, list):
                return selection, cached

        embeddings = await self._providers[selection.provider].embed(
            texts,
            selection.model,
            input_type=input_type,
        )

        for embedding in embeddings:
            if len(embedding) != selection.dimension:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {selection.dimension}, got {len(embedding)}"
                )

        if self._cache is not None and cache_key is not None:
            await self._cache.set_json(cache_key, embeddings)

        logger.info(
            "embeddings_generated profile=%s provider=%s model=%s count=%s",
            selection.profile_name,
            selection.provider,
            selection.model,
            len(embeddings),
        )
        return selection, embeddings

    def _find_profile_name(self, provider: str | None, model: str | None) -> str:
        if provider is None and model is None:
            return self._settings.default_embedding_profile

        matches: list[str] = []
        for profile_name, profile in self._settings.embedding_profiles.items():
            if provider is not None and profile.provider != provider:
                continue
            if model is not None and profile.model != model:
                continue
            matches.append(profile_name)

        if not matches:
            raise ValueError(
                "No configured embedding profile matches the requested provider/model pair"
            )
        if len(matches) > 1:
            raise ValueError(
                "Multiple embedding profiles match the requested provider/model pair. "
                "Specify embedding_profile explicitly."
            )
        return matches[0]
