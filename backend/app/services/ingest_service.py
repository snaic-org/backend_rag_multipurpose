import hashlib
from pathlib import Path

from fastapi import UploadFile
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.qdrant import QdrantManager
from app.db.redis import RedisManager
from app.db.repositories.chunks import ChunkRepository
from app.db.repositories.documents import DocumentRepository
from app.models.schemas import (
    IngestFileResult,
    IngestFilesResponse,
    IngestTextRequest,
    IngestTextResponse,
    NormalizedDocument,
    ParsedFile,
)
from app.parsers.factory import ParserFactory
from app.providers.registry import ProviderRegistry
from app.services.cache_service import CacheService
from app.services.chunking import ChunkingService
from app.services.embeddings import EmbeddingService

logger = get_logger(__name__)


class IngestService:
    def __init__(
        self,
        settings: Settings,
        redis_manager: RedisManager,
        qdrant_manager: QdrantManager,
        postgres_pool: AsyncConnectionPool,
        provider_registry: ProviderRegistry,
    ) -> None:
        self._settings = settings
        self._redis_manager = redis_manager
        self._postgres_pool = postgres_pool
        self._provider_registry = provider_registry
        self._document_repository = DocumentRepository(postgres_pool)
        self._chunk_repository = ChunkRepository(qdrant_manager)
        self._parser_factory = ParserFactory()
        self._chunking_service = ChunkingService(settings)
        self._embedding_service = EmbeddingService(
            settings,
            cache_service=CacheService(
                redis_manager.client,
                ttl_seconds=settings.embedding_cache_ttl_seconds,
            ),
        )

    async def ingest_text_items(self, payload: IngestTextRequest) -> IngestTextResponse:
        selection = self._embedding_service.resolve_selection(
            payload.embedding_profile,
            payload.embedding_provider,
            payload.embedding_model,
        )

        results: list[IngestFileResult] = []
        documents_inserted = 0
        chunks_inserted = 0

        for item in payload.items:
            document = NormalizedDocument(
                title=item.title,
                source_type=item.source_type,
                content=item.content.strip(),
                metadata=dict(item.metadata),
                url=item.url,
            )

            persisted = await self._persist_documents(
                parsed_file=ParsedFile(
                    filename=item.title,
                    detected_type=item.source_type,
                    documents=[document],
                ),
                embedding_provider=selection.provider,
                embedding_model=selection.model,
                embedding_profile=selection.profile_name,
                embedding_dimension=selection.dimension,
            )
            results.extend(persisted["results"])
            documents_inserted += persisted["documents_inserted"]
            chunks_inserted += persisted["chunks_inserted"]

        return IngestTextResponse(
            documents_inserted=documents_inserted,
            chunks_inserted=chunks_inserted,
            embedding_provider=selection.provider,
            embedding_model=selection.model,
            results=results,
        )

    async def ingest_uploaded_files(
        self,
        files: list[UploadFile],
        source_type_override: str | None,
        tags: list[str],
        shared_metadata: dict,
        embedding_profile: str | None,
        embedding_provider: str | None,
        embedding_model: str | None,
    ) -> IngestFilesResponse:
        selection = self._embedding_service.resolve_selection(
            embedding_profile,
            embedding_provider,
            embedding_model,
        )

        results: list[IngestFileResult] = []
        total_chunks_inserted = 0

        for upload in files:
            try:
                content = await upload.read()
                if not content or not content.strip():
                    raise ValueError("empty file")

                filename = upload.filename or "upload"
                detected_type = self._parser_factory.detect_type(filename, upload.content_type)
                parser = self._parser_factory.get_parser(detected_type)
                parsed_file = await parser.parse(
                    filename=filename,
                    content=content,
                    mime_type=upload.content_type,
                    source_type_override=source_type_override,
                    shared_metadata=shared_metadata,
                    tags=tags,
                )

                persisted = await self._persist_documents(
                    parsed_file=parsed_file,
                    embedding_provider=selection.provider,
                    embedding_model=selection.model,
                    embedding_profile=selection.profile_name,
                    embedding_dimension=selection.dimension,
                )
                results.extend(persisted["results"])
                total_chunks_inserted += persisted["chunks_inserted"]
            except Exception as exc:
                logger.exception("file_ingest_failed filename=%s", upload.filename)
                results.append(
                    IngestFileResult(
                        filename=upload.filename or "unknown",
                        detected_type=Path(upload.filename or "").suffix.lstrip(".") or "unknown",
                        success=False,
                        chunks_created=0,
                        error=str(exc),
                    )
                )

        succeeded = sum(1 for result in results if result.success)
        failed = sum(1 for result in results if not result.success)

        return IngestFilesResponse(
            total_files=len(files),
            succeeded=succeeded,
            failed=failed,
            total_chunks_inserted=total_chunks_inserted,
            embedding_provider=selection.provider,
            embedding_model=selection.model,
            results=results,
        )

    async def _persist_documents(
        self,
        parsed_file: ParsedFile,
        embedding_provider: str,
        embedding_model: str,
        embedding_profile: str,
        embedding_dimension: int,
    ) -> dict:
        results: list[IngestFileResult] = []
        documents_inserted = 0
        chunks_inserted = 0

        for document in parsed_file.documents:
            content_hash = self._hash_document(document.content)
            document_record, created = await self._document_repository.create_or_get_by_content_hash(
                document=document,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                content_hash=content_hash,
            )
            if not created:
                results.append(
                    IngestFileResult(
                        filename=parsed_file.filename,
                        detected_type=parsed_file.detected_type,
                        success=True,
                        chunks_created=0,
                        deduplicated=True,
                        document_id=document_record.id,
                    )
                )
                continue

            chunks = self._chunking_service.build_chunks(document)
            if not chunks:
                results.append(
                    IngestFileResult(
                        filename=parsed_file.filename,
                        detected_type=parsed_file.detected_type,
                        success=False,
                        chunks_created=0,
                        error="no chunks generated from content",
                    )
                )
                continue

            _, embeddings = await self._embedding_service.embed_texts(
                texts=[chunk["content"] for chunk in chunks],
                provider=embedding_provider,
                model=embedding_model,
                input_type="passage",
            )
            chunk_upserts = self._chunking_service.build_chunk_upserts(document, embeddings)
            inserted_chunks = await self._chunk_repository.bulk_create(
                document_id=document_record.id,
                chunks=chunk_upserts,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                embedding_profile=embedding_profile,
                embedding_dimension=embedding_dimension,
            )

            documents_inserted += 1
            chunks_inserted += len(inserted_chunks)
            results.append(
                IngestFileResult(
                    filename=parsed_file.filename,
                    detected_type=parsed_file.detected_type,
                    success=True,
                    chunks_created=len(inserted_chunks),
                    document_id=document_record.id,
                )
            )

        return {
            "results": results,
            "documents_inserted": documents_inserted,
            "chunks_inserted": chunks_inserted,
        }

    def _hash_document(self, content: str) -> str:
        normalized = " ".join(content.split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
