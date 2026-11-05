"""Deployment-owned Trigger sign-in resource registry; no user provisioning by Tank."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
import os
import time
import uuid

import httpx

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from tenant_db import tenant_scope

ISSUER = 'https://auth.tervory.com'
ISSUERS = dict.fromkeys(('us-east', 'za'), ISSUER)
PLATFORM = uuid.UUID('00000000-0000-0000-0000-000000000001')
_records: dict[str, dict] = {}
_expires = 0.0
_lock = asyncio.Lock()
_issuer: ContextVar[str] = ContextVar('trigger_signin_issuer', default='')
proof: ContextVar[dict[str, str]] = ContextVar('trigger_signin_proof', default={})


def setup_authorized(request: Request) -> bool:
    from command import key_matches
    from main import box_region
    return box_region() == 'us-east' and key_matches(request.headers.get('x-trigger-signin-setup', ''), os.environ.get('TRIGGER_SIGNIN_SETUP_TOKEN', ''))


def reader_authorized(request: Request) -> bool:
    from command import key_matches
    return key_matches(request.headers.get('x-trigger-signin-config', ''), os.environ.get('TRIGGER_SIGNIN_CONFIG_TOKEN', ''))


def http_client():
    return httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False)


async def remote_tenants() -> set[uuid.UUID]:
    token = os.environ.get('TRIGGER_SIGNIN_CONFIG_TOKEN', '')
    if not token:
        raise HTTPException(503, 'Trigger sign-in tenant directory is unavailable')
    result = set()
    try:
        async with http_client() as client:
            for offset in range(0, 100000, 500):
                response = await client.get('https://za.tervory.com/v1/case-studies/clients',
                    headers={'x-trigger-signin-config': token}, params={'offset': offset, 'limit': 500})
                response.raise_for_status()
                page = response.json()
                for tenant in page['clients']:
                    ident = uuid.UUID(tenant['tenant_id'])
                    if ident in result:
                        raise ValueError
                    result.add(ident)
                if page['has_more'] is False:
                    return result
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass
    raise HTTPException(503, 'Trigger sign-in tenant directory is unavailable')


async def require_setup(request: Request):
    if not setup_authorized(request):
        raise HTTPException(404, 'not found')


def config(region: str) -> dict:
    record = _records.get('us-east') if region in ISSUERS else None
    if record is None:
        raise HTTPException(503, 'Trigger sign-in setup is incomplete')
    return record


def founders() -> set[str]:
    issuer = _issuer.get()
    return {subject for record in _records.values() if record['issuer'] == issuer for subject in record['founders']}


def issuer() -> str:
    return _issuer.get()


async def refresh(*, force: bool = False):
    global _records, _expires
    if not force and _expires > time.monotonic():
        return
    async with _lock:
        if not force and _expires > time.monotonic():
            return
        from main import box_region
        if box_region() == 'za':
            try:
                token = os.environ.get('TRIGGER_SIGNIN_CONFIG_TOKEN', '')
                if not token:
                    raise ValueError
                async with http_client() as client:
                    response = await client.get('https://app.tervory.com/api/trigger-signin/config',
                        headers={'x-trigger-signin-config': token})
                    response.raise_for_status()
                    records = response.json()['regions']
                record = records['us-east']
                Deployment.model_validate({key: record[key] for key in (
                    'region', 'issuer', 'client_id', 'workspace_client_id', 'project_id')} | {
                    'founder_subject': record['founders'][0],
                    'organisations': [{'org_id': org, 'tenant_id': tenant} for org, tenant in record['organisations'].items()]})
                if set(records) != {'us-east'} or record['issuer'] != ISSUER or len(record['founders']) != 1:
                    raise ValueError
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
                raise HTTPException(503, 'Trigger sign-in setup is unavailable') from None
            _records, _expires = records, time.monotonic() + 30
            return
        from command import db
        with tenant_scope(PLATFORM, administrative=True):
            async with db().acquire() as conn:
                rows = await conn.fetch('SELECT region,issuer,client_id,workspace_client_id,project_id FROM tank.trigger_signin_config')
                bindings = await conn.fetch('SELECT region,org_id,tenant_id FROM tank.trigger_signin_organisations')
                owners = await conn.fetch('SELECT region,subject FROM tank.trigger_signin_founders')
        records = {}
        for row in rows:
            if row['region'] != 'us-east' or row['issuer'] != ISSUER:
                continue
            record = dict(row)
            record['organisations'] = {binding['org_id']: str(binding['tenant_id']) for binding in bindings if binding['region'] == row['region']}
            record['founders'] = [owner['subject'] for owner in owners if owner['region'] == row['region']]
            records[row['region']] = record
        _records, _expires = records, time.monotonic() + 30


class Binding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    org_id: str = Field(pattern=r'^\d{1,30}$')
    tenant_id: uuid.UUID


class Deployment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    region: str
    issuer: str
    client_id: str = Field(pattern=r'^[A-Za-z0-9@._-]{1,200}$')
    workspace_client_id: str = Field(pattern=r'^[A-Za-z0-9@._-]{1,200}$')
    project_id: str = Field(pattern=r'^\d{1,30}$')
    founder_subject: str = Field(pattern=r'^[A-Za-z0-9_-]{1,200}$')
    organisations: list[Binding] = Field(max_length=10000)


router = APIRouter(prefix='/v1/trigger-signin', include_in_schema=False)


@router.get('/config')
async def read_config(request: Request, response: Response):
    from command import key_matches
    from main import TANK_API_KEY
    if not setup_authorized(request) and not reader_authorized(request) and not key_matches(request.headers.get('x-tank-key', ''), TANK_API_KEY):
        raise HTTPException(404, 'not found')
    await refresh()
    response.headers['Cache-Control'] = 'private, no-store'
    return {'regions': _records}


@router.put('/config', dependencies=[Depends(require_setup)])
async def write_config(body: Deployment):
    if body.region != 'us-east' or body.issuer != ISSUER:
        raise HTTPException(422, 'Trigger sign-in region refused')
    organisations = {binding.org_id: binding.tenant_id for binding in body.organisations}
    if len(organisations) != len(body.organisations) or len(set(organisations.values())) != len(organisations):
        raise HTTPException(422, 'Trigger sign-in organisations refused')
    from command import db
    remote = await remote_tenants()
    with tenant_scope(PLATFORM, administrative=True):
        async with db().acquire() as conn, conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(72451301)')
            current = await conn.fetchrow('SELECT * FROM tank.trigger_signin_config WHERE region=$1 FOR UPDATE', body.region)
            if current and any(current[name] != getattr(body, name) for name in ('issuer', 'client_id', 'workspace_client_id', 'project_id')):
                raise HTTPException(409, 'Trigger sign-in resources are already bound')
            await conn.execute('''INSERT INTO tank.trigger_signin_config(region,issuer,client_id,workspace_client_id,project_id)
                VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING''', body.region, body.issuer, body.client_id, body.workspace_client_id, body.project_id)
            for org_id, tenant in organisations.items():
                if tenant not in remote and not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM tank.case_study_clients WHERE tenant_id=$1)', tenant):
                    raise HTTPException(422, 'Trigger sign-in tenant does not exist')
                existing = await conn.fetchrow('SELECT org_id,tenant_id FROM tank.trigger_signin_organisations WHERE region=$1 AND (org_id=$2 OR tenant_id=$3)', body.region, org_id, tenant)
                if existing and (existing['org_id'] != org_id or existing['tenant_id'] != tenant):
                    raise HTTPException(409, 'Trigger sign-in tenant is already bound')
                await conn.execute('''INSERT INTO tank.trigger_signin_organisations(region,org_id,tenant_id)
                    VALUES($1,$2,$3) ON CONFLICT DO NOTHING''', body.region, org_id, tenant)
            existing_founder = await conn.fetchval('SELECT subject FROM tank.trigger_signin_founders WHERE region=$1', body.region)
            if existing_founder and existing_founder != body.founder_subject:
                raise HTTPException(409, 'Trigger sign-in founder is already bound')
            await conn.execute('INSERT INTO tank.trigger_signin_founders(region,subject) VALUES($1,$2) ON CONFLICT DO NOTHING', body.region, body.founder_subject)
    await refresh(force=True)
    return {'configured': True}
