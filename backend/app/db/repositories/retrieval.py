from __future__ import annotations

from uuid import UUID

from app.db.qdrant import QdrantManager
from app.models.schemas import RetrievedChunk


class RetrievalRepository:
    def __init__(self, qdrant: QdrantManager) -> None:
        self._qdrant = qdrant

    async def search_similar_chunks(
        self,
        embedding: list[float],
        limit: int,
        similarity_threshold: float,
        embedding_provider: str,
        embedding_model: str,
        embedding_profile: str,
        embedding_dimension: int,
    ) -> list[RetrievedChunk]:
        collection_name = await self._qdrant.ensure_collection(embedding_dimension)
        query_filter = self._profile_filter(embedding_profile, embedding_provider, embedding_model)

        result = await self._qdrant.client.query_points(
            collection_name=collection_name,
            query=embedding,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
            score_threshold=similarity_threshold,
        )
        return [self._point_to_retrieved_chunk(point) for point in result.points]

    async def search_keyword_chunks(
        self,
        query_text: str,
        limit: int,
        embedding_provider: str,
        embedding_model: str,
        embedding_profile: str,
        embedding_dimension: int,
    ) -> list[RetrievedChunk]:
        collection_name = await self._qdrant.ensure_collection(embedding_dimension)
        query_filter = self._profile_filter(embedding_profile, embedding_provider, embedding_model)
        points, _ = await self._qdrant.client.scroll(
            collection_name=collection_name,
            scroll_filter=query_filter,
            with_payload=True,
            with_vectors=False,
            limit=1000,
        )

        normalized_query = self._normalize_query_text(query_text)
        search_terms = [term for term in normalized_query.lower().split(" ") if term]

        scored: list[RetrievedChunk] = []
        for point in points:
            payload = point.payload or {}
            text_blob = f"{payload.get('content', '')} {payload.get('title', '')}".lower()
            if not normalized_query:
                continue
            if normalized_query.lower() not in text_blob and not any(term in text_blob for term in search_terms):
                continue

            score = self._keyword_score(normalized_query, text_blob)
            scored.append(self._point_to_retrieved_chunk(point, similarity_score=score))

        scored.sort(key=lambda item: item.similarity_score, reverse=True)
        return scored[:limit]

    async def search_best_available_chunks(
        self,
        embedding: list[float],
        limit: int,
        embedding_provider: str,
        embedding_model: str,
        embedding_profile: str,
        embedding_dimension: int,
    ) -> list[RetrievedChunk]:
        collection_name = await self._qdrant.ensure_collection(embedding_dimension)
        query_filter = self._profile_filter(embedding_profile, embedding_provider, embedding_model)

        result = await self._qdrant.client.query_points(
            collection_name=collection_name,
            query=embedding,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        return [self._point_to_retrieved_chunk(point) for point in result.points]

    def _profile_filter(
        self,
        embedding_profile: str,
        embedding_provider: str,
        embedding_model: str,
    ) -> models.Filter:
        from qdrant_client.http import models

        return models.Filter(
            must=[
                models.FieldCondition(
                    key="embedding_profile",
                    match=models.MatchValue(value=embedding_profile),
                ),
                models.FieldCondition(
                    key="embedding_provider",
                    match=models.MatchValue(value=embedding_provider),
                ),
                models.FieldCondition(
                    key="embedding_model",
                    match=models.MatchValue(value=embedding_model),
                ),
            ]
        )

    def _point_to_retrieved_chunk(
        self,
        point,
        similarity_score: float | None = None,
    ) -> RetrievedChunk:
        payload = point.payload or {}
        score = similarity_score if similarity_score is not None else float(point.score)
        return RetrievedChunk(
            chunk_id=UUID(str(point.id)),
            document_id=UUID(str(payload["document_id"])),
            title=str(payload.get("title", "")),
            url=payload.get("url"),
            source_type=str(payload.get("source_type", "text")),
            content=str(payload.get("content", "")),
            metadata=dict(payload.get("metadata") or {}),
            similarity_score=score,
        )

    def _keyword_score(self, query_text: str, text_blob: str) -> float:
        query_terms = [term for term in query_text.lower().split(" ") if term]
        if not query_terms:
            return 0.0

        matches = sum(1 for term in query_terms if term in text_blob)
        return matches / len(query_terms)

    def _normalize_query_text(self, query_text: str) -> str:
        return " ".join(query_text.strip().split())
