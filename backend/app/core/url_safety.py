"""Guard server-side fetches of user-supplied URLs (SSRF protection)."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_PORTS = {None, 80, 443}


class UnsafeUrlError(ValueError):
    """The URL points somewhere the server must not fetch."""


async def ensure_public_url(url: str, *, allow_private_hosts: bool = False) -> None:
    """Raise UnsafeUrlError unless url is http(s) on a standard port and every
    address its host resolves to is publicly routable.

    Blocks loopback, private (VPC), link-local (cloud metadata endpoints such as
    169.254.169.254 / 169.254.170.2), multicast and reserved ranges. Call it again
    for every redirect hop.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError("Only http and https URLs can be ingested.")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("URL has no host.")
    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeUrlError("URL has an invalid port.") from exc
    if port not in _ALLOWED_PORTS:
        raise UnsafeUrlError("Only the standard web ports (80 and 443) can be ingested.")
    if allow_private_hosts:
        return

    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError("Could not resolve the host name.") from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise UnsafeUrlError("URL points to a private or internal network address, which cannot be ingested.")
