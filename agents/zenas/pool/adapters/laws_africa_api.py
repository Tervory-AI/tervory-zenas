"""Laws.Africa v3 requests. The caller binds this client to the ZA box.

The founder supplied the API contract on 28 October 2026. No changed-since
filter or page-size parameter is documented. Only API-returned paging and
expression URLs are followed, and an authorization header never redirects.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit, urlunsplit

from agents.zenas.pool.adapters.za_common import bearer_headers, laws_africa_token, reject_credential_url
from agents.zenas.pool.http import RateLimited, retry_after_seconds, urllib_get
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.region import RegionError

API = "https://api.laws.africa/v3/"
WORKS = API + "akn/za/.json"
CONTENT_API_403 = ("HTTP 403 from api.laws.africa: the Content API for za is not on this plan; "
                   "answers use the Laws.Africa Knowledge Base")
_WORK = re.compile(r"^/akn/za/act/(?:[a-z][a-z0-9_-]*/)?[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?/[a-zA-Z0-9._-]+$")


def national_work(value: object) -> str:
    if not isinstance(value, str):
        return ""
    path = value.rstrip("/")
    return path if _WORK.fullmatch(path) else ""


def api_url(value: str) -> str:
    """No userinfo, other host/region, encoded path, or credential-bearing URL."""
    if not isinstance(value, str) or any(char.isspace() for char in value):
        raise ValueError("invalid API URL")
    url = urljoin(API, value)
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "api.laws.africa"
            or not parsed.path.startswith("/v3/akn/za/") or parsed.fragment
            or "%" in parsed.path or any(part in {".", ".."} for part in parsed.path.split("/"))):
        raise ValueError("invalid API URL")
    reject_credential_url(url)
    return url


def representation(value: str, suffix: str) -> str:
    parsed = urlsplit(api_url(value))
    path = parsed.path.rstrip("/")
    if path.endswith((".json", ".html")):
        path = path.rsplit(".", 1)[0]
    query = "coverpage=0&standalone=1&resolver=none" if suffix == ".html" else ""
    return urlunsplit((parsed.scheme, parsed.netloc, path + suffix, query, ""))


def expressions(work: dict, *, on: date) -> list[dict[str, str]]:
    """Keep the date and URL of every available point, including past editions."""
    frbr = national_work(work.get("frbr_uri"))
    if not frbr or not isinstance(work.get("points_in_time"), list):
        raise ValueError("invalid work")
    rows: dict[tuple[str, str], dict[str, str]] = {}
    for point in work["points_in_time"]:
        day = date.fromisoformat(point["date"])
        # A future point becomes eligible when its day arrives, without a filter.
        if day > on:
            continue
        for expression in point["expressions"]:
            language = expression["language"]
            if not isinstance(language, str) or not re.fullmatch(r"[a-z]{3}", language):
                raise ValueError("invalid expression language")
            url = api_url(expression["url"])
            if not urlsplit(url).path.startswith("/v3" + frbr + "/" + language):
                raise ValueError("expression belongs to another work")
            row = {"date": day.isoformat(), "language": language, "url": url}
            key = (row["date"], language)
            if key in rows and rows[key] != row:
                raise ValueError("multiple expressions for a language and date")
            rows[key] = row
    if not rows:
        raise ValueError("no current expression")
    return sorted(rows.values(), key=lambda row: (row["date"], row["language"]))


class LawsAfricaClient:
    def __init__(self, *, region: str, http_get=None):
        if region != "za":
            raise RegionError("this box does not read za law")
        # Read only after checking the box. The value is never a property or cursor.
        token = laws_africa_token()
        if not token:
            raise FetchNotConfigured("LAWS_AFRICA_API_TOKEN is not set")
        self._headers = bearer_headers(token, "application/json")
        self._http_get = None if http_get is urllib_get else http_get

    async def get(self, url: str, *, accept: str = "application/json") -> bytes:
        url = api_url(url)
        reject_credential_url(url, self._headers["Authorization"].removeprefix("Bearer "))
        headers = {**self._headers, "Accept": accept}
        for attempt in range(6):
            # Includes the first request and every retry. A run-level lock serializes
            # Load now and nightly processes on the same box.
            await asyncio.sleep(1.0)
            try:
                if self._http_get is None:
                    return await urllib_get(url, headers, retry=False, redirects=False)
                return await self._http_get(url, headers)
            except HTTPError as exc:
                code = exc.code
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                exc.close()
                if code == 403:
                    # Build plan: Knowledge Bases only. /v3/akn/za/ needs the Content API add-on.
                    raise FetchNotConfigured(CONTENT_API_403) from None
                if code != 429 and not 500 <= code <= 599:
                    raise FetchNotConfigured(f"HTTP {code} from api.laws.africa") from None
                if attempt == 5:
                    if code == 429:
                        raise RateLimited("api.laws.africa", attempt) from None
                    raise FetchNotConfigured(f"HTTP {code} from api.laws.africa") from None
                wait = retry_after_seconds(retry_after, max_wait=None) if retry_after else 2.0 ** attempt
                await asyncio.sleep(wait)
        raise AssertionError("unreachable")

    async def json(self, url: str) -> dict:
        value = json.loads(await self.get(url))
        if not isinstance(value, dict):
            raise ValueError("invalid API object")
        return value
