from __future__ import annotations

import asyncio
from uuid import uuid4

from app.core.config import Settings
from app.models.schemas import RetrievedChunk
from app.services.rerank import RerankService


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _chunk(index: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        title=f"chunk-{index}",
        url=None,
        source_type="text",
        content=f"content-{index}",
        metadata={},
        similarity_score=1.0 - (index * 0.01),
    )


def test_rerank_service_preserves_unranked_tail(monkeypatch) -> None:
    settings = Settings(rerank_enabled=True, rerank_min_candidates=2)
    service = RerankService(settings)
    chunks = [_chunk(0), _chunk(1), _chunk(2), _chunk(3)]

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, headers=None, json=None):
            return _FakeResponse(
                {
                    "rankings": [
                        {"index": 2, "relevance_score": 0.91},
                        {"index": 0, "relevance_score": 0.72},
                    ]
                }
            )

    monkeypatch.setattr("app.services.rerank.httpx.AsyncClient", FakeAsyncClient)

    reranked = asyncio.run(service.rerank("test query", chunks))

    assert [chunk.title for chunk in reranked] == ["chunk-2", "chunk-0", "chunk-1", "chunk-3"]
