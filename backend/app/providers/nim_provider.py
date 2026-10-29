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
from app.models.schemas import ChatCompletionResult, ChatMessage, ProviderHealth
from app.providers.base import ProviderAdapter


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
        if self._requires_api_key() and not self._settings.nim_api_key:
            raise ValueError("NIM_API_KEY is required for NIM chat")

        headers = self._build_headers()
        thinking_enabled = self._thinking_enabled()
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
        if self._requires_api_key() and not self._settings.nim_api_key:
            raise ValueError("NIM_API_KEY is required for NIM chat")

        headers = self._build_headers()
        thinking_enabled = self._thinking_enabled()
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

    def _requires_api_key(self) -> bool:
        host = urlparse(self._settings.nim_base_url).netloc.lower()
        return "api.openai.com" in host

    def _extract_text_and_thinking(self, data: dict) -> tuple[str, str | None]:
        choices = data.get("choices", [])
        if not choices:
            return "", None

        message = choices[0].get("message", {})
        text = message.get("content") or ""
        thinking = message.get("thinking")
        if thinking is not None:
            return text, thinking

        extracted = self._extract_inline_thinking(text)
        if extracted is not None:
            return text, extracted

        return text, None

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._settings.nim_api_key:
            headers["Authorization"] = f"Bearer {self._settings.nim_api_key}"
        return headers

    def _build_messages(self, messages: list[ChatMessage], *, thinking_enabled: bool) -> list[dict]:
        return [message.model_dump() for message in messages]

    async def _post_chat(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        headers: dict[str, str],
        thinking_enabled: bool,
    ) -> dict | None:
        try:
            async with httpx.AsyncClient(
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
        async with httpx.AsyncClient(
            base_url=self._settings.nim_base_url,
            timeout=60.0,
        ) as client:
            async with client.stream(
                "POST",
                "/chat/completions",
                headers=headers,
                json=self._build_payload(messages, model, thinking_enabled=thinking_enabled, stream=True),
            ) as response:
                response.raise_for_status()

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
                            clean_delta, in_thinking = self._strip_thinking_blocks(delta, in_thinking)
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
            "messages": self._build_messages(messages, thinking_enabled=thinking_enabled),
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

    def _extract_inline_thinking(self, text: str) -> str | None:
        lowered = text.lower()
        start = lowered.find("<think>")
        start_alt = lowered.find("<thinking>")
        if start == -1 and start_alt == -1:
            return None

        if start_alt == -1 or (start != -1 and start < start_alt):
            open_tag = "<think>"
            close_tag = "</think>"
            start_index = start
        else:
            open_tag = "<thinking>"
            close_tag = "</thinking>"
            start_index = start_alt

        end = lowered.find(close_tag, start_index + len(open_tag))
        if end == -1:
            return None

        return text[start_index + len(open_tag) : end].strip() or None

    def _strip_thinking_blocks(self, text: str, in_thinking: bool) -> tuple[str, bool]:
        output: list[str] = []
        remaining = text

        while remaining:
            if in_thinking:
                end = remaining.find("</think>")
                end_alt = remaining.find("</thinking>")
                if end == -1 and end_alt == -1:
                    return "".join(output), True
                if end_alt == -1 or (end != -1 and end < end_alt):
                    remaining = remaining[end + len("</think>") :]
                else:
                    remaining = remaining[end_alt + len("</thinking>") :]
                in_thinking = False
                continue

            start = remaining.find("<think>")
            start_alt = remaining.find("<thinking>")
            if start == -1 and start_alt == -1:
                output.append(remaining)
                return "".join(output), False

            if start_alt == -1 or (start != -1 and start < start_alt):
                output.append(remaining[:start])
                remaining = remaining[start + len("<think>") :]
            else:
                output.append(remaining[:start_alt])
                remaining = remaining[start_alt + len("<thinking>") :]
            in_thinking = True

        return "".join(output), in_thinking

    def _thinking_enabled(self) -> bool:
        return bool(getattr(self._settings, "chat_thinking_enabled", False))

    def _should_retry_without_thinking(self, exc: httpx.HTTPStatusError) -> bool:
        if not self._thinking_enabled():
            return False
        status_code = getattr(exc.response, "status_code", None)
        if status_code not in {400, 404, 422}:
            return False
        detail = " ".join(
            part
            for part in [
                str(exc),
                getattr(exc.response, "text", ""),
                getattr(exc.response, "reason_phrase", ""),
            ]
            if part
        ).lower()
        return any(token in detail for token in ("detailed thinking", "thinking", "reasoning", "think"))
