"""Market Lab: series, gaps, and a prediction ledger. Industry words stay in an engine."""

from __future__ import annotations

import hmac
import logging
import os

from chassis.app import create_app
from chassis.jobs import JobContext
from fastapi import Header, HTTPException, Query

from core.jobs import (
    perform_opportunities,
    perform_refresh,
    perform_research,
    perform_resolve,
)
from core.ledger import PostgresLedger
from engines.ffl.engine import schedules as case_schedules, plan as case_plan

AGENT = os.environ.get("AGENT_NAME", "market_lab")
HEARTBEAT_TENANT = "00000000-0000-0000-0000-000000000001"

log = logging.getLogger("agent")


async def ping(ctx: JobContext) -> dict:
    result: dict = {"pong": True, "agent": ctx.agent}
    if ctx.input.get("heartbeat"):
        try:
            saved = await ctx.memory.remember(
                ctx.tenant_id,
                f"{ctx.agent} heartbeat",
                "heartbeat",
                "schedule:*/5",
            )
            result["heartbeats_seen"] = saved.get("seen")
        except Exception as exc:  # noqa: BLE001 — class name only, never the message
            log.warning("heartbeat memory failed (%s)", type(exc).__name__)
            result["memory"] = "unreachable"
    return result


async def refresh(ctx: JobContext) -> dict:
    fixtures = ctx.input.get("fixtures") or os.environ.get("FEEDS_FIXTURES") or None
    async with _ledger(memory=ctx.memory) as ledger:
        lock = ctx.tenant_id + ":market-collect"
        await ledger.conn.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", lock)
        try:
            result = await perform_refresh(
                tenant_id=ctx.tenant_id,
                engine=str(ctx.input.get("engine") or ""),
                ledger=ledger,
                fixtures=None if not fixtures else str(fixtures),
                live=not fixtures,
                backfill=bool(ctx.input.get("backfill")),
            )
            if result.get('observations_stored', 0):
                result['opportunities'] = await perform_opportunities(
                    tenant_id=ctx.tenant_id, engine=str(ctx.input.get('engine') or ''),
                    ledger=ledger, memory=_SafeMemory(ctx.memory))
            from core.warning_http import refresh as refresh_warnings
            result["warnings"] = await refresh_warnings(ledger, ctx.tenant_id, str(ctx.input.get("engine") or ""))
            return result
        finally:
            await ledger.conn.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", lock)


async def feed_links(ctx: JobContext) -> dict:
    from core.feed_links import refresh_links
    async with _ledger(memory=ctx.memory) as ledger:
        return await refresh_links(ledger, ctx.tenant_id, case_plan()["engine"])


async def opportunities(ctx: JobContext) -> dict:
    async with _ledger(memory=ctx.memory) as ledger:
        result = await perform_opportunities(
            tenant_id=ctx.tenant_id,
            engine=str(ctx.input.get("engine") or ""),
            ledger=ledger,
            areas=ctx.input.get("areas"),
            signals=ctx.input.get("signals"),
            memory=_SafeMemory(ctx.memory),
        )
        from core.warning_http import refresh as refresh_warnings
        await refresh_warnings(ledger, ctx.tenant_id, str(ctx.input.get("engine") or ""))
    return {"top_area": result["top_area"], "top_recommendation": result["top_recommendation"]}


async def resolve(ctx: JobContext) -> dict:
    async with _ledger(memory=ctx.memory) as ledger:
        from core.warning_http import refresh as refresh_warnings
        warning_result = await refresh_warnings(ledger, ctx.tenant_id, str(ctx.input.get("engine") or ""))
        return {**(await perform_resolve(tenant_id=ctx.tenant_id, ledger=ledger)), "warnings": warning_result}


def _has_source_passages(value: object) -> bool:
    return isinstance(value, list) and len(value) > 0


async def research(ctx: JobContext) -> dict:
    question = str(ctx.input.get("question") or "").strip()
    passages = ctx.input.get("passages")
    if not _has_source_passages(passages):
        if not question:
            return {"answer": "no sources yet", "citations": [], "cost_usd": 0}
        if os.environ.get("DATABASE_URL"):
            from core.evidence import answer as cited_answer
            async with _ledger(memory=ctx.memory) as ledger:
                saved = await cited_answer(ledger, ctx.tenant_id, question, engine=case_plan()["engine"])
                facts = saved.get("facts") or []
                if facts:
                    from core.research_records import save as save_research
                    report = await save_research(ledger, ctx.tenant_id, question, saved)
            if facts:
                from core.research_records import remember
                await remember(ctx.memory, report)
                return {'answer': report['answer'], 'citations': report['citations'],
                        'report_id': report['id'], 'receipts': report['receipts'], 'cost_usd': 0}
        from core.research_adapter import research_web

        result = await research_web(ctx.tenant_id, question)
        if result is not None and result.get('facts'):
            from core.research_records import save as save_research, remember
            async with _ledger(memory=ctx.memory) as ledger:
                report = await save_research(ledger, ctx.tenant_id, question, result)
            await remember(ctx.memory, report)
            return {**result, 'answer': report['answer'], 'citations': report['citations'], 'report_id': report['id']}
        return {"answer": "no sources yet", "citations": [], "cost_usd": 0}
    async with _ledger(memory=ctx.memory) as ledger:
        answer = await perform_research(
            tenant_id=ctx.tenant_id,
            ledger=ledger,
            question=question,
            passages=passages,
            model_text=None,
            memory=_SafeMemory(ctx.memory),
        )
    return {"answer": answer["answer"], "citations": answer["citations"]}


async def calibration(ctx: JobContext) -> dict:
    """Data only; a Workspace view requires its own approved Figma frame."""
    async with _ledger(memory=ctx.memory) as ledger:
        return await ledger.calibration_for(ctx.tenant_id)


async def intel(ctx: JobContext) -> dict:
    if ctx.tenant_id != HEARTBEAT_TENANT:
        raise ValueError("tenant_id is required")
    from core.intel import refresh as refresh_intel
    async with _ledger() as ledger:
        return await refresh_intel(ledger)


async def alerts(ctx: JobContext) -> dict:
    from core.ml3 import deliver
    async with _ledger(memory=ctx.memory) as ledger:
        return await deliver(ctx.tenant_id, ledger=ledger)


class _SafeMemory:
    def __init__(self, inner: object) -> None:
        self.inner = inner

    async def remember(self, tenant_id: str, text: str, kind: str, source: str, **kwargs: object) -> dict:
        from core.execution import defer
        if defer(lambda: self.remember(tenant_id, text, kind, source, **kwargs)):
            return {'seen': None}
        try:
            saved = await self.inner.remember(tenant_id, text, kind, source, **kwargs)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — class name only, never the message
            log.warning("memory failed (%s)", type(exc).__name__)
            return {"seen": None}
        return saved if isinstance(saved, dict) else {}


class _ledger:
    def __init__(self, *, memory: object | None = None) -> None:
        self.memory = memory
        self.conn = None
        self.ledger: PostgresLedger | None = None

    async def __aenter__(self) -> PostgresLedger:
        from core.database import connect

        url = os.environ.get("DATABASE_URL", "")
        if not url:
            raise RuntimeError("DATABASE_URL is not set")
        from core.execution import active_ledger
        shared = active_ledger.get()
        if shared is not None:
            self.shared = True
            return shared
        self.conn = await connect(url)
        self.ledger = PostgresLedger(self.conn)
        self.ledger.memory = self.memory if self.memory is not None else getattr(app.state, "memory", None)
        try:
            await self.ledger.apply_schema()
        except BaseException:
            await self.conn.close()
            self.conn = None
            raise
        return self.ledger

    async def __aexit__(self, *_exc: object) -> None:
        if self.conn is not None and not getattr(self, 'shared', False):
            await self.conn.close()


def _room_key_ok(header: str) -> bool:
    key = os.environ.get("TANK_KEY", "")
    try:
        return bool(key) and bool(header) and hmac.compare_digest(key.encode(), header.encode())
    except (AttributeError, TypeError, UnicodeError):
        return False


def _schedules() -> list[dict]:
    rows = [
        {
            "tenant_id": HEARTBEAT_TENANT,
            "cron": "*/5 * * * *",
            "kind": "ping",
            "input": {"heartbeat": True},
        }
    ]
    rows.append({"tenant_id": HEARTBEAT_TENANT, "cron": "15 6 * * *", "kind": "intel", "input": {}})
    rows.extend(case_schedules())
    return rows


app = create_app(
    agent=AGENT,
    handlers={
        "ping": ping,
        "refresh": refresh,
        "feed_links": feed_links,
        "opportunities": opportunities,
        "resolve": resolve,
        "research": research,
        "calibration": calibration,
        "intel": intel,
        "alerts": alerts,
    },
    schedules=_schedules(),
    job_timeouts={"refresh": 86400, "feed_links": 7200, "intel": 86400},
)


@app.get("/gold/candidates")
async def gold_candidates(
    limit: int = Query(default=200, ge=1, le=200),
    x_tank_key: str = Header(default=""),
) -> list[dict]:
    """Room key only. Public feeds on this box's region."""
    if not _room_key_ok(x_tank_key):
        raise HTTPException(status_code=401, detail="room key required")
    try:
        from gold_sources import read_candidates
    except ImportError:
        raise HTTPException(status_code=503, detail="gold evidence unavailable") from None
    try:
        return await read_candidates(limit=limit, region=os.environ.get("TANK_REGION", "us-east"))
    except Exception as exc:  # noqa: BLE001 — class name only; the message can carry the database URL
        log.warning("gold candidates failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="gold evidence unavailable") from None


def _batch_view(record: dict) -> dict:
    return {
        "id": record.get("id") or "",
        "source": record.get("source") or "",
        "region": record.get("region") or "",
        "currency": record.get("currency") or "",
        "unit": record.get("unit") or "",
        "fetched_at": record.get("fetched_at") or "",
        "status": record.get("status") or "",
        "reason": record.get("reason") or "",
    }


def _batch_series_name(record: dict) -> str:
    from feeds.catalog import sources
    from feeds.geo import US_POSTAL_SET, area_label

    if not all(isinstance(record.get(field), str) and record[field].strip() for field in ("ref", "engine")):
        raise ValueError("series reference or engine missing")
    try:
        catalogue = sources(record["engine"])
    except (SystemExit, ModuleNotFoundError):
        raise ValueError("series engine unavailable") from None
    source = next((item for item in catalogue if item.id == record["source"]), None)
    if source is None:
        raise ValueError("series source unavailable")
    # The existing feed adapters store the state's postal code at the end of ref.
    # Use the same catalogue name and place formatter as the source pages.
    area = record["ref"].rsplit(":", 1)[-1]
    return f"{source.name} · {area_label(area, 'state', '')}" if area in US_POSTAL_SET else source.name


@app.get("/quarantine")
async def quarantine_list(x_tank_key: str = Header(default="")) -> dict:
    if not _room_key_ok(x_tank_key):
        raise HTTPException(status_code=401, detail="room key required")
    try:
        async with _ledger() as ledger:
            rows = await ledger.conn.fetch(
                """
                SELECT b.id, b.series_id, b.source, b.fetched_at, b.region, b.currency,
                       b.unit, b.status, b.reason, s.ref, s.engine
                FROM agent_market_lab.batches b
                LEFT JOIN agent_market_lab.series s ON s.id = b.series_id AND s.tenant_id = b.tenant_id
                WHERE b.tenant_id = $1 AND b.status IN ('held', 'pooled', 'rejected', 'removed')
                ORDER BY b.created_at, b.id
                """,
                __import__("uuid").UUID(HEARTBEAT_TENANT),
            )
            batches = []
            for row in rows:
                record = dict(row)
                try:
                    series_name = _batch_series_name(record)
                except ValueError as exc:
                    # Only our safe label-validation reasons, never raw source data.
                    log.warning("quarantine batch %r skipped: %s", record["id"], exc)
                    continue
                batches.append({**_batch_view(record), "series_name": series_name})
    except Exception as exc:  # noqa: BLE001 — class name only; the message can carry the database URL
        log.warning("quarantine list failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="quarantine unavailable") from None
    return {"batches": batches}


async def _batch_action(batch_id: str, action: str, x_tank_key: str) -> dict:
    if not _room_key_ok(x_tank_key):
        raise HTTPException(status_code=401, detail="room key required")
    if not batch_id or len(batch_id) > 80:
        raise HTTPException(status_code=404, detail="not found")
    try:
        async with _ledger() as ledger:
            if action == "release":
                record = await ledger.release_batch(HEARTBEAT_TENANT, batch_id)
            elif action == "reject":
                record = await ledger.reject_batch(HEARTBEAT_TENANT, batch_id)
            else:
                record = await ledger.remove_batch(HEARTBEAT_TENANT, batch_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail="batch was not changed") from exc
    except Exception as exc:  # noqa: BLE001 — class name only; the message can carry the database URL
        log.warning("quarantine action failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="quarantine unavailable") from None
    return _batch_view(record)


@app.post("/quarantine/{batch_id}/release")
async def quarantine_release(batch_id: str, x_tank_key: str = Header(default="")) -> dict:
    return await _batch_action(batch_id, "release", x_tank_key)


@app.post("/quarantine/{batch_id}/reject")
async def quarantine_reject(batch_id: str, x_tank_key: str = Header(default="")) -> dict:
    return await _batch_action(batch_id, "reject", x_tank_key)


@app.post("/quarantine/{batch_id}/remove")
async def quarantine_remove(batch_id: str, x_tank_key: str = Header(default="")) -> dict:
    return await _batch_action(batch_id, "remove", x_tank_key)


from core.http import build_router as market_router

app.include_router(market_router(key_ok=_room_key_ok, ledger_context=_ledger, engine=case_plan()["engine"]))

from core.warning_http import build_router as warning_router

app.include_router(warning_router(key_ok=_room_key_ok, ledger_context=_ledger, engine=case_plan()["engine"]))

from core.document_http import build_router as document_router

app.include_router(document_router(key_ok=_room_key_ok, ledger_context=_ledger))

from core.feed_links import build_router as feed_router

app.include_router(feed_router(key_ok=_room_key_ok, ledger_context=_ledger, engine=case_plan()["engine"]))


from core.ml3 import build_router as ml3_router

app.include_router(ml3_router(key_ok=_room_key_ok, ledger_context=_ledger, engine=case_plan()["engine"]))

from core.readiness import install as install_readiness

install_readiness(app)

from core.execution import guarded

for _kind, _handler in list(app.state.handlers.items()):
    app.state.handlers[_kind] = guarded(_handler, _ledger)
