"""Configured reference-file transport using the existing public-web policy."""
from __future__ import annotations

import httpx

try:  # The Base image copies the existing helper under this import name.
    from reference_security import public_destination, public_url
except ImportError:  # Repository/offline tests use the same source directly.
    from agents._brain.security import public_destination, public_url


def file_client() -> httpx.AsyncClient:
    # No environment proxy or room authentication on a public file request.
    return httpx.AsyncClient(trust_env=False, follow_redirects=False)


def validate(url: str) -> None:
    public_url(url)
    parsed = httpx.URL(url)
    if parsed.scheme != 'https' or parsed.fragment:
        raise ValueError('The official reference file is unavailable.')


async def destination(url: str) -> tuple[httpx.URL, str]:
    validate(url)
    host, _, addresses = await public_destination(url)
    # Pin the socket destination so a second DNS lookup cannot reach a private IP.
    # Preserve the original TLS name and Host header in the caller.
    return httpx.URL(url).copy_with(host=addresses[0]), host
