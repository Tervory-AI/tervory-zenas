"""Tenant paid-rung decisions. Provider code never supplies approval authority.

Settings are per logical ladder step, not per provider-wide switch. A grant is
bound to one tenant, run and step. The founder master stop is checked on every
admission, including a previously approved run.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

PRODUCTS = ("base", "tiger", "market_lab", "rig", "tisan", "radix", "zenas", "trigger", "autumbra", "model")
ALIASES = {"ghost": "autumbra", "market": "market_lab", "reason": "model", "command": "model", "tier0": "model", "orchestrator": "model"}
# Exact existing paid vendor boundaries; models are registered from tank.policy.
VENDOR_STEPS = (
    ("base.find_site.brave", "base", "brave.search.query", "tank/skills/base.find_site/1.7.1/run.py"),
    ("base.find_site.places", "base", "google.places.request", "tank/skills/base.find_site/1.7.1/run.py"),
    ("base.context_dev.credit", "base", "context.dev.credit", "agents/base/sources/context_dev.py"),
    ("rig.forge.photogrammetry", "rig", "gpu", "tank/rig_forge_routes.py"),
    ("rig.forge.trellis2", "rig", "gpu", "tank/rig_forge_routes.py"),
)


def db():
    from main import pool
    return pool()


def product(task: str, agent: str | None = None) -> str:
    owner = agent if agent in PRODUCTS or agent in ALIASES else None
    prefix = (owner or (task.split(".")[1] if task.startswith("agent.") else task.partition(".")[0])).lower()
    prefix = ALIASES.get(prefix, prefix)
    return prefix if prefix in PRODUCTS else "model"


def model_step(task: str, model: str, *, agent: str | None = None) -> str:
    """Different tasks and paid seats must have independent switches."""
    slug = re.sub(r"[^a-z0-9_.-]", "_", task.lower())[:100]
    seat = hashlib.sha256(model.encode()).hexdigest()[:16]
    return f"{product(task, agent)}.{slug}.model.{seat}"


@dataclass(frozen=True)
class Run:
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    request_hash: str
    user_id: str | None = None
    thread_id: uuid.UUID | None = None
    admissions: dict = field(default_factory=dict, compare=False)
    paid_calls: set = field(default_factory=set, compare=False)
    root_run_id: uuid.UUID | None = None


current: ContextVar[Run | None] = ContextVar("paid_step_run", default=None)


@contextmanager
def run_scope(run: Run):
    token = current.set(run)
    try:
        yield
    finally:
        current.reset(token)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class ApprovalRequired(BaseException):
    def __init__(self, approval_id: uuid.UUID, step_id: str):
        self.approval_id = approval_id
        self.step_id = step_id
        run = current.get()
        self.detail = {"code": "paid_step_approval_required", "approval_id": str(approval_id), "step_id": step_id,
                       "run_id": str(run.run_id) if run else None,
                       "resume_run_id": str(run.root_run_id or run.run_id) if run else None,
                       "tenant_id": str(run.tenant_id) if run else None}
        super().__init__("Paid step requires tenant administrator approval")


async def register(conn, step_id: str, owner: str, kind: str, source: str, *, task=None, model=None):
    await conn.execute("""INSERT INTO tank.paid_steps(step_id,product,kind,source,task_type,model)
        VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(step_id) DO UPDATE
        SET task_type=coalesce(tank.paid_steps.task_type,EXCLUDED.task_type),
            model=coalesce(tank.paid_steps.model,EXCLUDED.model)""", step_id, owner, kind, source, task, model)


def free_model(info):
    if info.get("research_only") or info.get("input_cost_per_token") != 0 or info.get("output_cost_per_token") != 0:
        return False
    return all(value == 0 for key, value in info.items()
               if "cost_per_" in key or "fee_per_" in key)


async def sync_registry(conn, tenant_id):
    visible = {step[0] for step in VENDOR_STEPS}
    for step_id, owner, kind, source in VENDOR_STEPS:
        await register(conn, step_id, owner, kind, source, task=step_id.rsplit(".",1)[0])
    policies = await conn.fetch("SELECT * FROM tank.policy WHERE tenant_id IS NULL OR tenant_id=$1", tenant_id)
    # Configured chains are metadata only: reading them does not dispatch a model.
    from pathlib import Path
    import yaml
    import main
    path = Path(main._roster_path())
    raw = yaml.safe_load(path.read_text()) if path.is_file() else {}
    chains = {key: value for item in ((raw or {}).get("litellm_settings") or {}).get("fallbacks", []) for key, value in item.items()}
    free = {item["model_name"] for item in (raw or {}).get("model_list", [])
            if free_model(item.get("model_info") or {})}
    async def seat(task, model):
        if model and model not in free and not model.startswith(("skill:", "cache:", "routellm:", "tool:")):
            step_id = model_step(task, model)
            await register(conn, step_id, product(task), "model", "tank.policy", task=task, model=model)
            visible.add(step_id)
    for row in policies:
        task = row["task_type"]
        models = {row["model"], row.get("escalation_model"), row.get("previous_model")}
        models.update(row.get("fallbacks") if row.get("fallbacks") is not None else chains.get(row["model"], []))
        for model in models - {None, ""}:
            await seat(task, model)
            await seat(task + ".benchmark", model)
            await seat(task + ".revalidation", model)
        await seat(task + ".shadow", row.get("candidate_model"))
        await seat(task + ".shadow", row.get("routellm_weak_model"))
        config = row.get("moa_config") or {}
        config = json.loads(config) if isinstance(config, str) else config
        for item in config.get("seats", []):
            await seat(task + "#moa." + item["name"], item.get("model"))
            await seat(task + "#moa." + item["name"], item.get("candidate"))
        for name, model_field in (("aggregator_low","cheap_aggregator"),("aggregator_high","frontier_aggregator"),
                            ("single","top_model"),("aggregator_low","cheap_candidate"),("aggregator_high","frontier_candidate")):
            await seat(task + "#moa." + name, config.get(model_field))
    visible.update(row["step_id"] for row in await conn.fetch(
        "SELECT DISTINCT step_id FROM tank.paid_step_approvals WHERE tenant_id=$1", tenant_id))
    return sorted(visible)


async def admit(step_id: str, *, tenant_id: uuid.UUID, owner: str, kind: str, source: str,
                task: str, paid: bool = True, model: str | None = None) -> dict | None:
    if not paid:
        return None
    from controls import refuse_paid
    try:
        await refuse_paid(task=task, path="paid_steps")
    except HTTPException as exc:
        exc._paid_unspent = True
        raise
    run = current.get()
    # Missing trusted attribution cannot authorize spending, even on Auto.
    if run is None or run.tenant_id != tenant_id:
        raise HTTPException(503, "Paid-step run context is unavailable")
    async with db().acquire() as conn:
        async with conn.transaction():
            await register(conn, step_id, owner, kind, source, task=task, model=model)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", f"{tenant_id}:{run.run_id}:{step_id}")
            prior = await conn.fetchrow("""SELECT * FROM tank.paid_step_approvals
                WHERE tenant_id=$1 AND run_id=$2 AND step_id=$3 FOR UPDATE""", tenant_id, run.run_id, step_id)
            if prior:
                if prior["request_hash"] != run.request_hash:
                    raise HTTPException(409, "Paid-step run does not match its original request")
                if prior["state"] == "denied":
                    raise HTTPException(403, "Paid step denied")
                if prior["state"] == "approved":
                    remember(run, prior)
                    return dict(prior)
                approval_id = prior["id"]
            else:
                mode = await conn.fetchval("""SELECT mode FROM tank.paid_step_settings
                    WHERE tenant_id=$1 AND step_id=$2""", tenant_id, step_id) or "ask"
                approval_id = uuid.uuid4()
                state = "approved" if mode == "auto" else "pending"
                row = await conn.fetchrow("""INSERT INTO tank.paid_step_approvals
                    (id,tenant_id,run_id,step_id,request_hash,state,authorization_mode,user_id,thread_id)
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *""",
                    approval_id, tenant_id, run.run_id, step_id, run.request_hash, state,
                    "automatic" if mode == "auto" else "pending", run.user_id, run.thread_id)
                if state == "approved":
                    remember(run, row)
                    return dict(row)
    # Raise after committing the durable pending record.
    raise ApprovalRequired(approval_id, step_id)


async def decide(tenant_id: uuid.UUID, approval_id: uuid.UUID, *, user: str, approve: bool, reason: str):
    if not reason.strip():
        raise HTTPException(422, "A decision reason is required")
    async with db().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("""SELECT * FROM tank.paid_step_approvals
                WHERE tenant_id=$1 AND id=$2 FOR UPDATE""", tenant_id, approval_id)
            if row is None:
                raise HTTPException(404, "Paid step not found")
            target = "approved" if approve else "denied"
            if row["state"] != "pending":
                if row["state"] != target:
                    raise HTTPException(409, "Paid step has already been resolved")
                return dict(row)
            decision = dict(await conn.fetchrow("""UPDATE tank.paid_step_approvals
                SET state=$3,authorization_mode=$3,decided_by=$4,reason=$5,decided_at=now()
                WHERE tenant_id=$1 AND id=$2 RETURNING *""", tenant_id, approval_id, target, user, reason.strip()))
            if not approve and row.get("continuation"):
                data = json.loads(row["continuation"]) if isinstance(row["continuation"],str) else row["continuation"]
                if data.get("kind") in {"tank.task","command.turn"}:
                    task_id=data.get("task_id",data.get("turn_id"))
                    await conn.execute("""UPDATE tank.tasks SET status='failed',error='Paid step denied by tenant administrator',finished_at=now()
                        WHERE tenant_id=$1 AND id=$2""",tenant_id,uuid.UUID(task_id))
            return decision


class Setting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["ask", "auto"]


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool
    reason: str = Field(min_length=1, max_length=1000)


class RoomAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    paid_run_id: uuid.UUID | None = None
    step_id: Literal["base.context_dev.credit"]
    request_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def build_router():
    # Reuse the exact authenticated Workspace service-key and tenant/role checks.
    from command import identity
    router = APIRouter(prefix="/v1/command/paid-steps")

    def admin(actor):
        if not (actor.tenant_admin or actor.founder):
            raise HTTPException(403, "Tenant administrator required")

    @router.get("")
    async def settings(actor=Depends(identity)):
        admin(actor)
        async with db().acquire() as conn:
            visible = await sync_registry(conn, actor.tenant_id)
            rows = await conn.fetch("""SELECT p.step_id,p.product,p.kind,p.source,p.task_type,p.model,coalesce(s.mode,'ask') AS mode
                FROM tank.paid_steps p LEFT JOIN tank.paid_step_settings s
                ON s.step_id=p.step_id AND s.tenant_id=$1 WHERE p.step_id=ANY($2::text[])
                ORDER BY p.product,p.step_id""", actor.tenant_id, visible)
        return {"steps": [dict(r) for r in rows]}

    @router.put("/{step_id}")
    async def setting(step_id: str, body: Setting, actor=Depends(identity)):
        admin(actor)
        async with db().acquire() as conn:
            async with conn.transaction():
                if step_id not in await sync_registry(conn,actor.tenant_id):
                    raise HTTPException(404, "Paid step not found")
                await conn.execute("""INSERT INTO tank.paid_step_settings(tenant_id,step_id,mode,updated_by)
                    VALUES($1,$2,$3,$4) ON CONFLICT(tenant_id,step_id) DO UPDATE
                    SET mode=EXCLUDED.mode,updated_by=EXCLUDED.updated_by,updated_at=now()""",
                    actor.tenant_id, step_id, body.mode, actor.user_id)
                await conn.execute("""INSERT INTO tank.paid_step_settings_log(tenant_id,step_id,mode,by_user)
                    VALUES($1,$2,$3,$4)""", actor.tenant_id, step_id, body.mode, actor.user_id)
        return {"step_id": step_id, "mode": body.mode}

    @router.get("/approvals/pending")
    async def pending(actor=Depends(identity)):
        admin(actor)
        async with db().acquire() as conn:
            rows = await conn.fetch("""SELECT id,run_id,step_id,state,created_at FROM tank.paid_step_approvals
                WHERE tenant_id=$1 AND state='pending' ORDER BY created_at""", actor.tenant_id)
        return {"approvals": [dict(r) for r in rows]}

    @router.post("/approvals/{approval_id}")
    async def decision(approval_id: uuid.UUID, body: Decision, actor=Depends(identity)):
        admin(actor)
        row = await decide(actor.tenant_id, approval_id, user=actor.user_id, approve=body.approve, reason=body.reason)
        if body.approve:
            kick(row)
        else:
            await finish_denied_command(row)
        return {k: row[k] for k in ("id", "step_id", "state", "reason", "decided_at")}

    async def require_key(request: Request, x_tank_key: str = Header(default="")):
        # Resolve runtime authority when a request arrives, not while unrelated
        # routers are assembled with a substituted main module in protocol tests.
        import main
        return await main.require_key(request, x_tank_key)

    @router.get("/receipt/{run_id}")
    async def receipt(run_id: uuid.UUID, actor=Depends(identity)):
        admin(actor)
        async with db().acquire() as conn:
            rows = await conn.fetch("""SELECT step_id,state,authorization_mode,decided_by,reason,created_at,decided_at
                FROM tank.paid_step_approvals WHERE tenant_id=$1 AND run_id=$2 ORDER BY created_at""", actor.tenant_id, run_id)
        return {"steps": [dict(r) for r in rows]}

    room_router = APIRouter(prefix="/v1/paid-steps")

    @room_router.post("/admit")
    async def room_admit(body: RoomAdmission, caller=Depends(require_key)):
        if caller != "base" or body.step_id != "base.context_dev.credit":
            raise HTTPException(403, "Owning room required")
        from vendor_meter import price
        from datetime import datetime, timezone
        async with db().acquire() as conn:
            quote = await price(conn, "context.dev.credit", datetime.now(timezone.utc))
        with run_scope(Run(body.tenant_id, body.run_id, body.request_hash)):
            row = await admit(body.step_id, tenant_id=body.tenant_id, owner="base", kind="context.dev.credit",
                              source="agents/base/sources/context_dev.py", task="base.context_dev", paid=not bool(quote and quote["free_pass"]))
        return {"approval_id": str(row["id"]) if row else None, "mode": row["authorization_mode"] if row else "free"}

    @room_router.get("/approvals/{approval_id}")
    async def room_status(approval_id: uuid.UUID, tenant_id: uuid.UUID, caller=Depends(require_key)):
        async with db().acquire() as conn:
            row = await conn.fetchrow("""SELECT a.state,a.run_id,p.product FROM tank.paid_step_approvals a
                JOIN tank.paid_steps p ON p.step_id=a.step_id WHERE a.tenant_id=$1 AND a.id=$2""", tenant_id, approval_id)
        if row is None or (caller is not None and product(caller, caller) != row["product"]):
            raise HTTPException(404, "Paid step not found")
        return {"state": row["state"], "run_id": str(row["run_id"])}

    combined = APIRouter()
    combined.include_router(router)
    combined.include_router(room_router)
    return combined


async def checkpoint(key: str, invoke):
    """Replay a completed paid boundary; never repeat an uncertain dispatch.

    Only use inside a trusted run. The call key is an opaque digest, never a URL,
    prompt or credential. Completed results stay inside the tenant's home DB.
    """
    run = current.get()
    if run is None:
        return await invoke()
    async with connection(run.tenant_id) as conn:
        row = await conn.fetchrow("""INSERT INTO tank.paid_step_calls
            (tenant_id,run_id,call_key,request_hash,state) VALUES($1,$2,$3,$4,'executing')
            ON CONFLICT(tenant_id,run_id,call_key) DO NOTHING RETURNING state""",
            run.tenant_id, run.run_id, key, run.request_hash)
        if row is None:
            row = await conn.fetchrow("""SELECT state,result,request_hash FROM tank.paid_step_calls
                WHERE tenant_id=$1 AND run_id=$2 AND call_key=$3""", run.tenant_id, run.run_id, key)
            if row["request_hash"] != run.request_hash:
                raise HTTPException(409, "Paid-step run does not match its original request")
            if row["state"] != "completed":
                raise HTTPException(409, "Paid call outcome is unknown; do not repeat it")
            return json.loads(row["result"]) if isinstance(row["result"], str) else row["result"]
    try:
        result = await invoke()
    except BaseException as exc:
        if isinstance(exc, ApprovalRequired) or getattr(exc, "_paid_unspent", False):
            async with connection(run.tenant_id) as conn:
                await conn.execute("""DELETE FROM tank.paid_step_calls
                    WHERE tenant_id=$1 AND run_id=$2 AND call_key=$3 AND state='executing'""",
                    run.tenant_id, run.run_id, key)
        raise
    # Any other exception/crash leaves an uncertain claim; automatic replay is refused.
    async with connection(run.tenant_id) as conn:
        await conn.execute("""UPDATE tank.paid_step_calls SET state='completed',result=$4::jsonb
            WHERE tenant_id=$1 AND run_id=$2 AND call_key=$3 AND state='executing'""",
            run.tenant_id, run.run_id, key, json.dumps(result, default=str))
    return result


async def model_admission(tenant_id: uuid.UUID, task: str, model: str, *, agent: str | None = None, roster=None):
    if roster is None:
        import main
        roster = await main._roster_models()
    info = next((r.get("model_info", {}) for r in roster if r.get("model_name") == model), {})
    free = free_model(info)
    return await admit(model_step(task, model, agent=agent), tenant_id=tenant_id,
                       owner=product(task, agent), kind="model", source="tank.policy", task=task, paid=not free, model=model)


async def http_admission(request):
    import vendor_meter
    run = vendor_meter.current.get()
    if run is None:
        raise HTTPException(503, "Paid vendor attribution is unavailable")
    vendor = vendor_meter.product(request)
    if vendor is None:
        return
    task = run.skill.partition("@")[0]
    suffix = {"brave.search": "brave", "google.places": "places"}[vendor[0]]
    return await admit(f"{task}.{suffix}", tenant_id=run.tenant, owner=product(task, run.agent),
                kind=vendor[1], source="tank/vendor_meter.py", task=task)


async def sync_cards(thread_id, actor):
    """Use the existing confirm card, only in an authenticated admin's thread."""
    import command
    if not (actor.tenant_admin or actor.founder):
        return
    # Reading a thread with no new approvals must not rewrite its state or
    # updated_at. Recheck under the existing edit lock only when work exists.
    async with command.db().acquire() as conn:
        row = await command.owned(conn, thread_id, actor)
        cards = command.unpack(row["state"])["cards"]
        unresolved = [uuid.UUID(key) for key,card in cards.items()
                      if card.get("tool")=="paid_steps.approve" and card.get("status")=="pending"]
        pending = await conn.fetch("""SELECT id,state FROM tank.paid_step_approvals
            WHERE tenant_id=$1 AND ((delivery_thread_id=$2 AND id=ANY($3::uuid[]) AND state<>'pending')
                OR (delivery_thread_id IS NULL AND state='pending'))
            ORDER BY created_at LIMIT 100""", actor.tenant_id, thread_id,unresolved)
    statuses = {"pending": "pending", "approved": "confirmed", "denied": "cancelled"}
    if not any(cards.get(str(item["id"]), {}).get("status") != statuses[item["state"]] for item in pending):
        return
    async with command.editing(thread_id, actor) as (conn, row, state):
        await _sync_cards_locked(conn,row,state,thread_id,actor)


async def _sync_cards_locked(conn,row,state,thread_id,actor):
    import command
    if not (actor.tenant_admin or actor.founder):
        return
    unresolved = [uuid.UUID(key) for key,card in state["cards"].items()
                  if card.get("tool")=="paid_steps.approve" and card.get("status")=="pending"]
    approvals = await conn.fetch("""SELECT a.id,a.step_id,a.state,a.authorization_mode,a.reason,a.continuation,p.model,p.task_type
        FROM tank.paid_step_approvals a JOIN tank.paid_steps p USING(step_id)
        WHERE a.tenant_id=$1 AND ((a.delivery_thread_id=$2 AND a.id=ANY($3::uuid[]) AND a.state<>'pending')
            OR (a.delivery_thread_id IS NULL AND a.state='pending'))
        ORDER BY a.created_at LIMIT 100 FOR UPDATE OF a SKIP LOCKED""", actor.tenant_id, thread_id,unresolved)
    for approval in approvals:
        key = str(approval["id"])
        status = {"pending": "pending", "approved": "confirmed", "denied": "cancelled"}[approval["state"]]
        card = state["cards"].get(key)
        if card and card["status"] == status:
            continue
        await conn.execute("""UPDATE tank.paid_step_approvals SET delivery_thread_id=$3
            WHERE tenant_id=$1 AND id=$2 AND delivery_thread_id IS NULL""", actor.tenant_id, approval["id"], thread_id)
        card = {"card_id": key, "kind": "paid_run",
                "summary": f"{approval['task_type']} · {approval['model']}" if approval["model"] else approval["step_id"],
                "tool": "paid_steps.approve", "arguments": {"step_id": approval["step_id"]},
                "status": status, "result": {"authorization_mode": approval["authorization_mode"], "reason": approval["reason"]}}
        state["cards"][key] = card
        continuation = approval["continuation"]
        if isinstance(continuation,str):
            continuation = json.loads(continuation)
        if status == "cancelled" and continuation and continuation.get("kind") == "command.action":
            original = state["cards"].get(continuation["card_id"])
            if original and original.get("paid_approval_id") == key:
                original.update(status="cancelled",retry_safe=False)
                original.pop("paid_approval_id",None)
                await command.event(conn,row,"card",command.public_card(original))
        await command.event(conn, row, "card", command.public_card(card))
        await command.step(conn, row, state, {"step_id": key, "label": approval["step_id"],
                           "actor": "Tervory", "status": "planned" if status == "pending" else "done"})


async def resolve_card(thread_id, card_id, body, actor):
    import command
    async with command.db().acquire() as conn:
        row = await command.owned(conn, thread_id, actor)
    card = command.unpack(row["state"])["cards"][str(card_id)]
    if card.get("tool") != "paid_steps.approve":
        return None
    actor = await command.authorized_actor(actor)
    if not (actor.tenant_admin or actor.founder):
        raise HTTPException(403, "Tenant administrator required")
    decision = await decide(actor.tenant_id, card_id, user=actor.user_id, approve=body.action == "confirm",
                 reason=body.reason or ("Confirmed by tenant administrator" if body.action == "confirm" else "Cancelled by tenant administrator"))
    if body.action == "confirm":
        kick(decision)
    else:
        await finish_denied_command(decision)
    await sync_cards(thread_id, actor)
    async with command.db().acquire() as conn:
        row = await command.owned(conn, thread_id, actor)
    return command.public_card(command.unpack(row["state"])["cards"][str(card_id)])


class ApprovalMiddleware:
    """A pause is control flow, not a caught-and-skipped skill fallback error."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        started = False
        async def observe(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)
        try:
            await self.app(scope, receive, observe)
        except ApprovalRequired as pause:
            if started or scope["type"] != "http":
                raise
            await send({"type": "http.response.start", "status": 409,
                        "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": json.dumps({"detail": pause.detail}).encode()})


async def suspend_action(detail, thread_id, actor, card_id, action):
    """Only the checkpointed research request may resume a direct tool card."""
    import command
    approval_id = uuid.UUID(detail["approval_id"])
    async with command.editing(thread_id, actor) as (conn, row, state):
        stored = await conn.fetchrow("""UPDATE tank.paid_step_approvals
            SET continuation=$4::jsonb,resume_status=CASE WHEN state='denied' THEN 'failed' ELSE 'waiting' END,thread_id=$5
            WHERE tenant_id=$1 AND id=$2 AND run_id=$3 AND continuation IS NULL RETURNING *""",
            actor.tenant_id, approval_id, uuid.UUID(card_id),
            json.dumps({"kind":"command.action", "thread_id":str(thread_id), "user_id":actor.user_id,
                        "tenant_admin":actor.tenant_admin, "card_id":card_id, "action":action.model_dump()}), thread_id)
        if stored is None:
            raise HTTPException(409, "Paid research approval is unavailable")
        card = state["cards"][card_id]
        card.update(status="pending", paid_approval_id=str(approval_id), retry_safe=False)
        await command.event(conn, row, "card", command.public_card(card))
        await command.step(conn, row, state, {"step_id":card_id,"label":card["summary"],
                           "actor":"market_lab","status":"planned"})
    await sync_cards(thread_id, actor)
    if stored["state"]=="approved":
        kick(dict(stored))
    elif stored["state"]=="denied":
        await finish_denied_command(dict(stored))
    return command.public_card(card)


async def suspend_turn(pause, thread_id, actor, turn_id, messages, model, priority):
    import command
    continuation = {"kind": "command.turn", "thread_id": str(thread_id), "user_id": actor.user_id,
                    "tenant_admin": actor.tenant_admin, "turn_id": turn_id, "messages": messages,
                    "model": model, "priority": priority}
    async with command.editing(thread_id, actor) as (conn, row, state):
        saved = await conn.fetchrow("""UPDATE tank.paid_step_approvals
            SET continuation=$3::jsonb,resume_status=CASE WHEN state='denied' THEN 'failed' ELSE 'waiting' END,thread_id=$4
            WHERE tenant_id=$1 AND id=$2 AND continuation IS NULL RETURNING *""", actor.tenant_id,pause.approval_id,
            json.dumps(continuation),thread_id)
        if saved is None:
            saved=await conn.fetchrow("SELECT * FROM tank.paid_step_approvals WHERE tenant_id=$1 AND id=$2",actor.tenant_id,pause.approval_id)
        denied=saved["state"]=="denied"
        await conn.execute("UPDATE tank.tasks SET status=$3 WHERE tenant_id=$1 AND id=$2 AND status='running'",
            actor.tenant_id,uuid.UUID(turn_id),'failed' if denied else 'queued')
        if state["active_turn"]==turn_id:
            state["active_turn"] = None
        await command.step(conn,row,state,{**state["steps"][turn_id],"status":"failed" if denied else "planned",
                           "paused":not denied,"phase_finished":True})
        # Commit the pending card before ending the original stream phase.
        await _sync_cards_locked(conn,row,state,thread_id,actor)
    if saved["state"]=="approved":
        kick(dict(saved))


async def finish_denied_command(decision):
    """Reconcile the private originating card after the decision transaction."""
    value=decision.get("continuation")
    data=json.loads(value) if isinstance(value,str) else value
    if not data or data.get("kind") not in {"command.turn","command.action"}:
        return
    import command
    actor=command.Actor(decision["tenant_id"],data["user_id"],False,tenant_admin=data["tenant_admin"])
    try:
        async with command.editing(uuid.UUID(data["thread_id"]),actor) as (conn,row,state):
            key=data.get("turn_id",data.get("card_id"))
            if data["kind"]=="command.action":
                card=state["cards"].get(key)
                if card:
                    card.update(status="cancelled",retry_safe=False)
                    card.pop("paid_approval_id",None)
                    await command.event(conn,row,"card",command.public_card(card))
            if key in state["steps"] and state["steps"][key].get("status")!="done":
                await command.step(conn,row,state,{**state["steps"][key],"status":"failed","paused":False,"phase_finished":False})
            if state["active_turn"]==key:
                state["active_turn"]=None
    except HTTPException as exc:
        if exc.status_code!=404:
            raise


_background = set()


def kick(row):
    if not row.get("continuation"):
        return
    import asyncio
    task = asyncio.create_task(resume(row["tenant_id"], row["id"]))
    _background.add(task)
    def done(completed):
        _background.discard(completed)
        if not completed.cancelled():
            completed.exception()
    task.add_done_callback(done)


async def resume(tenant_id, approval_id):
    try:
        await _resume(tenant_id, approval_id)
    except Exception as error:
        # An explicit refusal before dispatch (including the master stop) can
        # wait safely. An uncertain dispatched call requires inspection.
        unspent = bool(getattr(error, "_paid_unspent", False))
        async with connection(tenant_id) as conn:
            await conn.execute("""UPDATE tank.paid_step_approvals SET resume_status=$3
                WHERE tenant_id=$1 AND id=$2 AND resume_status='running'""", tenant_id, approval_id,
                "waiting" if unspent else "failed")
        if not unspent:
            raise


async def _resume(tenant_id, approval_id):
    import command
    from tenant_db import tenant_scope
    with tenant_scope(tenant_id):
        async with db().acquire() as conn:
            row = await conn.fetchrow("""UPDATE tank.paid_step_approvals
                SET resume_status='running',resume_started_at=now()
                WHERE tenant_id=$1 AND id=$2 AND state='approved' AND continuation IS NOT NULL
                AND (resume_status='waiting' OR (resume_status='running' AND resume_started_at < now()-interval '15 minutes'))
                RETURNING *""", tenant_id, approval_id)
        if row is None:
            return
        data = json.loads(row["continuation"]) if isinstance(row["continuation"], str) else row["continuation"]
        if data.get("kind") != "command.turn":
            import main
            # Reuse the original run identity so the approved boundary is the
            # same one; no caller can manufacture a grant with input fields.
            with run_scope(Run(tenant_id, uuid.UUID(data["root_run_id"]) if data.get("root_run_id") else row["run_id"], data.get("root_request_hash", row["request_hash"]))):
                if data.get("kind") == "command.action":
                    actor = command.Actor(tenant_id,data["user_id"],False,tenant_admin=data["tenant_admin"])
                    thread_id = uuid.UUID(data["thread_id"])
                    async with command.editing(thread_id,actor) as (conn,thread,state):
                        card = state["cards"][data["card_id"]]
                        card.pop("paid_approval_id",None)
                        card.update(status="executing",started_at=command.datetime.now(command.timezone.utc).isoformat())
                    await command.execute_card(thread_id,actor,data["card_id"],command.PlannedAction(**data["action"]))
                elif data.get("kind") == "tank.task":
                    await main._run_task(uuid.UUID(data["task_id"]),main.RouteRequest.model_validate(data["request"]))
                elif data.get("kind") == "shadow":
                    await main._shadow(main.RouteRequest.model_validate(data["request"]), data["policy"],
                        data["messages"], data["max_tokens"], uuid.UUID(data["ledger_id"]),
                        data["primary"], data["primary_cost"], routellm=(main.routellm_shadow.Configuration(**data["routellm"][0]), data["routellm"][1]) if data["routellm"] else None,
                        cache_content=data["cache_content"])
                elif data.get("kind") == "moa_shadow":
                    import moa
                    value = data["result"]
                    results = [moa.Result(**{**item, "answer":moa.Answer(**item["answer"]) if item["answer"] else None,
                        "attempts":[moa.Answer(**attempt) for attempt in item["attempts"]]}) for item in value["results"]]
                    await moa.shadow_seats(main.RouteRequest.model_validate(data["request"]),data["policy"],
                        moa.Mixture(**{**value,"results":results}),uuid.UUID(data["run_id"]),data["caller"])
                elif data.get("kind") == "moa_benchmark":
                    import moa
                    import bench
                    from decimal import Decimal
                    await moa.bench_task(data["policy"], bench.price_book(await main._roster_models()),
                        data["region"], Decimal(data["cap"]), force=data["force"])
                elif data.get("kind") == "benchmark":
                    import bench
                    from decimal import Decimal
                    await bench.bench_model(main.pool(), data["policy"], data["model"], data["cases"],
                        bench.price_book(await main._roster_models()), data["region"], Decimal(data["cap"]), data["comparison"])
                else:
                    return
            async with db().acquire() as conn:
                await conn.execute("UPDATE tank.paid_step_approvals SET resume_status='completed' WHERE tenant_id=$1 AND id=$2", tenant_id, approval_id)
            return
        actor = command.Actor(tenant_id, data["user_id"], False, tenant_admin=data["tenant_admin"])
        async with command.editing(uuid.UUID(data["thread_id"]),actor) as (conn,thread,state):
            if state["active_turn"] not in {None,data["turn_id"]}:
                error=HTTPException(409,"Another turn is still running")
                error._paid_unspent=True
                raise error
            state["active_turn"]=data["turn_id"]
            await conn.execute("UPDATE tank.tasks SET status='running' WHERE tenant_id=$1 AND id=$2 AND status='queued'",
                tenant_id,uuid.UUID(data["turn_id"]))
            if data["turn_id"] in state["steps"]:
                await command.step(conn,thread,state,{**state["steps"][data["turn_id"]],"status":"running","paused":False,"phase_finished":False})
        await command.finish_turn(uuid.UUID(data["thread_id"]), actor, data["turn_id"],
                                  data["messages"], data["model"], priority=data["priority"])
        async with db().acquire() as conn:
            await conn.execute("""UPDATE tank.paid_step_approvals SET resume_status='completed'
                WHERE tenant_id=$1 AND id=$2 AND resume_status='running'""", tenant_id, approval_id)


async def recover(tank):
    """Discover committed continuations; all execution returns to tenant scope."""
    pool = getattr(tank, '_admin_pool', None)
    if pool is None:
        return
    async with pool.acquire() as conn:
        rows = await conn.fetch("""SELECT tenant_id,id,continuation FROM tank.paid_step_approvals
            WHERE state='approved' AND continuation IS NOT NULL AND
            (resume_status='waiting' OR (resume_status='running' AND resume_started_at < now()-interval '15 minutes'))
            ORDER BY created_at LIMIT 100""")
    for row in rows:
        kick(dict(row))


async def attach_receipt(ledger_id, authorization):
    if authorization is None:
        return
    run = current.get()
    if run is None:
        raise HTTPException(503, "Paid receipt context is unavailable")
    item = {"step_id": authorization["step_id"], "approval_id": str(authorization["id"]),
            "mode": authorization["authorization_mode"]}
    async with connection(run.tenant_id) as conn:
        await conn.execute("""UPDATE tank.ledger SET
            paid_step_id=coalesce(paid_step_id,$3),paid_step_approval_id=coalesce(paid_step_approval_id,$4),
            paid_step_mode=coalesce(paid_step_mode,$5),
            paid_steps=CASE WHEN paid_steps @> $6::jsonb THEN paid_steps ELSE paid_steps || $6::jsonb END
            WHERE tenant_id=$1 AND id=$2 AND EXISTS(SELECT 1 FROM tank.paid_step_approvals a
                WHERE a.id=$4 AND a.tenant_id=$1 AND a.state='approved')""",
            run.tenant_id, ledger_id, authorization["step_id"], uuid.UUID(str(authorization["id"])),
            authorization["authorization_mode"], json.dumps([item]))


async def bootstrap_access(conn, tenant_role: str):
    # Identifier comes from the already-open authenticated tenant connection,
    # quoted by PostgreSQL itself. Never grant to PUBLIC or discover extra roles.
    sql = await conn.fetchval("""SELECT format('GRANT SELECT,INSERT,UPDATE ON tank.paid_steps TO %1$I;
        GRANT SELECT,INSERT ON tank.paid_step_settings_log TO %1$I;
        GRANT SELECT,INSERT,UPDATE ON tank.paid_step_settings,tank.paid_step_approvals TO %1$I;
        GRANT SELECT,INSERT,UPDATE,DELETE ON tank.paid_step_calls TO %1$I', $1::text)""", tenant_role)
    await conn.execute(sql)


async def background_admission(task, model, payload):
    """R&D has its own paid step and platform cost owner, separate from delivery."""
    from datetime import datetime, timezone
    from tenant_db import tenant_scope
    platform = uuid.UUID('00000000-0000-0000-0000-000000000001')
    parent = current.get()
    digest = fingerprint([task, model, payload])
    run_id = parent.run_id if parent and parent.tenant_id == platform and parent.request_hash == digest else uuid.uuid5(
        parent.run_id if parent else platform, digest + ("" if parent else datetime.now(timezone.utc).date().isoformat()))
    with tenant_scope(platform), run_scope(Run(platform, run_id, digest)):
        row = await model_admission(platform, task, model)
    if row and parent:
        remember(parent, row)
    return row


async def save_background(pause, continuation):
    from tenant_db import tenant_scope
    tenant = uuid.UUID(pause.detail['tenant_id'])
    with tenant_scope(tenant):
        async with db().acquire() as conn:
            row=await conn.fetchrow("""UPDATE tank.paid_step_approvals
                SET continuation=$3::jsonb,resume_status=CASE WHEN state='denied' THEN 'failed' ELSE 'waiting' END
                WHERE tenant_id=$1 AND id=$2 AND continuation IS NULL RETURNING *""",tenant,pause.approval_id,
                json.dumps(continuation,default=str))
            if row is None:
                row=await conn.fetchrow("SELECT * FROM tank.paid_step_approvals WHERE tenant_id=$1 AND id=$2",tenant,pause.approval_id)
            if row['state']=='denied' and continuation.get('kind')=='tank.task':
                await conn.execute("""UPDATE tank.tasks SET status='failed',error=$3,finished_at=now()
                    WHERE tenant_id=$1 AND id=$2 AND status<>'done'""",tenant,uuid.UUID(continuation['task_id']),row['reason'])
    saved=dict(row)
    if saved['state']=='approved' and continuation.get('kind')!='tank.task':
        kick(saved)
    return saved


background_task = ContextVar("paid_background_task", default="model.shadow")
background_budget = ContextVar("paid_background_budget", default=None)


@contextmanager
def background_scope(task, budget_task=None):
    token = background_task.set(task)
    budget = background_budget.set(budget_task or background_budget.get())
    try:
        yield
    finally:
        background_budget.reset(budget)
        background_task.reset(token)


def metadata(row):
    return {"id": str(row["id"]), "step_id": row["step_id"], "authorization_mode": row["authorization_mode"]}


def remember(run, row):
    run.admissions[str(row["id"])] = metadata(row)


async def attach_all(ledger_id):
    run = current.get()
    if run:
        for decision in run.admissions.values():
            await attach_receipt(ledger_id, decision)


@asynccontextmanager
async def connection(tenant_id):
    from tenant_db import tenant_scope
    with tenant_scope(tenant_id):
        async with db().acquire() as conn:
            yield conn


async def reserve_shadow(task, ceiling, cap):
    async with connection(uuid.UUID('00000000-0000-0000-0000-000000000001')) as conn:
        async with conn.transaction():
            await conn.execute("INSERT INTO tank.shadow_spend(task_type,day,spent_usd) VALUES($1,CURRENT_DATE,0) ON CONFLICT DO NOTHING", task)
            reserved = await conn.fetchval("""UPDATE tank.shadow_spend SET spent_usd=spent_usd+$2
                WHERE task_type=$1 AND day=CURRENT_DATE AND spent_usd+$2 <= $3 RETURNING spent_usd""", task, ceiling, cap)
    if reserved is None:
        error = HTTPException(409, "Paid evaluation budget is exhausted")
        error._paid_unspent = True
        raise error


async def settle_shadow(task, ceiling, cost):
    if cost is None:
        return  # An uncertain call keeps its reservation.
    async with connection(uuid.UUID('00000000-0000-0000-0000-000000000001')) as conn:
        await conn.execute("""UPDATE tank.shadow_spend SET spent_usd=greatest(0,spent_usd-$2+$3)
            WHERE task_type=$1 AND day=CURRENT_DATE""", task, ceiling, cost)


async def begin_model_receipt(approval, model, task, region, *, agent="shadow", skill=None):
    if approval is None:
        return None
    tenant = uuid.UUID(str(approval['tenant_id']))
    ledger_id = uuid.uuid4()
    with run_scope(Run(tenant, approval['run_id'], approval['request_hash'])):
        async with connection(tenant) as conn:
            await conn.execute("""INSERT INTO tank.ledger
                (id,tenant_id,region,agent,task_type,rung,model,priority,cost_usd,cost_status,outcome,skill)
                VALUES($1,$2,$3,$6,$4,1,$5,'batch',0,'not_set','unverified',$7)""",
                ledger_id, tenant, region, task, model, agent, skill)
        await attach_receipt(ledger_id, approval)
    return ledger_id


async def finish_model_receipt(approval, ledger_id, meter, *, successful):
    if ledger_id is None:
        return
    tenant = uuid.UUID(str(approval['tenant_id']))
    cost = meter.get('cost')
    async with connection(tenant) as conn:
        await conn.execute("""UPDATE tank.ledger SET model=$3,tokens_in=$4,tokens_out=$5,
            cost_usd=coalesce($6::numeric,0),cost_status=CASE WHEN $6::numeric IS NULL THEN 'not_set' ELSE 'set' END,
            outcome=$7 WHERE tenant_id=$1 AND id=$2""", tenant, ledger_id, meter.get('model', 'unverified'),
            meter.get('tokens_in',0), meter.get('tokens_out',0), cost, 'delivered' if successful else 'unverified')


async def batch_admission(req, model, item_id):
    """Recheck a queued item's existing grant immediately before submission."""
    step_id = model_step(req.task_type,model,agent=req.agent)
    run_id = req.paid_run_id or item_id
    async with connection(req.tenant_id) as conn:
        existing = await conn.fetchrow("""SELECT request_hash FROM tank.paid_step_approvals
            WHERE tenant_id=$1 AND run_id=$2 AND step_id=$3""",req.tenant_id,run_id,step_id)
    # Queued input is an immutable server-owned snapshot. Retain its original
    # admission hash even when storage masking changed its representation.
    digest = existing["request_hash"] if existing else fingerprint(req.model_dump(exclude={"paid_run_id"}))
    with run_scope(Run(req.tenant_id,run_id,digest)):
        return await model_admission(req.tenant_id,req.task_type,model,agent=req.agent)
