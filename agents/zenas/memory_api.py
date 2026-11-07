"""Gates 7+8 memory controls: VIEW, CONTENT EDIT, FORGET, SWITCH OFF.

Identity is never taken from a request body field or bare X-Tervory-* header.
The signed-in caller is bound by Tank's existing lifecycle request signature
(agents/ghost/enforce_api.py:verify_lifecycle / ghost_routes signing, extended
to bind tenant+user in the message with the Zenas room key — same scheme Trigger
gates 7+8 uses). Room-key HMAC alone is not actor proof. Unsigned, wrong
signature, or signed-for-another-user requests yield no reads and no changes.
"""
from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from agents.zenas import gates_memory
from agents.zenas.request_signing import room_key, verify_signed_actor


@dataclass(frozen=True)
class SignedInActor:
    """Tenant+user bound by Tank's lifecycle request signature. Body is never identity."""

    tenant_id: UUID
    user_id: str


async def authorized(x_tank_key: str = Header(default="")) -> None:
    expected = os.environ.get("TANK_KEY", "")
    if not expected or not hmac.compare_digest(expected.encode(), x_tank_key.encode()):
        raise HTTPException(401, "Room authentication required")


async def signed_in_actor(
    request: Request,
    x_tank_key: str = Header(default=""),
    x_lifecycle_timestamp: str = Header(default="", alias="X-Lifecycle-Timestamp"),
    x_lifecycle_signature: str = Header(default="", alias="X-Lifecycle-Signature"),
    x_tervory_user: str = Header(default="", alias="X-Tervory-User"),
    x_tervory_tenant: str = Header(default="", alias="X-Tervory-Tenant"),
) -> SignedInActor:
    """Refuse unless the lifecycle signature binds a verified tenant and user."""
    await authorized(x_tank_key)
    key = room_key()
    body = await request.body()
    try:
        tenant_id, user_id = verify_signed_actor(
            key=key,
            method=request.method,
            path=request.url.path,
            timestamp=x_lifecycle_timestamp,
            signature=x_lifecycle_signature,
            tenant_header=x_tervory_tenant,
            user_header=x_tervory_user,
            body=body,
        )
        gates_memory.trusted_user(user_id)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(401, "Signed identity required") from exc
    return SignedInActor(tenant_id=tenant_id, user_id=user_id)


def refuse_scope_widen(
    actor: SignedInActor,
    *,
    body_tenant_id: UUID | None = None,
    body_user_id: str | None = None,
) -> None:
    """Body tenant/user cannot widen or replace the signed-in scope."""
    if body_tenant_id is not None and body_tenant_id != actor.tenant_id:
        raise HTTPException(403, "Memory ownership required")
    if body_user_id is not None and str(body_user_id).strip() and str(body_user_id).strip() != actor.user_id:
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


def register_routes(app: Any) -> None:
    @app.get("/memory/records")
    async def memory_view(
        tenant_id: UUID,
        actor: SignedInActor = Depends(signed_in_actor),
        user_id: str | None = None,
        firm_id: str | None = None,
        attorney_id: str | None = None,
        client_id: str | None = None,
        matter_id: str | None = None,
        tier: str = Query(default="client"),
    ):
        """VIEW: list only the signed-in caller's memories in their tenant/scope."""
        refuse_scope_widen(actor, body_tenant_id=tenant_id, body_user_id=user_id)
        memory = getattr(app.state, "memory", None)
        if memory is None:
            raise HTTPException(503, "Memory unavailable")
        rows = await memory.records(str(actor.tenant_id), tier=tier)
        if gates_memory.memory_off_from_rows(
            rows, tenant_id=str(actor.tenant_id), user_id=actor.user_id
        ):
            return {"memories": [], "memory_enabled": False, "count": 0, "remembers": gates_memory.REMEMBERS}
        gate = gates_memory.GateMemory(
            memory,
            str(actor.tenant_id),
            actor.user_id,
            firm_id=firm_id,
            attorney_id=attorney_id,
            client_id=client_id,
            matter_id=matter_id,
        )
        viewed = await gate.view()
        return {
            "memories": [{"id": r["id"], "kind": r["kind"], "meta": r.get("meta") or {}} for r in viewed],
            "memory_enabled": True,
            "count": len(viewed),
            "remembers": gates_memory.REMEMBERS,
        }

    @app.post("/memory/edit")
    async def memory_edit(body: MemoryEditIn, actor: SignedInActor = Depends(signed_in_actor)):
        """CONTENT EDIT: mutate only a row owned by the signed-in caller."""
        refuse_scope_widen(actor, body_tenant_id=body.tenant_id, body_user_id=body.user_id)
        memory = getattr(app.state, "memory", None)
        if memory is None:
            raise HTTPException(503, "Memory unavailable")
        rows = await memory.records(str(actor.tenant_id))
        if gates_memory.memory_off_from_rows(
            rows, tenant_id=str(actor.tenant_id), user_id=actor.user_id
        ):
            raise HTTPException(409, "Memory is switched off")
        if not body.memory_id:
            raise HTTPException(422, "memory_id is required")
        owned = gates_memory.owned_memory_row(
            rows,
            memory_id=body.memory_id,
            tenant_id=str(actor.tenant_id),
            user_id=actor.user_id,
        )
        if owned is None:
            raise HTTPException(403, "Memory ownership required")
        meta = dict(owned.get("meta") or {})
        # Ownership already proven (tenant+user). Replace content via remember after forget;
        # scope meta stays the signed-in owner's firm/attorney/client/matter.
        await memory.forget(body.memory_id, str(actor.tenant_id))
        meta.pop("receipt", None)
        meta.pop("seq", None)
        meta["user_id"] = actor.user_id
        meta["user"] = actor.user_id
        meta["result"] = body.value
        meta["value"] = body.value
        kind = gates_memory.CORRECTION if owned.get("kind") in {
            gates_memory.RESULT, gates_memory.CORRECTION, gates_memory.WORKED_LAW
        } else owned.get("kind")
        saved = await memory.remember(
            str(actor.tenant_id),
            str(meta.get("task_key") or owned.get("kind") or "zenas") + " correction",
            kind if kind in gates_memory.REMEMBERS else gates_memory.CORRECTION,
            gates_memory.SOURCE,
            meta=meta,
            index=False,
        )
        return {"ok": True, "memory": {"id": saved["id"], "kind": kind}, "memory_enabled": True}

    @app.post("/memory/forget")
    async def memory_forget(body: MemoryForgetIn, actor: SignedInActor = Depends(signed_in_actor)):
        """FORGET: delete only a row owned by the signed-in caller."""
        refuse_scope_widen(actor, body_tenant_id=body.tenant_id, body_user_id=body.user_id)
        memory = getattr(app.state, "memory", None)
        if memory is None:
            raise HTTPException(503, "Memory unavailable")
        rows = await memory.records(str(actor.tenant_id))
        owned = gates_memory.owned_memory_row(
            rows,
            memory_id=body.memory_id,
            tenant_id=str(actor.tenant_id),
            user_id=actor.user_id,
        )
        if owned is None:
            raise HTTPException(403, "Memory ownership required")
        await memory.forget(body.memory_id, str(actor.tenant_id))
        return {"ok": True, "forgotten": body.memory_id, "user_id": actor.user_id}

    @app.post("/memory/switch")
    async def memory_switch(body: MemorySwitchIn, actor: SignedInActor = Depends(signed_in_actor)):
        """SWITCH OFF/ON for the signed-in caller's own tenant+user scope only."""
        refuse_scope_widen(actor, body_tenant_id=body.tenant_id, body_user_id=body.user_id)
        memory = getattr(app.state, "memory", None)
        if memory is None:
            raise HTTPException(503, "Memory unavailable")
        gate = gates_memory.GateMemory(memory, str(actor.tenant_id), actor.user_id)
        await gate.set_enabled(bool(body.enabled))
        return {"ok": True, "memory_enabled": bool(body.enabled), "user_id": actor.user_id}
