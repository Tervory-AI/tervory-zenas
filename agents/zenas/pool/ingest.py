"""Copy text only after the source row is recorded and the licence gate passes."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date

from agents.zenas.pool.adapters import get_adapter
from agents.zenas.pool.blobs import text_payloads
from agents.zenas.pool.chunk import chunk_document
from agents.zenas.pool.citations import extract_citations
from agents.zenas.pool.embed import Embedder, NoOpEmbedder, VectorIndex, NoOpVectorIndex
from agents.zenas.pool.indexing import index_pending
from agents.zenas.pool.licence import LicenceError, may_store_text, refusal_reason
from agents.zenas.pool.model import LegalDocument, SourceSpec
from agents.zenas.pool.region import assert_source_region
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
    region: str | None = None,
    skips: Counter[str] | None = None,
) -> int:
    """Record the source, then store text. Returns instruments inserted.

    A parsed document with no text is not stored. It is counted in ``skips``
    under "no-text" and the load goes on. Rows an adapter drops while parsing
    (its ``skipped`` list, "reason:name") are counted by reason too.

    A source from another region is refused before any row is written.
    PDFs and images inside a zip are dropped. Disabled sources record a
    link and do not store the bytes.
    """
    if region is not None:
        assert_source_region(source, region)
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
        # Unzipping and parsing are CPU work. They run off the event loop so
        # /health and /pool/status keep answering during a load.
        payloads = await asyncio.to_thread(text_payloads, blob)
        for payload in payloads:
            documents, skipped = await asyncio.to_thread(_parse_payload, adapter, payload, source)
            if skips is not None:
                for item in skipped:
                    skips[item.split(":", 1)[0]] += 1
            for document in documents:
                if not (document.text or "").strip():
                    if skips is not None:
                        skips["no-text"] += 1
                    continue
                chunks, citations = await asyncio.to_thread(_prepare_document, document, source, on)
                if not await store.upsert_version(document, chunks, on=on):
                    if skips is not None:
                        skips["unchanged"] += 1
                else:
                    inserted += 1
                await index_pending(store, embedder, index, region=region or store.home_region,
                                    document_id=document.id)
                await _attach_cites(store, document, citations)
    return inserted


def _parse_payload(adapter, payload: bytes, source: SourceSpec) -> tuple[list[LegalDocument], list[str]]:
    """Parse one payload. Called in a worker thread."""
    documents = list(adapter.parse(payload, source))
    skipped = [str(item) for item in (getattr(adapter, "skipped", None) or [])]
    return documents, skipped


def _prepare_document(document: LegalDocument, source: SourceSpec, on: date) -> tuple[list, list[str]]:
    """Version, chunk, and cite. Called in a worker thread."""
    document.licence = source.licence
    document.known_from = document.known_from or on
    document.version_id = version_id_for(
        document.id,
        document.content_hash,
        document.effective_from,
        document.known_from,
    )
    return chunk_document(document), extract_citations(document.text)


async def _attach_cites(store: Store, document: LegalDocument, citations: list[str]) -> None:
    if not citations:
        return
    rows: list[tuple[str, str, bool]] = []
    for citation in citations:
        hits = await store.find_by_citation(
            citation,
            jurisdiction=document.jurisdiction,
            as_at=None,
        )
        to_document = hits[0].document_id if hits else ""
        rows.append((citation, to_document, bool(hits)))
    await store.record_cites(document.version_id, rows)


def assert_no_text(documents: list[LegalDocument]) -> None:
    for document in documents:
        if document.text.strip():
            raise LicenceError("refused source produced text")
