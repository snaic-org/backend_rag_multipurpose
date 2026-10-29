import asyncio

import httpx

from app.core.http_client import close_shared_http_clients, shared_http_client


def test_shared_client_is_reused_and_closed_on_shutdown() -> None:
    async def run():
        async with shared_http_client(base_url="https://integrate.api.nvidia.com/v1", timeout=60.0) as first:
            pass
        async with shared_http_client(base_url="https://integrate.api.nvidia.com/v1", timeout=60.0) as second:
            pass
        async with shared_http_client(base_url="https://ai.api.nvidia.com", timeout=30.0) as other:
            pass
        await close_shared_http_clients()
        return first, second, other

    first, second, other = asyncio.run(run())

    assert first is second
    assert other is not first
    assert isinstance(first, httpx.AsyncClient)
    assert first.is_closed and other.is_closed
