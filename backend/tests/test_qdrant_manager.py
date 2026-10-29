import asyncio
from types import SimpleNamespace

from app.core.config import Settings
from app.db.qdrant import QdrantManager


class FakeQdrantClient:
    def __init__(self) -> None:
        self.collections: set[str] = set()

    async def collection_exists(self, name: str) -> bool:
        return name in self.collections

    async def create_collection(self, collection_name: str, vectors_config) -> None:
        self.collections.add(collection_name)

    async def create_payload_index(self, **kwargs) -> None:
        return None

    async def get_collections(self):
        return SimpleNamespace(collections=[SimpleNamespace(name=name) for name in sorted(self.collections)])

    async def delete_collection(self, name: str) -> None:
        self.collections.discard(name)


def test_collection_is_recreated_after_reset() -> None:
    manager = QdrantManager(Settings())
    client = FakeQdrantClient()
    manager._client = client

    async def run() -> None:
        await manager.ensure_collection(2048)
        assert await manager.delete_all_collections() == 1
        await manager.ensure_collection(2048)

    asyncio.run(run())

    assert client.collections == {"rag_chunks_2048"}


def test_concurrent_first_searches_create_the_collection_once() -> None:
    manager = QdrantManager(Settings())
    client = FakeQdrantClient()
    creations: list[str] = []
    original_create = client.create_collection

    async def counting_create(collection_name: str, vectors_config) -> None:
        await asyncio.sleep(0)  # let the other searches run in between
        creations.append(collection_name)
        await original_create(collection_name, vectors_config)

    client.create_collection = counting_create
    manager._client = client

    async def run() -> None:
        await asyncio.gather(*(manager.ensure_collection(2048) for _ in range(5)))

    asyncio.run(run())

    assert creations == ["rag_chunks_2048"]
