"""Land Court judgments on the judiciary.org.za Land Court site.

The judgments library lists year categories. Court rolls on the same host
are left out. Files are Phoca `?download=` PDFs.
"""

from __future__ import annotations

import re

from agents.zenas.pool.adapters.za_archive import (
    ArchiveRun,
    accept_pdf,
    limit_years,
    paged_listings,
    polite_get,
)
from agents.zenas.pool.adapters.za_case_common import (
    absolute_url,
    env_int,
    neutral_citation,
    parse_case_blob,
    phoca_downloads,
)
from agents.zenas.pool.adapters.za_common import html_headers
from agents.zenas.pool.model import LegalDocument, SourceSpec

INDEX = (
    "https://www.judiciary.org.za/landcourt/index.php/"
    "land-court-library/judgements/land-court-judgements"
)


def category_urls(page: bytes, base: str = INDEX) -> list[str]:
    text = page.decode("utf-8", errors="replace")
    found: dict[str, int] = {}
    for href in re.findall(r"""href=["']([^"']+)["']""", text, re.I):
        if "download=" in href or "court-roll" in href.lower():
            continue
        url = absolute_url(base, href).split("?", 1)[0].rstrip("/")
        if "/land-court-judgements/category/" not in url:
            continue
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        if re.search(r"report|minute|media|directive", slug, re.I):
            continue
        match = re.search(r"(20\d{2})", url)
        found[url] = int(match.group(1)) if match else 0
    return [url for url, _year in sorted(found.items(), key=lambda item: (-item[1], item[0]))]


def _year_of(url: str) -> int:
    match = re.search(r"(20\d{2}|19\d{2})", url)
    return int(match.group(1)) if match else 0


class LandCourtAdapter:
    source_id = "landcourt"

    def __init__(self) -> None:
        self.run = ArchiveRun("")

    def load_note(self) -> dict:
        return self.run.load_note()

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        self.run = ArchiveRun(resume)
        run = self.run
        got = await polite_get(http_get, INDEX, html_headers(), "LAND_MIN_INTERVAL")
        blobs: list[bytes] = []
        if got.challenge:
            run.skip("challenge page")
            run.hold = True
            return [], run.cursor()
        if got.error:
            run.hold = True
            return [], run.cursor()
        cap = env_int("LAND_MAX_YEARS", 0)
        folders = limit_years(category_urls(got.body), cap, _year_of)
        scopes = ["index", *folders]
        front = str(_year_of(max(folders, key=_year_of))) if folders else "0"
        if not run.behind(scopes, "index", "0", "end"):
            run.place("index", "0", "0")
            blobs.extend(await _files(run, http_get, got.body, INDEX))
        for folder in folders:
            if run.stopped or run.hold:
                break
            year = str(_year_of(folder) or 0)
            if run.behind(scopes, folder, year, "end"):
                continue
            async for page in paged_listings(
                run,
                http_get,
                folder,
                scope=folder,
                year=year,
                scope_order=scopes,
                front_year=front,
                interval_env="LAND_MIN_INTERVAL",
                headers=html_headers(),
            ):
                blobs.extend(await _files(run, http_get, page, folder))
                if run.stopped:
                    break
            if run.stopped:
                break
        run.finish()
        return blobs, run.cursor()

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_case_blob(blob, source)


async def _files(run: ArchiveRun, http_get, page: bytes, base: str) -> list[bytes]:
    blobs: list[bytes] = []
    for url in phoca_downloads(page, base):
        if "directive" in url.lower():
            if not run.already(url):
                run.skip("not a judgment")
                run.mark(url)
            continue
        payload = await accept_pdf(
            run,
            http_get,
            url,
            title="",
            body="Land Court",
            interval_env="LAND_MIN_INTERVAL",
            headers=html_headers(),
            citation_for=lambda file_url, text: neutral_citation(text),
        )
        if payload:
            blobs.append(payload)
        if run.stopped:
            break
    return blobs
