"""Rebuildable row-id index. Claims, evidence and private metadata stay in SQL."""

import hashlib
from typing import Any

from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    HasIdCondition,
    MatchValue,
    PointStruct,
    VectorParams,
)
from hermes.record_schema import schema_for


def collection(agent: str, model: str) -> str:
    schema_for(agent)
    return (
        "memory_rows_" + agent + "_" + hashlib.sha256(model.encode()).hexdigest()[:20]
    )


class RecordIndex:
    def __init__(self, client: Any, dimensions: int):
        self.client, self.dimensions = client, dimensions

    def upsert(self, row: dict, vector: list[float], model: str) -> None:
        name = collection(row["agent"], model)
        if len(vector) != self.dimensions:
            raise ValueError("Embedding dimensions changed")
        if not self.client.collection_exists(name):
            try:
                self.client.create_collection(
                    name,
                    vectors_config=VectorParams(
                        size=self.dimensions, distance=Distance.COSINE
                    ),
                )
            except Exception:
                if not self.client.collection_exists(name):
                    raise
        payload = {k: str(row[k]) for k in ("tenant_id", "agent", "tier", "kind")}
        payload.update(row_id=str(row["id"]), embed_model=model)
        self.client.upsert(
            name,
            points=[PointStruct(id=str(row["id"]), vector=vector, payload=payload)],
            wait=True,
        )

    def search(
        self,
        agent: str,
        tenant: str,
        tier: str,
        vector: list[float],
        model: str,
        kind: str | None,
        limit: int,
    ) -> list[tuple[str, float]]:
        name = collection(agent, model)
        if not self.client.collection_exists(name):
            return []
        scope = dict(tenant_id=tenant, agent=agent, tier=tier, embed_model=model)
        if kind:
            scope["kind"] = kind
        result = self.client.query_points(
            name,
            query=vector,
            limit=limit,
            with_payload=True,
            query_filter=Filter(
                must=[
                    FieldCondition(key=k, match=MatchValue(value=v))
                    for k, v in scope.items()
                ]
            ),
        )
        return [
            (str(p.id), float(p.score))
            for p in result.points
            if all((p.payload or {}).get(k) == v for k, v in scope.items())
            and (p.payload or {}).get("row_id") == str(p.id)
        ]

    def delete(self, agent: str, row_id: str, model: str, tenant: str) -> None:
        name = collection(agent, model)
        if self.client.collection_exists(name):
            self.client.delete(
                name,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            HasIdCondition(has_id=[row_id]),
                            FieldCondition(
                                key="tenant_id", match=MatchValue(value=tenant)
                            ),
                        ]
                    )
                ),
                wait=True,
            )
