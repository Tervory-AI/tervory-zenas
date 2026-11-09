"""Tank-owned HF Jobs execution after Rig's durable GPU reservation and claim."""
import asyncio
import json
import os
import re
from urllib.parse import parse_qs, unquote, urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

import controls
from agent_skills import local_region, room_key
from rig_forge_routes import Scope


class Launch(Scope):
    namespace: str = Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}$')
    transfers: dict


def provider():
    token = os.environ.get('HF_JOBS_TOKEN', '').strip()
    if not token:
        raise HTTPException(503, 'HF_JOBS_TOKEN is not configured.')
    from huggingface_hub import HfApi
    return HfApi(endpoint='https://huggingface.co', token=token)


def transfers(body, row, grant):
    value = body.transfers
    if set(value) != {'manifest', 'get', 'put'} or len(json.dumps(value).encode()) > 120_000:
        raise HTTPException(422, 'Invalid Forge transfers.')
    manifest = value['manifest']
    if (not isinstance(manifest, dict) or manifest.get('tenant_id') != str(body.tenant_id)
            or manifest.get('job_id') != str(body.job_id) or manifest.get('home_region') != body.home_region
            or manifest.get('method') != row['method'] or manifest.get('timeout_seconds') != grant['timeout_seconds']):
        raise HTTPException(403, 'GPU inputs do not match the reservation.')
    names = manifest.get('inputs')
    if (not isinstance(names, list) or not 1 <= len(names) <= 150
            or any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}', name) for name in names)
            or len(set(names)) != len(names)
            or not isinstance(value['get'], dict) or not isinstance(value['put'], dict)
            or set(value['get']) != set(names) or set(value['put']) != {'result.glb', 'source.zip'}):
        raise HTTPException(422, 'Invalid Forge object names.')
    account = os.environ.get('R2_ACCOUNT_ID', '')
    if not re.fullmatch(r'[a-f0-9]{32}', account):
        raise HTTPException(503, 'R2_ACCOUNT_ID is not configured.')
    for kind, area in [('get', 'inputs'), ('put', 'gpu')]:
        for name, address in value[kind].items():
            try:
                if not isinstance(address, str) or len(address) > 8192:
                    raise ValueError('Invalid capability')
                url = urlsplit(address)
                query = parse_qs(url.query, strict_parsing=True)
                path = f'/tervory-rig/forge/{body.tenant_id}/{body.job_id}/{area}/{name}'
                valid = (url.scheme == 'https' and url.hostname == account + '.r2.cloudflarestorage.com'
                    and url.port in {None, 443} and not url.username and not url.password and not url.fragment
                    and unquote(url.path) == path and re.fullmatch('[a-f0-9]{64}', query['X-Amz-Signature'][0])
                    and 1 <= int(query['X-Amz-Expires'][0]) <= grant['timeout_seconds'] + 300)
                if not valid:
                    raise ValueError('Invalid capability')
            except (KeyError, IndexError, ValueError, TypeError):
                raise HTTPException(422, 'Invalid Forge capability scope.') from None
    return json.dumps(value, separators=(',', ':'))


def build_router(tank):
    router = APIRouter(prefix='/v1/agents/rig/forge')

    def auth(body, request):
        room_key('rig', request.headers.get('authorization', ''), request.headers.get('x-tank-key', ''))
        local_region(tank, body.home_region)

    async def owned(conn, body, *, lock=False):
        row = await conn.fetchrow('''SELECT * FROM tank.rig_forge_jobs WHERE tenant_id=$1 AND id=$2'''
                                  + (' FOR UPDATE' if lock else ''), body.tenant_id, body.job_id)
        if row is None:
            raise HTTPException(404, 'Forge reservation not found.')
        return row

    @router.post('/launch')
    async def launch(body: Launch, request: Request):
        auth(body, request)
        await controls.refuse_paid(task='rig.forge.gpu', path='rig.forge.launch')
        namespace = os.environ.get('HF_JOBS_NAMESPACE', '').strip()
        if not namespace or namespace != body.namespace:
            raise HTTPException(503, 'HF_JOBS_NAMESPACE is not configured for this job.')
        api = provider()
        async with tank.pool().acquire() as conn:
            async with conn.transaction():
                row = await owned(conn, body, lock=True)
                if row['state'] != 'claimed' or row['provider_started']:
                    raise HTTPException(409, 'This reservation cannot launch another GPU job.')
                if row['method'] == 'trellis2' and os.environ.get('RIG_FORGE_TRELLIS_ENABLED', '').lower() != 'true':
                    raise HTTPException(503, 'RIG_FORGE_TRELLIS_ENABLED is required.')
                grant = json.loads(row['grant_payload']) if isinstance(row['grant_payload'], str) else row['grant_payload']
                secret = transfers(body, row, grant)
                # Commit the once-only claim BEFORE an external request. A timeout
                # or lost reply leaves the reservation uncertain, never retryable.
                await conn.execute('''UPDATE tank.rig_forge_jobs SET provider_started=true,provider_namespace=$3
                    WHERE tenant_id=$1 AND id=$2''', body.tenant_id, body.job_id, namespace)
        try:
            job = await asyncio.wait_for(asyncio.to_thread(api.run_job, image=grant['image'],
                command=['python', '/opt/forge/gpu/entrypoint.py', row['method']],
                env={'FORGE_TENANT_ID': str(body.tenant_id), 'FORGE_JOB_ID': str(body.job_id), 'HF_HUB_DISABLE_TELEMETRY': '1'},
                secrets={'FORGE_TRANSFERS': secret}, namespace=namespace, flavor=grant['flavor'],
                timeout=grant['timeout_seconds']), timeout=30)
            identifier = job.id
            if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identifier):
                raise ValueError('Invalid provider identifier')
            await tank.pool().execute('''UPDATE tank.rig_forge_jobs SET provider_job_id=$3
                WHERE tenant_id=$1 AND id=$2''', body.tenant_id, body.job_id, identifier)
            return {'provider_job_id': identifier}
        except Exception:
            raise HTTPException(503, 'The GPU launch is unconfirmed. Its reservation remains recorded.') from None

    @router.post('/inspect')
    async def inspect(body: Scope, request: Request):
        auth(body, request)
        async with tank.pool().acquire() as conn:
            row = await owned(conn, body)
        if not row['provider_job_id']:
            raise HTTPException(409, 'The GPU launch is unconfirmed. Do not launch it again.')
        try:
            job = await asyncio.wait_for(asyncio.to_thread(provider().inspect_job,
                job_id=row['provider_job_id'], namespace=row['provider_namespace']), timeout=30)
            stage = job.status.stage
            if stage not in {'PENDING', 'RUNNING', 'COMPLETED', 'ERROR', 'FAILED', 'CANCELED', 'DELETED'}:
                raise ValueError('Unknown job status')
            return {'provider_job_id': row['provider_job_id'], 'stage': stage}
        except Exception:
            raise HTTPException(503, 'The GPU status is unavailable. No completion is claimed.') from None

    @router.post('/cancel')
    async def cancel(body: Scope, request: Request):
        auth(body, request)
        async with tank.pool().acquire() as conn:
            row = await owned(conn, body)
        if not row['provider_job_id']:
            raise HTTPException(409, 'The GPU launch is unconfirmed. Its reservation remains recorded.')
        try:
            await asyncio.wait_for(asyncio.to_thread(provider().cancel_job,
                job_id=row['provider_job_id'], namespace=row['provider_namespace']), timeout=30)
            return {'provider_job_id': row['provider_job_id'], 'cancelled': True}
        except Exception:
            raise HTTPException(503, 'The GPU cancellation is unconfirmed. Its reservation remains recorded.') from None

    return router
