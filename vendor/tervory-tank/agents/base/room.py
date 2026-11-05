"""Base's tables and the batch worker. Every client read is scoped by tenant_id."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any

from chassis.events import publish
from chassis.job_contract import Job
from chassis.tenant_db import tenant_scope

from pipeline import (
    OPTIONAL_SKILLS,
    SkillCall,
    enrich_queued,
    prepare_batch,
    result_line,
    skill_ids,
)

log = logging.getLogger("agent.base")

SCHEMA = "agent_base"
_MODEL_PAUSE_S = 600
_model_pause = {"until": 0.0, "reason": ""}
_wake = asyncio.Event()

RECORDS_SQL = """
CREATE TABLE IF NOT EXISTS agent_base.records (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    task_id         uuid,
    batch           text,
    raw             jsonb NOT NULL,
    profile         jsonb NOT NULL DEFAULT '{}'::jsonb,
    passes          jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence      smallint NOT NULL DEFAULT 0,
    status          text NOT NULL DEFAULT 'queued',
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
)
"""

TASKS_SQL = """
CREATE TABLE IF NOT EXISTS agent_base.tasks (
    id              uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL,
    created_by      text,
    agent           text NOT NULL DEFAULT 'base',
    task_type       text NOT NULL DEFAULT 'base.enrich',
    title           text NOT NULL,
    input           jsonb NOT NULL DEFAULT '{}'::jsonb,
    status          text NOT NULL DEFAULT 'queued',
    result          text,
    error           text,
    ledger_id       uuid,
    created_at      timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    finished_at     timestamptz
)
"""

SQL_RECORDS_BY_TENANT = (
    "SELECT id, task_id, batch, raw, profile, passes, confidence, status, error, created_at, updated_at "
    "FROM agent_base.records WHERE tenant_id=$1 AND ($2::uuid IS NULL OR task_id=$2) "
    "AND ($3::text IS NULL OR batch=$3) ORDER BY created_at DESC LIMIT $4 OFFSET $5"
)
SQL_BATCHES_BY_TENANT = """
SELECT batch, min(created_at) AS started, count(*) AS total,
       count(*) FILTER (WHERE status='done') AS done,
       count(*) FILTER (WHERE status='failed') AS failed,
       count(*) FILTER (WHERE status IN ('queued','running')) AS pending,
       count(*) FILTER (WHERE status='done' AND confidence >= 80) AS ready,
       count(*) FILTER (WHERE status='done' AND confidence BETWEEN 60 AND 79) AS aware,
       count(*) FILTER (WHERE status='done' AND confidence BETWEEN 40 AND 59) AS review,
       count(*) FILTER (WHERE status='done' AND confidence < 40) AS requeue,
       count(*) FILTER (WHERE status='done' AND coalesce(profile->'website'->>'value','') <> '') AS with_site,
       count(*) FILTER (WHERE status='done' AND jsonb_typeof(profile->'emails'->'value') = 'array'
                                            AND jsonb_array_length(profile->'emails'->'value') > 0) AS with_email,
       count(*) FILTER (WHERE status='done' AND (passes->'2c' ? 'skipped' OR passes->'5' ? 'skipped')) AS model_skipped
FROM agent_base.records WHERE tenant_id=$1
GROUP BY batch ORDER BY started DESC LIMIT 200
"""
SQL_RETRY = """
WITH r AS (UPDATE agent_base.records SET status='queued', error=NULL, updated_at=now()
           WHERE tenant_id=$1 AND batch=$2 AND status='failed' RETURNING task_id)
SELECT count(*) FROM r
"""
SQL_REQUEUE_TASKS = """
UPDATE agent_base.tasks SET status='queued', finished_at=NULL
WHERE tenant_id=$1 AND task_type='base.enrich' AND status IN ('done','failed')
  AND id IN (SELECT DISTINCT task_id FROM agent_base.records WHERE tenant_id=$1 AND batch=$2)
"""


def models_paused() -> bool:
    return time.time() < _model_pause["until"]


def pause_models(reason: str) -> None:
    if time.time() >= _model_pause["until"]:
        log.error("base worker: model account blocked, pausing %ss: %s", _MODEL_PAUSE_S, reason[:200])
    _model_pause["until"] = time.time() + _MODEL_PAUSE_S
    _model_pause["reason"] = reason[:300]


def wake() -> None:
    _wake.set()


def _load(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def task_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["call"] = None
    return data


class SkillClient:
    """Runs Skala skills through Tank. The key is sent and never logged."""

    def __init__(self, http: Any, base_url: str, api_key: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise RuntimeError("tank key is not set")
        return {"X-Tank-Key": self._api_key}

    async def _read(self, response: Any) -> dict[str, Any]:
        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                raw = body.get("detail") if isinstance(body, dict) else ""
                detail = raw if isinstance(raw, str) else ""
            except Exception:  # noqa: BLE001 — the body may not be JSON
                detail = ""
            error = SkillError(detail or f"tank answered {response.status_code}", response.status_code)
            raise error
        data = response.json()
        if not isinstance(data, dict):
            raise SkillError("tank answer was not an object", response.status_code)
        return data

    async def describe(self, skill_id: str, preview: bool) -> dict[str, Any]:
        response = await self._http.get(
            f"{self._base}/v1/skala/{skill_id}/run",
            params={"preview": str(preview).lower()},
            headers=self._headers(),
            timeout=30,
        )
        return await self._read(response)

    async def engines(self) -> list[str]:
        data = await self.describe("base.normalize", False)
        engines = data.get("engines") or []
        return [str(item) for item in engines]

    async def pin(self, preview: bool, engine: str | None) -> tuple[dict[str, str], dict[str, str]]:
        if engine:
            known = await self.engines()
            if engine not in known:
                raise UnknownEngine(engine, known)
        versions: dict[str, str] = {}
        refs: dict[str, str] = {}
        for skill_id in skill_ids(engine):
            data = await self.describe(skill_id, preview)
            version = data.get("version")
            if not isinstance(version, str) or not version:
                raise SkillError(f"{skill_id} has no version", 409)
            versions[skill_id] = version
            ref = data.get("ref")
            refs[skill_id] = ref if isinstance(ref, str) and ref else f"{skill_id}@{version}"
        for skill_id in OPTIONAL_SKILLS:
            version = await self.approved(skill_id)
            if version:
                versions[skill_id] = version
                refs[skill_id] = f"{skill_id}@{version}"
        return versions, refs

    async def approved(self, skill_id: str) -> str | None:
        """The approved version only (never preview, never staging). None when Skala has none."""
        try:
            data = await self.describe(skill_id, False)
        except SkillError as exc:
            if exc.status in (404, 409):
                return None
            raise
        version = data.get("version")
        return version if isinstance(version, str) and version else None

    def bind(self, versions: dict[str, str], preview: bool, tenant_id: str) -> SkillCall:
        async def direct(skill_id: str, payload: dict[str, Any]) -> dict[str, Any]:
            from chassis.paid_steps import post as paid_post
            response = await paid_post(
                self._http, self._base, f"/v1/skala/{skill_id}/run",
                body={
                    "tenant_id": tenant_id,
                    "input": payload,
                    "version": versions[skill_id],
                    "preview": preview,
                },
                headers=self._headers(),
                timeout=300,
            )
            return await self._read(response)

        reliability = None
        reliability_lock = asyncio.Lock()

        async def call(skill_id: str, payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal reliability
            if skill_id == "base.normalize":
                async with reliability_lock:
                    if reliability is None:
                        response = await self._http.get(
                            self._base + "/v1/base-product/source-reliability/room",
                            headers=self._headers(), timeout=30)
                        reliability = (await self._read(response))["scores"]
                # Reserved output metadata, not user input or an additional skill/model call.
                return {**await direct(skill_id, payload), "_source_reliability": reliability}
            if skill_id == "base.scrape_site":
                from access_sources import scrape_with_archives
                return await scrape_with_archives(lambda: direct(skill_id, payload), self._http, self._base,
                                                  self._headers(), tenant_id, payload)
            if skill_id == "base.socials":
                from access_sources import public_business_pages
                return await public_business_pages(lambda: direct(skill_id, payload), self._http, self._base,
                                                   self._headers(), tenant_id, payload)
            return await direct(skill_id, payload)

        return call


class SkillError(Exception):
    def __init__(self, detail: str, status: int) -> None:
        self.detail = detail[:500]
        self.status = status
        super().__init__(self.detail)


class UnknownEngine(Exception):
    def __init__(self, engine: str, known: list[str]) -> None:
        self.engine = engine
        self.known = known
        super().__init__(engine)


async def ensure_tables(conn: Any) -> None:
    try:
        await conn.execute(RECORDS_SQL)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS agent_base_records_tenant ON agent_base.records (tenant_id, created_at DESC)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS agent_base_records_task ON agent_base.records (task_id)"
        )
        await conn.execute(TASKS_SQL)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS agent_base_tasks_queued ON agent_base.tasks (created_at) "
            "WHERE status = 'queued' AND task_type = 'base.enrich'"
        )
    except Exception:
        present = None
        try:
            present = await conn.fetchval(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = $1 AND table_name = 'records'",
                SCHEMA,
            )
        except Exception:
            present = None
        if present != 1:
            raise
    await conn.execute(
        "UPDATE agent_base.records SET status='queued', updated_at=now() WHERE status='running'"
    )
    await conn.execute(
        "UPDATE agent_base.tasks SET status='queued' WHERE status='running' AND task_type='base.enrich'"
    )


async def insert_batch(
    conn: Any,
    tenant_id: uuid.UUID,
    created_by: str | None,
    label: str,
    records: list[dict[str, Any]],
    note: str,
    preview: bool,
    engine: str | None,
    concurrency: int,
    registry: list[dict[str, Any]] | None,
) -> Any:
    cfg = {
        "batch": label,
        "count": len(records),
        "preview": preview,
        "engine": engine,
        "concurrency": concurrency,
        "registry": registry,
        "tenant_id": str(tenant_id),
        "model_spend": 0,
    }
    cfg = Job(tenant_id=tenant_id, region=os.environ.get("TANK_REGION", "us-east"),
              agent="base", skill="base.enrich", input=cfg, engine=engine).to_room(skill_column="task_type")["input"]
    title = (
        f"Base passes 1-7{' + ' + engine + ' engine' if engine else ''}: "
        f"{label} ({len(records)} records){note}"
    )
    task_id = uuid.uuid4()
    row = await conn.fetchrow(
        """
        INSERT INTO agent_base.tasks
            (id, tenant_id, created_by, agent, task_type, title, input)
        VALUES ($1,$2,$3,'base','base.enrich',$4,$5::jsonb)
        RETURNING id, tenant_id, created_by, agent, task_type, title, status, result, error,
                  ledger_id, created_at, started_at, finished_at
        """,
        task_id,
        tenant_id,
        created_by,
        title,
        json.dumps(cfg),
    )
    await conn.executemany(
        "INSERT INTO agent_base.records (tenant_id, task_id, batch, raw) VALUES ($1,$2,$3,$4::jsonb)",
        [(tenant_id, task_id, label, json.dumps(record)) for record in records],
    )
    return row


async def _run_batch(pool: Any, task_id: uuid.UUID, cfg: dict[str, Any], call: SkillCall) -> None:
    wire = Job.from_room({"tenant_id": cfg["tenant_id"], "task_type": "base.enrich", "input": cfg},
                         agent="base", region=os.environ.get("TANK_REGION", "us-east"), skill_column="task_type")
    tenant_id, engine = wire.tenant_id, wire.engine
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_base.tasks SET status='running', started_at=coalesce(started_at, now()) "
            "WHERE id=$1 AND tenant_id=$2",
            task_id,
            tenant_id,
        )
        rows = await conn.fetch(
            "SELECT id, raw, status FROM agent_base.records WHERE task_id=$1 AND tenant_id=$2 ORDER BY created_at",
            task_id,
            tenant_id,
        )
    rec_ids = [str(row["id"]) for row in rows]
    raws = [_load(row["raw"]) for row in rows]
    todo = [index for index, row in enumerate(rows) if row["status"] == "queued"]
    supplied = cfg.get("registry") is not None
    inputs, engine_rows, registry, prep_cost = await prepare_batch(
        raws, rec_ids, engine, cfg.get("registry"), call
    )
    spent = float(cfg.get("model_spend") or 0) + prep_cost
    outcomes = await enrich_queued(
        todo,
        rec_ids,
        inputs,
        engine_rows,
        registry,
        supplied,
        engine,
        call,
        models_paused,
        int(cfg.get("concurrency") or 8),
        cfg.get("refs") if isinstance(cfg.get("refs"), dict) else None,
        cfg.get("engine_data_version") if isinstance(cfg.get("engine_data_version"), str) else None,
    )
    async with pool.acquire() as conn:
        for index, outcome in outcomes:
            if outcome is None:
                continue
            spent += outcome.cost_usd
            rec_id = uuid.UUID(rec_ids[index])
            if outcome.status == "queued":
                pause_models(outcome.error or "")
                await conn.execute(
                    "UPDATE agent_base.records SET status='queued', updated_at=now() "
                    "WHERE id=$1 AND tenant_id=$2",
                    rec_id,
                    tenant_id,
                )
            elif outcome.status == "failed":
                await conn.execute(
                    "UPDATE agent_base.records SET status='failed', error=$3, profile=$4::jsonb, "
                    "passes=$5::jsonb, updated_at=now() WHERE id=$1 AND tenant_id=$2",
                    rec_id,
                    tenant_id,
                    outcome.error,
                    json.dumps(outcome.profile),
                    json.dumps(outcome.passes),
                )
            else:
                saved_at = await conn.fetchval(
                    "UPDATE agent_base.records SET status='done', profile=$3::jsonb, passes=$4::jsonb, "
                    "confidence=$5, updated_at=now() WHERE id=$1 AND tenant_id=$2 RETURNING updated_at",
                    rec_id,
                    tenant_id,
                    json.dumps(outcome.profile),
                    json.dumps(outcome.passes),
                    outcome.confidence,
                )
                if saved_at is not None:
                    await publish(str(tenant_id), "base.record.enriched", f"{task_id}:{rec_id}", {
                        "record_id": str(rec_id), "task_id": str(task_id), "confidence": outcome.confidence,
                        "observed_at": saved_at.isoformat()})
        cfg["model_spend"] = spent
        waiting = await conn.fetchval(
            "SELECT count(*) FROM agent_base.records WHERE task_id=$1 AND tenant_id=$2 AND status='queued'",
            task_id,
            tenant_id,
        )
        if waiting:
            reason = _model_pause["reason"][:200]
            await conn.execute(
                "UPDATE agent_base.tasks SET status='queued', error=$3, input=$4::jsonb "
                "WHERE id=$1 AND tenant_id=$2",
                task_id,
                tenant_id,
                f"paused: {waiting} records waiting; the model provider refused the account ({reason})",
                json.dumps(cfg),
            )
            return
        counts = await conn.fetchrow(
            """
            SELECT count(*) FILTER (WHERE status='done'),
                   count(*) FILTER (WHERE status='failed'),
                   count(*) FILTER (WHERE status='done' AND confidence >= 80)
            FROM agent_base.records WHERE task_id=$1 AND tenant_id=$2
            """,
            task_id,
            tenant_id,
        )
        await conn.execute(
            "UPDATE agent_base.tasks SET status='done', error=NULL, result=$3, input=$4::jsonb, finished_at=now() "
            "WHERE id=$1 AND tenant_id=$2",
            task_id,
            tenant_id,
            result_line(int(counts[0]), int(counts[2]), int(counts[1]), spent),
            json.dumps(cfg),
        )


async def base_loop(pool: Any, skills: SkillClient) -> None:
    """One batch at a time, oldest queued first."""
    discovery_pool = getattr(pool, "admin_pool", None) or pool
    async with discovery_pool.acquire() as conn:
        await ensure_tables(conn)
    while True:
        try:
            if models_paused():
                await asyncio.sleep(min(60.0, max(1.0, _model_pause["until"] - time.time())))
                continue
            async with discovery_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, tenant_id, input FROM agent_base.tasks "
                    "WHERE task_type='base.enrich' AND status='queued' ORDER BY created_at LIMIT 1"
                )
            if row is None:
                _wake.clear()
                try:
                    await asyncio.wait_for(_wake.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
                continue
            with tenant_scope(row["tenant_id"]):
                cfg = _load(row["input"])
                if not isinstance(cfg, dict):
                    cfg = {}
                # The room row owns the tenant; JSON input cannot redirect work
                # or the resulting event stream to a different tenant.
                cfg["tenant_id"] = str(row["tenant_id"])
                try:
                    versions, refs = await skills.pin(bool(cfg.get("preview")), cfg.get("engine"))
                    cfg["refs"] = refs
                    # Word lists come only from the approved engine profile, even on a preview batch.
                    cfg["engine_data_version"] = (
                        await skills.approved(f"{cfg['engine']}.profile") if cfg.get("engine") else None
                    )
                    call = skills.bind(versions, bool(cfg.get("preview")), str(row["tenant_id"]))
                    if os.environ.get("BASE_BRAIN_URL"):
                        from base_brain import run_code_first
                        await run_code_first(pool, skills, row["id"], cfg, call)
                    else:
                        await _run_batch(pool, row["id"], cfg, call)
                except Exception as exc:  # noqa: BLE001 — one bad batch must not stop the queue
                    log.error("base worker: %s", type(exc).__name__)
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE agent_base.tasks SET status='failed', error=$2, finished_at=now() "
                            "WHERE id=$1 AND tenant_id=$3",
                            row["id"],
                            str(exc)[:1000],
                            row["tenant_id"],
                        )
        except Exception as exc:  # noqa: BLE001 — the database blinked; back off and carry on
            log.error("base worker: %s", type(exc).__name__)
            await asyncio.sleep(5)


__all__ = ["SQL_BATCHES_BY_TENANT", "SQL_RECORDS_BY_TENANT", "SQL_RETRY", "SQL_REQUEUE_TASKS"]
