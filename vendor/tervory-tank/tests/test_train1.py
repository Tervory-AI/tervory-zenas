"""TRAIN-1 isolation, review, collection and export tests. All credentials ephemeral."""
from __future__ import annotations
from trigger_signin_fakes import set_founders

import asyncio
import json
import os
import secrets
import uuid
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException

import gold
import skala
import training

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("TRAINING_SCHEMA_DATABASE_URL") or os.environ.get("POOL_SCHEMA_DATABASE_URL")


@pytest_asyncio.fixture
async def database(monkeypatch):
    if not URL:
        pytest.skip("Set TRAINING_SCHEMA_DATABASE_URL to a disposable Postgres")
    admin = await asyncpg.connect(URL)
    name = "train1_" + uuid.uuid4().hex
    await admin.execute(f'CREATE DATABASE "{name}"')
    pool = await asyncpg.create_pool(URL, database=name, min_size=1, max_size=4)
    monkeypatch.setenv("DATABASE_URL",URL.rsplit('/',1)[0]+'/'+name)
    monkeypatch.setenv("LITELLM_MASTER_KEY","placeholder-training-model-key")
    monkeypatch.setenv("TANK_API_KEY","placeholder-training-workspace-key")
    async with pool.acquire() as conn:
        await conn.execute((ROOT / "tank/schema.sql").read_text())
        from savings_defaults import initialize
        await initialize(conn)
    from paid_steps_fixtures import configure_paid_auto
    configure_paid_auto(monkeypatch,lambda:pool)
    rt = SimpleNamespace(pool=lambda: pool, box_region=lambda: "us-east", _packages={})
    monkeypatch.setattr(gold, "runtime", lambda: rt)
    monkeypatch.setattr(training, "runtime", lambda: rt)
    try:
        yield pool, rt
    finally:
        await pool.close()
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()


async def candidate(conn, tenant, value=None, skill="base.fixture"):
    return await gold.add_candidate(conn, tenant=tenant, skill=skill, agent=skill.split(".")[0],
                                    value=value or {"text": "a fixture"}, expected={"answer": "checked"},
                                    source="package", ref=gold.input_hash(value or {}))


@pytest.mark.asyncio
async def test_schema_empty_repeat_and_current_main_with_agent_roles(monkeypatch):
    monkeypatch.setattr(gold, "runtime", lambda: SimpleNamespace(box_region=lambda: "us-east"))
    if not URL:
        pytest.skip("Set TRAINING_SCHEMA_DATABASE_URL to a disposable Postgres")
    from test_zenas_schema_apply import _guarded_ownership
    from chassis.roles import statements_for
    admin = await asyncpg.connect(URL)
    new = (ROOT / "tank/schema.sql").read_text()
    baseline = (ROOT / "tests/fixtures/train1-main-schema.sql").read_text()
    # The historical fixture remains byte-identical. AGENT-1 corrected only
    # this comment; normalize it here without allowing any DDL-prefix change.
    historical = new.replace("as sent; agent chat includes its policy system prompt",
        "as sent (system prompt excluded; it lives in policy)")
    assert historical.startswith(baseline)
    for existing in (False, True):
        name = "train1_schema_" + uuid.uuid4().hex
        await admin.execute(f'CREATE DATABASE "{name}"')
        conn = await asyncpg.connect(URL, database=name)
        try:
            if existing:
                await conn.execute(baseline)
                for agent in ("base", "tiger", "market_lab", "zenas", "rig", "radix", "tisan"):
                    for statement in statements_for(agent, secrets.token_hex(24)):
                        if "ALTER SEQUENCE" in statement:
                            statement = _guarded_ownership(f"agent_{agent}")
                        await conn.execute(statement)
            await conn.execute(new)
            saved = await candidate(conn, gold.PLATFORM_TENANT)
            await conn.execute(new)
            assert await conn.fetchval("SELECT id FROM tank.gold_examples WHERE id=$1", saved) == saved
            for table in ("gold_examples", "gold_scores", "training_pairs", "training_exports"):
                assert await conn.fetchval("SELECT to_regclass($1)", f"tank.{table}")
            if existing:
                for agent in ("base", "tiger", "market_lab", "zenas", "rig", "radix", "tisan"):
                    assert not await conn.fetchval(
                        "SELECT has_table_privilege($1,'tank.gold_examples','SELECT')", f"agent_{agent}",
                    )
        finally:
            await conn.close()
            await admin.execute(f'DROP DATABASE "{name}"')
    await admin.close()


def test_holdout_stable_one_in_five_and_no_duplicate_leakage():
    tenant = gold.PLATFORM_TENANT
    count = sum(gold.is_holdout(tenant, "base.fixture", {"case": i}) for i in range(10000))
    assert 1900 <= count <= 2100
    assert gold.is_holdout(tenant, "base.fixture", {"a": 1, "b": 2}) == gold.is_holdout(tenant, "base.fixture", {"b": 2, "a": 1})
    assert gold.is_holdout(tenant, "base.fixture", {"a": 1}) == gold.is_holdout(uuid.uuid4(), "other.skill", {"a": 1})


@pytest.mark.asyncio
async def test_import_review_correction_and_tenant_seal(database):
    pool, rt = database
    rt._packages = skala.load_packages()
    await gold.import_packages()
    async with pool.acquire() as conn:
        before = await conn.fetchval("SELECT count(*) FROM tank.gold_examples")
    assert before > 100
    await gold.import_packages()
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM tank.gold_examples") == before
        one = await candidate(conn, gold.PLATFORM_TENANT)
    founder = gold.Reviewer("founder", gold.PLATFORM_TENANT, True)
    other = gold.Reviewer("founder", uuid.uuid4(), True)
    with pytest.raises(HTTPException) as denied:
        await gold.check(one, gold.Verdict(action="right"), other)
    assert denied.value.status_code == 404
    await gold.check(one, gold.Verdict(action="right"), founder)
    async with pool.acquire() as conn:
        checked = await conn.fetchrow("SELECT * FROM tank.gold_examples WHERE id=$1", one)
        pair = await conn.fetchrow("SELECT * FROM tank.training_pairs WHERE gold_id=$1", one)
        assert checked["status"] == "checked"
        assert pair is None if checked["holdout"] else pair["holdout"] is False
        wrong = await candidate(conn, gold.PLATFORM_TENANT, {"case": "wrong"})
    await gold.check(wrong, gold.Verdict(action="wrong", correction={"answer": "corrected"}), founder)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT status FROM tank.gold_examples WHERE id=$1", wrong) == "rejected"
        corrected = await conn.fetchrow("SELECT * FROM tank.gold_examples WHERE source='correction' AND source_ref=$1", str(wrong))
        assert corrected["status"] == "candidate"
        assert not await conn.fetchval("SELECT 1 FROM tank.training_pairs WHERE gold_id=$1", wrong)
        assert corrected["holdout"] == gold.is_holdout(gold.PLATFORM_TENANT, "base.fixture", {"case": "wrong"})
    with pytest.raises(HTTPException):
        await gold.check(one, gold.Verdict(action="right"), founder)
    assert (await gold.summary(other))["skills"] == []


@pytest.mark.asyncio
async def test_auth_missing_agent_keys_and_reviewer_boundary(monkeypatch):
    key = secrets.token_urlsafe(32)
    monkeypatch.setenv("TANK_API_KEY", key)
    set_founders(monkeypatch, "founder")
    app = FastAPI()
    app.include_router(gold.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        for headers in ({}, {"x-tank-key": secrets.token_urlsafe(32)}, {"x-tank-key": key}):
            assert (await client.get("/v1/gold/summary", headers=headers)).status_code == 404
    actor = await gold.require_reviewer(key, "reviewer", uuid.uuid4(), "reviewer")
    gold.allowed_agent(actor, "zenas")
    with pytest.raises(HTTPException):
        gold.allowed_agent(actor, "base")
    with pytest.raises(HTTPException):
        await gold.require_reviewer(key, "member", uuid.uuid4(), "member")


async def accepted_ledger(conn, tenant, accepted=True, region="us-east"):
    ident = uuid.uuid4()
    await conn.execute(
        """INSERT INTO tank.ledger (id,tenant_id,region,agent,task_type,model,feedback,feedback_by,accept_after)
        VALUES ($1,$2,$3,'base','base.fixture','code',$4,'person',now()-interval '1 second')""",
        ident, tenant, region, "accepted" if accepted else "rejected",
    )
    await conn.execute(
        """INSERT INTO tank.transcripts(ledger_id,tenant_id,task_type,messages,content)
        VALUES ($1,$2,'base.fixture',$3::jsonb,$4)""", ident, tenant,
        json.dumps([{"role": "user", "content": json.dumps({"record": str(ident)})}]), '{"answer":"yes"}',
    )
    return ident


@pytest.mark.asyncio
async def test_accepted_ledger_pair_rejection_and_region(database):
    pool, _ = database
    tenant, other = uuid.uuid4(), uuid.uuid4()
    async with pool.acquire() as conn:
        accepted = await accepted_ledger(conn, tenant)
        await accepted_ledger(conn, tenant, accepted=False)
        await accepted_ledger(conn, tenant, region="za")
        await accepted_ledger(conn, other)
        await training.collect_ledger(conn, tenant)
        await training.collect_ledger(conn, tenant)
        rows = await conn.fetch("SELECT * FROM tank.training_pairs")
        assert len(rows) == 2 and all(row["tenant_id"] == tenant for row in rows)
        positive = next(row for row in rows if row["source"] == "client_accepted")
        assert positive["source_ref"] == str(accepted) and positive["gold_id"] is None
        await conn.execute("UPDATE tank.ledger SET feedback='rejected' WHERE id=$1", accepted)
        await training.collect_ledger(conn, tenant)
        assert not await conn.fetchval("SELECT active FROM tank.training_pairs WHERE source_ref=$1", str(accepted))
        assert await conn.fetchval("SELECT count(*) FROM tank.gold_examples WHERE source_ref=$1", str(accepted)) == 0
        assert await conn.fetchval("SELECT polarity FROM tank.training_pairs WHERE source_ref=$1 AND source='client_rejected'", str(accepted)) == "negative"


@pytest.mark.asyncio
async def test_lawyer_released_and_decided_case_pairs(database):
    pool, _ = database
    tenant = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute((ROOT / "agents/zenas/schema.sql").read_text())
        matter, document, worked, pair = [uuid.uuid4() for _ in range(4)]
        await conn.execute("INSERT INTO agent_zenas.matters(id,tenant_id,jurisdiction,area,title,created_by) VALUES ($1,$2,'us','civil','fixture','lawyer')", matter, tenant)
        await conn.execute("INSERT INTO agent_zenas.documents(id,matter_id,tenant_id,kind,version,body,state) VALUES ($1,$2,$3,'memo',1,'masked','released')", document, matter, tenant)
        await conn.execute("INSERT INTO agent_zenas.reviews(document_id,tenant_id,reviewer,jurisdiction,admission_no,decision) VALUES ($1,$2,'lawyer','us','fixture','released')", document, tenant)
        await conn.execute("INSERT INTO agent_zenas.worked_matters(id,tenant_id,region,source,jurisdiction,released_matter_id,material_type) VALUES ($1,$2,'us-east','released','us',$3,'client_work')", worked, tenant, matter)
        await conn.execute("INSERT INTO agent_zenas.training_pairs(id,tenant_id,region,source,worked_id,request,outcome) VALUES ($1,$2,'us-east','released',$3,'masked facts','released answer')", pair, tenant, worked)
        await training.collect_zenas(conn, uuid.uuid4())
        assert await conn.fetchval("SELECT count(*) FROM tank.training_pairs") == 0
        await training.collect_zenas(conn, tenant)
        await training.collect_zenas(conn, tenant)
        row = await conn.fetchrow("SELECT * FROM tank.training_pairs")
        assert row["approved_by"] == "lawyer" and row["client_matter"]
        public = await conn.fetchval("INSERT INTO agent_zenas.worked_matters(region,source,jurisdiction,material_type) VALUES ('us-east','case','us','public_record') RETURNING id")
        await conn.execute("INSERT INTO agent_zenas.training_pairs(region,source,worked_id,request) VALUES ('us-east','case',$1,'public decision')", public)
        await training.collect_zenas(conn, None)
        assert await conn.fetchval("SELECT count(*) FROM tank.training_pairs") == 1


@pytest.mark.asyncio
async def test_retest_excludes_holdout_and_records_three_point_drop(database, monkeypatch):
    pool, rt = database
    tenant = uuid.uuid4()
    contexts = []
    pkg = SimpleNamespace(id="base.fixture", version="1.0.0")
    rt.get_package = lambda *_: pkg
    rt.make_ctx = lambda pkg, owner, **kwargs: contexts.append(owner)
    seen = []

    async def run(_pkg, value, _ctx):
        seen.append(value["i"])
        return {"ok": value["i"] >= 5}

    monkeypatch.setattr(skala, "run_skill", run)
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tank.skills(id,version,agent,rung,state) VALUES ('base.fixture','1.0.0','base',3,'approved')")
        for i in range(101):
            ident = await candidate(conn, tenant, {"i": i})
            await conn.execute("UPDATE tank.gold_examples SET status='checked',expected='{\"ok\":true}',holdout=$2 WHERE id=$1", ident, i == 100)
        await conn.execute("INSERT INTO tank.gold_scores(tenant_id,region,skill_id,version,score,n) VALUES ($1,'us-east','base.fixture','1.0.0',0.98,100)", tenant)
    result = await gold.run_retests(tenant=tenant)
    assert result[0]["score"] == 0.95 and result[0]["drop"] is True
    assert result[0]["previous"] == 0.98 and result[0]["n"] == 100
    assert 100 not in seen
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM tank.gold_scores") == 2
    # Read the persisted scores through the endpoint used by /skala/gold.
    report = await gold.summary(gold.Reviewer("founder", tenant, True))
    assert report["skills"][0]["trend"] == -0.03
    assert report["skills"][0]["drop"] is True
    assert report["skills"][0]["last_score"] == 0.95
    assert not (await gold.summary(gold.Reviewer("founder", uuid.uuid4(), True)))["skills"]
    assert not (await gold.summary(gold.Reviewer("reviewer", tenant, False)))["skills"]
    from datetime import datetime, timezone
    assert await gold.run_retests(tenant=tenant, since=datetime(2000, 1, 1, tzinfo=timezone.utc)) == []
    seen.clear()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tank.skills(id,version,agent,rung,state) VALUES ('base.fixture','1.1.0','base',3,'staging')")
    rt.get_package = lambda skill, version: SimpleNamespace(id=skill, version=version)
    await gold.final_check("base.fixture", "1.1.0", gold.Reviewer("founder", tenant, True))
    assert seen == [100]
    assert contexts and set(contexts) == {gold.PLATFORM_TENANT}


@pytest.mark.asyncio
async def test_empty_optional_configuration_starts_normally(database, monkeypatch):
    for name in ("HF_TOKEN", "HF_ORG", "TRAINING_JOB_KEY"):
        monkeypatch.setenv(name, "")
    app = FastAPI()
    app.include_router(gold.router)
    app.include_router(training.router)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.05)
        assert await training.export_once() == {"status": "skipped", "reason": "not configured"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            # An unconfigured job is disabled, not an unauthenticated paid run.
            assert (await client.post("/v1/gold/jobs/retest")).status_code == 404
    import yaml
    for path in ("docker-compose.yml", "docker-compose.za.yml"):
        env = yaml.safe_load((ROOT / path).read_text())["services"]["tank"]["environment"]
        for name in ("HF_TOKEN", "HF_ORG", "TRAINING_JOB_KEY"):
            assert env[name] == "${" + name + ":-}"


@pytest.mark.asyncio
async def test_export_za_preserves_identifiers_excludes_holdout_and_inactive_private_only(database, monkeypatch):
    pool, rt = database
    rt.box_region = lambda: "za"
    monkeypatch.setenv("HF_TOKEN", secrets.token_urlsafe(32))
    monkeypatch.setenv("HF_ORG", "fixture")
    tenant = uuid.uuid4()
    async with pool.acquire() as conn:
        for i, value in enumerate(({"text": "masked facts"}, {"text": "person@example.invalid"}, {"text": "held back"}, {"text": "inactive"}, {"text": "ID 8001015009087"})):
            await training.save_pair(conn, tenant=tenant, agent="zenas", task="zenas.reasoning", value=value,
                                     output={"answer": "masked"}, approved_by="reviewer", source="gold", ref=str(i),
                                     holdout=i == 2, client_matter=True)
        await conn.execute("UPDATE tank.training_pairs SET active=false WHERE tenant_id=$1 AND source_ref='3'", tenant)
    requests = []

    async def handler(request):
        requests.append(request)
        if request.url.path == "/api/repos/create":
            assert json.loads(request.content)["private"] is True
            return httpx.Response(201, json={})
        if request.method == "GET":
            return httpx.Response(200, json={"private": True})
        return httpx.Response(200, json={})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(training.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    result = await training.export_once()
    assert result == {"status": "complete", "exported": 0}
    assert requests == []


@pytest.mark.asyncio
async def test_official_court_split_is_saved_and_reported_per_source(database, monkeypatch):
    pool, rt = database
    rt.box_region = lambda: "za"
    monkeypatch.setenv("TANK_KEY_ZENAS", "placeholder-tank-key")
    monkeypatch.setattr(training, "is_holdout", lambda *_args: False)
    async with pool.acquire() as conn:
        await conn.execute((ROOT / "agents/zenas/pool/schema.sql").read_text())
    body = training.CourtTextPair(
        source_id="concourt", document_id="placeholder-document", version_id="placeholder-version",
        status="split", input={"facts": "placeholder facts", "issues": "placeholder issue"},
        output={"reasoning": "placeholder reasons", "order": "The application is granted.",
                "outcome": "granted", "costs": "without costs"},
    )
    request = SimpleNamespace(headers={"x-tank-key": "placeholder-tank-key"})
    assert await training.save_court_text_pair(body, request) == {"ok": True}
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM tank.training_pairs WHERE source='court_text'")
        assert row["tenant_id"] == gold.PLATFORM_TENANT
        assert row["public"] is True and row["client_matter"] is False and row["holdout"] is False
        assert row["source_ref"] == "concourt:placeholder-version"
    result = await training.training_status(training.Reviewer("founder", uuid.uuid4(), True))
    names = {item["name"]: item for item in result["items"]}
    assert names["concourt split"]["count"] == 1
    assert names["concourt pairs ready"]["count"] == 1
    for source in training.OFFICIAL_ZA_CASE_SOURCES:
        assert {f"{source} judgments stored", f"{source} split", f"{source} excluded", f"{source} pairs ready"} <= names.keys()


@pytest.mark.asyncio
async def test_refuse_existing_public_dataset(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(training, "archive_training_snapshot", AsyncMock())
    monkeypatch.setattr(training, "runtime", lambda: SimpleNamespace(box_region=lambda: "us-east"))
    async def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"private": False})
        assert request.url.path == "/api/repos/create"
        return httpx.Response(409)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="not private"):
            await training.upload_dataset(client, "fixture", "base", "2026-01-01", [])


@pytest.mark.asyncio
async def test_rig_only_approved_and_tiger_only_human_confirmed(database):
    pool, _ = database
    tenant, other = uuid.uuid4(), uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute((ROOT / "agents/rig/schema.sql").read_text())
        for part, approved in (("accepted", True), ("pending", False)):
            await conn.execute("INSERT INTO agent_rig.fits(tenant_id,part_sku,fits_sku,source,approved) VALUES ($1,$2,'target','suggested',$3)", tenant, part, approved)
        await training.collect_fits(conn, tenant)
        assert await conn.fetchval("SELECT count(*) FROM tank.gold_examples WHERE source='rig'") == 1
        await conn.execute("UPDATE agent_rig.fits SET approved=false,source='rejected' WHERE tenant_id=$1", tenant)
        await training.collect_fits(conn, tenant)
        assert await conn.fetchval("SELECT status FROM tank.gold_examples WHERE source='rig'") == "rejected"
        # Tiger's room schema does not have an approval flag: a human confirmation
        # endpoint is the sole path from an observation into the shared store.
        await conn.execute("CREATE SCHEMA IF NOT EXISTS agent_tiger")
        await conn.execute("CREATE TABLE agent_tiger.observations(id uuid,tenant_id uuid,url text,evidence text,price numeric,currency text,availability text,method text DEFAULT 'json_ld')")
        observation = uuid.uuid4()
        await conn.execute("INSERT INTO agent_tiger.observations(id,tenant_id,url,evidence,price,currency,availability) VALUES ($1,$2,'https://example.invalid/item','confirmed snapshot',12,'USD','in stock')", observation, tenant)
        private = uuid.uuid4()
        await conn.execute("INSERT INTO agent_tiger.observations VALUES ($1,$2,'https://dealer.example/item','private receipt',12,'USD','in stock','private_portal')", private, tenant)
        assert not await conn.fetchval("SELECT 1 FROM tank.gold_examples WHERE source='tiger_confirmed'")
    with pytest.raises(HTTPException):
        await training.confirm_tiger(observation, gold.Reviewer("founder", other, True))
    await training.confirm_tiger(observation, gold.Reviewer("founder", tenant, True))
    with pytest.raises(HTTPException) as denied:
        await training.confirm_tiger(private, gold.Reviewer("founder", tenant, True))
    assert denied.value.status_code == 404
    await training.confirm_tiger(observation, gold.Reviewer("founder", tenant, True))
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM tank.gold_examples WHERE source='tiger_confirmed'") == 1


@pytest.mark.asyncio
async def test_actual_tank_startup_with_empty_optional_settings(database):
    import subprocess
    import sys
    pool, _ = database
    async with pool.acquire() as conn:
        name = await conn.fetchval("SELECT current_database()")
    env = dict(os.environ)
    env.update(DATABASE_URL=URL.rsplit("/", 1)[0] + "/" + name,
               HF_TOKEN="", HF_ORG="", TRAINING_JOB_KEY="", REDIS_URL="redis://127.0.0.1:1/0")
    env.update({name: secrets.token_urlsafe(32) for name in ("TANK_API_KEY", "LITELLM_MASTER_KEY")})
    code = '''
import asyncio
import main
import training
async def check():
    async with main.app.router.lifespan_context(main.app):
        async with main.pool().acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM tank.gold_examples") > 100
        assert (await training.export_once())["status"] == "skipped"
    await main.pool().close()
asyncio.run(check())
'''
    result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", code], env=env,
                                     cwd=ROOT / "tank", capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, "Tank startup with optional settings empty failed"
