"""Tenant-owned RSS subscriptions using the room's existing scheduler/ledger."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from feeds.keyless import check_feed_url
from feeds.models import SourceSpec


def source_for(row) -> SourceSpec:
    return SourceSpec(
        id="rss_" + str(row["id"]), name=row["label"] or row["url"], kind="events", url=row["url"],
        licence="Publisher-supplied public RSS/Atom titles, links and excerpts; publisher retains rights.",
        terms_checked_at="2026-10-28", cadence=row["cadence"], enabled=True, allow_bulk=True,
        adapter="rss", config={"files": ["feed.xml"]},
    )


async def subscriptions(ledger, tenant_id: str) -> list[dict]:
    if not hasattr(ledger, "conn"):
        return []  # Existing in-memory ledgers have no subscription store.
    rows = await ledger.conn.fetch("""SELECT id,url,label,cadence,last_checked,status
        FROM agent_market_lab.feed_links WHERE tenant_id=$1 ORDER BY id""", UUID(str(tenant_id)))
    return [dict(row) for row in rows]


async def refresh_links(ledger, tenant_id: str, engine: str, *, now=None, client=None, fixtures=None) -> dict:
    from core.jobs import perform_refresh
    from feeds.collect import EvidenceClient

    owner = UUID(str(tenant_id))
    clock = now or datetime.now(timezone.utc)
    http = client or EvidenceClient(public_feeds=True, tenant_id=str(owner))
    checks = []
    for link in await subscriptions(ledger, str(owner)):
        # A concurrent removal waits for this one check and then deletes its
        # evidence. A queued job cannot resurrect a removed subscription.
        async with ledger.conn.transaction():
            row = await ledger.conn.fetchrow("""SELECT * FROM agent_market_lab.feed_links
                WHERE tenant_id=$1 AND id=$2 AND next_check <= $3 FOR UPDATE""", owner, link["id"], clock)
            if row is None:
                continue
            result = await perform_refresh(tenant_id=str(owner), engine=engine, ledger=ledger,
                extra_sources=[source_for(row)], live=not fixtures, fixtures=fixtures, now=clock, client=http)
            check = result["feeds"][0]
            status = "failed" if check["note"].startswith("Off, ") and check["note"] != "Off, the source returned no observations" else "checked"
            await ledger.conn.execute("""UPDATE agent_market_lab.feed_links
                SET last_checked=$3,next_check=$4,status=$5 WHERE tenant_id=$1 AND id=$2""",
                owner, row["id"], clock, clock + timedelta(days=7 if row["cadence"] == "weekly" else 1), status)
            checks.append(check)
    return {"feeds": checks}


class LinkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2000)
    label: str = Field(default="", max_length=500)
    frequency: Literal["daily", "weekly"]


def build_router(*, key_ok, ledger_context, engine: str):
    router = APIRouter()

    def authorize(key, request):
        if not key_ok(key):
            raise HTTPException(401, "Room key required")
        if request.query_params.get("region") != os.environ.get("TANK_REGION", "us-east"):
            raise HTTPException(403, "This request must stay on its home server.")

    @router.get("/market/feeds")
    async def listing(tenant_id: UUID, request: Request, x_tank_key: str = Header(default="")):
        authorize(x_tank_key, request)
        async with ledger_context() as ledger:
            rows = await subscriptions(ledger, str(tenant_id))
        return {"links": [{"id": str(row["id"]), "url": row["url"], "label": row["label"],
                           "frequency": row["cadence"], "status": row["status"],
                           "last_checked": row["last_checked"].isoformat() if row["last_checked"] else None}
                          for row in rows]}

    @router.put("/market/feeds/{ident}")
    async def subscribe(ident: UUID, tenant_id: UUID, body: LinkBody, request: Request,
                        x_tank_key: str = Header(default="")):
        from chassis.db import ensure_schedules
        authorize(x_tank_key, request)
        try:
            url = await asyncio.to_thread(check_feed_url, body.url)
        except (ValueError, OSError):
            raise HTTPException(422, "Check the fields and try again.") from None
        async with ledger_context() as ledger, ledger.conn.transaction():
            await ledger.conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", str(tenant_id) + ":feed-links")
            other = await ledger.conn.fetchval("""SELECT id FROM agent_market_lab.feed_links
                WHERE tenant_id=$1 AND url=$2 AND id<>$3""", tenant_id, url, ident)
            count = await ledger.conn.fetchval("SELECT count(*) FROM agent_market_lab.feed_links WHERE tenant_id=$1", tenant_id)
            if other or count >= 100:
                raise HTTPException(422, "Check the fields and try again.")
            await ledger.conn.execute("""INSERT INTO agent_market_lab.feed_links(tenant_id,id,url,label,cadence)
                VALUES($1,$2,$3,$4,$5) ON CONFLICT(tenant_id,id) DO UPDATE
                SET url=excluded.url,label=excluded.label,cadence=excluded.cadence,next_check=now()""",
                tenant_id, ident, url, body.label.strip(), body.frequency)
            await ensure_schedules(ledger.conn, "agent_market_lab", [{"tenant_id": str(tenant_id),
                "cron": "*/15 * * * *", "kind": "feed_links", "input": {"engine": engine}}])
            await ledger.conn.execute("""INSERT INTO agent_market_lab.jobs(tenant_id,kind,input,status,run_at,timeout_s)
                VALUES($1,'feed_links','{}'::jsonb,'queued',now(),7200)""", tenant_id)
        return {"ok": True}

    @router.delete("/market/feeds/{ident}")
    async def remove(ident: UUID, tenant_id: UUID, request: Request, x_tank_key: str = Header(default="")):
        authorize(x_tank_key, request)
        async with ledger_context() as ledger, ledger.conn.transaction():
            await ledger.conn.fetchrow("SELECT id FROM agent_market_lab.feed_links WHERE tenant_id=$1 AND id=$2 FOR UPDATE", tenant_id, ident)
            source_id = "rss_" + str(ident)
            await ledger.conn.execute("DELETE FROM agent_market_lab.feed_checks WHERE tenant_id=$1 AND source_id=$2", tenant_id, source_id)
            await ledger.conn.execute("DELETE FROM agent_market_lab.feed_documents WHERE tenant_id=$1 AND source_id=$2", tenant_id, source_id)
            await ledger.conn.execute("DELETE FROM agent_market_lab.feed_links WHERE tenant_id=$1 AND id=$2", tenant_id, ident)
        return {"ok": True}

    return router
