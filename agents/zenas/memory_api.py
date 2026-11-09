"""Personal memory controls use the shipped shared signed-user verifier."""
from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from agents.zenas import gates_memory as gm


@dataclass(frozen=True)
class SignedInActor:
    tenant_id: UUID
    user_id: str
    role: str = "member"


def is_moot_path(path: str) -> bool:
    return path == "/moot" or path.startswith("/moot/")


async def verified_request_actor(request: Request, tenant: str | None, *, required=False):
    """Verify once before replay, then reuse request-local verified identity only."""
    from chassis.user_assertion import HEADER, signed_user
    present = HEADER in request.headers
    if not tenant:
        if required or present:
            raise HTTPException(401, "Signed identity required")
        return None
    actor = await signed_user(request, tenant_id=tenant, product="zenas", required=required or present)
    if actor is None:
        return None
    for field, expected in (("x-moot-actor", actor.user_id), ("x-moot-tenant", actor.tenant_id),
                            ("x-street-law-actor", actor.user_id), ("x-street-law-tenant", actor.tenant_id)):
        if request.headers.get(field) not in (None, expected):
            raise HTTPException(403, "Memory ownership required")
    return SignedInActor(UUID(actor.tenant_id), actor.user_id, actor.role)


async def verified_actor(request: Request, tenant: str | None, *, required=False):
    """A valid Moot identity never grants access to Zenas personal memory."""
    if is_moot_path(request.url.path):
        if required:
            raise HTTPException(403, "Memory ownership required")
        return None
    return await verified_request_actor(request, tenant, required=required)


async def signed_in_actor(request: Request) -> SignedInActor:
    expected = os.environ.get("TANK_KEY", "")
    if not expected or not hmac.compare_digest(request.headers.get("x-tank-key", ""), expected):
        raise HTTPException(401, "Room authentication required")
    tenant = request.query_params.get("tenant_id")
    if request.method == "POST":
        body = await request.json()
        tenant = body.get("tenant_id") if isinstance(body, dict) else tenant
    return await verified_actor(request, tenant, required=True)


def refuse_scope_widen(actor, *, body_tenant_id=None, body_user_id=None):
    if ((body_tenant_id is not None and str(body_tenant_id) != str(actor.tenant_id)) or
            (body_user_id and str(body_user_id).strip() != actor.user_id)):
        raise HTTPException(403, "Memory ownership required")


class MemoryEditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    value: Any
    memory_id: str | None = None
    user_id: str | None = Field(default=None, max_length=200)
    firm_id: str | None = None
    attorney_id: str | None = None
    client_id: str | None = None
    matter_id: str | None = None


class MemoryForgetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    memory_id: str
    user_id: str | None = Field(default=None, max_length=200)


class MemorySwitchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    enabled: bool
    user_id: str | None = Field(default=None, max_length=200)


def register_routes(app):
    def memory():
        client = getattr(app.state, "memory", None)
        if client is None:
            raise HTTPException(503, "Memory unavailable")
        return client

    async def rows(actor):
        return await gm.memory_call(memory().records(str(actor.tenant_id), user_id=actor.user_id))

    async def owned(actor, ident):
        row = gm.owned_memory_row(await rows(actor), memory_id=ident,
                                  tenant_id=str(actor.tenant_id), user_id=actor.user_id)
        if row is None:
            raise HTTPException(403, "Memory ownership required")
        return row

    def gate(actor, row=None):
        meta = (row or {}).get("meta") or {}
        return gm.GateMemory(memory(), str(actor.tenant_id), actor.user_id,
            **{k: meta.get(k) for k in ("firm_id", "attorney_id", "client_id", "matter_id")})

    async def invalidate_commands(actor):
        # Preserve the paid claim and financial receipt, but never replay a
        # response containing forgotten/superseded personal content.
        from chassis.tenant_db import tenant_scope
        with tenant_scope(str(actor.tenant_id)):
            await app.state.pool.execute("""UPDATE agent_zenas.command_receipts SET receipt=NULL
                WHERE tenant_id=$1 AND user_id=$2 AND receipt IS NOT NULL""", actor.tenant_id, actor.user_id)

    @app.get("/memory/records")
    async def view(tenant_id: UUID, actor=Depends(signed_in_actor), user_id: str | None = None,
                   firm_id: str | None = None, attorney_id: str | None = None,
                   client_id: str | None = None, matter_id: str | None = None, tier: str = "client"):
        refuse_scope_widen(actor, body_tenant_id=tenant_id, body_user_id=user_id)
        if tier != "client":
            raise HTTPException(403, "Memory ownership required")
        found = await rows(actor)
        off = gm.memory_off_from_rows(found, tenant_id=str(tenant_id), user_id=actor.user_id)
        filters = dict(firm_id=firm_id, attorney_id=attorney_id, client_id=client_id, matter_id=matter_id)
        found = [r for r in found if r.get("kind") in gm.REMEMBERS
                 and gm.owned_memory_row([r], memory_id=r["id"], tenant_id=str(tenant_id), user_id=actor.user_id)
                 and all(not value or (r.get("meta") or {}).get(key) == value for key, value in filters.items())]
        if off:
            found = []
        return {"memories": [{"id": r["id"], "kind": r["kind"], "meta": r.get("meta") or {}} for r in found],
                "memory_enabled": not off, "count": len(found), "remembers": gm.REMEMBERS}

    @app.get("/memory/records/{memory_id}")
    async def get(memory_id: UUID, tenant_id: UUID, actor=Depends(signed_in_actor)):
        refuse_scope_widen(actor, body_tenant_id=tenant_id)
        return await owned(actor, str(memory_id))

    @app.post("/memory/edit")
    async def edit(body: MemoryEditIn, actor=Depends(signed_in_actor)):
        refuse_scope_widen(actor, body_tenant_id=body.tenant_id, body_user_id=body.user_id)
        row = await owned(actor, body.memory_id)
        scoped = gate(actor, row)
        if not await scoped.enabled():
            raise HTTPException(409, "Memory is switched off")
        saved = await scoped.edit(row["id"], body.value)
        await invalidate_commands(actor)
        return {"ok": True, "memory": {"id": saved["id"], "kind": saved["kind"]}, "memory_enabled": True}

    @app.post("/memory/forget")
    async def forget(body: MemoryForgetIn, actor=Depends(signed_in_actor)):
        refuse_scope_widen(actor, body_tenant_id=body.tenant_id, body_user_id=body.user_id)
        row = await owned(actor, body.memory_id)
        await gate(actor, row).forget(row["id"])
        await invalidate_commands(actor)
        return {"ok": True, "forgotten": body.memory_id, "user_id": actor.user_id}

    @app.post("/memory/switch")
    async def switch(body: MemorySwitchIn, actor=Depends(signed_in_actor)):
        refuse_scope_widen(actor, body_tenant_id=body.tenant_id, body_user_id=body.user_id)
        await gate(actor).set_enabled(body.enabled)
        return {"ok": True, "memory_enabled": body.enabled, "user_id": actor.user_id}
