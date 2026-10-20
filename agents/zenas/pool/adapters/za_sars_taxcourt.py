"""SARS Tax Court judgments. The year index links to anonymised PDFs.

Each year page lists judgment URLs. Those URLs are the PDF (they start with
`%PDF`), not an HTML wrapper. Newer bands use ``legal-drj-``; older bands use
``lapd-drj-``. SARS_MAX_YEARS unset walks every band.
"""

from __future__ import annotations

import re

from agents.zenas.pool.adapters.za_archive import ArchiveRun, accept_pdf, limit_years, polite_get
from agents.zenas.pool.adapters.za_case_common import (
    absolute_url,
    env_int,
    neutral_citation,
    parse_case_blob,
)
from agents.zenas.pool.adapters.za_common import html_headers
from agents.zenas.pool.model import LegalDocument, SourceSpec

INDEX = "https://www.sars.gov.za/legal-counsel/dispute-resolution-judgments/tax-court/"
_YEAR = re.compile(r"""href=["']([^"']*?/tax-court/tc-\d{4}-\d{4}/?)["']""", re.I)
_CASE = re.compile(
    r"""href=["'](https://www\.sars\.gov\.za/(?:legal-drj-|lapd-drj-)[^"']+)["']""",
    re.I,
)
_IT = re.compile(r"\b(it|vat)-(\d+)", re.I)
_BARE = re.compile(r"(tcit|tcvat|it|vat)(\d{2,})", re.I)
_ZATC = re.compile(r"(20\d{2})-zatc", re.I)


def year_urls(page: bytes, base: str = INDEX) -> list[str]:
    text = page.decode("utf-8", errors="replace")
    found: dict[str, int] = {}
    for href in _YEAR.findall(text):
        url = absolute_url(base, href).split("?", 1)[0]
        if not url.endswith("/"):
            url += "/"
        match = re.search(r"tc-(\d{4})-(\d{4})", url)
        if match:
            found[url] = int(match.group(2))
    return [url for url, _year in sorted(found.items(), key=lambda item: (-item[1], item[0]))]


def case_urls(page: bytes) -> list[str]:
    text = page.decode("utf-8", errors="replace")
    found: list[str] = []
    seen: set[str] = set()
    for href in _CASE.findall(text):
        url = href.split("#", 1)[0]
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def _band_year(url: str) -> int:
    """The later year in ``tc-2028-2026``. The index sorts on this edge."""
    match = re.search(r"tc-(\d{4})-(\d{4})", url)
    return int(match.group(1)) if match else 0


def _band_span(url: str) -> tuple[int, int]:
    match = re.search(r"tc-(\d{4})-(\d{4})", url)
    if not match:
        return (0, 0)
    later, earlier = int(match.group(1)), int(match.group(2))
    return (min(later, earlier), max(later, earlier))


def _citation(url: str, text: str) -> str:
    found = neutral_citation(text)
    if found:
        return found
    kind = _IT.search(url)
    year = _ZATC.search(url)
    if kind and year:
        return f"{kind.group(1).upper()} {kind.group(2)} [{year.group(1)}] ZATC"
    bare = _BARE.search(url)
    if bare and year:
        return f"{bare.group(1).upper()} {bare.group(2)} [{year.group(1)}] ZATC"
    return ""


class SarsTaxCourtAdapter:
    source_id = "sars-taxcourt"

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
        got = await polite_get(http_get, INDEX, html_headers(), "SARS_MIN_INTERVAL")
        blobs: list[bytes] = []
        if got.challenge:
            run.skip("challenge page")
            run.hold = True
            return [], run.cursor()
        if got.error:
            run.hold = True
            return [], run.cursor()
        cap = env_int("SARS_MAX_YEARS", 0)
        bands = limit_years(year_urls(got.body), cap, _band_year)
        order = [f"tc-{_band_year(url)}" for url in bands]
        front = str(_band_year((bands[-1] if cap == 0 else bands[0]) if bands else ""))
        for band in bands:
            if run.stopped or run.hold:
                break
            year = str(_band_year(band))
            scope = f"tc-{year}"
            if run.behind(order, scope, year, "end"):
                continue
            if not run.caught_up_visit(year, "0", front):
                continue
            run.place(scope, year, "0")
            for edge in _band_span(band):
                if edge:
                    run.note_year(edge)
            page = await polite_get(http_get, band, html_headers(), "SARS_MIN_INTERVAL")
            if page.challenge:
                run.skip("challenge page")
                run.hold = True
                break
            if page.error:
                run.hold = True
                break
            for url in case_urls(page.body):
                payload = await accept_pdf(
                    run,
                    http_get,
                    url,
                    title="",
                    body="Tax Court",
                    interval_env="SARS_MIN_INTERVAL",
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
