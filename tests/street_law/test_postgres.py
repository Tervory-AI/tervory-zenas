"""Actual persisted session with a non-bypass role and tenant-bound room routes."""
from pathlib import Path
import os
from uuid import UUID, uuid4

import asyncpg
from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio

from agents.zenas.core.matters import CURRENT_WORDING_VERSION
from agents.zenas.core.store import PgStore
from agents.zenas.core.street_law_store import StreetMatters
from agents.zenas.core import street_law_api as api
from chassis.tenant_db import TenantPool, TenantContextMiddleware, bind_tenant_endpoints, tenant_scope

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'agents/zenas/schema.sql'
URL = os.environ.get('POOL_SCHEMA_DATABASE_URL')


@pytest_asyncio.fixture
async def database():
    if not URL:
        pytest.skip('requires disposable POOL_SCHEMA_DATABASE_URL')
    admin = await asyncpg.connect(URL)
    suffix = uuid4().hex
    name, role = 'street_' + suffix, 'street_role_' + suffix
    raw = None
    await admin.execute(f'CREATE ROLE "{role}" LOGIN PASSWORD \'placeholder\' NOSUPERUSER NOBYPASSRLS')
    try:
        await admin.execute(f'CREATE DATABASE "{name}" OWNER "{role}"')
        raw = await asyncpg.create_pool(URL, database=name, user=role, password='placeholder', min_size=1, max_size=3)
        async with raw.acquire() as conn:
            await conn.execute(SCHEMA.read_text())
            # A new table must not introduce the RLS admin-pool requirement on a
            # box that has not rolled RLS out. It follows an already sealed room.
            assert await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid='agent_zenas.street_law_sessions'::regclass") is False
            await conn.execute('''ALTER TABLE agent_zenas.matters ENABLE ROW LEVEL SECURITY;
                ALTER TABLE agent_zenas.matters FORCE ROW LEVEL SECURITY;
                CREATE POLICY placeholder_tenant ON agent_zenas.matters
                USING (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)''')
            await conn.execute(SCHEMA.read_text())
            assert await conn.fetchval("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='agent_zenas.street_law_sessions'::regclass") is True
            assert await conn.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user') is False
        yield TenantPool(raw)
    finally:
        if raw is not None:
            await raw.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


@pytest.mark.asyncio
async def test_persisted_http_question_answer_review_release_and_rls(database, clinic, monkeypatch):
    monkeypatch.setenv('TANK_KEY', 'placeholder-room')
    store = PgStore(database)
    actor = clinic.actor
    client_id = 'placeholder-client'
    other_tenant = '22222222-2222-4222-8222-222222222222'
    with tenant_scope(actor.tenant_id):
        await store.register_tenant(actor.tenant_id, 'za')
        await store.add_consent({'tenant_id': actor.tenant_id, 'user_id': client_id, 'jurisdiction': 'za',
            'wording_version': CURRENT_WORDING_VERSION, 'text_hash': 'placeholder-consent-hash', 'ip': '127.0.0.1'})
    with tenant_scope(other_tenant):
        await store.register_tenant(other_tenant, 'za')
    app = FastAPI()
    app.add_middleware(TenantContextMiddleware)
    app.state.pool, app.state.law_pool = database, clinic.law
    api.register_routes(app, matters_factory=lambda: PgStore(database), box_region=lambda: 'za')
    bind_tenant_endpoints(app)
    base = {'tenant_id': actor.tenant_id, 'home_region': 'za'}
    founder = {'X-Tank-Key': 'placeholder-room', 'X-Street-Law-Tenant': actor.tenant_id,
               'X-Street-Law-Actor': actor.user_id, 'X-Street-Law-Founder': '1'}
    client_headers = {**founder, 'X-Street-Law-Actor': client_id, 'X-Street-Law-Founder': '0'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://placeholder') as client:
        session = await client.post('/street-law/sessions', headers=founder,
                                    json={**base, 'name': 'Placeholder legal help day'})
        assert session.status_code == 201, session.text
        session_path = '/street-law/sessions/' + session.json()['id']
        question = await client.post(session_path + '/questions', headers=client_headers,
                                     json={**base, 'question': 'When is my rental deposit returned?'})
        assert question.status_code == 201, question.text
        path = session_path + '/questions/' + question.json()['id']
        answer = await client.post(path + '/answer', headers=founder, json=base)
        assert answer.status_code == 200 and answer.json()['answer']['sources']
        withheld = await client.get(path, headers=client_headers, params=base)
        assert withheld.json()['answer'] is None
        original = StreetMatters.set_document_state
        async def failed_write(*args, **kwargs):
            raise RuntimeError('placeholder failed final write')
        monkeypatch.setattr(StreetMatters, 'set_document_state', failed_write)
        with pytest.raises(RuntimeError):
            await client.post(path + '/release', headers=founder, json={**base, 'confirmed': True})
        with tenant_scope(actor.tenant_id):
            async with database.acquire() as conn:
                assert await conn.fetchval('SELECT count(*) FROM agent_zenas.reviews WHERE tenant_id=$1', UUID(actor.tenant_id)) == 0
                assert await conn.fetchval('SELECT state FROM agent_zenas.documents WHERE tenant_id=$1', UUID(actor.tenant_id)) == 'in_review'
        monkeypatch.setattr(StreetMatters, 'set_document_state', original)
        released = await client.post(path + '/release', headers=founder, json={**base, 'confirmed': True})
        assert released.status_code == 200, released.text
        assert released.json()['released'] and released.json()['reviewed']
        visible = await client.get(path, headers=client_headers, params=base)
        assert visible.json()['answer'] == answer.json()['answer']
        again = await client.post(path + '/release', headers=founder, json={**base, 'confirmed': True})
        assert again.status_code == 200
    with tenant_scope(actor.tenant_id):
        async with database.acquire() as conn:
            assert await conn.fetchval('SELECT count(*) FROM agent_zenas.reviews WHERE tenant_id=$1',
                                       UUID(actor.tenant_id)) == 1
            assert await conn.fetchval('SELECT count(*) FROM agent_zenas.worked_matters WHERE tenant_id=$1', UUID(actor.tenant_id)) == 0
            assert await conn.fetchval('SELECT count(*) FROM agent_zenas.training_pairs WHERE tenant_id=$1', UUID(actor.tenant_id)) == 0
            assert await conn.fetchval("SELECT state FROM agent_zenas.documents WHERE tenant_id=$1",
                                       UUID(actor.tenant_id)) == 'released'
            assert await conn.fetchval('SELECT reviewed FROM agent_zenas.documents WHERE tenant_id=$1', UUID(actor.tenant_id)) is True
    with tenant_scope(other_tenant):
        async with database.acquire() as conn:
            # Deliberately omit an application predicate: FORCE RLS must hide it.
            assert await conn.fetchval('SELECT count(*) FROM agent_zenas.street_law_sessions') == 0
