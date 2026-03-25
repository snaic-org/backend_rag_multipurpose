import json
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx

from app.core.config import Settings
from app.models.schemas import ChatCompletionResult, ChatMessage, ProviderHealth
from app.providers.base import ProviderAdapter


class NimProvider(ProviderAdapter):
    provider_name = "nim"
    capabilities = ["chat", "embeddings"]

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def healthcheck(self) -> ProviderHealth:
        enabled = self._settings.nim_enabled
        configured = bool(self._settings.nim_base_url.strip())

        if not enabled:
            return ProviderHealth(
                ok=True,
                detail="disabled",
                enabled=False,
                provider=self.provider_name,
                capabilities=self.capabilities,
                configuration_present=configured,
            )

        if not configured:
            return ProviderHealth(
                ok=False,
                detail="missing NIM_BASE_URL",
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

        headers = {"Content-Type": "application/json"}
        if self._settings.nim_api_key:
            headers["Authorization"] = f"Bearer {self._settings.nim_api_key}"
        payload = {
            "model": model,
            "messages": [message.model_dump() for message in messages],
        }

        async with httpx.AsyncClient(
            base_url=self._settings.nim_base_url,
            timeout=60.0,
        ) as client:
            response = await client.post(
                "/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        text = data["choices"][0]["message"]["content"]
        return ChatCompletionResult(text=text, provider=self.provider_name, model=model)

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        model: str,
    ) -> AsyncIterator[str]:
        if not self._settings.nim_base_url.strip():
            raise ValueError("NIM_BASE_URL is required for NIM chat")
        if self._requires_api_key() and not self._settings.nim_api_key:
            raise ValueError("NIM_API_KEY is required for NIM chat")

        headers = {"Content-Type": "application/json"}
        if self._settings.nim_api_key:
            headers["Authorization"] = f"Bearer {self._settings.nim_api_key}"
        payload = {
            "model": model,
            "messages": [message.model_dump() for message in messages],
            "stream": True,
        }

        async with httpx.AsyncClient(
            base_url=self._settings.nim_base_url,
            timeout=60.0,
        ) as client:
            async with client.stream(
                "POST",
                "/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue

                    data = line[6:].strip()
                    if data == "[DONE]":
                        break

                    parsed = json.loads(data)
                    delta = parsed["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta

    def _requires_api_key(self) -> bool:
        host = urlparse(self._settings.nim_base_url).netloc.lower()
        return "api.openai.com" in host
