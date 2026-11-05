"""Founder control state. Only implemented controls have write routes."""
from __future__ import annotations

import uuid
import asyncio
import logging
from datetime import datetime, timezone
from contextvars import ContextVar

import httpx
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from admin import require_admin

PLATFORM = uuid.UUID('00000000-0000-0000-0000-000000000001')
log = logging.getLogger('tank.controls')
STATE_TIMEOUT_S = 2.0
AUDIT_TIMEOUT_S = 1.0
_task: ContextVar[str | None] = ContextVar('paid_work_task', default=None)

router = APIRouter(prefix='/v1/controls', dependencies=[Depends(require_admin)])


def db() -> Any:
    from main import pool
    return pool()


class Pause(BaseModel):
    model_config = ConfigDict(extra='forbid')
    paused: bool


async def paid_paused() -> bool:
    from tenant_db import tenant_scope
    # The master stop is platform-owned even when a client tenant is running.
    with tenant_scope(PLATFORM):
        async with db().acquire() as conn:
            return bool(await conn.fetchval(
                "SELECT value FROM tank.controls WHERE tenant_id=$1 AND name='paid_paused'", PLATFORM
            ))


async def refuse_paid(*, paid: bool = True, task: str | None = None, path: str = "route") -> None:
    """Free work never reads control state. Paid work fails closed on read failure."""
    if task is not None:
        _task.set(task)
    if not paid:
        return
    task = task or _task.get() or "internal.model (task unavailable)"
    reason = "paid work is paused"
    try:
        async with asyncio.timeout(STATE_TIMEOUT_S):
            paused = await paid_paused()
    except Exception:
        paused = True
        reason = "paid work is paused: pause state unavailable"
    if not paused:
        return
    # Safe identifiers only: never log a request URL, query, prompt or credentials.
    task = " ".join(task.split())[:120]
    path = path[:120]
    at = datetime.now(timezone.utc).isoformat()
    log.warning("paid_refused task=%s path=%s at=%s", task, path, at)
    try:
        async with asyncio.timeout(AUDIT_TIMEOUT_S):
            from tenant_db import tenant_scope
            with tenant_scope(PLATFORM):
                async with db().acquire() as conn:
                    await conn.execute(
                        """INSERT INTO tank.control_log
                        (tenant_id,name,by_user,old_value,new_value,task,path)
                        VALUES ($1,'paid_refused','system',true,true,$2,$3)""", PLATFORM, task, path,
                    )
    except Exception:
        # Database outage must not turn a refusal into permission.
        log.error("paid_refusal_audit_unavailable task=%s path=%s at=%s", task, path, at)
    raise HTTPException(status_code=503, detail=reason)


async def paid_host_hook(request: httpx.Request) -> None:
    import vendor_meter
    host = request.url.host.lower()
    if host in vendor_meter.PAID_HOSTS:
        await refuse_paid(path=f"HTTP host:{host}", task=_task.get() or "base.find_site (HTTP client has no run ID)")
        approval = await __import__("paid_steps").http_admission(request)
        await vendor_meter.request_hook(request)
        reserved = request.extensions.get("vendor_receipt")
        if reserved:
            await __import__("paid_steps").attach_receipt(reserved[0], approval)


async def paid_response_hook(response: httpx.Response) -> None:
    import vendor_meter
    await vendor_meter.response_hook(response)


async def workers_paused() -> bool:
    """WORKERS-2. Stored in the additive tank.workers_controls (tank.controls only allows paid_paused)."""
    async with db().acquire() as conn:
        return bool(await conn.fetchval(
            "SELECT value FROM tank.workers_controls WHERE tenant_id=$1 AND name='workers_paused'", PLATFORM
        ))


@router.get('')
async def read_controls() -> dict[str, Any]:
    async with db().acquire() as conn:
        rows = await conn.fetch(
            "SELECT task,path,at FROM tank.control_log WHERE tenant_id=$1 AND name='paid_refused' ORDER BY at DESC LIMIT 10", PLATFORM
        )
    return {'paid_paused': await paid_paused(), 'refused': [dict(row) for row in rows]}


@router.put('/paid-paused')
async def set_pause(body: Pause, x_tervory_admin: str = Header(default='')) -> dict[str, bool]:
    async with db().acquire() as conn:
        async with conn.transaction():
            # Serialize first-time creation and concurrent writes across processes.
            await conn.execute('SELECT pg_advisory_xact_lock(4810115)')
            old = bool(await conn.fetchval(
                "SELECT value FROM tank.controls WHERE tenant_id=$1 AND name='paid_paused'", PLATFORM
            ))
            await conn.execute(
                """INSERT INTO tank.controls (tenant_id,name,value,updated_by)
                VALUES ($1,'paid_paused',$2,$3) ON CONFLICT (tenant_id,name)
                DO UPDATE SET value=EXCLUDED.value,updated_by=EXCLUDED.updated_by,updated_at=now()""",
                PLATFORM, body.paused, x_tervory_admin,
            )
            await conn.execute(
                """INSERT INTO tank.control_log (tenant_id,name,by_user,old_value,new_value)
                VALUES ($1,'paid_paused',$2,$3,$4)""",
                PLATFORM, x_tervory_admin, old, body.paused,
            )
    return {'paid_paused': body.paused}


@router.put('/workers-paused')
async def set_workers_pause(body: Pause, x_tervory_admin: str = Header(default='')) -> dict[str, bool]:
    """Second founder-only control. Logged exactly like the paid pause, in tank.control_log."""
    async with db().acquire() as conn:
        async with conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(4810116)')
            old = bool(await conn.fetchval(
                "SELECT value FROM tank.workers_controls WHERE tenant_id=$1 AND name='workers_paused'", PLATFORM
            ))
            await conn.execute(
                """INSERT INTO tank.workers_controls (tenant_id,name,value,updated_by)
                VALUES ($1,'workers_paused',$2,$3) ON CONFLICT (tenant_id,name)
                DO UPDATE SET value=EXCLUDED.value,updated_by=EXCLUDED.updated_by,updated_at=now()""",
                PLATFORM, body.paused, x_tervory_admin,
            )
            await conn.execute(
                """INSERT INTO tank.control_log (tenant_id,name,by_user,old_value,new_value)
                VALUES ($1,'workers_paused',$2,$3,$4)""",
                PLATFORM, x_tervory_admin, old, body.paused,
            )
    return {'workers_paused': body.paused}
