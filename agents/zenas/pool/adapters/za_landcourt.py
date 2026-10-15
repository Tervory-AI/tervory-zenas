"""Land Court judgments on the judiciary.org.za Land Court site.

The judgments library lists year categories. Court rolls on the same host
are left out. Files are Phoca `?download=` PDFs.
"""

from __future__ import annotations

import re

from agents.zenas.pool.adapters.za_case_common import (
    absolute_url,
    after_cursor,
    case_pause,
    env_int,
    max_docs,
    neutral_citation,
    parse_case_blob,
    pdfs_to_cases,
    phoca_downloads,
)
from agents.zenas.pool.adapters.za_common import html_headers, reject_credential_url
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
        match = re.search(r"(20\d{2})", url)
        found[url] = int(match.group(1)) if match else 0
    return [url for url, _year in sorted(found.items(), key=lambda item: (-item[1], item[0]))]


class LandCourtAdapter:
    source_id = "landcourt"

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        reject_credential_url(INDEX)
        page = await http_get(INDEX, html_headers())
        await case_pause("LAND_MIN_INTERVAL")
        files = phoca_downloads(page, INDEX)
        for category in category_urls(page)[: env_int("LAND_MAX_YEARS", 1)]:
            reject_credential_url(category)
            folder = await http_get(category, html_headers())
            await case_pause("LAND_MIN_INTERVAL")
            for url in phoca_downloads(folder, category):
                if url not in files:
                    files.append(url)
        urls = after_cursor(files, resume)[: max_docs()]
        blobs = await pdfs_to_cases(
            http_get,
            urls,
            body="Land Court",
            interval_env="LAND_MIN_INTERVAL",
            citation_for=lambda url, text: neutral_citation(text),
        )
        return blobs, (urls[-1] if urls else resume)

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_case_blob(blob, source)
