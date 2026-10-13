"""CourtListener and Caselaw Access Project.

Federal appellate commercial decisions only. Criminal matters are dropped
and their text is never stored. Live bulk files are not downloaded here.
"""

from __future__ import annotations

import json
from datetime import date

from agents.zenas.pool.builddoc import build_document
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
)


def is_criminal(row: dict) -> bool:
    blob = " ".join(
        str(row.get(key) or "")
        for key in ("nature_of_suit", "suit_nature", "case_type")
    ).lower()
    if row.get("criminal") is True:
        return True
    return "criminal" in blob


def is_commercial(row: dict) -> bool:
    if row.get("commercial") is True:
        return True
    blob = " ".join(
        str(row.get(key) or "")
        for key in ("nature_of_suit", "suit_nature", "case_type")
    ).lower()
    return any(word in blob for word in _COMMERCIAL)


def _court_id(row: dict) -> str:
    court = row.get("court") or row.get("court_id") or ""
    if isinstance(court, dict):
        return str(court.get("slug") or court.get("id") or "").lower()
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
    data = body.get("data") if isinstance(body, dict) else None
    opinions = (data or {}).get("opinions") if isinstance(data, dict) else None
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
            if court not in FEDERAL_APPELLATE:
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
                    level="federal",
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
    reason = (
        "CourtListener bulk files are not in this wave. Set a snapshot. "
        "Do not page the membership API for bulk copy. "
        "COURTLISTENER_API_TOKEN is the name for a later reviewed fetch."
    )


class CapAdapter(_CaseAdapter):
    source_id = "cap"
    reason = (
        "Caselaw Access Project bulk data is unrestricted as of 2024-03-01, "
        "but the multi-gigabyte dump is not in this repository. Supply a snapshot."
    )
