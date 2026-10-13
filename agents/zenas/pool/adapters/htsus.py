"""USITC Harmonized Tariff Schedule JSON. Enacted HTSUS text only."""

from __future__ import annotations

import json

from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec

HTS_WAVE = ("https://hts.usitc.gov/reststop/search?keyword=8901",)


def _line(source: SourceSpec, number: str, title: str, text: str) -> LegalDocument:
    return build_document(
        source,
        citation=f"HTSUS {number}",
        title=title or f"HTSUS {number}",
        level="federal",
        kind="statute",
        body="U.S. International Trade Commission",
        sections=[Section("", title or number, text, [])],
        url="https://hts.usitc.gov/",
        status="in_force",
        doc_date=None,
        effective_from=None,
        text=text,
    )


def _rate_text(row: dict) -> str:
    description = (row.get("description") or "").strip()
    parts = [description]
    general = row.get("general")
    other = row.get("other")
    if general:
        parts.append(f"General rate: {general}.")
    if other:
        parts.append(f"Other rate: {other}.")
    return " ".join(part for part in parts if part)


class HtsusAdapter:
    source_id = "usitc-htsus"

    async def fetch(self, http_get) -> list[bytes]:
        blobs = []
        for url in HTS_WAVE:
            blobs.append(
                await http_get(url, {"Accept": "application/json", "User-Agent": "TervoryZenasPool/0.1"})
            )
        return blobs

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        payload = json.loads(blob.decode("utf-8"))
        rows = payload if isinstance(payload, list) else payload.get("results", [])
        documents: list[LegalDocument] = []
        heading_no = ""
        heading = ""
        children: list[Section] = []

        def flush() -> None:
            nonlocal heading_no, heading, children
            if not heading_no:
                children = []
                return
            documents.append(
                _line(
                    source,
                    heading_no,
                    heading or f"HTSUS {heading_no}",
                    heading or f"HTSUS {heading_no}",
                )
            )
            for child in children:
                documents.append(_line(source, child.path, child.heading, child.text))
            heading_no = ""
            heading = ""
            children = []

        for row in rows:
            number = str(row.get("htsno") or "").strip()
            indent = int(row.get("indent") or 0)
            if indent == 0 and number:
                flush()
                heading_no = number
                heading = (row.get("description") or "").strip()
                continue
            if not number or not heading_no:
                continue
            children.append(Section(number, (row.get("description") or "").strip(), _rate_text(row), []))
        flush()
        return documents
