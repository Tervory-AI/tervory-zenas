"""Federal Register API JSON. This wave stores one CBP notice."""

from __future__ import annotations

import json
from datetime import date

from agents.zenas.pool.builddoc import build_document
from agents.zenas.pool.http import USER_AGENT
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


# Nightly changes for the launch parts. The list endpoint omits citation unless asked.
FR_TITLES = ("19", "15", "22", "31")
FR_PARTS = (
    ("19", "141"),
    ("15", "730"),
    ("15", "774"),
    ("22", "120"),
    ("22", "130"),
    ("31", "501"),
)
_FIELDS = (
    "citation",
    "abstract",
    "title",
    "type",
    "publication_date",
    "html_url",
    "agencies",
    "document_number",
)


def _headers() -> dict[str, str]:
    return {"Accept": "application/json", "Accept-Encoding": "gzip", "User-Agent": USER_AGENT}


def fr_title_url(title: str) -> str:
    return fr_part_url(title, "")


def fr_part_url(title: str, part: str) -> str:
    fields = "".join(f"&fields[]={name}" for name in _FIELDS)
    part_query = f"&conditions[cfr][part]={part}" if part else ""
    return (
        "https://www.federalregister.gov/api/v1/documents.json"
        f"?conditions[cfr][title]={title}{part_query}&per_page=20&order=newest{fields}"
    )


class FederalRegisterAdapter:
    source_id = "federal-register"

    async def fetch(self, http_get) -> list[bytes]:
        blobs = []
        for number in FR_WAVE:
            blobs.append(await http_get(fr_url(number), _headers()))
        return blobs

    async def fetch_bulk(self, http_get, *, resume: str = "") -> tuple[list[bytes], str]:
        del resume
        blobs = []
        headers = _headers()
        for title, part in FR_PARTS:
            blobs.append(await http_get(fr_part_url(title, part), headers))
        return blobs, ""

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
