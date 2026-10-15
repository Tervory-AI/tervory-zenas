"""Government Gazette notices and the two sets of court rules.

Notices are HTML from gov.za, discovered on the notices index and on rss.xml.
PDF notice files are still left out. Court rules load from official Gazette
PDFs. A rules file whose name points at SAFLII is refused. HTML snapshots of
a rule still parse.
"""

from __future__ import annotations

import os
import re
from datetime import date
from html.parser import HTMLParser

from agents.zenas.pool.adapters.za_case_common import pdf_text
from agents.zenas.pool.adapters.za_common import html_headers, pause, pdf_headers, reject_credential_url
from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec

GAZETTE_INDEX = "https://www.gov.za/documents/notices"
GAZETTE_RSS = "https://www.gov.za/rss.xml"
# The consolidated Uniform Rules file on justice.gov.za has SAFLII in the filename.
# The live file is the Government Gazette amendment instead.
UNIFORM_RULES_URL = "https://www.gov.za/sites/default/files/gcis_document/202510/53572rg11899gon6753.pdf"
MAGISTRATES_RULES_URL = (
    "https://www.justice.gov.za/legislation/rules/Rules-MagistrateCourts-GG33487-GNR740-20100823.pdf"
)
RULES_INDEX = "https://www.justice.gov.za/legislation/rules/rules.htm"

_HREF = re.compile(r"""href=["']([^"']+)["']""", re.I)
_LINK = re.compile(r"<link>([^<]+)</link>", re.I)
_GG = re.compile(r"\bGG\s*\d{4,6}\b", re.I)
_NOTICE = re.compile(
    r"\bGN\s+R?\.?\s*\d+\s+in\s+GG\s+\d+\s+of\s+\d{1,2}\s+[A-Za-z]+\s+\d{4}\b",
    re.I,
)
_RULE_HEAD = re.compile(r"^Rule\s+(\d+[A-Za-z]?)(?:\s*[.\u2014:-]\s*(.*))?$", re.I)
_LONG_DATE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b")
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _max_notices() -> int:
    raw = os.environ.get("GOVZA_MAX_NOTICES", "10").strip()
    try:
        return max(0, int(raw or "10"))
    except ValueError:
        return 10


class _Blocks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capture: str | None = None
        self._chunks: list[str] = []
        self.blocks: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"h1", "h2", "p"}:
            self._capture = tag.lower()
            self._chunks = []

    def handle_endtag(self, tag: str) -> None:
        if self._capture and tag.lower() == self._capture:
            text = " ".join("".join(self._chunks).split())
            if text:
                self.blocks.append((self._capture, text))
            self._capture = None
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._chunks.append(data)


def _blocks(blob: bytes) -> list[tuple[str, str]]:
    parser = _Blocks()
    parser.feed(blob.decode("utf-8", errors="replace"))
    return parser.blocks


def _date_in(text: str) -> date | None:
    match = _LONG_DATE.search(text)
    if not match:
        return None
    month = _MONTHS.get(match.group(2).lower())
    if not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(1)))
    except ValueError:
        return None


def _notice_url(href: str) -> str:
    href = href.split("#", 1)[0].strip()
    lowered = href.lower()
    if not href or lowered.endswith((".pdf", ".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff")):
        return ""
    if "documents" not in lowered and "gazette" not in lowered and "notice" not in lowered:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        url = href
    elif href.startswith("/"):
        url = "https://www.gov.za" + href
    else:
        return ""
    if "gov.za" not in url:
        return ""
    if url.rstrip("/") in {GAZETTE_INDEX.rstrip("/"), GAZETTE_RSS.rstrip("/")}:
        return ""
    return url


def notice_links(page: bytes) -> list[str]:
    """HTML hrefs and RSS `<link>` entries for notice pages. PDF copies are left out."""
    text = page.decode("utf-8", errors="replace")
    found: list[str] = []
    seen: set[str] = set()
    candidates = [match.group(1) for match in _HREF.finditer(text)]
    candidates.extend(match.group(1) for match in _LINK.finditer(text))
    for href in candidates:
        url = _notice_url(href)
        if url and url not in seen:
            seen.add(url)
            found.append(url)
    return found


def official_rule_pdfs(page: bytes, base: str = RULES_INDEX) -> list[str]:
    """PDF hrefs on the rules index. A path or filename that says SAFLII is skipped."""
    from agents.zenas.pool.adapters.za_case_common import absolute_url

    text = page.decode("utf-8", errors="replace")
    found: list[str] = []
    seen: set[str] = set()
    for match in _HREF.finditer(text):
        href = match.group(1).split("#", 1)[0].strip()
        if not href.lower().split("?", 1)[0].endswith(".pdf"):
            continue
        url = absolute_url(base, href)
        if "saflii" in url.lower() or url in seen:
            continue
        seen.add(url)
        found.append(url)
    return found


def reject_saflii_rules(url: str) -> None:
    reject_credential_url(url)
    if "saflii" in url.lower():
        raise FetchNotConfigured("refusing a court-rules file that points at SAFLII")


def parse_notice(blob: bytes, source: SourceSpec) -> list[LegalDocument]:
    blocks = _blocks(blob)
    if not blocks:
        return []
    text = " ".join(piece for _tag, piece in blocks)
    found = _NOTICE.search(text)
    if not found:
        return []
    citation = " ".join(found.group(0).split())
    title = next((piece for tag, piece in blocks if tag == "h1"), citation)
    paragraphs = [piece for tag, piece in blocks if tag == "p" and piece != citation]
    if not paragraphs:
        return []
    noticed = _date_in(citation) or _date_in(text)
    return [
        build_document(
            source,
            citation=citation,
            title=title,
            level="national",
            kind="guidance",
            body="Government of South Africa",
            sections=[Section("", title, piece, []) for piece in paragraphs],
            url=source.canonical_url,
            status="published",
            doc_date=noticed,
            effective_from=noticed,
        )
    ]


def parse_rules(blob: bytes, source: SourceSpec, *, prefix: str) -> list[LegalDocument]:
    blocks = _blocks(blob)
    documents: list[LegalDocument] = []
    current_num = ""
    current_heading = ""
    current_parts: list[str] = []

    def flush() -> None:
        if not current_num or not current_parts:
            return
        citation = f"{prefix} {current_num}"
        body = " ".join(current_parts)
        documents.append(
            build_document(
                source,
                citation=citation,
                title=current_heading or citation,
                level="national",
                kind="court_rule",
                body="Rules Board for Courts of Law",
                sections=[Section(current_num, current_heading, body, [])],
                url=source.canonical_url,
                status="in_force",
                doc_date=None,
                effective_from=None,
            )
        )

    for tag, piece in blocks:
        if tag == "h2":
            match = _RULE_HEAD.match(piece)
            if match:
                flush()
                current_num = match.group(1)
                current_heading = (match.group(2) or "").strip() or piece
                current_parts = []
                continue
        if current_num and tag == "p":
            current_parts.append(piece)
    flush()
    return documents


def parse_rules_text(blob: bytes, source: SourceSpec, *, prefix: str) -> list[LegalDocument]:
    """Plain text of an official PDF. HTML snapshots still use parse_rules."""
    text = blob.decode("utf-8", errors="replace")
    documents: list[LegalDocument] = []
    current_num = ""
    current_heading = ""
    current_parts: list[str] = []

    def flush() -> None:
        if not current_num or not current_parts:
            return
        citation = f"{prefix} {current_num}"
        body = " ".join(current_parts)
        documents.append(
            build_document(
                source,
                citation=citation,
                title=current_heading or citation,
                level="national",
                kind="court_rule",
                body="Rules Board for Courts of Law",
                sections=[Section(current_num, current_heading, body, [])],
                url=source.canonical_url,
                status="in_force",
                doc_date=None,
                effective_from=None,
            )
        )

    for line in text.splitlines():
        piece = " ".join(line.split())
        if not piece:
            continue
        match = _RULE_HEAD.match(piece)
        if match:
            flush()
            current_num = match.group(1)
            current_heading = (match.group(2) or "").strip() or piece
            current_parts = []
            continue
        if current_num:
            current_parts.append(piece)
    flush()
    if documents:
        return documents
    body = " ".join(text.split())
    if len(body) < 40:
        return []
    gazette = _GG.search(body)
    citation = gazette.group(0) if gazette else prefix
    return [
        build_document(
            source,
            citation=citation,
            title=citation,
            level="national",
            kind="court_rule",
            body="Rules Board for Courts of Law",
            sections=[Section("", citation, body, [])],
            url=source.canonical_url,
            status="in_force",
            doc_date=None,
            effective_from=None,
        )
    ]


def _looks_html(blob: bytes) -> bool:
    head = blob.lstrip()[:200].lower()
    return head.startswith(b"<") or b"<html" in head or b"<h2" in head or b"<p>" in head


class GazetteAdapter:
    source_id = "govza-gazette"

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        headers = html_headers()
        reject_credential_url(GAZETTE_INDEX)
        reject_credential_url(GAZETTE_RSS)
        page = await http_get(GAZETTE_INDEX, headers)
        await pause("GOVZA_MIN_INTERVAL", "0")
        rss = await http_get(GAZETTE_RSS, headers)
        urls = notice_links(page)
        for url in notice_links(rss):
            if url not in urls:
                urls.append(url)
        if resume and resume in urls:
            urls = urls[urls.index(resume) + 1 :]
        elif resume:
            urls = [url for url in urls if url > resume]
        urls = urls[: _max_notices()]
        blobs: list[bytes] = []
        last = resume
        for url in urls:
            reject_credential_url(url)
            blobs.append(await http_get(url, headers))
            await pause("GOVZA_MIN_INTERVAL", "0")
            last = url
        return blobs, last

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_notice(blob, source)


class _RulesAdapter:
    source_id = ""
    page_url = ""
    prefix = ""

    async def _load(self, http_get) -> bytes:
        reject_saflii_rules(self.page_url)
        blob = await http_get(self.page_url, pdf_headers())
        if blob.startswith(b"%PDF"):
            return pdf_text(blob).encode("utf-8")
        return blob

    async def fetch(self, http_get) -> list[bytes]:
        return [await self._load(http_get)]

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        del resume
        blob = await self._load(http_get)
        return [blob], self.page_url

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        if blob.startswith(b"%PDF"):
            blob = pdf_text(blob).encode("utf-8")
        if _looks_html(blob):
            return parse_rules(blob, source, prefix=self.prefix)
        return parse_rules_text(blob, source, prefix=self.prefix)


class UniformRulesAdapter(_RulesAdapter):
    source_id = "uniform-rules"
    page_url = UNIFORM_RULES_URL
    prefix = "Uniform Rule"


class MagistratesRulesAdapter(_RulesAdapter):
    source_id = "magistrates-rules"
    page_url = MAGISTRATES_RULES_URL
    prefix = "Magistrates' Courts Rule"
