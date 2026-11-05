"""Founder onboarding records; the US Tank is the home-region directory."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from command import Actor, identity, require_founder
from tenant_db import tenant_scope


class Home(BaseModel):
    model_config = ConfigDict(extra="forbid")
    home_region: Literal["us-east", "za"]


async def recorded_home(tank, tenant_id):
    async with tank.pool().acquire() as conn:
        return await conn.fetchval(
            "SELECT home_region FROM tank.tenants WHERE tenant_id=$1", tenant_id
        )


def chosen_home(tank, saved, supplied):
    if saved is not None:
        if supplied is not None and supplied != saved:
            raise HTTPException(
                409, "The request does not match this tenant's home region."
            )
        return saved
    # Existing tenants retain the legacy region contract until founder onboarding.
    return tank.call_home(supplied, tank.box_region())


async def resolve_home(tank, tenant_id, supplied):
    refusal = tank.home_region_refusal(tank.call_home(supplied, tank.box_region()), tank.box_region())
    if refusal:
        raise HTTPException(403, refusal)
    return chosen_home(tank, await recorded_home(tank, tenant_id), supplied)


async def check_batch_home(tank, requests):
    for request in requests:
        refusal = tank.home_region_refusal(tank.call_home(request.home_region, tank.box_region()), tank.box_region())
        if refusal:
            raise HTTPException(403, refusal)
    saved = await recorded_home(tank, requests[0].tenant_id)
    for request in requests:
        home = chosen_home(tank, saved, request.home_region)
        refusal = tank.home_region_refusal(home, tank.box_region())
        if refusal:
            raise HTTPException(403, refusal)


def build_router(tank):
    router = APIRouter(prefix="/v1/tenants", tags=["tenant-home"])

    @router.get("/{tenant_id}/home", dependencies=[Depends(tank.require_key)])
    async def read_home(tenant_id: uuid.UUID):
        saved = await recorded_home(tank, tenant_id)
        if saved is None:
            raise HTTPException(404, {"code": "tenant_home_missing"})
        return {"tenant_id": str(tenant_id), "home_region": saved}

    @router.put("/{tenant_id}/home")
    async def onboard(
        tenant_id: uuid.UUID, body: Home, actor: Actor = Depends(identity)
    ):
        require_founder(actor)
        if tank.box_region() != "us-east":
            raise HTTPException(409, "Record onboarding on the United States Tank.")
        with tenant_scope(tenant_id, administrative=True):
            async with tank.pool().acquire() as conn, conn.transaction():
                if body.home_region == "za" and not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM tank.tenants WHERE tenant_id=$1)",
                    tenant_id,
                ):
                    existing = await conn.fetchval(
                        "SELECT tank.tenant_has_data($1)",
                        tenant_id,
                    )
                    if existing:
                        raise HTTPException(
                            409, "An existing tenant needs a separate region migration."
                        )
                row = await conn.fetchrow(
                    """INSERT INTO tank.tenants(tenant_id,home_region,onboarded_by)
                VALUES($1,$2,$3) ON CONFLICT(tenant_id) DO UPDATE
                SET home_region=EXCLUDED.home_region
                WHERE tank.tenants.home_region=EXCLUDED.home_region
                RETURNING tenant_id,home_region""",
                    tenant_id,
                    body.home_region,
                    actor.user_id,
                )
        if row is None:
            raise HTTPException(
                409, "Changing an existing home requires a separate migration."
            )
        return {**dict(row), "tenant_id": str(tenant_id)}

    return router
