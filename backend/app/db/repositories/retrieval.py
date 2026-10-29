from __future__ import annotations

from uuid import UUID

from app.db.qdrant import QdrantManager
from app.models.schemas import RetrievedChunk
from app.services.text_terms import significant_terms


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
        query_texts: list[str],
        limit: int,
        embedding_provider: str,
        embedding_model: str,
        embedding_profile: str,
        embedding_dimension: int,
    ) -> list[list[RetrievedChunk]]:
        """Keyword-ranked chunks for each query text, from a single scroll.

        One scroll serves every query variant (instead of one 1000-point download
        per variant). When the collection has a full-text index, Qdrant first
        drops chunks sharing no significant word with any query.
        """
        normalized_queries = [self._normalize_query_text(text) for text in query_texts]
        if not any(normalized_queries):
            return [[] for _ in query_texts]

        collection_name = await self._qdrant.ensure_collection(embedding_dimension)
        query_filter = self._profile_filter(embedding_profile, embedding_provider, embedding_model)
        terms = sorted({term for text in normalized_queries for term in significant_terms(text)})
        if terms and self._qdrant.has_text_index(collection_name):
            from qdrant_client.http import models

            query_filter.should = [
                models.FieldCondition(key="content", match=models.MatchText(text=term)) for term in terms
            ]

        points, _ = await self._qdrant.client.scroll(
            collection_name=collection_name,
            scroll_filter=query_filter,
            with_payload=True,
            with_vectors=False,
            limit=1000,
        )
        blobs = [
            (point, f"{(point.payload or {}).get('content', '')} {(point.payload or {}).get('title', '')}".lower())
            for point in points
        ]

        ranked_per_query: list[list[RetrievedChunk]] = []
        for normalized_query in normalized_queries:
            ranked_per_query.append(self._rank_by_keywords(normalized_query, blobs, limit))
        return ranked_per_query

    def _rank_by_keywords(self, normalized_query: str, blobs, limit: int) -> list[RetrievedChunk]:
        if not normalized_query:
            return []
        search_terms = [term for term in normalized_query.lower().split(" ") if term]
        scored: list[RetrievedChunk] = []
        for point, text_blob in blobs:
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
