"""Supreme Court of Appeal judgments, including the Electoral Court folders.

The year index lists jDownloads categories. Category ids are sparse (2026 is
101, 2023 is 48) so they are read from the index on each run, not hard-coded.

The PDF is `/component/jdownloads/send/{category}/{id}-{slug}`. `/download/`
returns HTML and is never requested. Category pages step `?start=` by 10.
"""

from __future__ import annotations

import html
import re

from agents.zenas.pool.adapters.za_archive import (
    ArchiveRun,
    accept_pdf,
    limit_years,
    next_number,
    polite_get,
)
from agents.zenas.pool.adapters.za_case_common import (
    absolute_url,
    env_int,
    is_criminal_caption,
    neutral_citation,
    parse_case_blob,
    parse_date,
)
from agents.zenas.pool.adapters.za_common import html_headers
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


def _year_of(url: str) -> int:
    match = re.search(r"(19\d{2}|20\d{2})", url)
    return int(match.group(1)) if match else 0


class ScaAdapter:
    source_id = "sca"

    def __init__(self) -> None:
        self.run = ArchiveRun("")

    def load_note(self) -> dict:
        return self.run.load_note()

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        """Every SCA and Electoral Court year, and every page of each year.

        SCA_MAX_YEARS and SCA_MAX_PAGES still bound a run when set. Unset, the
        walk covers the whole index and stops at ZENAS_ZA_ARCHIVE_MAX.
        """
        self.run = ArchiveRun(resume)
        run = self.run
        year_cap = env_int("SCA_MAX_YEARS", 0)
        page_cap = env_int("SCA_MAX_PAGES", 0)
        grouped: list[tuple[str, str, str]] = []
        for scope, index in (("sca", SCA_INDEX), ("electoral", ELECTORAL_INDEX)):
            got = await polite_get(http_get, index, html_headers(), "SCA_MIN_INTERVAL")
            if got.challenge:
                run.skip("challenge page")
                run.hold = True
                return [], run.cursor()
            if got.error:
                run.hold = True
                return [], run.cursor()
            folders = limit_years(category_urls(got.body), year_cap, _year_of)
            for url in folders:
                grouped.append((scope, str(_year_of(url)), url))
        scope_order = list(dict.fromkeys(scope for scope, _year, _url in grouped))
        fronts: dict[str, str] = {}
        for scope, year, _url in grouped:
            if scope not in fronts or int(year or 0) > int(fronts[scope] or 0):
                fronts[scope] = year
        blobs: list[bytes] = []
        by_send: dict[str, tuple[str, str]] = {}
        for scope, year, category in grouped:
            if run.stopped or run.hold:
                break
            if run.behind(scope_order, scope, year, "end"):
                continue
            front = fronts.get(scope, year)
            start = 0
            seen_pages = 0
            while not run.stopped and not run.hold:
                if not run.caught_up_visit(year, str(start), front):
                    break
                if run.behind(scope_order, scope, year, str(start)):
                    if run.resume_scope == scope and run.resume_year == year and start < int(run.resume_page or 0):
                        start = int(run.resume_page or 0)
                        continue
                    break
                run.place(scope, year, str(start))
                if int(year or 0):
                    run.note_year(int(year))
                url = category if start == 0 else f"{category}?start={start}"
                got = await polite_get(http_get, url, html_headers(), "SCA_MIN_INTERVAL")
                seen_pages += 1
                if got.challenge:
                    run.skip("challenge page")
                    run.hold = True
                    break
                if got.error:
                    run.hold = True
                    break
                for send, title in summary_sends(got.body):
                    by_send[send] = (title, category)
                    payload = await accept_pdf(
                        run,
                        http_get,
                        send,
                        title=title,
                        body="Supreme Court of Appeal",
                        interval_env="SCA_MIN_INTERVAL",
                        headers=html_headers(),
                        citation_for=lambda file_url, text, titles=by_send: (
                            neutral_citation(titles.get(file_url, ("", ""))[0]) or neutral_citation(text)
                        ),
                        title_for=lambda file_url, text, titles=by_send: (
                            titles.get(file_url, ("", ""))[0] or text.split("\n", 1)[0][:180]
                        ),
                    )
                    if payload:
                        blobs.append(payload)
                    if run.stopped:
                        break
                if run.stopped or run.hold:
                    break
                if page_cap and seen_pages >= page_cap:
                    break
                nxt = next_number(got.body, start, kind="start")
                if nxt is None:
                    break
                start = nxt
        blobs = [blob for blob in (_retag(blob, by_send) for blob in blobs) if blob]
        run.finish()
        return blobs, run.cursor()

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
