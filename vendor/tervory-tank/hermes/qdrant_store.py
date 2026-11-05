"""Qdrant adapter. Same contract as the in-memory store.

One collection per agent (mem_<agent>). Search and delete always pass a
tenant filter. The vector is written once; a dedupe only updates the payload,
so the original point stays put.
"""

from __future__ import annotations

import warnings
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from hermes.store import Hit, Memory, collection_for, copy_memory


class QdrantStore:
    def __init__(self, client: QdrantClient, dim: int) -> None:
        self._client = client
        self._dim = dim
        self._ready: set[str] = set()

    def ping(self) -> bool:
        try:
            self._client.get_collections()
        except Exception:  # noqa: BLE001 — do not include the driver error; it can echo the URL
            return False
        return True

    def close(self) -> None:
        self._client.close()

    def _check(self, collection: str) -> None:
        collection_for(collection[len("mem_"):] if collection.startswith("mem_") else "")

    def _ensure(self, collection: str) -> None:
        self._check(collection)
        if collection in self._ready:
            return
        if not self._client.collection_exists(collection):
            try:
                self._client.create_collection(
                    collection_name=collection,
                    vectors_config=VectorParams(size=self._dim, distance=Distance.COSINE),
                )
            except Exception:  # noqa: BLE001 — another request may have created it first
                if not self._client.collection_exists(collection):
                    raise
        for field_name in ("tenant_id", "kind"):
            try:
                with warnings.catch_warnings():
                    # Local Qdrant warns that indexes do nothing. Server Qdrant uses them.
                    warnings.simplefilter("ignore", UserWarning)
                    self._client.create_payload_index(
                        collection_name=collection,
                        field_name=field_name,
                        field_schema=PayloadSchemaType.KEYWORD,
                    )
            except Exception:  # noqa: BLE001 — an existing index is fine; filters still work
                pass
        self._ready.add(collection)

    def add(self, collection: str, memory: Memory, vector: list[float]) -> None:
        self._ensure(collection)
        if len(vector) != self._dim:
            raise ValueError("vector length")
        self._client.upsert(
            collection_name=collection,
            points=[
                PointStruct(
                    id=memory.id,
                    vector=vector,
                    payload=_payload(memory),
                )
            ],
        )

    def update(self, collection: str, memory: Memory) -> None:
        self._check(collection)
        found = self._client.retrieve(collection_name=collection, ids=[memory.id])
        if not found:
            raise KeyError(memory.id)
        self._client.set_payload(
            collection_name=collection,
            payload=_payload(memory),
            points=[memory.id],
        )

    def search(
        self,
        collection: str,
        vector: list[float],
        *,
        tenant_id: str,
        kind: str | None,
        limit: int,
    ) -> list[Hit]:
        self._check(collection)
        if not self._client.collection_exists(collection):
            return []
        result = self._client.query_points(
            collection_name=collection,
            query=vector,
            query_filter=_tenant_filter(tenant_id, kind),
            limit=limit,
            with_payload=True,
        )
        hits: list[Hit] = []
        for point in result.points:
            payload = point.payload or {}
            if str(payload.get("tenant_id", "")) != tenant_id:
                continue
            if kind is not None and str(payload.get("kind", "")) != kind:
                continue
            hits.append(Hit(_memory(str(point.id), payload), float(point.score)))
        return hits

    def delete(self, collection: str, memory_id: str, tenant_id: str | None = None) -> bool:
        self._check(collection)
        if not self._client.collection_exists(collection):
            return False
        found = self._client.retrieve(collection_name=collection, ids=[memory_id])
        if not found:
            return False
        if tenant_id is not None and (found[0].payload or {}).get('tenant_id') != tenant_id:
            return False
        self._client.delete(collection_name=collection, points_selector=[memory_id])
        return True

    def delete_tenant(self, collection: str, tenant_id: str) -> int:
        self._check(collection)
        if not self._client.collection_exists(collection):
            return 0
        filt = _tenant_filter(tenant_id, None)
        count = int(self._client.count(collection_name=collection, count_filter=filt, exact=True).count)
        if count:
            self._client.delete(collection_name=collection, points_selector=FilterSelector(filter=filt))
        return count


def _tenant_filter(tenant_id: str, kind: str | None) -> Filter:
    must = [FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))]
    if kind is not None:
        must.append(FieldCondition(key="kind", match=MatchValue(value=kind)))
    return Filter(must=must)


def _payload(memory: Memory) -> dict[str, Any]:
    return {
        "tenant_id": memory.tenant_id,
        "text": memory.text,
        "kind": memory.kind,
        "sources": list(memory.sources),
        "meta": dict(memory.meta),
        "seen": memory.seen,
        "created_at": memory.created_at,
    }


def _memory(memory_id: str, payload: dict[str, Any]) -> Memory:
    sources = payload.get("sources") or []
    if not isinstance(sources, list):
        sources = []
    meta = payload.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    return copy_memory(
        Memory(
            id=memory_id,
            tenant_id=str(payload.get("tenant_id", "")),
            text=str(payload.get("text", "")),
            kind=str(payload.get("kind", "")),
            sources=[str(item) for item in sources],
            meta={str(key): value for key, value in meta.items()},
            seen=int(payload.get("seen") or 1),
            created_at=str(payload.get("created_at", "")),
        )
    )
