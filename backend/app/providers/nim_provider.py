import json
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx

from app.core.config import (
    CHAT_FREQUENCY_PENALTY,
    CHAT_MAX_RESPONSE_TOKENS,
    CHAT_PRESENCE_PENALTY,
    CHAT_TOP_P,
    Settings,
)
from app.core.http_client import shared_http_client
from app.models.schemas import ChatCompletionResult, ChatMessage, ProviderHealth
from app.providers.base import ProviderAdapter
from app.providers.common import (
    bearer_headers,
    extract_inline_thinking,
    raise_for_status,
    should_retry_without_thinking,
    strip_thinking_delta,
    is_thinking_enabled,
)


_THINKING_ERROR_TOKENS = ("detailed thinking", "thinking", "reasoning", "think")


class NimProvider(ProviderAdapter):
    provider_name = "nim"
    capabilities = ["chat", "embeddings"]

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def healthcheck(self) -> ProviderHealth:
        configured = bool(self._settings.nim_base_url.strip())

        if not configured:
            return ProviderHealth(
                ok=False,
                detail="missing NIM_BASE_URL",
                enabled=True,
                provider=self.provider_name,
                capabilities=self.capabilities,
                configuration_present=False,
            )

        if self._uses_hosted_api() and not self._settings.nim_api_key:
            return ProviderHealth(
                ok=False,
                detail="missing NIM_API_KEY",
                enabled=True,
                provider=self.provider_name,
                capabilities=self.capabilities,
                configuration_present=False,
            )

        return ProviderHealth(
            ok=True,
            detail="configuration_present",
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
        if not self._settings.nim_base_url.strip():
            raise ValueError("NIM_BASE_URL is required for NIM chat")

        headers = bearer_headers(self._settings.nim_api_key)
        thinking_enabled = is_thinking_enabled(self._settings)
        data = await self._post_chat(messages, model, headers=headers, thinking_enabled=thinking_enabled)
        if data is None and thinking_enabled:
            data = await self._post_chat(messages, model, headers=headers, thinking_enabled=False)
        if data is None:
            raise ValueError("NIM chat request failed")

        text, thinking = self._extract_text_and_thinking(data)
        return ChatCompletionResult(text=text, thinking=thinking, provider=self.provider_name, model=model)

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        model: str,
    ) -> AsyncIterator[str]:
        if not self._settings.nim_base_url.strip():
            raise ValueError("NIM_BASE_URL is required for NIM chat")

        headers = bearer_headers(self._settings.nim_api_key)
        thinking_enabled = is_thinking_enabled(self._settings)
        try:
            async for delta in self._stream_chat(messages, model, headers=headers, thinking_enabled=thinking_enabled):
                yield delta
            return
        except httpx.HTTPStatusError as exc:
            if not self._should_retry_without_thinking(exc):
                raise

        async for delta in self._stream_chat(messages, model, headers=headers, thinking_enabled=False):
            yield delta

    def _uses_hosted_api(self) -> bool:
        host = urlparse(self._settings.nim_base_url).netloc.lower()
        return host.endswith("api.nvidia.com")

    def _extract_text_and_thinking(self, data: dict) -> tuple[str, str | None]:
        choices = data.get("choices", [])
        if not choices:
            return "", None

        message = choices[0].get("message", {})
        text = message.get("content") or ""
        thinking = message.get("thinking")
        if thinking is not None:
            return text, thinking

        extracted = extract_inline_thinking(text)
        if extracted is not None:
            return text, extracted

        return text, None

    async def _post_chat(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        headers: dict[str, str],
        thinking_enabled: bool,
    ) -> dict | None:
        try:
            async with shared_http_client(
                base_url=self._settings.nim_base_url,
                timeout=60.0,
            ) as client:
                response = await client.post(
                    "/chat/completions",
                    headers=headers,
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
        headers: dict[str, str],
        thinking_enabled: bool,
    ) -> AsyncIterator[str]:
        async with shared_http_client(
            base_url=self._settings.nim_base_url,
            timeout=60.0,
        ) as client:
            async with client.stream(
                "POST",
                "/chat/completions",
                headers=headers,
                json=self._build_payload(messages, model, thinking_enabled=thinking_enabled, stream=True),
            ) as response:
                await raise_for_status(response)

                in_thinking = False
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue

                    data = line[6:].strip()
                    if data == "[DONE]":
                        break

                    parsed = json.loads(data)
                    choices = parsed.get("choices", [])
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {}).get("content", "")
                    if delta:
                        if self._settings.chat_show_thinking_block:
                            yield delta
                        else:
                            clean_delta, in_thinking = strip_thinking_delta(delta, in_thinking)
                            if clean_delta:
                                yield clean_delta

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
            "messages": [message.model_dump() for message in messages],
            "stream": stream,
            "temperature": self._settings.chat_temperature,
            "top_p": CHAT_TOP_P,
            "frequency_penalty": CHAT_FREQUENCY_PENALTY,
            "presence_penalty": CHAT_PRESENCE_PENALTY,
            "max_tokens": CHAT_MAX_RESPONSE_TOKENS,
            "chat_template_kwargs": {
                "enable_thinking": thinking_enabled,
            },
        }

    def _should_retry_without_thinking(self, exc: httpx.HTTPStatusError) -> bool:
        return should_retry_without_thinking(self._settings, exc, _THINKING_ERROR_TOKENS)
