from __future__ import annotations

from urllib.parse import urlparse

import httpx

from app.core.config import Settings
from app.models.schemas import RetrievedChunk


class RerankService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def rerank(self, query_text: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        if not self._settings.rerank_enabled:
            return chunks
        if len(chunks) < self._settings.rerank_min_candidates:
            return chunks
        if not self._settings.rerank_invoke_url.strip():
            return chunks

        payload = {
            "model": self._settings.rerank_model,
            "query": {"text": query_text},
            "passages": [
                {"text": f"{chunk.title}\n\n{chunk.content}".strip()}
                for chunk in chunks
            ],
            "truncate": "END",
        }

        headers = {"Content-Type": "application/json"}
        if self._settings.nim_api_key:
            headers["Authorization"] = f"Bearer {self._settings.nim_api_key}"
        elif self._requires_api_key():
            raise ValueError("NIM_API_KEY is required for the configured reranker")

        async with httpx.AsyncClient(
            timeout=30.0,
        ) as client:
            response = await client.post(
                self._settings.rerank_invoke_url,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        ordered_indexes = self._extract_order(data, len(chunks))
        reranked = [chunks[index] for index in ordered_indexes if 0 <= index < len(chunks)]
        return reranked or chunks

    def _extract_order(self, data: object, candidate_count: int) -> list[int]:
        ranked_items: list[tuple[int, float]] = []

        for item in self._iter_rankings(data):
            index = self._coerce_index(item)
            score = self._coerce_score(item)
            if index is None or index < 0 or index >= candidate_count:
                continue
            ranked_items.append((index, score))

        if ranked_items:
            ranked_items.sort(key=lambda item: item[1], reverse=True)
            return [index for index, _ in ranked_items]

        return list(range(candidate_count))

    def _iter_rankings(self, data: object) -> list[object]:
        if isinstance(data, dict):
            for key in ("rankings", "results", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        if isinstance(data, list):
            return data
        return []

    def _coerce_index(self, item: object) -> int | None:
        if not isinstance(item, dict):
            return None
        for key in ("index", "passage_index", "rank", "id"):
            value = item.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return None

    def _coerce_score(self, item: object) -> float:
        if not isinstance(item, dict):
            return 0.0
        for key in ("relevance_score", "score", "logit", "rank_score"):
            value = item.get(key)
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value)
                except ValueError:
                    continue
        return 0.0

    def _requires_api_key(self) -> bool:
        host = urlparse(self._settings.rerank_invoke_url).netloc.lower()
        return "api.openai.com" in host
