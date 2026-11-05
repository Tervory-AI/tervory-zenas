"""One regional command for source runs, scheduled work and the existing HF job."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
import socket
import tempfile
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID

from agents.zenas.pool.config import current_region, jurisdiction_for
from agents.zenas.pool.region import RegionError

JOBS = {
    "regulations": "agents.zenas.core.regulations",
    "convert": "agents.zenas.core.convert",
    "judgment-training": "agents.zenas.core.judgment_training",
    "selftest": "agents.zenas.core.selftest",
}


class SettingsMissing(ValueError):
    def __init__(self, names):
        self.names = names
        super().__init__(", ".join(names))


def require(*names):
    missing = [name for name in names if not os.environ.get(name, "").strip()]
    if missing:
        raise SettingsMissing(missing)


def sources(region, wanted):
    from agents.zenas.pool.catalogue import sources_for_region
    found = sources_for_region(region)
    if wanted:
        if set(wanted) - {s.source_id for s in found}:
            raise RegionError("source is not on this region")
        found = [source for source in found if source.source_id in wanted]
    return found


@asynccontextmanager
async def connection(*, temporary=False):
    import asyncpg
    if not temporary:
        require("DATABASE_URL")
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            yield conn
        finally:
            await conn.close()
        return
    # Explicit, disposable cloud sample only. No production DSN is used here.
    import pgserver
    with tempfile.TemporaryDirectory(prefix="zenas-real-sample-") as directory:
        server = pgserver.get_server(Path(directory) / "postgres")
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            pgserver.pg_ctl(["-w", "restart", "-o", f"-h 127.0.0.1 -p {port}"], pgdata=server.pgdata)
            conn = await asyncpg.connect(f"postgresql://postgres@127.0.0.1:{port}/postgres")
            try:
                yield conn
            finally:
                await conn.close()
        finally:
            server.cleanup()


async def sample(conn, *, region, selected, http_get=None):
    """Fetch the adapters' small public wave, ingest twice, resolve stored citations."""
    from agents.zenas.pool.adapters import get_adapter
    from agents.zenas.pool.http import urllib_get
    from agents.zenas.pool.ingest import ingest_snapshot
    from agents.zenas.pool.licence import LicenceError, may_store_text
    from agents.zenas.pool.store import PostgresStore

    store = PostgresStore(conn, home_region=region)
    await store.apply_schema()
    receipts = []
    for source in selected:
        if not may_store_text(source):
            raise LicenceError("source disabled")
        requests = []

        async def getter(url, headers, _requests=requests):
            data = await (http_get or urllib_get)(url, headers)
            _requests.append({"url": url, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
            return data

        blobs = await get_adapter(source).fetch(getter)
        if not blobs:
            raise RuntimeError("source returned no sample")
        before = await store.instrument_count(source.source_id)
        inserted = await ingest_snapshot(store, source, blobs, on=date.today(), region=region)
        replayed = await ingest_snapshot(store, source, blobs, on=date.today(), region=region)
        documents = await conn.fetch("""SELECT document_id,version_id,citation,url,effective_from FROM zenas_pool.documents
            WHERE source_id=$1 AND jurisdiction=$2 AND effective_to IS NULL ORDER BY document_id""",
            source.source_id, jurisdiction_for(region))
        if not documents or replayed:
            raise RuntimeError("sample did not store idempotently")
        resolved = []
        for doc in documents:
            chunks = await store.find_by_citation(doc["citation"], jurisdiction=jurisdiction_for(region), as_at=None)
            if not any(chunk.document_id == doc["document_id"] and chunk.text.strip() for chunk in chunks):
                raise RuntimeError("sample citation did not resolve to stored text")
            resolved.append(dict(doc))
        receipts.append({"source": source.source_id, "requests": requests, "before": before,
                         "inserted": inserted, "replay_inserted": replayed, "documents": resolved})
    return {"ok": True, "region": region, "embedding": "not run; keyword and citation storage only", "sources": receipts}


async def training(args, *, transport=None, stop_before_launch=False):
    """Use Tank's export, founder authorization, readiness and cost-gated HF runner."""
    import httpx
    require("TANK_URL", "TANK_API_KEY", "TRAINING_JOB_KEY")
    current_region()  # Never guess a home for court text or the training writer.
    base = os.environ["TANK_URL"].rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"tank", "localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path):
        raise RegionError("training uses this region's private Tank only")
    if not args.founder:
        raise ValueError("--founder is required")
    headers = {"X-Tank-Key": os.environ["TANK_API_KEY"], "X-Tervory-User": args.founder,
               "X-Tervory-Tenant": str(UUID(args.tenant))}
    async with httpx.AsyncClient(base_url=base, transport=transport, trust_env=False,
                                 follow_redirects=False, timeout=600) as client:
        async def post(path, *, body=None, auth=None):
            response = await client.post(path, json=body, headers=auth or headers)
            if response.status_code != 200:
                raise RuntimeError("regional training endpoint refused the operation")
            value = response.json()
            if not isinstance(value, dict):
                raise TypeError("invalid training receipt")
            return value

        # Check founder authorization and row/budget readiness before any export.
        if args.job_id:
            if args.billed_usd is None:
                raise ValueError("--billed-usd is required with --job-id")
            billed = Decimal(args.billed_usd)
            if not billed.is_finite() or billed < 0:
                raise ValueError("invalid billed amount")
            result = await post("/v1/training/receipt", body={"job_id": args.job_id, "billed_usd": args.billed_usd})
            return {"ok": result.get("status") == "complete", "phase": "receipt", "result": result}
        body = {"agent": "zenas", "minutes": args.minutes}
        plan = await post("/v1/training/run/plan", body=body)
        collectible = {"fewer than 2,000 public zenas training rows", "fewer than 200 holdout rows"}
        if plan.get("status") != "ready" and (not args.submit or plan.get("status") not in collectible):
            return {"ok": False, "phase": "readiness", "result": plan}
        if not args.submit:
            return {"ok": True, "phase": "plan", "result": plan}
        court_text = await prepare_court_text(current_region())
        exported = await post("/v1/training/jobs/export?agent=zenas", auth={"X-Tank-Key": os.environ["TRAINING_JOB_KEY"]})
        if exported.get("status") != "complete":
            return {"ok": False, "phase": "dataset", "result": exported}
        if plan.get("status") != "ready":
            plan = await post("/v1/training/run/plan", body=body)
            if plan.get("status") != "ready":
                return {"ok": False, "phase": "readiness", "result": plan}
        if stop_before_launch:
            return {"ok": True, "phase": "ready_to_launch", "launch_requested": False, "court_text": court_text}
        result = await post("/v1/training/run", body=body)
        return {"ok": result.get("status") == "running", "phase": "submit", "court_text": court_text, "result": result}


async def prepare_court_text(region):
    from agents.zenas.core.judgment_training import run_scheduled
    counts = {"split": 0, "excluded": 0}
    if region != "za":
        return counts
    require("TANK_KEY")
    async with connection() as conn:
        while True:
            batch = await run_scheduled(conn, region=region)
            for name in counts:
                counts[name] += batch[name]
            if not sum(batch.values()):
                return counts


async def execute(args):
    require("TANK_REGION")
    region = current_region()
    if getattr(args, "dry_run", False):
        from agents.zenas.core.dry_run import run
        result = await run(args, region=region)
        if args.command == "train":
            result["artifacts"] = training_artifacts()
        return result
    if args.command == "nightly" and region == "za":
        from agents.zenas.pool.nightly import _ZA_RUN_SECONDS
        deadline = monotonic() + _ZA_RUN_SECONDS
        async with asyncio.timeout(_ZA_RUN_SECONDS):
            result = await _execute(args, region, deadline=deadline)
    else:
        result = await _execute(args, region)
    if args.command == "train":
        result["artifacts"] = training_artifacts()
    return result


def training_artifacts():
    return {
            "dataset_repo": "HF_ORG/tervory-public-zenas",
            "snapshots": "snapshots/{region}/{YYYY-MM-DD}.jsonl",
            "holdouts": "holdouts/{region}/{YYYY-MM-DD}.jsonl",
            "config": "tank/training_jobs/train.py",
            "model_repo": "HF_ORG/tervory-own-zenas-za",
            "results": ["evaluation.json", "holdout.json", "zenas-q4_k_m.gguf"],
            "qualification": {"method": "exact_outcome", "min_cases": 200, "min_agreement": 0.99, "auto_promote": False},
            "tank_settings": ["HF_TOKEN", "HF_ORG", "HF_TRAIN_MAX_USD"],
    }


async def _execute(args, region, *, deadline=None):
    if os.environ.get("POOL_USE_FIXTURES", "").strip().lower() in {"1", "true", "yes"}:
        raise ValueError("POOL_USE_FIXTURES must be disabled for operational runs")
    if args.command == "train":
        return await training(args)
    if args.command == "heartbeat":
        from types import SimpleNamespace

        from chassis.memory import MemoryClient

        from agents.zenas.app import HEARTBEAT_TENANT, ping
        require("TANK_KEY")
        result = await ping(SimpleNamespace(agent="zenas", tenant_id=HEARTBEAT_TENANT,
                                           input={"heartbeat": True}, memory=MemoryClient(agent="zenas")))
        return {"ok": result.get("memory") != "unreachable", "result": result}
    selected = sources(region, args.source) if args.command in {"sample", "load", "nightly"} else []
    if args.command == "sample" and not args.source:
        selected = sources(region, ["frcp"] if region == "us-east" else ["govza-acts"])
    if args.command in {"nightly", "convert", "selftest", "regulations", "judgment-training"}:
        require("TANK_URL", "TANK_KEY")
    if args.command in {"load", "nightly"} and not args.text_only:
        require("TANK_KEY")
    async with connection(temporary=args.temporary) as conn:
        if args.command == "sample":
            return await sample(conn, region=region, selected=selected)
        if args.command in {"erase-matter", "erase-tenant"}:
            from agents.zenas.core.erasure import erase
            return {"ok": True, **await erase(conn, tenant_id=args.tenant, region=region, matter_id=args.matter)}
        from agents.zenas.core.erasure import ConnectionPool
        from agents.zenas.core.store import PgStore
        from agents.zenas.pool.store import PostgresStore

        store = PostgresStore(conn, home_region=region)
        await store.apply_schema()
        await PgStore(ConnectionPool(conn)).apply_schema()
        if args.command == "reconcile":
            from agents.zenas.core.freshness import reconcile
            return {"ok": True, "retired": await reconcile(conn, region)}
        if args.command == "gold":
            from agents.zenas.gold_sources import candidates
            rows = await candidates(ConnectionPool(conn), region=region)
            # Candidates are consumed through the existing authenticated endpoint;
            # operator receipts never echo their contents.
            return {"ok": True, "candidates": len(rows)}
        if args.command in JOBS:
            result = await importlib.import_module(JOBS[args.command]).run_scheduled(conn, region=region)
            return {"ok": result.get("ok", True) if isinstance(result, dict) else False, "job": args.command, "result": result}
        from agents.zenas.pool.embed import NoOpEmbedder, NoOpVectorIndex
        backends = {"embedder": NoOpEmbedder(), "index": NoOpVectorIndex()} if args.text_only else {}
        if args.command == "load":
            from agents.zenas.pool.bulk import load_wave
            result = await load_wave(store, region=region, only=selected, live=True, **backends)
        else:
            from agents.zenas.pool.nightly import _scheduled_jobs, run_nightly
            result = await run_nightly(store, region=region, only=[s.source_id for s in selected], live=True,
                                       deadline=deadline, **backends)
            await _scheduled_jobs(conn, result, region=region, deadline=deadline)
        selected_ids = {source.source_id for source in selected}
        bad = [row["source_id"] for row in result.get("sources", [])
               if row["source_id"] in selected_ids
               and row.get("last_status") not in {"ok", "not_configured", "deferred", None}]
        return {"ok": not bad and not result.get("failed_jobs"), "failed_sources": bad, "result": result}


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    for name in ["sample", "load", "nightly", *JOBS, "reconcile", "gold", "heartbeat", "erase-matter", "erase-tenant", "train", "saturday"]:
        command = sub.add_parser(name)
        command.set_defaults(source=None, temporary=False, text_only=False, matter=None)
        command.add_argument("--dry-run", action="store_true", help="Run with disposable storage and offline fakes; never launch training")
        if name == "saturday":
            command.set_defaults(dry_run=True)
        command.add_argument("--receipt", type=Path, help="Write an operational JSON receipt; never contains credentials")
        if name in {"sample", "load", "nightly"}:
            command.add_argument("--source", action="append", help="Regional source ID; repeat to select sources")
        if name == "sample":
            command.add_argument("--temporary", action="store_true", help="Use disposable local PostgreSQL, ignoring DATABASE_URL")
        if name in {"load", "nightly"}:
            command.add_argument("--text-only", action="store_true", help="Explicitly defer embeddings; store searchable text")
        if name.startswith("erase-") or name == "train":
            command.add_argument("--tenant", required=name.startswith("erase-"), default="00000000-0000-0000-0000-000000000001", type=str)
        if name == "erase-matter":
            command.add_argument("--matter", required=True)
        if name == "train":
            command.add_argument("--founder", default="", help="Existing Trigger sign-in founder user ID; verified by Tank")
            command.add_argument("--minutes", default=120, type=int, choices=range(1, 241), metavar="1..240")
            action = command.add_mutually_exclusive_group()
            action.add_argument("--submit", action="store_true", help="Export dataset and start the cost-gated HF GPU job")
            action.add_argument("--job-id", default="", help="Reconcile a completed HF job using its actual billed receipt")
            command.add_argument("--billed-usd", help="Actual HF billed USD for --job-id")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    started = datetime.now(UTC).isoformat()
    try:
        result = asyncio.run(execute(args))
    except SettingsMissing as exc:
        result = {"ok": False, "needs_settings": exc.names}
    except Exception as exc:  # noqa: BLE001 — receipts expose the class, never private driver/HTTP details
        # No driver messages, request headers, bodies or DSNs in logs/receipts.
        result = {"ok": False, "error_type": type(exc).__name__}
    result = {"command": args.command, "started_at": started,
              "finished_at": datetime.now(UTC).isoformat(), **result}
    encoded = json.dumps(result, default=str, indent=2) + "\n"
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(encoded)
    print(encoded, end="")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
