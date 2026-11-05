"""Synthetic external histories, stored by the existing feed and Tiger paths."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [
    str(ROOT / "agents/market_lab"),
    str(ROOT / "agents/_chassis"),
    str(ROOT / "agents/tiger"),
]

import asyncpg
from chassis.db import ensure_schema
from core.jobs import perform_refresh
from core.ledger import PostgresLedger
from core.warnings import run
from fixture_proof.server import database_url
from watch import PgStore, _reading_evidence
from engines.ffl.warning_fixture import ENGINE, LABEL, PRODUCT, NAMES

OWNER = "00000000-0000-0000-0000-000000000916"
ONE = "00000000-0000-0000-0000-000000000917"
EMPTY = "00000000-0000-0000-0000-000000000918"
RESOLVED = "00000000-0000-0000-0000-000000000919"


def month(day, delta):
    number = day.year * 12 + day.month - 1 + delta
    return day.replace(year=number // 12, month=number % 12 + 1, day=1)


async def port(ledger, tenant, clock):
    current = clock.date().replace(day=1)
    drops = {month(current, offset) for offset in (-18, -12, -6, 0)}
    with tempfile.TemporaryDirectory(prefix="warning-series-") as folder:
        path = Path(folder) / "ports"
        path.mkdir()
        (path / "los_angeles.csv").write_text(
            "month,total_teu\n"
            + "".join(
                f"{month(current, i):%Y-%m},{82000 if month(current, i) in drops else 100000}\n"
                for i in range(-24, 1)
            )
        )
        await perform_refresh(
            tenant_id=tenant,
            engine=ENGINE,
            ledger=ledger,
            source="port_los_angeles",
            fixtures=folder,
            now=clock,
        )
    from uuid import UUID

    for table in ("feed_checks", "feed_documents"):
        await ledger.conn.execute(
            f"UPDATE agent_market_lab.{table} SET fixture=false WHERE tenant_id=$1 AND fixture=true",
            UUID(tenant),
        )


async def product(
    store, tenant, clock, *, anchor=None, outcome="hit", short=False, suffix="current"
):
    current = anchor or clock.date().replace(day=1)
    start = month(current, -9 if short else -24)
    url = f"https://1.1.1.1/market-lab-proof/{suffix}"
    watch = await store.get_watch(tenant, url)
    if not watch:
        watch = await store.create_watch(
            {
                "tenant_id": tenant,
                "url": url,
                "label": LABEL,
                "engine": ENGINE,
                "every": "7d",
                "enabled": False,
                "threshold": 0.1,
                "alert_price": False,
                "alert_stock": False,
            }
        )
    existing = {row["ts"][:10] for row in await store.list_observations(tenant, url)}
    drops = [month(current, i) for i in (-18, -12, -6, 0)]
    days = []
    d = start
    while d <= clock.date():
        out = any(
            trigger + timedelta(days=35 + 7 * (i % 3))
            <= d
            <= trigger + timedelta(days=63)
            for i, trigger in enumerate(drops)
            if trigger != current or outcome == "hit"
        )
        if d.isoformat() not in existing:
            await store.add_observation(
                {
                    "tenant_id": tenant,
                    "url": url,
                    "ts": datetime.combine(d, datetime.min.time(), timezone.utc),
                    "price": "24.95",
                    "currency": "USD",
                    "availability": "OutOfStock" if out else "InStock",
                    "method": "jsonld",
                    "evidence": _reading_evidence(
                        json.dumps({"source": "saved external input"}),
                        PRODUCT + " — " + NAMES[suffix],
                        None,
                        None,
                    ),
                }
            )
        days.append(d)
        d += timedelta(days=7)
    rows = await store.list_observations(tenant, url)
    return {
        "watch": watch,
        "observations": [
            row for row in rows if row["ts"][:10] <= clock.date().isoformat()
        ],
    }


async def seed():
    now = datetime.now(timezone.utc)
    pool = await asyncpg.create_pool(database_url(), min_size=1, max_size=3)
    try:
        async with pool.acquire() as conn:
            await ensure_schema(conn, "market_lab")
            await ensure_schema(conn, "tiger")
            ledger = PostgresLedger(conn)
            await ledger.apply_schema()
            for tenant in (OWNER, ONE):
                await port(ledger, tenant, now)
        store = PgStore(pool)
        await product(
            store, OWNER, now, outcome="miss"
        )  # Current stock still available.
        await product(store, ONE, now, outcome="miss", short=True)
        stage_month = month(now.date(), -4)
        stage = datetime.combine(
            stage_month + timedelta(days=7), datetime.min.time(), timezone.utc
        )
        groups = []
        for outcome in ("hit", "miss", "missing"):
            groups.append(
                await product(
                    store, RESOLVED, stage, anchor=stage_month, suffix=outcome
                )
            )
        async with pool.acquire() as conn:
            ledger = PostgresLedger(conn)
            await port(ledger, RESOLVED, stage)
            before = await run(ledger, RESOLVED, ENGINE, imported=groups, now=stage)
            assert len(before["warnings"]) == 3, before
        # Advance only the external saved observations. Production code uses its real clock.
        for outcome in ("hit", "miss"):
            await product(
                store,
                RESOLVED,
                now,
                anchor=stage_month,
                suffix=outcome,
                outcome=outcome,
            )
    finally:
        await pool.close()
