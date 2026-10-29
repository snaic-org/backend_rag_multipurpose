import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from app.models.schemas import (
    AuthenticatedUser,
    CmsIngestRequest,
    IngestFileResult,
    NormalizedDocument,
    ParsedFile,
)
from app.services.chunking import ChunkingService
from app.services.ingest_service import IngestService

USER = AuthenticatedUser(id=uuid4(), username="admin", is_admin=True, auth_type="bearer")


class FakeDocumentRepository:
    def __init__(self) -> None:
        self.documents: dict[UUID, SimpleNamespace] = {}

    def add(self, **fields) -> SimpleNamespace:
        doc = SimpleNamespace(
            id=uuid4(),
            content_hash=fields.pop("content_hash", None),
            embedding_provider=fields.pop("embedding_provider", "nim"),
            embedding_model=fields.pop("embedding_model", "nvidia/nemotron-3-embed-1b"),
            **fields,
        )
        self.documents[doc.id] = doc
        return doc

    async def get_by_content_hash(self, content_hash, embedding_provider, embedding_model):
        for doc in self.documents.values():
            if (doc.content_hash, doc.embedding_provider, doc.embedding_model) == (
                content_hash,
                embedding_provider,
                embedding_model,
            ):
                return doc
        return None

    async def create_or_get_by_content_hash(self, document, embedding_provider, embedding_model, content_hash, created_by):
        existing = await self.get_by_content_hash(content_hash, embedding_provider, embedding_model)
        if existing is not None:
            return existing, False
        doc = self.add(
            content_hash=content_hash,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            url=document.url,
            source_type=document.source_type,
            content_type=document.metadata.get("contentful_content_type"),
        )
        return doc, True

    async def get_by_id(self, document_id):
        return self.documents.get(document_id)

    async def delete_by_id(self, document_id) -> bool:
        return self.documents.pop(document_id, None) is not None

    async def list_ids_by_url(self, url, source_type):
        return [d.id for d in self.documents.values() if d.url == url and d.source_type == source_type]

    async def list_ids_by_source_type(self, source_type):
        return [d.id for d in self.documents.values() if d.source_type == source_type]

    async def list_ids_by_content_type(self, content_type):
        return [d.id for d in self.documents.values() if d.content_type == content_type]


class FakeChunkRepository:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.deleted: list[tuple[UUID, int]] = []

    async def bulk_create(self, document_id, chunks, **kwargs):
        if self.fail:
            raise RuntimeError("qdrant unavailable")
        return [SimpleNamespace() for _ in chunks]

    async def delete_for_document(self, document_id, embedding_dimension):
        self.deleted.append((document_id, embedding_dimension))
        return 1


class FakeEmbeddingService:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def embed_texts(self, texts, **kwargs):
        if self.fail:
            raise RuntimeError("embedding provider down")
        return None, [[0.0, 0.0] for _ in texts]


def _service(*, embedding_fails: bool = False, upsert_fails: bool = False) -> IngestService:
    settings = Settings()
    service = object.__new__(IngestService)
    service._settings = settings
    service._document_repository = FakeDocumentRepository()
    service._chunk_repository = FakeChunkRepository(fail=upsert_fails)
    service._chunking_service = ChunkingService(settings)
    service._embedding_service = FakeEmbeddingService(fail=embedding_fails)
    return service


def _persist(service: IngestService, content: str, *, force: bool = False, url: str | None = None) -> dict:
    document = NormalizedDocument(title="KB", source_type="text", content=content, metadata={}, url=url)
    return asyncio.run(
        service._persist_documents(
            parsed_file=ParsedFile(filename="kb.txt", detected_type="txt", documents=[document]),
            embedding_provider="nim",
            embedding_model="nvidia/nemotron-3-embed-1b",
            embedding_profile="nim_nemotron_2048",
            embedding_dimension=2048,
            force_reingest=force,
        )
    )


def test_embedding_failure_leaves_no_document_so_retry_succeeds() -> None:
    service = _service(embedding_fails=True)

    with pytest.raises(RuntimeError):
        _persist(service, "SNAIC overview")
    assert service._document_repository.documents == {}

    service._embedding_service.fail = False
    result = _persist(service, "SNAIC overview")
    assert result["documents_inserted"] == 1
    assert result["results"][0].deduplicated is False


def test_chunk_upsert_failure_removes_the_new_document_row() -> None:
    service = _service(upsert_fails=True)

    with pytest.raises(RuntimeError):
        _persist(service, "SNAIC overview")

    assert service._document_repository.documents == {}


def test_force_reingest_keeps_old_copy_when_embedding_fails() -> None:
    service = _service()
    original_id = _persist(service, "SNAIC overview")["results"][0].document_id
    service._embedding_service.fail = True

    with pytest.raises(RuntimeError):
        _persist(service, "SNAIC overview", force=True)

    assert set(service._document_repository.documents) == {original_id}
    assert service._chunk_repository.deleted == []


def test_delete_document_uses_the_documents_own_embedding_dimension() -> None:
    service = _service()
    doc = service._document_repository.add(
        embedding_provider="openai", embedding_model="text-embedding-3-small", url=None, source_type="text"
    )

    assert asyncio.run(service.delete_document(doc.id)) is True
    assert service._chunk_repository.deleted == [(doc.id, 1536)]


def test_website_reingest_replaces_changed_page_by_url(monkeypatch) -> None:
    service = _service()
    old = service._document_repository.add(content_hash="old", url="https://www.snaic.net/a", source_type="website")
    other = service._document_repository.add(content_hash="x", url="https://www.snaic.net/b", source_type="website")

    async def fake_get(self, url, headers):
        request = __import__("httpx").Request("GET", url)
        return __import__("httpx").Response(200, request=request, content=b"<html></html>")

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    class Parser:
        async def parse(self, filename, content, mime_type, shared_metadata):
            doc = NormalizedDocument(title="A", source_type="website", content="new page text", metadata={})
            return ParsedFile(filename=filename, detected_type="website", documents=[doc])

    selection = SimpleNamespace(provider="nim", model="nvidia/nemotron-3-embed-1b", profile_name="p", dimension=2048)
    result = asyncio.run(
        service._ingest_single_url(
            url="https://www.snaic.net/a", force_reingest=True, selection=selection, parser=Parser(), current_user=USER
        )
    )

    new_id = result["results"][0].document_id
    remaining = set(service._document_repository.documents)
    assert old.id not in remaining
    assert other.id in remaining
    assert new_id in remaining


def _cms_service(monkeypatch, failing_types: set[str]) -> IngestService:
    service = _service()
    service._settings = Settings(contentful_space_id="space", contentful_delivery_token="token")
    service._current_selection_value = SimpleNamespace(
        provider="nim", model="nvidia/nemotron-3-embed-1b", profile_name="p", dimension=2048
    )

    async def get_profile_name():
        return "nim_nemotron_2048"

    service._model_selection_service = SimpleNamespace(get_embedding_profile_name=get_profile_name)
    service._embedding_service.resolve_selection = lambda *args, **kwargs: service._current_selection_value

    async def fetch(client, space, env, token, content_type):
        if content_type in failing_types:
            raise RuntimeError("contentful down")
        return [{"sys": {"id": content_type}}], {}

    monkeypatch.setattr(service, "_fetch_contentful_entries", fetch)
    monkeypatch.setattr(
        service,
        "_contentful_entry_to_document",
        lambda entry, includes, cfg, base, user: NormalizedDocument(
            title=cfg["content_type"],
            source_type="cms",
            content=f"fresh {cfg['content_type']}",
            metadata={"contentful_content_type": cfg["content_type"]},
        ),
    )
    monkeypatch.setattr(service, "_build_cms_roster_document", lambda *args: None)
    return service


def test_cms_full_refresh_keeps_content_types_whose_fetch_failed(monkeypatch) -> None:
    service = _cms_service(monkeypatch, failing_types={"team"})
    repo = service._document_repository
    stale_news = repo.add(content_hash="old-news", source_type="cms", content_type="jointCentreNews", url=None)
    team = repo.add(content_hash="team", source_type="cms", content_type="team", url=None)

    response = asyncio.run(service.ingest_cms(CmsIngestRequest(), current_user=USER))

    remaining = set(repo.documents)
    assert stale_news.id not in remaining
    assert team.id in remaining
    assert response.failed == 1


def test_cms_refresh_deletes_nothing_when_every_fetch_fails(monkeypatch) -> None:
    service = _cms_service(monkeypatch, failing_types={"team", "jointCentreNews", "jointCentreProjectNew"})
    repo = service._document_repository
    kept = repo.add(content_hash="news", source_type="cms", content_type="jointCentreNews", url=None)

    asyncio.run(service.ingest_cms(CmsIngestRequest(), current_user=USER))

    assert kept.id in repo.documents
