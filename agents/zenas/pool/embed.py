"""Local-region embeddings through the existing Hermes corridor.

Production PostgreSQL loads use Hermes. NoOp implementations remain available
for offline parsing tests; they are never the production live default.
"""

from __future__ import annotations

import hashlib
import os
from typing import Protocol

import httpx

from agents.zenas.pool.config import jurisdiction_for
from agents.zenas.pool.region import RegionError, assert_box_region

from agents.zenas.pool.model import Chunk


class Embedder(Protocol):
    name: str

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Return one vector per text, or None when this embedder does not run."""


class VectorIndex(Protocol):
    async def upsert(
        self,
        jurisdiction: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
        model: str,
    ) -> None: ...

    async def query(
        self,
        jurisdiction: str,
        vector: list[float],
        *,
        limit: int,
    ) -> list[str]:
        """Return chunk ids, best first."""


class NoOpEmbedder:
    name = "noop"

    async def embed(self, texts: list[str]) -> None:
        return None


class NoOpVectorIndex:
    """Explicit offline test backend. Does not store vectors."""

    async def upsert(
        self,
        jurisdiction: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
        model: str,
    ) -> None:
        return None

    async def query(
        self,
        jurisdiction: str,
        vector: list[float],
        *,
        limit: int,
    ) -> list[str]:
        return []


def collection_name(jurisdiction: str) -> str:
    if jurisdiction not in {"us", "za"}:
        raise RegionError("unknown law jurisdiction")
    return f"law_{jurisdiction}"


LAW_MODEL = "BAAI/bge-small-en-v1.5@aa8f8b060edb00e03bfdd08813a2949946c8ba55"


def embedding_text(chunk):
    return "\n".join((chunk.heading, chunk.text))


def fingerprint(chunk):
    return hashlib.sha256(embedding_text(chunk).encode()).hexdigest()


class HermesLawClient:
    name = LAW_MODEL

    def __init__(self, *, region: str, transport=None):
        self.region = assert_box_region(region)
        self.jurisdiction = jurisdiction_for(region)
        self._transport = transport

    def _check(self, jurisdiction):
        if jurisdiction != self.jurisdiction:
            raise RegionError("this box does not read the other region's law")

    async def _post(self, path, payload):
        # Existing private Docker corridor only. Never send text, vectors or a
        # room credential to a configurable public URL, redirect or HTTP proxy.
        base = os.environ.get("HERMES_URL", "http://hermes:8000").rstrip("/")
        if base != "http://hermes:8000":
            raise RegionError("Hermes must be on this box")
        key = os.environ.get("TANK_KEY", "")
        if not key:
            raise RuntimeError("Hermes is not configured")
        async with httpx.AsyncClient(base_url=base, transport=self._transport, trust_env=False,
                                     follow_redirects=False, timeout=120) as client:
            response = await client.post(path, headers={"X-Tank-Key": key}, json={
                "jurisdiction": self.jurisdiction, "model": self.name, **payload})
            if response.status_code != 200:
                # Never include response text, request headers, or the exception URL.
                raise RuntimeError("meaning search unavailable")
            return response.json()

    async def embed(self, texts):
        if not texts:
            return []
        reply = await self._post("/law/embeddings", {"texts": texts})
        if reply.get("model") != self.name or len(reply.get("vectors", [])) != len(texts):
            raise RuntimeError("embedding model mismatch")
        return reply["vectors"]

    async def upsert(self, jurisdiction, chunks, vectors, model):
        self._check(jurisdiction)
        if model != self.name or len(chunks) != len(vectors) or any(c.jurisdiction != jurisdiction for c in chunks):
            raise ValueError("law vector mismatch")
        await self._post("/law/points", {"points": [
            {"chunk_id": chunk.chunk_id, "version_id": chunk.version_id, "vector": vector,
             "url": chunk.url, "version_date": chunk.version_date, "known_from": chunk.known_from,
             "attribution": chunk.attribution, "licence": chunk.licence, "fingerprint": fingerprint(chunk)}
            for chunk, vector in zip(chunks, vectors, strict=True)]})

    async def reuse(self, jurisdiction, chunks):
        self._check(jurisdiction)
        reply = await self._post("/law/reuse", {"fingerprints": [fingerprint(chunk) for chunk in chunks]})
        vectors = reply["vectors"]
        if len(vectors) != len(chunks):
            raise RuntimeError("law vector mismatch")
        return vectors

    async def query(self, jurisdiction, vector, *, limit):
        self._check(jurisdiction)
        reply = await self._post("/law/search", {"vector": vector, "limit": limit})
        return reply["chunk_ids"]


    async def query_pages(self, jurisdiction, vector, *, limit):
        self._check(jurisdiction)
        offset = 0
        while True:
            reply = await self._post("/law/search", {"vector": vector, "limit": limit, "offset": offset})
            chunk_ids = reply["chunk_ids"]
            if not chunk_ids:
                return
            yield chunk_ids
            if len(chunk_ids) < limit:
                return
            offset += limit


def live_backends(store, region, embedder=None, index=None):
    """Only PostgreSQL is deployed; MemoryStore is the explicit offline store."""
    from agents.zenas.pool.store import PostgresStore
    if embedder is None and index is None and isinstance(store, PostgresStore):
        client = HermesLawClient(region=region)
        return client, client
    return embedder or NoOpEmbedder(), index or NoOpVectorIndex()
