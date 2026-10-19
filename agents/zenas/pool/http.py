"""Live fetches: a named user agent, gzip, and a retry when a host says no."""

from __future__ import annotations

import asyncio
import gzip
import urllib.error
import urllib.request

from agents.zenas.pool.licence import FetchNotConfigured

USER_AGENT = "TervoryZenasPool/1.0 (+https://app.tervory.com)"
RETRY_STATUS = frozenset({406, 429, 500, 502, 503, 504})
# Sleeps between attempts. Tests set this to zeros.
BACKOFF_SECONDS = (0.5, 1.0, 2.0)


def request_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """Always name the client and allow gzip. The caller's Accept is kept."""
    merged = dict(headers or {})
    merged["User-Agent"] = USER_AGENT
    merged["Accept-Encoding"] = "gzip"
    return merged


def _reject_credential_url(url: str) -> None:
    lowered = url.lower()
    if "api_key=" in lowered or "token=" in lowered or "?key=" in lowered or "&key=" in lowered:
        raise FetchNotConfigured("refusing a URL that contains a credential")


def _decode(raw: bytes, encoding: str) -> bytes:
    if "gzip" in encoding.lower() or raw[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(raw)
        except (OSError, EOFError, gzip.BadGzipFile):
            return raw
    return raw


async def urllib_get(url: str, headers: dict[str, str] | None = None) -> bytes:
    """Public government URLs only. Never put a key in the URL.

    A 406, 429, or 5xx is retried with backoff. Gzip responses are unpacked.
    """
    _reject_credential_url(url)
    outgoing = request_headers(headers)

    def _once() -> bytes:
        request = urllib.request.Request(url, headers=outgoing)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                encoding = response.headers.get("Content-Encoding") or ""
                return _decode(raw, encoding)
        except urllib.error.HTTPError as exc:
            body = exc.read() if exc.fp is not None else b""
            code = int(exc.code or 0)
            if code not in RETRY_STATUS:
                raise
            raise _Retry(code, body) from None

    attempt = 0
    waits = list(BACKOFF_SECONDS)
    while True:
        try:
            return await asyncio.to_thread(_once)
        except _Retry as exc:
            if attempt >= len(waits):
                raise FetchNotConfigured(f"HTTP {exc.code} from {url}") from None
            await asyncio.sleep(waits[attempt])
            attempt += 1


class _Retry(Exception):
    def __init__(self, code: int, body: bytes) -> None:
        super().__init__(code)
        self.code = code
        self.body = body
