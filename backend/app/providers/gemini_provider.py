import json
from typing import AsyncIterator

import httpx

from app.core.config import Settings
from app.core.config import CHAT_MAX_RESPONSE_TOKENS, CHAT_TOP_P
from app.models.schemas import ChatCompletionResult, ChatMessage, ProviderHealth
from app.providers.base import ProviderAdapter
from app.providers.common import (
    raise_for_status,
    should_retry_without_thinking,
    is_thinking_enabled,
)


_THINKING_ERROR_TOKENS = ("thinkingconfig", "thoughts", "thinking", "reasoning", "think")


class GeminiProvider(ProviderAdapter):
    provider_name = "gemini"
    capabilities = ["chat", "embeddings_interface_ready"]

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def healthcheck(self) -> ProviderHealth:
        configured = bool(self._settings.gemini_api_key)

        if not configured:
            return ProviderHealth(
                ok=False,
                detail="missing GEMINI_API_KEY",
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
        if not self._settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required for Gemini chat")

        contents = self._build_contents(messages)

        thinking_enabled = is_thinking_enabled(self._settings)
        data = await self._generate_content(contents, model, thinking_enabled=thinking_enabled)
        if data is None and thinking_enabled:
            data = await self._generate_content(contents, model, thinking_enabled=False)
        if data is None:
            raise ValueError("Gemini chat request failed")

        text, thinking = self._extract_text_and_thinking(data)
        return ChatCompletionResult(text=text, thinking=thinking, provider=self.provider_name, model=model)

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        model: str,
    ) -> AsyncIterator[str]:
        if not self._settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required for Gemini chat")

        contents = self._build_contents(messages)

        thinking_enabled = is_thinking_enabled(self._settings)
        try:
            async for text in self._stream_generate_content(contents, model, thinking_enabled=thinking_enabled):
                yield text
            return
        except httpx.HTTPStatusError as exc:
            if not self._should_retry_without_thinking(exc):
                raise

        async for text in self._stream_generate_content(contents, model, thinking_enabled=False):
            yield text

    def _build_contents(self, messages: list[ChatMessage]) -> list[dict]:
        contents: list[dict] = []
        for message in messages:
            role = "model" if message.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": message.content}]})
        return contents

    def _extract_text_and_thinking(self, data: dict) -> tuple[str, str | None]:
        candidates = data.get("candidates", [])
        if not candidates:
            return "", None

        parts = candidates[0].get("content", {}).get("parts", [])
        answer_parts: list[str] = []
        thinking_parts: list[str] = []
        for part in parts:
            text = part.get("text", "")
            if not text:
                continue
            if part.get("thought"):
                thinking_parts.append(text)
            else:
                answer_parts.append(text)

        thinking = "\n".join(thinking_parts) if thinking_parts else None
        return "".join(answer_parts), thinking

    async def _generate_content(
        self,
        contents: list[dict],
        model: str,
        *,
        thinking_enabled: bool,
    ) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": self._settings.gemini_api_key},
                    json={
                        "contents": contents,
                        "generationConfig": {
                            "temperature": self._settings.chat_temperature,
                            "topP": CHAT_TOP_P,
                            "maxOutputTokens": CHAT_MAX_RESPONSE_TOKENS,
                            "candidateCount": 1,
                            "thinkingConfig": {
                                "thinkingBudget": -1 if thinking_enabled else 0,
                                "includeThoughts": thinking_enabled,
                            },
                        },
                    },
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            if self._should_retry_without_thinking(exc):
                return None
            raise

    async def _stream_generate_content(
        self,
        contents: list[dict],
        model: str,
        *,
        thinking_enabled: bool,
    ) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent",
                headers={"x-goog-api-key": self._settings.gemini_api_key},
                params={"alt": "sse"},
                json={
                    "contents": contents,
                    "generationConfig": {
                        "temperature": self._settings.chat_temperature,
                        "topP": CHAT_TOP_P,
                        "maxOutputTokens": CHAT_MAX_RESPONSE_TOKENS,
                        "candidateCount": 1,
                        "thinkingConfig": {
                            "thinkingBudget": -1 if thinking_enabled else 0,
                            "includeThoughts": thinking_enabled,
                        },
                    },
                },
            ) as response:
                await raise_for_status(response)

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue

                    payload = json.loads(line[6:].strip())
                    candidates = payload.get("candidates", [])
                    if not candidates:
                        continue

                    parts = candidates[0].get("content", {}).get("parts", [])
                    for part in parts:
                        if part.get("thought"):
                            continue
                        text = part.get("text", "")
                        if text:
                            yield text

    def _should_retry_without_thinking(self, exc: httpx.HTTPStatusError) -> bool:
        return should_retry_without_thinking(self._settings, exc, _THINKING_ERROR_TOKENS)
