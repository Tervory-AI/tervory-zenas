"""Command contract tests; Postgres integration also runs in the existing CI service."""
from trigger_signin_fakes import set_founders
import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

import command

TENANT = uuid.UUID('00000000-0000-0000-0000-000000000011')
OTHER = uuid.UUID('00000000-0000-0000-0000-000000000012')


def request(**headers):
    return Request({'type': 'http', 'method': 'GET', 'path': '/', 'query_string': b'',
                    'headers': [(k.encode(), v.encode()) for k, v in headers.items()]})


@pytest.fixture
def runtime(monkeypatch):
    runtime = SimpleNamespace(TANK_API_KEY='dummy-workspace-key', box_region=lambda: 'us-east',
        _storage_mask=lambda value: value, model_call_refusal=lambda *args, **kwargs: None,
        list_models=AsyncMock(return_value=[{'name': 'placeholder-model'}, {'name': 'placeholder-pin'}]))
    monkeypatch.setitem(sys.modules, 'main', runtime)
    set_founders(monkeypatch, 'founder')
    monkeypatch.setenv('TANK_KEY_WORKERS', 'dummy-worker-key')
    return runtime


@pytest.mark.asyncio
async def test_identity_roles_and_region(runtime, monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def acquire():
        yield SimpleNamespace(fetch=AsyncMock(return_value=[{'agent': 'tiger'}]),
                              fetchval=AsyncMock(return_value=None))

    monkeypatch.setattr(command, 'db', lambda: SimpleNamespace(acquire=acquire))
    headers = {'x-tank-key': runtime.TANK_API_KEY, 'x-tervory-tenant': str(TENANT),
               'x-tervory-user': 'client', 'x-tervory-squad': 'tiger'}
    actor = await command.identity(request(**headers))
    assert command.allowed_tools(actor) == sorted([*command.TENANT_TOOLS, 'plugins.list', 'tiger.alerts', 'tiger.match', 'tiger.match.confirm', 'tiger.match.reject', 'tiger.match.undo', 'tiger.matches', 'tiger.position', 'tiger.read', 'tiger.receipts', 'tiger.watch', 'tiger.watch.bulk', 'tiger.watch.pause', 'tiger.watch.remove', 'tiger.watch.resume', 'tiger.watches'])
    with pytest.raises(HTTPException) as refused:
        command.require_tool(actor, 'worker.build')
    assert refused.value.status_code == 403
    headers['x-tervory-user'] = 'founder'
    founder = await command.identity(request(**headers))
    assert 'worker.build' in command.allowed_tools(founder)
    assert 'zenas.review' in command.allowed_tools(founder)
    assert 'training.status' in command.allowed_tools(founder)
    assert {'training.run', 'training.receipt'} <= set(command.allowed_tools(founder))
    assert 'tiger.receipts' not in command.allowed_tools(command.Actor(TENANT, 'placeholder-other-user', False))
    assert 'training.status' not in command.allowed_tools(command.Actor(TENANT, 'placeholder-admin', False, tenant_admin=True))
    assert not {'training.run', 'training.receipt'} & set(command.allowed_tools(command.Actor(TENANT, 'placeholder-admin', False, tenant_admin=True)))
    headers['x-tervory-home-region'] = 'za'
    with pytest.raises(HTTPException) as refused:
        await command.identity(request(**headers))
    assert refused.value.status_code == 403
    headers['x-tank-key'] = 'dummy-worker-key'
    with pytest.raises(HTTPException) as refused:
        await command.identity(request(**headers))
    assert refused.value.status_code == 401


@pytest.mark.asyncio
async def test_worker_key_is_separate(runtime):
    with pytest.raises(HTTPException):
        await command.worker(request(**{'x-tank-key': runtime.TANK_API_KEY}))
    await command.worker(request(**{'x-tank-key': 'dummy-worker-key'}))


def test_receipts_never_invent_baseline():
    row = {'id': uuid.uuid4(), 'model': 'fixture-model', 'tokens_in': 10, 'tokens_out': 20, 'cost_usd': 0.02}
    receipt = command.receipt_row(row, 'step')
    assert receipt['baseline_status'] == 'baseline pending'
    assert receipt['saved_usd'] is None
    assert command.totals([receipt]) == {'cost_usd': 0.02, 'baseline_cost_usd': None, 'saved_usd': None}
    row.update(baseline_model='fixture-baseline', baseline_cost_usd=0.01)
    receipt = command.receipt_row(row, 'step')
    assert receipt['saved_usd'] == -0.01  # A loss must not be hidden.
    assert receipt['ledger_id'] == str(row['id'])
    assert command.totals([receipt])['baseline_cost_usd'] == 0.01


@pytest.mark.asyncio
async def test_auto_turn_uses_existing_router(runtime):
    runtime.Message = lambda **kw: kw
    runtime.RouteRequest = lambda **kw: SimpleNamespace(**kw)
    runtime.run_route = AsyncMock(return_value='answer')
    actor = command.Actor(TENANT, 'client', False)
    messages = [{'role': 'user', 'content': 'Hello'}]
    assert await command.route_turn(actor, messages) == 'answer'
    req = runtime.run_route.call_args.args[0]
    assert req.task_type == 'command.turn'
    assert req.tenant_id == TENANT
    assert req.home_region == 'us-east'
    assert req.messages == messages
    assert not hasattr(req, 'model')
    runtime.run_route.side_effect = HTTPException(503, 'paid work is paused')
    with pytest.raises(HTTPException) as error:
        await command.route_turn(actor, messages)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_unknown_pin_fails_explicitly_before_any_write(runtime):
    runtime.list_models = AsyncMock(return_value=[{'name': 'fixture-model'}])
    founder = command.Actor(TENANT, 'founder', True)
    with pytest.raises(HTTPException) as error:
        await command.turn(uuid.uuid4(), command.Turn(text='Hi', model='missing'), founder)
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_customer_pin_is_refused_403_naming_no_model_before_any_write(api, runtime, monkeypatch):
    # Review of #506 B2: pinning is founder-only; the refusal names no model and does
    # not reveal whether the requested name is on the roster.
    client, pool = api
    runtime.list_models = AsyncMock(return_value=[{'name': 'fixture-pin'}])
    route = AsyncMock()
    monkeypatch.setattr(command, 'route_turn', route)
    headers = {'x-tervory-user': 'customer'}
    thread = (await client.post('/v1/command/threads', json={}, headers=headers)).json()['thread_id']
    before = await pool.fetchval("SELECT state::text FROM tank.command_threads WHERE thread_id=$1", uuid.UUID(thread))
    for name in ('fixture-pin', 'missing-model'):
        reply = await client.post(f'/v1/command/threads/{thread}/turn', json={'text': 'Hi', 'model': name}, headers=headers)
        assert reply.status_code == 403
        assert reply.json() == {'detail': 'Only Auto is available on this account'}
        assert name not in reply.text and 'fixture' not in reply.text and 'roster' not in reply.text
    runtime.list_models.assert_not_awaited()
    route.assert_not_awaited()
    assert await pool.fetchval("SELECT state::text FROM tank.command_threads WHERE thread_id=$1", uuid.UUID(thread)) == before
    assert await pool.fetchval("SELECT count(*) FROM tank.tasks WHERE created_by='customer'") == 0


@pytest.mark.asyncio
async def test_models_roster_is_founder_only_and_404_for_everyone_else(api, runtime):
    # Review of #506 B2: name, provider_model and prices are model internals.
    client, _ = api
    roster = [{'name': 'fixture-pin', 'provider_model': 'provider/fixture', 'input_usd_per_m': 1.5, 'output_usd_per_m': 2.5}]
    runtime.list_models = AsyncMock(return_value=roster)
    for user, role in (('customer', ''), ('customer', 'org:admin')):
        reply = await client.get('/v1/command/models', headers={'x-tervory-user': user, 'x-tervory-role': role})
        assert reply.status_code == 404 and reply.json() == {'detail': 'Not Found'}
        assert 'fixture' not in reply.text and 'provider' not in reply.text
    # Same answer as a route that does not exist.
    assert (await client.get('/v1/command/no-such-route', headers={'x-tervory-user': 'customer'})).json() == {'detail': 'Not Found'}
    runtime.list_models.assert_not_awaited()
    founder = await client.get('/v1/command/models')
    assert founder.status_code == 200 and founder.json() == roster


@pytest_asyncio.fixture
async def command_pool():
    url = os.environ.get('POOL_SCHEMA_DATABASE_URL')
    if not url:
        pytest.skip('POOL_SCHEMA_DATABASE_URL is not set; CI provides isolated Postgres')
    admin = await asyncpg.connect(url)
    name = 'command_test_' + uuid.uuid4().hex
    await admin.execute(f'CREATE DATABASE "{name}"')
    from urllib.parse import urlsplit, urlunsplit
    parsed = urlsplit(url)
    target = urlunsplit((parsed.scheme, parsed.netloc, '/' + name, parsed.query, ''))
    pool = await asyncpg.create_pool(target, min_size=1, max_size=5)
    try:
        schema = Path('tank/schema.sql').read_text()
        async with pool.acquire() as conn:
            await conn.execute(schema)
            await conn.execute(schema)  # Empty database and idempotent reapplication.
            # Match Tank startup; this fixture mounts only the command router.
            from savings_defaults import initialize
            await initialize(conn)
        yield pool
    finally:
        if command._background:
            import asyncio
            await asyncio.gather(*command._background)
        await pool.close()
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()


@pytest_asyncio.fixture
async def api(runtime, monkeypatch, command_pool):
    monkeypatch.setattr(command, 'db', lambda: command_pool)
    app = FastAPI()
    app.include_router(command.router)
    headers = {'x-tank-key': runtime.TANK_API_KEY, 'x-tervory-tenant': str(TENANT), 'x-tervory-user': 'founder'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture', headers=headers) as client:
        yield client, command_pool


@pytest.mark.asyncio
async def test_persistence_search_ownership_and_replay(api):
    client, pool = api
    thread = (await client.post('/v1/command/threads', json={'title': 'Build the window'})).json()['thread_id']
    assert (await client.patch(f'/v1/command/threads/{thread}', json={'title': 'New title'})).status_code == 200
    assert len((await client.get('/v1/command/threads?q=New')).json()) == 1
    assert (await client.get('/v1/command/threads?q=absent')).json() == []
    for headers in [{'x-tervory-user': 'someone-else'}, {'x-tervory-tenant': str(OTHER)}]:
        assert (await client.get(f'/v1/command/threads/{thread}', headers=headers)).status_code == 404
        assert (await client.patch(f'/v1/command/threads/{thread}', json={'title': 'stolen'}, headers=headers)).status_code == 404
        assert (await client.get(f'/v1/command/threads/{thread}/events', headers=headers)).status_code == 404
        assert (await client.get('/v1/command/threads', headers=headers)).json() == []
    card = (await client.post('/v1/command/build', json={'thread_id': thread, 'brief_markdown': 'Make it work'})).json()
    event = await client.get(f'/v1/command/threads/{thread}/events')
    assert 'event: card' in event.text
    assert '"seq": 1' in event.text
    assert (await client.get(f'/v1/command/threads/{thread}/events?after=2')).text == ''
    assert (await client.get(f'/v1/command/threads/{thread}')).json()['cards'][0]['card_id'] == card['card_id']


@pytest.mark.asyncio
async def test_confirm_cancel_worker_progress_followups(api):
    import asyncio
    client, pool = api
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    body = {'thread_id': thread, 'brief_markdown': 'Build from this brief, no issue'}
    assert (await client.post('/v1/command/build', json=body, headers={'x-tervory-user': 'client'})).status_code == 403
    card = (await client.post('/v1/command/build', json=body)).json()
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT count(*) FROM tank.command_builds') == 0
    route = f"/v1/command/cards/{card['card_id']}"
    assert (await client.post(route, json={'action': 'confirm'}, headers={'x-tervory-user': 'client'})).status_code == 404
    replies = await asyncio.gather(*[client.post(route, json={'action': 'confirm'}) for _ in range(2)])
    assert all(r.status_code == 200 for r in replies)
    build_id = replies[0].json()['build_id']
    assert replies[1].json()['build_id'] == build_id
    assert (await client.post(route, json={'action': 'cancel'})).status_code == 409
    workers = {'x-tank-key': 'dummy-worker-key'}
    assert (await client.get('/v1/command/builds/next')).status_code == 401
    claims = await asyncio.gather(*[client.get('/v1/command/builds/next', headers=workers) for _ in range(2)])
    assert sum(r.json() is not None for r in claims) == 1
    progress = {'status': 'awaiting_review', 'step_label': 'PR opened', 'pr_url': 'https://github.com/Tervory-AI/tervory-tank/pull/123', 'ci_status': 'passed', 'review_status': 'pending'}
    assert (await client.post(f'/v1/command/builds/{build_id}/progress', headers=workers, json=progress)).status_code == 200
    data = (await client.get(f'/v1/command/threads/{thread}')).json()
    build_step = next(s for s in data['steps'] if s['step_id'] == build_id)
    assert build_step['link'] == progress['pr_url']
    assert build_step['ci_status'] == 'passed'
    follow = (await client.post(f'/v1/command/builds/{build_id}/followups', json={'followup_text': 'Fix X on the same PR'})).json()
    replay = await client.get(f'/v1/command/builds/{build_id}/followups', headers=workers)
    assert replay.json() == [{'followup_id': follow['followup_id'], 'followup_text': 'Fix X on the same PR'}]
    assert (await client.get(f"/v1/command/builds/{build_id}/followups?after={follow['followup_id']}", headers=workers)).json() == []
    # A cancelled proposal creates no additional build.
    card = (await client.post('/v1/command/build', json=body)).json()
    assert (await client.post(f"/v1/command/cards/{card['card_id']}", json={'action': 'cancel'})).status_code == 200
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT count(*) FROM tank.command_builds') == 1


@pytest.mark.asyncio
async def test_auto_stream_matches_ledger_and_recovers_errors(api, monkeypatch):
    client, pool = api
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    ledger_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tank.ledger(id,tenant_id,agent,task_type,model,tokens_in,tokens_out,cost_usd) VALUES($1,$2,'orchestrator','command.turn','fixture-model',10,20,0.02)", ledger_id, TENANT)
    route = AsyncMock(return_value=SimpleNamespace(ledger_id=ledger_id, content='A measured answer'))
    monkeypatch.setattr(command, 'route_turn', route)
    response = await client.post(f'/v1/command/threads/{thread}/turn', json={'text': 'Hi', 'attachments': [{'name': 'notes.txt', 'text': 'Local notes'}]})
    assert response.status_code == 200
    items = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert [r['seq'] for r in items] == list(range(1, len(items) + 1))
    receipt = next(item for item in items if 'rows' in item)
    assert receipt['rows'][0]['ledger_id'] == str(ledger_id)
    assert receipt['totals']['cost_usd'] == 0.02
    assert receipt['totals']['saved_usd'] is None
    assert route.call_args.args[1][-1]['content'] == 'Hi\nAttachment notes.txt:\nLocal notes'
    data = (await client.get(f'/v1/command/threads/{thread}')).json()
    assert data['messages'][-1]['content'] == 'A measured answer'
    assert (await client.get('/v1/command/threads?q=Local')).json()[0]['spent_usd'] == 0.02
    route.side_effect = RuntimeError('private upstream detail must never escape')
    failed = await client.post(f'/v1/command/threads/{thread}/turn', json={'text': 'Again'})
    assert 'event: error' in failed.text
    assert 'private upstream detail' not in failed.text
    async with pool.acquire() as conn:
        state = json.loads(await conn.fetchval('SELECT state FROM tank.command_threads WHERE thread_id=$1', uuid.UUID(thread)))
    assert state['active_turn'] is None


@pytest.mark.asyncio
async def test_interrupted_turn_is_not_retried_and_new_turn_can_start(api, monkeypatch):
    client, pool = api
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    async with pool.acquire() as conn:
        await conn.execute("UPDATE tank.command_threads SET state=state || $2::jsonb WHERE thread_id=$1",
                           uuid.UUID(thread), json.dumps({'active_turn': str(uuid.UUID(int=99)), 'active_started_at': '2000-01-01T00:00:00+00:00'}))
    route = AsyncMock(side_effect=HTTPException(503, 'paused'))
    monkeypatch.setattr(command, 'route_turn', route)
    reply = await client.post(f'/v1/command/threads/{thread}/turn', json={'text': 'New request'})
    assert reply.status_code == 200
    assert 'previous answer was interrupted' in reply.text
    route.assert_awaited_once()  # Only the new request, never the stale request.
    data = (await client.get(f'/v1/command/threads/{thread}')).json()
    assert data['thread']['spent_usd'] is None and data['thread']['receipt_status'] == 'pending'
    assert next(s for s in data['steps'] if s['step_id'] == str(uuid.UUID(int=99)))['receipt_status'] == 'pending'


@pytest.mark.asyncio
async def test_failed_paid_turn_keeps_ledger_receipt(api, monkeypatch):
    client, pool = api
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    ledger_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tank.ledger(id,tenant_id,agent,task_type,model,cost_usd,outcome) VALUES($1,$2,'orchestrator','command.turn','fixture-model',0.01,'failed')", ledger_id, TENANT)
    error = HTTPException(502, {'ledger_id': str(ledger_id), 'upstream': 'private upstream text'})
    monkeypatch.setattr(command, 'route_turn', AsyncMock(side_effect=error))
    reply = await client.post(f'/v1/command/threads/{thread}/turn', json={'text': 'Hello'})
    assert 'event: error' in reply.text
    assert 'event: receipt' in reply.text
    assert str(ledger_id) in reply.text
    assert 'private upstream text' not in reply.text
    detail = (await client.get(f'/v1/command/threads/{thread}')).json()
    assert detail['thread']['spent_usd'] == 0.01


@pytest.mark.asyncio
async def test_revoked_founder_cannot_send_implicit_worker_followup(api, monkeypatch):
    client, pool = api
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    card = (await client.post('/v1/command/build', json={'thread_id': thread, 'brief_markdown': 'Build'})).json()
    await client.post(f"/v1/command/cards/{card['card_id']}", json={'action': 'confirm'})
    set_founders(monkeypatch, '')
    monkeypatch.setattr(command, 'route_turn', AsyncMock(side_effect=HTTPException(503, 'paused')))
    await client.post(f'/v1/command/threads/{thread}/turn', json={'text': 'Change the build'})
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT count(*) FROM tank.command_followups') == 0


def test_plans_are_bounded_and_cannot_supply_authority(runtime):
    founder = command.Actor(TENANT, 'founder', True)
    client = command.Actor(TENANT, 'client', False, frozenset({'tiger'}))
    valid = {'text': 'Review this proposal.', 'actions': [{'tool': 'skala.approve', 'arguments': {'skill_id': 'base.find_site', 'version': '1.0.0'}}]}
    assert len(command.parse_plan(json.dumps(valid), founder).actions) == 1
    assert not command.parse_plan('Plain answer', client).actions
    with pytest.raises(HTTPException):
        command.parse_plan(json.dumps(valid), client)
    for args in [{'skill_id': '../main', 'version': '1.0.0'},
                 {'skill_id': 'base.find_site', 'version': '1.0.0', 'by': 'someone-else'},
                 {'skill_id': 'base.find_site', 'version': '1.0.0', 'tenant_id': str(OTHER)},
                 {'skill_id': 'base.find_site', 'version': '1.0.0', 'confirmed': True}]:
        with pytest.raises(HTTPException):
            command.validate_action(command.PlannedAction(tool='skala.approve', arguments=args), founder)
    with pytest.raises(ValueError):
        command.parse_plan(json.dumps({**valid, 'actions': valid['actions'] * 5}), founder)
    with pytest.raises(ValueError):
        command.parse_plan('x' * (command.MAX_PLAN_BYTES + 1), founder)
    with pytest.raises(HTTPException):
        command.validate_action(command.PlannedAction(tool='repo.read'), founder)
    prompt = command.planning_message(client)['content']
    assert 'tiger.watch' in prompt and 'skala.approve' not in prompt


def test_adapters_force_identity_and_do_not_accept_paid_route_overrides(runtime):
    actor = command.Actor(TENANT, 'founder', True)
    action = command.PlannedAction(tool='market_lab.research', arguments={'question': 'A question'})
    method, path, query, body = command.adapter_request(action, actor)
    assert (method, path) == ('POST', '/v1/research')
    assert body == {'question': 'A question', 'tenant_id': str(TENANT), 'home_region': 'us-east'}
    assert query['tenant_id'] == str(TENANT)
    for overrides in [{'preset': 'high'}, {'model': 'expensive'}, {'home_region': 'za'}, {'tenant_id': str(OTHER)}]:
        with pytest.raises(HTTPException):
            command.validate_action(command.PlannedAction(tool='market_lab.research', arguments={'question': 'Q', **overrides}), actor)
    method, path, query, body = command.adapter_request(command.PlannedAction(tool='skala.approve', arguments={'skill_id': 'base.find_site', 'version': '1.0.0'}), actor)
    assert path == '/v1/skala/base.find_site/1.0.0/approve'
    assert body == {'by': 'founder'}
    assert all(a.path.startswith('/v1/') for a in command.ADAPTERS.values())


def test_receipt_ids_are_explicit_and_unknown_cost_is_not_zero():
    one, two = uuid.uuid4(), uuid.uuid4()
    assert command.explicit_ledger_ids({'ledger_id': str(one), 'receipt': {'ledger_ids': {'model': str(one), 'search': str(two)}}}) == [one, two]
    assert command.explicit_ledger_ids({'records': [{'ledger_id': str(one)}], '_cost_usd': 123}) == []
    unknown = command.receipt_row({'id': one, 'model': 'fixture', 'tokens_in': 0, 'tokens_out': 0,
                                   'cost_usd': 0, 'research_cost_status': 'unknown'}, 'step')
    assert unknown['cost_usd'] is None
    assert command.totals([unknown])['cost_usd'] is None


async def seed_ledger(pool, tenant=TENANT, cost=0.02):
    ledger_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tank.ledger(id,tenant_id,agent,task_type,model,tokens_in,tokens_out,cost_usd) VALUES($1,$2,'orchestrator','command.turn','fixture-model',10,20,$3)", ledger_id, tenant, cost)
    return ledger_id


async def planned_turn(client, pool, monkeypatch, actions):
    ledger_id = await seed_ledger(pool)
    turn = AsyncMock(return_value=SimpleNamespace(ledger_id=ledger_id, content=json.dumps({'text': 'Please confirm the proposed actions.', 'actions': actions})))
    monkeypatch.setattr(command, 'route_turn', turn)
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    reply = await client.post(f'/v1/command/threads/{thread}/turn', json={'text': 'Do this work'})
    assert reply.status_code == 200
    turn.assert_awaited_once()
    return thread, (await client.get(f'/v1/command/threads/{thread}')).json()


@pytest.mark.asyncio
async def test_plan_only_proposes_then_native_tank_route_runs_once(api, runtime, monkeypatch):
    import asyncio
    client, pool = api
    app = client._transport.app
    runtime.app = app
    calls = []

    @app.post('/v1/skala/{skill_id}/{version}/approve')
    async def existing_approval(skill_id: str, version: str, request: Request):
        assert request.headers['x-tank-key'] == runtime.TANK_API_KEY
        assert request.headers['x-tervory-admin'] == 'founder'
        calls.append((skill_id, version, await request.json()))
        await asyncio.sleep(0.05)
        return {'ok': True}

    thread, data = await planned_turn(client, pool, monkeypatch, [{'tool': 'skala.approve', 'arguments': {'skill_id': 'base.find_site', 'version': '1.0.0'}}])
    assert calls == []
    card = data['cards'][0]
    assert card['kind'] == 'approve_skill' and card['status'] == 'pending'
    route = f"/v1/command/cards/{card['card_id']}"
    set_founders(monkeypatch, '')
    assert (await client.post(route, json={'action': 'confirm'})).status_code == 403
    assert calls == []
    set_founders(monkeypatch, 'founder')
    replies = await asyncio.gather(client.post(route, json={'action': 'confirm'}), client.post(route, json={'action': 'confirm'}))
    assert all(r.status_code == 200 for r in replies)
    assert calls == [('base.find_site', '1.0.0', {'by': 'founder'})]
    assert (await client.post(route, json={'action': 'confirm'})).json()['status'] == 'confirmed'
    assert len(calls) == 1
    assert (await client.post(route, json={'action': 'cancel'})).status_code == 409
    data = (await client.get(f'/v1/command/threads/{thread}')).json()
    assert data['cards'][0]['receipt_status'] == 'pending'
    receipt_events = (await client.get(f'/v1/command/threads/{thread}/events')).text
    assert '"cost_usd": null' in receipt_events  # no invented zero-cost approval receipt


@pytest.mark.asyncio
async def test_paid_cancel_has_no_side_effect_and_failed_preflight_can_recover(api, monkeypatch):
    import controls
    client, pool = api
    dispatch = AsyncMock(return_value=(200, {'ok': True}))
    pause = AsyncMock(side_effect=HTTPException(503, 'paused'))
    monkeypatch.setattr(command, 'dispatch_action', dispatch)
    monkeypatch.setattr(controls, 'refuse_paid', pause)
    thread, data = await planned_turn(client, pool, monkeypatch, [
        {'tool': 'rig.ask', 'arguments': {'product_id': 'fixture', 'question': 'Will it fit?'}},
        {'tool': 'rig.ask', 'arguments': {'product_id': 'other', 'question': 'Will it fit?'}},
    ])
    first, second = data['cards']
    await client.post(f"/v1/command/cards/{first['card_id']}", json={'action': 'cancel'})
    pause.assert_not_awaited()
    dispatch.assert_not_awaited()
    route = f"/v1/command/cards/{second['card_id']}"
    failed = (await client.post(route, json={'action': 'confirm'})).json()
    assert failed['status'] == 'failed' and failed['retry_safe']
    dispatch.assert_not_awaited()
    pause.side_effect = None
    assert (await client.post(route, json={'action': 'confirm'})).json()['status'] == 'confirmed'
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_timeout_and_crashed_claim_are_never_redispatched(api, monkeypatch):
    client, pool = api
    dispatch = AsyncMock(side_effect=TimeoutError('unknown upstream outcome'))
    monkeypatch.setattr(command, 'dispatch_action', dispatch)
    thread, data = await planned_turn(client, pool, monkeypatch, [{'tool': 'skala.retire', 'arguments': {'skill_id': 'base.find_site', 'version': '1.0.0'}}])
    route = f"/v1/command/cards/{data['cards'][0]['card_id']}"
    assert (await client.post(route, json={'action': 'confirm'})).json()['status'] == 'unknown'
    assert (await client.post(route, json={'action': 'confirm'})).status_code == 409
    dispatch.assert_awaited_once()
    async with pool.acquire() as conn:
        state = json.loads(await conn.fetchval('SELECT state FROM tank.command_threads WHERE thread_id=$1', uuid.UUID(thread)))
        card = state['cards'][data['cards'][0]['card_id']]
        card.update(status='executing', started_at='2000-01-01T00:00:00+00:00')
        await conn.execute('UPDATE tank.command_threads SET state=$2::jsonb WHERE thread_id=$1', uuid.UUID(thread), json.dumps(state))
    assert (await client.post(route, json={'action': 'confirm'})).json()['status'] == 'unknown'
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_tool_receipts_validate_ownership_and_reconcile_without_dispatch(api, monkeypatch):
    client, pool = api
    actual = await seed_ledger(pool, cost=0.03)
    dispatch = AsyncMock(return_value=(200, {'ledger_id': str(actual), 'ledger_ids': [str(actual)]}))
    monkeypatch.setattr(command, 'dispatch_action', dispatch)
    thread, data = await planned_turn(client, pool, monkeypatch, [{'tool': 'skala.list', 'arguments': {}}])
    route = f"/v1/command/cards/{data['cards'][0]['card_id']}"
    original = command.refresh_action_receipts
    monkeypatch.setattr(command, 'refresh_action_receipts', AsyncMock(side_effect=RuntimeError('temporary database failure')))
    result = (await client.post(route, json={'action': 'confirm'})).json()
    assert result['status'] == 'confirmed' and result['receipt_status'] == 'pending'
    monkeypatch.setattr(command, 'refresh_action_receipts', original)
    assert (await client.post(route, json={'action': 'confirm'})).json()['receipt_status'] == 'recorded'
    await client.post(route, json={'action': 'confirm'})
    dispatch.assert_awaited_once()
    data = (await client.get(f'/v1/command/threads/{thread}')).json()
    assert sum(r['ledger_id'] == str(actual) for r in data['receipts']) == 1
    assert data['thread']['spent_usd'] == 0.05
    # A successful route response cannot attach another tenant's ledger row.
    foreign = await seed_ledger(pool, OTHER, 9)
    dispatch.return_value = (200, {'ledger_id': str(foreign)})
    other_thread, data = await planned_turn(client, pool, monkeypatch, [{'tool': 'skala.list', 'arguments': {}}])
    await client.post(f"/v1/command/cards/{data['cards'][0]['card_id']}", json={'action': 'confirm'})
    data = (await client.get(f'/v1/command/threads/{other_thread}')).json()
    assert all(r['ledger_id'] != str(foreign) for r in data['receipts'])
    assert data['cards'][0]['receipt_status'] == 'pending'
    assert data['thread']['spent_usd'] is None
    assert data['thread']['recorded_spent_usd'] == 0.02


@pytest.mark.asyncio
async def test_worker_receipts_require_explicit_ids_and_are_idempotent(api):
    client, pool = api
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    card = (await client.post('/v1/command/build', json={'thread_id': thread, 'brief_markdown': 'Build'})).json()
    build = (await client.post(f"/v1/command/cards/{card['card_id']}", json={'action': 'confirm'})).json()['build_id']
    worker = {'x-tank-key': 'dummy-worker-key'}
    await client.get('/v1/command/builds/next', headers=worker)
    own = await seed_ledger(pool, cost=0.04)
    foreign = await seed_ledger(pool, OTHER, 9)
    route = f'/v1/command/builds/{build}/progress'
    body = {'status': 'done', 'step_label': 'Finished'}
    rejected = await client.post(route, headers=worker, json={**body, 'ledger_ids': [str(own), str(foreign)]})
    assert rejected.status_code == 409
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT status FROM tank.command_builds WHERE build_id=$1', uuid.UUID(build)) == 'running'
        assert await conn.fetchval('SELECT count(*) FROM tank.command_receipt_links') == 0
    assert (await client.post(route, headers=worker, json=body)).json()['receipt_status'] == 'pending'
    data = (await client.get(f'/v1/command/threads/{thread}')).json()
    assert data['receipts'] == []  # a same-tenant row is not inferred as build spend
    assert data['thread']['spent_usd'] is None
    assert data['thread']['receipt_status'] == 'pending'
    for _ in range(2):
        reply = await client.post(route, headers=worker, json={**body, 'ledger_ids': [str(own), str(own)]})
        assert reply.status_code == 200
    data = (await client.get(f'/v1/command/threads/{thread}')).json()
    assert len(data['receipts']) == 1
    assert data['receipts'][0]['cost_usd'] == 0.04


@pytest.mark.asyncio
async def test_binary_attachments_are_owned_bounded_and_not_given_to_text_model(api, monkeypatch):
    client, pool = api
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    binary = b'\x89PNG\x00\xffbinary bytes'
    metadata = (await client.post(f'/v1/command/threads/{thread}/attachments?name=picture.png', content=binary)).json()
    assert metadata['size'] == len(binary) and metadata['model_readable'] is False
    attachment = metadata['attachment_id']
    download = await client.get(f'/v1/command/attachments/{attachment}')
    assert download.content == binary
    assert download.headers['content-type'] == 'application/octet-stream'
    assert download.headers['x-content-type-options'] == 'nosniff'
    for headers in [{'x-tervory-user': 'other'}, {'x-tervory-tenant': str(OTHER)}]:
        assert (await client.get(f'/v1/command/attachments/{attachment}', headers=headers)).status_code == 404
    other = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    assert (await client.post(f'/v1/command/threads/{other}/turn', json={'text': 'Read', 'attachments': [{'attachment_id': attachment}]})).status_code == 404
    ledger = await seed_ledger(pool)
    turn = AsyncMock(return_value=SimpleNamespace(ledger_id=ledger, content='The attachment is stored.'))
    monkeypatch.setattr(command, 'route_turn', turn)
    assert (await client.post(f'/v1/command/threads/{thread}/turn', json={'text': 'Read', 'attachments': [{'attachment_id': attachment}]})).status_code == 200
    context = json.dumps(turn.call_args.args[1])
    assert 'content unavailable to the text router' in context
    assert 'binary bytes' not in context
    assert (await client.post(f'/v1/command/threads/{thread}/attachments?name=../bad', content=b'x')).status_code == 422
    monkeypatch.setattr(command, 'MAX_ATTACHMENT_BYTES', 4)
    assert (await client.post(f'/v1/command/threads/{thread}/attachments?name=big', content=b'12345')).status_code == 413
    monkeypatch.setattr(command, 'MAX_THREAD_ATTACHMENT_BYTES', len(binary))
    assert (await client.post(f'/v1/command/threads/{thread}/attachments?name=full', content=b'x')).status_code == 413


@pytest.mark.asyncio
async def test_invalid_plan_preserves_turn_receipt_and_creates_no_cards(api, monkeypatch):
    client, pool = api
    dispatch = AsyncMock()
    monkeypatch.setattr(command, 'dispatch_action', dispatch)
    thread, data = await planned_turn(client, pool, monkeypatch, [
        {'tool': 'skala.list', 'arguments': {}},
        {'tool': 'skala.approve', 'arguments': {'skill_id': '../invalid', 'version': '1.0.0'}},
    ])
    assert data['cards'] == []
    assert len(data['receipts']) == 1
    dispatch.assert_not_awaited()
    assert 'No action was started' in data['messages'][-1]['content']


@pytest.mark.asyncio
async def test_reused_tool_receipt_cannot_count_again_in_another_thread(api, monkeypatch):
    client, pool = api
    ledger = await seed_ledger(pool, cost=0.03)
    monkeypatch.setattr(command, 'dispatch_action', AsyncMock(return_value=(200, {'ledger_id': str(ledger)})))
    for index in range(2):
        thread, data = await planned_turn(client, pool, monkeypatch, [{'tool': 'skala.list', 'arguments': {}}])
        await client.post(f"/v1/command/cards/{data['cards'][0]['card_id']}", json={'action': 'confirm'})
        data = (await client.get(f'/v1/command/threads/{thread}')).json()
        assert sum(r['ledger_id'] == str(ledger) for r in data['receipts']) == (1 if index == 0 else 0)
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT count(*) FROM tank.command_receipt_links WHERE ledger_id=$1', ledger) == 1


@pytest.mark.asyncio
async def test_tool_http_failure_keeps_exact_receipt_and_is_not_retried(api, monkeypatch):
    client, pool = api
    ledger = await seed_ledger(pool, cost=0.04)
    dispatch = AsyncMock(return_value=(502, {'detail': {'ledger_id': str(ledger), 'upstream': 'private provider detail'}}))
    monkeypatch.setattr(command, 'dispatch_action', dispatch)
    thread, data = await planned_turn(client, pool, monkeypatch, [{'tool': 'skala.list', 'arguments': {}}])
    route = f"/v1/command/cards/{data['cards'][0]['card_id']}"
    result = await client.post(route, json={'action': 'confirm'})
    assert result.json()['status'] == 'failed'
    assert result.json()['receipt_status'] == 'recorded'
    assert 'private provider detail' not in result.text
    await client.post(route, json={'action': 'confirm'})
    dispatch.assert_awaited_once()
    data = (await client.get(f'/v1/command/threads/{thread}')).json()
    assert data['thread']['spent_usd'] == 0.06
    assert any(r['ledger_id'] == str(ledger) for r in data['receipts'])


def test_url_arguments_reject_embedded_credentials(runtime):
    actor = command.Actor(TENANT, 'founder', True)
    with pytest.raises(HTTPException):
        command.validate_action(command.PlannedAction(tool='tiger.watch', arguments={'url': 'https://dummy:dummy@example.com/item'}), actor)


@pytest.mark.asyncio
async def test_unknown_ledger_cost_can_refresh_without_reexecuting_action(api, monkeypatch):
    client, pool = api
    ledger = await seed_ledger(pool, cost=0)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE tank.ledger SET research_cost_status='unknown' WHERE id=$1", ledger)
    dispatch = AsyncMock(return_value=(200, {'ledger_id': str(ledger)}))
    monkeypatch.setattr(command, 'dispatch_action', dispatch)
    thread, data = await planned_turn(client, pool, monkeypatch, [{'tool': 'skala.list', 'arguments': {}}])
    route = f"/v1/command/cards/{data['cards'][0]['card_id']}"
    assert (await client.post(route, json={'action': 'confirm'})).json()['receipt_status'] == 'pending'
    assert (await client.get(f'/v1/command/threads/{thread}')).json()['thread']['spent_usd'] is None
    # Simulate the upstream cost-reconciliation owner supplying actual evidence.
    async with pool.acquire() as conn:
        await conn.execute("UPDATE tank.ledger SET cost_usd=0.04,research_cost_status='itemized' WHERE id=$1", ledger)
    assert (await client.post(route, json={'action': 'confirm'})).json()['receipt_status'] == 'recorded'
    data = (await client.get(f'/v1/command/threads/{thread}')).json()
    assert data['thread']['spent_usd'] == 0.06
    assert sum(r['ledger_id'] == str(ledger) for r in data['receipts']) == 1
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('known_family', [True, False])
async def test_core_baseline_fields_are_used_without_repricing(api, monkeypatch, known_family):
    client, pool = api
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tank.proof_prices(model,family,input_usd,output_usd) VALUES('fixture-model',$1,0.001,0.002),('sol','openai/gpt',0.001,0.002)", 'openai/gpt' if known_family else 'fixture')
    ledger = await seed_ledger(pool, cost=0.02)
    # Command must use the comparison stored by CORE-2, even if today's rates differ.
    await pool.execute("UPDATE tank.proof_prices SET input_usd=1,output_usd=2 WHERE model='sol'")
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    monkeypatch.setattr(command, 'route_turn', AsyncMock(return_value=SimpleNamespace(ledger_id=ledger, content='Measured answer')))
    await client.post(f'/v1/command/threads/{thread}/turn', json={'text': 'Hello'})
    receipt = (await client.get(f'/v1/command/threads/{thread}')).json()['receipts'][0]
    async with pool.acquire() as conn:
        actual = await conn.fetchrow('SELECT baseline_model,baseline_cost_usd FROM tank.ledger WHERE id=$1', ledger)
    assert receipt['baseline_model'] == actual['baseline_model']
    if known_family:
        assert receipt['baseline_model'] == 'sol'
        assert receipt['baseline_cost_usd'] == float(actual['baseline_cost_usd']) == 0.05
        assert receipt['saved_usd'] == pytest.approx(0.03)
    else:
        assert receipt['baseline_cost_usd'] is actual['baseline_cost_usd'] is None
        assert receipt['saved_usd'] is None and receipt['baseline_status'] == 'baseline pending'


@pytest.mark.asyncio
async def test_ledger_view_uses_existing_proof_route_and_scopes_output(api, runtime):
    client, pool = api
    runtime.app = client._transport.app
    received = []
    ledger_id = uuid.uuid4()

    @runtime.app.get('/v1/proof')
    async def existing_proof(request: Request):
        received.append(dict(request.query_params))
        assert request.headers['x-tervory-admin'] == 'founder'
        return {'tenants': [str(OTHER)], 'all_tenant_total': {'actual_usd': 999},
                'ledger': [{'id': str(ledger_id), 'tenant_id': str(TENANT), 'cost_usd': 0.02}],
                'page': 0, 'has_more': {'ledger': False}}

    actor = command.Actor(TENANT, 'founder', True)
    status, result = await command.dispatch_action(command.PlannedAction(tool='ledger.receipts', arguments={}), actor)
    assert status == 200
    assert received == [{'page': '0', 'tenant_id': str(TENANT)}]
    assert 'all_tenant_total' not in result and 'tenants' not in result
    assert result['ledger'][0]['id'] == str(ledger_id)
    assert command.explicit_ledger_ids(result) == []  # historical rows are not this read's cost



def test_scan_adapter_preserves_packaged_skill_input(runtime):
    import importlib.util
    skill = Path('tank/skills/scan.static/1.0.1')
    case = json.loads((skill / 'evals/cases.jsonl').read_text().splitlines()[0])
    actor = command.Actor(TENANT, 'founder', True)
    method, path, query, body = command.adapter_request(command.PlannedAction(tool='radix.scan', arguments=case['input']), actor)
    assert (method, path) == ('POST', '/v1/skala/scan.static/run')
    assert body['preview'] is False
    spec = importlib.util.spec_from_file_location('command_scan_fixture', skill / 'engine.py')
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    assert engine.run_skill(body['input']) == case['expected']
    _, _, _, scaffold = command.adapter_request(command.PlannedAction(tool='tisan.scaffold', arguments={
        'name': 'Example', 'slug': 'example', 'brand': {'colours': {}}}), actor)
    assert 'pages' not in scaffold['input']  # preserve the existing route's default pages
    assert command.ADAPTERS['tisan.scaffold'].paid is False  # current scaffold is pure code


@pytest.mark.asyncio
async def test_savings_adapter_uses_proof_and_keeps_unknown_values_pending(runtime):
    runtime.app = FastAPI()
    received = []

    @runtime.app.get('/v1/proof')
    async def existing_proof(request: Request):
        received.append(dict(request.query_params))
        return {'tenant_id': str(TENANT), 'all_tenant_total': {'actual_usd': 999}, 'tasks': [
            {'task_type': 'known', 'calls': 1, 'actual_usd': 0.02, 'baseline_usd': 0.05, 'saved_usd': 0.03, 'unknown_cost_calls': 0, 'unpriced_calls': 0},
            {'task_type': 'unknown', 'calls': 1, 'actual_usd': 0, 'baseline_usd': 0, 'saved_usd': 0, 'unknown_cost_calls': 1, 'unpriced_calls': 1},
        ]}

    status, data = await command.dispatch_action(command.PlannedAction(tool='ledger.savings', arguments={'days': 7}), command.Actor(TENANT, 'founder', True))
    assert status == 200 and received[0]['tenant_id'] == str(TENANT)
    assert 'start' in received[0] and 'end' in received[0] and 'days' not in received[0]
    assert 'all_tenant_total' not in data
    assert data['tasks'][0]['saved_usd'] == 0.03
    assert data['tasks'][1]['saved_usd'] is None and data['tasks'][1]['actual_usd'] is None
    assert data['tasks'][1]['receipt_status'] == 'pending'


def test_rig_approved_question_cannot_be_silently_truncated(runtime):
    with pytest.raises(HTTPException):
        command.validate_action(command.PlannedAction(tool='rig.ask', arguments={'product_id': 'fixture', 'question': 'x' * 501}), command.Actor(TENANT, 'founder', True))


@pytest.mark.asyncio
async def test_turn_failure_without_evidence_cannot_look_free(api, monkeypatch):
    client, pool = api
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    monkeypatch.setattr(command, 'route_turn', AsyncMock(side_effect=TimeoutError('No attributable ledger ID')))
    reply = await client.post(f'/v1/command/threads/{thread}/turn', json={'text': 'Try the work'})
    assert 'event: error' in reply.text and '"cost_usd": null' in reply.text
    data = (await client.get(f'/v1/command/threads/{thread}')).json()
    assert data['receipts'] == []
    assert data['thread']['spent_usd'] is None and data['thread']['receipt_status'] == 'pending'
    assert data['steps'][-1]['receipt_status'] == 'pending'
    other = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    monkeypatch.setattr(command, 'route_turn', AsyncMock(side_effect=HTTPException(503, 'paid work is paused')))
    await client.post(f'/v1/command/threads/{other}/turn', json={'text': 'Try the work'})
    data = (await client.get(f'/v1/command/threads/{other}')).json()
    assert data['steps'][-1]['receipt_status'] == 'not_started'
    assert data['receipts'] == []

    missing = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    monkeypatch.setattr(command, 'route_turn', AsyncMock(return_value=SimpleNamespace(ledger_id=uuid.uuid4(), content='Answer with missing evidence')))
    await client.post(f'/v1/command/threads/{missing}/turn', json={'text': 'Try the work'})
    data = (await client.get(f'/v1/command/threads/{missing}')).json()
    assert data['thread']['spent_usd'] is None
    assert data['steps'][-1]['receipt_status'] == 'pending'


@pytest.mark.asyncio
async def test_pinned_turn_passes_shared_hook_without_changing_request(runtime):
    runtime.Message = lambda **kw: kw
    runtime.RouteRequest = lambda **kw: SimpleNamespace(**kw)
    runtime.run_route = AsyncMock(return_value='answer')
    actor = command.Actor(TENANT, 'client', False)
    assert await command.route_turn(actor, [{'role': 'user', 'content': 'Hello'}], model='fixture-pin') == 'answer'
    runtime.run_route.assert_awaited_once()
    assert runtime.run_route.call_args.kwargs == {'model_override': 'fixture-pin'}
    assert runtime.run_route.call_args.args[0].task_type == 'command.turn'


@pytest.mark.asyncio
async def test_pinned_turn_receipt_uses_actual_model_and_pending_auto(api, runtime, monkeypatch):
    client, pool = api
    runtime.list_models = AsyncMock(return_value=[{'name': 'fixture-pin'}])
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    ledger_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tank.ledger(id,tenant_id,agent,task_type,model,tokens_in,tokens_out,cost_usd) VALUES($1,$2,'orchestrator','command.turn','fixture-actual',10,20,0.02)", ledger_id, TENANT)
    route = AsyncMock(return_value=SimpleNamespace(ledger_id=ledger_id, content='Pinned answer'))
    monkeypatch.setattr(command, 'route_turn', route)
    reply = await client.post(f'/v1/command/threads/{thread}/turn', json={'text': 'Hello', 'model': 'fixture-pin'})
    assert reply.status_code == 200 and 'event: done' in reply.text
    route.assert_awaited_once()
    assert route.call_args.kwargs['model'] == 'fixture-pin'
    assert isinstance(route.call_args.kwargs['task_id'], uuid.UUID)
    data = (await client.get(f'/v1/command/threads/{thread}')).json()
    receipt = data['receipts'][0]
    assert receipt['model'] == 'fixture-actual'  # the ledger wins over the requested pin
    assert receipt['requested_model'] == 'fixture-pin'
    assert receipt['auto_cost_usd'] is None and receipt['auto_cost_status'] == 'pending'
    assert receipt['cost_usd'] == 0.02
    assert data['steps'][-1]['requested_model'] == 'fixture-pin'
    assert data['messages'][0]['model'] == 'fixture-pin'


@pytest.fixture
def shared_router(monkeypatch):
    from contextlib import asynccontextmanager
    from test_tank_ladder import selected_policy_protocol
    selected_policy_protocol(monkeypatch)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://localhost/unused')
    monkeypatch.setenv('LITELLM_MASTER_KEY', 'dummy-router-key')
    monkeypatch.setenv('TANK_API_KEY', 'dummy-workspace-key')
    monkeypatch.setenv('TANK_REGION', 'us-east')
    monkeypatch.delenv('TANK_PROVIDER_ALLOW', raising=False)
    import main as tank
    # These tests isolate routing under an explicitly automatic tenant step.
    # Paid-step persistence and approval/card behavior run against Postgres in test_paid_steps.
    import paid_steps
    async def checkpoint(_key, invoke):
        return await invoke()
    monkeypatch.setattr(paid_steps, 'checkpoint', checkpoint)
    async def automatic_admission(_tenant, task, _model, **_kwargs):
        import controls
        await controls.refuse_paid(task=task, path='placeholder automatic model step')
        return None
    monkeypatch.setattr(paid_steps, 'model_admission', automatic_admission)
    policy = {'model': 'fixture-auto', 'priority': 'standard', 'rung': 1, 'system_prompt': 'Policy prompt',
              'max_tokens_out': 32, 'cache_scope': 'tenant', 'candidate_model': None, 'shadow_rate': 0}
    conn = SimpleNamespace(execute=AsyncMock(), fetchval=AsyncMock(return_value=False), fetch=AsyncMock(return_value=[]))

    @asynccontextmanager
    async def acquire():
        yield conn

    conn.transaction = acquire
    monkeypatch.setattr(tank, 'pool', lambda: SimpleNamespace(acquire=acquire))
    monkeypatch.setattr(tank, 'load_policy', AsyncMock(return_value=policy))
    monkeypatch.setattr(tank, 'list_models', AsyncMock(return_value=[{'name': 'fixture-pin'}, {'name': 'fixture-auto'}]))
    monkeypatch.setattr(tank, '_redis', None)
    calls = []

    class Gateway:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            calls.append(kwargs['json'])
            return httpx.Response(200, json={'choices': [{'message': {'content': 'Measured answer'}}],
                                             'usage': {'prompt_tokens': 10, 'completion_tokens': 20}},
                                  headers={'x-litellm-response-cost': '0.02', 'x-litellm-model-group': kwargs['json']['model']})

    monkeypatch.setattr(tank.httpx, 'AsyncClient', Gateway)
    req = tank.RouteRequest(tenant_id=TENANT, agent='orchestrator', task_type='command.turn', home_region='us-east',
                            max_tokens=1000, messages=[tank.Message(role='user', content='Hello')])
    return tank, conn, calls, policy, req


def memory_route_cache(monkeypatch, tank, conn):
    """Storage seam only; real key construction remains in run_route.

    Postgres persistence/isolation is exercised in test_tank_ladder.
    """
    cache = SimpleNamespace(entries={}, keys=[], forced=None)

    async def read(req, policy, identity):
        key = (str(req.tenant_id), identity[0], identity[1])
        cache.keys.append(key)
        return cache.forced if cache.forced is not None else cache.entries.get(key)

    async def write(req, policy, identity, ledger_id):
        transcript = next(call.args for call in conn.execute.call_args_list
                          if 'INSERT INTO tank.transcripts' in call.args[0] and call.args[1] == ledger_id)
        cache.entries[(str(req.tenant_id), identity[0], identity[1])] = transcript[-1]

    monkeypatch.setattr(tank, '_cache_read', read)
    monkeypatch.setattr(tank, '_cache_write', write)
    return cache


def receipt_seat(args):
    import re
    sql = args[0]
    columns = re.search(r'INSERT INTO tank.ledger\s*\((.*?)\)', sql, re.S)[1].split(',')
    position = [c.strip() for c in columns].index('route_seat')
    value = re.search(r'VALUES\s*\((.*)\)', sql, re.S)[1].split(',')[position]
    return args[int(re.search(r'\$(\d+)', value)[1])]


@pytest.mark.asyncio
async def test_shared_pin_keeps_policy_limits_and_records_actual_ledger(shared_router):
    tank, conn, calls, policy, req = shared_router
    before = dict(policy)
    answer = await tank.run_route(req, model_override='fixture-pin')
    assert len(calls) == 1 and calls[0]['model'] == 'fixture-pin'
    assert calls[0]['max_tokens'] == 32 and calls[0]['messages'][0]['content'] == 'Policy prompt'
    assert policy == before and answer.model == 'fixture-pin' and answer.cost_usd == 0.02
    ledger = [call.args for call in conn.execute.call_args_list if 'INSERT INTO tank.ledger' in call.args[0]]
    assert len(ledger) == 1 and ledger[0][2] == TENANT and ledger[0][8] == 'fixture-pin'
    assert ledger[0][10:13] == (10, 20, 0.02)
    assert not any('UPDATE tank.policy' in call.args[0] for call in conn.execute.call_args_list)
    tank.list_models.reset_mock()
    auto = await tank.run_route(req)
    assert auto.model == 'fixture-auto' and calls[-1]['model'] == 'fixture-auto'
    tank.list_models.assert_not_awaited()  # ordinary routing is unchanged


@pytest.mark.asyncio
@pytest.mark.parametrize('refusal,status', [('task', 403), ('roster', 422), ('provider', 403), ('pause', 503), ('region', 403)])
async def test_shared_pin_cannot_bypass_existing_guards(shared_router, monkeypatch, refusal, status):
    tank, conn, calls, policy, req = shared_router
    model = 'fixture-pin'
    if refusal == 'task':
        req.task_type = 'moa.proposer'  # future seats are not authorized yet
    elif refusal == 'roster':
        model = 'missing'
    elif refusal == 'provider':
        monkeypatch.setenv('TANK_PROVIDER_ALLOW', json.dumps({str(TENANT): ['fixture-auto']}))
    elif refusal == 'pause':
        conn.fetchval.return_value = True
    elif refusal == 'region':
        req.home_region = 'za'
    with pytest.raises(HTTPException) as error:
        await tank.run_route(req, model_override=model)
    assert error.value.status_code == status
    assert calls == []
    assert not any('INSERT INTO tank.ledger' in call.args[0] for call in conn.execute.call_args_list)
    assert policy['model'] == 'fixture-auto'


@pytest.mark.asyncio
async def test_shared_pin_uses_model_specific_cache_identity(shared_router, monkeypatch):
    tank, conn, calls, policy, req = shared_router
    cache = memory_route_cache(monkeypatch, tank, conn)
    await tank.run_route(req)
    await tank.run_route(req, model_override='fixture-pin')
    keys = list(cache.keys)
    assert keys[0] != keys[1] and str(TENANT) in keys[1]
    cache.forced = 'Cached pinned answer'
    answer = await tank.run_route(req, model_override='fixture-pin')
    assert cache.keys[-1] == keys[1]
    assert len(calls) == 2 and answer.rung == 4 and answer.model == 'fixture-pin'
    assert answer.cost_usd == 0 and answer.content == 'Cached pinned answer'


@pytest.mark.asyncio
async def test_tenant_squad_grants_are_founder_only_isolated_and_logged(api, monkeypatch):
    import asyncio
    client, pool = api
    client_headers = {'x-tervory-user': 'client', 'x-tervory-squad': 'base,tiger,zenas'}
    assert (await client.get('/v1/command/tools', headers=client_headers)).json() == sorted(command.TENANT_TOOLS)
    for method, path, body in [('get', '/v1/command/squads', None), ('get', '/v1/command/squads/log', None),
                                ('put', '/v1/command/squads/tiger', {'enabled': True})]:
        result = await client.request(method, path, headers=client_headers, json=body)
        assert result.status_code == 403
    replies = await asyncio.gather(*[client.put('/v1/command/squads/tiger', json={'enabled': True}) for _ in range(2)])
    assert sorted(r.json()['changed'] for r in replies) == [False, True]
    log = (await client.get('/v1/command/squads/log')).json()
    assert len(log) == 1 and log[0]['changed_by'] == 'founder' and log[0]['enabled'] is True
    grants = (await client.get('/v1/command/squads')).json()
    assert len(grants) == 7
    assert next(g for g in grants if g['agent'] == 'tiger')['enabled_by'] == 'founder'
    assert (await client.get('/v1/command/tools', headers=client_headers)).json() == sorted([*command.TENANT_TOOLS, 'plugins.list', 'tiger.alerts', 'tiger.match', 'tiger.match.confirm', 'tiger.match.reject', 'tiger.match.undo', 'tiger.matches', 'tiger.position', 'tiger.read', 'tiger.receipts', 'tiger.watch', 'tiger.watch.bulk', 'tiger.watch.pause', 'tiger.watch.remove', 'tiger.watch.resume', 'tiger.watches'])
    assert (await client.get('/v1/command/tools', headers={**client_headers, 'x-tervory-tenant': str(OTHER)})).json() == sorted(command.TENANT_TOOLS)
    assert (await client.get('/v1/command/squads/log', headers={'x-tervory-tenant': str(OTHER)})).json() == []
    assert (await client.put('/v1/command/squads/worker', json={'enabled': True})).status_code == 422
    await client.put('/v1/command/squads/tiger', json={'enabled': False})
    await client.put('/v1/command/squads/tiger', json={'enabled': False})
    assert (await client.get('/v1/command/tools', headers=client_headers)).json() == sorted(command.TENANT_TOOLS)
    log = (await client.get('/v1/command/squads/log')).json()
    assert [row['enabled'] for row in log] == [False, True]
    # Empty grants still allow chat; a model cannot grant access via its plan.
    ledger_id = await seed_ledger(pool)
    monkeypatch.setattr(command, 'route_turn', AsyncMock(return_value=SimpleNamespace(ledger_id=ledger_id, content='Chat is available')))
    thread = (await client.post('/v1/command/threads', headers=client_headers, json={})).json()['thread_id']
    response = await client.post(f'/v1/command/threads/{thread}/turn', headers=client_headers, json={'text': 'Hello'})
    assert 'Chat is available' in response.text and 'event: done' in response.text


@pytest.mark.asyncio
async def test_revoked_squad_blocks_saved_card_and_dispatch(api, monkeypatch, runtime):
    client, pool = api
    headers = {'x-tervory-user': 'client', 'x-tervory-squad': 'tiger'}
    await client.put('/v1/command/squads/tiger', json={'enabled': True})
    ledger_id = await seed_ledger(pool)
    plan = {'text': 'Please confirm', 'actions': [{'tool': 'tiger.watch', 'arguments': {'url': 'https://example.test/product'}}]}
    monkeypatch.setattr(command, 'route_turn', AsyncMock(return_value=SimpleNamespace(ledger_id=ledger_id, content=json.dumps(plan))))
    thread = (await client.post('/v1/command/threads', headers=headers, json={})).json()['thread_id']
    await client.post(f'/v1/command/threads/{thread}/turn', headers=headers, json={'text': 'Watch this product'})
    data = (await client.get(f'/v1/command/threads/{thread}', headers=headers)).json()
    card_id = data['cards'][0]['card_id']
    await client.put('/v1/command/squads/tiger', json={'enabled': False})
    assert (await client.post(f'/v1/command/cards/{card_id}', headers=headers, json={'action': 'confirm'})).status_code == 403
    # Even a stale Actor carrying the former grant cannot dispatch.
    runtime.app = client._transport.app
    with pytest.raises(HTTPException) as error:
        await command.dispatch_action(command.PlannedAction(**plan['actions'][0]), command.Actor(TENANT, 'client', False, frozenset({'tiger'})))
    assert error.value.status_code == 403
    assert (await client.post(f'/v1/command/cards/{card_id}', headers=headers, json={'action': 'cancel'})).json()['status'] == 'cancelled'


@pytest.mark.asyncio
async def test_squad_audit_failure_rolls_back_permission(api):
    client, pool = api
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE tank.tenant_squad_log ADD CONSTRAINT fixture_refuse_audit CHECK (false)")
    with pytest.raises(asyncpg.CheckViolationError):
        await client.put('/v1/command/squads/tiger', json={'enabled': True})
    assert not any(g['enabled'] for g in (await client.get('/v1/command/squads')).json())


@pytest.mark.asyncio
async def test_auto_comparison_prices_same_tokens_with_frozen_tenant_policy(api, runtime, monkeypatch):
    client, pool = api
    runtime.list_models = AsyncMock(return_value=[{'name': 'fixture-pin'}])
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tank.proof_prices(model,family,input_usd,output_usd) VALUES('fixture-auto','fixture',0.001,0.002),('fixture-platform','fixture',0.5,0.5)")
        # schema.sql now seeds a platform command.turn row; replace it with this test's fixture.
        await conn.execute("DELETE FROM tank.policy WHERE task_type='command.turn' AND tenant_id IS NULL")
        await conn.execute("INSERT INTO tank.policy(tenant_id,task_type,model) VALUES(NULL,'command.turn','fixture-platform'),($1,'command.turn','fixture-auto')", TENANT)
    ledger_id = await seed_ledger(pool)

    async def answer(actor, messages, model=None, *, task_id=None, on_token=None):
        assert model == 'fixture-pin'
        # A price or policy edit during the call must not rewrite its comparison.
        async with pool.acquire() as conn:
            await conn.execute("UPDATE tank.proof_prices SET input_usd=5 WHERE model='fixture-auto'")
            await conn.execute("UPDATE tank.policy SET model='fixture-platform' WHERE tenant_id=$1 AND task_type='command.turn'", TENANT)
        return SimpleNamespace(ledger_id=ledger_id, content='Measured answer')

    route = AsyncMock(side_effect=answer)
    monkeypatch.setattr(command, 'route_turn', route)
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    reply = await client.post(f'/v1/command/threads/{thread}/turn', json={'text': 'Hello', 'model': 'fixture-pin'})
    assert 'event: done' in reply.text
    route.assert_awaited_once()
    data = (await client.get(f'/v1/command/threads/{thread}')).json()
    receipt = data['receipts'][0]
    assert receipt['auto_model'] == 'fixture-auto'
    assert receipt['auto_cost_usd'] == pytest.approx(10 * 0.001 + 20 * 0.002)
    assert receipt['auto_cost_status'] == 'priced' and receipt['auto_priced_at']
    assert 'same tokens' in receipt['auto_cost_label'] and 'not a separate model run' in receipt['auto_cost_basis']
    assert receipt['cost_usd'] == 0.02
    replay = await client.get(f'/v1/command/threads/{thread}/events')
    assert '"auto_cost_usd": 0.05' in replay.text
    async with pool.acquire() as conn:
        other = await command.auto_price_snapshot(conn, OTHER)
        assert other['model'] == 'fixture-platform'
        await conn.execute("UPDATE tank.proof_prices SET active=false WHERE model='fixture-platform'")
        missing = await command.auto_price_snapshot(conn, OTHER)
        assert missing['model'] == 'fixture-platform' and missing['input_usd'] is None


@pytest.mark.asyncio
async def test_customer_receipts_carry_no_model_names_but_keep_the_auto_dollar_figure(api, runtime, monkeypatch):
    # Review item 3: only the founder sees serving, baseline and Auto model names.
    client, pool = api
    runtime.list_models = AsyncMock(return_value=[{'name': 'fixture-pin'}])
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tank.proof_prices(model,family,input_usd,output_usd) VALUES('fixture-auto','fixture',0.001,0.002)")
        await conn.execute("DELETE FROM tank.policy WHERE task_type='command.turn' AND tenant_id IS NULL")
        await conn.execute("INSERT INTO tank.policy(tenant_id,task_type,model) VALUES($1,'command.turn','fixture-auto')", TENANT)
    names = ('fixture-auto', 'fixture-model', 'fixture-baseline', 'fixture-pin', '"auto_model"', '"baseline_model"',
             '"requested_model"', '"model"', 'auto_comparison', 'input_usd', 'output_usd', 'provider')
    results = {}
    for user in ('customer', 'founder'):
        ledger_id = await seed_ledger(pool)
        await pool.execute("UPDATE tank.ledger SET baseline_model='fixture-baseline',baseline_cost_usd=0.5 WHERE id=$1", ledger_id)
        monkeypatch.setattr(command, 'route_turn', AsyncMock(return_value=SimpleNamespace(ledger_id=ledger_id, content='Measured answer')))
        headers = {'x-tervory-user': user}
        thread = (await client.post('/v1/command/threads', json={}, headers=headers)).json()['thread_id']
        # Customers can no longer pin (403), but threads pinned before that change keep
        # the names in durable state: make this one by pinning while briefly an admin.
        set_founders(monkeypatch, f'founder,{user}')
        reply = await client.post(f'/v1/command/threads/{thread}/turn', json={'text': 'Hello', 'model': 'fixture-pin'}, headers=headers)
        assert reply.status_code == 200, reply.text
        set_founders(monkeypatch, 'founder')
        snapshot = await client.get(f'/v1/command/threads/{thread}', headers=headers)
        events = await client.get(f'/v1/command/threads/{thread}/events', headers=headers)
        receipts = await client.get(f"/v1/command/tasks/{snapshot.json()['steps'][0]['step_id']}/receipts", headers=headers)
        listed = await client.get('/v1/command/threads', headers=headers)
        results[user] = (snapshot.text, events.text, listed.text, receipts.text, snapshot.json()['receipts'][0])
    for text in results['customer'][:4]:
        for name in names:
            assert name not in text, name
    for text in results['customer'][:2]:
        assert 'auto_cost_usd' in text
    customer = results['customer'][4]
    assert customer['auto_cost_usd'] == pytest.approx(0.05) and customer['auto_cost_status'] == 'priced'
    assert customer['cost_usd'] == 0.02 and customer['baseline_cost_usd'] == 0.5 and 'same tokens' in customer['auto_cost_label']
    assert not {'model', 'baseline_model', 'auto_model', 'requested_model'} & customer.keys()
    founder = results['founder'][4]
    assert (founder['model'], founder['baseline_model'], founder['auto_model']) == ('fixture-model', 'fixture-baseline', 'fixture-auto')
    assert founder['auto_cost_usd'] == pytest.approx(0.05)
    assert 'auto_comparison' in results['founder'][0] and '"requested_model"' in results['founder'][0]
    # Durable state still holds the full evidence for the founder's audit.
    state = await pool.fetchval("SELECT state FROM tank.command_threads WHERE user_id='customer'")
    assert 'fixture-auto' in (state if isinstance(state, str) else json.dumps(state))


@pytest.mark.asyncio
async def test_query_tenant_never_authenticates(runtime):
    req = request(**{'x-tank-key': runtime.TANK_API_KEY, 'x-tervory-user': 'founder'})
    req.scope['query_string'] = f'tenant_id={TENANT}'.encode()
    with pytest.raises(HTTPException) as error:
        await command.identity(req)
    assert error.value.status_code == 422


@pytest.mark.parametrize('content', ['{braces} can start a sentence.', '{"example": 1}', '{unfinished'])
def test_brace_prefixed_text_is_preserved_without_actions(content):
    actor = command.Actor(TENANT, 'founder', True)
    plan = command.parse_plan(content, actor)
    assert plan.text == content and plan.actions == []
    assert 'repo.read' not in command.allowed_tools(actor)
    assert 'repo.read' not in command.planning_message(actor)['content']


@pytest.mark.asyncio
async def test_pinned_failure_is_clear_and_logs_only_exception_type(api, runtime, monkeypatch, caplog):
    client, pool = api
    runtime.list_models = AsyncMock(return_value=[{'name': 'fixture-pin'}])
    monkeypatch.setattr(command, 'route_turn', AsyncMock(side_effect=RuntimeError('private provider detail')))
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    reply = await client.post(f'/v1/command/threads/{thread}/turn', json={'text': 'Hi', 'model': 'fixture-pin'})
    assert 'The pinned model did not answer. Pinned models never fall back; choose Auto or another model.' in reply.text
    # Review of #506 B2: the stored and streamed error never echoes the pinned name.
    error_events = [line for line in reply.text.split('\n\n') if line.startswith('id:') and 'event: error' in line]
    assert error_events and all('fixture-pin' not in e for e in error_events)
    assert await pool.fetchval("SELECT count(*) FROM tank.tasks WHERE error LIKE '%fixture-pin%'") == 0
    assert 'RuntimeError' in caplog.text
    assert 'private provider detail' not in caplog.text + reply.text
    data = (await client.get(f'/v1/command/threads/{thread}')).json()
    assert data['steps'][-1]['receipt_status'] == 'pending'
    assert data['cards'] == []


@pytest.mark.asyncio
async def test_shared_override_disables_fallback_and_shadow_without_mutating_policy(shared_router, monkeypatch):
    tank, conn, calls, policy, req = shared_router
    policy.update(rung=2, candidate_model='fixture-candidate', shadow_rate=1)
    original = dict(policy)
    shadow = AsyncMock()
    monkeypatch.setattr(tank, '_shadow', shadow)
    answer = await tank.run_route(req, model_override='fixture-pin')
    assert calls[0]['disable_fallbacks'] is True
    assert answer.rung == 1 and policy == original
    shadow.assert_not_called()
    ledger = next(c.args for c in conn.execute.call_args_list if 'INSERT INTO tank.ledger' in c.args[0])
    assert ledger[7] == 1 and receipt_seat(ledger) == 'pinned'
    # Auto still follows its policy, including the existing shadow mechanism.
    await tank.run_route(req)
    import asyncio
    await asyncio.gather(*list(tank._background))
    shadow.assert_awaited_once()
    assert calls[-1]['disable_fallbacks'] is True  # Tank admits each fallback seat separately.


@pytest.mark.asyncio
@pytest.mark.parametrize('task_type', ['rig.ask#moa.synthesizer', 'command.turn#moa.seat_1', 'Zenas-2#moa.a'])
async def test_moa_seat_requires_override_and_loads_parent_policy(shared_router, task_type):
    tank, conn, calls, policy, req = shared_router
    req.task_type = task_type
    with pytest.raises(HTTPException) as error:
        await tank.run_route(req)
    assert error.value.status_code == 400
    assert calls == []
    tank.load_policy.assert_not_awaited()
    await tank.run_route(req, model_override='fixture-pin')
    tank.load_policy.assert_awaited_once_with(task_type.partition('#moa.')[0], TENANT)
    assert calls[0]['disable_fallbacks'] is True
    ledger = next(c.args for c in conn.execute.call_args_list if 'INSERT INTO tank.ledger' in c.args[0])
    assert ledger[6] == task_type and receipt_seat(ledger) == task_type.partition('#moa.')[2]


@pytest.mark.asyncio
@pytest.mark.parametrize('task_type', ['rig.ask#moa.', '#moa.a', 'rig.ask#moa.A', 'rig.ask#moa.a-b',
                                      'rig.ask#moa.a\n', 'rig.ask#moa.a#moa.b', 'rig.ask#moa.' + 'a' * 81])
async def test_malformed_seat_cannot_use_override(shared_router, task_type):
    tank, conn, calls, policy, req = shared_router
    req.task_type = task_type
    assert not tank.override_allowed(task_type)
    with pytest.raises(HTTPException) as error:
        await tank.run_route(req, model_override='fixture-pin')
    assert error.value.status_code == 403 and calls == []


@pytest.mark.asyncio
async def test_same_model_auto_pin_and_seats_do_not_share_cache(shared_router, monkeypatch):
    tank, conn, calls, policy, req = shared_router
    cache = memory_route_cache(monkeypatch, tank, conn)
    for task, override in [('command.turn', None), ('command.turn', 'fixture-auto'),
                            ('command.turn#moa.writer', 'fixture-auto'), ('command.turn#moa.judge', 'fixture-auto')]:
        req.task_type = task
        first = await tank.run_route(req, model_override=override)
        cached = await tank.run_route(req, model_override=override)
        assert first.rung == 1 and cached.rung == 4
    assert len(cache.entries) == 4 and len(calls) == 4
    ledger = [c.args for c in conn.execute.call_args_list if 'INSERT INTO tank.ledger' in c.args[0]]
    assert [receipt_seat(row) for row in ledger] == [None, None, 'pinned', 'pinned', 'writer', 'writer', 'judge', 'judge']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['delivered', 'cache', 'transport', 'provider'])
@pytest.mark.parametrize('task,override,seat', [('command.turn', None, None), ('command.turn', 'fixture-pin', 'pinned'),
                                              ('rig.ask#moa.writer', 'fixture-pin', 'writer')])
async def test_route_seat_is_durable_in_every_ledger_insert(shared_router, command_pool, monkeypatch, mode, task, override, seat):
    tank, conn, calls, policy, req = shared_router
    req.task_type = task
    if mode == 'cache':
        memory_route_cache(monkeypatch, tank, conn).forced = 'Cached'
    elif mode in {'transport', 'provider'}:
        response = httpx.Response(503, json={'error': {'message': 'Unavailable'}})
        post = AsyncMock(side_effect=httpx.ConnectError('unavailable')) if mode == 'transport' else AsyncMock(return_value=response)
        monkeypatch.setattr(tank.httpx.AsyncClient, 'post', post)
    if mode in {'transport', 'provider'}:
        with pytest.raises(HTTPException) as error:
            await tank.run_route(req, model_override=override)
        assert error.value.status_code == 502
    else:
        await tank.run_route(req, model_override=override)
    # Execute the router's actual SQL and bound arguments against the reapplied
    # schema, checking all three INSERT shapes rather than just SQL strings.
    ledger = [c.args for c in conn.execute.call_args_list if 'INSERT INTO tank.ledger' in c.args[0]]
    assert len(ledger) == 1
    async with command_pool.acquire() as database:
        await database.execute(*ledger[0])
        row = await database.fetchrow('SELECT * FROM tank.ledger WHERE id=$1 AND tenant_id=$2', ledger[0][1], TENANT)
    assert row['route_seat'] == seat and row['task_type'] == task
    assert row['outcome'] == ('failed' if mode in {'transport', 'provider'} else 'delivered')
    assert row['rung'] == (4 if mode == 'cache' else 1)


@pytest.mark.asyncio
async def test_moa_case_ref_separates_bench_samples_without_changing_seat(shared_router, monkeypatch):
    tank, conn, calls, policy, req = shared_router
    cache = memory_route_cache(monkeypatch, tank, conn)
    req.task_type = 'command.turn#moa.writer'
    for case in ('first', 'second'):
        req.case_ref = case
        assert (await tank.run_route(req, model_override='fixture-auto')).rung == 1
        assert (await tank.run_route(req, model_override='fixture-auto')).rung == 4
    assert len(cache.entries) == 2 and len(calls) == 2
    ledger = [c.args for c in conn.execute.call_args_list if 'INSERT INTO tank.ledger' in c.args[0]]
    assert all(receipt_seat(row) == 'writer' for row in ledger)


@pytest.mark.asyncio
async def test_command_roster_reuses_existing_models_and_requires_identity(api, runtime):
    client, _ = api
    roster = [{'name': 'fixture-pin', 'input_usd_per_million': 1.0}]
    runtime.list_models = AsyncMock(return_value=roster)
    assert (await client.get('/v1/command/models')).json() == roster
    runtime.list_models.assert_awaited_once_with()
    refused = await client.get('/v1/command/models', headers={'x-tank-key': ''})
    assert refused.status_code == 401
    assert runtime.list_models.await_count == 1


@pytest.mark.asyncio
async def test_code_first_proposal_never_routes_or_dispatches_until_confirm(api, monkeypatch):
    client, pool = api
    route = AsyncMock(side_effect=AssertionError('Proposal must use code'))
    dispatch = AsyncMock(return_value=(200, {'ok': True}))
    monkeypatch.setattr(command, 'route_turn', route)
    monkeypatch.setattr(command, 'dispatch_action', dispatch)
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    path = f'/v1/command/threads/{thread}/actions'
    action = {'tool': 'skala.approve', 'arguments': {'skill_id': 'base.find_site', 'version': '1.0.0'}}
    result = await client.post(path, json=action)
    assert result.status_code == 200
    card = result.json()
    assert card['status'] == 'pending' and card['kind'] == 'approve_skill'
    assert 'payload' not in card
    dispatch.assert_not_awaited()
    route.assert_not_awaited()
    # The card and planned step survive a fresh polling request.
    saved = (await client.get(f'/v1/command/threads/{thread}')).json()
    assert saved['cards'] == [card] and saved['steps'][0]['status'] == 'planned'
    assert saved['seq'] == 2
    assert (await client.get(f"/v1/command/threads/{thread}/events?after={saved['seq']}")).text == ''
    assert saved['messages'] == [] and saved['receipts'] == []
    assert (await client.post(path, json={**action, 'arguments': {**action['arguments'], 'tenant_id': str(OTHER)}})).status_code == 422
    assert (await client.post(path, json=action, headers={'x-tervory-tenant': str(OTHER)})).status_code == 404
    assert (await client.post(path, json=action, headers={'x-tervory-user': 'client'})).status_code == 404
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT count(*) FROM tank.tasks') == 0
    confirmed = await client.post(f"/v1/command/cards/{card['card_id']}", json={'action': 'confirm'})
    assert confirmed.json()['status'] == 'confirmed'
    dispatch.assert_awaited_once()
    route.assert_not_awaited()


@pytest.mark.asyncio
async def test_code_first_proposal_rechecks_roles_and_cancel_has_no_side_effect(api, monkeypatch):
    client, _ = api
    dispatch = AsyncMock(side_effect=AssertionError('Cancelled action must not dispatch'))
    monkeypatch.setattr(command, 'dispatch_action', dispatch)
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    path = f'/v1/command/threads/{thread}/actions'
    action = {'tool': 'worker.build', 'arguments': {'brief_markdown': 'Offline test brief'}}
    card = (await client.post(path, json=action)).json()
    assert (await client.post(f"/v1/command/cards/{card['card_id']}", json={'action': 'cancel'})).json()['status'] == 'cancelled'
    set_founders(monkeypatch, '')
    assert (await client.post(path, json=action)).status_code == 403
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_task_receipts_require_owner_and_exact_ledger(api, monkeypatch):
    client, pool = api
    ledger = await seed_ledger(pool)
    monkeypatch.setattr(command, 'route_turn', AsyncMock(return_value=SimpleNamespace(ledger_id=ledger, content='Recorded answer')))
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    reply = await client.post(f'/v1/command/threads/{thread}/turn', json={'text': 'Offline task test'})
    assert 'event: done' in reply.text
    saved = (await client.get(f'/v1/command/threads/{thread}')).json()
    task_id = saved['messages'][-1]['message_id']
    path = f'/v1/command/tasks/{task_id}/receipts'
    response = await client.get(path)
    assert response.status_code == 200
    assert response.json()['rows'] == saved['receipts']
    assert response.json()['totals']['cost_usd'] == 0.02
    assert response.json()['totals']['baseline_cost_usd'] is None
    assert response.json()['status'] == 'recorded'
    assert (await client.get(path, headers={'x-tervory-user': 'client'})).status_code == 404
    assert (await client.get(path, headers={'x-tervory-tenant': str(OTHER)})).status_code == 404
    assert (await client.get(path, headers={'x-tank-key': ''})).status_code == 401
    async with pool.acquire() as conn:
        # Other runs are readable only when created by this exact user.
        unlinked = uuid.uuid4()
        await conn.execute("INSERT INTO tank.tasks(id,tenant_id,created_by,task_type,title,input,ledger_id) VALUES($1,$2,'founder','command.turn','offline','{}',$3)", unlinked, TENANT, ledger)
        assert (await client.get(f'/v1/command/tasks/{unlinked}/receipts')).json()['rows'][0]['ledger_id'] == str(ledger)
        await conn.execute("UPDATE tank.tasks SET created_by='someone-else' WHERE id=$1", unlinked)
        assert (await client.get(f'/v1/command/tasks/{unlinked}/receipts')).status_code == 404
        await conn.execute('UPDATE tank.tasks SET ledger_id=NULL WHERE id=$1', uuid.UUID(task_id))
    pending = (await client.get(path)).json()
    assert pending == {'rows': [], 'totals': {'cost_usd': None, 'baseline_cost_usd': None, 'saved_usd': None}, 'status': 'pending'}
    async with pool.acquire() as conn:
        await conn.execute('UPDATE tank.tasks SET ledger_id=$2 WHERE id=$1', uuid.UUID(task_id), ledger)
        await conn.execute("UPDATE tank.ledger SET cost_usd=0,cost_status='not_set' WHERE id=$1", ledger)
    unknown = (await client.get(path)).json()
    assert unknown['rows'][0]['cost_usd'] is None and unknown['totals']['cost_usd'] is None
    assert unknown['status'] == 'pending'
    async with pool.acquire() as conn:
        await conn.execute("UPDATE tank.ledger SET cost_status='set' WHERE id=$1", ledger)
    zero = (await client.get(path)).json()
    assert zero['rows'][0]['cost_usd'] == zero['totals']['cost_usd'] == 0
    assert zero['status'] == 'recorded'
    foreign = await seed_ledger(pool, tenant=OTHER)
    async with pool.acquire() as conn:
        await conn.execute('UPDATE tank.tasks SET ledger_id=$2 WHERE id=$1', uuid.UUID(task_id), foreign)
    assert (await client.get(path)).status_code == 409


@pytest.mark.asyncio
async def test_customer_lap_over_every_cli_corridor_route_names_no_model_price_or_auto_model(api, runtime, monkeypatch):
    # Review of #506, self-review (b): every route a customer CLI token reaches
    # (tests/test_pat_route_corridor.py pins the list), success and error bodies and
    # streams, carries no model name, provider model, price-book rate or Auto model.
    client, pool = api
    runtime.list_models = AsyncMock(return_value=[{'name': 'fixture-pin', 'provider_model': 'provider/fixture', 'input_usd_per_m': 3}])
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tank.proof_prices(model,family,input_usd,output_usd) VALUES('fixture-auto','fixture',0.001,0.002)")
        await conn.execute("DELETE FROM tank.policy WHERE task_type='command.turn' AND tenant_id IS NULL")
        await conn.execute("INSERT INTO tank.policy(tenant_id,task_type,model) VALUES($1,'command.turn','fixture-auto')", TENANT)
    forbidden = ('fixture-auto', 'fixture-model', 'fixture-baseline', 'fixture-pin', 'provider/fixture', '"model"',
                 '"auto_model"', '"baseline_model"', '"requested_model"', 'auto_comparison', 'input_usd', 'output_usd',
                 'usd_per_m', 'provider')
    headers = {'x-tervory-user': 'customer'}
    bodies = []

    async def call(method, path, **kwargs):
        response = await client.request(method, path, headers=headers, **kwargs)
        bodies.append((method, path, response.status_code, response.text))
        return response

    ledger_id = await seed_ledger(pool)
    await pool.execute("UPDATE tank.ledger SET baseline_model='fixture-baseline',baseline_cost_usd=0.5 WHERE id=$1", ledger_id)
    plan = json.dumps({'text': 'Please confirm.', 'actions': [{'tool': 'tiger.read', 'arguments': {}}]})
    monkeypatch.setattr(command, 'route_turn', AsyncMock(return_value=SimpleNamespace(ledger_id=ledger_id, content=plan)))
    assert (await call('GET', '/v1/command/models')).status_code == 404
    await call('GET', '/v1/command/tools')
    thread = (await call('POST', '/v1/command/threads', json={'title': 'Lap'})).json()['thread_id']
    await call('PATCH', f'/v1/command/threads/{thread}', json={'title': 'Lap two'})
    assert (await call('POST', f'/v1/command/threads/{thread}/turn', json={'text': 'Hi', 'model': 'fixture-pin'})).status_code == 403
    assert (await call('POST', f'/v1/command/threads/{thread}/turn', json={'text': 'Hi', 'model': 'not-a-model'})).status_code == 403
    assert (await call('POST', f'/v1/command/threads/{thread}/turn', json={'text': 'Hi'})).status_code == 200
    snapshot = (await call('GET', f'/v1/command/threads/{thread}')).json()
    assert snapshot['receipts'][0]['cost_usd'] == 0.02 and snapshot['receipts'][0]['baseline_cost_usd'] == 0.5
    await call('GET', f'/v1/command/threads/{thread}/events')
    await call('GET', '/v1/command/threads')
    await call('GET', f"/v1/command/tasks/{snapshot['steps'][0]['step_id']}/receipts")
    for card in snapshot['cards']:
        await call('POST', f"/v1/command/cards/{card['card_id']}", json={'action': 'cancel'})
    await call('POST', f'/v1/command/threads/{thread}/actions', json={'tool': 'tiger.read', 'arguments': {}})
    await call('POST', '/v1/command/build', json={'thread_id': thread, 'brief_markdown': 'Change'})
    # Error paths: an upstream refusal that names the model, and a provider crash.
    monkeypatch.setattr(command, 'route_turn', AsyncMock(side_effect=HTTPException(503, 'fixture-auto is unavailable')))
    await call('POST', f'/v1/command/threads/{thread}/turn', json={'text': 'Again'})
    monkeypatch.setattr(command, 'route_turn', AsyncMock(side_effect=RuntimeError('fixture-auto provider/fixture exploded')))
    await call('POST', f'/v1/command/threads/{thread}/turn', json={'text': 'Once more'})
    await call('GET', f'/v1/command/threads/{thread}')
    await call('GET', f'/v1/command/threads/{thread}/events')
    assert {path for _, path, _, _ in bodies} >= {'/v1/command/models', '/v1/command/tools', '/v1/command/threads', '/v1/command/build',
                                                  f'/v1/command/threads/{thread}', f'/v1/command/threads/{thread}/events',
                                                  f'/v1/command/threads/{thread}/turn', f'/v1/command/threads/{thread}/actions'}
    for method, path, status, text in bodies:
        for name in forbidden:
            assert name not in text, (method, path, status, name)
    errors = await pool.fetch("SELECT error FROM tank.tasks WHERE created_by='customer' AND error IS NOT NULL")
    assert errors and all('fixture' not in row['error'] for row in errors)


def test_rig_confirmation_tool_is_admin_and_founder_only(runtime):
    tool = 'rig.quote.confirm_payment'
    member = command.Actor(TENANT, 'placeholder-member', False, frozenset({'rig'}))
    admin = command.Actor(TENANT, 'placeholder-admin', False, tenant_admin=True)
    founder = command.Actor(TENANT, 'placeholder-founder', True)
    assert tool not in command.allowed_tools(member)
    assert tool in command.allowed_tools(admin)
    assert tool in command.allowed_tools(founder)
    assert tool in command.TOOLS['rig']
    with pytest.raises(HTTPException) as denied:
        command.require_tool(member, tool)
    assert denied.value.status_code == 403
    action = command.validate_action(command.PlannedAction(tool=tool, arguments={'quote_id': 'placeholder'}), admin)
    assert command.adapter_request(action, admin) == (
        'POST', '/v1/access/rig/admin/confirm-store-payment', {}, {'data': {'quote_id': 'placeholder'}})
    for extra in ('tenant_id', 'founder', 'bank_details'):
        with pytest.raises(HTTPException) as invalid:
            command.validate_action(command.PlannedAction(tool=tool, arguments={'quote_id': 'placeholder', extra: 'placeholder'}), admin)
        assert invalid.value.status_code == 422


@pytest.mark.asyncio
async def test_rig_confirmation_card_needs_confirm_and_records_zero_code_receipt(api, runtime, monkeypatch):
    import access
    client, pool = api
    runtime.pool = lambda: pool
    client._transport.app.include_router(access.router)
    room = AsyncMock(return_value={'quote_id': 'placeholder', 'state': 'store_confirmed'})
    monkeypatch.setattr(access, 'rig_request', room)
    route = AsyncMock(side_effect=AssertionError('No model calls'))
    monkeypatch.setattr(command, 'route_turn', route)
    async def dispatch(action, actor):
        method, path, _, body = command.adapter_request(action, actor)
        response = await client.request(method, path, json=body)
        return response.status_code, response.json()
    monkeypatch.setattr(command, 'dispatch_action', dispatch)
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    action = {'tool': 'rig.quote.confirm_payment', 'arguments': {'quote_id': 'placeholder'}}
    proposed = await client.post(f'/v1/command/threads/{thread}/actions', json=action)
    assert proposed.status_code == 200
    card = proposed.json()
    assert card['status'] == 'pending' and card['kind'] == 'other'
    assert card['summary'] == action['tool']
    direct = await client.post('/v1/access/rig/admin/confirm-store-payment', json={'data': action['arguments']})
    assert direct.status_code == 409
    room.assert_not_awaited()
    confirmed = await client.post(f"/v1/command/cards/{card['card_id']}", json={'action': 'confirm'})
    assert confirmed.json()['status'] == 'confirmed'
    room.assert_awaited_once()
    actor, operation, arguments = room.call_args.args
    assert actor.tenant_id == TENANT and operation == 'confirm-store-payment' and arguments == action['arguments']
    saved = (await client.get(f'/v1/command/threads/{thread}')).json()
    assert len(saved['receipts']) == 1
    receipt = saved['receipts'][0]
    assert receipt['cost_usd'] == 0 and receipt['model'] == 'code'
    await client.post(f"/v1/command/cards/{card['card_id']}", json={'action': 'confirm'})
    room.assert_awaited_once()
    route.assert_not_awaited()


@pytest.mark.asyncio
async def test_rig_confirmation_other_tenant_quote_is_404(api, runtime, monkeypatch, tmp_path):
    import access
    from test_rig_optional_auto_order import auto_shop
    from test_rig_locate_checkout import TENANT as RIG_TENANT
    from rigbox.service import RigError
    client, pool = api
    client.headers.update({"x-tervory-user": "placeholder-admin", "x-tervory-role": "org:admin"})
    runtime.pool = lambda: pool
    client._transport.app.include_router(access.router)
    store, service, _, _, body = await auto_shop(monkeypatch, tmp_path)
    accepted = await service.locate_accept(RIG_TENANT, body)
    async def room(actor, operation, data):
        assert actor.tenant_id == TENANT and str(TENANT) != str(RIG_TENANT)
        try:
            return await service.confirm_store_payment(str(actor.tenant_id), data['quote_id'])
        except RigError as error:
            raise HTTPException(error.status, 'placeholder unavailable') from None
    monkeypatch.setattr(access, 'rig_request', room)
    statuses = []
    async def dispatch(action, actor):
        method, path, _, body = command.adapter_request(action, actor)
        response = await client.request(method, path, json=body)
        statuses.append(response.status_code)
        return response.status_code, response.json()
    monkeypatch.setattr(command, 'dispatch_action', dispatch)
    thread = (await client.post('/v1/command/threads', json={})).json()['thread_id']
    card = (await client.post(f'/v1/command/threads/{thread}/actions', json={
        'tool': 'rig.quote.confirm_payment', 'arguments': {'quote_id': accepted['request_id']}})).json()
    result = await client.post(f"/v1/command/cards/{card['card_id']}", json={'action': 'confirm'})
    assert result.json()['status'] == 'failed'
    assert statuses == [404]
    assert not store.locate_orders
    # The confirmed corridor preserves the room's 404 and writes no success receipt.
    assert await pool.fetchval("SELECT count(*) FROM tank.ledger WHERE task_type='rig.quote.confirm_payment'") == 0
