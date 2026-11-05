"""ML3: authenticated code reads, confirmed alert opt-ins and signed delivery.

The room never receives a webhook secret. The one-time Confirm value is returned
in memory to command.execute_card, never persisted in its card/event/model history.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tenant_db import tenant_path

PLATFORM = "00000000-0000-0000-0000-000000000001"
READS = {"warnings", "predictions", "calibration", "feeds", "patterns", "gate", "briefs", "calibration.curve"}


def obj(raw):
    return json.loads(raw) if isinstance(raw, str) else raw


async def room(operation: str, tenant, *, report_id: UUID | None = None) -> dict:
    from ghost_routes import call_room
    response = await call_room("POST", "market_lab", "/market/ml3/" + operation,
                               body={"tenant_id": str(tenant), **({"report_id": str(report_id)} if report_id else {})})
    if response.status_code == 404:
        raise HTTPException(404, "Not found")
    if response.status_code != 200:
        raise HTTPException(503, "Market Lab is not connected")
    return response.json()


def link(value: str) -> str:
    if value.startswith("/market-lab/sources/"):
        return "https://app.tervory.com" + value
    return ""


def records(operation: str, data: dict) -> list[dict]:
    if operation == "calibration.curve":
        # The binned data is available to callers; its dedicated Workspace view
        # waits for Figma rather than creating labels or a new chart here.
        return [{"name": " / ".join(str(row[k]) for k in ("kind", "feed", "horizon_seconds")),
                 "count": row["count"], "bins": row["bins"], "brier_score": row["brier_score"],
                 "kind": row["kind"], "feed": row["feed"], "horizon_seconds": row["horizon_seconds"],
                 **{key: row.get(key) for key in ("mean_confidence", "realised_rate", "hits", "misses")}}
                for row in data.get("curves", [])]
    if operation == "briefs":
        return [{"name": row["question"], "summary": row["answer"],
                 "status": " / ".join(d["channel"] + ":" + d["status"] for d in row["deliveries"]),
                 "deliveries": row["deliveries"], "report_id": row["id"], "citations": row["citations"],
                 **{key: row[key] for key in ("made_at", "sources", "signals", "history") if key in row}}
                for row in data.get("briefs", [])]
    if operation == "patterns":
        return [{"name": row["claim"]["pair_id"], "status": row["tier"],
                 "count": row["claim"]["episodes"], "total": row["claim"]["hits"],
                 "claim": row["claim"]}
                for key in ("patterns", "published") for row in data.get(key, [])]
    if operation == "gate":
        return [{"name": " / ".join(str(row[key]) for key in ("kind", "feed", "horizon_seconds")),
                 "status": "open" if row["open"] else "closed", "summary": row.get("opens_at"),
                 "count": row["resolved_count"]}
                for row in data.get("cohorts", [])]
    if operation == "warnings":
        rows = []
        for warning in data.get("warnings", []):
            rows.extend({"name": warning["title"], "status": warning["status"], "text": f["text"],
                         "link": link(f["href"])} for f in warning["facts"])
        return rows
    if operation == "calibration":
        hits, misses = data.get("hits", 0), data.get("misses", 0)
        summary = {"hits": hits, "misses": misses, "hit_rate": hits / (hits + misses) if hits + misses else None}
        return [{"summary": json.dumps(summary)}] + [{"summary": json.dumps(c, default=str)} for c in data.get("curves", [])]
    fields = {
        "predictions": ("id", "made_at", "statement", "horizon_seconds", "confidence", "outcome", "realised_value", "realised_date", "sources"),
        "feeds": ("id", "name", "state", "rows", "checked_at", "key_name"),
    }[operation]
    return [{"name": r.get("name") or r.get("statement") or r["id"],
             "summary": json.dumps({k: r.get(k) for k in fields}, default=str, ensure_ascii=False)}
            for r in data.get(operation, [])]


@tenant_path("actor")
async def code_plan(actor, messages: list[dict], task_id=None):
    """Exact ML3 commands propose existing cards without consulting a model."""
    import command
    from types import SimpleNamespace
    last = messages[-1] if messages else {}
    if last.get("role") != "user":
        return None
    source = last.get("content", "").strip()
    allowed = {"market_lab." + name for name in READS} | {"market_lab.alerts." + name for name in ("email", "webhook", "off")}
    if not source or source.split(maxsplit=1)[0] not in allowed:
        return None
    from main import pool, box_region
    if task_id:
        raw = await pool().fetchval("SELECT state FROM tank.command_threads WHERE tenant_id=$1 AND user_id=$2 AND state->>'active_turn'=$3", actor.tenant_id, actor.user_id, str(task_id))
        original = next((m for m in reversed((obj(raw) or {}).get("messages", [])) if m.get("role") == "user"), {})
        source = original.get("content", "").strip()
    parts = source.split(maxsplit=1)
    if not parts or parts[0] not in allowed:
        # Once an exact command selected this code path, absent or mismatched
        # authoritative turn data must not fall through to the model planner.
        raise HTTPException(422, "The action arguments are invalid")
    try:
        arguments = json.loads(parts[1]) if len(parts) == 2 else {}
        action = command.validate_action(command.PlannedAction(tool=parts[0], arguments=arguments), await command.authorized_actor(actor))
    except (ValueError, TypeError):
        raise HTTPException(422, "The action arguments are invalid") from None
    ledger_id = await pool().fetchval("INSERT INTO tank.ledger(tenant_id,region,agent,task_type,rung,model,cost_usd,tokens_in,tokens_out,task_id) VALUES($1,$2,'market_lab','command.turn',3,'code',0,0,0,$3) RETURNING id", actor.tenant_id, box_region(), task_id)
    return SimpleNamespace(content=json.dumps({"text": action.tool, "actions": [{"tool": action.tool, "arguments": action.arguments}]}), ledger_id=ledger_id)


@tenant_path("actor")
async def confirmed(action, actor, thread_id, card_id):
    import command
    import vault
    from main import pool, box_region
    from vault_routes import public_url
    actor = await command.authorized_actor(actor)
    command.require_tool(actor, action.tool)
    if not (actor.founder or actor.tenant_admin):
        raise HTTPException(403, "This tool is not available to this user")
    mode = action.tool.removeprefix("market_lab.alerts.")
    if mode not in {"email", "webhook", "off"}:
        raise HTTPException(404, "Not found")
    # Only the durable executing claim created by the actual Confirm endpoint
    # can change preferences. A model plan or direct function call is insufficient.
    async with pool().acquire() as conn:
        raw = await conn.fetchval("SELECT state FROM tank.command_threads WHERE thread_id=$1 AND tenant_id=$2 AND user_id=$3", thread_id, actor.tenant_id, actor.user_id)
    card = (obj(raw) or {}).get("cards", {}).get(str(card_id), {})
    if card.get("status") != "executing" or card.get("payload") != {"tool": action.tool, "arguments": action.arguments}:
        raise HTTPException(409, "Card has already been resolved")
    url = public_url(action.arguments["url"]) if mode == "webhook" else None
    # Register an existing chassis job before enabling any delivery. This is a
    # confirmed change, not a side effect of a read.
    if mode != "off":
        await room("alerts", actor.tenant_id)
    once = None
    async with pool().acquire() as conn, conn.transaction():
        await conn.execute("INSERT INTO tank.market_lab_alerts(tenant_id) VALUES($1) ON CONFLICT (tenant_id) DO NOTHING", actor.tenant_id)
        settings = await conn.fetchrow("SELECT * FROM tank.market_lab_alerts WHERE tenant_id=$1 FOR UPDATE", actor.tenant_id)
        if mode == "email":
            await conn.execute("UPDATE tank.market_lab_alerts SET email_enabled=true,email_since=CASE WHEN email_enabled THEN email_since ELSE now() END,updated_at=now() WHERE tenant_id=$1", actor.tenant_id)
        elif mode == "webhook" and (settings["webhook_url"] != url or not settings["webhook_sealed"]):
            once = secrets.token_urlsafe(32)
            sealed = vault.seal({"value": once}, f"market_lab:webhook:v1:{actor.tenant_id}")
            await conn.execute("UPDATE tank.market_lab_alerts SET webhook_url=$2,webhook_sealed=$3,webhook_since=now(),updated_at=now() WHERE tenant_id=$1", actor.tenant_id, url, sealed)
            await conn.execute("UPDATE tank.market_lab_deliveries SET status='suppressed',updated_at=now() WHERE tenant_id=$1 AND channel='webhook' AND status='pending'", actor.tenant_id)
        elif mode == "off":
            await conn.execute("UPDATE tank.market_lab_alerts SET email_enabled=false,email_since=NULL,webhook_url=NULL,webhook_sealed=NULL,webhook_since=NULL,updated_at=now() WHERE tenant_id=$1", actor.tenant_id)
            await conn.execute("UPDATE tank.market_lab_deliveries SET status='suppressed',updated_at=now() WHERE tenant_id=$1 AND status='pending'", actor.tenant_id)
        ledger_id = await conn.fetchval("INSERT INTO tank.ledger(tenant_id,region,agent,task_type,rung,model,cost_usd,tokens_in,tokens_out) VALUES($1,$2,'market_lab',$3,3,'code',0,0,0) RETURNING id", actor.tenant_id, box_region(), action.tool)
    result = {"status": mode, "ledger_id": str(ledger_id), "cost_usd": 0}
    if once is not None:
        result["_once"] = once
    return result


async def prime(tenant: UUID):
    from main import pool
    async with pool().acquire() as conn:
        settings = await conn.fetchrow("SELECT * FROM tank.market_lab_alerts WHERE tenant_id=$1", tenant)
    if not settings or not (settings["email_enabled"] or settings["webhook_url"]):
        return
    data = await room("warnings", tenant)
    for warning in data.get("warnings", []):
        created = datetime.fromisoformat(warning["created_at"])
        text = "\n".join(part for f in warning["facts"] for part in (f["text"], link(f["href"])))
        letter = {"subject": warning["title"], "text": text}
        for channel in ("email", "webhook"):
            since = settings["email_since"] if channel == "email" else settings["webhook_since"]
            if since is None or created < since:
                continue
            async with pool().acquire() as conn, conn.transaction():
                current = await conn.fetchrow("SELECT email_since,webhook_since FROM tank.market_lab_alerts WHERE tenant_id=$1 FOR UPDATE", tenant)
                if current is None or current["email_since" if channel == "email" else "webhook_since"] != since:
                    continue
                await conn.execute("INSERT INTO tank.market_lab_deliveries(tenant_id,warning_id,channel,status,letter) VALUES($1,$2,$3,'pending',$4::jsonb) ON CONFLICT (tenant_id,warning_id,channel) DO NOTHING", tenant, UUID(warning["id"]), channel, json.dumps(letter))


async def claim(tenant: UUID, ident: UUID, channel: str) -> dict | None:
    from main import pool
    async with pool().acquire() as conn, conn.transaction():
        settings = await conn.fetchrow("SELECT * FROM tank.market_lab_alerts WHERE tenant_id=$1 FOR UPDATE", tenant)
        allowed = settings and (settings["email_enabled"] if channel == "email" else settings["webhook_url"])
        if not allowed:
            return None
        row = await conn.fetchrow("UPDATE tank.market_lab_deliveries SET status='sending',updated_at=now() WHERE tenant_id=$1 AND warning_id=$2 AND channel=$3 AND status='pending' RETURNING letter", tenant, ident, channel)
        if row is None:
            return None
        return {"allowed": True, "letter": obj(row["letter"]), "settings": dict(settings)}


def signature(raw: bytes, value: str) -> str:
    return "sha256=" + hmac.new(value.encode(), raw, hashlib.sha256).hexdigest()


async def send_webhook(tenant: UUID, settings: dict, payload: dict, ident: UUID) -> str:
    """Shared ML3 signer/transport for warning and saved-report deliveries."""
    from access import public_stream
    from vault import open_seal
    try:
        value = open_seal(settings["webhook_sealed"], f"market_lab:webhook:v1:{tenant}")["value"]
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        async with public_stream(settings["webhook_url"], method="POST", content=raw,
                                 headers={"Content-Type": "application/json", "X-Market-Lab-Signature": signature(raw, value),
                                          "Idempotency-Key": str(ident)}) as response:
            return "sent" if 200 <= response.status_code < 300 else "uncertain"
    except Exception:
        return "uncertain"  # No URL, payload, signature or decrypted value in logs.


async def webhooks(tenant: UUID) -> dict:
    from main import pool
    async with pool().acquire() as conn:
        ids = await conn.fetch("SELECT warning_id FROM tank.market_lab_deliveries WHERE tenant_id=$1 AND channel='webhook' AND status='pending' ORDER BY created_at,warning_id LIMIT 3", tenant)
    sent = 0
    for ident in ids:
        claimed = await claim(tenant, ident["warning_id"], "webhook")
        if claimed is None:
            continue
        state = await send_webhook(tenant, claimed["settings"],
                                   {"tenant_id": str(tenant), "warning_id": str(ident["warning_id"]), **claimed["letter"]},
                                   ident["warning_id"])
        sent += state == "sent"
        async with pool().acquire() as conn:
            await conn.execute("UPDATE tank.market_lab_deliveries SET status=$3,updated_at=now() WHERE tenant_id=$1 AND warning_id=$2 AND channel='webhook' AND status='sending'", tenant, ident["warning_id"], state)
    return {"sent": sent}


class ConfidenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    hits: int = Field(ge=0, strict=True)
    total: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def valid_counts(self):
        if self.hits > self.total:
            raise ValueError("The action arguments are invalid")
        return self


class MailBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    warning_id: UUID | None = None
    report_id: UUID | None = None
    title: str | None = None
    text: str | None = None
    citations: list[dict] | None = None
    status: Literal["pending", "sent", "uncertain", "suppressed"] | None = None


def build_router(tank):
    import command
    router = APIRouter()

    @router.get("/v1/market-lab/intel")
    async def intel(request: Request, x_tank_key: str = Header(default="")):
        if not command.key_matches(x_tank_key, os.environ.get("TANK_KEY_GHOST", "")):
            actor = await command.identity(request)
            if not actor.founder:
                raise HTTPException(403, "This tool is not available to this user")
        return JSONResponse(await room("intel", PLATFORM), headers={"Cache-Control": "private, no-store"})

    @router.get("/v1/market-lab/reads/{operation}")
    @tenant_path("actor")
    async def read(operation: str, actor=Depends(command.identity), limit: int = Query(default=20, ge=1, le=100), page: int = Query(default=0, ge=0, le=10000), report_id: UUID | None = None):
        from main import pool, box_region
        if operation not in READS:
            raise HTTPException(404, "Not found")
        command.require_tool(actor, "market_lab." + operation)
        if report_id is not None and operation != "briefs":
            raise HTTPException(422, "The action arguments are invalid")
        data = await room(operation, actor.tenant_id, report_id=report_id) if report_id else await room(operation, actor.tenant_id)
        rows = records(operation, data)
        ledger_id = await pool().fetchval("INSERT INTO tank.ledger(tenant_id,region,agent,task_type,rung,model,cost_usd,tokens_in,tokens_out) VALUES($1,$2,'market_lab',$3,3,'code',0,0,0) RETURNING id", actor.tenant_id, box_region(), "market_lab." + operation)
        return JSONResponse(jsonable_encoder({"records": rows[page * limit:(page + 1) * limit], "total": len(rows), "ledger_id": str(ledger_id), "cost_usd": 0}), headers={"Cache-Control": "private, no-store"})

    async def room_only(request: Request, x_tank_key: str = Header(default="")):
        if not command.key_matches(x_tank_key, os.environ.get("TANK_KEY_MARKET_LAB", "")):
            raise HTTPException(401, "Room key required")

    @router.post("/v1/market-lab/internal/confidence", dependencies=[Depends(room_only)])
    @tenant_path("body")
    async def confidence(body: ConfidenceBody):
        from base_reliability import wilson_lower
        return {"confidence": wilson_lower(body.hits, body.total), "hits": body.hits, "total": body.total}

    @router.post("/v1/market-lab/internal/alerts/{operation}", dependencies=[Depends(room_only)])
    @tenant_path("body")
    async def alerts(operation: str, body: MailBody):
        from main import pool
        if operation in {"settings", "brief-webhook"}:
            async with pool().acquire() as conn:
                settings = await conn.fetchrow("SELECT * FROM tank.market_lab_alerts WHERE tenant_id=$1", body.tenant_id)
            if operation == "settings":
                return {"email": bool(settings and settings["email_enabled"]),
                        "webhook": bool(settings and settings["webhook_url"]),
                        "email_since": settings["email_since"] if settings else None,
                        "webhook_since": settings["webhook_since"] if settings else None}
            if body.report_id is None or body.title is None or body.text is None or body.citations is None:
                raise HTTPException(422, "The action arguments are invalid")
            if not settings or not settings["webhook_url"]:
                return {"status": "suppressed"}
            state = await send_webhook(body.tenant_id, dict(settings),
                                       {"report_id": str(body.report_id), "title": body.title,
                                        "text": body.text, "citations": body.citations}, body.report_id)
            return {"status": state}
        if operation in {"pending", "webhooks"}:
            await prime(body.tenant_id)
        if operation == "pending":
            async with pool().acquire() as conn:
                ids = await conn.fetch("SELECT warning_id FROM tank.market_lab_deliveries WHERE tenant_id=$1 AND channel='email' AND status='pending' ORDER BY created_at,warning_id LIMIT 3", body.tenant_id)
            return {"pending": [str(r["warning_id"]) for r in ids]}
        if operation == "claim" and body.warning_id:
            record = await claim(body.tenant_id, body.warning_id, "email")
            # SMTP's shared sender needs only the letter, never webhook credentials.
            return {"record": {"allowed": record["allowed"], "letter": record["letter"]} if record else None}
        if operation == "finish" and body.warning_id and body.status:
            async with pool().acquire() as conn:
                await conn.execute("UPDATE tank.market_lab_deliveries SET status=$3,updated_at=now() WHERE tenant_id=$1 AND warning_id=$2 AND channel='email' AND status='sending'", body.tenant_id, body.warning_id, body.status)
            return {"status": body.status}
        if operation == "webhooks":
            return await webhooks(body.tenant_id)
        raise HTTPException(404, "Not found")
    return router
