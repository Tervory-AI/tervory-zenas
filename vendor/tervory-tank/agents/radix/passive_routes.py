"""Private passive-scan API. Every execution requires a fresh gateway DNS proof."""
from __future__ import annotations

import asyncio
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, field_validator

from passive_store import DDL, Store, artifact_id, domain_name, target_url
from passive_tools import collect
from verify.routes import require_tank

GATEWAY = 'http://passive-egress:8081'


class Domain(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: UUID
    domain: str

    @field_validator('domain')
    @classmethod
    def domain_valid(cls, value):
        return domain_name(value)


class Scan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: UUID
    target: str
    artifact: str

    @field_validator('target')
    @classmethod
    def target_valid(cls, value):
        return target_url(value)[0]

    @field_validator('artifact')
    @classmethod
    def artifact_valid(cls, value):
        return artifact_id(value)


class Active(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: UUID
    url: str


class Consent(Domain):
    consent_text: str


class Watch(Domain):
    interval: Literal['daily', 'weekly']
    run_now: bool = True


async def execute(store, body: Scan, *, scanner=collect, http=None, watched=False, ident=None) -> dict:
    queued = ident is not None
    ident = ident or (await store.create(body.tenant_id, body.target, body.artifact, watched=True) if watched else await store.create(body.tenant_id, body.target, body.artifact))
    lease = {'tenant_id': str(body.tenant_id), 'scan_id': str(ident)}
    async with httpx.AsyncClient(trust_env=False, timeout=15) if http is None else http as client:
        try:
            started = time.monotonic()
            async with asyncio.timeout(410 if watched else 360):
                response = await client.post(GATEWAY + '/lease', json=lease)
                if response.status_code != 200 or response.json().get('ready') is not True:
                    raise HTTPException(response.status_code if response.status_code in {403,409} else 503, 'scan not authorized')
                with tempfile.TemporaryDirectory(prefix='radix-passive-') as directory:
                    measurements = None
                    findings = await scanner(body.target, Path(directory))
                    if watched and scanner is collect:
                        from passive_r6 import collect as collect_watch, Budget
                        previous = await store.previous_hashes(body.tenant_id, target_url(body.target)[1])
                        measurements = await collect_watch(body.target, Path(directory), previous=previous,
                            budget=Budget(max(0, 400 - (time.monotonic() - started))))
                        findings.extend(measurements['findings'])
                    elif isinstance(findings, dict):
                        measurements, findings = findings, findings['findings']
                if measurements is None:
                    await store.complete(body.tenant_id, ident, findings)
                else:
                    await store.complete(body.tenant_id, ident, findings, measurements=measurements)
                if queued or watched:
                    # Other events may already have later pending rows. A queued
                    # run reports its own completion, not the latest origin gate.
                    return {'scan_id': str(ident), 'status': 'complete', 'findings': findings}
                result = await store.gate(body.tenant_id, body.target, body.artifact)
                if result.get('scan_id') != str(ident):
                    raise RuntimeError('assessment superseded')
                return {**result, 'findings': findings}
        except BaseException:
            await asyncio.shield(store.fail(body.tenant_id, ident))
            raise
        finally:
            try:
                await asyncio.shield(client.post(GATEWAY + '/release', json=lease))
            except Exception:
                # Gateway deadline closes old tunnels independently; never declare success here.
                pass


def attach(app):
    router = APIRouter(prefix='/passive', dependencies=[Depends(require_tank)])

    def identity(request, tenant):
        if request.headers.get('x-tervory-tenant') != str(tenant):
            raise HTTPException(403, 'tenant mismatch')

    @router.post('/domains', status_code=201)
    async def issue(body: Domain, request: Request):
        identity(request, body.tenant_id)
        return await Store(app.state.pool).issue(body.tenant_id, body.domain,own=request.headers.get('x-tervory-founder')=='true')

    @router.post('/scans')
    async def scan(body: Scan, request: Request):
        identity(request, body.tenant_id)
        try:
            return await execute(Store(app.state.pool), body)
        except PermissionError:
            raise HTTPException(403, 'domain proof required') from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, 'passive scan incomplete') from None

    @router.post('/gate')
    async def gate(body: Scan, request: Request):
        identity(request, body.tenant_id)
        return await Store(app.state.pool).gate(body.tenant_id, body.target, body.artifact)

    @router.get('/scans/{scan_id}/findings')
    async def findings(scan_id: UUID, tenant_id: UUID, request: Request):
        identity(request, tenant_id)
        store = Store(app.state.pool)
        return {'findings': await store.findings(tenant_id, scan_id),
                **await store.active_state(tenant_id, scan_id)}


    @router.get('/scans/{scan_id}/report')
    async def report(scan_id: UUID,tenant_id: UUID,request: Request):
        identity(request,tenant_id)
        try:
            return await Store(app.state.pool).report(tenant_id,scan_id)
        except LookupError:
            raise HTTPException(404,'scan not found') from None

    @router.post('/consent')
    async def consent(body: Consent, request: Request):
        identity(request,body.tenant_id)
        if request.headers.get('x-tervory-active-authorized')!='true' or not request.headers.get('x-tervory-user'):
            raise HTTPException(403,'scan not authorized')
        return await Store(app.state.pool).consent(body.tenant_id,body.domain,request.headers['x-tervory-user'],body.consent_text)

    @router.post('/consent-withdraw')
    async def withdraw(body: Domain, request: Request):
        identity(request,body.tenant_id)
        if request.headers.get('x-tervory-active-authorized')!='true' or not request.headers.get('x-tervory-user'):
            raise HTTPException(403,'scan not authorized')
        result=await Store(app.state.pool).withdraw_consent(body.tenant_id,body.domain,request.headers['x-tervory-user'])
        try:
            async with httpx.AsyncClient(trust_env=False,timeout=15) as client:
                # No retry of a write: consent is already withdrawn durably.
                response=await client.post(GATEWAY+'/active/revoke',json={'tenant_id':str(body.tenant_id),'scan_id':str(UUID(int=0))})
                response.raise_for_status()
        except httpx.HTTPError:
            raise HTTPException(503, 'scan egress unavailable') from None
        return result

    @router.post('/infra')
    @router.post('/active')
    async def active_scan(body: Active, request: Request):
        identity(request, body.tenant_id)
        if request.headers.get('x-tervory-active-authorized') != 'true':
            raise HTTPException(403, 'scan not authorized')
        from active_routes import execute as execute_active
        try:
            kwargs={}
            if request.url.path.endswith('/infra'):
                from passive_infra_tools import collect as collect_infra
                kwargs={'scanner':collect_infra,'infra':True}
            return await execute_active(Store(app.state.pool), body.tenant_id, body.url,
                client=request.headers.get('x-tervory-active-client') == 'true',**kwargs)
        except (PermissionError, ValueError):
            raise HTTPException(403, 'scan not authorized') from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, 'scan incomplete') from None

    @router.post('/watch')
    async def watch(body: Watch, request: Request):
        identity(request, body.tenant_id)
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
                response = await client.post(GATEWAY + '/proof', json={
                    'tenant_id': str(body.tenant_id), 'domain': body.domain})
            ready = response.json() if response.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, 'domain proof required') from None
        if response.status_code != 200 or not isinstance(ready, dict) or ready.get('ready') is not True:
            raise HTTPException(403 if response.status_code == 403 else 503, 'domain proof required')
        try:
            result = await Store(app.state.pool).watch(body.tenant_id, body.domain, body.interval, run_now=body.run_now)
        except PermissionError:
            raise HTTPException(403, 'domain proof required') from None
        if request.headers.get('x-tervory-founder')=='true':
            await Store(app.state.pool).enroll_weekly(body.tenant_id,body.domain,own=True)
        return {**result, 'queued': body.run_now}

    @router.post('/unwatch')
    async def unwatch(body: Domain, request: Request):
        identity(request, body.tenant_id)
        return await Store(app.state.pool).unwatch(body.tenant_id, body.domain)

    @router.get('/watches')
    async def watches(tenant_id: UUID, request: Request):
        identity(request, tenant_id)
        return {'items': await Store(app.state.pool).watches(tenant_id)}

    @router.get('/changes')
    async def changes(tenant_id: UUID, domain: str, request: Request):
        identity(request, tenant_id)
        return await Store(app.state.pool).changes(tenant_id, domain)

    app.include_router(router)
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with inner(app):
            async with app.state.pool.acquire() as conn:
                await conn.execute(DDL)
            yield

    app.router.lifespan_context = lifespan
