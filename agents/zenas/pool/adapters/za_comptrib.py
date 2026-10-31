"""Competition Tribunal decided cases from the official decided-cases page.

Files whose names are the confidential version are skipped. A name that says
"non confidential" is kept. Case numbers such as LM046Jun26 become the citation
when the PDF has no neutral citation.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

from agents.zenas.pool.adapters.za_archive import (
    ArchiveRun,
    accept_pdf,
    next_number,
    note_case_years,
    polite_get,
)
from agents.zenas.pool.adapters.za_case_common import (
    absolute_url,
    header_case_number,
    judgment_head,
    own_citation,
    parse_case_blob,
)
from agents.zenas.pool.adapters.za_common import html_headers
from agents.zenas.pool.model import LegalDocument, SourceSpec

INDEX = "https://www.comptrib.co.za/decided-cases"
_PDF = re.compile(r"""href=["']([^"']+\.pdf[^"']*)["']""", re.I)
_CASE = re.compile(r"\b((?:LM|CR|SA)\d{2,}[A-Za-z]{3}\d{2})\b", re.I)
# Interim relief, consent orders, exemptions and other Tribunal case codes (IR165Nov20, CO026May20).
_ANY_CASE = re.compile(r"\b([A-Z]{2,4}\d{2,4}(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\d{2})\b", re.I)
_HEAD_CODE = re.compile(
    r"\b([A-Z]{2,4}\d{2,4}(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\d{2})(?=\d?\b)", re.I
)


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
    found = own_citation(text, ("ZACT",), url=url)
    if found:
        return found
    match = _CASE.search(unquote(url))
    if match:
        return f"CompTrib {match.group(1).upper()}"
    match = _ANY_CASE.search(unquote(url).replace("_", " "))
    if match:
        return f"CompTrib {match.group(1).upper()}"
    number = header_case_number(text)
    if number:
        return f"CompTrib {number}"
    # An unlabelled case code in the header ("CR212Feb17" with a footnote mark after it).
    match = _HEAD_CODE.search(judgment_head(text))
    if match:
        return f"CompTrib {match.group(1).upper()}"
    return ""


class ComptribAdapter:
    source_id = "comptrib"

    def __init__(self) -> None:
        self.run = ArchiveRun("")

    def load_note(self) -> dict:
        return self.run.load_note()

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        """Every decided-cases page. Merger approval letters are not judgments.

        A caught-up run re-reads page 1 only. New matters are filed there;
        the older pages are not fetched again.
        """
        self.run = ArchiveRun(resume)
        run = self.run
        blobs: list[bytes] = []
        page_no = 1
        while not run.stopped and not run.hold:
            if not run.caught_up_visit("0", str(page_no), "0"):
                break
            if run.behind(["comptrib"], "comptrib", "0", str(page_no)):
                if page_no < int(run.resume_page or 1):
                    page_no = int(run.resume_page or 1)
                    continue
                break
            run.place("comptrib", "0", str(page_no))
            url = INDEX if page_no == 1 else f"{INDEX}?page={page_no}"
            got = await polite_get(http_get, url, html_headers(), "COMPTRIB_MIN_INTERVAL")
            if got.challenge:
                run.skip("challenge page")
                run.hold = True
                break
            if got.error:
                run.hold = True
                break
            for pdf in decision_pdfs(got.body):
                note_case_years(run, pdf)
                payload = await accept_pdf(
                    run,
                    http_get,
                    pdf,
                    title="",
                    body="Competition Tribunal",
                    interval_env="COMPTRIB_MIN_INTERVAL",
                    headers=html_headers(),
                    citation_for=_citation,
                )
                if payload:
                    blobs.append(payload)
                if run.stopped:
                    break
            if run.stopped or run.hold:
                break
            nxt = next_number(got.body, page_no, kind="page")
            if nxt is None:
                break
            page_no = nxt
        run.finish()
        return blobs, run.cursor()

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_case_blob(blob, source)
