"""User-enabled PR review and signed, replay-safe GitHub webhook intake."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator

import command
import window_agents as agents
from tenant_db import activate_tenant
from vault import open_seal, seal

router = APIRouter(prefix='/v1/window-agents')
SCHEMA = """
ALTER TABLE tank.window_agent_jobs ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'task';
ALTER TABLE tank.window_agent_jobs ADD COLUMN IF NOT EXISTS pull_number integer;
ALTER TABLE tank.window_agent_jobs ADD COLUMN IF NOT EXISTS target_sha text;
CREATE TABLE IF NOT EXISTS tank.window_agent_webhooks(
 tenant_id uuid NOT NULL, user_id text NOT NULL, hook_id uuid NOT NULL, repo text NOT NULL,
 credential_name text NOT NULL, enabled boolean NOT NULL DEFAULT false,
 PRIMARY KEY(tenant_id,hook_id), UNIQUE(tenant_id,user_id,repo));
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  ALTER TABLE tank.window_agent_webhooks ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tank.window_agent_webhooks FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.window_agent_webhooks'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON tank.window_agent_webhooks FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.window_agent_webhooks'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON tank.window_agent_webhooks AS RESTRICTIVE FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;
CREATE TABLE IF NOT EXISTS tank.window_agent_deliveries(
 tenant_id uuid NOT NULL, hook_id uuid NOT NULL, delivery_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,hook_id,delivery_id),
 FOREIGN KEY(tenant_id,hook_id) REFERENCES tank.window_agent_webhooks(tenant_id,hook_id));
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid='tank.command_threads'::regclass AND relrowsecurity) THEN
  ALTER TABLE tank.window_agent_deliveries ENABLE ROW LEVEL SECURITY;
  ALTER TABLE tank.window_agent_deliveries FORCE ROW LEVEL SECURITY;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.window_agent_deliveries'::regclass AND polname='tenant_access') THEN
   CREATE POLICY tenant_access ON tank.window_agent_deliveries FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid='tank.window_agent_deliveries'::regclass AND polname='tenant_boundary') THEN
   CREATE POLICY tenant_boundary ON tank.window_agent_deliveries AS RESTRICTIVE FOR ALL TO PUBLIC
    USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
    WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid);
  END IF;
 END IF;
END $$;
"""


class Review(command.Body):
    repo: str = Field(max_length=200)
    credential_name: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.-]+$')
    pull_number: int = Field(gt=0)
    request_id: uuid.UUID

    @field_validator('repo')
    @classmethod
    def valid_repo(cls, value):
        return agents.Start.valid_repo(value)

    def task(self, prompt=None):
        return agents.Start(repo=self.repo, credential_name=self.credential_name,
                            prompt=prompt or f'Review pull request #{self.pull_number}', request_id=self.request_id)


class Webhook(command.Body):
    repo: str = Field(max_length=200)
    credential_name: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.-]+$')
    enabled: bool

    @field_validator('repo')
    @classmethod
    def valid_repo(cls, value):
        return agents.Start.valid_repo(value)


def hook_vault(actor):
    return agents.vault_name(actor) + '.webhooks'


def hook_context(actor, hook_id):
    return f'plugins:v1:{actor.tenant_id}:{hook_vault(actor)}:secret:{hook_id}'


@router.post('/reviews', status_code=202)
async def start_review(body: Review, actor: command.Actor = Depends(agents.actor_identity)):
    return await agents.enqueue(body.task(), actor, kind='review', pull_number=body.pull_number)


def public_origin():
    from main import box_region
    return {'us-east': 'https://app.tervory.com', 'za': 'https://za.tervory.com'}[box_region()]


@router.post('/webhooks')
async def configure_hook(body: Webhook, actor: command.Actor = Depends(agents.actor_identity)):
    task = agents.Start(repo=body.repo, credential_name=body.credential_name, prompt='Webhook', request_id=uuid.uuid4())
    repo = task.repo.casefold()
    async with command.db().acquire() as conn:
        async with conn.transaction():
            await agents.credential(conn, actor, body.credential_name)
            lock_key = ':'.join((str(actor.tenant_id), str(actor.user_id), repo))
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', lock_key)
            row = await conn.fetchrow('SELECT hook_id FROM tank.window_agent_webhooks WHERE tenant_id=$1 AND user_id=$2 AND repo=$3', actor.tenant_id, actor.user_id, repo)
            hook_id = row['hook_id'] if row else uuid.uuid4()
            if row:
                encrypted = await conn.fetchval("SELECT sealed FROM tank.vault_items WHERE tenant_id=$1 AND vault=$2 AND kind='secrets' AND name=$3", actor.tenant_id, hook_vault(actor), str(hook_id))
                value = open_seal(encrypted, hook_context(actor, hook_id))['value']
            else:
                value = secrets.token_hex(32)
                await conn.execute("INSERT INTO tank.vault_items(tenant_id,vault,kind,name,metadata,sealed) VALUES($1,$2,'secrets',$3,$4::jsonb,$5)", actor.tenant_id, hook_vault(actor), str(hook_id), json.dumps({'name': str(hook_id)}), seal({'value': value}, hook_context(actor, hook_id)))
            await conn.execute('INSERT INTO tank.window_agent_webhooks VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(tenant_id,hook_id) DO UPDATE SET enabled=EXCLUDED.enabled,credential_name=EXCLUDED.credential_name', actor.tenant_id, actor.user_id, hook_id, repo, body.credential_name, body.enabled)
    origin = public_origin()
    return JSONResponse({'hook_id': str(hook_id), 'enabled': body.enabled,
                         'url': f"{origin}{'/v1/command' if origin == 'https://app.tervory.com' else '/v1'}/window-agents/webhooks/{actor.tenant_id}/{hook_id}",
                         'secret': value if body.enabled else None}, headers={'Cache-Control': 'no-store'})


@router.post('/webhooks/{tenant_id}/{hook_id}', status_code=202)
async def receive_hook(tenant_id: uuid.UUID, hook_id: uuid.UUID, request: Request):
    activate_tenant(tenant_id)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 1_000_000:
            raise HTTPException(413, 'Webhook is too large.')
    async with command.db().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow('SELECT * FROM tank.window_agent_webhooks WHERE tenant_id=$1 AND hook_id=$2 AND enabled=true', tenant_id, hook_id)
            if not row:
                raise HTTPException(404, 'Not Found')
            actor = command.Actor(tenant_id, row['user_id'], False)
            encrypted = await conn.fetchval("SELECT sealed FROM tank.vault_items WHERE tenant_id=$1 AND vault=$2 AND kind='secrets' AND name=$3", tenant_id, hook_vault(actor), str(hook_id))
            secret = open_seal(encrypted, hook_context(actor, hook_id))['value']
            expected = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
            supplied = request.headers.get('x-hub-signature-256', '')
            if not re.fullmatch(r'sha256=[0-9a-f]{64}', supplied) or not hmac.compare_digest(supplied, expected):
                raise HTTPException(404, 'Not Found')
            # Verify the hook's own HMAC before parsing the body or recording delivery.
            try:
                delivery = uuid.UUID(request.headers.get('x-github-delivery', ''))
            except ValueError:
                raise HTTPException(422, 'Webhook payload refused.') from None
            claimed = await conn.fetchval('INSERT INTO tank.window_agent_deliveries(tenant_id,hook_id,delivery_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING RETURNING delivery_id', tenant_id, hook_id, delivery)
            if claimed is None:
                raise HTTPException(409, 'Delivery already processed.')
            if request.headers.get('x-github-event') == 'ping':
                return {'accepted': True}
            if request.headers.get('x-github-event') != 'pull_request':
                return {'accepted': False}
            try:
                data = json.loads(raw)
                if data.get('action') not in {'opened', 'synchronize', 'reopened', 'ready_for_review'}:
                    return {'accepted': False}
                pull = data['pull_request']
                number, sha = data['number'], pull['head']['sha']
                if (type(number) is not int or number < 1 or not re.fullmatch(r'[0-9a-f]{40}', sha)
                        or data['repository']['full_name'].casefold() != row['repo']
                        or pull['base']['repo']['full_name'].casefold() != row['repo']):
                    raise ValueError()
                request_id = uuid.uuid5(hook_id, f'{number}:{sha}')
                task = agents.Start(repo=row['repo'], credential_name=row['credential_name'],
                                    prompt=f'Review pull request #{number} at {sha}', request_id=request_id)
            except (ValueError, TypeError, KeyError, AttributeError):
                raise HTTPException(422, 'Webhook payload refused.') from None
            # Delivery claim and durable enqueue commit atomically. Distinct deliveries
            # for the same head still share one job and therefore one review charge.
            return await agents.enqueue_on(conn, task, actor, kind='review', pull_number=number, target_sha=sha)


def install(app, tank):
    app.include_router(router)
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous(application):
            await agents.migrate(tank, SCHEMA, ['window_agent_webhooks', 'window_agent_deliveries'])
            yield
    app.router.lifespan_context = lifespan
