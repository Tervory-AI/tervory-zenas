"""Competition Appeal Court judgments published as PDFs on cac.org.za.

The judgments page lists the files. The comments RSS at `/judgments/feed/`
and the sitemap index do not list those PDFs, so they are not the source.
Rules and practice notes on the same page are skipped.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from agents.zenas.pool.adapters.za_archive import ArchiveRun, accept_pdf, polite_get
from agents.zenas.pool.adapters.za_case_common import (
    absolute_url,
    header_case_number,
    own_citation,
    parse_case_blob,
)
from agents.zenas.pool.adapters.za_common import html_headers
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
    # The CAC also hosts the Constitutional Court judgments on its appeals.
    found = own_citation(text, ("ZACAC", "ZACC"), url=url)
    if found:
        return found
    match = _CASE_NO.search(unquote(url))
    if match:
        return f"CAC {match.group(1)}"
    number = re.match(r"(\d+)\s*/\s*CAC", header_case_number(text))
    if number:
        return f"CAC {number.group(1)}"
    return ""


class CacAdapter:
    source_id = "cac"

    def __init__(self) -> None:
        self.run = ArchiveRun("")

    def load_note(self) -> dict:
        return self.run.load_note()

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        """The judgments page is the whole archive. The pager in the browser is not a second request."""
        self.run = ArchiveRun(resume)
        run = self.run
        got = await polite_get(http_get, INDEX, html_headers(), "CAC_MIN_INTERVAL")
        blobs: list[bytes] = []
        if got.challenge:
            run.skip("challenge page")
            run.hold = True
            return [], run.cursor()
        if got.error:
            run.hold = True
            return [], run.cursor()
        run.place("cac", "0", "0")
        kept = judgment_pdfs(got.body)
        text = got.body.decode("utf-8", errors="replace")
        for href in _PDF.findall(text):
            url = absolute_url(INDEX, href).split("#", 1)[0]
            name = unquote(url).lower()
            if url not in kept and ("practice" in name or name.endswith("rules.pdf")):
                run.skip("not a judgment")
                run.mark(url)
        for url in kept:
            match = re.search(r"/uploads/((?:19|20)\d{2})/", url)
            if match:
                run.note_year(int(match.group(1)))
            payload = await accept_pdf(
                run,
                http_get,
                url,
                title="",
                body="Competition Appeal Court",
                interval_env="CAC_MIN_INTERVAL",
                headers=html_headers(),
                citation_for=_citation,
            )
            if payload:
                blobs.append(payload)
            if run.stopped:
                break
        run.finish()
        return blobs, run.cursor()

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_case_blob(blob, source)
