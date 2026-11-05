"""Live collection and status use the same tenant ledger as the room API."""
from __future__ import annotations

import json
import uuid
from argparse import Namespace

import asyncpg

from core.evidence import overview
from core.jobs import perform_refresh
from core.ledger import PostgresLedger


async def run_postgres(args: Namespace, db: str) -> int:
    if not args.tenant:
        raise ValueError("--tenant is required for Postgres")
    tenant = str(uuid.UUID(args.tenant))
    conn = await asyncpg.connect(db)
    try:
        ledger = PostgresLedger(conn)
        await ledger.apply_schema()
        if args.command == "run":
            # Serialize CLI and worker refreshes for the same tenant. No URL or
            # environment value is included in the report.
            await conn.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", tenant + ":market-collect")
            try:
                await perform_refresh(tenant_id=tenant, engine=args.engine, ledger=ledger,
                                      fixtures=args.fixtures, live=args.live, backfill=args.backfill, source=args.source)
            finally:
                await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", tenant + ":market-collect")
        result = await overview(ledger, tenant, args.engine, fixtures=bool(getattr(args, "fixtures", None)))
        print(json.dumps(result, indent=2))
        return 1 if not any(feed["kind"] == "copy" and feed["rows"] > 0 for feed in result["feeds"]) or any(feed["kind"] == "copy" and feed["note"] in {"Off, source could not be read", "Off, blocked"} for feed in result["feeds"]) else 0
    finally:
        await conn.close()
