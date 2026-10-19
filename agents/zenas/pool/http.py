"""Live fetches: a named user agent, gzip, and a retry when a host says no."""

from __future__ import annotations

import asyncio
import email.utils
import gzip
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from agents.zenas.pool.licence import FetchNotConfigured

USER_AGENT = "TervoryZenasPool/1.0 (+https://app.tervory.com)"
RETRY_STATUS = frozenset({406, 500, 502, 503, 504})
# Sleeps between attempts. Tests set this to zeros.
BACKOFF_SECONDS = (0.5, 1.0, 2.0)
# A 429 waits for the host's Retry-After, or this many seconds, up to
# RATE_LIMIT_RETRIES times. A longer Retry-After is capped. Tests set zeros.
RATE_LIMIT_WAIT_SECONDS = 60.0
RATE_LIMIT_MAX_WAIT_SECONDS = 300.0
RATE_LIMIT_RETRIES = 5


class RateLimited(FetchNotConfigured):
    """The host still answered 429 after every retry.

    The message names the host only, never the URL, so a cursor or a
    credential cannot reach a log or the feeds page.
    """

    def __init__(self, host: str, retries: int) -> None:
        super().__init__(f"HTTP 429 from {host} after {retries} retries")
        self.host = host
        self.retries = retries


def host_of(url: str) -> str:
    try:
        return urlsplit(url).hostname or "the host"
    except ValueError:
        return "the host"


def retry_after_seconds(value: str | None, *, now: float | None = None) -> float:
    """Seconds from a Retry-After header (delta or HTTP date). Default when absent."""
    raw = (value or "").strip()
    wait = RATE_LIMIT_WAIT_SECONDS
    if raw:
        try:
            wait = float(int(raw))
        except ValueError:
            try:
                when = email.utils.parsedate_to_datetime(raw)
            except (TypeError, ValueError, IndexError):
                when = None
            if when is not None:
                wait = when.timestamp() - (time.time() if now is None else now)
    return max(0.0, min(wait, RATE_LIMIT_MAX_WAIT_SECONDS))


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

    A 406 or 5xx is retried with backoff. A 429 waits for Retry-After (or
    RATE_LIMIT_WAIT_SECONDS) up to RATE_LIMIT_RETRIES times, then raises
    RateLimited. Gzip responses are unpacked.
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
            if code == 429:
                headers = exc.headers if exc.headers is not None else {}
                raise _Retry(code, body, headers.get("Retry-After")) from None
            if code not in RETRY_STATUS:
                raise
            raise _Retry(code, body) from None

    attempt = 0
    limited = 0
    waits = list(BACKOFF_SECONDS)
    while True:
        try:
            return await asyncio.to_thread(_once)
        except _Retry as exc:
            if exc.code == 429:
                if limited >= RATE_LIMIT_RETRIES:
                    raise RateLimited(host_of(url), limited) from None
                await asyncio.sleep(retry_after_seconds(exc.retry_after))
                limited += 1
                continue
            if attempt >= len(waits):
                raise FetchNotConfigured(f"HTTP {exc.code} from {host_of(url)}") from None
            await asyncio.sleep(waits[attempt])
            attempt += 1


class _Retry(Exception):
    def __init__(self, code: int, body: bytes, retry_after: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.body = body
        self.retry_after = retry_after


class PatchrightPages:
    """One Chromium, via patchright, for a SAFLII load.

    The package is imported when the browser opens, so a test that never
    fetches does not need it installed. Chromium sends its own headers.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None

    async def __aenter__(self) -> PatchrightPages:
        from patchright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        # The room runs as an unprivileged user. Chromium's sandbox cannot
        # start there, and /dev/shm in that container is too small for a page.
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        self._context = await self._browser.new_context(locale="en-ZA")
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def get(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        """HTML of one page. A credential in the URL is refused before launch."""
        del headers
        _reject_credential_url(url)
        if self._context is None:
            raise FetchNotConfigured("patchright is not open")
        page = await self._context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:  # noqa: BLE001 — a page that never goes idle still has HTML
                pass
            return (await page.content()).encode("utf-8")
        finally:
            await page.close()
