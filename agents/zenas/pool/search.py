"""Combine keyword and meaning ranks; PostgreSQL decides version visibility."""
from __future__ import annotations

import logging
from datetime import date

from agents.zenas.core.citation_sources import public_source_url
from agents.zenas.pool.config import jurisdiction_for
from agents.zenas.pool.embed import Embedder, VectorIndex, collection_name
from agents.zenas.pool.region import RegionError, assert_box_region
from agents.zenas.pool.store import Store

log = logging.getLogger("zenas.pool")


def _region(store, jurisdiction):
    region = assert_box_region(store.home_region)
    if jurisdiction != jurisdiction_for(region):
        raise RegionError("this box does not read the other region's law")


async def search_meaning(store: Store, embedder: Embedder, index: VectorIndex, query: str, *,
                         jurisdiction: str, limit: int = 10, as_at: date | None = None,
                         level: str | None = None, kind: str | None = None) -> dict:
    _region(store, jurisdiction)
    vectors = await embedder.embed([query])
    if not vectors:
        return {"chunks": [], "reason": "embeddings unavailable", "collection": collection_name(jurisdiction)}
    async def pages():
        if hasattr(index, "query_pages"):
            async for chunk_ids in index.query_pages(jurisdiction, vectors[0], limit=limit):
                yield chunk_ids
        else:
            yield await index.query(jurisdiction, vectors[0], limit=limit)
    chunks = []
    async for chunk_ids in pages():
        rows = await store.get_chunks(chunk_ids, jurisdiction=jurisdiction, as_at=as_at, level=level, kind=kind)
        chunks.extend(chunk for chunk in rows if public_source_url(chunk.url))
        if len(chunks) >= limit:
            break
    return {"chunks": chunks[:limit],
            "reason": "ok", "collection": collection_name(jurisdiction)}


async def search_hybrid(store, embedder, index, query, *, keyword_query, jurisdiction, as_at=None,
                        level=None, kind=None, limit=8):
    _region(store, jurisdiction)
    count = min(200, max(limit * 4, 32))
    keywords = await store.search_text(keyword_query, jurisdiction=jurisdiction, as_at=as_at,
                                       level=level, kind=kind, limit=count) if keyword_query else []
    try:
        meaning = (await search_meaning(store, embedder, index, query, jurisdiction=jurisdiction,
                                       as_at=as_at, level=level, kind=kind, limit=count))["chunks"]
    except RegionError:
        raise
    except Exception:
        # A vector outage must not hide sourced keyword results or leak a driver
        # message. This is observable in server logs, with no new page wording.
        log.warning("meaning search unavailable")
        meaning = []
    scores = {}
    for rows in (keywords, meaning):
        for rank, chunk in enumerate(rows, 1):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0) + 1 / (60 + rank)
    ordered = sorted(scores, key=lambda key: (-scores[key], key))
    # Rehydrate every candidate, including keywords, from the exact stored
    # version. Qdrant cannot replace citations, URLs or dates supplied by law.
    chunks = await store.get_chunks(ordered, jurisdiction=jurisdiction, as_at=as_at, level=level, kind=kind)
    return [chunk for chunk in chunks if public_source_url(chunk.url)][:limit]
