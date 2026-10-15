"""Labour Court and Labour Appeal Court judgments on judiciary.org.za.

The index links to year folders. The 2025 Labour Court folder and the Labour
Appeal Court year folders are live. Each folder is a Phoca `?download=` list
of PDFs. Court rolls are not collected.
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
    "https://www.judiciary.org.za/index.php/courts/specialised-courts/"
    "labour-and-labour-appeal-court/court-judgments"
)
LC_MARKER = "/judgements-labour-court/"
LAC_MARKER = "/judgements-labour-court-2/"


def year_folders(page: bytes, marker: str, base: str = INDEX) -> list[str]:
    """Year folder URLs for one court, newest first."""
    text = page.decode("utf-8", errors="replace")
    found: dict[str, int] = {}
    for href in re.findall(r"""href=["']([^"']+)["']""", text, re.I):
        url = absolute_url(base, href).split("?", 1)[0].rstrip("/")
        if marker not in url or "court-rolls" in url:
            continue
        match = re.search(r"(20\d{2})", url)
        if not match:
            continue
        found[url] = int(match.group(1))
    return [url for url, _year in sorted(found.items(), key=lambda item: (-item[1], item[0]))]


class _LabourFamily:
    source_id = ""
    marker = ""
    body = ""

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        reject_credential_url(INDEX)
        index = await http_get(INDEX, html_headers())
        await case_pause("LABOUR_MIN_INTERVAL")
        folders = year_folders(index, self.marker)[: env_int("LABOUR_MAX_YEARS", 1)]
        files: list[str] = []
        for folder in folders:
            reject_credential_url(folder)
            page = await http_get(folder, html_headers())
            await case_pause("LABOUR_MIN_INTERVAL")
            for url in phoca_downloads(page, folder):
                if url not in files:
                    files.append(url)
        urls = after_cursor(files, resume)[: max_docs()]
        blobs = await pdfs_to_cases(
            http_get,
            urls,
            body=self.body,
            interval_env="LABOUR_MIN_INTERVAL",
            citation_for=lambda url, text: neutral_citation(text),
        )
        return blobs, (urls[-1] if urls else resume)

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_case_blob(blob, source)


class LabourCourtAdapter(_LabourFamily):
    source_id = "labour-court"
    marker = LC_MARKER
    body = "Labour Court"


class LabourAppealAdapter(_LabourFamily):
    source_id = "labour-appeal"
    marker = LAC_MARKER
    body = "Labour Appeal Court"
