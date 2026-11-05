"""URL builders and the polite HTTP client. Fixture runs do not call it."""

from __future__ import annotations

import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Callable

from feeds.geo import US_POSTAL

USER_AGENT = "Tervory Market Lab feeds (+https://app.tervory.com)"
DEFAULT_DELAY = 2.0
DEFAULT_TIMEOUT = 60.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 1.0

_ANCHOR = re.compile(
    r"<a\s[^>]*href=(['\"])(.*?)\1[^>]*>(.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_LISTING_STAMP = re.compile(r"(\d{2})(\d{2})-ffl-list", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


class NeedsKey(RuntimeError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"needs key {name}")


class BlockedSite(RuntimeError):
    """An access boundary, never a retryable transport failure."""

    def __init__(self, host: str) -> None:
        self.host = host
        super().__init__("blocked")


def login_boundary(url: str) -> bool:
    parts = urllib.parse.urlsplit(url).path.lower().split("/")
    return any(part in {"login", "log-in", "signin", "sign-in", "subscribe", "paywall", "captcha"} for part in parts)


def blocked_page(body: bytes) -> bool:
    text = body[:65536].decode("utf-8", errors="ignore").lower()
    markers = ("g-recaptcha", "h-captcha", "cf-chl-", "cf_chl_", "challenge-platform",
               "verify you are human", "verify that you are human", "captcha challenge",
               "subscribe to continue", "subscribe to read", "sign in to continue", "access denied")
    return any(marker in text for marker in markers) or bool(re.search(r'<input[^>]+type\s*=\s*[\'"]?password', text))


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise NeedsKey(name)
    return value


def safe_failure(url: str, exc: BaseException) -> str:
    """Host and status only. Query strings can carry a key."""
    host = urllib.parse.urlsplit(url).hostname or "host"
    code = getattr(exc, "code", None)
    kind = type(exc).__name__
    if isinstance(code, int):
        return f"{kind}: HTTP {code} from {host}"
    return f"{kind}: request to {host} failed"


def _retryable(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code in {500, 502, 503, 504}
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError))


class PoliteClient:
    """Per-domain delay, three tries, backoff, and a named User-Agent."""

    def __init__(
        self,
        *,
        delay: float = DEFAULT_DELAY,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        backoff: float = DEFAULT_BACKOFF,
        opener: Callable[..., object] | None = None,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        self._last: dict[str, float] = {}
        self.blocked_hosts: set[str] = set()

    def ensure_open(self, url: str) -> None:
        host = urllib.parse.urlsplit(url).hostname or ""
        if login_boundary(url):
            self.blocked_hosts.add(host)
        if host in self.blocked_hosts:
            raise BlockedSite(host)

    def _check_block(self, url: str, *, status: int = 200, body: bytes = b"", error: BaseException | None = None) -> None:
        host = urllib.parse.urlsplit(url).hostname or ""
        # A denied CONNECT can arrive wrapped in URLError. Never retry it as
        # an ordinary transport error, and never include its message in logs.
        if isinstance(error, urllib.error.HTTPError) and error.code not in {401, 402, 403, 429}:
            try:
                body = error.read(65536)
            except Exception:
                body = b""
        denied_proxy = isinstance(error, urllib.error.URLError) and bool(re.search(r"\b(?:401|402|403|429)\b", str(error.reason)))
        if isinstance(error, BlockedSite) or status in {401, 402, 403, 429} or denied_proxy or blocked_page(body):
            self.blocked_hosts.add(host)
            if isinstance(error, BlockedSite):
                self.blocked_hosts.add(error.host)
            elif isinstance(error, urllib.error.HTTPError):
                self.blocked_hosts.add(urllib.parse.urlsplit(error.url).hostname or host)
            raise BlockedSite(host) from None

    def _headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        merged = dict(extra or {})
        merged["User-Agent"] = USER_AGENT
        return merged

    def _wait(self, host: str) -> None:
        if self.delay <= 0:
            self._last[host] = self.clock()
            return
        previous = self._last.get(host)
        now = self.clock()
        if previous is not None:
            gap = self.delay - (now - previous)
            if gap > 0:
                self.sleep(gap)
                now = self.clock()
        self._last[host] = now

    def get(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        self.ensure_open(url)
        host = urllib.parse.urlsplit(url).hostname or ""
        last: BaseException | None = None
        for attempt in range(self.retries):
            self._wait(host)
            request = urllib.request.Request(url, headers=self._headers(headers))
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    status = getattr(response, "status", 200) or 200
                    self._check_block(url, status=status)
                    if status >= 400:
                        raise urllib.error.HTTPError(url, status, "bad status", None, None)
                    body = response.read()
                    self._check_block(url, body=body)
                return body
            except Exception as exc:
                self._check_block(url, status=getattr(exc, "code", 0), error=exc)
                last = exc
                if attempt + 1 >= self.retries or not _retryable(exc):
                    raise RuntimeError(safe_failure(url, exc)) from None
                self.sleep(self.backoff * (2**attempt))
        raise RuntimeError(safe_failure(url, last or RuntimeError("failed"))) from None

    def get_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        return self.get(url, headers=headers).decode("utf-8")

    def iter_lines(self, url: str, headers: dict[str, str] | None = None) -> Iterator[str]:
        """Stream one response. A failure before the first line is retried."""
        self.ensure_open(url)
        host = urllib.parse.urlsplit(url).hostname or ""
        last: BaseException | None = None
        for attempt in range(self.retries):
            self._wait(host)
            request = urllib.request.Request(url, headers=self._headers(headers))
            try:
                response = self.opener(request, timeout=self.timeout)
            except Exception as exc:
                self._check_block(url, status=getattr(exc, "code", 0), error=exc)
                last = exc
                if attempt + 1 >= self.retries or not _retryable(exc):
                    raise RuntimeError(safe_failure(url, exc)) from None
                self.sleep(self.backoff * (2**attempt))
                continue
            started = False
            try:
                status = getattr(response, "status", 200) or 200
                self._check_block(url, status=status)
                if status >= 400:
                    raise urllib.error.HTTPError(url, status, "bad status", None, None)
                prefix = b""
                for raw in response:
                    if len(prefix) < 65536:
                        prefix += raw if isinstance(raw, bytes) else str(raw).encode()
                        self._check_block(url, body=prefix)
                    started = True
                    if isinstance(raw, bytes):
                        yield raw.decode("utf-8", "replace")
                    else:
                        yield str(raw)
                return
            except Exception as exc:
                self._check_block(url, status=getattr(exc, "code", 0), error=exc)
                last = exc
                if started or attempt + 1 >= self.retries or not _retryable(exc):
                    raise RuntimeError(safe_failure(url, exc)) from None
                self.sleep(self.backoff * (2**attempt))
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        raise RuntimeError(safe_failure(url, last or RuntimeError("failed"))) from None


def fetch_text(url: str, headers: dict[str, str] | None = None) -> str:
    return PoliteClient().get_text(url, headers=headers)


def listing_stamp(blob: str) -> str | None:
    match = _LISTING_STAMP.search(blob)
    if match is None:
        return None
    month = int(match.group(1))
    year = 2000 + int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return f"{year:04d}-{month:02d}"


def complete_listing_link(html: str, page_url: str) -> tuple[str, str] | None:
    """Return (YYYY-MM, absolute txt URL) for the complete list on one results page."""
    fallback: tuple[str, str] | None = None
    for match in _ANCHOR.finditer(html):
        href = match.group(2)
        text = _TAG.sub("", match.group(3))
        blob = f"{href} {text}"
        if "complete" not in blob.lower() or ".txt" not in blob.lower():
            continue
        stamp = listing_stamp(blob)
        if stamp is None:
            continue
        url = urllib.parse.urljoin(page_url, urllib.parse.unquote(html_unescape(href)))
        if href.lower().split("?", 1)[0].endswith(".txt"):
            return stamp, url
        fallback = (stamp, url)
    return fallback


def html_unescape(text: str) -> str:
    return (
        text.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def census_population_urls(key: str, years: list[int]) -> list[tuple[str, str]]:
    urls: list[tuple[str, str]] = []
    for year in years:
        base = f"https://api.census.gov/data/{year}/acs/acs5"
        state = (
            f"{base}?get=NAME,B01003_001E&for=state:*&key="
            + urllib.parse.quote(key)
        )
        county = (
            f"{base}?get=NAME,B01003_001E&for=county:*&in=state:*&key="
            + urllib.parse.quote(key)
        )
        urls.append((f"acs5-{year}-state", state))
        urls.append((f"acs5-{year}-county", county))
    return urls


def census_trade_urls(key: str, months: list[str], codes: list[str], flow: str) -> list[tuple[str, str]]:
    path = "imports" if flow == "import" else "exports"
    value = "GEN_VAL_MO" if flow == "import" else "ALL_VAL_MO"
    commodity = "I_COMMODITY" if flow == "import" else "E_COMMODITY"
    urls: list[tuple[str, str]] = []
    for month in months:
        for code in codes:
            query = urllib.parse.urlencode(
                {
                    "get": f"{commodity},CTY_NAME,{value},DIST_NAME,time",
                    "COMM_LVL": "HS6",
                    "time": month,
                    commodity: code,
                    "key": key,
                }
            )
            url = f"https://api.census.gov/data/timeseries/intltrade/{path}/hs?{query}"
            urls.append((f"{flow}-{month}-{code}", url))
    return urls


def legiscan_urls(key: str, topics: list[str], states: tuple[str, ...] = US_POSTAL) -> list[tuple[str, str]]:
    urls: list[tuple[str, str]] = []
    for state in states:
        for topic in topics:
            query = urllib.parse.urlencode(
                {"key": key, "op": "getSearch", "state": state, "query": topic}
            )
            urls.append((f"{state}-{topic}", f"https://api.legiscan.com/?{query}"))
    return urls


def federal_register_url(agencies: list[str]) -> str:
    pairs = [("conditions[agencies][]", agency) for agency in agencies]
    pairs.append(("per_page", "100"))
    pairs.append(("order", "newest"))
    return "https://www.federalregister.gov/api/v1/documents.json?" + urllib.parse.urlencode(pairs)


def ecfr_structure_url(title: str) -> str:
    return f"https://www.ecfr.gov/api/versioner/v1/structure/current/title-{title}.json"


def eia_diesel_url(key: str, product: str, area: str) -> str:
    query = urllib.parse.urlencode(
        {
            "frequency": "weekly",
            "data[0]": "value",
            "facets[product][]": product,
            "facets[duoarea][]": area,
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": "5000",
            "api_key": key,
        }
    )
    return f"https://api.eia.gov/v2/petroleum/pri/gnd/data/?{query}"


def world_bank_url(indicator: str) -> str:
    return (
        "https://api.worldbank.org/v2/country/WLD/indicator/"
        f"{urllib.parse.quote(indicator)}?format=json&per_page=20000"
    )


def nws_alerts_url() -> str:
    return "https://api.weather.gov/alerts/active?status=actual"


def ncei_url(datatype: str, state_fips: str, start: str, end: str) -> str:
    query = urllib.parse.urlencode(
        {
            "datasetid": "GSOM",
            "datatypeid": datatype,
            "locationid": f"FIPS:{state_fips}",
            "startdate": start,
            "enddate": end,
            "units": "standard",
            "limit": "1000",
        }
    )
    return f"https://www.ncei.noaa.gov/cdo-web/api/v2/data?{query}"
