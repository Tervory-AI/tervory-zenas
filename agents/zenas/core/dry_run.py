"""Exercise the operational commands in disposable storage, without external I/O."""
from __future__ import annotations

import json
import os
from contextlib import ExitStack, asynccontextmanager
from copy import copy
from datetime import date
from unittest.mock import patch
from uuid import UUID

import httpx

COMMANDS = ("sample", "load", "nightly", "regulations", "convert", "judgment-training", "selftest",
            "reconcile", "gold", "heartbeat", "train", "erase-matter", "erase-tenant")
TENANT = "11111111-1111-4111-8111-111111111111"
MATTER = "22222222-2222-4222-8222-222222222222"
FACTS = "The placeholder buyer purchased goods under a written supply agreement. The placeholder seller delivered the goods and the buyer disputed the amount due under that agreement."
COURT_TEXT = f"Facts\n{FACTS}\nIssues\nWas payment due under the supply agreement?\nReasoning\nThe written agreement governs the parties' obligations.\nOrder\nThe appeal is dismissed without costs."


class Model:
    async def complete(self, messages, **kwargs):
        text = messages[-1]["content"]
        citation = next((line.removeprefix("Citation: ") for line in text.splitlines() if line.startswith("Citation: ")), "")
        value = {"facts": FACTS, "question": "Was payment due under the supply agreement?", "holding": "dismissed",
                 "elements": [{"element_id": "payment", "finding": "met", "quote": FACTS}],
                 "citations": [{"citation": citation, "quote": FACTS}]}
        return {"content": json.dumps(value) if kwargs.get("task_type") == "zenas.case_facts" else "{}",
                "cost_usd": 0, "finish_reason": "stop", "tokens_in": 0, "tokens_out": 0}


async def seed(conn, region):
    from agents.zenas.core.erasure import ConnectionPool
    from agents.zenas.core.operations import sources
    from agents.zenas.core.store import PgStore
    from agents.zenas.pool.builddoc import build_document
    from agents.zenas.pool.chunk import chunk_document
    from agents.zenas.pool.model import Section
    from agents.zenas.pool.store import PostgresStore

    store = PostgresStore(conn, home_region=region)
    await store.apply_schema()
    await PgStore(ConnectionPool(conn)).apply_schema()
    await conn.execute("INSERT INTO agent_zenas.tenants VALUES($1,$2)", UUID(TENANT), region)
    await conn.execute("""INSERT INTO agent_zenas.matters(id,tenant_id,jurisdiction,area,title,created_by)
        VALUES($1,$2,$3,'civil','Placeholder dry-run matter','placeholder')""", UUID(MATTER), UUID(TENANT), "za" if region == "za" else "us")
    source = next(s for s in sources(region, None) if s.source_id == ("concourt" if region == "za" else "courtlistener"))
    doc = build_document(source, citation="[2024] ZACC 1" if region == "za" else "1 F.3d 1",
                         title="Placeholder Supply v Placeholder Buyer", level="national", kind="case",
                         body="Constitutional Court" if region == "za" else "Court of Appeals",
                         sections=[Section("", "", COURT_TEXT)], url=source.canonical_url,
                         status="in_force", doc_date=date(2024, 1, 1), effective_from=date(2024, 1, 1))
    await store.record_source(source)
    await store.upsert_version(doc, chunk_document(doc), on=date.today())


async def run(args, *, region):
    from agents.zenas.core import convert, selftest
    from agents.zenas.core import operations as ops
    from agents.zenas.pool import bulk, nightly
    from agents.zenas.pool.ingest import ingest_snapshot
    from agents.zenas.pool.store import PostgresStore

    # Capture only names; supplied credentials are neither read nor echoed.
    names = ("DATABASE_URL", "TANK_URL", "TANK_KEY", "TANK_API_KEY", "TRAINING_JOB_KEY", "HF_TOKEN", "HF_ORG")
    missing = [name for name in names if not os.environ.get(name)]
    snapshots = nightly.packaged_snapshots(region)
    # Defaults stay small and civil; callers may explicitly select any recorded source.
    defaults = ["frcp"] if region == "us-east" else ["govza-gazette"]
    selected = ops.sources(region, args.source or defaults)
    requested = COMMANDS if args.command == "saturday" else (args.command,)
    requests = []

    def respond(request):
        requests.append({"method": request.method, "path": request.url.path})
        if request.url.path == "/v1/training/run":
            raise AssertionError("dry run cannot launch training")
        if request.url.path.endswith("/run/plan"):
            return httpx.Response(200, json={"status": "ready", "simulation": True})
        if request.url.path.endswith("/jobs/export"):
            return httpx.Response(200, json={"status": "complete", "simulation": True})
        if request.url.path == "/v1/training/receipt":
            return httpx.Response(200, json={"status": "complete", "simulation": True})
        if request.url.path.endswith("/court-text"):
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/memories":
            return httpx.Response(200, json={"id": MATTER, "seen": 1})
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json={"ok": True})
        raise AssertionError("unrecorded dry-run HTTP operation")

    transport = httpx.MockTransport(respond)
    client = httpx.AsyncClient

    def local_client(*a, **kw):
        kw.update(transport=transport, trust_env=False)
        return client(*a, **kw)

    load, night = bulk.load_wave, nightly.run_nightly

    async def offline_load(*a, **kw):
        kw.update(live=False, snapshots=snapshots)
        return await load(*a, **kw)

    async def offline_night(*a, **kw):
        kw.update(live=False, snapshots=snapshots)
        return await night(*a, **kw)

    async def offline_sample(conn, *, region, selected):
        store = PostgresStore(conn, home_region=region)
        receipts = []
        for source in selected:
            blobs = snapshots.get(source.source_id, [])
            if not blobs:
                raise ValueError("no recorded sample for the selected source")
            inserted = await ingest_snapshot(store, source, blobs, region=region, on=date.today())
            replay = await ingest_snapshot(store, source, blobs, region=region, on=date.today())
            if replay:
                raise AssertionError("dry-run source replay created duplicates")
            receipts.append({"source": source.source_id, "inserted": inserted, "replay_inserted": replay})
        return {"ok": True, "sources": receipts}

    async with ops.connection(temporary=True) as conn:
        await seed(conn, region)

        @asynccontextmanager
        async def local_connection(**kwargs):
            yield conn

        original_training = ops.training

        async def training_to_launch(command):
            return await original_training(command, transport=transport, stop_before_launch=True)

        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"TANK_REGION": region, "TANK_URL": "http://tank:8000",
                "TANK_KEY": "placeholder", "TANK_API_KEY": "placeholder", "TRAINING_JOB_KEY": "placeholder",
                "POOL_USE_FIXTURES": "0"}))
            for target, name, value in ((ops, "connection", local_connection), (ops, "sample", offline_sample),
                                        (ops, "training", training_to_launch), (bulk, "load_wave", offline_load),
                                        (nightly, "run_nightly", offline_night), (convert, "_night_model", lambda: (Model(), None)),
                                        (selftest, "_tank_model", lambda: (Model(), None)), (httpx, "AsyncClient", local_client)):
                stack.enter_context(patch.object(target, name, value))
            results = []
            for name in requested:
                command = copy(args)
                command.command, command.dry_run = name, False
                command.source, command.temporary, command.text_only = [s.source_id for s in selected], False, True
                command.tenant, command.matter = TENANT, MATTER if name == "erase-matter" else None
                if name == "train":
                    command.founder, command.tenant = "placeholder-founder", "00000000-0000-0000-0000-000000000001"
                    billing = bool(getattr(args, "job_id", ""))
                    command.submit, command.minutes = not billing, getattr(args, "minutes", 120)
                    command.job_id = "placeholder-job" if billing else ""
                    command.billed_usd = getattr(args, "billed_usd", None)
                value = await ops._execute(command, region)
                if name == "train":
                    value["artifacts"] = ops.training_artifacts()
                results.append({"command": name, **value})
            launches = sum(request["path"] == "/v1/training/run" for request in requests)
            if launches:
                raise AssertionError("dry run attempted GPU launch")
            return {"ok": all(row.get("ok") for row in results), "dry_run": True, "simulation": True,
                    "region": region, "storage": "disposable", "live_ready": False, "needs_settings": missing,
                    "launches": launches, "requests": requests, "results": results}
