"""User-owned window jobs. Existing identity, vault and command receipts only."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, field_validator

import command
from tenant_db import activate_tenant, tenant_scope
from vault import open_seal, seal

PLATFORM = uuid.UUID('00000000-0000-0000-0000-000000000001')
REPO = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*\Z')
SCHEMA = """
CREATE TABLE IF NOT EXISTS tank.window_agent_jobs (
 tenant_id uuid NOT NULL, user_id text NOT NULL, job_id uuid NOT NULL, thread_id uuid NOT NULL,
 repo text NOT NULL, credential_name text NOT NULL, prompt text NOT NULL,
 status text NOT NULL DEFAULT 'queued', lease_hash text, heartbeat timestamptz,
 result jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,job_id));
CREATE TABLE IF NOT EXISTS tank.window_agent_dispatch (
 tenant_id uuid NOT NULL, job_id uuid NOT NULL, owner_tenant uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), claimed boolean NOT NULL DEFAULT false, PRIMARY KEY(tenant_id,job_id));
CREATE INDEX IF NOT EXISTS window_agent_jobs_owner_created ON tank.window_agent_jobs(tenant_id,user_id,created_at DESC);
CREATE INDEX IF NOT EXISTS window_agent_dispatch_pending ON tank.window_agent_dispatch(tenant_id,claimed,created_at);
DO $$ DECLARE tab text; BEGIN
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  FOREACH tab IN ARRAY ARRAY['window_agent_jobs','window_agent_dispatch'] LOOP
   EXECUTE format('ALTER TABLE tank.%I ENABLE ROW LEVEL SECURITY',tab);
   EXECUTE format('ALTER TABLE tank.%I FORCE ROW LEVEL SECURITY',tab);
   IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid=('tank.'||tab)::regclass AND polname='tenant_access') THEN
    EXECUTE format($policy$CREATE POLICY tenant_access ON tank.%I FOR ALL TO PUBLIC USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid) WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)$policy$,tab);
   END IF;
   IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid=('tank.'||tab)::regclass AND polname='tenant_boundary') THEN
    EXECUTE format($policy$CREATE POLICY tenant_boundary ON tank.%I AS RESTRICTIVE FOR ALL TO PUBLIC USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid) WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)$policy$,tab);
   END IF;
  END LOOP;
 END IF;
END $$;
"""
router = APIRouter(prefix='/v1/window-agents', tags=['window agents'])


async def actor_identity(request: Request) -> command.Actor:
    if request.headers.get('authorization'):
        from personal_tokens import bearer_identity
        actor, _ = await bearer_identity(request, audience='cli')
    else:
        actor = await command.identity(request)
    activate_tenant(actor.tenant_id)
    return actor


class Credential(command.Body):
    name: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.-]+$')
    value: str = Field(min_length=20, max_length=500, repr=False)

    @field_validator('value')
    @classmethod
    def token_shape(cls, value):
        if not re.fullmatch(r'(?:github_pat_|gh[pous]_)[A-Za-z0-9_]+', value):
            raise ValueError('Use a GitHub token in the vault form.')
        return value


class Start(command.Body):
    repo: str = Field(max_length=200)
    credential_name: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.-]+$')
    prompt: str = Field(min_length=1, max_length=18000)
    request_id: uuid.UUID

    @field_validator('repo')
    @classmethod
    def valid_repo(cls, value):
        if not REPO.fullmatch(value) or any(p in {'.', '..'} or p.endswith('.git') for p in value.split('/')):
            raise ValueError('Use owner/repository.')
        return value


class Lease(command.Body):
    lease: str = Field(min_length=32, max_length=100, repr=False)


class Finish(Lease):
    status: Literal['done', 'failed', 'superseded']
    pr_number: int | None = Field(default=None, gt=0)
    sha: str | None = Field(default=None, pattern=r'^[a-f0-9]{40}$')


def vault_name(actor):
    # Existing vault_items storage; separate user vaults inside the tenant.
    return 'window.agents.' + hashlib.sha256(actor.user_id.encode()).hexdigest()


def context(actor, name):
    return f'plugins:v1:{actor.tenant_id}:{vault_name(actor)}:secret:{name}'


async def credential(conn, actor, name):
    value = await conn.fetchval("SELECT sealed FROM tank.vault_items WHERE tenant_id=$1 AND vault=$2 AND kind='secrets' AND name=$3",
                                actor.tenant_id, vault_name(actor), name)
    if value is None:
        raise HTTPException(404, 'Named GitHub credential not found in your vault.')
    return open_seal(value, context(actor, name))['value']


@router.post('/vault/github')
async def save_credential(body: Credential, actor: command.Actor = Depends(actor_identity)):
    async with command.db().acquire() as conn:
        await conn.execute("INSERT INTO tank.vault_items(tenant_id,vault,kind,name,metadata,sealed) VALUES($1,$2,'secrets',$3,$4::jsonb,$5) ON CONFLICT(tenant_id,vault,kind,name) DO UPDATE SET sealed=EXCLUDED.sealed,metadata=EXCLUDED.metadata",
                           actor.tenant_id, vault_name(actor), body.name, json.dumps({'name': body.name}), seal({'value': body.value}, context(actor, body.name)))
    return {'saved': True}


@router.get('/vault/github')
async def names(actor: command.Actor = Depends(actor_identity)):
    async with command.db().acquire() as conn:
        rows = await conn.fetch("SELECT name FROM tank.vault_items WHERE tenant_id=$1 AND vault=$2 AND kind='secrets' ORDER BY name", actor.tenant_id, vault_name(actor))
    return [r['name'] for r in rows]


async def enqueue(body: Start, actor, *, kind='task', pull_number=None, target_sha=None):
    async with command.db().acquire() as conn:
        return await enqueue_on(conn, body, actor, kind=kind, pull_number=pull_number, target_sha=target_sha)


async def enqueue_on(conn, body: Start, actor, *, kind='task', pull_number=None, target_sha=None):
    async with conn.transaction():
        # Serialise duplicate requests before creating their receipt thread.
        await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', f'{actor.tenant_id}:{body.request_id}')
        previous = await conn.fetchrow('SELECT * FROM tank.window_agent_jobs WHERE tenant_id=$1 AND job_id=$2', actor.tenant_id, body.request_id)
        if previous:
            if previous['user_id'] != actor.user_id:
                raise HTTPException(404, 'Job not found.')
            if (previous['repo'], previous['credential_name'], previous['prompt'], previous['kind'], previous['pull_number'], previous['target_sha']) != (body.repo, body.credential_name, body.prompt, kind, pull_number, target_sha):
                raise HTTPException(409, 'Request already used for another task.')
            return {'job_id': str(body.request_id)}
        await credential(conn, actor, body.credential_name)
        thread_id = uuid.uuid4()
        state = {'messages': [], 'steps': {}, 'cards': {}, 'receipts': [], 'active_turn': None}
        await conn.execute('INSERT INTO tank.command_threads(thread_id,tenant_id,user_id,title,state) VALUES($1,$2,$3,$4,$5::jsonb)',
                           thread_id, actor.tenant_id, actor.user_id, body.repo, json.dumps(state))
        await conn.execute('INSERT INTO tank.window_agent_jobs(tenant_id,user_id,job_id,thread_id,repo,credential_name,prompt,kind,pull_number,target_sha) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)',
                           actor.tenant_id, actor.user_id, body.request_id, thread_id, body.repo, body.credential_name, body.prompt, kind, pull_number, target_sha)
        await conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(PLATFORM))
        await conn.execute('INSERT INTO tank.window_agent_dispatch(tenant_id,job_id,owner_tenant) VALUES($1,$2,$3)', PLATFORM, body.request_id, actor.tenant_id)
        await conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(actor.tenant_id))
    return {'job_id': str(body.request_id)}


@router.post('/jobs', status_code=202)
async def start(body: Start, actor: command.Actor = Depends(actor_identity)):
    return await enqueue(body, actor)


@router.post('/jobs/{job_id}/cancel')
async def cancel(job_id: uuid.UUID, actor: command.Actor = Depends(actor_identity)):
    async with command.db().acquire() as conn:
        status = await conn.fetchval("UPDATE tank.window_agent_jobs SET status='cancelled' WHERE tenant_id=$1 AND user_id=$2 AND job_id=$3 AND status IN ('queued','running') RETURNING status", actor.tenant_id, actor.user_id, job_id)
        if status is None:
            status = await conn.fetchval('SELECT status FROM tank.window_agent_jobs WHERE tenant_id=$1 AND user_id=$2 AND job_id=$3', actor.tenant_id, actor.user_id, job_id)
    if status is None:
        raise HTTPException(404, 'Job not found.')
    return {'status': status}


@router.get('/jobs')
async def jobs(actor: command.Actor = Depends(actor_identity)):
    async with command.db().acquire() as conn:
        await conn.execute("UPDATE tank.window_agent_jobs SET status='failed' WHERE tenant_id=$1 AND user_id=$2 AND status='running' AND heartbeat < now() - interval '2 minutes'", actor.tenant_id, actor.user_id)
        rows = await conn.fetch("""WITH ranked AS (
            SELECT job_id,thread_id,repo,status,result,created_at,kind,pull_number,
            row_number() OVER(PARTITION BY status IN ('queued','running')
              ORDER BY CASE WHEN status='running' THEN 0 ELSE 1 END,created_at DESC,job_id) AS position
            FROM tank.window_agent_jobs WHERE tenant_id=$1 AND user_id=$2)
            SELECT job_id,thread_id,repo,status,result,created_at,kind,pull_number FROM ranked
            WHERE position<=100 OR status='running'
            ORDER BY CASE WHEN status IN ('queued','running') THEN 0 ELSE 1 END,created_at DESC,job_id""", actor.tenant_id, actor.user_id)
    return [{**dict(row), 'result': command.unpack(row['result'])} for row in rows]


@router.get('/jobs/{job_id}/receipt')
async def receipt(job_id: uuid.UUID, actor: command.Actor = Depends(actor_identity)):
    from fastapi.responses import JSONResponse
    async with command.db().acquire() as conn:
        job = await conn.fetchrow('SELECT thread_id,status FROM tank.window_agent_jobs WHERE tenant_id=$1 AND user_id=$2 AND job_id=$3', actor.tenant_id, actor.user_id, job_id)
        if job is None:
            raise HTTPException(404, 'Job not found.')
        thread = await command.owned(conn, job['thread_id'], actor)
    state = command.unpack(thread['state'])
    rows = [command.public_receipt(row, actor.founder) for row in state['receipts']]
    totals = command.thread_totals(state)
    status = 'pending' if (state.get('active_turn') or totals['cost_usd'] is None or (not rows and job['status'] in {'queued', 'running'})) else 'recorded'
    return JSONResponse({'thread_id': str(job['thread_id']), 'rows': rows, 'totals': totals, 'status': status},
                        headers={'Cache-Control': 'private, no-store'})


async def leased(conn, tenant_id, job_id, lease):
    row = await conn.fetchrow('SELECT * FROM tank.window_agent_jobs WHERE tenant_id=$1 AND job_id=$2', tenant_id, job_id)
    if row is None or not row['lease_hash'] or not hmac.compare_digest(row['lease_hash'], hashlib.sha256(lease.encode()).hexdigest()):
        raise HTTPException(404, 'Job not found.')
    if row['status'] != 'running':
        raise HTTPException(409, 'Job stopped.')
    return dict(row)


class Next(command.Body):
    kind: Literal['task', 'review'] | None = None
    peek: bool = False


@router.post('/worker/next', dependencies=[Depends(command.worker)])
async def next_job(body: Next):
    with tenant_scope(PLATFORM, administrative=True):
        async with command.db().acquire() as conn:
            async with conn.transaction():
                # Interrupted jobs are failed, never replayed after an uncertain charge/push.
                abandoned = await conn.fetch('SELECT owner_tenant,job_id FROM tank.window_agent_dispatch WHERE tenant_id=$1 AND claimed=true ORDER BY created_at LIMIT 1000', PLATFORM)
                for pending in abandoned:
                    await conn.execute("UPDATE tank.window_agent_jobs SET status='failed' WHERE tenant_id=$1 AND job_id=$2 AND status='running' AND heartbeat < now() - interval '2 minutes'", pending['owner_tenant'], pending['job_id'])
                    status = await conn.fetchval('SELECT status FROM tank.window_agent_jobs WHERE tenant_id=$1 AND job_id=$2', pending['owner_tenant'], pending['job_id'])
                    if status not in {'queued', 'running'}:
                        await conn.execute('DELETE FROM tank.window_agent_dispatch WHERE tenant_id=$1 AND job_id=$2', PLATFORM, pending['job_id'])
                dispatch = await conn.fetchrow("SELECT d.* FROM tank.window_agent_dispatch d WHERE d.tenant_id=$1 AND d.claimed=false AND EXISTS(SELECT 1 FROM tank.window_agent_jobs j WHERE j.tenant_id=d.owner_tenant AND j.job_id=d.job_id AND ($2::text IS NULL OR j.kind=$2)) ORDER BY d.created_at LIMIT 1 FOR UPDATE OF d SKIP LOCKED", PLATFORM, body.kind)
                if body.peek:
                    return {'available': dispatch is not None}
                if not dispatch:
                    return None
                tenant_id, job_id = dispatch['owner_tenant'], dispatch['job_id']
                await conn.execute('UPDATE tank.window_agent_dispatch SET claimed=true WHERE tenant_id=$1 AND job_id=$2', PLATFORM, job_id)
                lease = secrets.token_urlsafe(32)
                row = await conn.fetchrow("UPDATE tank.window_agent_jobs SET status='running',lease_hash=$3,heartbeat=now() WHERE tenant_id=$1 AND job_id=$2 AND status='queued' RETURNING *", tenant_id, job_id, hashlib.sha256(lease.encode()).hexdigest())
                if row is None:
                    return None
                return {**{k: str(row[k]) for k in ('tenant_id', 'job_id', 'thread_id')}, 'repo': row['repo'], 'prompt': row['prompt'], 'lease': lease, 'kind': row['kind'], 'pull_number': row['pull_number'], 'target_sha': row['target_sha']}


@router.post('/worker/{tenant_id}/{job_id}/heartbeat', dependencies=[Depends(command.worker)])
async def heartbeat(tenant_id: uuid.UUID, job_id: uuid.UUID, body: Lease):
    activate_tenant(tenant_id)
    async with command.db().acquire() as conn:
        await leased(conn, tenant_id, job_id, body.lease)
        await conn.execute('UPDATE tank.window_agent_jobs SET heartbeat=now() WHERE tenant_id=$1 AND job_id=$2', tenant_id, job_id)
    return {'status': 'running'}


@router.post('/worker/{tenant_id}/{job_id}/credential', dependencies=[Depends(command.worker)])
async def worker_credential(tenant_id: uuid.UUID, job_id: uuid.UUID, body: Lease):
    from fastapi.responses import JSONResponse
    activate_tenant(tenant_id)
    async with command.db().acquire() as conn:
        row = await leased(conn, tenant_id, job_id, body.lease)
        token = await credential(conn, command.Actor(tenant_id, row['user_id'], False), row['credential_name'])
    return JSONResponse({'value': token}, headers={'Cache-Control': 'no-store'})


class Turn(Lease):
    turn: command.CodeTurn


@router.post('/worker/{tenant_id}/{job_id}/code-turn')
async def code_turn(tenant_id: uuid.UUID, job_id: uuid.UUID, body: Turn):
    activate_tenant(tenant_id)
    async with command.db().acquire() as conn:
        row = await leased(conn, tenant_id, job_id, body.lease)
    if body.turn.model != 'auto':
        raise HTTPException(422, 'Use automatic routing.')
    actor = await command.authorized_actor(command.Actor(tenant_id, row['user_id'], False))
    plan = await command.code_turn(row['thread_id'], body.turn, actor)
    # Background permission covers repository edits only. Never execute product cards.
    if plan.get('cards') or (row['kind'] == 'review' and plan.get('requests')):
        raise HTTPException(422, 'Background agents only edit the repository.')
    return plan


@router.post('/worker/{tenant_id}/{job_id}/finish', dependencies=[Depends(command.worker)])
async def finish(tenant_id: uuid.UUID, job_id: uuid.UUID, body: Finish):
    activate_tenant(tenant_id)
    async with command.db().acquire() as conn:
        row = await leased(conn, tenant_id, job_id, body.lease)
        result = {'sha': body.sha, 'pr_url': f"https://github.com/{row['repo']}/pull/{body.pr_number}" if body.pr_number else None}
        await conn.execute("UPDATE tank.window_agent_jobs SET status=$3,result=$4::jsonb WHERE tenant_id=$1 AND job_id=$2 AND status='running'", tenant_id, job_id, body.status, json.dumps(result))
    return {'status': body.status}



async def migrate(tank, sql, tables):
    # Existing deployments use separate tenant and BYPASSRLS migration pools.
    with tenant_scope(PLATFORM):
        async with tank.pool().acquire() as conn:
            role = await conn.fetchval('SELECT current_user')
    if not isinstance(role, str) or not role:
        raise RuntimeError('Tenant database role is unavailable')
    if any(not re.fullmatch(r'window_agent_[a-z_]+', table) for table in tables):
        raise RuntimeError('Feature table name was refused')
    grants = {
        'window_agent_jobs': """DO $$ BEGIN EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE tank.window_agent_jobs TO %I', current_setting('window_agents.tenant_role')); END $$""",
        'window_agent_dispatch': """DO $$ BEGIN EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE tank.window_agent_dispatch TO %I', current_setting('window_agents.tenant_role')); END $$""",
        'window_agent_webhooks': """DO $$ BEGIN EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE tank.window_agent_webhooks TO %I', current_setting('window_agents.tenant_role')); END $$""",
        'window_agent_deliveries': """DO $$ BEGIN EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE tank.window_agent_deliveries TO %I', current_setting('window_agents.tenant_role')); END $$""",
    }
    if any(table not in grants for table in tables):
        raise RuntimeError('Feature table name was refused')
    with tenant_scope(PLATFORM, administrative=True):
        async with tank.pool().acquire() as conn:
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute("SELECT set_config('window_agents.tenant_role', $1, true)", role)
                # Fixed table grants; PostgreSQL quotes the database-owned role.
                for table in tables:
                    await conn.execute(grants[table])

def install(app, tank):
    app.include_router(router)
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous(application):
            await migrate(tank, SCHEMA, ['window_agent_jobs', 'window_agent_dispatch'])
            yield
    app.router.lifespan_context = lifespan
    __import__('window_agent_reviews').install(app, tank)
