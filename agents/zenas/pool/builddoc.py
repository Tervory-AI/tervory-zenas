"""Fill version ids once the text and citation are known."""

from __future__ import annotations

from datetime import date

from agents.zenas.pool.model import Kind, LegalDocument, Level, Section, SourceSpec
from agents.zenas.pool.textutil import content_hash, document_id_for, flatten_text, version_id_for


def build_document(
    source: SourceSpec,
    *,
    citation: str,
    title: str,
    level: Level,
    kind: Kind,
    body: str,
    sections: list[Section],
    url: str,
    status: str,
    doc_date: date | None,
    effective_from: date | None,
    text: str = "",
) -> LegalDocument:
    full = text.strip() or flatten_text(sections)
    digest = content_hash(full)
    doc_id = document_id_for(source.source_id, citation)
    return LegalDocument(
        id=doc_id,
        jurisdiction=source.jurisdiction,
        level=level,
        body=body,
        kind=kind,
        citation=citation,
        title=title,
        date=doc_date,
        effective_from=effective_from,
        effective_to=None,
        status=status,
        url=url,
        licence=source.licence,
        text=full,
        sections=sections,
        source_id=source.source_id,
        version_id=version_id_for(doc_id, digest, effective_from),
        content_hash=digest,
    )
