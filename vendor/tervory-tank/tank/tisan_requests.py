"""Tenant-admin requests and sign-off; founder-confirmed requests reuse the room pipeline."""
import json
import os
import re
from types import SimpleNamespace
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
import httpx
from pydantic import BaseModel, ConfigDict, Field
from tenant_db import tenant_path, tenant_transaction

TOOLS = ['tisan.request', 'tisan.preview', 'tisan.feedback', 'tisan.handover']
MOVES = {'requested': {'changes_requested', 'accepted'},
         'changes_requested': {'changes_requested', 'accepted'}, 'accepted': set()}


class RequestIdArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')
    slug: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class FeedbackArgs(RequestIdArgs):
    text: str = Field(min_length=1, max_length=4000)


def refused(code: str, status: int = 409):
    raise HTTPException(status, {'status': code})


def transition(row: dict, target: str, published: dict) -> None:
    if target not in MOVES.get(row['state'], set()):
        refused('invalid_transition')
    # Acceptance after feedback must advance the build as well as the state;
    # the build already sent back cannot be signed off unchanged.
    if row['build'] is not None and (published['build'] <= row['build'] or published['commit'] == row['commit']):
        refused('new_build_required')


async def latest(tenant: UUID, slug: str) -> dict:
    from main import _http
    key = os.environ.get('TANK_KEY_TISAN', '')
    if _http is None or not key:
        refused('unavailable', 503)
    try:
        response = await _http.get('http://agent-tisan:8000/publish/latest',
            params={'tenant_id': str(tenant), 'slug': slug}, headers={'X-Tank-Key': key}, timeout=15)
        if response.status_code != 200:
            refused('unavailable', 503)
        data = response.json()
        if data == {'status': 'unavailable'}:
            return data
        # Return only the journal's verified successful evidence, never body errors.
        from urllib.parse import urlsplit
        url = urlsplit(data['url'])
        if (data['status'] != 'succeeded' or type(data['build']) is not int or data['build'] < 1
                or not re.fullmatch(r'[0-9a-f]{40}', data['commit'])
                or url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.path not in ('', '/') or url.query or url.fragment or url.port not in (None, 443)):
            refused('unavailable', 503)
        return {k: data[k] for k in ('status', 'build', 'commit', 'url')}
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        refused('unavailable', 503)


class Store:
    def __init__(self, pool):
        self.pool = pool

    @tenant_path('tenant')
    async def create(self, tenant, actor, operation, data):
        async with tenant_transaction(self.pool) as conn:
            row = await conn.fetchrow(
                "INSERT INTO tank.tisan_requests(tenant_id,request_id,actor,data,state) "
                "VALUES($1,$2,$3,$4::jsonb,'requested') ON CONFLICT DO NOTHING RETURNING request_id",
                tenant, operation, actor, json.dumps(data))
            if row is None:
                old = await self.get(conn, tenant, operation)
                if old['actor'] != actor or obj(old['data']) != data:
                    refused('operation_changed')
                return {'request_id': str(operation), 'status': old['state'], 'input': data}
            await conn.execute("INSERT INTO tank.tisan_request_events(tenant_id,event_id,request_id,kind,actor) "
                               "VALUES($1,$2,$2,'requested',$3)", tenant, operation, actor)
        return {'request_id': str(operation), 'status': 'requested', 'input': data}

    async def get(self, conn, tenant, request):
        row = await conn.fetchrow("SELECT * FROM tank.tisan_requests WHERE tenant_id=$1 AND request_id=$2 FOR UPDATE", tenant, request)
        if row is None:
            refused('not_found', 404)
        return dict(row)

    async def open_request(self, conn, tenant, slug):
        row = await conn.fetchrow("SELECT * FROM tank.tisan_requests WHERE tenant_id=$1 AND data->>'slug'=$2 "
                                  "AND state IN ('requested','changes_requested') "
                                  "ORDER BY created_at DESC,request_id DESC LIMIT 1 FOR UPDATE", tenant, slug)
        if row is None:
            refused('not_found', 404)
        return dict(row)

    @tenant_path('tenant')
    async def preview(self, tenant, slug):
        async with tenant_transaction(self.pool) as conn:
            row = await self.open_request(conn, tenant, slug)
            published = await latest(tenant, slug)
            return evidence(row['request_id'], row['state'], published)

    @tenant_path('tenant')
    async def move(self, tenant, actor, operation, slug, target, text=None):
        async with tenant_transaction(self.pool) as conn:
            row = await self.open_request(conn, tenant, slug)
            request = row['request_id']
            prior = await conn.fetchrow("SELECT request_id,kind,actor,build,\"commit\",text FROM tank.tisan_request_events "
                                        "WHERE tenant_id=$1 AND event_id=$2", tenant, operation)
            if prior:
                if (prior['request_id'], prior['kind'], prior['actor'], prior['text']) != (request, target, actor, text):
                    refused('operation_changed')
                return evidence(request, prior['kind'], dict(prior))
            if target not in MOVES.get(row['state'], set()):
                refused('invalid_transition')
            published = await latest(tenant, obj(row['data'])['slug'])
            if published['status'] != 'succeeded':
                refused('unavailable')
            transition(row, target, published)
            await conn.execute("INSERT INTO tank.tisan_request_events(tenant_id,event_id,request_id,kind,actor,build,\"commit\",text) "
                               "VALUES($1,$2,$3,$4,$5,$6,$7,$8)", tenant, operation, request, target, actor,
                               published['build'], published['commit'], text)
            await conn.execute("UPDATE tank.tisan_requests SET state=$3,build=$4,\"commit\"=$5,updated_at=now() "
                               "WHERE tenant_id=$1 AND request_id=$2", tenant, request, target, published['build'], published['commit'])
        return evidence(request, target, published)


def evidence(request, state, published):
    result = {'request_id': str(request), 'status': state}
    if published.get('build') is not None:
        result.update(build=published['build'], build_id=published['build'], commit=published['commit'])
    if published.get('url'):
        result['url'] = published['url']
    return result


def obj(value):
    return json.loads(value) if isinstance(value, str) else value


async def code_plan(actor, messages, task_id):
    from command import authorized_actor, PlannedAction, validate_action
    from trigger_routes import code_receipt
    source = messages[-1] if messages else {}
    if source.get('role') != 'user':
        return None
    match = re.fullmatch(r'\s*tisan\.(request|preview|feedback|handover)\s*:?\s+(.+)', source.get('content', ''), re.I | re.S)
    if not match:
        return None
    actor = await authorized_actor(actor)
    tool, raw = 'tisan.' + match[1].lower(), match[2]
    try:
        args = {'slug': raw.strip()} if tool in ('tisan.preview', 'tisan.handover') and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', raw.strip()) else json.loads(raw)
    except ValueError:
        refused('invalid_arguments', 422)
    if not isinstance(args, dict):
        refused('invalid_arguments', 422)
    action = validate_action(PlannedAction(tool=tool, arguments=args), actor)
    return SimpleNamespace(content=json.dumps({'text': '', 'actions': [action.model_dump(mode='json')]}),
        ledger_id=await code_receipt(actor.tenant_id, 'command.turn', task_id=task_id, agent='tisan'))


async def queue_pipeline(actor, operation, data):
    from main import _http
    key = os.environ.get('TANK_KEY_TISAN', '')
    if _http is None or not key:
        refused('pipeline_unavailable', 503)
    try:
        dispatched = await _http.post('http://agent-tisan:8000/pipeline/start', json={
            'tenant_id': str(actor.tenant_id), 'request_id': str(operation), 'page_id': data.get('page_id'),
            'brief': {**{k: v for k, v in data.items() if k not in {'page_id', 'assembly'}},
                      **data.get('assembly', {})}},
            headers={'X-Tank-Key': key, 'X-Tervory-Tenant': str(actor.tenant_id),
                     'X-Tervory-Admin': actor.user_id}, timeout=30, follow_redirects=False)
        if dispatched.status_code != 200:
            refused('pipeline_unavailable', 503)
        job = dispatched.json()
        if (not isinstance(job, dict) or job.get('tenant_id') != str(actor.tenant_id)
                or job.get('request_id') != str(operation) or job.get('job_id') != str(operation)
                or job.get('kind') != 'pipeline' or job.get('status') not in {'queued', 'running', 'done', 'failed'}):
            refused('pipeline_unavailable', 503)
        return {key: job[key] for key in ('job_id', 'kind', 'status')}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        refused('pipeline_unavailable', 503)


def build_router():
    from command import Actor, TisanRequestArgs, db, identity, require_tool
    from trigger_commands import confirmation
    from trigger_routes import code_receipt
    router = APIRouter(prefix='/v1/command/tisan', tags=['tisan'])
    # Local annotation must be available to FastAPI's signature resolver.
    globals()['TisanRequestArgs'] = TisanRequestArgs
    globals()['Actor'] = Actor

    async def result(actor, tool, data):
        return {**data, 'ledger_id': str(await code_receipt(actor.tenant_id, tool, agent='tisan')), 'cost_usd': 0}

    @router.post('/request')
    async def request(args: TisanRequestArgs, actor: Annotated[Actor, Depends(identity)]):
        require_tool(actor, 'tisan.request')
        from command import current_actor
        actor = current_actor(actor)
        data = args.model_dump(mode='json', exclude_none=True)
        if (data.get('page_id') or data.get('assembly')) and not actor.founder:
            refused('founder_required', 403)
        operation = confirmation(actor, 'tisan.request', data)
        saved = await Store(db()).create(actor.tenant_id, actor.user_id, operation, data)
        if actor.founder:
            saved.update(await queue_pipeline(actor, operation, data))
        return await result(actor, 'tisan.request', saved)

    @router.get('/preview')
    async def preview(slug: Annotated[str, Field(min_length=1, max_length=60, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")], actor: Annotated[Actor, Depends(identity)]):
        require_tool(actor, 'tisan.preview')
        return await result(actor, 'tisan.preview', await Store(db()).preview(actor.tenant_id, slug))

    @router.post('/feedback')
    async def feedback(args: FeedbackArgs, actor: Annotated[Actor, Depends(identity)]):
        require_tool(actor, 'tisan.feedback')
        operation = confirmation(actor, 'tisan.feedback', args.model_dump(mode='json', exclude_none=True))
        return await result(actor, 'tisan.feedback', await Store(db()).move(actor.tenant_id, actor.user_id, operation,
                            args.slug, 'changes_requested', args.text))

    @router.post('/handover')
    async def handover(args: RequestIdArgs, actor: Annotated[Actor, Depends(identity)]):
        require_tool(actor, 'tisan.handover')
        operation = confirmation(actor, 'tisan.handover', args.model_dump(mode='json', exclude_none=True))
        return await result(actor, 'tisan.handover', await Store(db()).move(actor.tenant_id, actor.user_id, operation,
                            args.slug, 'accepted'))
    router.include_router(__import__('tisan_pipeline').build_router())
    return router


async def build_result(tenant: UUID, result: dict) -> dict:
    """Hydrate a long build's existing card through the existing room job read."""
    from main import _http
    if result.get('kind') != 'pipeline' and (result.get('kind') != 'build'
            or result.get('status') not in ('queued', 'running')):
        return result
    key = os.environ.get('TANK_KEY_TISAN', '')
    if _http is None or not key:
        return result
    try:
        job_id = str(UUID(result['job_id']))
        if result.get('kind') == 'pipeline':
            response = await _http.get('http://agent-tisan:8000/pipeline/status/' + job_id,
                params={'tenant_id': str(tenant)}, headers={'X-Tank-Key': key}, timeout=15, follow_redirects=False)
            if response.status_code != 200 or len(response.content) > 256000:
                return result
            measured = response.json()
            if measured.get('tenant_id') != str(tenant) or measured.get('request_id') != job_id:
                return result
            return {**result, **{k: v for k, v in measured.items() if k in {
                'status', 'pipeline_status', 'steps', 'checks', 'results', 'production', 'ok', 'slug', 'build',
                'memory_id', 'url', 'commit', 'detail', 'summary', 'copy_needed', 'media_needed'}}}
        response = await _http.get('http://agent-tisan:8000/jobs/' + job_id,
            params={'tenant_id': str(tenant)}, headers={'X-Tank-Key': key,
                'X-Tervory-Tenant': str(tenant)}, timeout=10, follow_redirects=False)
        if response.status_code != 200 or len(response.content) > 256000:
            return result
        job = response.json()
        if (str(UUID(job['tenant_id'])) != str(tenant) or str(UUID(job['id'])) != job_id
                or job.get('kind') != 'build' or job.get('status') != 'done' or not isinstance(job.get('result'), dict)):
            return result
        measured = {k: v for k, v in job['result'].items() if k in {
            'slug', 'name', 'ok', 'checks', 'detail', 'archive', 'build', 'memory_id', 'refused',
            'copy_needed', 'media_needed', 'token_set', 'files'}}
        if 'build' in measured:
            measured['build_id'] = measured['build']
        return {**result, **measured, 'status': 'done'}
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        return result
