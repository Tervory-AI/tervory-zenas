"""RIG-5F vendor receipts and fail-closed GPU reservations, relayed by Rig's brain.

The existing /v1/ledger/vendor credit-only contract is deliberately independent.
No provider token, URL, arbitrary error text or command is accepted here.
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal, ROUND_CEILING
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

import controls
from agent_skills import local_region, room_key


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: uuid.UUID
    job_id: uuid.UUID
    home_region: Literal["us-east"]


class Reserve(Scope):
    method: Literal["photogrammetry", "trellis2"]
    timeout_seconds: int = Field(ge=60, le=3600)
    cost_cap_usd: Decimal = Field(gt=0, le=100, max_digits=12, decimal_places=6)


class Complete(Scope):
    outcome: Literal["delivered", "failed", "unverified"]
    provider_job_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")


class Step(Scope):
    stage: Literal["intake", "preflight", "blender", "dedup", "simplify", "meshopt",
                   "resize", "gltfpack", "ktx2", "meshopt_final", "model_check", "validate", "upload", "gpu_submit", "gpu_poll", "gpu_cancel", "gpu_download"]
    outcome: Literal["delivered", "failed"]


def build_router(tank) -> APIRouter:
    router = APIRouter(prefix="/v1/agents/rig/forge")

    def auth(body: Scope, request: Request):
        room_key("rig", request.headers.get("authorization", ""), request.headers.get("x-tank-key", ""))
        local_region(tank, body.home_region)

    async def get_job(conn, body: Scope):
        row = await conn.fetchrow("""SELECT * FROM tank.rig_forge_jobs
            WHERE tenant_id=$1 AND id=$2 FOR UPDATE""", body.tenant_id, body.job_id)
        if row is None:
            raise HTTPException(404, "Forge reservation not found.")
        return row

    @router.post("/reserve")
    async def reserve(body: Reserve, request: Request):
        auth(body, request)
        await controls.refuse_paid(task="rig.forge.gpu",path="rig.forge.reserve")
        async with tank.pool().acquire() as conn:
            async with conn.transaction():
                # A reservation has one immutable request and one launch claim.
                # Lock the tenant's configured budget to serialize new jobs.
                limit = await conn.fetchrow("""SELECT * FROM tank.rig_forge_limits
                    WHERE tenant_id=$1 AND method=$2 AND enabled FOR UPDATE""", body.tenant_id, body.method)
                if limit is None:
                    raise HTTPException(503, "GPU budget and approved image are not configured.")
                previous = await conn.fetchrow("""SELECT * FROM tank.rig_forge_jobs
                    WHERE tenant_id=$1 AND id=$2""", body.tenant_id, body.job_id)
                if previous:
                    if json.loads(previous["request"]) != body.model_dump(mode="json"):
                        raise HTTPException(409, "Job id has different reservation inputs.")
                    return json.loads(previous["grant_payload"])
                seconds = Decimal(body.timeout_seconds)
                quantum = Decimal(limit["billing_quantum_seconds"])
                billed = (seconds / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum
                upper = (billed * limit["hour_usd"] / Decimal(3600)).quantize(Decimal("0.000001"), rounding=ROUND_CEILING)
                if (body.timeout_seconds > limit["max_seconds"] or upper > body.cost_cap_usd
                        or body.cost_cap_usd > limit["max_job_usd"]):
                    raise HTTPException(409, "GPU timeout exceeds the approved cost cap.")
                used = await conn.fetchval("""SELECT COALESCE(sum(reserved_usd),0) FROM tank.rig_forge_jobs
                    WHERE tenant_id=$1 AND method=$2""", body.tenant_id, body.method)
                # Reservations never disappear on failure/restart. Unknown remote
                # outcomes retain their entire charge until operator reconciliation.
                if used + upper > limit["total_budget_usd"]:
                    raise HTTPException(409, "The configured GPU budget is exhausted.")
                import paid_steps
                with paid_steps.run_scope(paid_steps.Run(body.tenant_id,body.job_id,paid_steps.fingerprint(body.model_dump()))):
                    decision = await paid_steps.admit("rig.forge."+body.method,tenant_id=body.tenant_id,
                        owner="rig",kind="gpu",source="tank/rig_forge_routes.py",task="rig.forge."+body.method)
                result = {"tenant_id": str(body.tenant_id), "job_id": str(body.job_id),
                          "method": body.method, "image": limit["image"], "flavor": limit["flavor"],
                          "timeout_seconds": body.timeout_seconds, "reserved_usd": str(upper),
                          "cost_basis": "upper_bound", "state": "reserved",
                          "paid_step_id": decision["step_id"], "paid_step_approval_id": str(decision["id"]),
                          "paid_step_mode": decision["authorization_mode"]}
                await conn.execute("""INSERT INTO tank.rig_forge_jobs
                    (tenant_id,id,method,request,grant_payload,reserved_usd,state)
                    VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6,'reserved')""",
                    body.tenant_id, body.job_id, body.method, json.dumps(body.model_dump(mode="json")),
                    json.dumps(result), upper)
                return result

    @router.post("/claim")
    async def claim(body: Scope, request: Request):
        auth(body, request)
        await controls.refuse_paid(task="rig.forge.gpu", path="rig.forge.claim")
        async with tank.pool().acquire() as conn:
            reserved = await get_job(conn, body)
        import paid_steps
        original = json.loads(reserved["request"]) if isinstance(reserved["request"], str) else reserved["request"]
        with paid_steps.run_scope(paid_steps.Run(body.tenant_id, body.job_id, paid_steps.fingerprint(original))):
            decision = await paid_steps.admit("rig.forge." + reserved["method"], tenant_id=body.tenant_id,
                owner="rig", kind="gpu", source="tank/rig_forge_routes.py", task="rig.forge." + reserved["method"])
        paid = {"paid_step_id": decision["step_id"], "paid_step_approval_id": str(decision["id"]),
                "paid_step_mode": decision["authorization_mode"]}
        async with tank.pool().acquire() as conn:
            async with conn.transaction():
                row = await get_job(conn, body)
                if row["state"] != "reserved":
                    raise HTTPException(409, "This GPU reservation was already claimed; never relaunch it.")
                grant = json.loads(row["grant_payload"]) if isinstance(row["grant_payload"], str) else row["grant_payload"]
                await conn.execute("""UPDATE tank.rig_forge_jobs SET state='claimed',grant_payload=$3::jsonb
                    WHERE tenant_id=$1 AND id=$2""", body.tenant_id, body.job_id, json.dumps({**grant, **paid}))
                ledger = uuid.uuid5(body.job_id, str(body.tenant_id) + ":gpu")
                pending = {"tenant_id": str(body.tenant_id), "task_id": str(body.job_id), "room": "rig",
                           "region": body.home_region, "ledger_id": str(ledger), "provider_job_id": "unknown",
                           "outcome": "unverified", "cost_usd": str(row["reserved_usd"]), "cost_basis": "upper_bound", **paid}
                # Account for uncertain submissions before the provider is called.
                await conn.execute("""INSERT INTO tank.ledger
                    (id,tenant_id,region,agent,task_type,model,vendor,units,cost_usd,outcome,rung,task_id)
                    VALUES ($1,$2,$3,'rig',$4,'vendor:huggingface_jobs','huggingface_jobs',1,$5,'unverified',1,$6)""",
                    ledger, body.tenant_id, body.home_region, "rig.forge." + row["method"], row["reserved_usd"], body.job_id)
                await conn.execute("""UPDATE tank.ledger SET paid_step_id=$3,paid_step_approval_id=$4,
                    paid_step_mode=$5,paid_steps=$6::jsonb WHERE tenant_id=$1 AND id=$2""",
                    body.tenant_id, ledger, decision["step_id"], decision["id"], decision["authorization_mode"],
                    json.dumps([{"step_id": decision["step_id"], "approval_id": str(decision["id"]), "mode": decision["authorization_mode"]}]))
                await conn.execute("""INSERT INTO tank.agent_receipts (ledger_id,tenant_id,room,receipt)
                    VALUES ($1,$2,'rig',$3::jsonb)""", ledger, body.tenant_id, json.dumps(pending))
        return {"claimed": True, "job_id": str(body.job_id)}

    @router.post("/complete")
    async def complete(body: Complete, request: Request):
        auth(body, request)
        async with tank.pool().acquire() as conn:
            async with conn.transaction():
                row = await get_job(conn, body)
                ledger = uuid.uuid5(body.job_id, str(body.tenant_id) + ":gpu")
                grant = json.loads(row["grant_payload"]) if isinstance(row["grant_payload"], str) else row["grant_payload"]
                provenance = {key: grant[key] for key in ("paid_step_id", "paid_step_approval_id", "paid_step_mode") if key in grant}
                result = {"tenant_id": str(body.tenant_id), "task_id": str(body.job_id), "room": "rig", **provenance,
                          "region": body.home_region, "ledger_id": str(ledger),
                          "provider_job_id": body.provider_job_id, "outcome": body.outcome,
                          "cost_usd": str(row["reserved_usd"]), "cost_basis": "upper_bound"}
                if row["state"] == "complete":
                    if json.loads(row["receipt"]) != result:
                        raise HTTPException(409, "Completed job receipt cannot be replaced.")
                    return result
                if row["state"] != "claimed":
                    raise HTTPException(409, "The reservation must be claimed before completion.")
                await conn.execute("""UPDATE tank.ledger SET outcome=$3
                    WHERE id=$1 AND tenant_id=$2 AND agent='rig' AND vendor='huggingface_jobs'""",
                    ledger, body.tenant_id, body.outcome)
                await conn.execute("""UPDATE tank.agent_receipts SET receipt=$3::jsonb
                    WHERE ledger_id=$1 AND tenant_id=$2 AND room='rig'""", ledger, body.tenant_id, json.dumps(result))
                await conn.execute("""UPDATE tank.rig_forge_jobs SET state='complete',receipt=$3::jsonb
                    WHERE tenant_id=$1 AND id=$2""", body.tenant_id, body.job_id, json.dumps(result))
        return result

    @router.post("/steps")
    async def step(body: Step, request: Request):
        auth(body, request)
        ledger = uuid.uuid5(body.job_id, str(body.tenant_id) + ":" + body.stage)
        result = {"tenant_id": str(body.tenant_id), "task_id": str(body.job_id), "room": "rig",
                  "region": body.home_region, "ledger_id": str(ledger), "stage": body.stage,
                  "outcome": body.outcome, "cost_usd": "0"}
        async with tank.pool().acquire() as conn:
            async with conn.transaction():
                inserted = await conn.fetchval("""INSERT INTO tank.ledger
                    (id,tenant_id,region,agent,task_type,model,cost_usd,outcome,rung,task_id)
                    VALUES ($1,$2,$3,'rig',$4,'code',0,$5,0,$6)
                    ON CONFLICT (id) DO NOTHING RETURNING id""", ledger, body.tenant_id, body.home_region,
                    "rig.forge." + body.stage, body.outcome, body.job_id)
                if inserted:
                    await conn.execute("""INSERT INTO tank.agent_receipts (ledger_id,tenant_id,room,receipt)
                        VALUES ($1,$2,'rig',$3::jsonb)""", ledger, body.tenant_id, json.dumps(result))
                else:
                    prior = await conn.fetchval("""SELECT receipt FROM tank.agent_receipts
                        WHERE tenant_id=$1 AND ledger_id=$2 AND room='rig'""", body.tenant_id, ledger)
                    if prior is None or json.loads(prior) != result:
                        raise HTTPException(409, "Step receipt cannot be replaced.")
        return result

    return router
