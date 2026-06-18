from __future__ import annotations

from app.core.config import Settings
from app.models.schemas import DependencyHealth


class QdrantManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from qdrant_client import AsyncQdrantClient

            self._client = AsyncQdrantClient(
                url=self._settings.qdrant_url,
                api_key=self._settings.qdrant_api_key,
            )
        return self._client

    def collection_name(self, dimension: int) -> str:
        if dimension <= 0:
            raise ValueError("Embedding dimension must be a positive integer")
        return f"{self._settings.qdrant_collection_prefix}_{dimension}"

    async def ensure_collection(self, dimension: int) -> str:
        from qdrant_client.http import models

        collection_name = self.collection_name(dimension)
        if not await self.client.collection_exists(collection_name):
            await self.client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=dimension,
                    distance=models.Distance.COSINE,
                ),
            )

        for field in ("embedding_profile", "embedding_provider", "embedding_model", "document_id"):
            await self.client.create_payload_index(
                collection_name=collection_name,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

        return collection_name

    async def delete_all_collections(self) -> int:
        deleted = 0
        collections = await self.client.get_collections()
        prefix = f"{self._settings.qdrant_collection_prefix}_"
        for collection in collections.collections:
            if collection.name.startswith(prefix):
                await self.client.delete_collection(collection.name)
                deleted += 1
        return deleted

    async def healthcheck(self) -> DependencyHealth:
        try:
            await self.client.get_collections()
            return DependencyHealth(ok=True, detail="connected")
        except Exception as exc:  # pragma: no cover - defensive branch
            return DependencyHealth(ok=False, detail=f"qdrant_error: {exc}")

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.close()
        self._client = None
