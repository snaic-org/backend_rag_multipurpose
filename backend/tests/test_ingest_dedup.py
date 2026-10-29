from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace
from uuid import UUID

import pytest

redis_module = types.ModuleType("redis")
redis_asyncio_module = types.ModuleType("redis.asyncio")
redis_asyncio_module.Redis = object
redis_module.asyncio = redis_asyncio_module
sys.modules.setdefault("redis", redis_module)
sys.modules.setdefault("redis.asyncio", redis_asyncio_module)

psycopg_pool_module = types.ModuleType("psycopg_pool")
psycopg_pool_module.AsyncConnectionPool = object
sys.modules.setdefault("psycopg_pool", psycopg_pool_module)

psycopg_module = types.ModuleType("psycopg")
psycopg_rows_module = types.ModuleType("psycopg.rows")
psycopg_rows_module.dict_row = object
psycopg_types_module = types.ModuleType("psycopg.types")
psycopg_types_json_module = types.ModuleType("psycopg.types.json")
psycopg_types_json_module.Jsonb = object
psycopg_module.rows = psycopg_rows_module
psycopg_module.types = psycopg_types_module
sys.modules.setdefault("psycopg", psycopg_module)
sys.modules.setdefault("psycopg.rows", psycopg_rows_module)
sys.modules.setdefault("psycopg.types", psycopg_types_module)
sys.modules.setdefault("psycopg.types.json", psycopg_types_json_module)

docx_module = types.ModuleType("docx")
docx_module.Document = object
sys.modules.setdefault("docx", docx_module)

openpyxl_module = types.ModuleType("openpyxl")
openpyxl_module.load_workbook = lambda *args, **kwargs: None
sys.modules.setdefault("openpyxl", openpyxl_module)

multipart_module = types.ModuleType("multipart")
multipart_module.__version__ = "0.0.0"
multipart_submodule = types.ModuleType("multipart.multipart")
multipart_submodule.parse_options_header = lambda *args, **kwargs: (b"", {})
multipart_module.multipart = multipart_submodule
sys.modules.setdefault("multipart", multipart_module)
sys.modules.setdefault("multipart.multipart", multipart_submodule)

from app.models.schemas import NormalizedDocument, ParsedFile
from app.services.ingest_service import IngestService


class FakeDocumentRepository:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.record = SimpleNamespace(id=UUID("11111111-1111-1111-1111-111111111111"))
        self.deleted_ids: list[UUID] = []
        self.existing_record = self.record

    async def create_or_get_by_content_hash(
        self,
        document: NormalizedDocument,
        embedding_provider: str,
        embedding_model: str,
        content_hash: str,
        created_by: str,
    ) -> tuple[SimpleNamespace, bool]:
        self.calls.append(content_hash)
        if self.existing_record is None:
            self.existing_record = self.record
            return self.record, True
        return self.record, False

    async def get_by_content_hash(
        self,
        content_hash: str,
        embedding_provider: str,
        embedding_model: str,
    ):
        return self.existing_record

    async def delete_by_id(self, document_id: UUID) -> bool:
        self.deleted_ids.append(document_id)
        self.existing_record = None
        return True


class FakeChunkingService:
    def build_chunks(self, document: NormalizedDocument) -> list[dict]:
        raise AssertionError("chunking should not run for duplicate documents")


class ForceChunkingService:
    def build_chunks(self, document: NormalizedDocument) -> list[dict]:
        return [{"content": document.content, "metadata": {"title": document.title}}]

    def build_chunk_upserts(self, document: NormalizedDocument, chunks: list[dict], embeddings: list[list[float]]):
        return [
            SimpleNamespace(
                chunk_index=0,
                content=document.content,
                metadata={"title": document.title},
                embedding=embeddings[0],
            )
        ]


class FakeEmbeddingService:
    async def embed_texts(self, *args, **kwargs):
        raise AssertionError("embedding should not run for duplicate documents")


class ForceEmbeddingService:
    async def embed_texts(self, *args, **kwargs):
        return None, [[0.1, 0.2, 0.3]]


class FakeChunkRepository:
    def __init__(self) -> None:
        self.deleted_ids: list[UUID] = []
        self.bulk_calls: int = 0

    async def bulk_create(self, *args, **kwargs):
        self.bulk_calls += 1
        return [SimpleNamespace(id=UUID("22222222-2222-2222-2222-222222222222"))]

    async def delete_for_document(self, document_id: UUID, embedding_dimension: int) -> int:
        self.deleted_ids.append(document_id)
        return 1


def build_service() -> IngestService:
    service = object.__new__(IngestService)
    service._document_repository = FakeDocumentRepository()
    service._chunking_service = FakeChunkingService()
    service._embedding_service = FakeEmbeddingService()
    service._chunk_repository = FakeChunkRepository()
    return service


def test_duplicate_document_upload_is_skipped() -> None:
    service = build_service()
    parsed_file = ParsedFile(
        filename="kb.txt",
        detected_type="txt",
        documents=[
            NormalizedDocument(
                title="KB",
                source_type="text",
                content="Same knowledge base content",
                metadata={},
            )
        ],
    )

    result = asyncio.run(
            service._persist_documents(
                parsed_file=parsed_file,
                embedding_provider="ollama",
                embedding_model="test-model",
                embedding_profile="ollama_1536",
                embedding_dimension=1536,
                force_reingest=False,
            )
        )

    assert result["documents_inserted"] == 0
    assert result["chunks_inserted"] == 0
    assert result["results"][0].deduplicated is True
    assert result["results"][0].success is True


def test_force_reingest_replaces_existing_document() -> None:
    service = object.__new__(IngestService)
    service._document_repository = FakeDocumentRepository()
    service._chunking_service = ForceChunkingService()
    service._embedding_service = ForceEmbeddingService()
    service._chunk_repository = FakeChunkRepository()
    parsed_file = ParsedFile(
        filename="kb.txt",
        detected_type="txt",
        documents=[
            NormalizedDocument(
                title="KB",
                source_type="text",
                content="Same knowledge base content",
                metadata={},
            )
        ],
    )

    result = asyncio.run(
        service._persist_documents(
            parsed_file=parsed_file,
            embedding_provider="ollama",
            embedding_model="test-model",
            embedding_profile="ollama_1536",
            embedding_dimension=1536,
            force_reingest=True,
        )
    )

    assert result["documents_inserted"] == 1
    assert result["chunks_inserted"] == 1
    assert service._document_repository.deleted_ids == [UUID("11111111-1111-1111-1111-111111111111")]
    assert service._chunk_repository.bulk_calls == 1
