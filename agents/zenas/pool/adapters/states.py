"""Enacted state statutes. Official comments and the ALI text are not parsed here."""

from __future__ import annotations

import json

from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.htmlparse import parse_delaware_html
from agents.zenas.pool.licence import FetchNotConfigured
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec

DELAWARE_WAVE = ("https://delcode.delaware.gov/title8/c001/sc01/index.html",)


class DelawareAdapter:
    source_id = "delaware-code"

    async def fetch(self, http_get) -> list[bytes]:
        blobs = []
        for url in DELAWARE_WAVE:
            blobs.append(
                await http_get(url, {"Accept": "text/html", "User-Agent": "TervoryZenasPool/0.1"})
            )
        return blobs

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        documents = []
        for number, heading, sections in parse_delaware_html(blob):
            if not number or not sections:
                continue
            citation = f"8 Del. C. § {number}"
            documents.append(
                build_document(
                    source,
                    citation=citation,
                    title=heading or citation,
                    level="state",
                    kind="statute",
                    body="Delaware General Assembly",
                    sections=sections,
                    url=DELAWARE_WAVE[0],
                    status="in_force",
                    doc_date=None,
                    effective_from=None,
                )
            )
        return documents


class _JsonStateAdapter:
    """Normalized enacted-text JSON. No snapshot is shipped for NY or Texas."""

    source_id = ""
    citation_template = ""
    body_name = ""
    reason = ""

    async def fetch(self, http_get) -> list[bytes]:
        raise FetchNotConfigured(self.reason)

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        payload = json.loads(blob.decode("utf-8"))
        rows = payload if isinstance(payload, list) else [payload.get("result", payload)]
        documents = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = (row.get("text") or "").strip()
            location = str(row.get("locationId") or row.get("citation") or "").strip()
            title = (row.get("title") or "").strip()
            if not text or not location:
                continue
            citation = location if location[:1].isdigit() is False and "§" in location else self.citation_template.format(location=location)
            if citation == location and "§" not in citation and self.citation_template:
                citation = self.citation_template.format(location=location)
            paragraphs = row.get("paragraphs") or []
            if paragraphs:
                sections = [
                    Section(item.get("path") or "", item.get("heading") or "", item.get("text") or "", [])
                    for item in paragraphs
                    if (item.get("text") or "").strip()
                ]
            else:
                sections = [Section("", title, text, [])]
            documents.append(
                build_document(
                    source,
                    citation=citation,
                    title=title or citation,
                    level="state",
                    kind="statute",
                    body=self.body_name,
                    sections=sections,
                    url=row.get("url") or source.canonical_url,
                    status="in_force",
                    doc_date=None,
                    effective_from=None,
                    text=text,
                )
            )
        return documents


class NewYorkAdapter(_JsonStateAdapter):
    source_id = "ny-laws"
    citation_template = "N.Y. U.C.C. Law § {location}"
    body_name = "New York Legislature"
    reason = "nysenate.gov returned HTTP 403. No enacted text was saved. Supply a snapshot from the Open Legislation API."


class TexasAdapter(_JsonStateAdapter):
    source_id = "texas-statutes"
    citation_template = "{location}"
    body_name = "Texas Legislature"
    reason = (
        "statutes.capitol.texas.gov returned a script shell without the enacted text. "
        "Supply a snapshot of the official section JSON."
    )
