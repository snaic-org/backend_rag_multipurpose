from redis.asyncio import Redis

from app.core.config import Settings
from app.models.schemas import DependencyHealth


class RedisManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Redis | None = None

    @property
    def client(self) -> Redis:
        if self._client is None:
            raise RuntimeError("Redis client has not been initialized.")
        return self._client

    async def connect(self) -> None:
        self._client = Redis.from_url(
            self._settings.redis_url,
            max_connections=self._settings.redis_max_connections,
            decode_responses=True,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def healthcheck(self) -> DependencyHealth:
        try:
            if self._client is None:
                return DependencyHealth(ok=False, detail="redis_not_initialized")
            pong = await self._client.ping()
            return DependencyHealth(ok=bool(pong), detail="connected")
        except Exception as exc:
            return DependencyHealth(ok=False, detail=f"redis_unreachable: {exc}")

    async def delete_by_prefixes(self, prefixes: list[str]) -> int:
        if self._client is None:
            raise RuntimeError("Redis client has not been initialized.")

        deleted = 0
        for prefix in prefixes:
            pattern = f"{prefix}*"
            cursor = 0
            while True:
                cursor, keys = await self._client.scan(cursor=cursor, match=pattern, count=200)
                if keys:
                    deleted += await self._client.delete(*keys)
                if cursor == 0:
                    break

        return deleted
