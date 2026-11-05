"""Memory rows. One collection per agent. Every read filters on tenant_id.

The in-memory store is what the tests drive. Qdrant is the same contract
in production (hermes/qdrant_store.py). Neither store picks the collection
from caller input: the service passes mem_<agent> only.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Protocol

COLLECTION = re.compile(r"^mem_[a-z][a-z0-9_]{0,60}$")


def collection_for(agent: str) -> str:
    name = "mem_" + agent
    if not COLLECTION.fullmatch(name):
        raise ValueError("bad collection")
    return name


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / math.sqrt(left_norm * right_norm)


@dataclass
class Memory:
    id: str
    tenant_id: str
    text: str
    kind: str
    sources: list[str]
    meta: dict[str, Any]
    seen: int
    created_at: str


@dataclass
class Hit:
    memory: Memory
    score: float


def copy_memory(memory: Memory) -> Memory:
    return Memory(
        id=memory.id,
        tenant_id=memory.tenant_id,
        text=memory.text,
        kind=memory.kind,
        sources=list(memory.sources),
        meta=dict(memory.meta),
        seen=memory.seen,
        created_at=memory.created_at,
    )


class MemoryStore(Protocol):
    def ping(self) -> bool: ...

    def add(self, collection: str, memory: Memory, vector: list[float]) -> None: ...

    def update(self, collection: str, memory: Memory) -> None: ...

    def search(
        self,
        collection: str,
        vector: list[float],
        *,
        tenant_id: str,
        kind: str | None,
        limit: int,
    ) -> list[Hit]: ...

    def delete(self, collection: str, memory_id: str, tenant_id: str | None = None) -> bool: ...

    def delete_tenant(self, collection: str, tenant_id: str) -> int: ...

    def close(self) -> None: ...


class InMemoryStore:
    """Process-local vectors. Cosine, tenant filter, one map per collection."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, tuple[Memory, list[float]]]] = {}

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        return None

    def _collection(self, collection: str) -> dict[str, tuple[Memory, list[float]]]:
        _agent_from(collection)
        return self._rows.setdefault(collection, {})

    def add(self, collection: str, memory: Memory, vector: list[float]) -> None:
        rows = self._collection(collection)
        rows[memory.id] = (copy_memory(memory), list(vector))

    def update(self, collection: str, memory: Memory) -> None:
        _agent_from(collection)
        rows = self._rows.get(collection)
        if not rows or memory.id not in rows:
            raise KeyError(memory.id)
        _current, vector = rows[memory.id]
        rows[memory.id] = (copy_memory(memory), vector)

    def search(
        self,
        collection: str,
        vector: list[float],
        *,
        tenant_id: str,
        kind: str | None,
        limit: int,
    ) -> list[Hit]:
        _agent_from(collection)
        rows = self._rows.get(collection, {})
        hits: list[Hit] = []
        for memory, stored in rows.values():
            if memory.tenant_id != tenant_id:
                continue
            if kind is not None and memory.kind != kind:
                continue
            hits.append(Hit(copy_memory(memory), cosine(vector, stored)))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

    def delete(self, collection: str, memory_id: str, tenant_id: str | None = None) -> bool:
        _agent_from(collection)
        rows = self._rows.get(collection)
        if not rows or memory_id not in rows:
            return False
        if tenant_id is not None and rows[memory_id][0].tenant_id != tenant_id:
            return False
        del rows[memory_id]
        return True

    def delete_tenant(self, collection: str, tenant_id: str) -> int:
        _agent_from(collection)
        rows = self._rows.get(collection)
        if not rows:
            return 0
        ids = [memory_id for memory_id, (memory, _vector) in rows.items() if memory.tenant_id == tenant_id]
        for memory_id in ids:
            del rows[memory_id]
        return len(ids)


def _agent_from(collection: str) -> str:
    if not collection.startswith("mem_"):
        raise ValueError("bad collection")
    agent = collection[len("mem_"):]
    collection_for(agent)
    return agent
