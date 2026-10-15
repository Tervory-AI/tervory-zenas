"""SARS Tax Court judgments. The year index links to anonymised PDFs.

Each year page lists judgment URLs. Those URLs are the PDF (they start with
`%PDF`), not an HTML wrapper. Only the newest year band is fetched unless
SARS_MAX_YEARS is raised.
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
)
from agents.zenas.pool.adapters.za_common import html_headers, reject_credential_url
from agents.zenas.pool.model import LegalDocument, SourceSpec

INDEX = "https://www.sars.gov.za/legal-counsel/dispute-resolution-judgments/tax-court/"
_YEAR = re.compile(r"""href=["']([^"']*?/tax-court/tc-\d{4}-\d{4}/?)["']""", re.I)
_CASE = re.compile(r"""href=["'](https://www\.sars\.gov\.za/legal-drj-[^"']+)["']""", re.I)
_IT = re.compile(r"\b(it|vat)-(\d+)", re.I)
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


def _citation(url: str, text: str) -> str:
    found = neutral_citation(text)
    if found:
        return found
    kind = _IT.search(url)
    year = _ZATC.search(url)
    if kind and year:
        return f"{kind.group(1).upper()} {kind.group(2)} [{year.group(1)}] ZATC"
    return ""


class SarsTaxCourtAdapter:
    source_id = "sars-taxcourt"

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        reject_credential_url(INDEX)
        index = await http_get(INDEX, html_headers())
        await case_pause("SARS_MIN_INTERVAL")
        years = year_urls(index)[: env_int("SARS_MAX_YEARS", 1)]
        cases: list[str] = []
        for year in years:
            reject_credential_url(year)
            page = await http_get(year, html_headers())
            await case_pause("SARS_MIN_INTERVAL")
            for url in case_urls(page):
                if url not in cases:
                    cases.append(url)
        urls = after_cursor(cases, resume)[: max_docs()]
        blobs = await pdfs_to_cases(
            http_get,
            urls,
            body="Tax Court",
            interval_env="SARS_MIN_INTERVAL",
            citation_for=_citation,
        )
        return blobs, (urls[-1] if urls else resume)

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_case_blob(blob, source)
