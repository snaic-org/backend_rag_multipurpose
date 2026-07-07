from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.models.schemas import DocumentRecord, NormalizedDocument


class DocumentRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create_or_get_by_content_hash(
        self,
        document: NormalizedDocument,
        embedding_provider: str,
        embedding_model: str,
        content_hash: str,
        created_by: str,
    ) -> tuple[DocumentRecord, bool]:
        existing = await self.get_by_content_hash(
            content_hash=content_hash,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
        )
        if existing is not None:
            return existing, False

        document_id = uuid4()

        query = """
            INSERT INTO documents (
                id,
                content_hash,
                title,
                url,
                source_type,
                metadata,
                original_filename,
                mime_type,
                created_by,
                embedding_provider,
                embedding_model
            )
            VALUES (
                %(id)s,
                %(content_hash)s,
                %(title)s,
                %(url)s,
                %(source_type)s,
                %(metadata)s,
                %(original_filename)s,
                %(mime_type)s,
                %(created_by)s,
                %(embedding_provider)s,
                %(embedding_model)s
            )
            ON CONFLICT DO NOTHING
            RETURNING
                id,
                content_hash,
                title,
                url,
                source_type,
                metadata,
                original_filename,
                mime_type,
                created_by,
                embedding_provider,
                embedding_model,
                created_at,
                updated_at
        """

        params = {
            "id": document_id,
            "content_hash": content_hash,
            "title": document.title,
            "url": document.url,
            "source_type": document.source_type,
            "metadata": Jsonb(document.metadata),
            "original_filename": document.original_filename,
            "mime_type": document.mime_type,
            "created_by": created_by,
            "embedding_provider": embedding_provider,
            "embedding_model": embedding_model,
        }

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, params)
                row = await cursor.fetchone()
            await connection.commit()

        if row is not None:
            return DocumentRecord.model_validate(row), True

        existing = await self.get_by_content_hash(
            content_hash=content_hash,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
        )
        if existing is None:
            raise RuntimeError("Failed to insert or fetch document record.")
        return existing, False

    async def get_by_content_hash(
        self,
        content_hash: str,
        embedding_provider: str,
        embedding_model: str,
    ) -> DocumentRecord | None:
        query = """
            SELECT
                id,
                content_hash,
                title,
                url,
                source_type,
                metadata,
                original_filename,
                mime_type,
                created_by,
                embedding_provider,
                embedding_model,
                created_at,
                updated_at
            FROM documents
            WHERE content_hash = %(content_hash)s
              AND embedding_provider = %(embedding_provider)s
              AND embedding_model = %(embedding_model)s
        """

        params = {
            "content_hash": content_hash,
            "embedding_provider": embedding_provider,
            "embedding_model": embedding_model,
        }

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, params)
                row = await cursor.fetchone()

        if row is None:
            return None

        return DocumentRecord.model_validate(row)

    async def get_by_id(self, document_id: UUID) -> DocumentRecord | None:
        query = """
            SELECT
                id,
                content_hash,
                title,
                url,
                source_type,
                metadata,
                original_filename,
                mime_type,
                created_by,
                embedding_provider,
                embedding_model,
                created_at,
                updated_at
            FROM documents
            WHERE id = %(document_id)s
        """

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, {"document_id": document_id})
                row = await cursor.fetchone()

        if row is None:
            return None

        return DocumentRecord.model_validate(row)

    async def list_recent(self, limit: int = 20, source_type: str | None = None) -> list[DocumentRecord]:
        base_cols = """
            id, content_hash, title, url, source_type, metadata,
            original_filename, mime_type, created_by, embedding_provider,
            embedding_model, created_at, updated_at
        """
        if source_type is not None:
            query = f"SELECT {base_cols} FROM documents WHERE source_type = %(source_type)s ORDER BY created_at DESC LIMIT %(limit)s"
            params: dict = {"limit": limit, "source_type": source_type}
        else:
            query = f"SELECT {base_cols} FROM documents ORDER BY created_at DESC LIMIT %(limit)s"
            params = {"limit": limit}

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, params)
                rows = await cursor.fetchall()

        return [DocumentRecord.model_validate(row) for row in rows]

    async def list_ids_by_source_type(self, source_type: str) -> list[UUID]:
        query = "SELECT id FROM documents WHERE source_type = %(source_type)s"
        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, {"source_type": source_type})
                rows = await cursor.fetchall()

        return [row["id"] for row in rows]

    async def list_ids_by_content_type(self, content_type: str) -> list[UUID]:
        query = """
            SELECT id
            FROM documents
            WHERE metadata->>'contentful_content_type' = %(content_type)s
        """
        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, {"content_type": content_type})
                rows = await cursor.fetchall()

        return [row["id"] for row in rows]

    async def delete_by_id(self, document_id: UUID) -> bool:
        query = "DELETE FROM documents WHERE id = %(document_id)s"

        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(query, {"document_id": document_id})
                deleted = cursor.rowcount > 0
            await connection.commit()

        return deleted

    async def delete_all(self) -> dict[str, int]:
        document_count_query = "SELECT COUNT(*) AS count FROM documents"
        delete_query = "DELETE FROM documents"

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(document_count_query)
                document_row = await cursor.fetchone()
                await cursor.execute(delete_query)
            await connection.commit()

        return {
            "documents_deleted": int(document_row["count"]) if document_row is not None else 0,
            "chunks_deleted": 0,
        }
