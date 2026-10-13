"""Embeddings stay behind this interface. Hermes (C7) supplies the real one.

The default does no work and stores nothing. Meaning search returns no hits
until a real embedder and the Qdrant collection `law_<jurisdiction>` are wired.
"""

from __future__ import annotations

from typing import Protocol

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
    """Names the collection Hermes will own. Does not store vectors."""

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
    return f"law_{jurisdiction}"
