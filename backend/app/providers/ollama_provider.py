import json
from typing import AsyncIterator

import httpx

from app.core.config import Settings
from app.core.config import CHAT_MAX_RESPONSE_TOKENS, CHAT_TOP_P
from app.core.http_client import shared_http_client
from app.models.schemas import ChatCompletionResult, ChatMessage, ProviderHealth
from app.providers.base import ProviderAdapter
from app.providers.common import (
    raise_for_status,
    should_retry_without_thinking,
    strip_thinking_delta,
    is_thinking_enabled,
)


_THINKING_ERROR_TOKENS = ("think", "thinking", "reasoning")


class OllamaProvider(ProviderAdapter):
    provider_name = "ollama"
    capabilities = ["chat", "embeddings"]

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def healthcheck(self) -> ProviderHealth:
        try:
            async with shared_http_client(
                base_url=self._settings.ollama_base_url,
                timeout=self._settings.ollama_health_timeout_seconds,
            ) as client:
                response = await client.get("/api/tags")

            if response.status_code >= 400:
                return ProviderHealth(
                    ok=False,
                    detail=f"ollama_http_{response.status_code}",
                    enabled=True,
                    provider=self.provider_name,
                    capabilities=self.capabilities,
                    configuration_present=True,
                )

            return ProviderHealth(
                ok=True,
                detail="reachable",
                enabled=True,
                provider=self.provider_name,
                capabilities=self.capabilities,
                configuration_present=True,
            )
        except httpx.HTTPError as exc:
            return ProviderHealth(
                ok=False,
                detail=f"ollama_unreachable: {type(exc).__name__}",
                enabled=True,
                provider=self.provider_name,
                capabilities=self.capabilities,
                configuration_present=True,
            )

    async def complete_chat(
        self,
        messages: list[ChatMessage],
        model: str,
    ) -> ChatCompletionResult:
        thinking_enabled = is_thinking_enabled(self._settings)
        data = await self._post_chat(messages, model, thinking_enabled=thinking_enabled)
        if data is None and thinking_enabled:
            data = await self._post_chat(messages, model, thinking_enabled=False)
        if data is None:
            raise ValueError("Ollama chat request failed")

        text = data.get("message", {}).get("content") or ""
        thinking = data.get("message", {}).get("thinking")
        return ChatCompletionResult(text=text, thinking=thinking, provider=self.provider_name, model=model)

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        model: str,
    ) -> AsyncIterator[str]:
        thinking_enabled = is_thinking_enabled(self._settings)
        try:
            async for delta in self._stream_chat(messages, model, thinking_enabled=thinking_enabled):
                yield delta
            return
        except httpx.HTTPStatusError as exc:
            if not self._should_retry_without_thinking(exc):
                raise

        async for delta in self._stream_chat(messages, model, thinking_enabled=False):
            yield delta

    async def _post_chat(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        thinking_enabled: bool,
    ) -> dict | None:
        try:
            async with shared_http_client(
                base_url=self._settings.ollama_base_url,
                timeout=60.0,
            ) as client:
                response = await client.post(
                    "/api/chat",
                json=self._build_payload(messages, model, thinking_enabled=thinking_enabled, stream=False),
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            if self._should_retry_without_thinking(exc):
                return None
            raise

    async def _stream_chat(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        thinking_enabled: bool,
    ) -> AsyncIterator[str]:
        async with shared_http_client(
            base_url=self._settings.ollama_base_url,
            timeout=60.0,
        ) as client:
            async with client.stream(
                "POST",
                "/api/chat",
                json=self._build_payload(messages, model, thinking_enabled=thinking_enabled, stream=True),
            ) as response:
                await raise_for_status(response)

                in_thinking = False
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    payload = json.loads(line)
                    message = payload.get("message", {})
                    if self._settings.chat_show_thinking_block:
                        delta = message.get("content", "")
                    else:
                        delta, in_thinking = strip_thinking_delta(
                            message.get("content", ""),
                            in_thinking,
                        )
                        if message.get("thinking") and not message.get("content"):
                            continue
                    if delta:
                        yield delta

    def _build_payload(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        thinking_enabled: bool,
        stream: bool,
    ) -> dict:
        return {
            "model": model,
            "stream": stream,
            "messages": [message.model_dump() for message in messages],
            "think": thinking_enabled,
            "options": {
                "temperature": self._settings.chat_temperature,
                "top_p": CHAT_TOP_P,
                "num_predict": CHAT_MAX_RESPONSE_TOKENS,
            },
        }

    def _should_retry_without_thinking(self, exc: httpx.HTTPStatusError) -> bool:
        return should_retry_without_thinking(self._settings, exc, _THINKING_ERROR_TOKENS)
