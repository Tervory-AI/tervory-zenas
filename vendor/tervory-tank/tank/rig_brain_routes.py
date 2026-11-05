"""RIG-2: tenant-scoped receipts for the brain's non-model steps."""
from __future__ import annotations

import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from agent_skills import local_region, room_key
from command import Actor, identity, require_founder
import skala


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    tenant_id: uuid.UUID
    task_id: uuid.UUID
    home_region: Literal["us-east"]
    action: Literal["recall", "skills", "skala_run", "browser", "sandbox", "remember"]
    status: Literal["done", "failed"]
    # No prompts, URLs, credentials, or arbitrary caller-supplied receipt fields.
    count: int | None = Field(default=None, ge=0, le=100000)


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: uuid.UUID
    task_id: uuid.UUID
    home_region: Literal["us-east"]
    version: str = Field(pattern=r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
    input: dict


def build_router(tank) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/agents/rig/price-history")
    async def price_history(response: Response, tenant_id: uuid.UUID,
                            product_id: str = Query(min_length=1, max_length=200),
                            offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000),
                            actor: Actor = Depends(identity)):
        require_founder(actor)
        response.headers["Cache-Control"] = "private, no-store"
        async with tank.pool().acquire() as conn:
            rows = await conn.fetch("""SELECT price,currency,observed_at,event_id
                FROM agent_rig.price_history WHERE tenant_id=$1 AND product_id=$2
                ORDER BY observed_at DESC,event_id OFFSET $3 LIMIT $4""", tenant_id, product_id, offset, limit + 1)
        return {"tenant_id": str(tenant_id), "product_id": product_id,
                "history": [{**dict(row), "price": str(row["price"])} for row in rows[:limit]],
                "has_more": len(rows) > limit}

    @router.post("/v1/agents/rig/skills/{skill_id}/run")
    async def run_skill(skill_id: str, body: Skill, request: Request):
        room_key("rig", request.headers.get("authorization", ""), request.headers.get("x-tank-key", ""))
        local_region(tank, body.home_region)
        package = tank.get_package(skill_id, body.version)
        if package.agent != "rig" or not package.id.startswith("rig."):
            raise HTTPException(403, "Only Rig skills may run in this room.")
        if await tank.skill_state(skill_id, body.version) != "approved":
            raise HTTPException(409, "This skill is not approved.")
        # Use Skala's existing runner and Tank context, with the brain task ID
        # so any model calls made by the skill retain the same receipt linkage.
        ctx = tank.make_ctx(package, body.tenant_id, preview=False)
        ctx.agent, ctx.task_id = "rig", body.task_id
        result = await skala.run_skill(package, body.input, ctx)
        result["_cost_usd"] = round(ctx.cost_usd, 6)
        return result

    @router.post("/v1/agents/rig/steps")
    async def step(body: Step, request: Request):
        room_key("rig", request.headers.get("authorization", ""), request.headers.get("x-tank-key", ""))
        region = local_region(tank, body.home_region)
        receipt = {**body.model_dump(mode="json", exclude={"id", "home_region"}),
                   "ledger_id": str(body.id), "room": "rig", "region": region, "cost_usd": 0}
        async with tank.pool().acquire() as conn:
            async with conn.transaction():
                # Replayed delivery is safe; a reused ID with different contents is not.
                inserted = await conn.fetchval("""INSERT INTO tank.ledger
                    (id,tenant_id,region,agent,task_type,rung,model,priority,cost_usd,outcome,task_id)
                    VALUES ($1,$2,$3,'rig',$4,0,'code','standard',0,$5,$6)
                    ON CONFLICT (id) DO NOTHING RETURNING id""",
                    body.id, body.tenant_id, region, "rig.brain." + body.action,
                    "delivered" if body.status == "done" else "failed", body.task_id)
                if inserted:
                    await conn.execute("""INSERT INTO tank.agent_receipts (ledger_id,tenant_id,room,receipt)
                        VALUES ($1,$2,'rig',$3::jsonb)""", body.id, body.tenant_id, json.dumps(receipt))
                else:
                    previous = await conn.fetchval("""SELECT receipt FROM tank.agent_receipts
                        WHERE ledger_id=$1 AND tenant_id=$2 AND room='rig'""", body.id, body.tenant_id)
                    if isinstance(previous, str):
                        previous = json.loads(previous)
                    if previous != receipt:
                        raise HTTPException(409, "Step id is already in use.")
        return receipt

    return router
