from typing import Any

import httpx
from pathlib import Path


class BackendRagClient:
    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._base_url = base_url
        self._username = username
        self._password = password
        self._client = httpx.AsyncClient(
            base_url=self._base_url, timeout=timeout_seconds)
        self._headers: dict[str, str] = {}

    async def __aenter__(self) -> "BackendRagClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._client.aclose()

    async def login(self) -> None:
        response = await self._client.post("/auth/token",
            json={"username": self._username, "password": self._password},
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise RuntimeError(
                f"Login succeeded but no access token was returned: {payload}")
        self._headers = {"Authorization": f"Bearer {token}"}

    async def reset(self) -> dict[str, Any]:
        response = await self._client.delete("/admin/reset", headers=self._headers)
        response.raise_for_status()
        return response.json()

    async def health(self) -> dict[str, Any]:
        response = await self._client.get("/health")
        response.raise_for_status()
        return response.json()

    async def get_model_selection(self) -> dict[str, Any]:
        response = await self._client.get("/admin/model-selection", headers=self._headers)
        response.raise_for_status()
        return response.json()

    async def update_model_selection(
        self,
        *,
        generation_profile: str,
        embedding_profile: str,
    ) -> dict[str, Any]:
        response = await self._client.put(
            "/admin/model-selection",
            headers=self._headers,
            json={
                "generation_profile": generation_profile,
                "embedding_profile": embedding_profile,
            },
        )
        response.raise_for_status()
        return response.json()

    async def get_system_prompt(self) -> dict[str, Any]:
        response = await self._client.get("/admin/system-prompt", headers=self._headers)
        response.raise_for_status()
        return response.json()

    async def update_system_prompt(self, system_prompt: str) -> dict[str, Any]:
        response = await self._client.put(
            "/admin/system-prompt",
            headers=self._headers,
            json={"system_prompt": system_prompt},
        )
        response.raise_for_status()
        return response.json()

    async def ingest_text_items(
        self,
        *,
        items: list[dict[str, Any]],
        force_reingest: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "items": items,
            "force_reingest": force_reingest,
        }

        response = await self._client.post("/ingest/text", headers=self._headers, json=payload)
        response.raise_for_status()
        return response.json()

    async def ingest_files(
        self,
        *,
        file_paths: list[Path],
        force_reingest: bool,
    ) -> dict[str, Any]:
        files = []
        handles = []
        try:
            for path in file_paths:
                handle = path.open("rb")
                handles.append(handle)
                files.append(
                    (
                        "files",
                        (
                            path.name,
                            handle,
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        ),
                    )
                )

            response = await self._client.post(
                "/ingest/files",
                headers=self._headers,
                files=files,
                data={"force_reingest": str(force_reingest).lower()},
            )
            response.raise_for_status()
            return response.json()
        finally:
            for handle in handles:
                handle.close()

    async def chat(
        self,
        *,
        message: str,
        top_k: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "message": message,
            "debug": True,
            "top_k": top_k,
        }

        response = await self._client.post("/chat", headers=self._headers, json=payload)
        response.raise_for_status()
        return response.json()
