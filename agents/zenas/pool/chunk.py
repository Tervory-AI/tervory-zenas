"""Chunk by section structure. Never by a blind character count."""

from __future__ import annotations

from agents.zenas.pool.model import Chunk, LegalDocument, Section
from agents.zenas.pool.textutil import citation_key


def pinpoint(citation: str, path: str) -> str:
    if not path:
        return citation
    if path.startswith("("):
        return f"{citation}{path}"
    return f"{citation} {path}"


def chunk_document(doc: LegalDocument) -> list[Chunk]:
    chunks: list[Chunk] = []

    def walk(nodes: list[Section]) -> None:
        for node in nodes:
            if node.text.strip():
                cite = pinpoint(doc.citation, node.path)
                ordinal = len(chunks)
                chunks.append(
                    Chunk(
                        chunk_id=f"{doc.version_id}:{ordinal}",
                        version_id=doc.version_id,
                        document_id=doc.id,
                        jurisdiction=doc.jurisdiction,
                        path=node.path,
                        heading=node.heading,
                        text=node.text.strip(),
                        citation=cite,
                        citation_key=citation_key(cite),
                        ordinal=ordinal,
                    )
                )
            walk(node.children)

    if doc.sections:
        walk(doc.sections)
    if not chunks and doc.text.strip():
        chunks.append(
            Chunk(
                chunk_id=f"{doc.version_id}:0",
                version_id=doc.version_id,
                document_id=doc.id,
                jurisdiction=doc.jurisdiction,
                path="",
                heading=doc.title,
                text=doc.text.strip(),
                citation=doc.citation,
                citation_key=citation_key(doc.citation),
                ordinal=0,
            )
        )
    return chunks
