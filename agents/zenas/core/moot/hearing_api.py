"""Trusted internal hearing routes. Identity is forwarded by Tank, not JSON."""
from __future__ import annotations

import hmac
import os
from typing import Any, Literal
from uuid import UUID

from fastapi import Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import hearing
from .hearing_ai import AIExecutor, validate_turn
from .hearing_sources import edited_problem, worked_problem
from .hearing_store import HearingStore


class CreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    home_region: Literal["us-east", "za"] | None = None
    request_id: UUID
    worked_id: UUID
    seats: dict[str, dict[str, Any]]


class CommandIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    home_region: Literal["us-east", "za"] | None = None
    request_id: UUID
    revision: int = Field(ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


def register_routes(app, pool, tenant_store, box_region, tank):
    async def identity(home: str, key: str, tenant: str, user: str, founder: str) -> hearing.Actor:
        expected = os.environ.get("TANK_KEY", "")
        if not expected or not key or not hmac.compare_digest(key.encode(), expected.encode()):
            raise HTTPException(401, "room_key_required")
        if not user or len(user) > 200 or founder not in ("0", "1"):
            raise HTTPException(401, "actor_required")
        try:
            tenant = str(UUID(tenant))
        except ValueError:
            raise HTTPException(401, "actor_required") from None
        region = box_region()
        from agents.zenas.core.service import _use_home
        _, refusal = await _use_home(tenant_store(), tenant_id=tenant, home_region=home, box_region=region)
        if refusal:
            raise HTTPException(403, "wrong_region")
        return hearing.Actor(tenant, user, region, founder == "1")

    def error(exc):
        code = exc.code if isinstance(exc, hearing.HearingError) else "invalid_request"
        status = 404 if code == "not_found" else 403 if code in ("operator_required", "seat_not_owned") else 409 if code in (
            "stale_revision", "idempotency_conflict", "ai_turn_pending", "ai_claim_invalid") else 422
        raise HTTPException(status, code) from None

    def present(state, actor, seat=None):
        projection = hearing.view(state, actor, seat)
        projection["pending_ai"] = ({"seat": state["pending_ai"]["seat"], "status": state["pending_ai"]["status"]}
            if state["pending_ai"] else None)
        projection["last_ai_resolution"] = state["ai_attempts"][-1].get("resolution") if state["ai_attempts"] else None
        return {"hearing": projection, "operator": actor.founder and actor.user_id == state["owner_id"]}

    @app.get("/moot/hearing-sources")
    async def sources(home_region: str, x_tank_key: str = Header(default=""),
        x_moot_tenant: str = Header(default=""), x_moot_actor: str = Header(default=""), x_moot_founder: str = Header(default="")):
        actor = await identity(home_region, x_tank_key, x_moot_tenant, x_moot_actor, x_moot_founder)
        if not actor.founder:
            raise HTTPException(404, "not_found")
        # Only identifiers/titles, no outcomes, source bodies or private records.
        sql = """SELECT w.id,d.title FROM agent_zenas.worked_matters w
            JOIN zenas_pool.documents d ON d.document_id=w.pool_doc_id AND d.version_id=w.pool_version_id
            JOIN zenas_pool.sources s ON s.source_id=d.source_id
            WHERE w.region=$1 AND (w.tenant_id=$2 OR w.tenant_id IS NULL)
            AND w.source='case' AND w.material_type='public_record' AND w.holdout=false
            AND s.region=$1 AND s.bulk_use='allowed' AND d.kind='case' AND d.status <> 'superseded'
            ORDER BY w.created_at DESC LIMIT 100"""
        async with pool().acquire() as conn:
            rows = await conn.fetch(sql, actor.region, UUID(actor.tenant_id))
        return {"sources": [{"id": str(r["id"]), "title": r["title"]} for r in rows]}

    @app.get("/moot/hearings")
    async def listing(home_region: str, x_tank_key: str = Header(default=""),
        x_moot_tenant: str = Header(default=""), x_moot_actor: str = Header(default=""), x_moot_founder: str = Header(default="")):
        actor = await identity(home_region, x_tank_key, x_moot_tenant, x_moot_actor, x_moot_founder)
        async with pool().acquire() as conn:
            rows = await conn.fetch("""SELECT state FROM agent_zenas.moot_hearings
                WHERE tenant_id=$1 AND region=$2 AND (state->>'owner_id'=$3
                OR state->'seats'->'applicant'->>'user_id'=$3
                OR state->'seats'->'respondent'->>'user_id'=$3
                OR state->'seats'->'judge'->>'user_id'=$3)
                ORDER BY updated_at DESC LIMIT 100""", UUID(actor.tenant_id), actor.region, actor.user_id)
        return {"hearings": [{"id": s["id"], "title": s["problem"]["title"], "phase": s["phase"],
            "revision": s["revision"]} for s in (HearingStore._decode(r["state"]) for r in rows)]}

    @app.post("/moot/hearings", status_code=201)
    async def create(body: CreateIn, home_region: str, x_tank_key: str = Header(default=""),
        x_moot_tenant: str = Header(default=""), x_moot_actor: str = Header(default=""), x_moot_founder: str = Header(default="")):
        if body.home_region is not None and body.home_region != home_region:
            raise HTTPException(403, "wrong_region")
        actor = await identity(home_region, x_tank_key, x_moot_tenant, x_moot_actor, x_moot_founder)
        if not actor.founder:
            raise HTTPException(403, "operator_required")
        try:
            problem = await worked_problem(pool(), actor, str(body.worked_id))
            state = await HearingStore(pool()).create(actor, str(body.request_id), problem, body.seats)
            return present(state, actor)
        except (hearing.HearingError, ValueError) as exc:
            error(exc)

    @app.get("/moot/hearings/{hearing_id}")
    async def read(hearing_id: UUID, home_region: str, seat: Literal["applicant", "respondent", "judge"] | None = Query(default=None),
        x_tank_key: str = Header(default=""), x_moot_tenant: str = Header(default=""),
        x_moot_actor: str = Header(default=""), x_moot_founder: str = Header(default="")):
        actor = await identity(home_region, x_tank_key, x_moot_tenant, x_moot_actor, x_moot_founder)
        try:
            state = await HearingStore(pool()).load(actor, str(hearing_id))
            return present(state, actor, seat)
        except hearing.HearingError as exc:
            error(exc)

    @app.post("/moot/hearings/{hearing_id}/{action}")
    async def command(hearing_id: UUID, action: Literal["edit", "lock", "start", "handover", "issue", "turn", "ai", "abandon", "publish"],
        body: CommandIn, home_region: str, seat: Literal["applicant", "respondent", "judge"] | None = Query(default=None), x_tank_key: str = Header(default=""), x_moot_tenant: str = Header(default=""),
        x_moot_actor: str = Header(default=""), x_moot_founder: str = Header(default="")):
        actor = await identity(home_region, x_tank_key, x_moot_tenant, x_moot_actor, x_moot_founder)
        if body.home_region is not None and body.home_region != home_region:
            raise HTTPException(403, "wrong_region")
        store = HearingStore(pool())
        try:
            state = await store.load(actor, str(hearing_id))
            if action == "ai":
                # Controller configuration is the founder's durable delegation.
                # Participants can advance a configured AI seat, never change its role.
                delegated = hearing.Actor(actor.tenant_id, state["owner_id"], actor.region, True)
                state = await AIExecutor(store, tank()).run(delegated, str(hearing_id), str(body.request_id), body.revision)
            elif action == "edit":
                hearing._operator(state, actor)
                problem = await edited_problem(pool(), actor, state["problem"], body.payload.get("changes"))
                state = await store.mutate(actor, str(hearing_id), str(body.request_id), body.revision, action, body.payload,
                    lambda s, a, r, _p: hearing.change(s, a, r, "edit", {"problem": problem}))
            elif action == "turn":
                def human_turn(s, a, r, p):
                    hearing._begin(s, a, r)
                    return hearing.turn(s, a, r, validate_turn(s, p))
                state = await store.mutate(actor, str(hearing_id), str(body.request_id), body.revision, action, body.payload, human_turn)
            elif action == "abandon":
                state = await store.mutate(actor, str(hearing_id), str(body.request_id), body.revision, action, body.payload,
                    lambda s, a, r, p: hearing.abandon_ai(s, a, r, s["pending_ai"]["token"] if s["pending_ai"] else ""))
            else:
                state = await store.mutate(actor, str(hearing_id), str(body.request_id), body.revision, action, body.payload,
                    lambda s, a, r, p: hearing.change(s, a, r, action, p))
            return present(state, actor, seat)
        except (hearing.HearingError, ValueError) as exc:
            error(exc)
