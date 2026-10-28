"""Acknowledge a chunk only after Qdrant confirms it; retries need no re-fetch."""
from agents.zenas.core.citation_sources import public_source_url
from agents.zenas.pool.config import jurisdiction_for
from agents.zenas.pool.embed import NoOpEmbedder, NoOpVectorIndex, collection_name, embedding_text
from agents.zenas.pool.region import bind_store


async def index_pending(store, embedder, index, *, region, source_ids=None, document_id=None):
    bind_store(store, region)
    if isinstance(embedder, NoOpEmbedder) or isinstance(index, NoOpVectorIndex):
        return
    jurisdiction = jurisdiction_for(region)
    after = ""
    while rows := await store.pending_embeddings(embedder.name, jurisdiction=jurisdiction,
                                                 source_ids=source_ids, document_id=document_id, after=after):
        after = rows[-1].chunk_id
        chunks = [row for row in rows if public_source_url(row.url) and row.attribution]
        if not chunks:
            continue
        try:
            vectors = await index.reuse(jurisdiction, chunks) if hasattr(index, "reuse") else [None] * len(chunks)
            missing = [i for i, vector in enumerate(vectors) if vector is None]
            if missing:
                encoded = await embedder.embed([embedding_text(chunks[i]) for i in missing])
                if not encoded or len(encoded) != len(missing):
                    raise RuntimeError("embeddings unavailable")
                for i, vector in zip(missing, encoded, strict=True):
                    vectors[i] = vector
            await index.upsert(jurisdiction, chunks, vectors, embedder.name)
            await store.record_embeddings(chunks, embedder.name, len(vectors[0]), collection_name(jurisdiction))
        except Exception:
            # Load status uses its existing wording, never an HTTP/driver error.
            raise RuntimeError("the load failed") from None
