"""Private Zenas law interface; uses this box's existing encoder and Qdrant."""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from hermes.auth import identify
from hermes.embeddings import MODEL_ID


class LawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jurisdiction: Literal["us", "za"]
    model: str


class EmbeddingsIn(LawRequest):
    texts: list[str] = Field(min_length=1, max_length=64)


class LawPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    version_date: str
    known_from: str = ""
    attribution: str = Field(min_length=1)
    licence: str
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    vector: list[float] = Field(min_length=1, max_length=384)


class PointsIn(LawRequest):
    points: list[LawPoint] = Field(min_length=1, max_length=64)


class ReuseIn(LawRequest):
    fingerprints: list[str] = Field(min_length=1, max_length=64)


class SearchIn(LawRequest):
    vector: list[float] = Field(min_length=1, max_length=384)
    limit: int = Field(default=32, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


def register_law_routes(app, current):
    def checked(body, key):
        if identify(key) != "zenas":
            raise HTTPException(status_code=403, detail="memory store unreachable")
        ready, encoder = current()
        law = getattr(ready, "law", None)
        if law is None or body.model != MODEL_ID:
            raise HTTPException(status_code=503, detail="memory store unreachable")
        try:
            law.check(body.jurisdiction, body.model)
        except ValueError:
            raise HTTPException(status_code=409, detail="memory store unreachable") from None
        return law, encoder

    async def run(function, *args, **kwargs):
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except Exception:
            # Driver errors can contain keys or text. Reuse the existing failure
            # state; never return the exception or introduce page wording.
            raise HTTPException(status_code=503, detail="memory store unreachable") from None

    @app.post("/law/embeddings")
    async def embeddings(body: EmbeddingsIn, x_tank_key: str = Header(default="")):
        _, encoder = checked(body, x_tank_key)
        vectors = await run(encoder.embed_many, body.texts)
        return {"model": MODEL_ID, "vectors": vectors}

    @app.post("/law/points")
    async def upsert(body: PointsIn, x_tank_key: str = Header(default="")):
        law, _ = checked(body, x_tank_key)
        await run(law.upsert, body.jurisdiction, [point.model_dump() for point in body.points], body.model)
        return {"ok": True}

    @app.post("/law/reuse")
    async def reuse(body: ReuseIn, x_tank_key: str = Header(default="")):
        law, _ = checked(body, x_tank_key)
        return {"vectors": await run(law.reuse, body.jurisdiction, body.fingerprints, body.model)}

    @app.post("/law/search")
    async def search(body: SearchIn, x_tank_key: str = Header(default="")):
        law, _ = checked(body, x_tank_key)
        return {"chunk_ids": await run(law.query, body.jurisdiction, body.vector, body.model, limit=body.limit, offset=body.offset)}
