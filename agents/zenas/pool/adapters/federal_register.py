"""Federal Register API JSON. This wave stores one CBP notice."""

from __future__ import annotations

import json
from datetime import date

from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.model import LegalDocument, Section, SourceSpec

FR_WAVE = ("2026-20884",)

_KINDS = {
    "Rule": "regulation",
    "Proposed Rule": "guidance",
    "Notice": "guidance",
    "Presidential Document": "guidance",
}


def fr_url(document_number: str) -> str:
    return f"https://www.federalregister.gov/api/v1/documents/{document_number}.json"


class FederalRegisterAdapter:
    source_id = "federal-register"

    async def fetch(self, http_get) -> list[bytes]:
        blobs = []
        for number in FR_WAVE:
            blobs.append(
                await http_get(
                    fr_url(number),
                    {"Accept": "application/json", "User-Agent": "TervoryZenasPool/0.1"},
                )
            )
        return blobs

    def parse(self, blob: bytes, source: SourceSpec) -> list[LegalDocument]:
        payload = json.loads(blob.decode("utf-8"))
        rows = payload.get("results", [payload]) if isinstance(payload, dict) else payload
        documents = []
        for row in rows:
            citation = (row.get("citation") or "").strip()
            abstract = (row.get("abstract") or "").strip()
            full_text = (row.get("full_text") or "").strip()
            text = full_text or abstract
            if not citation or not text:
                continue
            published = row.get("publication_date") or None
            doc_date = date.fromisoformat(published) if published else None
            agencies = row.get("agencies") or []
            body = agencies[0].get("name") if agencies else "Federal Register"
            kind = _KINDS.get(row.get("type") or "", "guidance")
            title = (row.get("title") or citation).strip()
            documents.append(
                build_document(
                    source,
                    citation=citation,
                    title=title,
                    level="federal",
                    kind=kind,  # type: ignore[arg-type]
                    body=body,
                    sections=[Section("", title, text, [])],
                    url=row.get("html_url") or "https://www.federalregister.gov/",
                    status="published",
                    doc_date=doc_date,
                    effective_from=doc_date,
                    text=text,
                )
            )
        return documents
