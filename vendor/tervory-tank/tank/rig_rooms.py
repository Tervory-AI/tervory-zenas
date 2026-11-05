"""Store-room onboarding over the existing signed Autumbra lifecycle corridor."""
import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel


class Empty(BaseModel):
    model_config = {'extra': 'forbid'}


def host(tenant):
    return 'http://rig-room-' + str(UUID(str(tenant)))


async def record(pool, tenant):
    async with pool.acquire() as conn:
        return await conn.fetchrow('SELECT coolify_uuid, state FROM tank.rig_rooms WHERE tenant_id=$1', tenant)


async def endpoint(pool, actor):
    if not os.environ.get('RIG_LIFECYCLE_KEY'):
        return 'http://agent-rig:8003'
    row = await record(pool, actor.tenant_id)
    if row is None:
        return 'http://agent-rig:8003'  # Previously issued stores retain their existing runtime.
    if row['state'] != 'created':
        raise HTTPException(409, 'This store room is not available. Check its lifecycle receipt.')
    return host(actor.tenant_id) + ':8003'


async def public_endpoint(pool, tenant):
    row = await record(pool, tenant)
    if row is None:
        return 'http://agent-rig:8000'
    if row['state'] != 'created':
        raise HTTPException(409, 'This store room is not available.')
    return host(tenant) + ':8000'


async def catalogue_tenants(pool, legacy):
    # Existing Tiger-only platform discovery exposes identifiers, never store data.
    from tenant_db import tenant_scope
    with tenant_scope(UUID('00000000-0000-0000-0000-000000000001'), administrative=True):
        async with pool.acquire() as conn:
            rows = await conn.fetch('SELECT tenant_id,state FROM tank.rig_rooms')
    registered = {str(row['tenant_id']) for row in rows}
    active = {str(row['tenant_id']) for row in rows if row['state'] == 'created'}
    return sorted((set(legacy) - registered) | active)


def register(parent, identity, db):
    router = APIRouter(prefix='/rig/rooms')

    @router.post('/{action}')
    async def operation(action: str, body: Empty, request: Request, actor=Depends(identity)):
        from command import require_tool
        from ghost_routes import call_room
        from trigger_commands import confirmation
        from trigger_routes import code_receipt
        from access import rig_request
        tools = {'onboard': 'rig.store.onboard', 'list': 'rig.rooms.list', 'destroy': 'rig.store.destroy'}
        if action not in tools:
            raise HTTPException(404, 'Unknown room operation.')
        tool = tools[action]
        require_tool(actor, tool)
        if action != 'list':
            confirmation(actor, tool, {})
        if not os.environ.get('RIG_LIFECYCLE_KEY'):
            raise HTTPException(503, 'Rig lifecycle is not configured.')
        from main import box_region
        if box_region() != 'us-east':
            raise HTTPException(403, 'Rig store rooms must stay on their configured home box.')
        pool = db()
        if action == 'onboard':
            async with pool.acquire() as conn:
                reserved = await conn.fetchval("INSERT INTO tank.rig_rooms(tenant_id,state) VALUES($1,'creating') ON CONFLICT DO NOTHING RETURNING tenant_id", actor.tenant_id)
            if reserved is None:
                raise HTTPException(409, 'This store already has a room record. Check its lifecycle receipt.')
        elif action == 'destroy':
            current = await record(pool, actor.tenant_id)
            if not current or current['state'] != 'created':
                raise HTTPException(409, 'Check the current room state before destroying it.')
            # Refuse DELETE unless its own authenticated data plane confirms bucket retention.
            saved = await rig_request(actor, 'room-archive', {})
            if saved.get('archived') is not True or saved.get('tenant_id') != str(actor.tenant_id):
                raise HTTPException(409, 'The store did not confirm its retained snapshot.')
            async with pool.acquire() as conn:
                claimed = await conn.fetchval("UPDATE tank.rig_rooms SET state='destroying',updated_at=now() WHERE tenant_id=$1 AND state='created' RETURNING tenant_id", actor.tenant_id)
            if claimed is None:
                raise HTTPException(409, 'Another room operation is in progress.')
        try:
            response = await call_room('POST', 'ghost', '/rig/rooms/' + {'onboard': 'create'}.get(action, action),
                body={'tenant_id': str(actor.tenant_id)}, timeout=150)
            if response.status_code != 200:
                raise HTTPException(409, 'Room lifecycle did not confirm success; inspect Coolify before repeating.')
            result = response.json()
            if action != 'list':
                receipt = result.get('receipt') or {}
                expected = 'created' if action == 'onboard' else 'destroyed'
                state = receipt.get('outcome', 'unknown')
                async with pool.acquire() as conn:
                    await conn.execute('UPDATE tank.rig_rooms SET state=$2,coolify_uuid=COALESCE($3,coolify_uuid),updated_at=now() WHERE tenant_id=$1', actor.tenant_id, state, receipt.get('uuid'))
                if state != expected and not (action == 'destroy' and state == 'absent'):
                    raise HTTPException(409, 'Room outcome is uncertain; inspect Coolify before repeating.')
            return {**result, 'name': 'Rig room', 'status': result.get('receipt', {}).get('outcome', 'listed'),
                    'ledger_id': str(await code_receipt(actor.tenant_id, tool, agent='rig')), 'cost_usd': 0}
        except Exception:
            if action != 'list':
                async with pool.acquire() as conn:
                    await conn.execute("UPDATE tank.rig_rooms SET state='unknown',updated_at=now() WHERE tenant_id=$1 AND state IN ('creating','destroying')", actor.tenant_id)
            raise

    parent.include_router(router)
