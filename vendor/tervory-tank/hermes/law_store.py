"""Public law vectors, isolated from tenant memories and from the other region.

Only the authenticated Zenas room uses this store. The PostgreSQL law pool is
still authoritative for text, dates, source attribution and version visibility.
"""
from __future__ import annotations

import math
import warnings
from uuid import NAMESPACE_URL, uuid5

from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PayloadSchemaType, PointStruct, VectorParams

from hermes.embeddings import MODEL_ID


def jurisdiction_for(region: str) -> str:
    if region not in {"us-east", "za"}:
        raise ValueError("unknown law region")
    return "za" if region == "za" else "us"


class LawIndex:
    def __init__(self, client, dim: int, *, region: str):
        self.client, self.dim = client, dim
        self.jurisdiction = jurisdiction_for(region)
        self.collection = "law_" + self.jurisdiction
        self._ready = False

    def check(self, jurisdiction: str, model: str) -> None:
        if jurisdiction != self.jurisdiction or model != MODEL_ID:
            raise ValueError("law region or embedding model mismatch")

    def _vector(self, vector):
        if len(vector) != self.dim or not all(math.isfinite(x) for x in vector) or not any(vector):
            raise ValueError("invalid law vector")

    def _ensure(self):
        if self._ready:
            return
        if not self.client.collection_exists(self.collection):
            try:
                self.client.create_collection(self.collection, vectors_config=VectorParams(size=self.dim, distance=Distance.COSINE))
            except Exception:
                if not self.client.collection_exists(self.collection):
                    raise
        config = self.client.get_collection(self.collection).config.params.vectors
        if not isinstance(config, VectorParams) or config.size != self.dim or config.distance != Distance.COSINE:
            raise ValueError("law collection vector configuration mismatch")
        for field in ("jurisdiction", "model", "fingerprint"):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                self.client.create_payload_index(self.collection, field, PayloadSchemaType.KEYWORD, wait=True)
        self._ready = True

    def upsert(self, jurisdiction, points, model):
        self.check(jurisdiction, model)
        for point in points:
            self._vector(point["vector"])
            if not point["url"] or not point["attribution"]:
                raise ValueError("law source is missing")
        self._ensure()
        self.client.upsert(self.collection, points=[PointStruct(
            id=str(uuid5(NAMESPACE_URL, self.collection + ":" + point["chunk_id"])),
            vector=point["vector"], payload={**{key: value for key, value in point.items() if key != "vector"},
                                          "model": model, "jurisdiction": jurisdiction},
        ) for point in points], wait=True)

    def reuse(self, jurisdiction, fingerprints, model):
        self.check(jurisdiction, model)
        if not self.client.collection_exists(self.collection):
            return [None] * len(fingerprints)
        self._ensure()
        vectors = []
        for fingerprint in fingerprints:
            rows, _ = self.client.scroll(self.collection, scroll_filter=Filter(must=[
                FieldCondition(key="jurisdiction", match=MatchValue(value=jurisdiction)),
                FieldCondition(key="model", match=MatchValue(value=model)),
                FieldCondition(key="fingerprint", match=MatchValue(value=fingerprint)),
            ]), limit=1, with_vectors=True, with_payload=False)
            vectors.append(rows[0].vector if rows else None)
        return vectors

    def query(self, jurisdiction, vector, model, *, limit, offset=0):
        self.check(jurisdiction, model)
        self._vector(vector)
        if not self.client.collection_exists(self.collection):
            return []
        self._ensure()
        result = self.client.query_points(self.collection, query=vector, query_filter=Filter(must=[
            FieldCondition(key="jurisdiction", match=MatchValue(value=jurisdiction)),
            FieldCondition(key="model", match=MatchValue(value=model)),
        ]), limit=limit, offset=offset, with_payload=True, with_vectors=False)
        return [point.payload["chunk_id"] for point in result.points if point.payload
                and point.payload.get("url") and point.payload.get("attribution")
                and point.payload.get("jurisdiction") == jurisdiction and point.payload.get("model") == model]
