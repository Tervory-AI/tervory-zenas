"""WORKERS-2: the host workers service checks in here, and asks whether it is paused.

Worker routes take only TANK_KEY_WORKERS (compared in constant time, never logged).
The founder route is behind require_admin like every other control.
Additive storage only: tank.workers_heartbeat (see schema.sql). No existing table changes.
"""
from __future__ import annotations

import hmac
import asyncio
import os
import re
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from admin import require_admin

# A heartbeat older than this shows "not checking in" on /control.
STALE_AFTER_S = 180
MAX_ERROR = 300
_WORKER = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_COMMIT = re.compile(r"^(unknown|[0-9a-f]{7,40})$")
# Last-resort redaction on the Tank side. The worker already redacts with workers/redact.py.
_SECRETS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_.-]+"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"x-access-token:[^@\s]+"),
)
_PREFIXED = (
    re.compile(r"(?i)(bearer\s+)\S+"),
    re.compile(r"(?i)(x-tank-key:\s*)\S+"),
    re.compile(r"(?i)(password=)\S+"),
)


def db() -> Any:
    from main import pool
    return pool()


def scrub(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = " ".join(str(text).split())
    for pattern in _SECRETS:
        cleaned = pattern.sub("[redacted]", cleaned)
    for pattern in _PREFIXED:
        cleaned = pattern.sub(lambda m: m.group(1) + "[redacted]", cleaned)
    return cleaned[:MAX_ERROR] or None


async def require_workers_key(x_tank_key: str = Header(default="")) -> str:
    expected = os.environ.get("TANK_KEY_WORKERS", "").strip()
    presented = (x_tank_key or "").strip()
    if not expected or not presented:
        raise HTTPException(status_code=401, detail="missing or invalid X-Tank-Key")
    if not hmac.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="missing or invalid X-Tank-Key")
    return "workers"


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker: str = "boston"
    state: Literal["running", "paused"]
    issue: int | None = Field(default=None, ge=1, le=10_000_000)
    commit: str = "unknown"
    last_error: str | None = Field(default=None, max_length=4000)

    @field_validator("worker")
    @classmethod
    def _worker(cls, value: str) -> str:
        if not _WORKER.fullmatch(value):
            raise ValueError("worker must be a short lowercase name")
        return value

    @field_validator("commit")
    @classmethod
    def _commit(cls, value: str) -> str:
        value = value.strip().lower()
        if not _COMMIT.fullmatch(value):
            raise ValueError("commit must be a git sha")
        return value


router = APIRouter(prefix="/v1/workers", dependencies=[Depends(require_workers_key)])
founder_router = APIRouter(prefix="/v1/controls", dependencies=[Depends(require_admin)])


@router.post("/heartbeat")
async def heartbeat(body: Heartbeat) -> dict[str, Any]:
    async with db().acquire() as conn:
        await conn.execute(
            """INSERT INTO tank.workers_heartbeat (worker,state,issue,commit,last_error,seen_at)
            VALUES ($1,$2,$3,$4,$5,now()) ON CONFLICT (worker)
            DO UPDATE SET state=EXCLUDED.state,issue=EXCLUDED.issue,commit=EXCLUDED.commit,
                          last_error=EXCLUDED.last_error,seen_at=now()""",
            body.worker, body.state, body.issue, body.commit, scrub(body.last_error),
        )
    return {"ok": True}


@router.get("/paused")
async def paused_for_workers() -> dict[str, bool]:
    """The worker reads this before taking a task. Any failure is a 503 and the worker stays paused."""
    from controls import workers_paused

    try:
        return {"paused": await workers_paused()}
    except Exception:
        raise HTTPException(status_code=503, detail="pause state unavailable") from None


def view(row: Any, now: datetime | None = None) -> dict[str, Any] | None:
    if row is None:
        return None
    now = now or datetime.now(timezone.utc)
    seen = row["seen_at"]
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    age = max(0, int((now - seen).total_seconds()))
    return {
        "worker": row["worker"],
        "state": row["state"],
        "issue": row["issue"],
        "commit": row["commit"],
        "last_error": row["last_error"],
        "seen_at": seen.isoformat(),
        "age_s": age,
        "checking_in": age <= STALE_AFTER_S,
    }


@founder_router.get("/workers")
async def workers_tile() -> dict[str, Any]:
    from controls import PLATFORM

    try:
        async with asyncio.timeout(5):
            # TenantPool.acquire already opens the checkout transaction (read committed), so the
            # snapshot options go there. A nested conn.transaction(isolation=...) is a savepoint
            # and asyncpg refuses it ("nested transaction has a different isolation level").
            async with db().acquire(readonly=True, isolation="repeatable_read") as conn:
                row = await conn.fetchrow(
                    "SELECT worker,state,issue,commit,last_error,seen_at FROM tank.workers_heartbeat ORDER BY seen_at DESC LIMIT 1"
                )
                pause = await conn.fetchrow(
                    "SELECT value,updated_by,updated_at FROM tank.workers_controls "
                    "WHERE tenant_id=$1 AND name='workers_paused'", PLATFORM,
                )
                change = await conn.fetchrow(
                    "SELECT by_user,old_value,new_value,at FROM tank.control_log "
                    "WHERE tenant_id=$1 AND name='workers_paused' ORDER BY at DESC LIMIT 1", PLATFORM,
                )
                # Box-wide founder snapshot, matching the worker's thread/tenant
                # join. Counts do not establish capacity, claimability or progress.
                counts = await conn.fetchrow(
                    "SELECT count(*) FILTER (WHERE b.status='queued') AS queued, "
                    "count(*) FILTER (WHERE b.status='running') AS running, "
                    "count(*) FILTER (WHERE b.status='awaiting_review') AS awaiting_review "
                    "FROM tank.command_builds b JOIN tank.command_threads t "
                    "ON t.thread_id=b.thread_id AND t.tenant_id=b.tenant_id "
                    "WHERE b.status IN ('queued','running','awaiting_review')"
                )
            return {
                "workers_paused": bool(pause["value"]) if pause else False,
                "worker": view(row), "stale_after_s": STALE_AFTER_S,
                "pause_control": {"updated_by": pause["updated_by"], "updated_at": pause["updated_at"].isoformat()} if pause else None,
                "last_pause_change": {"by_user": change["by_user"], "old_value": change["old_value"],
                                      "new_value": change["new_value"], "at": change["at"].isoformat()} if change else None,
                "build_counts": {name: int(counts[name]) for name in ("queued", "running", "awaiting_review")},
            }
    except Exception:
        raise HTTPException(status_code=503, detail="worker diagnostics unavailable") from None


async def panel() -> dict[str, Any]:
    """GET /v1/workers (the Tank page panel), from tank.workers_heartbeat instead of the old
    http://workers:8000. Available only while the worker checks in; one row for its current issue."""
    try:
        async with db().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT worker,state,issue,commit,last_error,seen_at FROM tank.workers_heartbeat ORDER BY seen_at DESC LIMIT 1"
            )
    except Exception:
        return {"available": False, "tasks": []}
    beat = view(row)
    if beat is None or not beat["checking_in"]:
        return {"available": False, "tasks": []}
    tasks = []
    if beat["issue"]:
        tasks.append({"issue": beat["issue"], "brief": "", "status": "running", "pr_url": None, "minutes": None, "cost_usd": None})
    return {"available": True, "tasks": tasks}
