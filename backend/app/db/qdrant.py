from __future__ import annotations

import asyncio

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.schemas import DependencyHealth

logger = get_logger(__name__)

TEXT_INDEXED_FIELDS = ("content", "title")


class QdrantManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None
        self._initialized_collections: set[str] = set()
        self._text_indexed_collections: set[str] = set()
        # Retrieval runs several searches concurrently; without the lock their
        # first calls would all try to create the same collection.
        self._ensure_lock = asyncio.Lock()

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
        collection_name = self.collection_name(dimension)
        if collection_name in self._initialized_collections:
            return collection_name

        async with self._ensure_lock:
            if collection_name not in self._initialized_collections:
                await self._initialize_collection(collection_name, dimension)
        return collection_name

    async def _initialize_collection(self, collection_name: str, dimension: int) -> None:
        from qdrant_client.http import models

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

        # Full-text indexes on chunk content and title let keyword search filter
        # server-side. Best-effort: keyword search falls back to an unfiltered
        # scroll without them.
        try:
            for field in TEXT_INDEXED_FIELDS:
                await self.client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field,
                    field_schema=models.TextIndexParams(
                        type=models.TextIndexType.TEXT,
                        tokenizer=models.TokenizerType.PREFIX,
                        min_token_len=2,
                        max_token_len=20,
                        lowercase=True,
                    ),
                )
            self._text_indexed_collections.add(collection_name)
        except Exception as exc:
            logger.warning("qdrant_text_index_unavailable collection=%s error=%r", collection_name, exc)

        self._initialized_collections.add(collection_name)

    def has_text_index(self, collection_name: str) -> bool:
        return collection_name in self._text_indexed_collections

    async def delete_all_collections(self) -> int:
        deleted = 0
        collections = await self.client.get_collections()
        prefix = f"{self._settings.qdrant_collection_prefix}_"
        for collection in collections.collections:
            if collection.name.startswith(prefix):
                await self.client.delete_collection(collection.name)
                deleted += 1
        # Forget the deleted collections so ensure_collection recreates them;
        # otherwise every chat and ingest fails with "collection not found".
        self._initialized_collections.clear()
        self._text_indexed_collections.clear()
        return deleted

    async def healthcheck(self) -> DependencyHealth:
        try:
            await self.client.get_collections()
            return DependencyHealth(ok=True, detail="connected")
        except Exception as exc:  # pragma: no cover - defensive branch
            return DependencyHealth(ok=False, detail=f"qdrant_error: {type(exc).__name__}")

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.close()
        self._client = None
