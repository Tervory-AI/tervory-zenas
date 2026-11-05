"""Local collection, scheduled re-tests and optional private exports.

The background collector reads accepted/released sources and private Rig events.
Rooms keep their own credentials. Exports preserve reviewed content.
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import logging
import json
import os
import re
import sys
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from storage import StorageError, archive_training_snapshot
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from gold import (PLATFORM_TENANT, Reviewer, add_candidate, encoded, input_hash,
                  obj, require_job, require_reviewer, runtime, is_holdout)

log = logging.getLogger("tank.training")

OFFICIAL_ZA_CASE_SOURCES = frozenset({
    "concourt", "sca", "cac", "comptrib", "sars-taxcourt", "landcourt",
    "ocj-highcourt", "labour-court", "labour-appeal", "special-tribunal",
})


def market_row(row: dict[str, Any]) -> bool:
    agent = str(row.get("agent") or "")
    skill = str(row.get("skill_id") or row.get("task_type") or "")
    return agent == "market_lab" or skill.startswith("market.")


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _numeric(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    text = str(value).strip().replace(",", "")
    if not text:
        return False
    try:
        float(text)
    except ValueError:
        return False
    return True


def carries_regional_fact(row: dict[str, Any]) -> bool:
    """A price, a series value, or a currency has to stay labelled."""
    for blob in (row.get("input"), row.get("expected"), row.get("output")):
        for node in _walk(blob):
            if _text_present(node.get("currency")):
                return True
            if "price" in node and _numeric(node.get("price")):
                return True
            if "value" in node and _numeric(node.get("value")) and ("date" in node or "unit" in node):
                return True
    return False


def _text_present(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def regional_labels(row: dict[str, Any]) -> tuple[str, str]:
    currency = str(row.get("currency") or "").strip()
    unit = str(row.get("unit") or "").strip()
    for blob in (row.get("input"), row.get("expected"), row.get("output")):
        for node in _walk(blob):
            if not currency:
                currency = str(node.get("currency") or "").strip()
            if not unit:
                unit = str(node.get("unit") or "").strip()
    return currency, unit


def admits_market_candidate(row: dict[str, Any]) -> bool:
    """A market row enters gold only after its batch has passed quarantine."""
    if not market_row(row):
        return True
    if row.get("quarantine") != "passed" or not str(row.get("batch_id") or "").strip():
        return False
    if row.get("region") not in ("us-east", "za"):
        return False
    currency, unit = regional_labels(row)
    return bool(currency and unit)


def admits_training(row: dict[str, Any]) -> bool:
    """Spot check and quarantine both have to pass before a regional row is trained."""
    if market_row(row) and not admits_market_candidate(row):
        return False
    if market_row(row) and row.get("status") not in (None, "checked"):
        return False
    currency, unit = regional_labels(row)
    if carries_regional_fact(row) and not (currency and unit):
        return False
    return True


async def save_pair(conn, *, tenant: uuid.UUID, agent: str, task: str, value: Any,
                    output: Any, approved_by: str, source: str, ref: str,
                    gold_id: uuid.UUID | None = None, holdout: bool = False,
                    client_matter: bool = False, public: bool = False,
                    region: str | None = None, batch_id: str | None = None,
                    currency: str | None = None, unit: str | None = None,
                    teacher_model: str | None = None) -> None:
    stored_region = region if region in ("us-east", "za") else runtime().box_region()
    await conn.execute(
        """INSERT INTO tank.training_pairs
        (tenant_id,agent,task_type,region,input,output,approved_by,source,source_ref,gold_id,holdout,client_matter,public,batch_id,currency,unit,teacher_model)
        VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        ON CONFLICT (tenant_id,region,source,source_ref) DO NOTHING""",
        tenant, agent, task, stored_region, encoded(value), encoded(output),
        approved_by, source, ref, gold_id, holdout, client_matter, public,
        batch_id or None, currency or None, unit or None, teacher_model,
    )


async def pair_from_gold(conn, row: dict[str, Any]) -> None:
    if row["holdout"] or (row.get("auto_label") and not row.get("auto_approved")):
        return
    if not admits_training(row):
        return
    currency, unit = regional_labels(row)
    teacher = None
    if row.get("source") == "ledger":
        try:
            ledger_id = uuid.UUID(str(row.get("source_ref") or ""))
        except (ValueError, TypeError, AttributeError):
            pass
        else:
            teacher = await conn.fetchval(
                "SELECT model FROM tank.ledger WHERE id=$1 AND tenant_id=$2 AND rung IN (1,2)",
                ledger_id, row["tenant_id"])
    await save_pair(conn, tenant=row["tenant_id"], agent=row["agent"], task=row["skill_id"],
                    value=obj(row["input"]), output=obj(row["expected"]), approved_by=row["checked_by"],
                    source="gold", ref=str(row["id"]), gold_id=row["id"], holdout=row["holdout"],
                    client_matter=row["tenant_id"] != PLATFORM_TENANT,
                    public=row.get("public") is True and row["tenant_id"] == PLATFORM_TENANT,
                    region=row.get("region"), batch_id=row.get("batch_id"),
                    currency=currency or None, unit=unit or None, teacher_model=teacher)


async def collect_ledger(conn, tenant: uuid.UUID) -> None:
    """Client signals are tenant-local training, never automatically reviewed gold."""
    import graduate
    region = runtime().box_region()
    # Late rejection revokes the positive pair, including the previous collector's
    # ledger-derived gold provenance. A negative pair is retained but not exported
    # as a positive completion (active=false, polarity=negative).
    await conn.execute(
        """UPDATE tank.training_pairs p SET active=false
        WHERE p.tenant_id=$1 AND p.region=$2 AND p.source IN ('ledger','client_accepted','gold')
        AND EXISTS (SELECT 1 FROM tank.ledger l WHERE l.tenant_id=$1
          AND (l.id::text=p.source_ref OR EXISTS (SELECT 1 FROM tank.gold_examples g
            WHERE g.id=p.gold_id AND g.tenant_id=$1 AND g.source='ledger' AND g.source_ref=l.id::text))
          AND (l.feedback='rejected' OR l.outcome<>'delivered'))""", tenant, region,
    )
    rows = await conn.fetch(
        """SELECT l.*,t.messages,t.content FROM tank.ledger l
        JOIN tank.transcripts t ON t.ledger_id=l.id AND t.tenant_id=l.tenant_id
        WHERE l.tenant_id=$1 AND l.region=$2 AND l.outcome='delivered' AND l.task_type NOT LIKE '%#shadow'
          -- A response whose mask failed still has a receipt, but the storage
          -- placeholder is not a client answer and must never become training.
          AND jsonb_typeof(t.messages)='array'
          AND t.content NOT IN ('[omitted: masking unavailable]','[omitted: HTML]')
          AND (l.feedback='rejected' OR coalesce(l.accept_after,l.ts+interval '1 day')<=now())
          AND NOT EXISTS (SELECT 1 FROM tank.training_pairs p WHERE p.tenant_id=$1 AND p.region=$2
            AND p.source_ref=l.id::text AND p.source=CASE WHEN l.feedback='rejected'
              THEN 'client_rejected' ELSE 'client_accepted' END)
        ORDER BY l.ts LIMIT 500""", tenant, region,
    )
    for row in rows:
        rejected = row["feedback"] == "rejected"
        value = graduate.skill_payload(obj(row["messages"]))
        output = graduate.as_obj(row["content"]) or {"text": row["content"]}
        inserted = await conn.fetchval(
            """INSERT INTO tank.training_pairs(tenant_id,region,agent,task_type,input,output,
               approved_by,source,source_ref,client_matter,public,active,polarity,teacher_model)
            VALUES($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8,$9,$10,false,$11,$12,$13)
            ON CONFLICT(tenant_id,region,source,source_ref) DO NOTHING RETURNING id""",
            tenant, region, row["agent"], row["task_type"], encoded(value), encoded(output),
            row["feedback_by"] or "client acceptance window", "client_rejected" if rejected else "client_accepted",
            str(row["id"]), row["agent"] == "zenas", not rejected, "negative" if rejected else "positive",
            row["model"] if row.get("rung") in (1, 2) else None,
        )
        if rejected and inserted:
            # Copy the effective policy before changing it: A's rejection never
            # changes B's shadow rate or the platform defaults.
            await conn.execute(
                """INSERT INTO tank.policy SELECT (jsonb_populate_record(NULL::tank.policy,
                to_jsonb(p)||jsonb_build_object('tenant_id',$1::uuid))).*
                FROM tank.policy p WHERE task_type=$2 AND (tenant_id=$1 OR tenant_id IS NULL)
                ORDER BY tenant_id NULLS LAST LIMIT 1 ON CONFLICT DO NOTHING""", tenant, row["task_type"],
            )
            await conn.execute("""UPDATE tank.policy SET shadow_rate=least(1,shadow_rate+0.1),updated_at=now()
                WHERE tenant_id=$1 AND task_type=$2""", tenant, row["task_type"])


async def collect_zenas(conn, tenant: uuid.UUID | None) -> None:
    region = runtime().box_region()
    rows = await conn.fetch(
        """SELECT p.*,r.reviewer FROM agent_zenas.training_pairs p
        JOIN agent_zenas.worked_matters w ON w.id=p.worked_id AND w.region=p.region
          AND w.tenant_id IS NOT DISTINCT FROM p.tenant_id
        LEFT JOIN LATERAL (
          SELECT r.reviewer FROM agent_zenas.reviews r
          JOIN agent_zenas.documents d ON d.id=r.document_id AND d.tenant_id=r.tenant_id
          WHERE d.matter_id=w.released_matter_id AND d.tenant_id=p.tenant_id
          AND d.state='released' AND r.decision='released' ORDER BY r.ts DESC LIMIT 1
        ) r ON true
        WHERE p.tenant_id IS NOT DISTINCT FROM $1::uuid AND p.region=$2
        AND p.source='released' AND w.source='released' AND r.reviewer IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM tank.training_pairs t WHERE t.tenant_id=coalesce($1::uuid,$3)
          AND t.region=$2 AND t.source='zenas' AND t.source_ref=p.id::text)
        ORDER BY p.created_at LIMIT 500""", tenant, region, PLATFORM_TENANT,
    )
    for row in rows:
        value = {"text": row["request"]}
        output = {"findings": obj(row["findings"]), "outcome": row["outcome"],
                  "memo": row["memo"], "lawyer_edits": row["lawyer_edits"]}
        scope = tenant or PLATFORM_TENANT
        # These are legal reasoning examples, not synthetic inputs to an unrelated
        # registered skill. A reviewer can inspect them under zenas.reasoning.
        skill = "zenas.reasoning"
        gold_id = await add_candidate(conn, tenant=scope, skill=skill, agent="zenas", value=value,
                                      expected=output, source="zenas", ref=str(row["id"]))
        from gold import is_holdout
        await save_pair(conn, tenant=scope, agent="zenas", task=skill, value=value, output=output,
                        approved_by=row["reviewer"] or "decided case", source="zenas", ref=str(row["id"]),
                        gold_id=gold_id, holdout=is_holdout(scope, skill, value),
                        client_matter=row["source"] == "released",
                        public=False)


async def collect_fits(conn, tenant: uuid.UUID) -> None:
    rows = await conn.fetch(
        """SELECT * FROM agent_rig.fits WHERE tenant_id=$1 AND approved AND source<>'rejected'""", tenant,
    )
    current = []
    for row in rows:
        value = {"part_sku": row["part_sku"], "fits_sku": row["fits_sku"], "fits_family": row["fits_family"]}
        expected = {"approved": True, "rule": obj(row["rule"]), "note": row["note"]}
        ref = input_hash([value, expected])
        current.append(ref)
        await add_candidate(conn, tenant=tenant, skill="rig.fit", agent="rig", value=value,
                            expected=expected, source="rig", ref=ref)
    await conn.execute(
        """UPDATE tank.gold_examples SET status='rejected'
        WHERE tenant_id=$1 AND region=$2 AND source='rig' AND NOT (source_ref=ANY($3::text[]))""",
        tenant, runtime().box_region(), current,
    )
    await conn.execute(
        """UPDATE tank.training_pairs p SET active=false WHERE p.tenant_id=$1 AND p.region=$2
        AND EXISTS (SELECT 1 FROM tank.gold_examples g WHERE g.id=p.gold_id AND g.tenant_id=$1
          AND g.region=$2 AND g.source='rig' AND g.status='rejected')""", tenant, runtime().box_region(),
    )


async def collect_rig_events(conn, tenant: uuid.UUID) -> None:
    """Drain a tenant's durable room events through the canonical pair writer."""
    region = runtime().box_region()
    rows = await conn.fetch("""SELECT * FROM agent_rig.training_events
        WHERE tenant_id=$1 AND region=$2 ORDER BY created_at LIMIT 500 FOR UPDATE""", tenant, region)
    for row in rows:
        value, output = obj(row["input"]), obj(row["output"])
        currency, _ = regional_labels({"input": value, "output": output})
        await save_pair(conn, tenant=tenant, agent="rig",
                        task="rig.search" if row["source"] == "rig_no_result" else "rig.quote",
                        value=value, output=output, approved_by="system:rig-event",
                        source=row["source"], ref=row["source_ref"], region=region,
                        client_matter=True, public=False, currency=currency, unit="quote" if currency else None)
        await conn.execute("""DELETE FROM agent_rig.training_events
            WHERE tenant_id=$1 AND region=$2 AND source=$3 AND source_ref=$4""",
            tenant, region, row["source"], row["source_ref"])


async def collect_once() -> None:
    rt = runtime()
    async with rt.pool().acquire() as conn:
        if not await conn.fetchval("SELECT pg_try_advisory_lock(871222)"):
            return
        try:
            # Previous case conversion used model-authored fields. Court-text pairs are the
            # only public Zenas rows this collector leaves active now.
            await conn.execute("""UPDATE tank.training_pairs SET active=false
                WHERE region=$1 AND agent='zenas' AND source='zenas' AND public""", rt.box_region())
            if await conn.fetchval("SELECT to_regclass('agent_rig.training_events')"):
                tenants = await conn.fetch("SELECT DISTINCT tenant_id FROM agent_rig.training_events WHERE region=$1", rt.box_region())
                for row in tenants:
                    async with conn.transaction():
                        await collect_rig_events(conn, row["tenant_id"])
            # Tenant discovery is internal maintenance, never a response or export.
            tenants = await conn.fetch("SELECT DISTINCT tenant_id FROM tank.ledger WHERE region=$1", rt.box_region())
            for row in tenants:
                async with conn.transaction():
                    await collect_ledger(conn, row["tenant_id"])
            if await conn.fetchval("SELECT to_regclass('agent_zenas.training_pairs')"):
                tenants = await conn.fetch(
                    "SELECT DISTINCT tenant_id FROM agent_zenas.training_pairs WHERE region=$1", rt.box_region(),
                )
                for row in tenants:
                    async with conn.transaction():
                        await collect_zenas(conn, row["tenant_id"])
            if await conn.fetchval("SELECT to_regclass('agent_rig.fits')"):
                for row in await conn.fetch("SELECT DISTINCT tenant_id FROM agent_rig.fits"):
                    async with conn.transaction():
                        await collect_fits(conn, row["tenant_id"])
        finally:
            await conn.execute("SELECT pg_advisory_unlock(871222)")



ROOMS = ("base", "tiger", "market_lab", "rig", "zenas", "radix", "tisan", "ghost")


GOLD_RESPONSE_MAX_BYTES = 5 * 1024 * 1024
GOLD_ROW_MAX_BYTES = 64 * 1024


def gold_auto_max() -> int:
    raw = os.environ.get("GOLD_AUTO_MAX")
    if raw is None:
        return 200
    try:
        value = int(raw)
        if value > 0:
            return value
    except ValueError:
        pass
    log.warning("Invalid GOLD_AUTO_MAX; using 200")
    return 200


def skill_owners() -> dict[str, set[str]]:
    from skala import load_packages

    owners: dict[str, set[str]] = {}
    for package in load_packages().values():
        owners.setdefault(package.id, set()).add(package.agent)
    # Ghost's incident labels are a learning task, not an executable Skala skill.
    owners.setdefault("ghost.incident", set()).add("ghost")
    return owners


def valid_auto_candidate(row: Any, room: str, region: str,
                         owners: dict[str, set[str]] | None = None) -> bool:
    """Accept public room labels and Ghost's private monthly incident labels."""
    owners = skill_owners() if owners is None else owners
    private_ghost = (isinstance(row, dict) and room == "ghost"
                     and row.get("skill_id") == "ghost.incident" and row.get("public") is False
                     and isinstance(row.get("label_source"), str)
                     and row["label_source"].startswith("ghost:incident:")
                     and isinstance(row.get("label_family"), str)
                     and re.fullmatch(r"ghost:incident:\d{4}-(0[1-9]|1[0-2])", row["label_family"]) is not None)
    return (isinstance(row, dict) and (private_ghost or (room != "ghost" and row.get("public") is True))
            and row.get("client_matter", False) is False
            and row.get("tenant_id") in (None, str(PLATFORM_TENANT))
            and region in ("us-east", "za") and row.get("region") == region
            and isinstance(row.get("skill_id"), str)
            and owners.get(row["skill_id"]) == {room}
            and isinstance(row.get("label_source"), str)
            and 0 < len(row["label_source"]) <= 500
            and (row.get("label_family") is None or
                 (isinstance(row["label_family"], str) and bool(row["label_family"].strip()) and len(row["label_family"]) <= 500))
            and isinstance(row.get("input"), dict) and isinstance(row.get("expected"), dict))


def candidate_json(value: dict) -> str:
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    if len(text.encode("utf-8")) > GOLD_ROW_MAX_BYTES:
        raise ValueError("Candidate row exceeds size limit")
    return text


async def collect_room_gold(day: date) -> dict[str, int]:
    stats = dict(inserted=0, rejected=0, capped=0, duplicates=0, rejected_responses=0, unavailable=0)
    region = runtime().box_region()
    if region not in ("us-east", "za"):
        return stats
    limit = gold_auto_max()
    owners = skill_owners()
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        for room in ROOMS:
            key = os.environ.get(f"TANK_KEY_{room.upper()}", "")
            if not key:
                continue
            host = "agent-" + room.replace("_", "-")
            try:
                # Stream decoded bytes, so a compressed or chunked response is also capped.
                async with client.stream("GET", f"http://{host}:8000/gold/candidates",
                                         params={"limit": min(limit, 200)},
                                         headers={"X-Tank-Key": key}) as response:
                    if response.status_code in (404, 405, 501):
                        continue
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        if len(body) + len(chunk) > GOLD_RESPONSE_MAX_BYTES:
                            raise ValueError("Room response exceeds size limit")
                        body.extend(chunk)
                    rows = json.loads(body)
                    if not isinstance(rows, list):
                        raise ValueError("Room response must be a list")
            except (httpx.HTTPError, ValueError, RecursionError):
                stats["rejected_responses"] += 1
                log.warning("Room gold response rejected: %s", room)
                continue
            stats["rejected"] += max(0, len(rows) - 200)
            try:
                async with runtime().pool().acquire() as conn, conn.transaction():
                    await conn.execute("SELECT pg_advisory_xact_lock(871226)")
                    for row in rows[:200]:
                        try:
                            # Nested transaction is a PostgreSQL savepoint. Catch outside
                            # it so a JSON/database error cannot poison the next insertion.
                            async with conn.transaction():
                                if not valid_auto_candidate(row, room, region, owners):
                                    raise ValueError("Invalid candidate")
                                if not admits_market_candidate(row):
                                    raise ValueError("Invalid candidate")
                                candidate_json(row)
                                value = candidate_json(row["input"])
                                expected = candidate_json(row["expected"])
                                source = row["label_source"]
                                used = await conn.fetchval(
                                    """SELECT count(*) FROM tank.gold_examples
                                    WHERE tenant_id=$1 AND region=$2 AND agent=$3
                                    AND auto_label AND intake_day=$4""",
                                    PLATFORM_TENANT, region, room, day,
                                )
                                if used >= limit:
                                    stats["capped"] += 1
                                    continue
                                result = await conn.execute(
                                    """INSERT INTO tank.gold_examples
                                    (tenant_id,region,skill_id,agent,input,expected,source,source_ref,
                                     input_hash,holdout,auto_label,label_source,public,intake_day,label_family,
                                     batch_id,currency,unit,quarantine)
                                    VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,'room_auto',$7,$8,$9,true,$10,$17,$11,$12,$13,$14,$15,$16)
                                    ON CONFLICT (tenant_id,region,skill_id,source,source_ref) DO NOTHING""",
                                    PLATFORM_TENANT, region, row["skill_id"], room, value, expected,
                                    input_hash([source, row["input"], row["expected"]]), input_hash(row["input"]),
                                    row.get("holdout") is True or is_holdout(PLATFORM_TENANT, row["skill_id"], row["input"]), source, day,
                                    row.get("label_family"), row.get("batch_id"), row.get("currency"),
                                    row.get("unit"), row.get("quarantine"), row["public"],
                                )
                                from gold import settle_auto_source
                                await settle_auto_source(conn, {"tenant_id": PLATFORM_TENANT, "region": region,
                                                               "skill_id": row["skill_id"], "source": "room_auto",
                                                               "label_source": source, "label_family": row.get("label_family")})
                            stats["inserted" if result == "INSERT 0 1" else "duplicates"] += 1
                        except Exception:
                            # No exception text: it may contain private data or credentials.
                            stats["rejected"] += 1
            except Exception:
                stats["unavailable"] += 1
                log.warning("Room gold storage unavailable: %s", room)
    log.info("Room gold intake counts: %s", stats)
    return stats

@asynccontextmanager
async def lifespan(_app):
    tasks = [asyncio.create_task(collector_loop()),
             asyncio.create_task(schedule_loop("export")),
             asyncio.create_task(schedule_loop("gold")),
             asyncio.create_task(schedule_loop("retest"))]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


async def collector_loop() -> None:
    while True:
        try:
            await collect_once()
        except Exception:
            # Never include exception text: database/HTTP exceptions may contain
            # credentials, prompts, or matter data. Retry next cycle.
            log.warning("Training maintenance failed; will retry.")
        await asyncio.sleep(300)


def scheduled_slot(operation: str, now: datetime) -> datetime:
    """Most recent UTC boundary; a restart catches up once, never a backlog."""
    now = now.astimezone(timezone.utc)
    if operation in ("export", "gold"):
        slot = now.replace(hour=2, minute=0, second=0, microsecond=0)
        return slot if slot <= now else slot - timedelta(days=1)
    if operation != "retest":
        raise ValueError("Unknown training operation")
    slot = now.replace(hour=6, minute=0, second=0, microsecond=0)
    slot -= timedelta(days=(now.weekday() + 1) % 7)
    return slot if slot <= now else slot - timedelta(days=7)


async def scheduled_once(operation: str, now: datetime | None = None) -> None:
    slot = scheduled_slot(operation, now or datetime.now(timezone.utc))
    region = runtime().box_region()
    if region not in ("us-east", "za"):
        return
    # A separate session lock per operation covers the completion check and work
    # across workers. The existing platform audit log needs no schema change.
    lock = {"export": 871224, "retest": 871225, "gold": 871227}[operation]
    marker = encoded({"tenant_id": str(PLATFORM_TENANT), "region": region,
                      "operation": operation, "slot": slot.isoformat()})
    async with runtime().pool().acquire() as conn:
        if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", lock):
            return
        try:
            if await conn.fetchval(
                """SELECT 1 FROM tank.policy_log WHERE task_type='training.schedule'
                AND action='complete' AND detail=$1::jsonb LIMIT 1""", marker,
            ):
                return
            if operation == "gold":
                await collect_room_gold(slot.date())
            elif operation == "export":
                await collect_once()
                result = await export_once(day=slot.date())
                if result["status"] != "complete":
                    return  # Missing configuration or a busy exporter can retry.
            else:
                from gold import retest
                await retest(since=slot)
            await conn.execute(
                """INSERT INTO tank.policy_log(task_type,action,by_user,detail)
                VALUES ('training.schedule','complete','tank',$1::jsonb)""", marker,
            )
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", lock)


async def schedule_loop(operation: str) -> None:
    while True:
        try:
            await scheduled_once(operation)
        except Exception:
            log.warning("Scheduled training maintenance failed; will retry.")
        await asyncio.sleep(60)


router = APIRouter(prefix="/v1/training", lifespan=lifespan)

@router.get("/teachers")
async def teachers(skill_id: str = Query(min_length=1, max_length=100,
                                       pattern=r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+$"),
                   actor: Reviewer = Depends(require_reviewer)) -> dict[str, Any]:
    """Founder-only tenant-local aggregates; pair content never leaves this query."""
    if not actor.founder:
        raise HTTPException(404, "not found")
    from command_tools.skala import TeachersArgs
    from trigger_routes import code_receipt
    args = TeachersArgs(skill_id=skill_id)
    async with runtime().pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT s.version,s.task_type,p.teacher_model,count(p.id) AS count,max(p.created_at) AS newest_created_at
            FROM tank.skills s
            LEFT JOIN tank.agent_skill_submissions owner ON owner.skill_id=s.id AND owner.version=s.version
            LEFT JOIN tank.training_pairs p
              ON p.tenant_id=$2 AND p.region=$3 AND p.agent=s.agent
              AND (p.task_type=s.task_type OR p.task_type=s.id)
              AND p.active AND p.polarity='positive'
            WHERE s.id=$1 AND (owner.skill_id IS NULL OR (owner.tenant_id=$2 AND owner.region=$3))
            GROUP BY s.version,s.task_type,p.teacher_model
            ORDER BY s.version,p.teacher_model NULLS FIRST""", args.skill_id, actor.tenant, runtime().box_region())
        ledger_id = await code_receipt(actor.tenant, "skala.teachers", agent="tank", conn=conn)
    return {"name": args.skill_id, "count": len(rows),
            "items": [{"name": row["version"], "task_type": row["task_type"],
                       **({"model": row["teacher_model"]} if row["teacher_model"] is not None else {}),
                       "teacher_model": row["teacher_model"], "count": row["count"],
                       "newest_created_at": row["newest_created_at"].isoformat() if row["newest_created_at"] else None}
                      for row in rows], "ledger_id": str(ledger_id)}



class CourtTextPair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=80)
    document_id: str = Field(min_length=1, max_length=300)
    version_id: str = Field(min_length=1, max_length=300)
    status: str = Field(pattern="^(split|excluded)$")
    reason: str = Field(default="", max_length=40)
    input: dict[str, str] | None = None
    output: dict[str, Any] | None = None


class TrainingRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str = Field(pattern="^zenas$")
    minutes: int = Field(ge=1, le=240)


class TrainingReceiptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
    billed_usd: Decimal = Field(ge=0)


async def _training_readiness(conn: Any, body: TrainingRunRequest) -> dict[str, Any] | None:
    ready = await conn.fetchval(
        """SELECT count(*) FROM tank.training_pairs WHERE tenant_id=$1 AND agent=$2
        AND active AND public AND NOT client_matter""",
        PLATFORM_TENANT, body.agent,
    )
    holdout = await conn.fetchval(
        """SELECT count(*) FROM tank.training_pairs WHERE tenant_id=$1 AND agent=$2
        AND active AND public AND NOT client_matter AND holdout""",
        PLATFORM_TENANT, body.agent,
    )
    if int(ready or 0) < 2000:
        return {"status": "fewer than 2,000 public zenas training rows", "count": int(ready or 0)}
    if int(holdout or 0) < 200:
        return {"status": "fewer than 200 holdout rows", "count": int(holdout or 0)}
    if await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM tank.training_jobs WHERE tenant_id=$1 AND ledger_id IS NULL)",
        PLATFORM_TENANT,
    ):
        return {"status": "reconcile the previous training job"}
    if not os.environ.get("HF_TOKEN") or not os.environ.get("HF_ORG"):
        return {"status": "Hugging Face training is not configured"}
    return None


async def _training_quote(body: TrainingRunRequest) -> dict[str, Any]:
    return await _training_subprocess("--agent", body.agent, "--minutes", str(body.minutes))


async def _training_subprocess(*arguments: str) -> dict[str, Any]:
    """Run the narrowly scoped HF client without changing Tank's offline environment."""
    child_env = os.environ.copy()
    child_env["HF_HUB_OFFLINE"] = "0"
    script = Path(__file__).with_name("training_jobs") / "run.py"
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(script), *arguments, "--json",
        env=child_env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return {"status": "Training operation failed; check configuration, budget and provenance."}
    try:
        return json.loads(stdout.decode().strip().splitlines()[-1])
    except (IndexError, UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "Training operation returned an invalid result."}


@router.post("/run/plan")
async def training_run_plan(
    body: TrainingRunRequest, actor: Reviewer = Depends(require_reviewer),
) -> dict[str, Any]:
    if not actor.founder:
        raise HTTPException(403, "Founder access required")
    async with runtime().pool().acquire() as conn:
        refusal = await _training_readiness(conn, body)
    return refusal or await _training_quote(body)


@router.post("/run")
async def training_run(
    body: TrainingRunRequest, actor: Reviewer = Depends(require_reviewer),
) -> dict[str, Any]:
    if not actor.founder:
        raise HTTPException(403, "Founder access required")
    async with runtime().pool().acquire() as conn:
        refusal = await _training_readiness(conn, body)
    if refusal:
        return refusal
    quote = await _training_quote(body)
    if quote.get("status") != "ready":
        return quote
    return await _training_subprocess(
        "--agent", body.agent, "--minutes", str(body.minutes), "--submit",
        "--day", datetime.now(timezone.utc).date().isoformat(),
    )


@router.post("/receipt")
async def training_receipt(
    body: TrainingReceiptRequest, actor: Reviewer = Depends(require_reviewer),
) -> dict[str, Any]:
    if not actor.founder:
        raise HTTPException(403, "Founder access required")
    if not os.environ.get("HF_TOKEN") or not os.environ.get("HF_ORG"):
        return {"status": "Hugging Face training is not configured"}
    return await _training_subprocess(
        "--job-id", body.job_id, "--billed-usd", str(body.billed_usd),
    )


@router.post("/zenas/court-text")
async def save_court_text_pair(body: CourtTextPair, request: Request) -> dict[str, bool]:
    """Accept only code-split official court text from the regional Zenas room."""
    supplied = request.headers.get("x-tank-key", "")
    # The scheduled splitter runs in the Zenas room, which never holds the
    # Workspace key. Only that owning room may submit official court splits.
    expected = os.environ.get("TANK_KEY_ZENAS", "").strip()
    if not supplied or not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "Room authentication required")
    if runtime().box_region() != "za" or body.source_id not in OFFICIAL_ZA_CASE_SOURCES:
        raise HTTPException(403, "Not in scope")
    if body.status == "split" and (body.input is None or body.output is None or body.reason):
        raise HTTPException(422, "Invalid court text split")
    if body.status == "excluded" and (body.input is not None or body.output is not None
                                      or body.reason not in {"empty", "criminal", "headings", "order"}):
        raise HTTPException(422, "Invalid court text exclusion")
    ref = f"{body.source_id}:{body.version_id}"
    from tenant_db import tenant_scope

    # Platform-owned public data still needs an explicit transaction-local RLS
    # tenant. No body field can choose a client tenant or an admin connection.
    with tenant_scope(PLATFORM_TENANT):
        async with runtime().pool().acquire() as conn:
            async with conn.transaction():
                if body.status == "split":
                    value, output = dict(body.input or {}), dict(body.output or {})
                    await save_pair(
                        conn, tenant=PLATFORM_TENANT, agent="zenas", task="zenas.reasoning",
                        value=value, output=output, approved_by="court decision", source="court_text",
                        ref=ref, holdout=is_holdout(PLATFORM_TENANT, "zenas.reasoning", value),
                        client_matter=False, public=True, region="za",
                    )
                await conn.execute(
                    """INSERT INTO tank.judgment_splits(region,source_id,document_id,version_id,status,reason)
                    VALUES('za',$1,$2,$3,$4,$5) ON CONFLICT(region,version_id) DO NOTHING""",
                    body.source_id, body.document_id, body.version_id, body.status, body.reason,
                )
    return {"ok": True}


@router.get("/status")
async def training_status(actor: Reviewer = Depends(require_reviewer)) -> dict[str, Any]:
    if not actor.founder:
        raise HTTPException(403, "Founder access required")
    region = runtime().box_region()
    items: list[dict[str, Any]] = []
    async with runtime().pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT agent,
              count(*) FILTER (WHERE active AND public AND NOT client_matter AND NOT holdout) AS ready,
              count(*) FILTER (WHERE active AND public AND NOT client_matter AND holdout) AS holdout
            FROM tank.training_pairs WHERE tenant_id=$1 AND region=$2 GROUP BY agent ORDER BY agent""",
            PLATFORM_TENANT, region,
        )
        exports = {row["agent"]: row for row in await conn.fetch(
            """SELECT DISTINCT ON (agent) agent,day,exported_at FROM tank.training_exports
            WHERE region=$1 ORDER BY agent,day DESC""", region,
        )}
        for row in rows:
            export = exports.get(row["agent"])
            items.extend((
                {"name": f"{row['agent']} pairs ready", "count": int(row["ready"]),
                 "status": "ready"},
                {"name": f"{row['agent']} holdout", "count": int(row["holdout"]),
                 "status": "held out"},
                {"name": f"{row['agent']} export", "count": int(row["ready"]),
                 "status": f"complete {export['day']}" if export else "not exported"},
            ))
        job = await conn.fetchrow(
            """SELECT id,model_repo,estimated_usd,actual_usd,created_at FROM tank.training_jobs
            WHERE tenant_id=$1 AND region=$2 ORDER BY created_at DESC LIMIT 1""", PLATFORM_TENANT, region,
        )
        if job:
            items.append({"name": str(job["model_repo"]), "count": 1,
                          "status": "complete" if job["actual_usd"] is not None else "running",
                          "cost_usd": job["actual_usd"] if job["actual_usd"] is not None else job["estimated_usd"]})
        evaluation = await conn.fetchrow(
            """SELECT count(*) AS n,avg(agreement) AS agreement FROM tank.shadow
            WHERE task_type='zenas.reasoning' AND candidate_model='tervory-own-zenas-za'
              AND method='exact_outcome'""",
        )
        if evaluation and int(evaluation["n"] or 0):
            agreement = float(evaluation["agreement"] or 0)
            items.append({"name": "Zenas own model holdout", "count": int(evaluation["n"]),
                          "status": f"{agreement:.2%} agreement"})
        if region == "za":
            stored = {row["source_id"]: int(row["count"]) for row in await conn.fetch(
                """SELECT source_id,count(DISTINCT document_id) AS count FROM zenas_pool.documents
                WHERE jurisdiction='za' AND kind='case' AND effective_to IS NULL
                  AND status IS DISTINCT FROM 'superseded' AND source_id=ANY($1::text[])
                GROUP BY source_id""", sorted(OFFICIAL_ZA_CASE_SOURCES),
            )}
            split_rows = await conn.fetch(
                """SELECT source_id,status,count(*) AS count FROM tank.judgment_splits
                WHERE region='za' GROUP BY source_id,status""",
            )
            split = {(row["source_id"], row["status"]): int(row["count"]) for row in split_rows}
            pairs = {row["source_id"]: int(row["count"]) for row in await conn.fetch(
                """SELECT split_part(source_ref,':',1) AS source_id,count(*) AS count
                FROM tank.training_pairs WHERE tenant_id=$1 AND region='za' AND agent='zenas'
                  AND source='court_text' AND active AND public AND NOT client_matter AND NOT holdout
                GROUP BY split_part(source_ref,':',1)""", PLATFORM_TENANT,
            )}
            for source in sorted(OFFICIAL_ZA_CASE_SOURCES):
                items.extend((
                    {"name": f"{source} judgments stored", "count": stored.get(source, 0), "status": "stored"},
                    {"name": f"{source} split", "count": split.get((source, "split"), 0), "status": "split"},
                    {"name": f"{source} excluded", "count": split.get((source, "excluded"), 0), "status": "excluded"},
                    {"name": f"{source} pairs ready", "count": pairs.get(source, 0), "status": "ready"},
                ))
    return {"items": items}


@router.post("/tiger/{observation_id}/confirm")
async def confirm_tiger(observation_id: uuid.UUID, actor: Reviewer = Depends(require_reviewer)) -> dict[str, bool]:
    if not actor.founder:
        raise HTTPException(404, "not found")
    async with runtime().pool().acquire() as conn:
        if not await conn.fetchval("SELECT to_regclass('agent_tiger.observations')"):
            raise HTTPException(404, "not found")
        row = await conn.fetchrow(
            "SELECT * FROM agent_tiger.observations WHERE id=$1 AND tenant_id=$2", observation_id, actor.tenant,
        )
        if row is None or row["price"] is None or row.get("method") == "private_portal":
            raise HTTPException(404, "not found")
        await add_candidate(conn, tenant=actor.tenant, skill="tiger.price_read", agent="tiger",
                            value={"url": row["url"], "evidence": row["evidence"]},
                            expected={"price": str(row["price"]), "currency": row["currency"],
                                      "availability": row["availability"]},
                            source="tiger_confirmed", ref=str(observation_id))
    return {"ok": True}


def exportable(row: dict[str, Any]) -> bool:
    return (row.get("region") in ("us-east", "za") and row.get("region") == runtime().box_region()
            and row.get("holdout") is False and row.get("active") is True
            and row.get("public") is True and row.get("client_matter") is False
            and row.get("tenant_id") == PLATFORM_TENANT)


async def upload_dataset(client: httpx.AsyncClient, org: str, agent: str, day: str, rows: list[dict], region: str = "us-east",
                         holdouts: list[dict] | None = None) -> None:
    # Validate again at the network boundary, before any request is sent.
    if region not in ("us-east", "za") or region != runtime().box_region() or any(
        row.get("scope") != "platform" or "tenant_id" in row or row.get("region") != region
        or not exportable({**row, "tenant_id": PLATFORM_TENANT}) for row in rows
    ):
        raise ValueError("Unsafe training export")
    if any(row.get("scope") != "platform" or "tenant_id" in row or row.get("region") != region
           or row.get("holdout") is not True or row.get("active") is not True
           or row.get("public") is not True or row.get("client_matter") is not False
           for row in (holdouts or [])):
        raise ValueError("Unsafe holdout export")
    data = "\n".join(encoded(row) for row in rows) + "\n"
    await archive_training_snapshot(agent, day, data.encode(), region=region)
    name = f"tervory-public-{agent}"
    created = await client.post("https://huggingface.co/api/repos/create",
                                json={"type": "dataset", "name": name, "organization": org, "private": True})
    if created.status_code not in (200, 201, 409):
        raise RuntimeError("Private dataset creation failed")
    repo = f"{org}/{name}"
    info = await client.get(f"https://huggingface.co/api/datasets/{repo}")
    if info.status_code != 200 or info.json().get("private") is not True:
        raise RuntimeError("Dataset is not private")
    # Native Hub commit API, no new runtime dependency; a date is a complete snapshot.
    operations = [
        {"key": "header", "value": {"summary": f"Training snapshot {day}"}},
        {"key": "file", "value": {"path": f"snapshots/{region}/{day}.jsonl", "encoding": "base64",
                                  "content": base64.b64encode(data.encode()).decode()}},
    ]
    if holdouts:
        held = "\n".join(encoded(row) for row in holdouts) + "\n"
        operations.append(
            {"key": "file", "value": {"path": f"holdouts/{region}/{day}.jsonl", "encoding": "base64",
                                        "content": base64.b64encode(held.encode()).decode()}},
        )
    response = await client.post(f"https://huggingface.co/api/datasets/{repo}/commit/main",
                                 content="\n".join(encoded(op) for op in operations),
                                 headers={"Content-Type": "application/x-ndjson"})
    if response.status_code not in (200, 201):
        raise RuntimeError("Private dataset commit failed")


async def export_once(*, day: date | None = None) -> dict[str, Any]:
    token, org = os.environ.get("HF_TOKEN", ""), os.environ.get("HF_ORG", "")
    if not token or not org:
        return {"status": "skipped", "reason": "not configured"}
    region = runtime().box_region()
    if region not in ("us-east", "za"):
        return {"status": "skipped", "reason": "unknown region"}
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", org):
        return {"status": "skipped", "reason": "invalid organisation"}
    day = day or datetime.now(timezone.utc).date()
    exported = 0
    incomplete = False
    async with runtime().pool().acquire() as conn:
        if not await conn.fetchval("SELECT pg_try_advisory_lock(871223)"):
            return {"status": "skipped", "reason": "already running"}
        try:
            from gold import AGENTS
            async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60,
                                         follow_redirects=False) as client:
                for agent in AGENTS:
                    done = await conn.fetchval(
                        "SELECT 1 FROM tank.training_exports WHERE region=$3 AND day=$1 AND agent=$2", day, agent, region,
                    )
                    if done:
                        continue
                    payload = []
                    holdouts = []
                    scopes = await conn.fetch(
                        "SELECT DISTINCT tenant_id FROM tank.training_pairs WHERE region=$2 AND agent=$1 AND tenant_id=$3 AND public AND NOT client_matter", agent, region, PLATFORM_TENANT,
                    )
                    for scope in scopes:
                        rows = await conn.fetch(
                            """SELECT p.* FROM tank.training_pairs p
                            WHERE p.tenant_id=$1 AND p.region=$3 AND p.agent=$2 AND p.active AND NOT p.holdout AND p.public AND NOT p.client_matter
                            AND NOT EXISTS (SELECT 1 FROM tank.gold_examples g WHERE g.tenant_id=p.tenant_id
                              AND g.region=p.region AND g.input=p.input AND (g.holdout OR (g.status='rejected' AND g.id=p.gold_id)))""",
                            scope["tenant_id"], agent, region,
                        )
                        for row in rows:
                            if exportable(dict(row)) and not await _batch_removed(conn, scope["tenant_id"], row["batch_id"]):
                                # No tenant, reviewer identity or matter reference leaves the box.
                                item = {"region": region, "public": True, "client_matter": False,
                                        "scope": "platform", "holdout": False, "active": True, "task_type": row["task_type"], "input": obj(row["input"]),
                                        "output": obj(row["output"])}
                                if row["batch_id"]:
                                    item["batch_id"] = row["batch_id"]
                                if row["currency"]:
                                    item["currency"] = row["currency"]
                                if row["unit"]:
                                    item["unit"] = row["unit"]
                                payload.append(item)
                        held_rows = await conn.fetch(
                            """SELECT p.* FROM tank.training_pairs p
                            WHERE p.tenant_id=$1 AND p.region=$3 AND p.agent=$2 AND p.active AND p.holdout
                              AND p.public AND NOT p.client_matter""",
                            scope["tenant_id"], agent, region,
                        )
                        for row in held_rows:
                            if row["tenant_id"] == PLATFORM_TENANT and not await _batch_removed(conn, scope["tenant_id"], row["batch_id"]):
                                holdouts.append({"region": region, "public": True, "client_matter": False,
                                                 "scope": "platform", "holdout": True, "active": True,
                                                 "task_type": row["task_type"], "input": obj(row["input"]),
                                                 "output": obj(row["output"])})
                    if not payload:
                        continue
                    try:
                        await upload_dataset(client, org, agent, day.isoformat(), payload, region, holdouts)
                    except StorageError:
                        # Leave this agent unrecorded so the slot retries it,
                        # while independent rooms can still finish their exports.
                        incomplete = True
                        log.warning("Training archive unavailable for agent: %s", agent)
                        continue
                    for item in payload:
                        if item.get("batch_id"):
                            await conn.execute(
                                """INSERT INTO tank.training_snapshot_batches(tenant_id,region,day,agent,batch_id)
                                VALUES ($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING""",
                                PLATFORM_TENANT, region, day, agent, item["batch_id"],
                            )
                    await conn.execute(
                        """INSERT INTO tank.training_exports(region,day,agent) VALUES ($3,$1,$2)
                        ON CONFLICT DO NOTHING""", day, agent, region,
                    )
                    exported += len(payload)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(871223)")
    return {"status": "incomplete" if incomplete else "complete", "exported": exported}


async def _batch_removed(conn, tenant: uuid.UUID, batch_id: str | None) -> bool:
    if not batch_id:
        return False
    return bool(await conn.fetchval(
        "SELECT 1 FROM tank.removed_batches WHERE tenant_id=$1 AND batch_id=$2", tenant, batch_id,
    ))


async def models_trained_on(conn, tenant: uuid.UUID, batch_id: str) -> list[str]:
    rows = await conn.fetch(
        """SELECT DISTINCT model_repo FROM tank.training_job_batches
        WHERE tenant_id=$1 AND batch_id=$2 ORDER BY model_repo""",
        tenant, batch_id,
    )
    return [row["model_repo"] for row in rows]


async def retire_batch(conn, tenant: uuid.UUID, batch_id: str) -> list[str]:
    """Drop a batch from training and name every model that already used it."""
    await conn.execute(
        """INSERT INTO tank.removed_batches (tenant_id, batch_id) VALUES ($1, $2)
        ON CONFLICT DO NOTHING""",
        tenant, batch_id,
    )
    await conn.execute(
        """UPDATE tank.gold_examples SET status='rejected', note='Batch removed'
        WHERE tenant_id=$1 AND batch_id=$2 AND status <> 'rejected'""",
        tenant, batch_id,
    )
    await conn.execute(
        """UPDATE tank.training_pairs SET active=false
        WHERE tenant_id=$1 AND batch_id=$2""",
        tenant, batch_id,
    )
    return await models_trained_on(conn, tenant, batch_id)


async def record_job_batches(conn, *, tenant: uuid.UUID, job_id: str, model_repo: str, day: date) -> None:
    rows = await conn.fetch(
        """SELECT DISTINCT batch_id, region FROM tank.training_snapshot_batches
        WHERE tenant_id=$1 AND day=$2""",
        tenant, day,
    )
    for row in rows:
        await conn.execute(
            """INSERT INTO tank.training_job_batches (tenant_id, job_id, model_repo, batch_id, region)
            VALUES ($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING""",
            tenant, job_id, model_repo, row["batch_id"], row["region"],
        )


_BATCH_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


@router.get("/batches/{batch_id}/models")
async def list_batch_models(batch_id: str, actor: Reviewer = Depends(require_reviewer)) -> dict[str, Any]:
    if not actor.founder or _BATCH_ID.fullmatch(batch_id) is None:
        raise HTTPException(404, "not found")
    async with runtime().pool().acquire() as conn:
        return {"models": await models_trained_on(conn, actor.tenant, batch_id)}


@router.post("/batches/{batch_id}/remove")
async def remove_training_batch(batch_id: str, actor: Reviewer = Depends(require_reviewer)) -> dict[str, Any]:
    if not actor.founder or _BATCH_ID.fullmatch(batch_id) is None:
        raise HTTPException(404, "not found")
    async with runtime().pool().acquire() as conn:
        models = await retire_batch(conn, actor.tenant, batch_id)
    return {"removed": True, "models": models}


@router.post("/jobs/export", dependencies=[Depends(require_job)])
async def export_job() -> dict[str, Any]:
    await collect_once()
    return await export_once()
