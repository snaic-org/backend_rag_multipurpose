from __future__ import annotations

import asyncio

import pytest
import httpx


def test_live_api_flow(live_api_config: dict[str, str | None]) -> None:
    assert live_api_config["base_url"] is not None
    assert live_api_config["username"] is not None
    assert live_api_config["password"] is not None
    assert live_api_config["ingest_text"] is not None

    async def run_flow() -> None:
        base_url = live_api_config["base_url"]
        async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as client:
            token_response = await client.post(
                "/auth/token",
                json={
                    "username": live_api_config["username"],
                    "password": live_api_config["password"],
                },
            )
            assert token_response.status_code == 200, token_response.text
            token = token_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            ingest_payload: dict[str, object] = {
                "items": [
                        {
                            "title": live_api_config["ingest_title"],
                            "content": live_api_config["ingest_text"],
                        }
                ]
            }
            ingest_response = await client.post(
                "/ingest/text",
                headers=headers,
                json=ingest_payload,
            )
            assert ingest_response.status_code == 200, ingest_response.text

            chat_payload: dict[str, object] = {
                "message": live_api_config["chat_message"],
            }

            chat_response = await client.post("/chat", headers=headers, json=chat_payload)
            assert chat_response.status_code == 200, chat_response.text
            payload = chat_response.json()

            print("ANSWER:", payload["answer"])
            print("CITATIONS:", payload["citations"])

            assert payload["answer"].strip()
            assert "SIT Centre for AI" in payload["answer"]
            assert "NVIDIA" in payload["answer"]
            assert payload["citations"]
            assert set(payload) == {"answer", "citations", "session_id"}
            assert all(
                set(citation) == {"document_id", "chunk_id"}
                for citation in payload["citations"]
            )

    asyncio.run(run_flow())
