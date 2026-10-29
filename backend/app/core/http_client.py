"""Shared, long-lived HTTP clients so provider calls reuse TCP/TLS connections.

Creating an ``httpx.AsyncClient`` per call meant a fresh TCP and TLS handshake to
the model API for every embedding, rerank and chat request.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

_clients: dict[tuple[str, float], httpx.AsyncClient] = {}


@asynccontextmanager
async def shared_http_client(*, base_url: str = "", timeout: float = 60.0) -> AsyncIterator[httpx.AsyncClient]:
    """Drop-in for ``async with httpx.AsyncClient(...)``: yields a pooled client for
    (base_url, timeout) and leaves its connections open for the next request."""
    key = (base_url, timeout)
    client = _clients.get(key)
    if client is None or getattr(client, "is_closed", False):
        client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            # Per-call clients had no shared cap; keep it that way so concurrent
            # chats never queue behind each other for a connection.
            limits=httpx.Limits(max_connections=None, max_keepalive_connections=20),
        )
        _clients[key] = client
    yield client


async def close_shared_http_clients() -> None:
    clients = list(_clients.values())
    _clients.clear()
    for client in clients:
        await client.aclose()


def forget_shared_http_clients() -> None:
    """Drop pooled clients without closing them (tests run each case in a new event loop)."""
    _clients.clear()
