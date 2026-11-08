"""Signed legacy hearings retain their command boundary, never Zenas memory."""
import importlib
import json
from uuid import uuid4

import httpx
import pytest
from chassis.memory import MemoryClient
from chassis.user_assertion import HEADER, SignedUser, sign_assertion

from agents.zenas.core import memory_task
from agents.zenas.core.moot.hearing import Actor
from agents.zenas.core.moot.hearing_store import HearingStore
from agents.zenas.core.store import PgStore

TENANT = '11111111-1111-4111-8111-111111111111'
USER = 'placeholder-moot-owner'
KEY = 'placeholder-moot-room-key'


@pytest.mark.asyncio
async def test_signed_hearing_never_prepares_recalls_or_remembers_zenas_memory(database, monkeypatch):
    from moot_hearing.test_hearing import problem
    for name, value in {'TANK_REGION': 'us-east', 'TANK_KEY': KEY, 'TANK_KEY_ZENAS': KEY,
                        'ZENAS_PII_KEY': '00' * 32, 'ZENAS_MEMORY_ENABLED': 'true',
                        'ZENAS_BRAIN_ENABLED': 'true'}.items():
        monkeypatch.setenv(name, value)
    await PgStore(database).apply_schema()
    await PgStore(database).register_tenant(TENANT, 'us-east')
    app = importlib.import_module('agents.zenas.app').app
    monkeypatch.setattr(app.state, 'pool', database, raising=False)
    calls, rows, prepared = [], [], []

    def memory_response(request):
        calls.append((request.method, request.url.path))
        if request.method == 'GET' and request.url.path == '/records':
            return httpx.Response(200, json={'memories': rows})
        if request.url.path == '/recall':
            return httpx.Response(200, json={'memories': []})
        if request.method == 'POST' and request.url.path == '/memories':
            row = {'id': str(uuid4()), **json.loads(request.content)}
            rows.append(row)
            return httpx.Response(201, json=row)
        raise AssertionError('Unexpected memory request')

    class CodeQuote:
        async def run_skill(self, tenant, skill, payload):
            from agents.zenas.core.scope_rules import classify
            assert tenant == TENANT and skill == 'zenas.scope'
            return classify(payload['text'], payload['jurisdiction'])

        async def savings_quote(self, *args):
            return {'cost': 0, 'would have cost': 0, 'tokens_in': 0, 'tokens_out': 0, 'paid_calls': 0}

    prepare = memory_task.prepare
    async def observe_prepare(request, payload):
        prepared.append(request.url.path)
        assert not request.url.path.startswith('/moot')
        return await prepare(request, payload)
    monkeypatch.setattr(memory_task, 'prepare', observe_prepare)
    monkeypatch.setattr(app.state, 'tank', CodeQuote(), raising=False)
    owner = Actor(TENANT, USER, 'us-east', True)
    store = HearingStore(database)
    state = await store.create(owner, str(uuid4()), problem(),
        {seat: {'kind': 'ai'} for seat in ('applicant', 'respondent', 'judge')})
    async with httpx.AsyncClient(transport=httpx.MockTransport(memory_response)) as memory_http:
        monkeypatch.setattr(app.state, 'memory', MemoryClient('http://localhost', KEY, 'zenas', memory_http), raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as http:
            actor = SignedUser(TENANT, USER, 'org:admin')
            async def send(path, payload, command, *, signature=None):
                target = path + '?tenant_id=' + TENANT + '&home_region=us-east'
                raw = json.dumps(payload, separators=(',', ':')).encode()
                headers = {'X-Tank-Key': KEY, 'Content-Type': 'application/json', 'Idempotency-Key': command,
                    'X-Moot-Tenant': TENANT, 'X-Moot-Actor': USER, 'X-Moot-Founder': '1',
                    HEADER: signature or sign_assertion('zenas', actor, 'POST', target, raw, key=KEY)}
                return await http.post(target, content=raw, headers=headers)

            path = '/moot/hearings/' + state['id'] + '/lock'
            payload = {'request_id': str(uuid4()), 'revision': state['revision']}
            command = str(uuid4())
            first = await send(path, payload, command)
            assert first.status_code == 200, first.text
            assert first.json()['hearing']['phase'] == 'locked'
            assert first.json()['savings_receipt']['cost'] == 0
            again = await send(path, payload, command)
            assert again.status_code == 200 and again.json() == first.json()
            denied = await send(path, payload, str(uuid4()), signature='placeholder-invalid-assertion')
            assert denied.status_code == 401
            assert not calls and not rows and not prepared
            assert (await store.load(owner, state['id']))['revision'] == first.json()['hearing']['revision']

            legal = await send('/matters', {'tenant_id': TENANT, 'home_region': 'us-east',
                'jurisdiction': 'us', 'created_by': USER, 'title': 'A written supply agreement.'}, str(uuid4()))
            assert legal.status_code == 201, legal.text
            assert legal.json()['memory']['enabled'] is True
            assert prepared == ['/matters']
            assert ('POST', '/recall') in calls and ('POST', '/memories') in calls and rows


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/moot', '/moot/hearings', '/moot/published'])
async def test_moot_cannot_enter_memory_even_if_prepare_or_remember_is_called_directly(path):
    from types import SimpleNamespace
    from agents.zenas.memory_api import verified_actor
    class Forbidden:
        def __getattr__(self, name):
            raise AssertionError('Moot reached Zenas memory: ' + name)
    request = SimpleNamespace(url=SimpleNamespace(path=path), state=Forbidden(), app=Forbidden())
    assert await verified_actor(request, TENANT) is None
    task = await memory_task.prepare(request, {'visitor_context': 'placeholder private hearing'})
    assert not task.enabled and task.gate is None
    task.enabled, task.gate = True, Forbidden()
    await task.remember({'hearing': 'placeholder private hearing'}, {'cost': 0})
