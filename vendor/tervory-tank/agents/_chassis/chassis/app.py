"""FastAPI app for one agent box: health, jobs, worker, scheduler."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from chassis.browser import BrowserClient
from chassis.db import as_json, ensure_schedules, ensure_schema, schema_for
from chassis.tenant_db import TenantPool, tenant_path, tenant_scope, bind_tenant_endpoints, TenantContextMiddleware
from chassis.files import read_file, save_file
from chassis.jobs import JobContext, timeout_for
from chassis.job_contract import Job
from chassis.logs import log_line
from chassis.memory import MemoryClient
from chassis.tank import TankClient
from chassis.storage_api import storage_router
from chassis.worker import job_loop, schedule_loop


class JobIn(BaseModel):
    tenant_id: uuid.UUID
    kind: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_.]{0,79}$")
    input: dict[str, Any] = Field(default_factory=dict)
    run_at: datetime | None = None


class FileStore:
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def save_file(self, tenant_id: str, name: str, data: bytes) -> str:
        return str(save_file(tenant_id, name, data, root=self.root))

    def read_file(self, tenant_id: str, name: str) -> bytes:
        return read_file(tenant_id, name, root=self.root)


def create_app(
    agent: str,
    handlers: dict[str, Any],
    schedules: list[dict[str, Any]] | None = None,
    job_timeouts: dict[str, int] | None = None,
) -> FastAPI:
    schema_for(agent)  # reject a bad name before the process serves traffic
    planned = list(schedules or [])
    # Checked up front so a bad limit fails at import, not on the first job.
    timeouts = {kind: timeout_for(kind, job_timeouts) for kind in (job_timeouts or {})}
    timeouts = {kind: seconds for kind, seconds in timeouts.items() if seconds is not None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database_url = os.environ.get("DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("DATABASE_URL is not set")
        # Jobs hold a connection for the whole handler and handlers take more,
        # so a three-connection pool starved /health (Base and Tiger unhealthy).
        raw_pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
        # Health gets its own connection so it never waits behind job work.
        health_pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1)
        pool = TenantPool(raw_pool)
        admin_url = os.environ.get("CHASSIS_ADMIN_DATABASE_URL", "")
        admin_pool = await asyncpg.create_pool(admin_url, min_size=1, max_size=2) if admin_url else None
        pool.admin_pool = admin_pool
        if admin_pool is not None:
            async with admin_pool.acquire() as conn:
                role = await conn.fetchrow("SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname=current_user")
                if role["rolname"] != os.environ.get("CHASSIS_RLS_ADMIN_ROLE", "") or not role["rolbypassrls"]:
                    raise RuntimeError("CHASSIS_ADMIN_DATABASE_URL must use the configured BYPASSRLS role")
        async with raw_pool.acquire() as conn:
            enforced = await conn.fetchval("SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relrowsecurity AND n.nspname=$1)", schema_for(agent))
            if enforced is True:
                role = await conn.fetchrow("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")
                if role["rolsuper"] or role["rolbypassrls"] or admin_pool is None:
                    raise RuntimeError("RLS requires a non-bypass room role and a separate admin pool")
        http = httpx.AsyncClient(timeout=60)
        app.state.pool = pool
        app.state.health_pool = health_pool
        app.state.admin_pool = admin_pool
        app.state.http = http
        app.state.files = FileStore(os.environ.get("DATA_DIR", "/data"))
        app.state.tank = TankClient(
            os.environ.get("TANK_URL", "http://tank:8000"),
            os.environ.get("TANK_KEY", ""),
            agent,
            http,
        )
        app.state.memory = MemoryClient()
        app.state.browser = BrowserClient()
        async with (admin_pool or raw_pool).acquire() as conn:
            schema = await ensure_schema(conn, agent)
            await ensure_schedules(conn, schema, planned)
        if not os.environ.get("TANK_KEY"):
            log_line(agent, "tank key is not set")
        log_line(agent, "chassis ready")
        tasks = [
            asyncio.create_task(job_loop(pool, agent, handlers, lambda job: _context(app, agent, job), discovery_pool=admin_pool or raw_pool)),
            asyncio.create_task(schedule_loop(admin_pool or raw_pool, agent, timeouts)),
        ]
        bind_tenant_endpoints(app)
        try:
            # Room-specific outer lifespan initialization is administrative.
            # HTTP requests are reset by middleware, and claimed jobs set owner.
            with tenant_scope(None, administrative=True):
                yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await http.aclose()
            await pool.close()
            await health_pool.close()
            if admin_pool is not None:
                await admin_pool.close()

    app = FastAPI(title=f"Tervory agent {agent}", version="0.1.0", lifespan=lifespan)
    app.add_middleware(TenantContextMiddleware)
    app.include_router(storage_router(agent))
    app.state.agent = agent
    app.state.handlers = handlers
    app.state.job_timeouts = timeouts
    app.state.schedules = planned

    @app.get("/health")
    async def health() -> dict[str, Any]:
        pool = getattr(app.state, "health_pool", None) or getattr(app.state, "pool", None)
        if pool is None:
            raise HTTPException(status_code=503, detail="database unreachable")
        try:
            async with pool.acquire(timeout=2) as conn:
                await conn.fetchval("SELECT 1", timeout=2)
        except Exception:  # noqa: BLE001 — do not include the database error in the response
            raise HTTPException(status_code=503, detail="database unreachable") from None
        return {"ok": True, "agent": agent}

    @app.post("/jobs", status_code=201)
    @tenant_path("body")
    async def enqueue(body: JobIn | Job) -> dict[str, Any]:
        region = os.environ.get("TANK_REGION", "us-east")
        try:
            wire = body if isinstance(body, Job) else Job.from_room(
                body.model_dump(), agent=agent, region=region)
        except ValueError:
            raise HTTPException(422) from None
        if wire.agent != agent or wire.region != region:
            raise HTTPException(422)
        body = JobIn(**wire.to_room(), run_at=body.run_at if isinstance(body, JobIn) else None)
        run_at = body.run_at or datetime.now(timezone.utc)
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)
        schema = schema_for(agent)
        timeout_s = timeout_for(body.kind, app.state.job_timeouts)
        async with app.state.pool.acquire() as conn:
            if timeout_s is None:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO {schema}.jobs (tenant_id, kind, input, status, run_at)
                    VALUES ($1, $2, $3::jsonb, 'queued', $4)
                    RETURNING id, tenant_id, kind, status
                    """,
                    body.tenant_id,
                    body.kind,
                    json.dumps(body.input),
                    run_at,
                )
            else:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO {schema}.jobs (tenant_id, kind, input, status, run_at, timeout_s)
                    VALUES ($1, $2, $3::jsonb, 'queued', $4, $5)
                    RETURNING id, tenant_id, kind, status
                    """,
                    body.tenant_id,
                    body.kind,
                    json.dumps(body.input),
                    run_at,
                    timeout_s,
                )
        log_line(agent, "job queued", tenant_id=str(body.tenant_id), job_id=str(row["id"]))
        return {
            "id": str(row["id"]),
            "tenant_id": str(row["tenant_id"]),
            "kind": row["kind"],
            "status": row["status"],
        }

    @app.get("/jobs/{job_id}")
    @tenant_path("tenant_id")
    async def get_job(job_id: uuid.UUID, tenant_id: uuid.UUID = Query()) -> dict[str, Any]:
        schema = schema_for(agent)
        async with app.state.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT id, tenant_id, kind, status, attempts, error, result, created_at, started_at, finished_at
                FROM {schema}.jobs
                WHERE id = $1 AND tenant_id = $2
                """,
                job_id,
                tenant_id,
            )
        if row is None:
            raise HTTPException(status_code=404, detail="no such job")
        return {
            "id": str(row["id"]),
            "tenant_id": str(row["tenant_id"]),
            "kind": row["kind"],
            "status": row["status"],
            "attempts": row["attempts"],
            "error": row["error"],
            "result": as_json(row["result"]) if row["result"] is not None else None,
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    return app


def _context(app: FastAPI, agent: str, job: Any) -> JobContext:
    wire = Job.from_room(job, agent=agent, region=os.environ.get("TANK_REGION", "us-east"))
    # Existing room handlers read engine from input. Map the shared field at
    # execution time without changing the stored payload or room table shape.
    execution_input = dict(wire.input)
    if wire.engine is not None:
        execution_input["engine"] = wire.engine
    return JobContext(
        agent=wire.agent,
        tenant_id=str(wire.tenant_id),
        job_id=str(job["id"]),
        kind=wire.skill,
        input=execution_input,
        files=app.state.files,
        tank=app.state.tank,
        memory=app.state.memory,
        browser=app.state.browser,
    )
