"""CourtListener and Caselaw Access Project.

Opinions from the federal appellate courts and the named state commercial
courts. Criminal matters are dropped and their text is never stored.
Judgments are stored as published. No redaction step runs on them.

The REST token is read from COURTLISTENER_API_TOKEN and sent as a header.
It is never placed in a URL. A live load is skipped until the token is set.

The v4 search API returns snippets, not opinion text. A row that passes the
criminal, court and commercial filters has its text fetched from the v4
opinions endpoint, one request per COURTLISTENER_MIN_INTERVAL (default 1 s),
with the same Authorization header. A 429 that outlasts the retries stops the
walk: the pages already read are kept, the cursor is saved, and the run is
recorded as rate_limited so the next Load resumes there.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import urllib.error
from collections import Counter
from datetime import date
from urllib.parse import urljoin

from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.http import USER_AGENT, RateLimited
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec

FEDERAL_APPELLATE = {
    "scotus",
    "ca1",
    "ca2",
    "ca3",
    "ca4",
    "ca5",
    "ca6",
    "ca7",
    "ca8",
    "ca9",
    "ca10",
    "ca11",
    "cadc",
    "cafc",
}

# Delaware Supreme and Chancery, NY Court of Appeals and Appellate Division,
# Texas Supreme. District courts stay out.
STATE_COMMERCIAL = {"del", "delch", "ny", "nyappdiv", "tex"}
ALLOWED_COURTS = FEDERAL_APPELLATE | STATE_COMMERCIAL

_API = "https://www.courtlistener.com/api/rest/v4/search/"
_OPINION_API = "https://www.courtlistener.com/api/rest/v4/opinions/"
_OPINION_FIELDS = "id,plain_text,html_with_citations,html,xml_harvard"
SKIP_REASONS = ("criminal", "not-appellate", "not-commercial", "no-text")
_TAGS = re.compile(r"<[^>]+>")
# "1:20-cr-00123", "18-1234-cr", "No. 19-CR-55", "Crim. No. 4".
_CRIMINAL_DOCKET = re.compile(r"(?i)(?:^|[^a-z])(?:cr|crim)(?:[^a-z]|$)")
_CRIMINAL_NAMES = ("people v. ", "people v ", "state v. ", "commonwealth v. ", "s v ")

_COMMERCIAL = (
    "insurance",
    "marine",
    "contract",
    "copyright",
    "patent",
    "trademark",
    "admiralty",
    "freight",
    "cargo",
    "negotiable",
    "commercial",
    "promissory",
    "sale of",
    "corporation",
    "shareholder",
    "tariff",
    "customs",
    "ucc",
    "lease",
    "partnership",
    "bill of lading",
    "goods",
)
_CAP_LIST = "https://static.case.law/f2d/1/cases/"
_JSON_HREF = re.compile(r"""href=["']([^"']+\.json)["']""", re.I)
_CIRCUITS = (
    ("first", "ca1"),
    ("second", "ca2"),
    ("third", "ca3"),
    ("fourth", "ca4"),
    ("fifth", "ca5"),
    ("sixth", "ca6"),
    ("seventh", "ca7"),
    ("eighth", "ca8"),
    ("ninth", "ca9"),
    ("tenth", "ca10"),
    ("eleventh", "ca11"),
    ("district of columbia", "cadc"),
    ("federal circuit", "cafc"),
)


def _case_name(row: dict) -> str:
    return str(row.get("case_name") or row.get("caseName") or row.get("name") or "")


def docket_number(row: dict) -> str:
    """The docket number as the record gives it. v4 search uses docketNumber."""
    raw = row.get("docket_number") or row.get("docketNumber") or ""
    if isinstance(raw, dict):
        raw = raw.get("docket_number") or ""
    return " ".join(str(raw).split())


def nature_of_suit(row: dict) -> str:
    """Nature of suit as the record gives it. v4 search uses suitNature."""
    raw = row.get("nature_of_suit") or row.get("suitNature") or row.get("suit_nature") or ""
    return " ".join(str(raw).split())


def _nature_blob(row: dict) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in ("nature_of_suit", "suitNature", "suit_nature", "case_type")
    ).lower()


def is_criminal(row: dict) -> bool:
    if row.get("criminal") is True:
        return True
    if "criminal" in _nature_blob(row):
        return True
    if _CRIMINAL_DOCKET.search(docket_number(row)):
        return True
    name = _case_name(row).lower()
    return name.startswith(_CRIMINAL_NAMES) or "criminal" in name


def is_commercial(row: dict) -> bool:
    if row.get("commercial") is True:
        return True
    blob = _nature_blob(row)
    if any(word in blob for word in _COMMERCIAL):
        return True
    # A CAP or v4 opinion often has no nature-of-suit. Read the published text,
    # or before the text is fetched, the names and the search snippet.
    if blob.strip():
        return False
    text = " ".join(
        [
            _opinion_text(row),
            _case_name(row),
            str(row.get("caseNameFull") or ""),
            _snippets(row),
        ]
    ).lower()
    return any(word in text for word in _COMMERCIAL)


def _snippets(row: dict) -> str:
    opinions = row.get("opinions")
    if not isinstance(opinions, list):
        return ""
    return " ".join(str(item.get("snippet") or "") for item in opinions if isinstance(item, dict))


def skip_reason(row: dict) -> str:
    """The first filter a row fails, before text is needed. Empty when it passes."""
    if is_criminal(row):
        return "criminal"
    if _court_id(row) not in ALLOWED_COURTS:
        return "not-appellate"
    if not is_commercial(row):
        return "not-commercial"
    return ""


def _cap_slug(name: str, abbr: str) -> str:
    blob = f"{name} {abbr}".lower()
    if "supreme court of the united states" in blob:
        return "scotus"
    if "circuit" in blob:
        for word, slug in _CIRCUITS:
            if word in blob:
                return slug
    if "delaware" in blob and "chancery" in blob:
        return "delch"
    if "delaware" in blob and "supreme" in blob:
        return "del"
    if "appellate division" in blob:
        return "nyappdiv"
    if "new york" in blob and "appeals" in blob:
        return "ny"
    if "texas" in blob and "supreme" in blob:
        return "tex"
    return ""


def _court_id(row: dict) -> str:
    # v4 search gives court_id as the slug and court as the full name.
    slug = row.get("court_id")
    if isinstance(slug, str) and slug.strip():
        return slug.strip().lower()
    court = row.get("court") or ""
    if isinstance(court, dict):
        slug = str(court.get("slug") or "").lower()
        if slug:
            return slug
        mapped = _cap_slug(str(court.get("name") or ""), str(court.get("name_abbreviation") or ""))
        if mapped:
            return mapped
        return str(court.get("id") or "").lower()
    return str(court).lower()


def _citation(row: dict) -> str:
    citations = row.get("citations") or row.get("citation") or []
    if isinstance(citations, str) and citations.strip():
        return citations.strip()
    if citations and isinstance(citations[0], str):
        return citations[0].strip()
    if citations and isinstance(citations[0], dict):
        first = citations[0]
        if first.get("cite"):
            return str(first["cite"])
        volume = first.get("volume")
        reporter = first.get("reporter")
        page = first.get("page")
        if volume and reporter and page:
            return f"{volume} {reporter} {page}"
    return ""


def html_text(raw: str) -> str:
    """Text of opinion HTML, paragraphs kept as blank lines."""
    if not raw:
        return ""
    blocks = re.sub(r"(?i)</p>|<br\s*/?>|</div>|</h\d>", "\n\n", raw)
    text = html.unescape(_TAGS.sub(" ", blocks))
    paragraphs = [" ".join(block.split()) for block in text.split("\n\n")]
    return "\n\n".join(item for item in paragraphs if item).strip()


def opinion_payload_text(payload: dict) -> str:
    """plain_text from a v4 opinion, else its HTML as text."""
    plain = str(payload.get("plain_text") or "").strip()
    if plain:
        return plain
    for key in ("html_with_citations", "html", "xml_harvard"):
        text = html_text(str(payload.get(key) or ""))
        if text:
            return text
    return ""


def _opinion_text(row: dict) -> str:
    plain = (row.get("plain_text") or "").strip()
    if plain:
        return plain
    body = row.get("casebody") or {}
    opinions = None
    if isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, dict):
            opinions = data.get("opinions")
        if not opinions:
            opinions = body.get("opinions")
    if opinions and isinstance(opinions, list):
        return "\n\n".join((item.get("text") or "").strip() for item in opinions if item.get("text")).strip()
    return ""


def _paragraphs(text: str) -> list[Section]:
    blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
    if len(blocks) <= 1:
        return [Section("", "", text, [])]
    return [Section(f"¶{index}", "", block, []) for index, block in enumerate(blocks, start=1)]


class _CaseAdapter:
    source_id = ""
    reason = ""

    async def fetch(self, http_get) -> list[bytes]:
        raise FetchNotConfigured(self.reason)

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        payload = json.loads(blob.decode("utf-8"))
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict) and "results" not in payload and "cases" not in payload:
            rows = [payload]
        else:
            rows = payload.get("results", payload.get("cases", []))
        documents = []
        self.skipped: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = _case_name(row)
            court = _court_id(row)
            reason = skip_reason(row)
            if reason:
                label = court or name if reason == "not-appellate" else name
                self.skipped.append(f"{reason}:{label}")
                continue
            text = _opinion_text(row)
            citation = _citation(row)
            if not text or not citation:
                self.skipped.append(f"no-text:{name or citation}")
                continue
            filed = row.get("date_filed") or row.get("dateFiled") or row.get("decision_date")
            doc_date = date.fromisoformat(str(filed)[:10]) if filed else None
            url = row.get("absolute_url") or row.get("frontend_url") or ""
            if url and url.startswith("/"):
                url = "https://www.courtlistener.com" + url
            documents.append(
                build_document(
                    source,
                    citation=citation,
                    title=name or citation,
                    level="federal" if court in FEDERAL_APPELLATE else "state",
                    kind="case",
                    body=court,
                    sections=_paragraphs(text),
                    url=url,
                    status=str(row.get("precedential_status") or row.get("status") or "published"),
                    doc_date=doc_date,
                    effective_from=doc_date,
                    text=text,
                    docket_number=docket_number(row),
                    nature_of_suit=nature_of_suit(row),
                )
            )
        return documents


class CourtListenerAdapter(_CaseAdapter):
    source_id = "courtlistener"
    reason = "COURTLISTENER_API_TOKEN is not set"

    def __init__(self) -> None:
        self.fetch_skips: Counter[str] = Counter()
        self.rate_limited = ""
        self.pages_read = 0

    def load_note(self) -> dict:
        """What the last fetch_bulk saw, for the run record. No URL, no key."""
        return {
            "status": "rate_limited" if self.rate_limited else "",
            "message": self.rate_limited,
            "pages": self.pages_read,
            "skips": dict(self.fetch_skips),
        }

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        self.fetch_skips = Counter()
        self.rate_limited = ""
        self.pages_read = 0
        token = os.environ.get("COURTLISTENER_API_TOKEN", "").strip()
        if not token:
            raise FetchNotConfigured("COURTLISTENER_API_TOKEN is not set")
        if resume and (token in resume or "token=" in resume.lower()):
            raise FetchNotConfigured("refusing a URL that contains a credential")
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT,
            "Authorization": f"Token {token}",
        }
        courts = ",".join(sorted(ALLOWED_COURTS))
        url = resume or f"{_API}?type=o&court={courts}"
        if token in url or "token=" in url.lower():
            raise FetchNotConfigured("refusing a URL that contains a credential")
        delay = float(os.environ.get("COURTLISTENER_MIN_INTERVAL", "1") or "1")
        page_cap = int(os.environ.get("COURTLISTENER_MAX_PAGES", "20") or "20")
        blobs: list[bytes] = []
        cursor = url
        pages = 0

        async def get(target: str) -> bytes:
            if token in target:
                raise FetchNotConfigured("refusing a URL that contains a credential")
            if delay > 0:
                await asyncio.sleep(delay)
            return await http_get(target, headers)

        while cursor and pages < page_cap:
            try:
                blob = await get(cursor)
            except RateLimited as exc:
                # Keep what was read. The cursor still points at this page.
                self.rate_limited = str(exc)
                return blobs, cursor
            try:
                payload = json.loads(blob.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                break
            if not isinstance(payload, dict):
                break
            nxt = payload.get("next") or ""
            if nxt and token in str(nxt):
                raise FetchNotConfigured("refusing a URL that contains a credential")
            rows = payload.get("results") or []
            kept: list[dict] = []
            stopped = False
            for row in rows:
                if not isinstance(row, dict):
                    continue
                reason = skip_reason(row)
                if reason:
                    self.fetch_skips[reason] += 1
                    continue
                if not _opinion_text(row):
                    try:
                        text = await self._opinion_text(get, row)
                    except RateLimited as exc:
                        self.rate_limited = str(exc)
                        stopped = True
                        break
                    if not text:
                        self.fetch_skips["no-text"] += 1
                        continue
                    row = {**row, "plain_text": text}
                kept.append(row)
            if kept:
                blobs.append(json.dumps({"results": kept}).encode("utf-8"))
            if stopped:
                # Rows already fetched on this page are kept. The next Load
                # reads this page again; rows already stored are not inserted twice.
                return blobs, cursor
            cursor = str(nxt) if nxt else ""
            pages += 1
            self.pages_read = pages
        return blobs, cursor

    async def _opinion_text(self, get, row: dict) -> str:
        """plain_text of the row's opinions from the v4 opinions endpoint."""
        opinions = row.get("opinions")
        if not isinstance(opinions, list):
            return ""
        parts: list[str] = []
        for item in opinions:
            if not isinstance(item, dict):
                continue
            opinion_id = item.get("id")
            if not isinstance(opinion_id, int) and not str(opinion_id or "").isdigit():
                continue
            target = f"{_OPINION_API}{int(opinion_id)}/?fields={_OPINION_FIELDS}"
            try:
                blob = await get(target)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise FetchNotConfigured(f"HTTP {exc.code} from www.courtlistener.com") from None
                continue
            try:
                payload = json.loads(blob.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(payload, dict):
                text = opinion_payload_text(payload)
                if text:
                    parts.append(text)
        return "\n\n".join(parts).strip()


class CapAdapter(_CaseAdapter):
    source_id = "cap"
    reason = (
        "Caselaw Access Project bulk data is unrestricted as of 2024-03-01. "
        "Load now reads a small public volume from static.case.law."
    )

    async def fetch(self, http_get) -> list[bytes]:
        blobs, _cursor = await self.fetch_bulk(http_get)
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        del resume
        cap = int(os.environ.get("CAP_MAX", "10") or "10")
        headers = {"Accept": "text/html", "Accept-Encoding": "gzip", "User-Agent": USER_AGENT}
        index = await http_get(_CAP_LIST, headers)
        json_headers = {"Accept": "application/json", "Accept-Encoding": "gzip", "User-Agent": USER_AGENT}
        blobs: list[bytes] = []
        for match in _JSON_HREF.finditer(index.decode("utf-8", errors="replace")):
            if len(blobs) >= cap:
                break
            href = match.group(1)
            url = href if href.startswith("http") else urljoin(_CAP_LIST, href)
            if "static.case.law" not in url:
                continue
            blobs.append(await http_get(url, json_headers))
        return blobs, ""
