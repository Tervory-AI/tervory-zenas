"""Government Gazette notices and the two sets of court rules.

Gazette and rules pages on gov.za and justice.gov.za are HTML. PDF members are
dropped by the loader before parse. Snapshots of the rule text still load.
"""

from __future__ import annotations

import os
import re
from datetime import date
from html.parser import HTMLParser

from agents.zenas.pool.adapters.za_common import html_headers, pause, reject_credential_url
from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec

GAZETTE_INDEX = "https://www.gov.za/documents/notices"
UNIFORM_RULES_URL = "https://www.justice.gov.za/legislation/rules/rules.htm"
MAGISTRATES_RULES_URL = "https://www.justice.gov.za/legislation/rules/rules.htm"

_HREF = re.compile(r"""href=["']([^"']+)["']""", re.I)
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


def notice_links(page: bytes) -> list[str]:
    text = page.decode("utf-8", errors="replace")
    found: list[str] = []
    seen: set[str] = set()
    for match in _HREF.finditer(text):
        href = match.group(1).split("#", 1)[0].strip()
        lowered = href.lower()
        if lowered.endswith((".pdf", ".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff")):
            continue
        if "documents" not in lowered and "gazette" not in lowered and "notice" not in lowered:
            continue
        if href.startswith("http://") or href.startswith("https://"):
            url = href
        elif href.startswith("/"):
            url = "https://www.gov.za" + href
        else:
            continue
        if "gov.za" not in url:
            continue
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


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


class GazetteAdapter:
    source_id = "govza-gazette"

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        headers = html_headers()
        reject_credential_url(GAZETTE_INDEX)
        page = await http_get(GAZETTE_INDEX, headers)
        urls = notice_links(page)
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

    async def fetch(self, http_get) -> list[bytes]:
        reject_credential_url(self.page_url)
        return [await http_get(self.page_url, html_headers())]

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        del resume
        reject_credential_url(self.page_url)
        blob = await http_get(self.page_url, html_headers())
        return [blob], self.page_url

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_rules(blob, source, prefix=self.prefix)


class UniformRulesAdapter(_RulesAdapter):
    source_id = "uniform-rules"
    page_url = UNIFORM_RULES_URL
    prefix = "Uniform Rule"


class MagistratesRulesAdapter(_RulesAdapter):
    source_id = "magistrates-rules"
    page_url = MAGISTRATES_RULES_URL
    prefix = "Magistrates' Courts Rule"
