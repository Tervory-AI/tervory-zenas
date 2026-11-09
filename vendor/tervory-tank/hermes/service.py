"""Tenant-scoped SQL memories and legacy vector reads.

Agent keys stay agent-scoped. Founder writes require the workspace key AND
an allowlisted founder identity. Production new writes always use Records.
The injected legacy store keeps existing vector-only tests/readers compatible.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from hermes.auth import agent_key_hashes, identify
from hermes.embeddings import Embedder
from hermes.records import ClaimTooLong
from hermes.user_scope import write_meta, wide_kinds
from hermes.record_routes import claim_validation_error
from hermes.store import Hit, Memory, MemoryStore, collection_for, copy_memory

log = logging.getLogger("hermes")

DEDUPE_SCORE = 0.97
Connect = Callable[[], tuple[MemoryStore, Embedder]]


class MemoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    tenant_id: uuid.UUID
    text: str = Field(min_length=1, max_length=8000)
    kind: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_.]{0,79}$")
    source: str = Field(min_length=1, max_length=2000)
    meta: dict[str, Any] = Field(default_factory=dict)
    agent: str | None = Field(default=None, max_length=61)
    evidence: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=.5, ge=0, le=1)
    last_verified: datetime | None = None
    id: uuid.UUID | None = None
    index: bool = True
    user_id: str | None = Field(default=None, min_length=1, max_length=200)


class RecallIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: uuid.UUID
    query: str = Field(min_length=1, max_length=2000)
    kind: str | None = Field(default=None, max_length=80, pattern=r"^[a-z][a-z0-9_.]{0,79}$")
    limit: int = Field(default=8, ge=1, le=50)
    agent: str | None = Field(default=None, max_length=61)
    tier: Literal['client', 'platform'] = 'client'
    user_id: str | None = Field(default=None, min_length=1, max_length=200)


def create_app(
    store: MemoryStore | None = None,
    embedder: Embedder | None = None,
    connect: Connect | None = None,
    records: Any = None,
) -> FastAPI:
    holder: dict[str, Any] = {"store": store, "embedder": embedder, "records": records}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        opened = False
        worker = None
        record_pool = None
        if holder["store"] is None:
            if connect is None:
                raise RuntimeError("memory store is not set")
            built_store, built_embedder = await asyncio.to_thread(connect)
            holder["store"] = built_store
            holder["embedder"] = built_embedder
            opened = True
        if opened:
            from hermes.record_runtime import connect_records
            try:
                holder['records'] = await connect_records()
            except Exception:
                holder['store'].close()
                raise
            record_pool = holder['records'].pool
            worker = asyncio.create_task(holder['records'].loop())
        app.state.store = holder["store"]
        try:
            yield
        finally:
            if worker is not None:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            if record_pool is not None:
                await record_pool.close()
                holder['records'].index.client.close()
            if opened:
                closer = getattr(holder["store"], "close", None)
                if closer is not None:
                    closer()

    app = FastAPI(title="Tervory Hermes", version="0.1.0", lifespan=lifespan)

    def current() -> tuple[MemoryStore, Embedder]:
        ready_store = holder["store"]
        ready_embedder = holder["embedder"]
        if ready_store is None or ready_embedder is None:
            raise HTTPException(status_code=503, detail="memory store unreachable")
        if not ready_store.ping():
            raise HTTPException(status_code=503, detail="memory store unreachable")
        return ready_store, ready_embedder

    def caller_agent(x_tank_key: str) -> str | None:
        return identify(x_tank_key)

    def resolve_agent(caller: str | None, requested: str | None, *, write: bool) -> str:
        if caller is None:
            if write:
                raise HTTPException(status_code=403, detail="The workspace key cannot write memories.")
            if not requested:
                raise HTTPException(status_code=400, detail="Say which agent to read.")
            if requested not in agent_key_hashes():
                raise HTTPException(status_code=404, detail="Unknown agent.")
            return requested
        if requested and requested != caller:
            raise HTTPException(status_code=403, detail="An agent cannot name another agent's collection.")
        return caller

    def collection(agent: str) -> str:
        try:
            return collection_for(agent)
        except ValueError:
            raise HTTPException(status_code=400, detail="Unknown agent.") from None


    async def enforce_personal(
        request: Request,
        *,
        method: str,
        path: str,
        key: str,
        tenant,
        user,
    ) -> tuple[str, str] | None:
        """Personal ops require nonce-protected signed tenant+user; tenant-wide (no user) skips."""
        if user is None or not str(user).strip():
            return None
        from hermes.signed_identity import verify_personal_identity
        return await verify_personal_identity(
            request=request, records=holder['records'], key=key,
            body_tenant=str(tenant) if tenant is not None else None, body_user=str(user).strip(),
        )

    @app.get("/health")
    async def health() -> dict[str, bool]:
        if holder['records'] is not None:
            await holder['records'].pool.fetchval('SELECT 1')
            return {'ok': True}
        ready, _embedder = current()
        if not ready.ping():
            raise HTTPException(status_code=503, detail="memory store unreachable")
        return {"ok": True}

    @app.patch("/memories/{memory_id}")
    async def edit_memory(
        request: Request,
        memory_id: uuid.UUID,
        body: MemoryIn,
        x_tank_key: str = Header(default=""),
    ):
        _json_meta(body.meta)
        agent = resolve_agent(caller_agent(x_tank_key), body.agent, write=True)
        if not body.user_id or (body.id is not None and body.id != memory_id) or not body.index:
            raise HTTPException(422, "Personal edit requires user_id, matching id and indexing")
        await enforce_personal(
            request,
            method="PATCH",
            path=f"/memories/{memory_id}",
            key=x_tank_key,
            tenant=body.tenant_id,
            user=body.user_id,
        )
        try:
            meta = write_meta(agent, body.kind, body.meta, body.user_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if holder['records'] is None:
            raise HTTPException(503, "memory store unreachable")
        try:
            saved = await holder['records'].edit(agent, str(body.tenant_id), str(memory_id), body.text,
                list(dict.fromkeys([body.source, *body.evidence])), user_id=body.user_id,
                kind=body.kind, meta=meta, confidence=body.confidence, last_verified=body.last_verified)
        except ClaimTooLong as exc:
            raise claim_validation_error("text", exc) from None
        if saved is None:
            raise HTTPException(404, "not found")
        return saved

    @app.post("/memories")
    async def remember(
        request: Request,
        body: MemoryIn,
        x_tank_key: str = Header(default=""),
    ) -> JSONResponse:
        _json_meta(body.meta)
        caller = caller_agent(x_tank_key)
        agent = resolve_agent(caller, body.agent, write=True)
        try:
            body.meta = write_meta(agent, body.kind, body.meta, body.user_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        # Personal rows: nonce-protected signed identity required. Declared tenant-wide
        # kinds with no user keep agent-key-only background writes.
        await enforce_personal(
            request,
            method="POST",
            path="/memories",
            key=x_tank_key,
            tenant=body.tenant_id,
            user=body.meta.get("user_id") or body.user_id,
        )
        if holder['records'] is not None:
            try:
                saved = await holder['records'].save(agent,str(body.tenant_id),body.text,
                    list(dict.fromkeys([body.source,*body.evidence])),confidence=body.confidence,
                    last_verified=body.last_verified,kind=body.kind,meta=body.meta,
                    row_id=str(body.id) if body.id else None, **({"index": False} if not body.index else {}))
                return JSONResponse(saved,status_code=201)
            except ClaimTooLong as exc:
                raise claim_validation_error("text", exc) from None
            except ValueError:
                raise HTTPException(409,'Memory record conflict') from None
            except Exception:
                log.warning('record write failed')
                raise HTTPException(503,'memory store unreachable') from None
        if body.meta.get("user_id") or not body.index:
            raise HTTPException(503, "memory store unreachable")
        name = collection(agent)
        ready, model = current()
        try:
            saved = await asyncio.to_thread(
                _remember,
                ready,
                model,
                name,
                str(body.tenant_id),
                body.text,
                body.kind,
                body.source,
                body.meta,
            )
        except HTTPException:
            raise
        except Exception:
            log.warning("remember failed")
            raise HTTPException(status_code=503, detail="memory store unreachable") from None
        log.info("remember agent=%s tenant=%s", agent, body.tenant_id)
        status = 200 if saved["deduped"] else 201
        return JSONResponse(saved, status_code=status)

    @app.post("/recall")
    async def recall(
        request: Request,
        body: RecallIn,
        x_tank_key: str = Header(default=""),
    ) -> dict[str, Any]:
        caller = caller_agent(x_tank_key)
        agent = resolve_agent(caller, body.agent, write=False)
        await enforce_personal(
            request,
            method="POST",
            path="/recall",
            key=x_tank_key,
            tenant=body.tenant_id,
            user=body.user_id,
        )
        if holder['records'] is not None:
            try:
                new_hits = await holder['records'].recall(agent,str(body.tenant_id),body.query,body.kind,body.limit,body.tier,user_id=body.user_id)
                if body.tier == 'platform' or body.user_id is not None:
                    return {'memories':new_hits}
                # Legacy vectors remain in place and readable; never imported or rewritten.
                ready, model = current()
                legacy = await asyncio.to_thread(_recall,ready,model,collection(agent),str(body.tenant_id),body.query,body.kind,body.limit)
                return {'memories':sorted(new_hits+[_hit(hit) for hit in legacy],key=lambda h:h['score'],reverse=True)[:body.limit]}
            except Exception:
                log.warning('record recall failed')
                raise HTTPException(503,'memory store unreachable') from None
        if body.user_id is not None:
            return {"memories": []}
        name = collection(agent)
        ready, model = current()
        try:
            hits = await asyncio.to_thread(
                _recall,
                ready,
                model,
                name,
                str(body.tenant_id),
                body.query,
                body.kind,
                body.limit,
            )
        except HTTPException:
            raise
        except Exception:
            log.warning("recall failed")
            raise HTTPException(status_code=503, detail="memory store unreachable") from None
        return {"memories": [_hit(hit) for hit in hits]}

    @app.delete("/memories/{memory_id}")
    async def forget(
        request: Request,
        memory_id: uuid.UUID,
        tenant_id: uuid.UUID | None = None,
        user_id: str | None = None,
        x_tank_key: str = Header(default=""),
    ) -> dict[str, bool]:
        caller = caller_agent(x_tank_key)
        agent = resolve_agent(caller, None, write=True)
        if tenant_id is None:
            raise HTTPException(422, 'A tenant UUID is required')
        await enforce_personal(
            request,
            method="DELETE",
            path=f"/memories/{memory_id}",
            key=x_tank_key,
            tenant=tenant_id,
            user=user_id,
        )
        if holder['records'] is not None:
            removed = await holder['records'].forget(agent,str(tenant_id),str(memory_id),user_id=user_id)
            if not removed:
                ready, _ = current()
                removed = False if user_id is not None else await asyncio.to_thread(ready.delete,collection(agent),str(memory_id),str(tenant_id),wide_kinds=wide_kinds(agent))
                if not removed:
                    raise HTTPException(404,'no such memory')
            return {'deleted':True}
        ready, _model = current()
        try:
            removed = False if user_id is not None else await asyncio.to_thread(ready.delete, collection(agent), str(memory_id), str(tenant_id) if tenant_id else None, wide_kinds=wide_kinds(agent))
        except HTTPException:
            raise
        except Exception:
            log.warning("forget failed")
            raise HTTPException(status_code=503, detail="memory store unreachable") from None
        if not removed:
            raise HTTPException(status_code=404, detail="no such memory")
        log.info("forget agent=%s", agent)
        return {"deleted": True}

    @app.delete("/tenants/{tenant_id}")
    async def forget_tenant(
        request: Request,
        tenant_id: uuid.UUID,
        user_id: str | None = None,
        x_tank_key: str = Header(default=""),
    ) -> dict[str, int]:
        caller = caller_agent(x_tank_key)
        agent = resolve_agent(caller, None, write=True)
        await enforce_personal(
            request,
            method="DELETE",
            path=f"/tenants/{tenant_id}",
            key=x_tank_key,
            tenant=tenant_id,
            user=user_id,
        )
        if holder['records'] is not None:
            removed = await holder['records'].forget(agent,str(tenant_id),user_id=user_id)
            ready,_ = current()
            removed += (0 if user_id is not None else await asyncio.to_thread(ready.delete_tenant,collection(agent),str(tenant_id),wide_kinds=wide_kinds(agent)))
            return {'deleted':removed}
        ready, _model = current()
        try:
            deleted = (0 if user_id is not None else await asyncio.to_thread(ready.delete_tenant, collection(agent), str(tenant_id),wide_kinds=wide_kinds(agent)))
        except HTTPException:
            raise
        except Exception:
            log.warning("forget tenant failed")
            raise HTTPException(status_code=503, detail="memory store unreachable") from None
        log.info("forget tenant agent=%s tenant=%s deleted=%s", agent, tenant_id, deleted)
        return {"deleted": deleted}

    from hermes.law_routes import register_law_routes
    register_law_routes(app, current)
    from hermes.record_routes import register_record_routes
    register_record_routes(app, lambda: holder['records'], resolve_agent)
    from hermes.engine_routes import register as register_engine
    register_engine(app, lambda: holder['records'])
    return app


def _json_meta(meta: dict[str, Any]) -> None:
    try:
        json.dumps(meta)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="meta must be JSON") from None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _remember(
    store: MemoryStore,
    embedder: Embedder,
    name: str,
    tenant_id: str,
    text: str,
    kind: str,
    source: str,
    meta: dict[str, Any],
) -> dict[str, Any]:
    vector = embedder.embed(text)
    nearest = store.search(name, vector, tenant_id=tenant_id, kind=kind, limit=1, wide_kinds=wide_kinds(name.removeprefix("mem_")))
    if nearest and nearest[0].score >= DEDUPE_SCORE:
        current = nearest[0].memory
        sources = list(current.sources)
        if source not in sources:
            sources.append(source)
        merged = dict(current.meta)
        merged.update(meta)
        updated = copy_memory(current)
        updated.sources = sources
        updated.meta = merged
        updated.seen = current.seen + 1
        store.update(name, updated)
        return {
            "id": updated.id,
            "seen": updated.seen,
            "deduped": True,
            "created_at": updated.created_at,
        }
    memory = Memory(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        text=text,
        kind=kind,
        sources=[source],
        meta=dict(meta),
        seen=1,
        created_at=_now(),
    )
    store.add(name, memory, vector)
    return {
        "id": memory.id,
        "seen": 1,
        "deduped": False,
        "created_at": memory.created_at,
    }


def _recall(
    store: MemoryStore,
    embedder: Embedder,
    name: str,
    tenant_id: str,
    query: str,
    kind: str | None,
    limit: int,
) -> list[Hit]:
    vector = embedder.embed(query)
    return store.search(name, vector, tenant_id=tenant_id, kind=kind, limit=limit, wide_kinds=wide_kinds(name.removeprefix("mem_")))


def _hit(hit: Hit) -> dict[str, Any]:
    memory = hit.memory
    source = memory.sources[0] if memory.sources else ""
    return {
        "id": memory.id,
        "tenant_id": memory.tenant_id,
        "text": memory.text,
        "kind": memory.kind,
        "score": hit.score,
        "source": source,
        "sources": list(memory.sources),
        "created_at": memory.created_at,
        "seen": memory.seen,
        "meta": dict(memory.meta),
    }
