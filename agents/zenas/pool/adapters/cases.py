"""CourtListener and Caselaw Access Project.

Opinions from the federal appellate courts and the named state commercial
courts. Criminal matters are dropped and their text is never stored.
Judgments are stored as published. No redaction step runs on them.

The REST token is read from COURTLISTENER_API_TOKEN and sent as a header.
It is never placed in a URL. A live load is skipped until the token is set.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import date
from urllib.parse import urljoin

from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.http import USER_AGENT
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


def is_criminal(row: dict) -> bool:
    blob = " ".join(
        str(row.get(key) or "")
        for key in ("nature_of_suit", "suit_nature", "case_type")
    ).lower()
    if row.get("criminal") is True:
        return True
    if "criminal" in blob:
        return True
    name = str(row.get("case_name") or row.get("name") or "").lower()
    return name.startswith("s v ") or "criminal" in name


def is_commercial(row: dict) -> bool:
    if row.get("commercial") is True:
        return True
    blob = " ".join(
        str(row.get(key) or "")
        for key in ("nature_of_suit", "suit_nature", "case_type")
    ).lower()
    if any(word in blob for word in _COMMERCIAL):
        return True
    # A CAP opinion has no nature-of-suit field. Read the published text.
    if blob.strip():
        return False
    text = (_opinion_text(row) + " " + str(row.get("name") or row.get("case_name") or "")).lower()
    return any(word in text for word in _COMMERCIAL)


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
    court = row.get("court") or row.get("court_id") or ""
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
    citations = row.get("citations") or []
    if isinstance(citations, str) and citations.strip():
        return citations.strip()
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
            name = row.get("case_name") or row.get("name") or ""
            if is_criminal(row):
                self.skipped.append(f"criminal:{name}")
                continue
            court = _court_id(row)
            if court not in ALLOWED_COURTS:
                self.skipped.append(f"not-appellate:{court or name}")
                continue
            if not is_commercial(row):
                self.skipped.append(f"not-commercial:{name}")
                continue
            text = _opinion_text(row)
            citation = _citation(row)
            if not text or not citation:
                self.skipped.append(f"no-text:{name or citation}")
                continue
            filed = row.get("date_filed") or row.get("decision_date")
            doc_date = date.fromisoformat(filed) if filed else None
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
                    status=str(row.get("precedential_status") or "published"),
                    doc_date=doc_date,
                    effective_from=doc_date,
                    text=text,
                )
            )
        return documents


class CourtListenerAdapter(_CaseAdapter):
    source_id = "courtlistener"
    reason = "COURTLISTENER_API_TOKEN is not set"

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
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
        while cursor and pages < page_cap:
            if delay > 0:
                await asyncio.sleep(delay)
            if token in cursor:
                raise FetchNotConfigured("refusing a URL that contains a credential")
            blob = await http_get(cursor, headers)
            blobs.append(blob)
            try:
                payload = json.loads(blob.decode("utf-8"))
            except json.JSONDecodeError:
                break
            nxt = payload.get("next") or ""
            if nxt and token in str(nxt):
                raise FetchNotConfigured("refusing a URL that contains a credential")
            cursor = str(nxt) if nxt else ""
            pages += 1
        return blobs, cursor


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
