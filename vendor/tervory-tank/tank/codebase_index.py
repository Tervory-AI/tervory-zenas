"""Request-local code index; only opaque reusable embeddings enter shared storage."""
import math

from pydantic import BaseModel, ConfigDict, Field, model_validator
from semantic_encoder import LocalEncoder


class Search(BaseModel):
    model_config = ConfigDict(extra='forbid')
    files: dict[str, str] = Field(min_length=1, max_length=16)
    query: str = Field(min_length=1, max_length=1200)
    limit: int = Field(default=5, ge=1, le=20)

    @model_validator(mode='after')
    def bounded(self):
        if any(len(name) > 500 or len(text.encode()) > 8000 for name, text in self.files.items()):
            raise ValueError('Code index input exceeds the per-file limit')
        return self


def search(body: Search):
    # DECISION no code is retained in a shared search collection. Index the
    # caller's supplied files only; C4 shares mathematical vectors, not access.
    encoder = LocalEncoder()
    query = encoder.embed(body.query)
    norm = math.sqrt(sum(v * v for v in query))
    result = []
    for name, text in body.files.items():
        for offset in range(0, len(text), 400):
            chunk = text[offset:offset + 400]
            vector = encoder.embed(chunk)
            denominator = norm * math.sqrt(sum(v * v for v in vector))
            score = sum(a * b for a, b in zip(query, vector)) / denominator if denominator else 0.
            result.append(dict(file=name, start=offset, end=offset + len(chunk), score=score))
    return sorted(result, key=lambda row: (-row['score'], row['file'], row['start']))[:body.limit]


def register(router, rt, identity, require_founder):
    import asyncio
    import json
    from fastapi import Depends, HTTPException
    import cache_metrics
    import embedding_cache

    @router.post('/codebase/search')
    async def search_codebase(body: Search, actor=Depends(identity)):
        require_founder(actor)
        with embedding_cache.measure() as counter:
            try:
                rows = await asyncio.to_thread(search, body)
            except ValueError:
                raise HTTPException(422, 'Code index input exceeds the encoder context') from None
            except RuntimeError:
                raise HTTPException(503, 'Code index encoder unavailable') from None
        req = rt.RouteRequest(tenant_id=actor.tenant_id, task_type='command.code.index', agent='tank',
            messages=[rt.Message(role='user', content=body.query)])
        answer = await rt._record_local(req, {}, model='code:embedding-index', rung=3, content=json.dumps(rows))
        answer = await cache_metrics.record(rt, req, answer, 'C4', counter.values)
        return {'matches': rows, 'receipt': answer.model_dump(mode='json')}
