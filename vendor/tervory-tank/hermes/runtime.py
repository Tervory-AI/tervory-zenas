"""Production wiring. Imported by the server process, not by the unit tests."""

from __future__ import annotations

import os
from typing import Any

_MODEL = "BAAI/bge-small-en-v1.5"


def connect_production() -> tuple[Any, Any]:
    """Construct lazy legacy clients; vector outages cannot block SQL startup."""
    from qdrant_client import QdrantClient

    from hermes.embeddings import FastEmbedder
    from hermes.qdrant_store import QdrantStore

    embedder = FastEmbedder(os.environ.get("HERMES_EMBED_MODEL", _MODEL))
    api_key = os.environ.get("QDRANT_API_KEY", "").strip() or None
    # Same Docker network only. Never forward either region's vectors to an
    # alternate host, proxy or public service.
    url = os.environ.get("QDRANT_URL", "http://qdrant:6333").rstrip("/")
    if url != "http://qdrant:6333":
        raise RuntimeError("Qdrant must be on this box")
    client = QdrantClient(
        url=url,
        api_key=api_key,
        timeout=10,
        check_compatibility=False,
        trust_env=False,
        follow_redirects=False,
    )
    # A vector outage must not prevent the record service starting or committing SQL.
    from hermes.law_store import LawIndex
    store = QdrantStore(client, embedder.dim)
    store.law = LawIndex(client, embedder.dim, region=os.environ.get("TANK_REGION", ""))
    return store, embedder
