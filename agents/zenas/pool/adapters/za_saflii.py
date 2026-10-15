"""SAFLII judgments: SCA, Constitutional Court, and High Court divisions.

Commercial and civil judgments are stored as published. Criminal matters are
dropped and their text is never stored. SAFLII's usage policy is recorded on
the catalogue row as information. It is not a switch.
"""

from __future__ import annotations

import os
import re
from datetime import date
from html.parser import HTMLParser

from agents.zenas.pool.adapters.za_common import html_headers, pause, reject_credential_url
from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec

# Court code, recent-decisions page, name used on the instrument.
COURTS: tuple[tuple[str, str, str], ...] = (
    ("ZASCA", "https://www.saflii.org/za/cases/ZASCA/recent.html", "Supreme Court of Appeal"),
    ("ZACC", "https://www.saflii.org/za/cases/ZACC/recent.html", "Constitutional Court"),
    ("ZAGPJHC", "https://www.saflii.org/za/cases/ZAGPJHC/recent.html", "High Court, Gauteng, Johannesburg"),
    ("ZAGPPHC", "https://www.saflii.org/za/cases/ZAGPPHC/recent.html", "High Court, Gauteng, Pretoria"),
    ("ZAWCHC", "https://www.saflii.org/za/cases/ZAWCHC/recent.html", "High Court, Western Cape"),
    ("ZAKZDHC", "https://www.saflii.org/za/cases/ZAKZDHC/recent.html", "High Court, KwaZulu-Natal, Durban"),
    ("ZAKZPHC", "https://www.saflii.org/za/cases/ZAKZPHC/recent.html", "High Court, KwaZulu-Natal, Pietermaritzburg"),
    ("ZAECGHC", "https://www.saflii.org/za/cases/ZAECGHC/recent.html", "High Court, Eastern Cape, Makhanda"),
    ("ZAECHC", "https://www.saflii.org/za/cases/ZAECHC/recent.html", "High Court, Eastern Cape"),
    ("ZAFSHC", "https://www.saflii.org/za/cases/ZAFSHC/recent.html", "High Court, Free State"),
    ("ZANWHC", "https://www.saflii.org/za/cases/ZANWHC/recent.html", "High Court, North West"),
    ("ZALMPPHC", "https://www.saflii.org/za/cases/ZALMPPHC/recent.html", "High Court, Limpopo"),
    ("ZAMPMHC", "https://www.saflii.org/za/cases/ZAMPMHC/recent.html", "High Court, Mpumalanga"),
)

_HOST = "https://www.saflii.org"
_NEUTRAL = re.compile(r"\[(\d{4})\]\s+(ZA[A-Z]{2,6})\s+(\d+)")
_REPORT = re.compile(r"\b(\d{4})\s+\((\d+)\)\s+SA\s+(\d+)\s+\(([A-Za-z]{2,6})\)")
_CRIMINAL_NAME = re.compile(r"^\s*s\s+v\s+\S", re.I)
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
_HREF = re.compile(r"""href=["']([^"']+)["']""", re.I)


def _max_pages() -> int:
    raw = os.environ.get("SAFLII_MAX_PAGES", "20").strip()
    try:
        return max(0, int(raw or "20"))
    except ValueError:
        return 20


def judgment_links(page: bytes, court: str) -> list[str]:
    """Judgment HTML links for one court. PDFs are left out."""
    text = page.decode("utf-8", errors="replace")
    found: list[str] = []
    seen: set[str] = set()
    marker = f"/za/cases/{court}/"
    for match in _HREF.finditer(text):
        href = match.group(1).split("#", 1)[0].strip()
        if href.lower().endswith(".pdf"):
            continue
        if marker not in href or not href.lower().endswith(".html"):
            continue
        if href.startswith("http://") or href.startswith("https://"):
            url = href
        elif href.startswith("/"):
            url = _HOST + href
        else:
            url = f"{_HOST}/za/cases/{court}/{href}"
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def _after(urls: list[str], resume: str) -> list[str]:
    ordered = sorted(set(urls))
    if not resume:
        return ordered
    if resume in ordered:
        return ordered[ordered.index(resume) + 1 :]
    return [url for url in ordered if url > resume]


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.case_type = ""
        self._capture: str | None = None
        self._chunks: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        names = {key.lower(): (value or "") for key, value in attrs}
        if tag.lower() == "meta" and names.get("name", "").lower() == "case-type":
            self.case_type = names.get("content", "")
        if tag.lower() in {"h1", "p", "h2"}:
            self._capture = tag.lower()
            self._chunks = []
        if tag.lower() == "title":
            self._capture = "title"
            self._chunks = []

    def handle_endtag(self, tag: str) -> None:
        if self._capture and tag.lower() == self._capture:
            text = " ".join("".join(self._chunks).split())
            if self._capture == "title" and text:
                self.title = text
            elif text:
                self.parts.append(text)
            self._capture = None
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._chunks.append(data)


def is_criminal_matter(title: str, case_type: str, heading: str) -> bool:
    """Criminal matters are dropped. The word is read from the title line, not the reasons."""
    if "criminal" in case_type.lower():
        return True
    for line in (title, heading):
        cleaned = " ".join(line.split())
        if _CRIMINAL_NAME.match(cleaned):
            return True
        first = cleaned.lower()
        if first.startswith("the state v ") or "criminal appeal" in first or "criminal trial" in first:
            return True
    return False


def _court_name(code: str) -> str:
    for court, _url, name in COURTS:
        if court == code:
            return name
    return "South African court"


def _when(text: str, year_hint: str) -> date | None:
    match = _LONG_DATE.search(text)
    if not match:
        if year_hint.isdigit():
            return date(int(year_hint), 1, 1)
        return None
    month = _MONTHS.get(match.group(2).lower())
    if not month:
        return date(int(year_hint), 1, 1) if year_hint.isdigit() else None
    try:
        return date(int(match.group(3)), month, int(match.group(1)))
    except ValueError:
        return None


def parse_judgment(blob: bytes, source: SourceSpec) -> list[LegalDocument]:
    page = _Page()
    page.feed(blob.decode("utf-8", errors="replace"))
    blob_text = " ".join(page.parts)
    heading = page.parts[0] if page.parts else page.title
    if is_criminal_matter(page.title, page.case_type, heading):
        return []
    neutral = _NEUTRAL.search(page.title) or _NEUTRAL.search(blob_text)
    report = _REPORT.search(page.title) or _REPORT.search(blob_text)
    if neutral:
        citation = f"[{neutral.group(1)}] {neutral.group(2)} {neutral.group(3)}"
        court = _court_name(neutral.group(2))
        year = neutral.group(1)
    elif report:
        citation = f"{report.group(1)} ({report.group(2)}) SA {report.group(3)} ({report.group(4)})"
        court = report.group(4).upper()
        year = report.group(1)
    else:
        return []
    paragraphs = [part for part in page.parts if part and part != citation]
    if not paragraphs:
        return []
    sections = [Section("", heading, part, []) for part in paragraphs]
    decided = _when(blob_text, year)
    return [
        build_document(
            source,
            citation=citation,
            title=heading or citation,
            level="national",
            kind="case",
            body=court,
            sections=sections,
            url=source.canonical_url,
            status="published",
            doc_date=decided,
            effective_from=decided,
        )
    ]


class SafliiAdapter:
    source_id = "saflii"

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get, resume="")
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        headers = html_headers()
        urls: list[str] = []
        for code, index_url, _name in COURTS:
            reject_credential_url(index_url)
            page = await http_get(index_url, headers)
            await pause("SAFLII_MIN_INTERVAL")
            urls.extend(judgment_links(page, code))
        pending = _after(urls, resume)
        pending = pending[: _max_pages()]
        blobs: list[bytes] = []
        last = resume
        for url in pending:
            reject_credential_url(url)
            blobs.append(await http_get(url, headers))
            await pause("SAFLII_MIN_INTERVAL")
            last = url
        return blobs, last

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_judgment(blob, source)
