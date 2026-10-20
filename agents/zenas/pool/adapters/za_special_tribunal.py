"""Special Tribunal judgments from justice.gov.za and the SIU site.

justice.gov.za/tribunal/jdgm.html is the historic PDF list. Affidavits and
schedules are skipped. siu.org.za/special-tribunal-judgements/ is the newer
list: each matter page links to one PDF under wp-content/uploads. Other SIU
pages (NLC, Eskom, High Court) are not followed.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from agents.zenas.pool.adapters.za_archive import ArchiveRun, accept_pdf, polite_get
from agents.zenas.pool.adapters.za_case_common import (
    absolute_url,
    neutral_citation,
    parse_case_blob,
)
from agents.zenas.pool.adapters.za_common import html_headers
from agents.zenas.pool.model import LegalDocument, SourceSpec

JUSTICE = "https://www.justice.gov.za/tribunal/jdgm.html"
SIU = "https://www.siu.org.za/special-tribunal-judgements/"
_HREF = re.compile(r"""href=["']([^"']+)["']""", re.I)
_FILE = re.compile(r"ST[-_\s]?Judgment[-_\s]?([A-Za-z]+)-(\d+)-(\d{4})", re.I)
_SIU_NO = re.compile(r"\b(gp|ec|wc|kzn|kn|fs|lp|mp|nw)\s*0*(\d+)[_\-](20\d{2})", re.I)


def _keep_justice(name: str) -> bool:
    lower = name.lower()
    if any(piece in lower for piece in ("affidavit", "scha", "settlement")):
        return False
    return any(piece in lower for piece in ("judgment", "judgement", "jdm", "order"))


def justice_pdfs(page: bytes, base: str = "https://www.justice.gov.za/tribunal/") -> list[str]:
    text = page.decode("utf-8", errors="replace")
    found: list[str] = []
    seen: set[str] = set()
    for href in _HREF.findall(text):
        if ".pdf" not in href.lower():
            continue
        url = absolute_url(base, href).split("#", 1)[0]
        if "saflii" in url.lower() or not _keep_justice(unquote(url)):
            continue
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def siu_matter_urls(page: bytes, base: str = SIU) -> list[str]:
    text = page.decode("utf-8", errors="replace")
    found: list[str] = []
    seen: set[str] = set()
    for href in _HREF.findall(text):
        url = absolute_url(base, href).split("?", 1)[0].rstrip("/")
        path = url.lower()
        if "/judgements/" not in path:
            continue
        if not any(piece in path for piece in ("/judgment-", "/judgement-", "/order-")):
            continue
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def siu_pdf(page: bytes, base: str) -> str:
    text = page.decode("utf-8", errors="replace")
    for href in _HREF.findall(text):
        if ".pdf" not in href.lower():
            continue
        url = absolute_url(base, href).split("#", 1)[0]
        if "/wp-content/uploads/" in url.lower():
            return url
    return ""


def _citation(url: str, text: str) -> str:
    found = neutral_citation(text)
    if found:
        return found
    name = unquote(url)
    match = _FILE.search(name)
    if match:
        return f"Special Tribunal {match.group(1).upper()} {int(match.group(2))}/{match.group(3)}"
    match = _SIU_NO.search(name)
    if match:
        return f"Special Tribunal {match.group(1).upper()} {int(match.group(2))}/{match.group(3)}"
    return ""


def _next_listing(page: bytes, base: str, current: str) -> str:
    """The next SIU listing page, if this HTML has one. Matter pages are not listings."""
    text = page.decode("utf-8", errors="replace")
    current_page = 1
    match = re.search(r"/page/(\d+)/?$", current.rstrip("/"))
    if match:
        current_page = int(match.group(1))
    pages = [int(value) for value in re.findall(r"/page/(\d+)/?", text) if int(value) > current_page]
    if not pages:
        return ""
    nxt = min(pages)
    return absolute_url(base, f"page/{nxt}/")


class SpecialTribunalAdapter:
    source_id = "special-tribunal"

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
        blobs: list[bytes] = []
        justice = await polite_get(http_get, JUSTICE, html_headers(), "TRIBUNAL_MIN_INTERVAL")
        if justice.challenge:
            run.skip("challenge page")
            run.hold = True
        elif not justice.error:
            run.place("justice", "0", "0")
            for url in justice_pdfs(justice.body):
                payload = await accept_pdf(
                    run,
                    http_get,
                    url,
                    title="",
                    body="Special Tribunal",
                    interval_env="TRIBUNAL_MIN_INTERVAL",
                    headers=html_headers(),
                    citation_for=_citation,
                )
                if payload:
                    blobs.append(payload)
                if run.stopped:
                    break
        if not run.stopped and not run.hold:
            listing_url = SIU
            seen_listings: set[str] = set()
            page_no = 1
            while listing_url and listing_url not in seen_listings and not run.stopped and not run.hold:
                seen_listings.add(listing_url)
                if run.resume_done and page_no > 1:
                    break
                run.place("siu", "0", str(page_no))
                listing = await polite_get(http_get, listing_url, html_headers(), "TRIBUNAL_MIN_INTERVAL")
                if listing.challenge:
                    run.skip("challenge page")
                    run.hold = True
                    break
                if listing.error:
                    run.hold = True
                    break
                for matter in siu_matter_urls(listing.body):
                    matter_page = await polite_get(http_get, matter, html_headers(), "TRIBUNAL_MIN_INTERVAL")
                    if matter_page.challenge:
                        run.skip("challenge page")
                        run.mark(matter)
                        continue
                    if matter_page.error or not matter_page.body:
                        continue
                    pdf = siu_pdf(matter_page.body, matter)
                    if not pdf:
                        continue
                    payload = await accept_pdf(
                        run,
                        http_get,
                        pdf,
                        title="",
                        body="Special Tribunal",
                        interval_env="TRIBUNAL_MIN_INTERVAL",
                        headers=html_headers(),
                        citation_for=_citation,
                    )
                    if payload:
                        blobs.append(payload)
                    if run.stopped:
                        break
                nxt = _next_listing(listing.body, listing_url, listing_url)
                page_no += 1
                listing_url = nxt
        run.finish()
        return blobs, run.cursor()

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_case_blob(blob, source)
