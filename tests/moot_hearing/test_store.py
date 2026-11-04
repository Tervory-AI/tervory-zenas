"""Real Postgres CAS tests in a uniquely named, disposable database only.

Hosted CI supplies pooltest on loopback. Local execution requires an explicit
MOOT_TEST_DATABASE_URL ending in _test; production URLs are always refused.
"""
import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from agents.zenas.core.moot.hearing import Actor, HearingError, change, turn, view
from agents.zenas.core.moot.hearing_store import HearingStore
from .test_hearing import hearing, problem, HUMANS, OWNER




def transition(action):
    return lambda s, actor, revision, payload: change(s, actor, revision, action, payload)


async def new(store):
    seats = hearing()["seats"]
    s = await store.create(OWNER, str(uuid4()), problem(), seats)
    for action in ("lock", "start"):
        s = await store.mutate(OWNER, s["id"], str(uuid4()), s["revision"], action, {}, transition(action))
    return s


@pytest.mark.asyncio
async def test_duplicate_human_turn_survives_restart_and_only_one_event(pg_store):
    s = await new(pg_store)
    req = str(uuid4())
    args = (HUMANS["applicant"], s["id"], req, s["revision"], "turn", {"kind": "submission", "text": "Actual human argument"})
    fn = lambda state, actor, rev, payload: turn(state, actor, rev, payload)
    first, retry = await asyncio.gather(pg_store.mutate(*args, fn), HearingStore(pg_store.pool).mutate(*args, fn))
    assert first == retry
    saved = await HearingStore(pg_store.pool).load(HUMANS["applicant"], s["id"])
    assert len(saved["transcript"]) == 1
    async with pg_store.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agent_zenas.moot_hearing_events WHERE hearing_id=$1 AND kind='turn'", UUID(s["id"])) == 1
    with pytest.raises(HearingError, match="idempotency_conflict"):
        await pg_store.mutate(*args[:-1], {"kind": "submission", "text": "changed retry"}, fn)


@pytest.mark.asyncio
async def test_handover_and_turn_compete_one_revision_wins(pg_store):
    s = await new(pg_store)
    commands = [
        pg_store.mutate(OWNER, s["id"], str(uuid4()), s["revision"], "handover", {"seat": "applicant", "controller": {"kind": "ai"}}, transition("handover")),
        pg_store.mutate(HUMANS["applicant"], s["id"], str(uuid4()), s["revision"], "turn", {"kind": "submission", "text": "student"}, lambda s, a, r, p: turn(s, a, r, p)),
    ]
    results = await asyncio.gather(*commands, return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, HearingError) for r in results) == 1
    saved = await pg_store.load(OWNER, s["id"])
    assert saved["revision"] == s["revision"] + 1


@pytest.mark.asyncio
async def test_replay_projects_current_roles_never_old_judge_authority(pg_store):
    # A founder create retry returns current state, not a saved initial controller map.
    create_req = str(uuid4()); p = problem(); seats = hearing()["seats"]
    s = await pg_store.create(OWNER, create_req, p, seats)
    s = await pg_store.mutate(OWNER, s["id"], str(uuid4()), s["revision"], "handover", {"seat": "judge", "controller": {"kind": "ai"}}, transition("handover"))
    retry = await pg_store.create(OWNER, create_req, p, seats)
    assert retry["revision"] == s["revision"] and retry["seats"]["judge"]["kind"] == "ai"
    with pytest.raises(HearingError, match="not_found"):
        await pg_store.load(HUMANS["judge"], s["id"])
    other = Actor(str(uuid4()), OWNER.user_id, "us-east", True)
    with pytest.raises(HearingError, match="not_found"):
        await pg_store.load(other, s["id"])
    assert "never counsel" not in json.dumps(view(retry, HUMANS["applicant"], "applicant"))


def test_schema_is_additive_and_in_room_startup_schema():
    sql = Path("agents/zenas/core/moot/hearing_schema.sql").read_text()
    assert sql in Path("agents/zenas/schema.sql").read_text()
    assert "DROP " not in sql and "CREATE TABLE IF NOT EXISTS" in sql


@pytest.mark.asyncio
async def test_founder_publish_roundtrip_uses_existing_public_reader_without_private_state(pg_store):
    from agents.zenas.core.moot.published import published_matches
    s = await new(pg_store)
    # Commit an entire genuine two-input/human-judge hearing; only storage is disposable.
    for seat, kind, extra in [("applicant", "submission", {}), ("judge", "question", {"target": "respondent"}),
        ("respondent", "submission", {}), ("judge", "ruling", {"order": "Independent order", "reasons": ["Independent reason"], "authority_ids": ["case-1"]})]:
        s = await pg_store.mutate(HUMANS[seat], s["id"], str(uuid4()), s["revision"], "turn", {"kind": kind, "text": seat + " words", **extra},
            lambda s, a, r, p: turn(s, a, r, p))
    with pytest.raises(HearingError):
        await pg_store.mutate(HUMANS["judge"], s["id"], str(uuid4()), s["revision"], "publish", {}, transition("publish"))
    request = str(uuid4()); revision = s["revision"]
    s = await pg_store.mutate(OWNER, s["id"], request, revision, "publish", {}, transition("publish"))
    replay = await pg_store.mutate(OWNER, s["id"], request, revision, "publish", {}, transition("publish"))
    assert replay["published"] and replay["revision"] == s["revision"]
    rows = await published_matches(pg_store.pool, UUID("00000000-0000-0000-0000-000000000001"), "us-east", UUID(s["id"]))
    assert len(rows) == 1 and rows[0]["published"] is True
    assert rows[0]["judgmentOrder"] == "Independent order"
    assert "never counsel" not in json.dumps(rows) and "actor_id" not in json.dumps(rows)
    assert rows[0]["reveal"] == "real holding hidden until judgment"


@pytest.mark.asyncio
async def test_closed_reasoning_becomes_pending_review_never_automatic_gold(pg_store):
    from .test_reasoning import trace
    s = await new(pg_store)
    for seat, kind, extra in [("applicant", "submission", {}), ("judge", "question", {"target": "respondent"}),
        ("respondent", "submission", {}), ("judge", "ruling", {"order": "Independent order", "reasons": ["Independent reason"], "authority_ids": ["case-1"]})]:
        s = await pg_store.mutate(HUMANS[seat], s["id"], str(uuid4()), s["revision"], "turn", {"kind": kind, "text": seat + " words", "reasoning": trace(), **extra},
            lambda s, a, r, p: turn(s, a, r, p))
    async with pg_store.pool.acquire() as conn:
        rows = await conn.fetch("SELECT status,candidate,reviewed_by FROM agent_zenas.moot_hearing_learning WHERE tenant_id=$1 AND hearing_id=$2",
            UUID(OWNER.tenant_id), UUID(s["id"]))
    assert len(rows) == 4 and all(r["status"] == "pending_review" and r["reviewed_by"] is None for r in rows)
    assert all(json.loads(r["candidate"])["verdict"] is None for r in rows)
    assert "never counsel" not in str(rows) and "real holding hidden" not in str(rows)


@pytest.mark.asyncio
async def test_pair_creation_is_atomic_retryable_and_region_tenant_private(pg_store):
    seats = {seat: {"kind": "ai"} for seat in HUMANS}
    s = await pg_store.create(OWNER, str(uuid4()), problem(), seats)
    s = await pg_store.mutate(OWNER, s["id"], str(uuid4()), 0, "lock", {}, transition("lock"))
    request = str(uuid4()); revision = s["revision"]
    first, retry = await asyncio.gather(pg_store.create_pair(OWNER, s["id"], request, revision), pg_store.create_pair(OWNER, s["id"], request, revision))
    assert first == retry and first["fairness_pair_id"]
    report = await pg_store.fairness(OWNER, s["id"])
    assert report["status"] == "incomplete" and report["first_hearing"] != report["second_hearing"]
    second = await pg_store.load(OWNER, report["second_hearing"])
    assert second["floor"] == "respondent" and second["problem"] == first["problem"]
    async with pg_store.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agent_zenas.moot_hearings") == 2
        assert await conn.fetchval("SELECT count(*) FROM agent_zenas.moot_hearing_pairs") == 1


async def complete_ai(store, s, decision, *, stop_before_ruling=False):
    from .test_reasoning import trace
    from agents.zenas.core.moot.hearing import claim_ai, mark_ai_started
    while s["phase"] != "closed":
        if stop_before_ruling and s["floor"] == "judge" and {t["seat"] for t in s["transcript"] if t["kind"] == "submission"} == {"applicant", "respondent"}:
            return s
        if s["seats"][s["floor"]]["kind"] == "human":
            target = "respondent" if any(t["seat"] == "applicant" for t in s["transcript"]) else "applicant"
            s = await store.mutate(HUMANS["judge"], s["id"], str(uuid4()), s["revision"], "turn", {"kind": "direction", "text": "Please address the issue", "target": target},
                lambda s, a, r, p: turn(s, a, r, p))
            continue
        token = str(uuid4())
        s = await store.mutate(OWNER, s["id"], str(uuid4()), s["revision"], "claim", {"token": token},
            lambda s, a, r, p: claim_ai(s, a, r, p["token"]))
        s = await store.mutate(OWNER, s["id"], str(uuid4()), s["revision"], "start_ai", {"token": token},
            lambda s, a, r, p: mark_ai_started(s, a, r, p["token"]))
        heard = {t["seat"] for t in s["transcript"] if t["kind"] == "submission"}
        payload = {"text": "Offline independently supplied reasoning", "reasoning": trace(), "authority_ids": ["case-1"]}
        if s["floor"] != "judge": payload["kind"] = "submission"
        elif heard == {"applicant", "respondent"}: payload.update(kind="ruling", order="Offline independent order", reasons=["Independent source reasoning"], decision=decision)
        else: payload.update(kind="question", target="respondent" if "applicant" in heard else "applicant")
        receipt = {"ledger_id": str(uuid4()), "cost_usd": 0.002, "model": "offline-only-double", "settings_hash": "a" * 64}
        s = await store.mutate(OWNER, s["id"], str(uuid4()), s["revision"], "ai_turn", {"payload": payload, "token": token, "receipt": receipt},
            lambda s, a, r, p: turn(s, a, r, p["payload"], ai_token=p["token"], receipt=p["receipt"]))
    return s


@pytest.mark.asyncio
async def test_paired_publish_is_atomic_and_opposite_requests_do_not_deadlock(pg_store):
    from agents.zenas.core.moot.published import published_matches
    s = await pg_store.create(OWNER, str(uuid4()), problem(), {seat: {"kind": "ai"} for seat in HUMANS})
    s = await pg_store.mutate(OWNER, s["id"], str(uuid4()), 0, "lock", {}, transition("lock"))
    s = await pg_store.create_pair(OWNER, s["id"], str(uuid4()), s["revision"], start=True)
    report = await pg_store.fairness(OWNER, s["id"])
    other = await pg_store.load(OWNER, report["second_hearing"])
    first = await complete_ai(pg_store, s, "applicant")
    with pytest.raises(HearingError, match="paired_hearing_incomplete"):
        await pg_store.mutate(OWNER, first["id"], str(uuid4()), first["revision"], "publish", {}, transition("publish"))
    assert not (await pg_store.load(OWNER, first["id"]))["published"]
    other = await pg_store.create_pair(OWNER, other["id"], str(uuid4()), other["revision"], start=True)
    second = await complete_ai(pg_store, other, "respondent")
    results = await asyncio.wait_for(asyncio.gather(*[
        pg_store.mutate(OWNER, state["id"], str(uuid4()), state["revision"], "publish", {}, transition("publish"))
        for state in (first, second)], return_exceptions=True), timeout=5)
    assert any(isinstance(r, dict) for r in results), results
    assert all(isinstance(r, (dict, HearingError)) for r in results)
    async with pg_store.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agent_zenas.moot_matches WHERE published") == 2
        assert await conn.fetchval("SELECT count(*) FROM agent_zenas.moot_hearings WHERE (state->>'published')::boolean") == 2
    rows = await published_matches(pg_store.pool, UUID("00000000-0000-0000-0000-000000000001"), "us-east")
    assert len(rows) == 2 and all(row["fairnessReading"] == "The same AI seat prevailed in both runs." for row in rows)
    assert "student" not in str(rows) and "never counsel" not in str(rows)
