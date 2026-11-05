"""Ghost's narrow corridor. No policy, key, merge, database or arbitrary URL API.

The recovery backend is deliberately unconfigured until the operator supplies an
existing control-plane integration. It must verify current state and implement
only the two operations below. Missing integration fails closed with a receipt.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Literal

import admin
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

router = APIRouter(prefix="/v1/ghost")
ROOMS = {name: f"http://agent-{name.replace('_', '-')}:8000" for name in
         ("base", "tiger", "market_lab", "rig", "tisan", "radix", "zenas", "ghost")}
TARGETS = frozenset((*ROOMS, "workspace", "site", "tank"))
ROOM_METHODS = {("GET", "/v1/ghost/google/signing-keys"), ("POST", "/v1/ghost/check"), ("POST", "/v1/ghost/actions"),
                ("POST", "/v1/ghost/radix"), ("GET", "/v1/ghost/radix"),
                ("GET", "/v1/ghost/autumbra/memory/records"),
                ("POST", "/v1/ghost/autumbra/memory/memories"),
                ("POST", "/v1/ghost/autumbra/blocks/withdraw")}


def room_method(method: str, path: str) -> bool:
    if (method, path) in ROOM_METHODS:
        return True
    prefix = "/v1/ghost/autumbra/memory/memories/"
    if method != "DELETE" or not path.startswith(prefix):
        return False
    try:
        return str(uuid.UUID(path[len(prefix):])) == path[len(prefix):]
    except ValueError:
        return False


def runtime():
    import main
    return main


async def require_ghost(request: Request, x_tank_key: str = Header(default="")):
    if await runtime().require_key(request, x_tank_key) != "ghost":
        raise HTTPException(403, "Only Autumbra Watch may use this corridor.")


@router.get("/google/signing-keys", dependencies=[Depends(require_ghost)])
async def google_signing_keys(kid: str = Query(default="", max_length=200)):
    from autumbra_google_keys import CACHE
    try:
        keys = await CACHE.get(kid)
    except (ValueError, OSError, httpx.HTTPError, TimeoutError):
        raise HTTPException(503, "The room is unavailable.") from None
    return keys


async def call_room(method: str, room: str, path: str, *, body=None, params=None, tenant=None, timeout=60):
    rig_lifecycle = room == "ghost" and path.startswith("/rig/rooms/")
    lifecycle = rig_lifecycle or (room == "ghost" and path.startswith("/ghost/autumbra/enforce/"))
    key = os.environ.get("RIG_LIFECYCLE_KEY" if rig_lifecycle else "AUTUMBRA_LIFECYCLE_KEY" if lifecycle else f"TANK_KEY_{room.upper()}", "")
    http = runtime()._http
    if not key or http is None:
        raise HTTPException(503, "The room is unavailable.")
    headers={"X-Tank-Key": key}
    if tenant is not None:
        headers["X-Tervory-Tenant"]=str(uuid.UUID(str(tenant)))
    try:
        base = "http://enforce-lifecycle:8000" if lifecycle else ROOMS[room]
        if lifecycle:
            content = json.dumps(body, separators=(",", ":")).encode() if body is not None else b""
            timestamp = str(int(time.time()))
            message = f"{timestamp}\n{method}\n{path}\n".encode() + content
            headers = {"X-Lifecycle-Timestamp": timestamp,
                       "X-Lifecycle-Signature": hmac.new(key.encode(), message, hashlib.sha256).hexdigest(),
                       "Content-Type": "application/json"}
            response = await http.request(method, base + path, content=content, params=params,
                                          headers=headers, follow_redirects=False, timeout=timeout)
        else:
            response = await http.request(method, base + path, json=body, params=params,
                                          headers=headers, follow_redirects=False, timeout=timeout)
    except httpx.HTTPError:
        raise HTTPException(503, "The room is unavailable.") from None
    if len(response.content) > 2_000_000 or response.status_code >= 500:
        raise HTTPException(503, "The room is unavailable.")
    return response


@router.api_route("/{action}", methods=["GET", "POST"], dependencies=[Depends(admin.require_admin)])
async def panel(action: str, request: Request):
    paths = {("GET", "incidents"): "/incidents", ("POST", "look"): "/look",
             ("POST", "message"): "/message", ("POST", "alert"): "/alert"}
    path = paths.get((request.method, action))
    if path is None:
        raise HTTPException(404, "Not found.")
    raw = await request.body()
    if len(raw) > 4096:
        raise HTTPException(413, "Request too large.")
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        raise HTTPException(422, "Invalid Autumbra Watch request.") from None
    response = await call_room(request.method, "ghost", path, body=body)
    return Response(response.content, status_code=response.status_code, media_type="application/json")


@router.post("/incidents/{incident}/resolve", dependencies=[Depends(admin.require_admin)])
async def resolve(incident: uuid.UUID, request: Request):
    raw = await request.body()
    if len(raw) > 1024:
        raise HTTPException(413, "Request too large.")
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(422, "Invalid resolution.") from None
    response = await call_room("POST", "ghost", f"/incidents/{incident}/resolve", body=body)
    return Response(response.content, status_code=response.status_code, media_type="application/json")


# ---------- GHOST-2 Shields Up: founder-only reads and the Shield / Undo taps ----------

@router.get("/shields/list", dependencies=[Depends(admin.require_admin)])
async def shields_list():
    response = await call_room("GET", "ghost", "/ghost/shields")
    return Response(response.content, status_code=response.status_code, media_type="application/json")


@router.get("/shields/tile", dependencies=[Depends(admin.require_admin)])
async def shields_tile():
    """The founder tile: Autumbra Watch's own numbers plus cost this month from Tank's receipts."""
    response = await call_room("GET", "ghost", "/ghost/summary")
    if response.status_code != 200:
        raise HTTPException(503, "The room is unavailable.")
    core = runtime()
    async with core.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT coalesce(sum(cost_usd), 0) AS cost, count(*) AS receipts FROM tank.ledger"
            " WHERE tenant_id=$1 AND agent='ghost' AND ts >= date_trunc('month', now())",
            core.PLATFORM_TENANT)
    return {**response.json(), "cost_this_month_usd": float(row["cost"] if row else 0),
            "receipts_this_month": int(row["receipts"] if row else 0)}


@router.post("/shields/{shield}/{tap}", dependencies=[Depends(admin.require_admin)])
async def shields_tap(shield: uuid.UUID, tap: Literal["raise", "undo"]):
    response = await call_room("POST", "ghost", f"/ghost/shields/{shield}/{tap}")
    return Response(response.content, status_code=response.status_code, media_type="application/json")


# ---------- Autumbra Enforce lifecycle: founder Confirm cards only ----------

class EnforceTenant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: uuid.UUID


@router.get("/autumbra/enforce/list", dependencies=[Depends(admin.require_admin)])
async def enforce_list(x_tervory_tenant: uuid.UUID = Header()):
    response = await call_room("GET", "ghost", "/ghost/autumbra/enforce/list")
    if response.status_code != 200:
        return Response(response.content, status_code=response.status_code, media_type="application/json")
    from trigger_routes import code_receipt
    ledger_id = await code_receipt(x_tervory_tenant, "autumbra.enforce.list", agent="ghost")
    data = response.json()
    items = [{"name": row["name"], "summary": row["uuid"], "status": row["status"]} for row in data.get("applications", [])]
    return {**data, "items": items, "ledger_id": str(ledger_id), "cost_usd": 0}


async def enforce_write_result(response, actor, tool):
    if response.status_code != 200:
        return Response(response.content, status_code=response.status_code, media_type="application/json")
    result = response.json()
    outcome = (result.get("receipt") or {}).get("outcome", "unknown")
    if outcome not in {"created", "destroyed", "absent"}:
        raise HTTPException(409, f"Enforce outcome {outcome}; check Coolify before repeating")
    from trigger_routes import code_receipt
    receipt = result.get("receipt") or {}
    facts = [str(receipt["uuid"])] if receipt.get("uuid") else []
    facts.extend(receipt.get("founder_fills") or [])
    return {**result, "name": receipt.get("project"), "status": outcome, "summary": " · ".join(facts),
            "ledger_id": str(await code_receipt(actor.tenant_id, tool, agent="ghost")), "cost_usd": 0}


@router.post("/autumbra/enforce/create", dependencies=[Depends(admin.require_admin)])
async def enforce_create(body: EnforceTenant, request: Request):
    from command import identity, require_tool
    from trigger_commands import confirmation
    actor = await identity(request)
    tool = "autumbra.enforce.create"
    require_tool(actor, tool)
    confirmation(actor, tool, body.model_dump(mode="json"))
    response = await call_room("POST", "ghost", "/ghost/autumbra/enforce/plan-create",
                               body={"tenant_id": str(body.tenant_id), "execute": True}, timeout=150)
    return await enforce_write_result(response, actor, tool)


@router.post("/autumbra/enforce/destroy", dependencies=[Depends(admin.require_admin)])
async def enforce_destroy(body: EnforceTenant, request: Request):
    from command import identity, require_tool
    from trigger_commands import confirmation
    actor = await identity(request)
    tool = "autumbra.enforce.destroy"
    require_tool(actor, tool)
    confirmation(actor, tool, body.model_dump(mode="json"))
    response = await call_room("POST", "ghost", "/ghost/autumbra/enforce/plan-destroy",
                               body={"tenant_id": str(body.tenant_id), "execute": True}, timeout=90)
    return await enforce_write_result(response, actor, tool)


# ---------- Autumbra code fixes: existing watcher matches, founder Confirm ----------

class FixMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match_id: uuid.UUID


@router.get("/autumbra/fixes", dependencies=[Depends(admin.require_admin)])
async def fixes_list(request: Request):
    from command import identity, require_tool
    from trigger_routes import code_receipt
    actor = await identity(request)
    require_tool(actor, "autumbra.fixes")
    response = await call_room("GET", "ghost", "/ghost/patch/fixes")
    if response.status_code != 200:
        return Response(response.content, status_code=response.status_code, media_type="application/json")
    return {**response.json(), "cost_usd": 0,
            "ledger_id": str(await code_receipt(actor.tenant_id, "autumbra.fixes", agent="ghost"))}


@router.post("/autumbra/fix/approve", dependencies=[Depends(admin.require_admin)])
async def fix_approve(body: FixMatch, request: Request):
    from command import identity, require_tool
    from trigger_commands import confirmation
    from trigger_routes import code_receipt
    actor = await identity(request)
    tool = "autumbra.fix.approve"
    require_tool(actor, tool)
    confirmation(actor, tool, body.model_dump(mode="json"))
    response = await call_room("POST", "ghost", "/ghost/patch/from-match", body=body.model_dump(mode="json"))
    if response.status_code != 200:
        return Response(response.content, status_code=response.status_code, media_type="application/json")
    return {**response.json(), "cost_usd": 0,
            "ledger_id": str(await code_receipt(actor.tenant_id, tool, agent="ghost"))}


# ---------- Standing blocks: founder reads and policy-checked publication ----------
class BlockTenant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: uuid.UUID


class BlockShare(BlockTenant):
    record_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


@router.get("/autumbra/blocks", dependencies=[Depends(admin.require_admin)])
async def blocks_list(request: Request, tenant_id: uuid.UUID):
    from command import identity, require_tool
    from trigger_routes import code_receipt
    actor = await identity(request)
    require_tool(actor, "autumbra.blocks")
    response = await call_room("GET", "ghost", "/ghost/autumbra/blocks", params={"tenant_id": str(tenant_id)})
    if response.status_code != 200:
        return Response(response.content, status_code=response.status_code, media_type="application/json")
    return {**response.json(), "cost_usd": 0,
            "ledger_id": str(await code_receipt(actor.tenant_id, "autumbra.blocks", agent="ghost"))}


@router.post("/autumbra/blocks/share", dependencies=[Depends(admin.require_admin)])
async def blocks_share(body: BlockShare, request: Request):
    from command import identity, require_tool
    from trigger_commands import confirmation
    from trigger_routes import code_receipt
    actor = await identity(request)
    tool = "autumbra.blocks.share"
    require_tool(actor, tool)
    confirmation(actor, tool, body.model_dump(mode="json"))
    response = await call_room("GET", "ghost", "/ghost/autumbra/blocks", params={"tenant_id": str(body.tenant_id)})
    if response.status_code != 200:
        return Response(response.content, status_code=response.status_code, media_type="application/json")
    available = {str(row["id"]) for row in response.json()["items"]}
    if any(str(identity) not in available for identity in body.record_ids):
        raise HTTPException(422, "Invalid Autumbra Watch request.")
    core = runtime()
    items = []
    status = 200
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=core.app), base_url="http://tank.internal", trust_env=False) as client:
        for source in dict.fromkeys(body.record_ids):
            result = await client.post("/v1/memory/publish", json={"tenant_id": str(body.tenant_id), "agent": "ghost", "source_row": str(source)},
                headers={"X-Tank-Key": core.TANK_API_KEY, "X-Tervory-Admin": actor.user_id})
            data = result.json()
            if result.status_code != 200:
                status = result.status_code
                data = data.get("detail") or {"result": "refused"}
            outcome = data.get("result", "refused") if isinstance(data, dict) else "refused"
            items.append({"id": str(source), "summary": str(source), "status": outcome,
                          **(data if isinstance(data, dict) else {"result": "refused"})})
    ledger = str(await code_receipt(actor.tenant_id, tool, agent="ghost"))
    return Response(json.dumps({"items": items, "ledger_id": ledger, "cost_usd": 0}), status_code=status, media_type="application/json")


# Ghost's existing memory client reaches Hermes only through this same-box corridor.
async def ghost_memory(method, path, *, body=None, params=None):
    core = runtime()
    if core._http is None:
        raise HTTPException(503, "memory store unreachable")
    response = await core._http.request(method, "http://hermes:8000" + path,
        json=body, params=params, headers={"X-Tank-Key": os.environ.get("TANK_KEY_GHOST", "")},
        follow_redirects=False, timeout=40)
    if len(response.content) > 2_000_000 or response.status_code >= 300:
        raise HTTPException(503, "memory store unreachable")
    return response.json()


@router.get("/autumbra/memory/records", dependencies=[Depends(require_ghost)])
async def ghost_memory_records(tenant_id: uuid.UUID, tier: Literal["client", "platform"] = "client",
                               limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    return await ghost_memory("GET", "/records", params={"tenant_id": str(tenant_id), "tier": tier, "agent": "ghost", "limit": limit, "offset": offset})


@router.post("/autumbra/memory/memories", dependencies=[Depends(require_ghost)])
async def ghost_memory_write(request: Request):
    raw = await request.body()
    if len(raw) > 16000:
        raise HTTPException(413, "Request too large.")
    try:
        body = json.loads(raw)
        uuid.UUID(body["tenant_id"])
        if body.get("kind") != "blocked_address" or body.get("index") is not False:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, "Invalid Autumbra Watch request.") from None
    return await ghost_memory("POST", "/memories", body={**body, "agent": "ghost"})


@router.delete("/autumbra/memory/memories/{id}", dependencies=[Depends(require_ghost)])
async def ghost_memory_forget(id: uuid.UUID, tenant_id: uuid.UUID):
    return await ghost_memory("DELETE", "/memories/" + str(id), params={"tenant_id": str(tenant_id)})


class BlockWithdrawal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: uuid.UUID
    source_row: uuid.UUID
    published_row: uuid.UUID


@router.post("/autumbra/blocks/withdraw", dependencies=[Depends(require_ghost)])
async def ghost_block_withdrawal(body: BlockWithdrawal):
    # A previous founder publication delegates its mandatory source-undo cleanup.
    # The room cannot choose the founder or another agent's record.
    row = await runtime().pool().fetchrow(
        "SELECT actor FROM agent_ghost.memory_publish_log WHERE tenant_id=$1 AND source_row=$2 AND published_row=$3 AND result IN ('published','already_published') ORDER BY happened_at DESC LIMIT 1",
        body.tenant_id, body.source_row, body.published_row)
    if row is None or row["actor"] not in __import__("trigger_signin_config").founders():
        raise HTTPException(404, "not found")
    from memory_routes import hermes
    return await hermes("POST", "/founder/records/" + str(body.published_row) + "/withdraw", row["actor"],
                        body={"tenant_id": str(body.tenant_id), "agent": "ghost"})


# ---------- Autumbra owner undo (public email link → workspace → Tank → Watch) ----------

@router.post("/autumbra/queue/undo")
async def autumbra_queue_undo(request: Request, x_tank_key: str = Header(default="")):
    """Owner one-tap undo. Workspace key or ghost key; proxies ThreatPayload undo to Watch."""
    agent = await runtime().require_key(request, x_tank_key)
    if agent not in (None, "ghost"):
        raise HTTPException(404, "Not found.")
    raw = await request.body()
    if len(raw) > 8_000:
        raise HTTPException(413, "Request too large.")
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        raise HTTPException(422, "Undo was not understood.") from None
    if not isinstance(body, dict):
        raise HTTPException(422, "Undo was not understood.")
    response = await call_room("POST", "ghost", "/ghost/autumbra/queue/undo", body=body)
    return Response(response.content, status_code=response.status_code, media_type="application/json")



@router.get("/autumbra/receipts/weekly/{tenant_id}")
async def autumbra_weekly_receipts(tenant_id: str, request: Request, x_tank_key: str = Header(default="")):
    """Owner weekly page: list that week's receipts for one client."""
    agent = await runtime().require_key(request, x_tank_key)
    if agent not in (None, "ghost"):
        raise HTTPException(404, "Not found.")
    response = await call_room(
        "GET", "ghost", f"/ghost/autumbra/receipts/weekly/{tenant_id}", body=None
    )
    return Response(response.content, status_code=response.status_code, media_type="application/json")


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str
    signal: Literal["health", "slow", "errors", "deploy", "ci", "radix"]


@router.post("/check", dependencies=[Depends(require_ghost)])
async def check(request: Request):
    body = await parse(request, Check)
    if body.target not in TARGETS:
        raise HTTPException(422, "Unknown check target.")
    if body.signal not in {"health", "slow"}:
        # No raw-log API, GitHub administration credential, or cross-region proxy.
        raise HTTPException(503, "The read-only evidence feed is not connected.")
    if body.target not in ROOMS and body.target != "tank":
        raise HTTPException(422, "Public sites use Autumbra Watch's own browser.")
    start = time.monotonic()
    if body.target == "tank":
        async with runtime().pool().acquire() as conn:
            await conn.fetchval("SELECT 1")
        status = 200
    else:
        try:
            response = await call_room("GET", body.target, "/health")
            status = response.status_code
        except HTTPException:
            status = 503
    latency = min(120000, int((time.monotonic() - start) * 1000))
    return {"target": body.target, "signal": body.signal, "failing": status >= 400 or (body.signal == "slow" and latency > 3000),
            "count": 1, "latency_ms": latency, "status_code": status}


async def parse(request: Request, model, max_bytes: int = 4096):
    raw = await request.body()
    if len(raw) > max_bytes:
        raise HTTPException(413, "Request too large.")
    try:
        return model.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(422, "Invalid Autumbra Watch request.") from None


class Recovery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    action: Literal["rerun_failed_deploy", "restart_crashed_room"]
    target: str


async def action_receipt(identity: uuid.UUID, step: str, outcome: str):
    core = runtime()
    receipt = core.VendorCall(id=uuid.uuid5(identity, step), agent="ghost", task_type="ghost.recovery",
                              vendor=f"ghost.{step}", units=1, cost_usd=0,
                              outcome=outcome, region=core.box_region())
    return await core.vendor_ledger(receipt, agent="ghost")


@router.post("/actions", dependencies=[Depends(require_ghost)])
async def recover(request: Request):
    """One guarded action route. Pending/uncertain requests are never retried."""
    # Record every authenticated call, including malformed/refused requests.
    attempt = uuid.uuid4()
    receipt = await action_receipt(attempt, "requested", "unverified")
    try:
        body = await parse(request, Recovery)
    except HTTPException:
        await action_receipt(attempt, "refused", "failed")
        raise
    allowed = set(ROOMS) if body.action == "restart_crashed_room" else TARGETS
    if body.target not in allowed:
        await action_receipt(attempt, "refused", "failed")
        raise HTTPException(403, {"message": "Action target is not allowed.", **receipt})
    core = runtime()
    async with core.pool().acquire() as conn:
        row = await conn.fetchrow("""INSERT INTO tank.ghost_recovery
            (tenant_id,id,action,target,status) VALUES ($1,$2,$3,$4,'pending')
            ON CONFLICT (tenant_id,id) DO NOTHING RETURNING status""",
                                 core.PLATFORM_TENANT, body.id, body.action, body.target)
        if row is None:
            prior = await conn.fetchrow("SELECT action,target,status FROM tank.ghost_recovery WHERE tenant_id=$1 AND id=$2",
                                        core.PLATFORM_TENANT, body.id)
            if prior is None or prior["action"] != body.action or prior["target"] != body.target:
                raise HTTPException(409, {"message": "Request identifier already used.", **receipt})
            return {"status": prior["status"], "repeated": True, **receipt}
    backend = getattr(core.app.state, "ghost_recovery_backend", None)
    status = "unavailable"
    try:
        if backend is None:
            raise HTTPException(503, {"message": "Recovery integration is not connected. No action was taken.", **receipt})
        observed = await backend.observe(body.target)
        required = "crashed" if body.action == "restart_crashed_room" else "failed"
        if observed.get("state") != required or not observed.get("revision"):
            status = "refused"
            raise HTTPException(409, {"message": "Current state does not permit this action.", **receipt})
        await action_receipt(body.id, "authorized", "unverified")
        status = "uncertain"
        # The backend must compare this revision again atomically and rerun the
        # exact failed revision, never deploy current main or change settings.
        result = await backend.execute(body.action, body.target, observed["revision"], str(body.id))
        if result != "queued":
            raise HTTPException(502, {"message": "Action outcome is uncertain; do not retry.", **receipt})
        status = "queued"
        await action_receipt(body.id, "queued", "delivered")
        return {"status": status, **receipt}
    except (HTTPException, asyncio.CancelledError):
        raise
    except Exception:
        raise HTTPException(502, {"message": "Action outcome is uncertain; do not retry.", **receipt}) from None
    finally:
        async with core.pool().acquire() as conn:
            await conn.execute("UPDATE tank.ghost_recovery SET status=$3 WHERE tenant_id=$1 AND id=$2",
                               core.PLATFORM_TENANT, body.id, status)
        if status in {"refused", "unavailable"}:
            await action_receipt(body.id, status, "failed")


class Scan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archive: str = Field(min_length=1, max_length=2_000_000)


@router.post("/radix", dependencies=[Depends(require_ghost)])
async def scan(request: Request):
    body = await parse(request, Scan, max_bytes=2_010_000)
    try:
        raw = base64.b64decode(body.archive, validate=True)
    except ValueError:
        raise HTTPException(422, "Hand over a base64 archive.") from None
    if not (raw.startswith((b"PK\x03\x04", b"\x1f\x8b")) or raw[257:262] == b"ustar"):
        raise HTTPException(422, "Hand over a tar or zip archive.")
    response = await call_room("POST", "radix", "/jobs", body={
        "tenant_id": str(runtime().PLATFORM_TENANT), "kind": "scan",
        "input": {"source": "b64:" + body.archive, "ref": "Autumbra Watch incident code"},
    })
    if response.status_code != 201:
        raise HTTPException(503, "Radix did not accept the scan.")
    data = response.json()
    return {"id": str(uuid.UUID(data["id"])), "status": "queued"}


@router.get("/radix", dependencies=[Depends(require_ghost)])
async def scan_result(id: uuid.UUID):
    response = await call_room("GET", "radix", f"/jobs/{id}", params={"tenant_id": str(runtime().PLATFORM_TENANT)})
    if response.status_code != 200:
        raise HTTPException(503, "Radix results are unavailable.")
    data = response.json()
    result = data.get("result") or {}
    # Never forward findings' snippets, paths, or exception text to Ghost.
    return {"id": str(id), "status": data.get("status") if data.get("status") in {"queued", "running", "done", "failed"} else "unknown",
            "findings": len(result.get("findings", [])), "critical": int(result.get("critical", 0))}


# Explicit routes must precede the founder panel's single-segment catch-all.
router.routes.sort(key=lambda route: "{action}" in route.path)
