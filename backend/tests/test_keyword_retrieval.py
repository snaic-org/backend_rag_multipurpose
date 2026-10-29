import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.db.repositories.retrieval import RetrievalRepository


def _point(content: str, title: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        id=str(uuid4()),
        score=0.0,
        payload={"document_id": str(uuid4()), "content": content, "title": title, "source_type": "text"},
    )


class FakeClient:
    def __init__(self, points) -> None:
        self.points = points
        self.scroll_filters = []

    async def scroll(self, collection_name, scroll_filter, with_payload, with_vectors, limit):
        self.scroll_filters.append(scroll_filter)
        return self.points, None


class FakeQdrant:
    def __init__(self, points, text_index: bool) -> None:
        self.client = FakeClient(points)
        self._text_index = text_index

    async def ensure_collection(self, dimension: int) -> str:
        return f"rag_chunks_{dimension}"

    def has_text_index(self, collection_name: str) -> bool:
        return self._text_index


PROFILE = {
    "embedding_provider": "nim",
    "embedding_model": "nvidia/nemotron-3-embed-1b",
    "embedding_profile": "nim_nemotron_2048",
    "embedding_dimension": 2048,
}


def test_one_scroll_ranks_every_query_variant() -> None:
    points = [
        _point("SNAIC director is Alan Tan"),
        _point("SNAIC projects in healthcare"),
        _point("Unrelated page about parking"),
    ]
    qdrant = FakeQdrant(points, text_index=False)
    repository = RetrievalRepository(qdrant)

    ranked = asyncio.run(
        repository.search_keyword_chunks(
            query_texts=["who is the SNAIC director", "healthcare projects"], limit=2, **PROFILE
        )
    )

    assert len(qdrant.client.scroll_filters) == 1
    assert ranked[0][0].content == "SNAIC director is Alan Tan"
    assert ranked[1][0].content == "SNAIC projects in healthcare"
    assert qdrant.client.scroll_filters[0].should is None  # no text index: unfiltered, as before


def test_text_index_filters_candidates_by_significant_terms() -> None:
    qdrant = FakeQdrant([_point("SNAIC director is Alan Tan")], text_index=True)
    repository = RetrievalRepository(qdrant)

    asyncio.run(repository.search_keyword_chunks(query_texts=["Who is the SNAIC director?"], limit=5, **PROFILE))

    should = qdrant.client.scroll_filters[0].should
    assert sorted(condition.match.text for condition in should) == ["director", "snaic"]
    assert all(condition.key == "content" for condition in should)


def test_blank_queries_skip_qdrant() -> None:
    qdrant = FakeQdrant([], text_index=True)
    repository = RetrievalRepository(qdrant)

    assert asyncio.run(repository.search_keyword_chunks(query_texts=["   "], limit=5, **PROFILE)) == [[]]
    assert qdrant.client.scroll_filters == []
