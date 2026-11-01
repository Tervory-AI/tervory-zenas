"""Postgres transaction boundary for causal hearing commands.

A row lock serializes commands across processes. A request fingerprint prevents
reusing an idempotency key with different intent; retries return CURRENT state,
not an old privileged projection. No connection is held while an AI call runs.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable
from uuid import NAMESPACE_URL, UUID, uuid5

from . import hearing


@dataclass(frozen=True)
class Mutation:
    state: dict[str, Any]
    replayed: bool


class HearingStore:
    def __init__(self, pool: Any):
        self.pool = pool

    @staticmethod
    def fingerprint(command: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(command, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

    @staticmethod
    def _decode(value: Any) -> dict[str, Any]:
        return json.loads(value) if isinstance(value, str) else value

    async def _row(self, conn: Any, actor: hearing.Actor, hearing_id: str, *, lock: bool = False) -> dict[str, Any]:
        sql = """SELECT state FROM agent_zenas.moot_hearings
                 WHERE tenant_id=$1 AND region=$2 AND id=$3""" + (" FOR UPDATE" if lock else "")
        row = await conn.fetchrow(sql, UUID(actor.tenant_id), actor.region, UUID(hearing_id))
        if row is None:
            raise hearing.HearingError("not_found")
        state = self._decode(row["state"])
        hearing.authorize(state, actor)
        return state

    async def load(self, actor: hearing.Actor, hearing_id: str) -> dict[str, Any]:
        async with self.pool.acquire() as conn:
            return await self._row(conn, actor, hearing_id)

    async def _request(self, conn: Any, actor: hearing.Actor, hearing_id: str, request_id: str, fingerprint: str) -> bool:
        row = await conn.fetchrow("""SELECT fingerprint FROM agent_zenas.moot_hearing_requests
            WHERE tenant_id=$1 AND hearing_id=$2 AND actor_id=$3 AND request_id=$4""",
            UUID(actor.tenant_id), UUID(hearing_id), actor.user_id, UUID(request_id))
        if row is None:
            return False
        if row["fingerprint"] != fingerprint:
            raise hearing.HearingError("idempotency_conflict")
        return True

    async def _record(self, conn: Any, actor: hearing.Actor, state: dict[str, Any], request_id: str,
                      fingerprint: str, kind: str) -> None:
        values = (UUID(actor.tenant_id), UUID(state["id"]), actor.user_id, UUID(request_id))
        await conn.execute("""INSERT INTO agent_zenas.moot_hearing_requests
            (tenant_id,hearing_id,actor_id,request_id,fingerprint,result_revision)
            VALUES($1,$2,$3,$4,$5,$6)""", *values, fingerprint, state["revision"])
        await conn.execute("""INSERT INTO agent_zenas.moot_hearing_events
            (tenant_id,hearing_id,actor_id,request_id,revision,kind)
            VALUES($1,$2,$3,$4,$5,$6)""", *values, state["revision"], kind)

    async def create(self, actor: hearing.Actor, request_id: str, problem: dict[str, Any],
                     seats: dict[str, Any]) -> dict[str, Any]:
        request_id = str(UUID(request_id))
        hearing_id = str(uuid5(NAMESPACE_URL, f"tervory:moot:{actor.tenant_id}:{actor.user_id}:{request_id}"))
        state = hearing.create(hearing_id, actor, problem, seats)
        fingerprint = self.fingerprint({"action": "create", "worked_id": problem["worked_id"], "seats": seats})
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                inserted = await conn.fetchval("""INSERT INTO agent_zenas.moot_hearings(id,tenant_id,region,revision,state)
                    VALUES($1,$2,$3,0,$4::jsonb) ON CONFLICT (id) DO NOTHING RETURNING id""",
                    UUID(hearing_id), UUID(actor.tenant_id), actor.region, json.dumps(state, allow_nan=False))
                saved = await self._row(conn, actor, hearing_id, lock=True)
                if await self._request(conn, actor, hearing_id, request_id, fingerprint):
                    return saved
                if inserted is None:
                    raise hearing.HearingError("idempotency_conflict")
                await self._record(conn, actor, saved, request_id, fingerprint, "create")
                return saved

    async def mutate(self, actor: hearing.Actor, hearing_id: str, request_id: str, revision: int,
                     action: str, payload: dict[str, Any], transition: Callable) -> dict[str, Any]:
        return (await self.commit(actor, hearing_id, request_id, revision, action, payload, transition)).state

    async def commit(self, actor: hearing.Actor, hearing_id: str, request_id: str, revision: int,
                     action: str, payload: dict[str, Any], transition: Callable) -> Mutation:
        """Fresh/replayed is transactional: an AI invocation must only follow fresh start."""
        UUID(request_id)
        fingerprint = self.fingerprint({"action": action, "revision": revision, "payload": payload})
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                state = await self._row(conn, actor, hearing_id, lock=True)
                if await self._request(conn, actor, hearing_id, request_id, fingerprint):
                    return Mutation(state, replayed=True)
                updated = transition(state, actor, revision, payload)
                if updated["revision"] != state["revision"] + 1 or any(
                    updated[key] != state[key] for key in ("id", "tenant_id", "region", "owner_id")
                ):
                    raise hearing.HearingError("invalid_transition")
                result = await conn.fetchval("""UPDATE agent_zenas.moot_hearings
                    SET state=$4::jsonb, revision=$5, updated_at=now()
                    WHERE tenant_id=$1 AND region=$2 AND id=$3 AND revision=$6 RETURNING revision""",
                    UUID(actor.tenant_id), actor.region, UUID(hearing_id), json.dumps(updated, allow_nan=False),
                    updated["revision"], state["revision"])
                if result is None:
                    raise hearing.HearingError("stale_revision")
                if updated["phase"] == "closed" and state["phase"] != "closed":
                    from .hearing_learning import candidates
                    for candidate in candidates(updated):
                        await conn.execute("""INSERT INTO agent_zenas.moot_hearing_learning
                            (tenant_id,hearing_id,turn_number,candidate)
                            VALUES($1,$2,$3,$4::jsonb)""", UUID(actor.tenant_id), UUID(hearing_id),
                            candidate["turn_number"], json.dumps(candidate))
                if updated["published"] and not state["published"]:
                    hearing._operator(state, actor)
                    await self._publish(conn, updated)
                await self._record(conn, actor, updated, request_id, fingerprint, action)
                return Mutation(updated, replayed=False)


    async def _publish(self, conn: Any, state: dict[str, Any]) -> None:
        """Founder-reviewed public copy, same transaction as the publication event.

        The existing public reader is platform-scoped. Copy only explicit public
        fields into that platform scope; the private tenant hearing stays private.
        """
        from .publication import public_rows
        problem, match = public_rows(state)
        platform = UUID("00000000-0000-0000-0000-000000000001")
        await conn.execute("""INSERT INTO agent_zenas.moot_problems(id,tenant_id,region,title,body,locked)
            VALUES($1,$2,$3,$4,$5::jsonb,true)""", problem["id"], platform, state["region"],
            problem["title"], json.dumps(problem["body"]))
        await conn.execute("""INSERT INTO agent_zenas.moot_matches(id,tenant_id,problem_id,region,mode,published,fairness,runs)
            VALUES($1,$2,$3,$4,$5,true,$6::jsonb,$7::jsonb)""", UUID(state["id"]), platform,
            problem["id"], state["region"], match["mode"], json.dumps(match["fairness"]), json.dumps(match["runs"]))
