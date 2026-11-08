"""Trigger room. Only authenticated same-box Tank calls; no public page."""
from __future__ import annotations

import hmac
import os
import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from uuid import UUID

import asyncpg
import httpx
from chassis.tank import TankClient
from dataclasses import dataclass
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from trigger.service import CheckIn, CheckerIn, WriteIn, check_text, write
from trigger.voice import VoiceStore


@dataclass(frozen=True)
class SignedInActor:
    """Tenant and user verified by the shared nonce-protected assertion helper.

    Memory controls require this binding. Ordinary writing keeps Tank-key auth
    without personal identity; body or bare X-Tervory-* are never identity.
    """

    tenant_id: UUID
    user_id: str


async def authorized(x_tank_key: str = Header(default="")):
    missing = getattr(app.state, "missing_settings", [])
    if missing:
        raise HTTPException(503, {"missing_settings": missing})
    expected = os.environ.get("TANK_KEY", "")
    if not expected or not hmac.compare_digest(expected.encode(), x_tank_key.encode()):
        raise HTTPException(401, "Room authentication required")


async def _verified_actor(request: Request, x_tank_key: str, *, required: bool):
    await authorized(x_tank_key)
    if not required and not request.headers.get("X-Tervory-User-Assertion"):
        return None
    from chassis.user_assertion import signed_user
    try:
        body = await request.json() if request.method != "GET" else {}
    except ValueError:
        body = {}
    tenant = body.get("tenant_id") or request.query_params.get("tenant_id")
    actor = await signed_user(request, tenant_id=str(tenant or ""), product="trigger", required=required)
    return SignedInActor(UUID(actor.tenant_id), actor.user_id) if actor else None


async def signed_in_actor(request: Request, x_tank_key: str = Header(default="")) -> SignedInActor:
    return await _verified_actor(request, x_tank_key, required=True)


async def optional_signed_actor(request: Request, x_tank_key: str = Header(default="")) -> SignedInActor | None:
    return await _verified_actor(request, x_tank_key, required=False)


def refuse_scope_widen(actor: SignedInActor, *, body_tenant_id: UUID | None = None, body_user_id: str | None = None) -> None:
    """Body tenant/user cannot widen or replace the signed-in scope."""
    if body_tenant_id is not None and body_tenant_id != actor.tenant_id:
        raise HTTPException(403, "Memory ownership required")
    if body_user_id is not None and str(body_user_id).strip() and str(body_user_id).strip() != actor.user_id:
        raise HTTPException(403, "Memory ownership required")


@asynccontextmanager
async def lifespan(app):
    app.state.pool = None
    app.state.memory = None
    from trigger.trigger_brain import brain_enabled
    app.state.brain_enabled = brain_enabled()
    app.state.missing_settings = [name for name in ("DATABASE_URL", "TANK_KEY") if not os.environ.get(name)]
    if app.state.missing_settings:
        yield
        return
    async with AsyncExitStack() as resources:
        app.state.pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3,
                                                timeout=10, command_timeout=30)
        resources.push_async_callback(app.state.pool.close)
        resources.callback(setattr, app.state, "pool", None)
        await app.state.pool.execute(Path(__file__).with_name("schema.sql").read_text())
        app.state.http = httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False)
        resources.push_async_callback(app.state.http.aclose)
        app.state.tank = TankClient("http://tank:8000", os.environ.get("TANK_KEY", ""), "trigger", app.state.http)
        from chassis.memory import MemoryClient
        from trigger.trigger_brain import brain_enabled
        # Hermes only (HERMES_URL). Never Tank /v1/memory. The room's TANK_KEY is
        # the agent key injected from TANK_KEY_TRIGGER in compose — not the
        # Workspace shared key, which Hermes refuses for writes.
        hermes_url = (os.environ.get("HERMES_URL") or "http://hermes:8000").rstrip("/")
        if "/v1/memory" in hermes_url:
            raise RuntimeError("Trigger memory must call Hermes directly, not Tank /v1/memory")
        app.state.memory = MemoryClient(
            base_url=hermes_url,
            api_key=os.environ.get("TANK_KEY", ""),
            agent="trigger",
            http=app.state.http,
        )
        app.state.brain_enabled = brain_enabled()
        yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
from trigger.gates_memory import MemoryAuthorizationError


@app.exception_handler(MemoryAuthorizationError)
async def memory_auth_refused(request: Request, error: MemoryAuthorizationError):
    return JSONResponse(status_code=401, content={"detail": "Memory ownership required"})


REQUEST_TIMEOUT_SECONDS = 60


class RequestBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        started = False
        finished = False

        async def bounded_send(message):
            nonlocal started, finished
            if message['type'] == 'http.response.start':
                started = True
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                finished = True
            await send(message)

        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                return await self.app(scope, receive, bounded_send)
        except TimeoutError:
            detail = "The action outcome is unknown. Do not repeat it until checked."
        except Exception as error:
            logging.getLogger(__name__).warning("Trigger request unavailable (%s)", type(error).__name__)
            detail = "The action could not be completed. Please try again."
        if finished:
            return
        if started:
            await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
        else:
            await JSONResponse(status_code=503, content={'detail': detail})(scope, receive, send)


app.add_middleware(RequestBoundary)


@app.get("/health")
async def health():
    if getattr(app.state, "missing_settings", []):
        raise HTTPException(503, {"missing_settings": app.state.missing_settings})
    if not getattr(app.state, "pool", None):
        raise HTTPException(503, "Database unavailable")
    await app.state.pool.fetchval("SELECT 1")
    return {"ok": True, "agent": "trigger"}


@app.post("/check", dependencies=[Depends(authorized)])
async def post_check(body: CheckIn):
    return await check_text(body, VoiceStore(app.state.pool), app.state.http, app.state.tank)


@app.post("/write")
async def post_write(
    body: WriteIn,
    actor: SignedInActor | None = Depends(optional_signed_actor),
):
    """Tank-key auth for write (CURRENT room() has no signature).

    Memory recall/remember is fail-closed: only with a verified signed actor.
    Memory control routes still use hard signed_in_actor (401 without signature).
    """
    from trigger.gates_memory import MEMORY_OUTAGE_ERRORS, memory_enabled, memory_off_from_rows, refuse_memory_auth

    memory = None
    user_id = None
    memory_unavailable = False
    if actor is not None:
        refuse_scope_widen(actor, body_tenant_id=body.tenant_id, body_user_id=body.user_id)
        user_id = actor.user_id
        memory = getattr(app.state, "memory", None)
        if memory is not None and memory_enabled():
            try:
                rows = await memory.records(str(actor.tenant_id), user_id=actor.user_id)
            except MEMORY_OUTAGE_ERRORS as exc:
                refuse_memory_auth(exc)
                # Memory service down: the draft still goes through the paid route,
                # without recall, a stored repeat or a remember.
                rows, memory, memory_unavailable = [], None, True
            if memory is not None and memory_off_from_rows(rows, tenant_id=str(actor.tenant_id), user_id=actor.user_id):
                memory = None
        else:
            memory = None
    try:
        contact_key = (body.contact_id or "").strip() or None
        result = await write(
            body,
            VoiceStore(app.state.pool),
            app.state.tank,
            app.state.http,
            app.state.tank,
            memory=memory,
            user_id=user_id,
            contact_key=contact_key,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if memory_unavailable or (result.get("memory_receipt") or {}).get("memory_unavailable"):
        result["memory_unavailable"] = True
    return result


@app.post("/skala/languagetool", dependencies=[Depends(authorized)])
async def skala_languagetool(body: CheckerIn):
    """Transport only: the approved package owns rule/result interpretation."""
    import hashlib
    import json
    response = await app.state.http.post(
        "http://trigger-languagetool:8081/v2/check",
        data={"text": body.text, "language": "en-US"}, timeout=30,
    )
    response.raise_for_status()
    config = Path(__file__).with_name("languagetool.properties").read_bytes()
    pins = json.loads((Path(__file__).parent / "sources/pins.json").read_text())
    return {"result": response.json(), "config_sha256": hashlib.sha256(config).hexdigest(),
            "image": pins["languagetool-image"]}

from pydantic import BaseModel, ConfigDict, Field
from trigger.voice_history import VoiceHistory


class MemoryForgetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    memory_id: str = Field(min_length=1, max_length=80)
    user_id: str | None = Field(default=None, max_length=200)


class MemoryEditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    text: str = Field(min_length=1, max_length=8000)
    kind: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=200)
    user_id: str | None = Field(default=None, max_length=200)
    contact_id: str | None = Field(default=None, max_length=200)
    task_key: str | None = Field(default=None, max_length=200)
    memory_id: str | None = Field(default=None, max_length=80)


class MemorySwitchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    enabled: bool
    user_id: str | None = Field(default=None, max_length=200)


@app.get("/memory/records")
async def memory_records(
    tenant_id: UUID,
    actor: SignedInActor = Depends(signed_in_actor),
    user_id: str | None = None,
    tier: str = Query(default="client"),
):
    """VIEW: list only the signed-in caller's memories in their tenant."""
    from trigger.gates_memory import memory_enabled, memory_off_from_rows

    refuse_scope_widen(actor, body_tenant_id=tenant_id, body_user_id=user_id)
    if not memory_enabled():
        return {"memories": [], "memory_enabled": False}
    memory = getattr(app.state, "memory", None)
    if memory is None:
        raise HTTPException(503, "Memory unavailable")
    # Pass verified actor user_id; Hermes/chassis filters — no product-side filter.
    rows = await memory.records(
        str(actor.tenant_id), user_id=actor.user_id, tier=tier
    )
    if memory_off_from_rows(rows, tenant_id=str(actor.tenant_id), user_id=actor.user_id):
        return {"memories": [], "memory_enabled": False, "count": 0}
    return {"memories": rows, "memory_enabled": True, "count": len(rows)}


@app.get("/memory/records/{memory_id}")
async def memory_record(memory_id: UUID, tenant_id: UUID, actor: SignedInActor = Depends(signed_in_actor)):
    from trigger.gates_memory import owned_memory_row
    listed = await memory_records(tenant_id, actor=actor, user_id=actor.user_id, tier="client")
    row = owned_memory_row(listed["memories"], memory_id=str(memory_id),
                           tenant_id=str(actor.tenant_id), user_id=actor.user_id)
    if row is None:
        raise HTTPException(404, "Memory ownership required")
    return {"memory": row, "memory_enabled": listed["memory_enabled"]}


@app.post("/memory/edit")
async def memory_edit(body: MemoryEditIn, actor: SignedInActor = Depends(signed_in_actor)):
    """CONTENT EDIT: remember only into the signed-in caller's scope."""
    from trigger.gates_memory import (
        memory_enabled,
        memory_kinds,
        memory_off_from_rows,
        owned_memory_row,
    )

    refuse_scope_widen(actor, body_tenant_id=body.tenant_id, body_user_id=body.user_id)
    if not memory_enabled():
        raise HTTPException(409, "Memory is switched off")
    memory = getattr(app.state, "memory", None)
    if memory is None:
        raise HTTPException(503, "Memory unavailable")
    rows = await memory.records(str(actor.tenant_id), user_id=actor.user_id)
    if memory_off_from_rows(rows, tenant_id=str(actor.tenant_id), user_id=actor.user_id):
        raise HTTPException(409, "Memory is switched off")
    if body.kind not in memory_kinds():
        raise HTTPException(422, "unsupported Trigger memory kind")
    from trigger.gates_memory import _text_has_secret
    if _text_has_secret(body.text):
        raise HTTPException(422, "Trigger memory refuses secret-bearing text")
    if body.memory_id:
        owned = owned_memory_row(
            rows,
            memory_id=body.memory_id,
            tenant_id=str(actor.tenant_id),
            user_id=actor.user_id,
        )
        if owned is None:
            raise HTTPException(403, "Memory ownership required")
    saved = await memory.remember(
        str(actor.tenant_id),
        body.text,
        body.kind,
        body.source,
        user_id=actor.user_id,
        meta={
            "user_id": actor.user_id,
            "contact_id": body.contact_id,
            "task_key": body.task_key,
            "edited": True,
            **({"replaces": body.memory_id} if body.memory_id else {}),
        },
    )
    if body.memory_id and str(saved["id"]) != str(body.memory_id):
        await memory.forget(body.memory_id, str(actor.tenant_id), user_id=actor.user_id)
    return {"ok": True, "memory": saved, "memory_enabled": True}


@app.post("/memory/forget")
async def memory_forget(body: MemoryForgetIn, actor: SignedInActor = Depends(signed_in_actor)):
    """FORGET: delete only a row owned by the signed-in caller."""
    from trigger.gates_memory import memory_enabled, owned_memory_row

    refuse_scope_widen(actor, body_tenant_id=body.tenant_id, body_user_id=body.user_id)
    if not memory_enabled():
        raise HTTPException(409, "Memory is switched off")
    memory = getattr(app.state, "memory", None)
    if memory is None:
        raise HTTPException(503, "Memory unavailable")
    rows = await memory.records(str(actor.tenant_id), user_id=actor.user_id)
    owned = owned_memory_row(
        rows,
        memory_id=body.memory_id,
        tenant_id=str(actor.tenant_id),
        user_id=actor.user_id,
    )
    if owned is None:
        raise HTTPException(403, "Memory ownership required")
    await memory.forget(body.memory_id, str(actor.tenant_id), user_id=actor.user_id)
    return {"ok": True, "forgotten": body.memory_id, "user_id": actor.user_id}


@app.post("/memory/switch")
async def memory_switch(body: MemorySwitchIn, actor: SignedInActor = Depends(signed_in_actor)):
    """SWITCH-OFF/ON for the signed-in caller's own tenant+user scope only."""
    from trigger.gates_memory import (
        MEMORY_OFF_KIND,
        MEMORY_OFF_SOURCE,
        MEMORY_OFF_TEXT,
        memory_enabled,
    )

    refuse_scope_widen(actor, body_tenant_id=body.tenant_id, body_user_id=body.user_id)
    if not memory_enabled():
        raise HTTPException(409, "Memory is switched off globally")
    memory = getattr(app.state, "memory", None)
    if memory is None:
        raise HTTPException(503, "Memory unavailable")
    rows = await memory.records(str(actor.tenant_id), user_id=actor.user_id)
    off_rows = [
        row
        for row in rows
        if row.get("kind") == MEMORY_OFF_KIND and MEMORY_OFF_TEXT in str(row.get("text", row.get("claim", "")))
    ]
    currently_off = bool(off_rows)
    if body.enabled is False:
        if not currently_off:
            await memory.remember(
                str(actor.tenant_id),
                MEMORY_OFF_TEXT,
                MEMORY_OFF_KIND,
                MEMORY_OFF_SOURCE,
                user_id=actor.user_id,
                meta={"user_id": actor.user_id, "memory_off": True},
            )
        return {"ok": True, "memory_enabled": False, "user_id": actor.user_id}
    # Switch back on: forget this user's off markers only.
    for row in off_rows:
        await memory.forget(row["id"], str(actor.tenant_id), user_id=actor.user_id)
    return {"ok": True, "memory_enabled": True, "user_id": actor.user_id}


class VoiceOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    text: str = Field(default="", max_length=12000)
    expected_revision: int = Field(default=0, ge=0)
    preview_hash: str = ""
    operation_id: UUID | None = None
    rules: list[str] | None = Field(default=None, max_length=25)
    target: int | None = Field(default=None, ge=1)
    offset: int = Field(default=0, ge=0)


@app.post("/voice/{operation}", dependencies=[Depends(authorized)])
async def voice_operation(operation: str, body: VoiceOperation):
    store = VoiceHistory(app.state.pool)
    try:
        if operation == "show":
            return await store.show(body.tenant_id, body.offset, body.target)
        if operation == "preview":
            return await store.preview(body.tenant_id, body.text, body.target, body.rules)
        if operation == "commit" and body.operation_id:
            return {"voice": await store.commit(body.tenant_id, text=body.text,
                expected_revision=body.expected_revision, digest=body.preview_hash,
                operation_id=body.operation_id, target=body.target, rules=body.rules)}
        raise HTTPException(404, "Voice operation unavailable")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
