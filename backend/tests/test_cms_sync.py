import asyncio
from types import SimpleNamespace

from app.services import contentful
from app.services.cms_sync import sync_cms_if_outdated


class FakeRedis:
    def __init__(self, values: dict | None = None) -> None:
        self.values = dict(values or {})

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key):
        self.values.pop(key, None)


class FakeIngest:
    def __init__(self, failed: int = 0, error: Exception | None = None) -> None:
        self.calls = []
        self.failed = failed
        self.error = error

    async def ingest_cms(self, payload, current_user):
        self.calls.append((payload, current_user))
        if self.error:
            raise self.error
        return SimpleNamespace(failed=self.failed, succeeded=5, total_chunks_inserted=9)


def _settings(configured: bool = True):
    return SimpleNamespace(
        contentful_space_id="space" if configured else None,
        contentful_delivery_token="token" if configured else None,
    )


def test_outdated_index_is_reingested_once() -> None:
    redis, ingest = FakeRedis(), FakeIngest()

    assert asyncio.run(sync_cms_if_outdated(_settings(), redis, ingest)) is True
    payload, user = ingest.calls[0]
    assert payload.force_reingest is True and payload.content_types == []
    assert user.username == "system"
    assert redis.values == {"cms_index_version": contentful.CMS_INDEX_VERSION}

    # Current version: nothing to do.
    assert asyncio.run(sync_cms_if_outdated(_settings(), redis, ingest)) is False
    assert len(ingest.calls) == 1


def test_sync_skipped_when_unconfigured_or_locked() -> None:
    ingest = FakeIngest()

    assert asyncio.run(sync_cms_if_outdated(_settings(configured=False), FakeRedis(), ingest)) is False
    locked = FakeRedis({"cms_index_sync_lock": "1"})
    assert asyncio.run(sync_cms_if_outdated(_settings(), locked, ingest)) is False
    assert ingest.calls == []


def test_failed_sync_retries_next_time_and_releases_lock() -> None:
    for ingest in (FakeIngest(failed=1), FakeIngest(error=RuntimeError("boom"))):
        redis = FakeRedis()

        assert asyncio.run(sync_cms_if_outdated(_settings(), redis, ingest)) is False
        assert redis.values == {}
