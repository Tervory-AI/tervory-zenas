"""National Acts from the official gov.za Acts index.

Each Act is the PDF as published: the Act as passed, or an amendment Act.
One stored document is one section. An amendment is recorded beside the
section it changes. This module does not write a consolidated text.

Fetching stays on www.gov.za, one request at a time, with the pool user
agent, backoff, and robots.txt. A challenge page is logged and not stored.
The bytes are for the ZA box. A US box raises before any request.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import io
import json
import logging
import os
import re
from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import date
from urllib.parse import urlsplit, urljoin

from agents.zenas.pool.adapters.za_common import pause, reject_credential_url
from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.http import BACKOFF_SECONDS, USER_AGENT, RateLimited
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec
from agents.zenas.pool.region import RegionError

log = logging.getLogger(__name__)

HOSTS = frozenset({"www.gov.za", "gov.za"})
ORIGIN = "https://www.gov.za"
ACTS_INDEX = "https://www.gov.za/documents/acts"
ROBOTS_URL = "https://www.gov.za/robots.txt"
MAX_PDF_BYTES = 25_000_000
MAX_PDF_PAGES = 400
_MIN_SECTION = 40

_locks: dict[str, asyncio.Lock] = {}

# The nightly acknowledges each file only after all its sections are stored.
# Other callers retain the original collected-blobs interface.
_SINK: contextvars.ContextVar[Callable[[bytes, str], Awaitable[None]] | None] = contextvars.ContextVar(
    "govza_sink", default=None,
)


@contextmanager
def streaming(sink: Callable[[bytes, str], Awaitable[None]]) -> Iterator[None]:
    token = _SINK.set(sink)
    try:
        yield
    finally:
        _SINK.reset(token)

_PAGE_HREF = re.compile(r"""href=["']([^"']*?)\?page=(\d+)["']""", re.I)
_TITLE_LINK = re.compile(
    r"""class=["']gazettetitle[^"']*["'][^>]*>\s*<a\s+href=["']([^"']+)["'][^>]*>(.*?)</a>""",
    re.I | re.S,
)
_GAZETTE_BIT = re.compile(r"""class=["']gazette-result["'][^>]*>(.*?)</div>""", re.I | re.S)
_WHEN = re.compile(r"""datetime=["'](\d{4}-\d{2}-\d{2})""")
_ACT_IN_ROW = re.compile(r"Act\s+(\d+)\s+of\s+(\d{4})", re.I)
_GAZETTE_NO = re.compile(r"\bG\s*(\d{4,6})\b")
_RG = re.compile(r"\bRG\s*(\d+)\b", re.I)
_NOTICE = re.compile(r"\b(?:GoN|GeN|BN|R)\s*(\d+)\b")
_FILE_SIZE = re.compile(r"File Size:\s*([0-9.]+\s*[KMG]B)", re.I)
_PDF_HREF = re.compile(r"""href=["']([^"']+\.pdf)["']""", re.I)
_ACT_HEADER = re.compile(r"Act\s+(?:No\.?\s*)?0*(\d+)\s+of\s+(\d{4})", re.I)
_NUM_LIST = r"[0-9]+[A-Za-z]?(?:\s*(?:,|and|to|-)\s*[0-9]+[A-Za-z]?){0,12}"
_GAZETTE_HEADER = re.compile(r"No\.\s*(\d{5,6})")
_DATE_TEXT = re.compile(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b")
_SECTION_LINE = re.compile(r"^(\d{1,3})([A-Za-z]?)\.\s+(\S.*)$")
_SCHEDULE_LINE = re.compile(r"^SCHEDULE\s+(\d+[A-Za-z]?)\b", re.I)
_DEFS = re.compile(r"(?m)^1\.\s+In this Act\b")
_ENACTED = re.compile(r"(?im)^[ \t]*B?[ \t]*E IT ENACTED\b")
_ACT_CITE = re.compile(r"\(Act(?:\s+No\.?)?\s+(\d+)\s+of\s+(\d{4})\)", re.I)
_R_NOTICE = re.compile(r"NO\.\s*R\.?\s*(\d+)", re.I)
_HEADING_AMEND = re.compile(
    r"(?i)^(Amendment|Substitution|Repeal|Insertion) of "
    r"(section|sections|schedule|schedules)\s+"
    rf"({_NUM_LIST})\s+"
    r"(?:of|to|in)\s+Act(?:\s+No\.?)?\s+(\d+)\s+of\s+(\d{4})"
)
_BODY_AMEND = re.compile(
    r"(?is)\b(section|sections|schedule|schedules)\s+"
    rf"({_NUM_LIST})\b"
    r"(?:(?!\b(?:section|sections|schedule|schedules)\b).){0,240}?"
    r"\(Act(?:\s+No\.?)?\s+(\d+)\s+of\s+(\d{4})\)"
    r"(?:(?!\b(?:section|sections|schedule|schedules)\b).){0,200}?"
    r"(?:is|are)\s+(?:hereby\s+)?amended"
)
_HEREWITH = re.compile(r"(?i)herewith amended")
_SIGNAL = re.compile(
    r"(?i)hereby amended|herewith amended|\bis amended\b|\bare amended\b|"
    r"amendment of|substitution of|repeal of|insertion of"
)

_MONTHS = {
    "january": 1, "januarie": 1,
    "february": 2, "februarie": 2,
    "march": 3, "maart": 3,
    "april": 4,
    "may": 5, "mei": 5,
    "june": 6, "junie": 6,
    "july": 7, "julie": 7,
    "august": 8, "augustus": 8,
    "september": 9,
    "october": 10, "oktober": 10,
    "november": 11,
    "december": 12, "desember": 12,
}

_DROP_LINE = (
    re.compile(r"(?i)^this gazette is also available"),
    re.compile(r"(?i)^JOBNAME:"),
    re.compile(r"^/first/"),
    re.compile(r"(?i)^government gazette\b"),
    re.compile(r"(?i)^staatskoerant\b"),
    re.compile(r"(?i)^\d*\s*no\.\s*\d+\s+government gazette"),
    re.compile(r"(?i)^\d{1,3}$"),
    re.compile(r"(?i)^act\s+no\.?\s*\d+\s+of\s+\d{4}\b.{0,80}$"),
    re.compile(r"(?i)^\d+\(english text signed"),
)

_ENGLISH_SIGNAL = (
    "in this act",
    "it is hereby",
    "herewith amended",
    "is hereby amended",
    "are hereby amended",
    "e it enacted",
    "short title",
    "amendment of",
    "substitution of",
    "amendment of rules",
)


@dataclass
class Report:
    acts: int = 0
    amendments: int = 0
    errors: int = 0

    def detail(self) -> str:
        return (
            f"Acts loaded {self.acts}; amendments recorded {self.amendments}; "
            f"errors {self.errors}"
        )


@dataclass
class IndexRow:
    page_url: str
    title: str
    act_number: str
    act_year: str
    gazette_number: str
    gazette_date: str
    notice: str
    regulation_gazette: str
    file_size: str


@dataclass
class _State:
    page: int = 0
    full_on: str = ""
    files: dict[str, dict[str, str]] = field(default_factory=dict)

    def dump(self) -> str:
        return json.dumps(
            {"page": self.page, "full_on": self.full_on, "files": self.files},
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass
class _Got:
    body: bytes | None
    blocked: bool


class Robots:
    """Longest matching Allow or Disallow wins. A tie allows the path."""

    def __init__(self, rules: list[tuple[str, bool]]) -> None:
        compiled: list[tuple[str, bool, re.Pattern[str] | None]] = []
        for pattern, allow in rules:
            compiled.append((pattern, allow, _robots_regex(pattern)))
        self._rules = compiled

    def allows(self, path: str) -> bool:
        if not path.startswith("/"):
            path = "/" + path
        best_len = -1
        best_allow = True
        for pattern, allow, regex in self._rules:
            if regex is None:
                if pattern == "":
                    matched = 0
                elif path.startswith(pattern):
                    matched = len(pattern)
                else:
                    continue
            else:
                found = regex.match(path)
                if not found:
                    continue
                matched = len(found.group(0))
            if matched > best_len or (matched == best_len and allow and not best_allow):
                best_len = matched
                best_allow = allow
        return True if best_len < 0 else best_allow


def parse_robots(text: str) -> Robots:
    """Rules for User-agent: *. Other agents are ignored."""
    agents: list[str] = []
    pending: list[tuple[str, bool]] = []
    groups: list[tuple[list[str], list[tuple[str, bool]]]] = []

    def flush() -> None:
        nonlocal agents, pending
        if agents and pending:
            groups.append((agents, pending))
        agents = []
        pending = []

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        lowered = key.lower()
        if lowered == "user-agent":
            if pending:
                flush()
            agents.append(value)
        elif lowered in {"allow", "disallow"}:
            pending.append((value, lowered == "allow"))
    if pending:
        flush()
    rules: list[tuple[str, bool]] = []
    for names, group_rules in groups:
        if "*" in names:
            rules.extend(group_rules)
    return Robots(rules)


def _robots_regex(pattern: str) -> re.Pattern[str] | None:
    if "*" not in pattern and not pattern.endswith("$"):
        return None
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    parts = [".*" if char == "*" else re.escape(char) for char in body]
    expr = "".join(parts)
    if anchored:
        expr += "$"
    return re.compile(expr)


def public_ref(url: str) -> str:
    """Host and path only. A query string never reaches a log."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "www.gov.za"
    host = parts.hostname or "www.gov.za"
    return f"{parts.scheme or 'https'}://{host}{parts.path}"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _as_of() -> date:
    raw = os.environ.get("GOVZA_ACTS_AS_OF", "").strip()
    if not raw:
        return date.today()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return date.today()


def _refuse_us_box() -> None:
    region = os.environ.get("TANK_REGION", "").strip().lower()
    if region in {"us", "us-east"}:
        raise RegionError("gov.za statutes are fetched on the ZA box only")


def _host_lock(host: str) -> asyncio.Lock:
    lock = _locks.get(host)
    if lock is None:
        lock = asyncio.Lock()
        _locks[host] = lock
    return lock


def _challenge(blob: bytes) -> bool:
    sample = blob[:4000].lower()
    return (
        b"just a moment" in sample
        or b"cf-browser-verification" in sample
        or b"attention required" in sample
        or b"cf-challenge" in sample
    )


def _headers(accept: str) -> dict[str, str]:
    return {"Accept": accept, "User-Agent": USER_AGENT}


def _check_url(url: str) -> str:
    reject_credential_url(url)
    parts = urlsplit(url)
    if parts.hostname not in HOSTS or parts.scheme not in {"https", "http"}:
        raise FetchNotConfigured("gov.za fetches stay on www.gov.za")
    return parts.path or "/"


async def polite_get(
    http_get,
    url: str,
    *,
    accept: str,
    robots: Robots | None,
    report: Report,
) -> _Got:
    """One request at a time for this host. A blocked path is not requested."""
    path = _check_url(url)
    if robots is not None and not robots.allows(path):
        return _Got(None, True)
    host = urlsplit(url).hostname or "www.gov.za"
    async with _host_lock(host):
        await pause("GOVZA_STATUTE_MIN_INTERVAL", "1")
        last = "error"
        for delay in (0.0, *BACKOFF_SECONDS):
            if delay:
                await asyncio.sleep(delay)
            try:
                body = await http_get(url, _headers(accept))
            except RateLimited:
                raise
            except Exception as exc:
                last = type(exc).__name__
                continue
            if not isinstance(body, (bytes, bytearray)):
                body = str(body).encode("utf-8", "replace")
            blob = bytes(body)
            if _challenge(blob):
                log.error(
                    "gov.za returned a challenge page and it was not stored: %s",
                    public_ref(url),
                )
                report.errors += 1
                return _Got(None, False)
            return _Got(blob, False)
        log.error("gov.za fetch failed: %s %s", last, public_ref(url))
        report.errors += 1
        return _Got(None, False)


def _strip_tags(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return " ".join(text.split())


def index_rows(html: str) -> list[IndexRow]:
    rows: list[IndexRow] = []
    for part in re.split(r"<tr\b", html, flags=re.I):
        found = _TITLE_LINK.search(part)
        if not found:
            continue
        href = found.group(1).strip()
        title = _strip_tags(found.group(2))
        page_url = urljoin(ORIGIN, href)
        if urlsplit(page_url).hostname not in HOSTS:
            continue
        bits = [_strip_tags(bit) for bit in _GAZETTE_BIT.findall(part)]
        blob = " ".join(bits)
        act = _ACT_IN_ROW.search(blob)
        gazette = _GAZETTE_NO.search(blob)
        rg = _RG.search(blob)
        notice = _NOTICE.search(blob)
        size = _FILE_SIZE.search(blob)
        when = _WHEN.search(part)
        rows.append(
            IndexRow(
                page_url=page_url.split("?")[0],
                title=title,
                act_number=act.group(1) if act else "",
                act_year=act.group(2) if act else "",
                gazette_number=gazette.group(1) if gazette else "",
                gazette_date=when.group(1) if when else "",
                notice=notice.group(1) if notice else "",
                regulation_gazette=rg.group(1) if rg else "",
                file_size=size.group(1).strip() if size else "",
            )
        )
    return rows


def is_regulation_row(row: IndexRow) -> bool:
    """A rules or regulations notice. A bare court order is left on the index."""
    title = row.title.lower()
    if "regulat" in title:
        return True
    if row.regulation_gazette and ("rule" in title or "regulat" in title):
        return True
    return False


def next_page(html: str, current: int) -> int | None:
    higher = [int(number) for _href, number in _PAGE_HREF.findall(html) if int(number) > current]
    return min(higher) if higher else None


def pdf_links(html: str) -> list[str]:
    found: list[str] = []
    for href in _PDF_HREF.findall(html):
        url = urljoin(ORIGIN, href).split("?")[0]
        if urlsplit(url).hostname in HOSTS and url not in found:
            found.append(url)
    return found


def _fingerprint(row: IndexRow) -> str:
    return "|".join((row.gazette_number, row.gazette_date, row.file_size, row.title))


def _load_state(resume: str) -> _State:
    if not resume or resume.startswith("snap:"):
        return _State()
    try:
        data = json.loads(resume)
    except json.JSONDecodeError:
        return _State()
    if not isinstance(data, dict):
        return _State()
    files = data.get("files")
    page = data.get("page") or 0
    try:
        page_no = int(page)
    except (TypeError, ValueError):
        page_no = 0
    return _State(
        page=max(page_no, 0),
        full_on=str(data.get("full_on") or ""),
        files=files if isinstance(files, dict) else {},
    )


def _recent(state: _State, today: date) -> bool:
    if not state.full_on:
        return False
    try:
        done = date.fromisoformat(state.full_on)
    except ValueError:
        return False
    return (today - done).days < _env_int("GOVZA_STATUTE_WEEK_DAYS", 7)


def _doubled_ratio(text: str) -> float:
    words = re.findall(r"[A-Za-z]{6,}", text)
    if len(words) < 8:
        return 0.0
    doubled = 0
    for word in words:
        if len(word) % 2 == 0 and all(word[i] == word[i + 1] for i in range(0, len(word), 2)):
            doubled += 1
    return doubled / len(words)


def _keep_page(text: str) -> bool:
    if _doubled_ratio(text) >= 0.15:
        return False
    head = text[:700].lower()
    gazette = head.find("government gazette")
    afrikaans = head.find("staatskoerant")
    if gazette >= 0 and (afrikaans < 0 or gazette < afrikaans):
        return True
    if "staatskoerant" not in head and "gazette" in head:
        return True
    folded = text.lower()
    return any(signal in folded for signal in _ENGLISH_SIGNAL)


def _clean_lines(text: str) -> str:
    kept: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if any(pattern.match(line) for pattern in _DROP_LINE):
            continue
        kept.append(line)
    return "\n".join(kept)


def english_text(blob: bytes) -> str:
    """English pages of a gazette PDF. Form pages and translation pages drop out."""
    if not blob.startswith(b"%PDF") or len(blob) > MAX_PDF_BYTES:
        return ""
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(blob))
    except (PdfReadError, OSError, ValueError, KeyError, TypeError):
        return ""
    parts: list[str] = []
    for index, page in enumerate(reader.pages):
        if index >= MAX_PDF_PAGES:
            break
        try:
            extracted = page.extract_text() or ""
        except (PdfReadError, OSError, ValueError, KeyError, TypeError):
            continue
        if _keep_page(extracted):
            parts.append(extracted)
    return "\n".join(parts).strip()


def _parse_date(text: str) -> str:
    for day, month, year in _DATE_TEXT.findall(text):
        number = _MONTHS.get(month.lower())
        if number is None:
            continue
        try:
            return date(int(year), number, int(day)).isoformat()
        except ValueError:
            continue
    return ""


def _fill_from_text(text: str, act_number: str, act_year: str, gazette: str, when: str) -> tuple[str, str, str, str]:
    if not act_number or not act_year:
        found = _ACT_HEADER.search(text)
        if found:
            act_number = act_number or str(int(found.group(1)))
            act_year = act_year or found.group(2)
    if not gazette:
        found = _GAZETTE_HEADER.search(text)
        if found:
            gazette = found.group(1)
    if not when:
        when = _parse_date(text)
    return act_number, act_year, gazette, when


def _expand_nums(raw: str) -> list[str]:
    pieces = re.split(r"[,;]|(?:\band\b)", raw, flags=re.I)
    numbers: list[str] = []
    for piece in pieces:
        piece = piece.strip()
        span = re.fullmatch(r"(\d+)\s*(?:to|-)\s*(\d+)", piece, flags=re.I)
        if span:
            start, end = int(span.group(1)), int(span.group(2))
            if 0 < end - start <= 12:
                numbers.extend(str(number) for number in range(start, end + 1))
            else:
                numbers.append(str(start))
            continue
        single = re.fullmatch(r"(\d+[A-Za-z]?)", piece)
        if single:
            numbers.append(single.group(1))
    return numbers


@dataclass
class _Target:
    kind: str
    number: str
    act_number: str
    act_year: str


def _targets_in(heading: str, text: str) -> list[_Target]:
    found: list[_Target] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add(kind: str, number: str, act_number: str, act_year: str) -> None:
        key = (kind, number, act_number, act_year)
        if key in seen:
            return
        seen.add(key)
        found.append(_Target(kind, number, str(int(act_number)), act_year))

    for match in _HEADING_AMEND.finditer(heading):
        kind = "schedule" if match.group(2).lower().startswith("schedule") else "section"
        for number in _expand_nums(match.group(3)):
            add(kind, number, match.group(4), match.group(5))
    if not _SIGNAL.search(text):
        return found
    for match in _BODY_AMEND.finditer(text):
        kind = "schedule" if match.group(1).lower().startswith("schedule") else "section"
        for number in _expand_nums(match.group(2)):
            add(kind, number, match.group(3), match.group(4))
    if not found and _HEREWITH.search(text):
        cite = _ACT_CITE.search(text)
        if cite:
            add("rules", "", cite.group(1), cite.group(2))
    return found


def _accept_number(previous: tuple[int, str] | None, number: int, suffix: str) -> bool:
    if previous is None:
        return True
    prev_number, prev_suffix = previous
    if suffix == "" and number == prev_number + 1:
        return True
    if number == prev_number and suffix and suffix != prev_suffix:
        return True
    return False


def split_sections(text: str) -> list[tuple[str, str, str, str]]:
    """(schedule, number, heading, text) from the operative English text."""
    body = text
    defs = _DEFS.search(text)
    if defs:
        body = text[defs.start():]
    else:
        enacted = _ENACTED.search(text)
        if enacted:
            body = text[enacted.end():]
    sections: list[tuple[str, str, str, str]] = []
    schedule = ""
    previous: tuple[int, str] | None = None
    pending = ""
    current: tuple[str, str, str, list[str]] | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        sch, number, heading, lines = current
        chunk = " ".join(" ".join(lines).split())
        if len(chunk) >= _MIN_SECTION:
            sections.append((sch, number, heading, chunk))
        current = None

    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        schedule_line = _SCHEDULE_LINE.match(line)
        if schedule_line:
            flush()
            schedule = schedule_line.group(1)
            previous = None
            pending = ""
            continue
        match = _SECTION_LINE.match(line)
        if match and _accept_number(previous, int(match.group(1)), match.group(2)):
            flush()
            number = match.group(1) + match.group(2)
            previous = (int(match.group(1)), match.group(2))
            heading = pending or line
            lines = [pending, line] if pending else [line]
            pending = ""
            current = (schedule, number, heading[:180], lines)
            continue
        if current is None:
            if _HEADING_AMEND.match(line) or len(line) <= 80:
                pending = line
            continue
        current[3].append(line)
    flush()
    return sections


def _iso_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _citation(
    act_number: str,
    act_year: str,
    schedule: str,
    number: str,
    notice: str,
    gazette_number: str,
) -> str:
    base = f"Act {act_number} of {act_year}"
    if schedule:
        return f"{base}, Schedule {schedule}, item {number}"
    if number == "notice":
        # Two notices under one Act must not share a citation.
        if notice:
            return f"{base}, GN R. {notice}"
        if gazette_number:
            return f"{base}, Gazette {gazette_number}"
        return base
    return f"{base}, s {number}"


def _amended_citation(target: _Target, label: str) -> str:
    base = f"Act {target.act_number} of {target.act_year}"
    if target.kind == "rules":
        return f"{base}, rules, amended by {label}"
    if target.kind == "schedule":
        return f"{base}, Schedule {target.number}, amended by {label}"
    return f"{base}, s {target.number}, amended by {label}"


def _instrument_label(act_number: str, act_year: str, notice: str, kind: str) -> str:
    if kind == "regulation":
        if notice:
            return f"GN R. {notice}"
        return f"Act {act_number} of {act_year}" if act_number else "Government Gazette"
    if act_number and act_year:
        return f"Act {act_number} of {act_year}"
    return "Government Gazette"


def documents_from_text(
    source: SourceSpec,
    *,
    text: str,
    url: str,
    title: str,
    act_number: str,
    act_year: str,
    gazette_number: str,
    gazette_date: str,
    notice: str,
    kind: str,
) -> list[LegalDocument]:
    cleaned = _clean_lines(text)
    act_number, act_year, gazette_number, gazette_date = _fill_from_text(
        text, act_number, act_year, gazette_number, gazette_date
    )
    if not notice:
        found_notice = _R_NOTICE.search(text)
        if found_notice:
            notice = found_notice.group(1)
    if not act_number or not act_year:
        return []
    parts = split_sections(cleaned)
    if not parts and kind == "regulation" and len(" ".join(cleaned.split())) >= _MIN_SECTION:
        notice_text = " ".join(cleaned.split())
        parts = [("", "notice", title or "Notice", notice_text)]
    label = _instrument_label(act_number, act_year, notice, kind)
    doc_kind = "regulation" if kind == "regulation" else "statute"
    body = "Government Gazette" if kind == "regulation" else "Parliament of South Africa"
    when = _iso_date(gazette_date)
    documents: list[LegalDocument] = []
    seen: set[str] = set()

    def add(document: LegalDocument) -> None:
        previous = next((item for item in documents if item.citation == document.citation), None)
        if previous is not None:
            if len(document.text) > len(previous.text):
                documents.remove(previous)
                documents.append(document)
            return
        if document.citation in seen:
            return
        seen.add(document.citation)
        documents.append(document)

    for schedule, number, heading, chunk in parts:
        citation = _citation(act_number, act_year, schedule, number, notice, gazette_number)
        head = heading if len(heading) <= 140 else heading[:137] + "..."
        doc_title = ", ".join(
            bit
            for bit in (
                head,
                f"Act {act_number} of {act_year}",
                f"Gazette {gazette_number}" if gazette_number else "",
                gazette_date,
            )
            if bit
        )
        path = f"schedule {schedule} item {number}" if schedule else f"s {number}"
        add(
            build_document(
                source,
                citation=citation,
                title=doc_title or citation,
                level="national",
                kind=doc_kind,
                body=body,
                sections=[Section(path, head, chunk, [])],
                url=url,
                status="as_passed",
                doc_date=when,
                effective_from=None,
                text=chunk,
            )
        )
        for target in _targets_in(heading, chunk):
            record = "Amending words only. Not a consolidated text.\n" + chunk
            amended = _amended_citation(target, label)
            record_title = ", ".join(
                bit
                for bit in (
                    f"Amended by {label}",
                    f"Act {target.act_number} of {target.act_year}",
                    f"Gazette {gazette_number}" if gazette_number else "",
                    gazette_date,
                )
                if bit
            )
            add(
                build_document(
                    source,
                    citation=amended,
                    title=record_title,
                    level="national",
                    kind=doc_kind,
                    body=body,
                    sections=[Section(amended, record_title, record, [])],
                    url=url,
                    status=f"as_amended_by: [{label}]",
                    doc_date=when,
                    effective_from=None,
                    text=record,
                )
            )
    return documents


def _payload(tag: str, row: IndexRow, pdf_url: str, digest: str, text: str) -> bytes:
    return json.dumps(
        {
            "source": tag,
            "url": pdf_url,
            "page_url": row.page_url,
            "title": row.title,
            "act_number": row.act_number,
            "act_year": row.act_year,
            "gazette_number": row.gazette_number,
            "gazette_date": row.gazette_date,
            "notice": row.notice,
            "regulation_gazette": row.regulation_gazette,
            "sha256": digest,
            "text": text,
        },
        ensure_ascii=True,
    ).encode("utf-8")


def parse_payload(blob: bytes, source: SourceSpec, *, kind: str, report: Report) -> list[LegalDocument]:
    if blob.startswith(b"%PDF"):
        raw = english_text(blob)
        documents = documents_from_text(
            source,
            text=raw,
            url=source.canonical_url,
            title="",
            act_number="",
            act_year="",
            gazette_number="",
            gazette_date="",
            notice="",
            kind=kind,
        )
    else:
        try:
            data = json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            report.errors += 1
            return []
        if not isinstance(data, dict):
            report.errors += 1
            return []
        documents = documents_from_text(
            source,
            text=str(data.get("text") or ""),
            url=str(data.get("url") or ""),
            title=str(data.get("title") or ""),
            act_number=str(data.get("act_number") or ""),
            act_year=str(data.get("act_year") or ""),
            gazette_number=str(data.get("gazette_number") or ""),
            gazette_date=str(data.get("gazette_date") or ""),
            notice=str(data.get("notice") or ""),
            kind=kind,
        )
    if not documents:
        report.errors += 1
        return []
    report.acts += 1
    report.amendments += sum(1 for item in documents if item.status.startswith("as_amended_by"))
    return documents


async def _read_robots(http_get, report: Report) -> Robots | None:
    got = await polite_get(http_get, ROBOTS_URL, accept="text/plain", robots=None, report=report)
    if got.body is None:
        return None
    return parse_robots(got.body.decode("utf-8", "replace"))


async def walk_index(
    http_get,
    *,
    index_url: str,
    tag: str,
    kind: str,
    resume: str,
    report: Report,
    max_pages_env: str,
    max_pages_default: int,
    max_files_env: str,
    max_files_default: int,
) -> tuple[list[bytes], str]:
    """Walk one gov.za index. Unchanged PDFs are not downloaded again."""
    _refuse_us_box()
    state = _load_state(resume)
    today = _as_of()
    recent = _recent(state, today)
    backfill = state.page
    robots = await _read_robots(http_get, report)
    if robots is None:
        return [], state.dump()
    index_path = urlsplit(index_url).path or "/"
    if not robots.allows(index_path):
        report.errors += 1
        log.error("gov.za robots.txt disallows the index and it was not fetched: %s", public_ref(index_url))
        return [], state.dump()
    max_pages = _env_int(max_pages_env, max_pages_default)
    max_files = _env_int(max_files_env, max_files_default)
    page = 0 if recent else state.page
    pages_fetched = 0
    files_done = 0
    blobs: list[bytes] = []
    while pages_fetched < max_pages:
        url = index_url if page == 0 else f"{index_url}?page={page}"
        got = await polite_get(http_get, url, accept="text/html", robots=robots, report=report)
        pages_fetched += 1
        if got.blocked or got.body is None:
            if got.blocked:
                report.errors += 1
            break
        html = got.body.decode("utf-8", "replace")
        rows = index_rows(html)
        if kind == "regulation":
            rows = [row for row in rows if is_regulation_row(row)]
        following = next_page(html, page)
        page_complete = True
        for row in rows:
            fingerprint = _fingerprint(row)
            known = state.files.get(row.page_url)
            if isinstance(known, dict) and known.get("fingerprint") == fingerprint and known.get("sha256"):
                continue
            if files_done >= max_files:
                page_complete = False
                break
            if not robots.allows(urlsplit(row.page_url).path or "/"):
                continue
            document = await polite_get(
                http_get, row.page_url, accept="text/html", robots=robots, report=report
            )
            if document.body is None:
                continue
            links = pdf_links(document.body.decode("utf-8", "replace"))
            if not links:
                report.errors += 1
                continue
            pdf_url = links[0]
            if not robots.allows(urlsplit(pdf_url).path or "/"):
                continue
            pdf = await polite_get(
                http_get, pdf_url, accept="application/pdf", robots=robots, report=report
            )
            if pdf.body is None:
                continue
            if not pdf.body.startswith(b"%PDF"):
                report.errors += 1
                log.error("gov.za returned a non-PDF and it was not stored: %s", public_ref(pdf_url))
                continue
            if len(pdf.body) > MAX_PDF_BYTES:
                report.errors += 1
                continue
            digest = hashlib.sha256(pdf.body).hexdigest()
            files_done += 1
            previous = digest == (known.get("sha256") if isinstance(known, dict) else "")
            acknowledged = {
                "sha256": digest,
                "fingerprint": fingerprint,
                "pdf": pdf_url,
            }
            if previous:
                state.files[row.page_url] = acknowledged
                continue
            text = await asyncio.to_thread(english_text, pdf.body)
            if not text.strip():
                report.errors += 1
                continue
            payload = _payload(tag, row, pdf_url, digest, text)
            state.files[row.page_url] = acknowledged
            sink = _SINK.get()
            if sink is None:
                blobs.append(payload)
            else:
                # The candidate cursor is durable only when the sink has
                # ingested this PDF. Cancellation before that replays it.
                await sink(payload, state.dump())
        if recent:
            state.page = backfill
            break
        if not page_complete:
            state.page = page
            break
        if following is None:
            state.full_on = today.isoformat()
            state.page = 0
            break
        state.page = following
        page = following
    return blobs, state.dump()


class GovzaActsAdapter:
    source_id = "govza-acts"

    def __init__(self) -> None:
        self.report = Report()

    def feeds_detail(self) -> str:
        return self.report.detail()

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get)
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        self.report = Report()
        return await walk_index(
            http_get,
            index_url=ACTS_INDEX,
            tag="govza-act",
            kind="statute",
            resume=resume,
            report=self.report,
            max_pages_env="GOVZA_ACTS_MAX_PAGES",
            max_pages_default=20,
            max_files_env="GOVZA_ACTS_MAX_FILES",
            max_files_default=40,
        )

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        return parse_payload(blob, source, kind="statute", report=self.report)
