"""Live fetches: a named user agent, gzip, and a retry when a host says no."""

from __future__ import annotations

import asyncio
import email.utils
import gzip
import ssl
import threading
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


def retry_after_seconds(value: str | None, *, now: float | None = None, max_wait: float | None = RATE_LIMIT_MAX_WAIT_SECONDS) -> float:
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
    return max(0.0, min(wait, max_wait) if max_wait is not None else wait)


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


# Some hosts (www.siu.org.za, which lists newer Special Tribunal judgments) send
# their certificate without the intermediate that signed it. Browsers fill the
# gap from the certificate's Authority Information Access "CA Issuers" URL;
# Python does not, so the request fails with "unable to get local issuer
# certificate". This does what a browser does. The fetched certificate is only a
# chain link: it must be a CA and not self-signed, partial chains stay off, so
# the chain must still end at a system root, and the host name is still checked.
_UNABLE_TO_GET_ISSUER = 20  # X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY
_ISSUER_MAX_BYTES = 64 * 1024
_ISSUER_CONTEXTS: dict[str, ssl.SSLContext] = {}
_ISSUER_LOCK = threading.Lock()


def missing_issuer(exc: BaseException) -> bool:
    """True when TLS verification failed only because the server left out its intermediate."""
    reason = getattr(exc, "reason", exc)
    return (
        isinstance(reason, ssl.SSLCertVerificationError)
        and getattr(reason, "verify_code", 0) == _UNABLE_TO_GET_ISSUER
    )


def issuer_urls(leaf_pem: str) -> list[str]:
    """The http(s) CA Issuers URLs a certificate names for its own issuer."""
    from cryptography import x509
    from cryptography.x509.oid import AuthorityInformationAccessOID

    cert = x509.load_pem_x509_certificate(leaf_pem.encode("ascii"))
    try:
        aia = cert.extensions.get_extension_for_class(x509.AuthorityInformationAccess).value
    except x509.ExtensionNotFound:
        return []
    return [
        item.access_location.value
        for item in aia
        if item.access_method == AuthorityInformationAccessOID.CA_ISSUERS
        and isinstance(item.access_location, x509.UniformResourceIdentifier)
        and item.access_location.value.lower().startswith(("http://", "https://"))
    ]


def intermediate_pem(raw: bytes) -> str:
    """One CA certificate that is not self-signed, as PEM. Anything else raises ValueError.

    A self-signed certificate is refused so a fetched file can never act as a root.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding

    if raw.lstrip().startswith(b"-----BEGIN"):
        cert = x509.load_pem_x509_certificate(raw)
    else:
        cert = x509.load_der_x509_certificate(raw)
    if cert.issuer == cert.subject:
        raise ValueError("self-signed issuer refused")
    try:
        basic = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound:
        raise ValueError("issuer is not a CA") from None
    if not basic.ca:
        raise ValueError("issuer is not a CA")
    return cert.public_bytes(Encoding.PEM).decode("ascii")


def chain_context(intermediates: list[str]) -> ssl.SSLContext:
    """System roots, plus the given intermediates as untrusted chain links."""
    context = ssl.create_default_context()
    context.verify_flags &= ~getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
    context.load_verify_locations(cadata="".join(intermediates))
    return context


def _fetch_issuer(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(_ISSUER_MAX_BYTES + 1)
    if len(raw) > _ISSUER_MAX_BYTES:
        raise ValueError("issuer certificate too large")
    return raw


def issuer_context(host: str, port: int = 443) -> ssl.SSLContext | None:
    """A verifying context that adds the issuer this host left out, or None."""
    key = f"{host}:{port}"
    with _ISSUER_LOCK:
        if key in _ISSUER_CONTEXTS:
            return _ISSUER_CONTEXTS[key]
    try:
        # Read unverified only to learn the issuer URL. The request itself is
        # verified with the context built below.
        leaf = ssl.get_server_certificate((host, port), timeout=30)
        pems: list[str] = []
        for url in issuer_urls(leaf)[:2]:
            try:
                pems.append(intermediate_pem(_fetch_issuer(url)))
            except (OSError, ValueError):
                continue
    except (OSError, ValueError):
        return None
    if not pems:
        return None
    context = chain_context(pems)
    with _ISSUER_LOCK:
        _ISSUER_CONTEXTS[key] = context
    return context


def _known_context(url: str) -> ssl.SSLContext | None:
    parts = urlsplit(url)
    with _ISSUER_LOCK:
        return _ISSUER_CONTEXTS.get(f"{parts.hostname}:{parts.port or 443}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


async def urllib_get(url: str, headers: dict[str, str] | None = None, *, retry: bool = True, redirects: bool = True) -> bytes:
    """Public government URLs only. Never put a key in the URL.

    A 406 or 5xx is retried with backoff. A 429 waits for Retry-After (or
    RATE_LIMIT_WAIT_SECONDS) up to RATE_LIMIT_RETRIES times, then raises
    RateLimited. Gzip responses are unpacked.
    """
    _reject_credential_url(url)
    outgoing = request_headers(headers)

    def _open(context: ssl.SSLContext | None) -> bytes:
        request = urllib.request.Request(url, headers=outgoing)
        handlers: list[urllib.request.BaseHandler] = []
        if context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=context))
        if not redirects:
            handlers.append(_NoRedirect())
        open_url = urllib.request.build_opener(*handlers).open if handlers else urllib.request.urlopen
        with open_url(request, timeout=60) as response:
            raw = response.read()
            encoding = response.headers.get("Content-Encoding") or ""
            return _decode(raw, encoding)

    def _once() -> bytes:
        try:
            try:
                return _open(_known_context(url))
            except urllib.error.URLError as exc:
                if isinstance(exc, urllib.error.HTTPError) or not missing_issuer(exc):
                    raise
                parts = urlsplit(url)
                context = issuer_context(parts.hostname or "", parts.port or 443)
                if context is None:
                    raise
                return _open(context)
        except urllib.error.HTTPError as exc:
            if not retry:
                raise
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
