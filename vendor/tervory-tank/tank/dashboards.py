"""Read-only dashboard views. Unknown measurements are null, never sample figures."""
from __future__ import annotations

import hmac
import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from admin import require_admin

router = APIRouter(prefix='/v1/dashboards')


def db() -> Any:
    from main import pool
    return pool()


async def workspace_only(x_tank_key: str = Header(default='')) -> None:
    expected = os.environ.get('TANK_API_KEY', '')
    if not expected or not hmac.compare_digest(expected, x_tank_key):
        raise HTTPException(status_code=404, detail='not found')


def client_tenant(tenant_id: uuid.UUID) -> uuid.UUID:
    from gold import PLATFORM_TENANT
    if tenant_id == PLATFORM_TENANT:
        raise HTTPException(status_code=404, detail='not found')
    return tenant_id


async def measurements(conn: Any, tenant_id: uuid.UUID | None) -> dict[str, Any]:
    # A null scope is only passed by the founder-authorized route.
    daily = await conn.fetch(
        """SELECT ts::date::text AS day, count(*) FILTER (WHERE agent <> 'bench')::int AS calls,
        count(*) FILTER (WHERE agent <> 'bench' AND rung=3 AND outcome='delivered')::int AS code,
        count(*) FILTER (WHERE agent <> 'bench' AND outcome='delivered')::int AS delivered,
        sum(cost_usd) FILTER (WHERE cost_status='set')::float AS spent, count(*) FILTER (WHERE cost_status='not_set')::int AS unpriced
        FROM tank.ledger WHERE ($1::uuid IS NULL OR tenant_id=$1)
        AND ts >= date_trunc('month', now()) GROUP BY ts::date ORDER BY ts::date""", tenant_id,
    )
    jobs = await conn.fetchval(
        """SELECT count(*) FROM tank.tasks WHERE ($1::uuid IS NULL OR tenant_id=$1)
        AND status='done' AND finished_at >= date_trunc('month',now())""", tenant_id,
    )
    return {'daily': [dict(row) for row in daily], 'jobs_done': jobs,
            'saved_usd': None, 'credits_left': None,
            'savings_note': 'No per-call baseline has been recorded.',
            'credits_note': 'No credit balance source is connected.'}


@router.get('/client', dependencies=[Depends(workspace_only)])
async def client_dashboard(tenant_id: uuid.UUID) -> dict[str, Any]:
    client_tenant(tenant_id)
    async with db().acquire() as conn:
        result = await measurements(conn, tenant_id)
        recent = await conn.fetch(
            """SELECT id::text,title,status FROM tank.tasks WHERE tenant_id=$1
            ORDER BY created_at DESC LIMIT 6""", tenant_id,
        )
        grants = await conn.fetch(
            'SELECT agent FROM tank.tenant_squad WHERE tenant_id=$1 ORDER BY agent', tenant_id,
        )
    result['granted_squads'] = [row['agent'] for row in grants]
    # Explicit projection: model names, call IDs and failures never reach this view.
    result['recent'] = [dict(row) for row in recent]
    return result


@router.get('/founder', dependencies=[Depends(require_admin)])
async def founder_dashboard() -> dict[str, Any]:
    async with db().acquire() as conn:
        result = await measurements(conn, None)
        skills = await conn.fetch(
            """SELECT id,version,last_eval_score,eval_threshold FROM tank.skills
            WHERE present AND state='staging' ORDER BY id,version"""
        )
        squad = await conn.fetch(
            """SELECT agent,status,count(*)::int AS jobs FROM tank.tasks
            WHERE status IN ('queued','running') GROUP BY agent,status ORDER BY agent,status"""
        )
        shadow = await conn.fetchval(
            'SELECT count(*) FROM tank.policy WHERE tenant_id IS NULL AND shadow_rate > 0'
        )
        batch = await conn.fetchval(
            'SELECT count(*) FROM tank.policy WHERE tenant_id IS NULL AND batch_eligible'
        )
    result.update(skills=[dict(row) for row in skills], squad=[dict(row) for row in squad],
                  shadow_policies=shadow, batch_policies=batch)
    return result
