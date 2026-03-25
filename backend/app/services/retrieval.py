from app.core.config import Settings
from app.db.qdrant import QdrantManager
from app.db.redis import RedisManager
from app.db.repositories.retrieval import RetrievalRepository
from app.models.schemas import EmbeddingSelection, RetrievedChunk
from app.services.cache_service import CacheService
from app.services.rerank import RerankService


class RetrievalService:
    def __init__(
        self,
        settings: Settings,
        qdrant_manager: QdrantManager,
        redis_manager: RedisManager,
    ) -> None:
        self._settings = settings
        self._repository = RetrievalRepository(qdrant_manager)
        self._cache = CacheService(
            redis_manager.client,
            ttl_seconds=settings.retrieval_cache_ttl_seconds,
        )
        self._rerank_service = RerankService(settings)

    async def retrieve(
        self,
        query_text: str,
        query_embedding: list[float],
        selection: EmbeddingSelection,
        top_k: int,
    ) -> list[RetrievedChunk]:
        candidate_limit = self._candidate_limit(top_k)
        cache_key = self._cache.make_key(
            "retrieval",
            {
                "embedding_provider": selection.provider,
                "embedding_model": selection.model,
                "top_k": top_k,
                "candidate_limit": candidate_limit,
                "query_text": query_text,
                "query_embedding": query_embedding,
                "similarity_threshold": self._settings.similarity_threshold,
                "rerank_enabled": self._settings.rerank_enabled,
                "rerank_invoke_url": self._settings.rerank_invoke_url,
                "rerank_model": self._settings.rerank_model,
                "rerank_max_candidates": self._settings.rerank_max_candidates,
                "rerank_min_candidates": self._settings.rerank_min_candidates,
            },
        )
        cached = await self._cache.get_json(cache_key)
        if isinstance(cached, list):
            return [RetrievedChunk.model_validate(item) for item in cached]

        results = await self._repository.search_similar_chunks(
            embedding=query_embedding,
            limit=candidate_limit,
            similarity_threshold=self._settings.similarity_threshold,
            embedding_provider=selection.provider,
            embedding_model=selection.model,
            embedding_profile=selection.profile_name,
            embedding_dimension=selection.dimension,
        )
        if not results and query_text.strip():
            results = await self._repository.search_keyword_chunks(
                query_text=query_text,
                limit=candidate_limit,
                embedding_provider=selection.provider,
                embedding_model=selection.model,
                embedding_profile=selection.profile_name,
                embedding_dimension=selection.dimension,
            )
        if not results:
            results = await self._repository.search_best_available_chunks(
                embedding=query_embedding,
                limit=candidate_limit,
                embedding_provider=selection.provider,
                embedding_model=selection.model,
                embedding_profile=selection.profile_name,
                embedding_dimension=selection.dimension,
            )
        results = await self._rerank_service.rerank(query_text, results)
        results = results[:top_k]
        await self._cache.set_json(cache_key, [item.model_dump(mode="json") for item in results])
        return results

    def _candidate_limit(self, top_k: int) -> int:
        if not self._settings.rerank_enabled:
            return top_k
        return max(top_k, self._settings.rerank_max_candidates)
