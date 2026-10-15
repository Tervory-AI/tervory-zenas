"""Supreme Court of Appeal judgments, including the Electoral Court folders.

The year index lists jDownloads categories. Category ids are sparse (2026 is
101, 2023 is 48) so they are read from the index on each run, not hard-coded.

The PDF is `/component/jdownloads/send/{category}/{id}-{slug}`. `/download/`
returns HTML and is never requested. Category pages step `?start=` by 10.
"""

from __future__ import annotations

import html
import re

from agents.zenas.pool.adapters.za_case_common import (
    absolute_url,
    after_cursor,
    case_pause,
    env_int,
    is_criminal_caption,
    neutral_citation,
    parse_case_blob,
    parse_date,
    pdfs_to_cases,
)
from agents.zenas.pool.adapters.za_common import html_headers, reject_credential_url
from agents.zenas.pool.model import LegalDocument, SourceSpec

HOST = "https://www.supremecourtofappeal.org.za"
SCA_INDEX = f"{HOST}/index.php/judgements"
ELECTORAL_INDEX = f"{HOST}/index.php/judgements/electoral-court-judgments"
_CATEGORY = re.compile(
    r"""href=["']([^"']*?/component/jdownloads/category/(\d+)-((?:judgment|judgements|judgments)-\d{4})[^"']*)["']""",
    re.I,
)
_SUMMARY = re.compile(
    r"""href=["']([^"']*?/component/jdownloads/summary/([^"'/]+)/(\d+)-[^"']*)["'][^>]*>(.*?)</a>""",
    re.I | re.S,
)
_START = re.compile(r"[?&]start=(\d+)")


def send_url(summary_href: str, base: str = HOST) -> str:
    """Turn a summary link into the PDF send link. Anything under `/download/` is refused."""
    path = absolute_url(base, summary_href).split("?", 1)[0]
    if "/component/jdownloads/summary/" not in path:
        return ""
    send = path.replace("/component/jdownloads/summary/", "/component/jdownloads/send/", 1)
    if "/download/" in send or "/component/jdownloads/send/" not in send:
        return ""
    return send


def category_urls(page: bytes, base: str = HOST) -> list[str]:
    """Year categories, newest year first. Query strings are dropped."""
    text = page.decode("utf-8", errors="replace")
    found: dict[str, int] = {}
    for href, _cat_id, slug in _CATEGORY.findall(text):
        url = absolute_url(base, href).split("?", 1)[0].rstrip("/")
        year = int(slug.rsplit("-", 1)[-1])
        found[url] = year
    return [url for url, _year in sorted(found.items(), key=lambda item: (-item[1], item[0]))]


def summary_sends(page: bytes, base: str = HOST) -> list[tuple[str, str]]:
    """`(send_url, title)` for judgment files on one category page."""
    text = page.decode("utf-8", errors="replace")
    found: dict[str, tuple[str, str]] = {}
    for href, _category, file_id, inner in _SUMMARY.findall(text):
        title = " ".join(re.sub(r"<[^>]+>", " ", html.unescape(inner)).split())
        if not title or title.lower() == "download":
            continue
        send = send_url(href, base)
        if not send:
            continue
        previous = found.get(file_id)
        if previous is None or len(title) > len(previous[1]):
            found[file_id] = (send, title)
    return list(found.values())


def next_starts(page: bytes) -> list[int]:
    text = page.decode("utf-8", errors="replace")
    return sorted({int(value) for value in _START.findall(text) if int(value) > 0})


def _court(citation: str, category_url: str) -> str:
    if "ZAEC" in citation or "electoral" in category_url.lower():
        return "Electoral Court"
    return "Supreme Court of Appeal"


class ScaAdapter:
    source_id = "sca"

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        years = env_int("SCA_MAX_YEARS", 1)
        pages = env_int("SCA_MAX_PAGES", 1)
        limit = env_int("SCA_MAX_FILES", env_int("ZA_CASE_MAX_DOCS", 8))
        categories: list[str] = []
        for index in (SCA_INDEX, ELECTORAL_INDEX):
            reject_credential_url(index)
            page = await http_get(index, html_headers())
            await case_pause("SCA_MIN_INTERVAL")
            picked = category_urls(page)[:years]
            for url in picked:
                if url not in categories:
                    categories.append(url)
        sends: list[tuple[str, str, str]] = []
        for category in categories:
            start = 0
            seen_pages = 0
            while seen_pages < pages:
                url = category if start == 0 else f"{category}?start={start}"
                reject_credential_url(url)
                page = await http_get(url, html_headers())
                await case_pause("SCA_MIN_INTERVAL")
                seen_pages += 1
                for send, title in summary_sends(page):
                    sends.append((send, title, category))
                jumps = [value for value in next_starts(page) if value > start and value % 10 == 0]
                if not jumps:
                    break
                start = jumps[0]
        pending = set(after_cursor([send for send, _title, _category in sends], resume))
        window = [(send, title, category) for send, title, category in sends if send in pending][:limit]
        chosen = [
            (send, title, category) for send, title, category in window if not is_criminal_caption(title)
        ]
        by_send = {send: (title, category) for send, title, category in chosen}
        blobs = await pdfs_to_cases(
            http_get,
            [send for send, _title, _category in chosen],
            body="Supreme Court of Appeal",
            interval_env="SCA_MIN_INTERVAL",
            citation_for=lambda url, text: neutral_citation(by_send.get(url, ("", ""))[0]) or neutral_citation(text),
            title_for=lambda url, text: by_send.get(url, ("", ""))[0] or text.split("\n", 1)[0][:180],
        )
        # pdfs_to_cases stamps every row as the SCA. Electoral rows are rewritten below.
        blobs = [_retag(blob, by_send) for blob in blobs]
        blobs = [blob for blob in blobs if blob]
        last = window[-1][0] if window else resume
        return blobs, last

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_case_blob(blob, source)


def _retag(blob: bytes, by_send: dict[str, tuple[str, str]]) -> bytes:
    import json

    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return b""
    url = str(data.get("url") or "")
    title, category = by_send.get(url, ("", ""))
    if is_criminal_caption(title or str(data.get("title") or "")):
        return b""
    data["body"] = _court(str(data.get("citation") or ""), category)
    if title:
        data["title"] = title
    when = parse_date(title) or parse_date(str(data.get("date") or ""))
    if when:
        data["date"] = when.isoformat()
    return json.dumps(data, ensure_ascii=False).encode("utf-8")
