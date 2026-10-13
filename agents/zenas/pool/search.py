"""Citation and full-text search go to the store. Meaning search needs Hermes."""

from __future__ import annotations

from datetime import date

from agents.zenas.pool.embed import Embedder, VectorIndex, collection_name
from agents.zenas.pool.store import Store


async def search_meaning(
    store: Store,
    embedder: Embedder,
    index: VectorIndex,
    query: str,
    *,
    jurisdiction: str,
    limit: int = 10,
) -> dict:
    vectors = await embedder.embed([query])
    if not vectors:
        return {
            "chunks": [],
            "reason": "embeddings unavailable",
            "collection": collection_name(jurisdiction),
        }
    chunk_ids = await index.query(jurisdiction, vectors[0], limit=limit)
    chunks = await store.get_chunks(chunk_ids, jurisdiction=jurisdiction)
    return {
        "chunks": chunks,
        "reason": "ok",
        "collection": collection_name(jurisdiction),
    }
