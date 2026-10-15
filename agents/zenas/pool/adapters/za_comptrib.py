"""Competition Tribunal decided cases from the official decided-cases page.

Files whose names are the confidential version are skipped. A name that says
"non confidential" is kept. Case numbers such as LM046Jun26 become the citation
when the PDF has no neutral citation.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

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

INDEX = "https://www.comptrib.co.za/decided-cases"
_PDF = re.compile(r"""href=["']([^"']+\.pdf[^"']*)["']""", re.I)
_CASE = re.compile(r"\b((?:LM|CR|SA)\d{2,}[A-Za-z]{3}\d{2})\b", re.I)


def _keep(name: str) -> bool:
    lower = name.lower()
    if "non-confidential" in lower or "non confidential" in lower:
        return True
    if re.search(r"\bconfi\b", lower) or "confidential" in lower:
        return False
    return True


def decision_pdfs(page: bytes, base: str = "https://www.comptrib.co.za/") -> list[str]:
    text = page.decode("utf-8", errors="replace")
    found: list[str] = []
    seen: set[str] = set()
    for href in _PDF.findall(text):
        url = absolute_url(base, href).split("#", 1)[0]
        # The Tribunal links PDFs with raw spaces in the path; http.client
        # rejects those, so percent-encode while keeping existing escapes.
        url = quote(url, safe=":/?=&%#~+,;@!$'()*")
        if not _keep(unquote(url)):
            continue
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def _citation(url: str, text: str) -> str:
    found = neutral_citation(text)
    if found:
        return found
    match = _CASE.search(unquote(url))
    if match:
        return f"CompTrib {match.group(1).upper()}"
    return ""


class ComptribAdapter:
    source_id = "comptrib"

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        reject_credential_url(INDEX)
        page = await http_get(INDEX, html_headers())
        await case_pause("COMPTRIB_MIN_INTERVAL")
        urls = after_cursor(decision_pdfs(page), resume)[: max_docs()]
        blobs = await pdfs_to_cases(
            http_get,
            urls,
            body="Competition Tribunal",
            interval_env="COMPTRIB_MIN_INTERVAL",
            citation_for=_citation,
        )
        return blobs, (urls[-1] if urls else resume)

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_case_blob(blob, source)
