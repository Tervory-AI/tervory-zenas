"""Competition Appeal Court judgments published as PDFs on cac.org.za.

The judgments page lists the files. The comments RSS at `/judgments/feed/`
and the sitemap index do not list those PDFs, so they are not the source.
Rules and practice notes on the same page are skipped.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from agents.zenas.pool.adapters.za_case_common import (
    absolute_url,
    after_cursor,
    case_pause,
    max_docs,
    neutral_citation,
    parse_case_blob,
    pdfs_to_cases,
)
from agents.zenas.pool.adapters.za_common import html_headers, reject_credential_url
from agents.zenas.pool.model import LegalDocument, SourceSpec

INDEX = "https://www.cac.org.za/judgments/"
_PDF = re.compile(r"""href=["']([^"']+\.pdf[^"']*)["']""", re.I)
_CASE_NO = re.compile(r"(\d{2,4})\s*\.?\s*CAC", re.I)


def judgment_pdfs(page: bytes, base: str = INDEX) -> list[str]:
    text = page.decode("utf-8", errors="replace")
    found: list[str] = []
    seen: set[str] = set()
    for href in _PDF.findall(text):
        url = absolute_url(base, href).split("#", 1)[0]
        name = unquote(url.split("?", 1)[0]).lower()
        if "practice-note" in name or "practice_note" in name or name.endswith("rules.pdf"):
            continue
        if "/wp-content/uploads/" not in name and not name.endswith(".pdf"):
            continue
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def _citation(url: str, text: str) -> str:
    found = neutral_citation(text) or neutral_citation(unquote(url))
    if found:
        return found
    match = _CASE_NO.search(unquote(url))
    if match:
        return f"CAC {match.group(1)}"
    return ""


class CacAdapter:
    source_id = "cac"

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        reject_credential_url(INDEX)
        page = await http_get(INDEX, html_headers())
        await case_pause("CAC_MIN_INTERVAL")
        urls = after_cursor(judgment_pdfs(page), resume)[: max_docs()]
        blobs = await pdfs_to_cases(
            http_get,
            urls,
            body="Competition Appeal Court",
            interval_env="CAC_MIN_INTERVAL",
            citation_for=_citation,
        )
        return blobs, (urls[-1] if urls else resume)

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_case_blob(blob, source)
