"""Special Tribunal judgments from justice.gov.za and the SIU site.

justice.gov.za/tribunal/jdgm.html is the historic PDF list. Affidavits and
schedules are skipped. siu.org.za/special-tribunal-judgements/ is the newer
list: each matter page links to one PDF under wp-content/uploads. Other SIU
pages (NLC, Eskom, High Court) are not followed.
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


class SpecialTribunalAdapter:
    source_id = "special-tribunal"

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        urls: list[str] = []
        reject_credential_url(JUSTICE)
        try:
            page = await http_get(JUSTICE, html_headers())
        except OSError:
            page = b""
        await case_pause("TRIBUNAL_MIN_INTERVAL")
        urls.extend(justice_pdfs(page))
        reject_credential_url(SIU)
        try:
            listing = await http_get(SIU, html_headers())
        except OSError:
            listing = b""
        await case_pause("TRIBUNAL_MIN_INTERVAL")
        for matter in siu_matter_urls(listing):
            reject_credential_url(matter)
            try:
                matter_page = await http_get(matter, html_headers())
            except OSError:
                await case_pause("TRIBUNAL_MIN_INTERVAL")
                continue
            await case_pause("TRIBUNAL_MIN_INTERVAL")
            pdf = siu_pdf(matter_page, matter)
            if pdf and pdf not in urls:
                urls.append(pdf)
        pending = after_cursor(urls, resume)[: max_docs()]
        blobs = await pdfs_to_cases(
            http_get,
            pending,
            body="Special Tribunal",
            interval_env="TRIBUNAL_MIN_INTERVAL",
            citation_for=_citation,
        )
        return blobs, (pending[-1] if pending else resume)

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_case_blob(blob, source)
