"""Signed-in Rig controls and build intake through the shared grant boundary."""
import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from command import Actor, identity, require_tool, verified_memory_actor
from product_identity import product_extensions


def build_router(tank):
    router = APIRouter(prefix='/v1/rig', dependencies=[Depends(tank.require_key)])

    async def forward(request, actor, path, tool, *, brain=False):
        require_tool(actor, tool)
        tenant, _ = verified_memory_actor(actor)
        body = await tank._rig_body(request) if request.method == 'POST' else None
        params = dict(request.query_params)
        for data in (params, body or {}):
            if data.get('tenant_id') and str(data['tenant_id']) != tenant:
                raise HTTPException(403, 'Tenant scope mismatch')
        if body is not None:
            body['tenant_id'] = tenant
        params['tenant_id'] = tenant
        if brain:
            if os.environ.get('RIG_BRAIN_ENABLED', 'false').lower() not in {'true', '1', 'yes', 'on'}:
                raise HTTPException(503, 'Rig brain is switched off')
            base = (os.environ.get('RIG_BRAIN_URL') or 'http://rig-brain:8001').rstrip('/')
        else:
            base = os.environ.get('AGENT_RIG_URL', 'http://agent-rig:8000').rstrip('/')
            if os.environ.get('RIG_LIFECYCLE_KEY'):
                from rig_rooms import public_endpoint
                base = await public_endpoint(tank.pool(), UUID(tenant))
        try:
            response = await tank._http.request(request.method, base + path,
                params=params, json=body if body is not None else None, timeout=30,
                headers={'X-Tank-Key': os.environ.get('TANK_KEY_RIG', ''), 'X-Tervory-Tenant': tenant},
                extensions=product_extensions('rig', base))
        except Exception:
            raise HTTPException(503, 'Rig is unavailable') from None
        return Response(response.content, status_code=response.status_code,
                        media_type='application/json', headers={'Cache-Control': 'private, no-store'})

    @router.get('/memory/records')
    async def records(request: Request, actor: Actor = Depends(identity)):
        return await forward(request, actor, '/memory/records', 'rig.memory.view')

    @router.post('/memory/{operation}')
    async def control(operation: str, request: Request, actor: Actor = Depends(identity)):
        if operation not in {'edit', 'forget', 'switch'}:
            raise HTTPException(404, 'Not found')
        return await forward(request, actor, '/memory/' + operation, 'rig.memory.' + operation)

    @router.post('/tasks')
    async def build(request: Request, actor: Actor = Depends(identity)):
        return await forward(request, actor, '/tasks', 'rig.build', brain=True)

    @router.get('/tasks/{task_id}')
    @router.delete('/tasks/{task_id}')
    async def task(task_id: UUID, request: Request, actor: Actor = Depends(identity)):
        return await forward(request, actor, '/tasks/' + str(task_id), 'rig.build', brain=True)

    return router
