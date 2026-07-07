from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.db.qdrant import QdrantManager
from app.db.repositories.chunks import ChunkRepository
from app.db.repositories.documents import DocumentRepository
from app.models.schemas import (
    ChunkRecord,
    DocumentRecord,
    IngestedDocumentDetails,
    IngestedDocumentSummary,
)


class DocumentInspectionService:
    def __init__(
        self,
        settings: Settings,
        postgres_pool: AsyncConnectionPool,
        qdrant_manager: QdrantManager,
    ) -> None:
        self._settings = settings
        self._document_repository = DocumentRepository(postgres_pool)
        self._chunk_repository = ChunkRepository(qdrant_manager)

    async def list_documents(self, limit: int = 20, source_type: str | None = None) -> list[IngestedDocumentSummary]:
        documents = await self._document_repository.list_recent(limit=limit, source_type=source_type)
        return [
            IngestedDocumentSummary(
                document=document,
                embedding_profile=self._resolve_embedding_profile(document),
            )
            for document in documents
        ]

    async def get_document(self, document_id: UUID) -> IngestedDocumentDetails:
        document = await self._document_repository.get_by_id(document_id)
        if document is None:
            raise ValueError("document not found")

        embedding_profile = self._resolve_embedding_profile(document)
        embedding_dimension = self._resolve_embedding_dimension(document)
        chunks = await self._chunk_repository.list_for_document(document.id, embedding_dimension)
        full_text = self._reconstruct_full_text(chunks)

        return IngestedDocumentDetails(
            document=document,
            embedding_profile=embedding_profile,
            full_text=full_text,
            chunk_count=len(chunks),
            chunks=chunks,
        )

    async def get_document_chunks(self, document_id: UUID) -> list[ChunkRecord]:
        document = await self._document_repository.get_by_id(document_id)
        if document is None:
            raise ValueError("document not found")

        embedding_dimension = self._resolve_embedding_dimension(document)
        chunks = await self._chunk_repository.list_for_document(document.id, embedding_dimension)
        return chunks

    def _resolve_embedding_profile(self, document: DocumentRecord) -> str | None:
        for profile_name, spec in self._settings.embedding_profiles.items():
            if spec.provider == document.embedding_provider and spec.model == document.embedding_model:
                return profile_name
        return None

    def _resolve_embedding_dimension(self, document: DocumentRecord) -> int:
        for spec in self._settings.embedding_profiles.values():
            if spec.provider == document.embedding_provider and spec.model == document.embedding_model:
                return spec.dimension
        raise ValueError("embedding profile not found for document")

    def _reconstruct_full_text(self, chunks: list[ChunkRecord]) -> str:
        if not chunks:
            return ""

        return "\n\n".join(chunk.content.strip() for chunk in chunks if chunk.content.strip())
