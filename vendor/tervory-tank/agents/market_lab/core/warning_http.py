"""Room endpoints for warning reads and reactions; identity comes from Tank."""

from uuid import UUID
from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from core import warning_bridge, warning_store
from core.ledger import PostgresLedger

from core.warnings import run, listing


class WarningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    warning_id: UUID | None = None
    event_id: UUID | None = None
    useful: StrictBool | None = None
    actor: str = Field(default="", max_length=200)


async def refresh(ledger: PostgresLedger, tenant: str, engine: str) -> dict:
    problem = None
    try:
        stored = await warning_bridge.call("series", {"tenant_id": tenant})
        imported = stored["series"]
        if stored.get("problem"):
            problem = stored["problem"]
    except RuntimeError:
        imported = []
        problem = "Saved product observations could not be read. Existing warnings keep their saved evidence; new warnings wait for those observations."
    from chassis.db import ensure_schedules
    from core.jobs import load_engine

    await ensure_schedules(
        ledger.conn,
        "agent_market_lab",
        [
            {**schedule, "tenant_id": tenant}
            for schedule in load_engine(engine).schedules()
        ],
    )
    result = await run(ledger, tenant, engine, imported=imported)
    delivered = await warning_bridge.deliver(ledger, tenant)
    return {**result, "training_pending": not delivered, "problem": problem}


def build_router(
    *, key_ok: Callable[[str], bool], ledger_context: Any, engine: str
) -> APIRouter:
    router = APIRouter()

    @router.post("/market/warnings/{operation}")
    async def call(
        operation: str, body: WarningRequest, x_tank_key: str = Header(default="")
    ):
        if not key_ok(x_tank_key):
            raise HTTPException(401, "Room key required")
        tenant = str(body.tenant_id)
        async with ledger_context() as ledger:
            if operation == "list":
                return await refresh(ledger, tenant, engine)
            if operation == "pair":
                if body.event_id is None:
                    raise HTTPException(422, "An event is required")
                row = await ledger.conn.fetchrow(
                    "SELECT * FROM agent_market_lab.warning_training WHERE tenant_id=$1 AND id=$2",
                    body.tenant_id,
                    body.event_id,
                )
                if row is None:
                    raise HTTPException(404, "Event not found")
                return warning_store.row_dict(row)
            if operation == "feedback":
                if (
                    body.warning_id is None
                    or body.event_id is None
                    or body.useful is None
                    or not body.actor.strip()
                ):
                    raise HTTPException(422, "A warning and a choice are required")
                async with ledger.conn.transaction():
                    await ledger.conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                        tenant + ":warnings",
                    )
                    row = await ledger.conn.fetchrow(
                        "SELECT * FROM agent_market_lab.warnings WHERE tenant_id=$1 AND id=$2",
                        body.tenant_id,
                        body.warning_id,
                    )
                    if row is None:
                        raise HTTPException(404, "Warning not found")
                    existing = await ledger.conn.fetchrow(
                        "SELECT warning_id,output,actor FROM agent_market_lab.warning_training WHERE tenant_id=$1 AND id=$2",
                        body.tenant_id,
                        body.event_id,
                    )
                    if existing and (
                        existing["warning_id"] != body.warning_id
                        or existing["actor"] != body.actor
                        or warning_store.decoded(existing["output"])
                        != {"useful": body.useful}
                    ):
                        raise HTTPException(409, "That choice has already been saved")
                    await warning_store.pair_event(
                        ledger,
                        tenant,
                        warning_store.row_dict(row),
                        "feedback",
                        {"useful": body.useful},
                        actor=body.actor,
                        ident=str(body.event_id),
                    )
                delivered = await warning_bridge.deliver(ledger, tenant)
                return {
                    "saved": True,
                    "useful": body.useful,
                    "sync_pending": not delivered,
                }
            if operation == "read":
                return await listing(ledger, tenant)
        raise HTTPException(404, "Not found")

    return router
