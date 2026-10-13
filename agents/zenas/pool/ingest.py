"""Copy text only after the source row is recorded and the licence gate passes."""

from __future__ import annotations

from datetime import date

from agents.zenas.pool.adapters import get_adapter
from agents.zenas.pool.chunk import chunk_document
from agents.zenas.pool.embed import Embedder, NoOpEmbedder, VectorIndex, NoOpVectorIndex
from agents.zenas.pool.licence import LicenceError, may_store_text, refusal_reason
from agents.zenas.pool.model import LegalDocument, SourceSpec
from agents.zenas.pool.store import Store
from agents.zenas.pool.textutil import version_id_for


async def ingest_snapshot(
    store: Store,
    source: SourceSpec,
    blobs: list[bytes],
    *,
    on: date,
    embedder: Embedder | None = None,
    index: VectorIndex | None = None,
) -> int:
    """Record the source, then store text or a link. Returns instruments inserted."""
    await store.record_source(source)
    reason = refusal_reason(source)
    if not may_store_text(source):
        await store.record_link(source.source_id, source.canonical_url, reason or "not ingestible")
        if blobs:
            raise LicenceError(reason or "not ingestible")
        return 0
    adapter = get_adapter(source)
    inserted = 0
    embedder = embedder or NoOpEmbedder()
    index = index or NoOpVectorIndex()
    for blob in blobs:
        for document in adapter.parse(blob, source):
            document.licence = source.licence
            document.known_from = document.known_from or on
            document.version_id = version_id_for(
                document.id,
                document.content_hash,
                document.effective_from,
                document.known_from,
            )
            chunks = chunk_document(document)
            if await store.upsert_version(document, chunks, on=on):
                inserted += 1
                vectors = await embedder.embed([chunk.text for chunk in chunks])
                if vectors:
                    await index.upsert(document.jurisdiction, chunks, vectors, embedder.name)
    return inserted


def assert_no_text(documents: list[LegalDocument]) -> None:
    for document in documents:
        if document.text.strip():
            raise LicenceError("refused source produced text")
