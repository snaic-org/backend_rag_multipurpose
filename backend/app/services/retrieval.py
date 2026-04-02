from app.core.config import Settings
from app.db.qdrant import QdrantManager
from app.db.redis import RedisManager
from app.db.repositories.retrieval import RetrievalRepository
from app.models.schemas import EmbeddingSelection, RetrievedChunk
from app.services.cache_service import CacheService
from app.services.rerank import RerankService


class RetrievalService:
    _RETRIEVAL_STRATEGY_VERSION = "hybrid_keyword_union_v2"

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
        query_variants: list[str] | None,
        query_variant_embeddings: list[list[float]] | None,
        selection: EmbeddingSelection,
        top_k: int,
    ) -> list[RetrievedChunk]:
        candidate_limit = self._candidate_limit(top_k)
        query_plans = self._build_query_plans(
            query_text,
            query_embedding,
            query_variants,
            query_variant_embeddings,
        )
        cache_key = self._cache.make_key(
            "retrieval",
            {
                "embedding_provider": selection.provider,
                "embedding_model": selection.model,
                "top_k": top_k,
                "candidate_limit": candidate_limit,
                "query_text": query_text,
                "query_plans": query_plans,
                "similarity_threshold": self._settings.similarity_threshold,
                "rerank_enabled": self._settings.rerank_enabled,
                "rerank_invoke_url": self._settings.rerank_invoke_url,
                "rerank_model": self._settings.rerank_model,
                "rerank_max_candidates": self._settings.rerank_max_candidates,
                "rerank_min_candidates": self._settings.rerank_min_candidates,
                "retrieval_multi_query_enabled": self._settings.retrieval_multi_query_enabled,
                "retrieval_multi_query_max_queries": self._settings.retrieval_multi_query_max_queries,
                "retrieval_source_diversity_enabled": self._settings.retrieval_source_diversity_enabled,
                "retrieval_source_diversity_min_sources": self._settings.retrieval_source_diversity_min_sources,
                "retrieval_strategy_version": self._RETRIEVAL_STRATEGY_VERSION,
            },
        )
        cached = await self._cache.get_json(cache_key)
        if isinstance(cached, list):
            return [RetrievedChunk.model_validate(item) for item in cached]

        merged_results: list[RetrievedChunk] = []
        semantic_limit = self._semantic_limit(top_k, candidate_limit, len(query_plans))
        keyword_limit = self._keyword_limit(top_k, candidate_limit, len(query_plans))
        rerank_input_limit = candidate_limit + (keyword_limit * len(query_plans))

        for planned_query_text, planned_query_embedding in query_plans:
            semantic_results = await self._repository.search_similar_chunks(
                embedding=planned_query_embedding,
                limit=semantic_limit,
                similarity_threshold=self._settings.similarity_threshold,
                embedding_provider=selection.provider,
                embedding_model=selection.model,
                embedding_profile=selection.profile_name,
                embedding_dimension=selection.dimension,
            )
            merged_results = self._dedupe_chunks([*merged_results, *semantic_results])

        for planned_query_text, _ in query_plans:
            if not planned_query_text.strip():
                continue
            keyword_results = await self._repository.search_keyword_chunks(
                query_text=planned_query_text,
                limit=keyword_limit,
                embedding_provider=selection.provider,
                embedding_model=selection.model,
                embedding_profile=selection.profile_name,
                embedding_dimension=selection.dimension,
            )
            merged_results = self._dedupe_chunks([*merged_results, *keyword_results])

        if len(merged_results) < candidate_limit:
            best_available_results = await self._repository.search_best_available_chunks(
                embedding=query_embedding,
                limit=candidate_limit,
                embedding_provider=selection.provider,
                embedding_model=selection.model,
                embedding_profile=selection.profile_name,
                embedding_dimension=selection.dimension,
            )
            merged_results = self._dedupe_chunks([*merged_results, *best_available_results])

        results = await self._rerank_service.rerank(query_text, merged_results[:rerank_input_limit])
        results = self._apply_source_diversity(results, top_k)
        await self._cache.set_json(cache_key, [item.model_dump(mode="json") for item in results])
        return results

    def _candidate_limit(self, top_k: int) -> int:
        if not self._settings.rerank_enabled:
            return top_k
        return max(top_k, self._settings.rerank_max_candidates)

    def _keyword_limit(self, top_k: int, candidate_limit: int, query_count: int) -> int:
        if not self._settings.rerank_enabled:
            return top_k
        return max(2, min(candidate_limit, (candidate_limit + max(1, query_count) - 1) // max(1, query_count)))

    def _semantic_limit(self, top_k: int, candidate_limit: int, query_count: int) -> int:
        if query_count <= 1:
            return candidate_limit
        return max(top_k, min(candidate_limit, (candidate_limit + query_count - 1) // query_count))

    def _dedupe_chunks(self, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        unique: list[RetrievedChunk] = []
        seen_chunk_ids: set[str] = set()
        for chunk in chunks:
            chunk_id = str(chunk.chunk_id)
            if chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk_id)
            unique.append(chunk)
        return unique

    def _build_query_plans(
        self,
        query_text: str,
        query_embedding: list[float],
        query_variants: list[str] | None,
        query_variant_embeddings: list[list[float]] | None,
    ) -> list[tuple[str, list[float]]]:
        if not self._settings.retrieval_multi_query_enabled:
            return [(query_text, query_embedding)]

        planned_queries = query_variants or [query_text]
        planned_embeddings = query_variant_embeddings or [query_embedding]
        query_plans: list[tuple[str, list[float]]] = []
        seen_queries: set[str] = set()

        for planned_query_text, planned_query_embedding in zip(planned_queries, planned_embeddings, strict=False):
            normalized_query = " ".join(planned_query_text.strip().split())
            if not normalized_query or normalized_query.lower() in seen_queries:
                continue
            seen_queries.add(normalized_query.lower())
            query_plans.append((normalized_query, planned_query_embedding))

        if not query_plans:
            return [(query_text, query_embedding)]

        return query_plans

    def _apply_source_diversity(self, chunks: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
        if not self._settings.retrieval_source_diversity_enabled or len(chunks) <= 1:
            return chunks[:top_k]

        distinct_target = min(top_k, max(1, self._settings.retrieval_source_diversity_min_sources))
        diversified: list[RetrievedChunk] = []
        seen_document_ids: set[str] = set()

        for chunk in chunks:
            document_id = str(chunk.document_id)
            if document_id in seen_document_ids:
                continue
            diversified.append(chunk)
            seen_document_ids.add(document_id)
            if len(diversified) >= distinct_target:
                break

        if not diversified:
            return chunks[:top_k]

        selected_chunk_ids = {str(chunk.chunk_id) for chunk in diversified}
        for chunk in chunks:
            if len(diversified) >= top_k:
                break
            chunk_id = str(chunk.chunk_id)
            if chunk_id in selected_chunk_ids:
                continue
            diversified.append(chunk)
            selected_chunk_ids.add(chunk_id)

        return diversified[:top_k]
