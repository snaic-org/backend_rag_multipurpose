from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace
from uuid import uuid4

redis_module = types.ModuleType("redis")
redis_asyncio_module = types.ModuleType("redis.asyncio")
redis_asyncio_module.Redis = object
redis_module.asyncio = redis_asyncio_module
sys.modules.setdefault("redis", redis_module)
sys.modules.setdefault("redis.asyncio", redis_asyncio_module)

psycopg_pool_module = types.ModuleType("psycopg_pool")
psycopg_pool_module.AsyncConnectionPool = object
sys.modules.setdefault("psycopg_pool", psycopg_pool_module)

from app.core.config import Settings
from app.models.schemas import EmbeddingSelection, RetrievedChunk
from app.services.query_planner import QueryPlannerService
from app.services.retrieval import RetrievalService


class _FakeCache:
    def make_key(self, *_args, **_kwargs) -> str:
        return "retrieval:test"

    async def get_json(self, _key: str):
        return None

    async def set_json(self, _key: str, _payload) -> None:
        return None


class _FakeRepository:
    def __init__(self, semantic_map: dict[float, list[RetrievedChunk]]) -> None:
        self.semantic_map = semantic_map
        self.semantic_calls: list[float] = []

    async def search_similar_chunks(self, *, embedding: list[float], limit: int, **_kwargs) -> list[RetrievedChunk]:
        self.semantic_calls.append(embedding[0])
        return self.semantic_map.get(embedding[0], [])[:limit]

    async def search_keyword_chunks(self, **_kwargs) -> list[RetrievedChunk]:
        return []

    async def search_best_available_chunks(self, **_kwargs) -> list[RetrievedChunk]:
        return []


class _FakeRerankService:
    async def rerank(self, _query_text: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        return chunks


def _chunk(label: str, document_suffix: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4() if document_suffix == "unique" else uuid4(),
        title=label,
        url=None,
        source_type="text",
        content=f"content-{label}",
        metadata={},
        similarity_score=0.9,
    )


def _chunk_with_doc(label: str, document_id: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4() if document_id == "new" else uuid4(),
        title=label,
        url=None,
        source_type="text",
        content=f"content-{label}",
        metadata={},
        similarity_score=0.9,
    )


def test_query_planner_builds_clause_variants_for_multi_source_question() -> None:
    settings = Settings(
        retrieval_multi_query_enabled=True,
        retrieval_multi_query_max_queries=3,
    )
    planner = QueryPlannerService(settings)

    query = (
        "Does the article from Wired suggest that Sony headphones do not offer the best value in their class "
        "during the Walmart Cyber Monday Deals, while the article from Music Business Worldwide indicates that "
        "artists are seeking deals that offer more control and better economics?"
    )

    queries = planner.build_queries(query)

    assert queries[0] == " ".join(query.split())
    assert len(queries) >= 2
    assert any("Wired" in candidate for candidate in queries[1:])
    assert any("Music Business Worldwide" in candidate for candidate in queries[1:])


def test_retrieval_service_uses_multi_query_union_and_source_diversity() -> None:
    primary_doc = uuid4()
    second_doc = uuid4()
    third_doc = uuid4()

    primary_first = RetrievedChunk(
        chunk_id=uuid4(),
        document_id=primary_doc,
        title="primary-a",
        url=None,
        source_type="text",
        content="primary-a",
        metadata={},
        similarity_score=0.98,
    )
    primary_second = RetrievedChunk(
        chunk_id=uuid4(),
        document_id=primary_doc,
        title="primary-b",
        url=None,
        source_type="text",
        content="primary-b",
        metadata={},
        similarity_score=0.96,
    )
    second_source = RetrievedChunk(
        chunk_id=uuid4(),
        document_id=second_doc,
        title="secondary",
        url=None,
        source_type="text",
        content="secondary",
        metadata={},
        similarity_score=0.95,
    )
    third_source = RetrievedChunk(
        chunk_id=uuid4(),
        document_id=third_doc,
        title="tertiary",
        url=None,
        source_type="text",
        content="tertiary",
        metadata={},
        similarity_score=0.94,
    )

    semantic_map = {
        1.0: [primary_first, primary_second],
        2.0: [second_source],
        3.0: [third_source],
    }

    service = object.__new__(RetrievalService)
    service._settings = SimpleNamespace(
        similarity_threshold=0.35,
        rerank_enabled=True,
        rerank_invoke_url="",
        rerank_model="nvidia/llama-nemotron-rerank-1b-v2",
        rerank_max_candidates=6,
        rerank_min_candidates=2,
        retrieval_multi_query_enabled=True,
        retrieval_multi_query_max_queries=3,
        retrieval_source_diversity_enabled=True,
        retrieval_source_diversity_min_sources=3,
    )
    service._cache = _FakeCache()
    service._repository = _FakeRepository(semantic_map)
    service._rerank_service = _FakeRerankService()

    selection = EmbeddingSelection(
        profile_name="nim_nemotron_2048",
        provider="nim",
        model="nvidia/llama-nemotron-embed-1b-v2",
        dimension=2048,
    )

    results = asyncio.run(
        service.retrieve(
            query_text="main question",
            query_embedding=[1.0],
            query_variants=["main question", "sub query one", "sub query two"],
            query_variant_embeddings=[[1.0], [2.0], [3.0]],
            selection=selection,
            top_k=3,
        )
    )

    assert service._repository.semantic_calls == [1.0, 2.0, 3.0]
    assert [chunk.title for chunk in results] == ["primary-a", "secondary", "tertiary"]
