from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from agents.zenas.core.moot.hearing_api import register_routes
from agents.zenas.core.moot.hearing_store import HearingStore
from .test_hearing import OWNER, HUMANS, problem
from .test_reasoning import trace


def application(monkeypatch, store):
    monkeypatch.setenv("TANK_KEY", "disposable-room-test-key")
    app = FastAPI()
    class Tenants:
        async def get_tenant(self, tenant):
            return {"tenant_id": tenant, "home_region": "us-east"} if tenant == OWNER.tenant_id else None
    register_routes(app, lambda: store.pool, Tenants, lambda: "us-east", lambda: None)
    return app


def headers(actor=OWNER):
    return {"X-Tank-Key": "disposable-room-test-key", "X-Moot-Tenant": actor.tenant_id,
        "X-Moot-Actor": actor.user_id, "X-Moot-Founder": "1" if actor.founder else "0"}


@pytest.mark.asyncio
async def test_room_identity_is_key_bound_tenant_scoped_and_never_json_asserted(monkeypatch, pg_store):
    app = application(monkeypatch, pg_store)
    seats = {seat: {"kind": "human", "user_id": actor.user_id} for seat, actor in HUMANS.items()}
    state = await pg_store.create(OWNER, str(uuid4()), problem(), seats)
    path = f"/moot/hearings/{state['id']}?home_region=us-east&seat=applicant"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(path, headers=headers(HUMANS["applicant"]))).status_code == 200
        assert (await client.get(path, headers={**headers(), "X-Tank-Key": "forged"})).status_code == 401
        assert (await client.get(path, headers={**headers(), "X-Moot-Actor": ""})).status_code == 401
        assert (await client.get(path.replace("us-east", "za"), headers=headers())).status_code == 403
        assert (await client.get(path, headers={**headers(), "X-Moot-Tenant": str(uuid4())})).status_code == 403
        detail = await client.get(path.replace("seat=applicant", "seat=judge"), headers=headers(HUMANS["applicant"]))
        assert detail.status_code == 403
        forged = await client.post(f"/moot/hearings/{state['id']}/lock?home_region=us-east", headers=headers(HUMANS["applicant"]),
            json={"request_id": str(uuid4()), "revision": 0, "payload": {}, "founder": True})
        assert forged.status_code == 422


@pytest.mark.asyncio
async def test_actual_room_turn_retry_is_idempotent_and_privacy_is_projected(monkeypatch, pg_store):
    app = application(monkeypatch, pg_store)
    seats = {seat: {"kind": "human", "user_id": actor.user_id} for seat, actor in HUMANS.items()}
    state = await pg_store.create(OWNER, str(uuid4()), problem(), seats)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for action in ("lock", "start"):
            res = await client.post(f"/moot/hearings/{state['id']}/{action}?home_region=us-east", headers=headers(),
                json={"request_id": str(uuid4()), "revision": state["revision"], "payload": {}})
            assert res.status_code == 200
            state = res.json()["hearing"]
        body = {"request_id": str(uuid4()), "revision": state["revision"], "payload": {
            "kind": "submission", "text": "Supported student submission", "reasoning": trace(), "authority_ids": ["case-1"]}}
        path = f"/moot/hearings/{state['id']}/turn?home_region=us-east"
        first = await client.post(path, headers=headers(HUMANS["applicant"]), json=body)
        repeat = await client.post(path, headers=headers(HUMANS["applicant"]), json=body)
        assert first.status_code == repeat.status_code == 200
        assert len(repeat.json()["hearing"]["transcript"]) == 1
        assert "never counsel" not in first.text and "actor_id" not in first.text
        saved = await HearingStore(pg_store.pool).load(OWNER, state["id"])
        assert saved["transcript"][0]["reasoning"]["opposing_argument"]


@pytest.mark.asyncio
async def test_ai_counsel_start_creates_swapped_pair_atomically(monkeypatch, pg_store):
    app = application(monkeypatch, pg_store)
    seats = {seat: {"kind": "ai"} for seat in HUMANS}
    state = await pg_store.create(OWNER, str(uuid4()), problem(), seats)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        lock = await client.post(f"/moot/hearings/{state['id']}/lock?home_region=us-east", headers=headers(),
            json={"request_id": str(uuid4()), "revision": 0, "payload": {}})
        assert lock.status_code == 200
        body = {"request_id": str(uuid4()), "revision": 1, "payload": {}}
        path = f"/moot/hearings/{state['id']}/start?home_region=us-east"
        first = await client.post(path, headers=headers(), json=body)
        repeat = await client.post(path, headers=headers(), json=body)
        assert first.status_code == repeat.status_code == 200
        assert first.json()["hearing"]["phase"] == "hearing"
        result = await client.get(f"/moot/hearings/{state['id']}/fairness?home_region=us-east", headers=headers())
        report = result.json()["fairness"]
        assert report["status"] == "incomplete"
        secondary = await pg_store.load(OWNER, report["second_hearing"])
        assert secondary["phase"] == "locked" and secondary["floor"] == "respondent"


def transported(path, body):
    import json
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(["node", "tests/moot_hearing/transport.cjs"], cwd=root,
        input=json.dumps({"path": path, "body": body}), text=True, capture_output=True, timeout=15, check=True)
    captured = json.loads(result.stdout)
    return captured["url"].replace("http://offline-tank/v1/zenas", ""), captured["body"]


@pytest.mark.asyncio
async def test_actual_workspace_transport_accepts_create_commands_and_preserves_region_guard(monkeypatch, pg_store):
    from agents.zenas.core.moot import hearing_api
    async def source(*_args):
        return problem()
    monkeypatch.setattr(hearing_api, "worked_problem", source)
    app = application(monkeypatch, pg_store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        path, body = transported("moot/hearings?home_region=us-east", {"request_id": str(uuid4()),
            "worked_id": str(uuid4()), "seats": {s: {"kind": "human", "user_id": a.user_id} for s, a in HUMANS.items()}})
        assert body["home_region"] == "us-east"
        response = await client.post(path, headers=headers(), json=body)
        assert response.status_code == 201, response.text
        state = response.json()["hearing"]
        for action in ("lock", "start", "turn"):
            payload = {"kind": "submission", "text": "Source supported argument", "reasoning": trace(), "authority_ids": ["case-1"]} if action == "turn" else {}
            path, body = transported(f"moot/hearings/{state['id']}/{action}?home_region=us-east",
                {"request_id": str(uuid4()), "revision": state["revision"], "payload": payload})
            if action == "lock":
                wrong = await client.post(path, headers=headers(), json={**body, "home_region": "za"})
                assert wrong.status_code == 403
            response = await client.post(path, headers=headers(HUMANS["applicant"] if action == "turn" else OWNER), json=body)
            assert response.status_code == 200, response.text
            state = response.json()["hearing"]
        assert len(state["transcript"]) == 1


@pytest.mark.asyncio
async def test_paired_human_judge_reveal_waits_for_both_durable_closures_on_reads_and_retries(monkeypatch, pg_store):
    from agents.zenas.core.moot import hearing
    from .test_store import complete_ai, transition
    app = application(monkeypatch, pg_store)
    seats = {seat: {"kind": "ai"} for seat in HUMANS}
    seats["judge"] = {"kind": "human", "user_id": HUMANS["judge"].user_id}
    first = await pg_store.create(OWNER, str(uuid4()), problem(), seats)
    first = await pg_store.mutate(OWNER, first["id"], str(uuid4()), 0, "lock", {}, transition("lock"))
    start = {"request_id": str(uuid4()), "revision": first["revision"], "payload": {}}
    first = await pg_store.create_pair(OWNER, first["id"], start["request_id"], start["revision"], start=True)
    report = await pg_store.fairness(OWNER, first["id"])
    second = await pg_store.load(OWNER, report["second_hearing"])
    holding = first["problem"]["real_holding"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for index, state in enumerate((first, second)):
            if index:
                state = await pg_store.create_pair(OWNER, state["id"], str(uuid4()), state["revision"], start=True)
            state = await complete_ai(pg_store, state, "uncertain", stop_before_ruling=True)
            body = {"request_id": str(uuid4()), "revision": state["revision"], "payload": {
                "kind": "ruling", "text": "Independent reasoned judgment", "order": "Independent order", "reasons": ["Source reasoning"],
                "authority_ids": ["case-1"], "reasoning": trace(), "decision": "uncertain"}}
            path = f"/moot/hearings/{state['id']}/turn?home_region=us-east&seat=judge"
            for _ in range(2):
                response = await client.post(path, headers=headers(HUMANS["judge"]), json=body)
                assert response.status_code == 200, response.text
                assert (holding in response.text) is bool(index)
            forged = await client.post(path, headers=headers(HUMANS["judge"]), json={**body, "pair_complete": True})
            assert forged.status_code == 422
            for ident in (first["id"], second["id"]):
                for actor, role in ((HUMANS["judge"], "judge"), (OWNER, "judge"), (OWNER, "applicant")):
                    response = await client.get(f"/moot/hearings/{ident}?home_region=us-east&seat={role}", headers=headers(actor))
                    assert response.status_code == 200
                    assert (holding in response.text) is bool(index)
            retry = await client.post(f"/moot/hearings/{first['id']}/start?home_region=us-east", headers=headers(), json=start)
            assert retry.status_code == 200 and (holding in retry.text) is bool(index)
        # A pure/domain projection has no proof of partner completion and fails closed.
        closed = await pg_store.load(OWNER, first["id"])
        assert "reveal" not in hearing.view(closed, HUMANS["judge"], "judge")
