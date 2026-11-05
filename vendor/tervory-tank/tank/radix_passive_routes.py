"""One tenant-scoped Radix passive-scan corridor, including Tisan's gate call."""
from __future__ import annotations

import hmac
import os
from uuid import UUID
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: UUID
    url: str | None = Field(default=None, max_length=300)
    target: str | None = Field(default=None, max_length=300)
    artifact: str | None = Field(default=None, max_length=80)
    domain: str | None = Field(default=None, max_length=253)
    interval: Literal['daily', 'weekly'] | None = None
    run_now: bool | None = None
    consent_text: str | None = Field(default=None,min_length=1,max_length=16000)


def build_router(tank):
    router = APIRouter(prefix='/v1/radix/passive')

    async def authorize(request: Request, tenant: UUID, operation: str):
        supplied = request.headers.get('x-tank-key', '')
        if request.headers.get('x-tervory-tenant') != str(tenant):
            raise HTTPException(403, 'tenant mismatch')
        region = request.headers.get('x-tervory-region', request.headers.get('x-tervory-home-region'))
        if region != tank.box_region():
            raise HTTPException(403, 'region mismatch')
        room_key = os.environ.get('TANK_KEY_TISAN', '')
        if (operation in {'gate', 'scans'} and supplied and room_key
                and hmac.compare_digest(supplied.encode(), room_key.encode())
                and request.headers.get('x-tervory-squad') == 'tisan'):
            return
        # Workspace may request Radix work only for an enabled tenant/product.
        key = tank.TANK_API_KEY
        if not key or not supplied or not hmac.compare_digest(supplied.encode(), key.encode()):
            raise HTTPException(401, 'authentication required')
        if request.headers.get('x-tervory-squad') != 'radix' or not request.headers.get('x-tervory-user'):
            raise HTTPException(403, 'Radix access required')
        founders = __import__("trigger_signin_config").founders()
        if operation in {'active','infra','consent','consent-withdraw'}:
            user=request.headers['x-tervory-user']
            if user not in founders:
                if request.headers.get('x-tervory-role')!='org:admin':
                    raise HTTPException(403,'Radix access required')
                async with tank.pool().acquire() as conn:
                    if not await conn.fetchval('SELECT 1 FROM tank.tenant_squad WHERE tenant_id=$1 AND agent=$2',tenant,'radix'):
                        raise HTTPException(403,'Radix access required')
            from trigger_commands import CONFIRMED
            bound=CONFIRMED.get()
            tool={'infra':'radix.infra.scan','active':'radix.active.scan','consent':'radix.active.consent','consent-withdraw':'radix.active.consent.withdraw'}[operation]
            if not bound or bound[:2]!=(tenant,user) or bound[3]!=tool:
                raise HTTPException(409,'Confirmed action required')
            return
        if operation in {'watch','unwatch','watches','changes'} and request.headers['x-tervory-user'] in founders:
            return
        async with tank.pool().acquire() as conn:
            if not await conn.fetchval('SELECT 1 FROM tank.tenant_squad WHERE tenant_id=$1 AND agent=$2', tenant, 'radix'):
                raise HTTPException(403, 'Radix access required')

    async def forward(request, tenant, operation, *, body=None, params=None, method='POST'):
        await authorize(request, tenant, operation.split('/')[0])
        if operation in {'active','infra','consent','consent-withdraw'}:
            from trigger_commands import CONFIRMED
            if CONFIRMED.get()[4] != {key:value for key,value in body.items() if key!='tenant_id'}:
                raise HTTPException(409, 'Confirmed action required')
        key = os.environ.get('TANK_KEY_RADIX', '')
        if not key or tank._http is None:
            raise HTTPException(503, 'Radix unavailable')
        try:
            response = await tank._http.request(method, 'http://agent-radix:8000/passive/' + operation,
                json=body, params=params, timeout=1230 if operation in {'active','infra'} else 390 if operation == 'scans' else 15,
                headers={'X-Tank-Key': key, 'X-Tervory-Tenant': str(tenant),
                    'X-Tervory-Founder':str(request.headers.get('x-tervory-user','') in __import__("trigger_signin_config").founders()).lower(),
                    **({'X-Tervory-Active-Authorized':'true','X-Tervory-User':request.headers['x-tervory-user'],
                        'X-Tervory-Active-Client':str(request.headers['x-tervory-user'] not in __import__("trigger_signin_config").founders()).lower()}
                        if operation in {'active','infra','consent','consent-withdraw'} else {})}, follow_redirects=False)
            if response.status_code not in {200,201}:
                detail = response.json().get('detail')
                if response.status_code == 403 and isinstance(detail, str) and detail in {
                    'domain proof required', 'scan not authorized'
                }:
                    raise HTTPException(403, 'domain proof required')
                if response.status_code == 422 and isinstance(detail, list) and any(
                    item.get('loc') == ['body', 'artifact'] and item.get('msg') == 'Value error, invalid artifact'
                    for item in detail if isinstance(item, dict)
                ):
                    raise HTTPException(422, 'invalid artifact')
                raise HTTPException(response.status_code if response.status_code in {403,404,409,422} else 503,
                                    'Radix request incomplete')
            return JSONResponse(response.json(), status_code=response.status_code,
                                headers={'Cache-Control': 'private, no-store'})
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, 'Radix unavailable') from None

    @router.post('/{operation}')
    async def call(operation: str, body: Body, request: Request):
        if operation not in {'domains','scans','gate','watch','unwatch','active','infra','consent','consent-withdraw'}:
            raise HTTPException(404)
        payload = body.model_dump(mode='json', exclude_none=True)
        required = ({'tenant_id','domain','consent_text'} if operation=='consent' else {'tenant_id','domain'} if operation=='consent-withdraw' else {'tenant_id','url'} if operation in {'active','infra'} else {'tenant_id','domain','interval'} if operation == 'watch' else
                    {'tenant_id','domain'} if operation in {'domains','unwatch'} else
                    {'tenant_id','target','artifact'})
        if not required <= set(payload) or set(payload) - required - ({'run_now'} if operation == 'watch' else set()):
            raise HTTPException(422)
        return await forward(request, body.tenant_id, operation, body=payload)

    @router.get('/watches')
    async def watches(tenant_id: UUID, request: Request):
        return await forward(request, tenant_id, 'watches', params={'tenant_id': str(tenant_id)}, method='GET')

    @router.get('/changes')
    async def changes(tenant_id: UUID, domain: str, request: Request):
        return await forward(request, tenant_id, 'changes',
                             params={'tenant_id': str(tenant_id), 'domain': domain}, method='GET')

    @router.get('/scans/{scan_id}/findings')
    async def findings(scan_id: UUID, tenant_id: UUID, request: Request):
        return await forward(request, tenant_id, f'scans/{scan_id}/findings',
                             params={'tenant_id': str(tenant_id)}, method='GET')

    return router
