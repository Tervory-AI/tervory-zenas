from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from agents.zenas.core.street_law import Actor, Refused
from agents.zenas.core.matters import CURRENT_WORDING_VERSION
from agents.zenas import street_law_api as api


@pytest.mark.asyncio
async def test_session_groups_and_volunteer_assignment_are_tenant_private(clinic):
    actor = clinic.actor
    await clinic.service.volunteers(actor, 'za', clinic.session, 'placeholder-volunteer')
    volunteer = Actor(actor.tenant_id, 'placeholder-volunteer')
    queued = await clinic.service.submit(actor, 'za', clinic.session, 'My landlord keeps the deposit?')
    handed = await clinic.service.submit(actor, 'za', clinic.session, 'I have an eviction summons.')
    assigned = await clinic.service.assign(volunteer, 'za', clinic.session, queued['id'], volunteer.user_id)
    assert assigned['assigned_to'] == volunteer.user_id
    await clinic.service.answer(volunteer, 'za', clinic.session, queued['id'])
    view = await clinic.service.queue(volunteer, 'za', clinic.session)
    assert view['queued'] == [] and view['answered'][0]['id'] == queued['id']
    assert view['handed_over'][0]['id'] == handed['id']
    with pytest.raises(Refused):
        await clinic.service.volunteers(volunteer, 'za', clinic.session, 'placeholder-intruder', 'placeholder-admission')
    stranger = Actor(actor.tenant_id, 'placeholder-stranger')
    with pytest.raises(Refused):
        await clinic.service.queue(stranger, 'za', clinic.session)


@pytest.mark.asyncio
async def test_client_cannot_read_draft_or_other_clients_question(clinic):
    client = Actor(clinic.actor.tenant_id, 'placeholder-client')
    await clinic.matters.add_consent({'tenant_id': client.tenant_id, 'user_id': client.user_id,
                                    'wording_version': CURRENT_WORDING_VERSION})
    case = await clinic.service.submit(client, 'za', clinic.session, 'What about a rental deposit?')
    await clinic.service.answer(clinic.actor, 'za', clinic.session, case['id'])
    view = await clinic.service.read(client, 'za', clinic.session, case['id'])
    assert view['answer'] is None and not view['released']
    with pytest.raises(Refused):
        await clinic.service.read(Actor(client.tenant_id, 'placeholder-other-client'), 'za', clinic.session, case['id'])


@pytest.mark.asyncio
async def test_handover_rejects_draft_and_withholds_it_from_clients(clinic):
    case = await clinic.service.submit(clinic.actor, 'za', clinic.session, 'My landlord keeps my deposit?')
    await clinic.service.answer(clinic.actor, 'za', clinic.session, case['id'])
    result = await clinic.service.handover(clinic.actor, 'za', clinic.session, case['id'])
    assert result['status'] == 'handed_over' and result['answer'] is None
    assert clinic.matters.documents[0]['state'] == 'rejected'


@pytest.mark.asyncio
async def test_actual_room_api_requires_attested_actor_tenant_and_region(clinic, monkeypatch):
    monkeypatch.setenv('TANK_KEY', 'placeholder-room')
    monkeypatch.setattr(api, 'PgSessions', lambda pool: clinic.sessions)
    app = FastAPI()
    app.state.pool = object()
    app.state.law_pool = clinic.law
    api.register_routes(app, matters_factory=lambda: clinic.matters, box_region=lambda: 'za')
    headers = {'X-Tank-Key': 'placeholder-room', 'X-Street-Law-Actor': clinic.actor.user_id,
               'X-Street-Law-Tenant': clinic.actor.tenant_id, 'X-Street-Law-Founder': '1'}
    base = {'tenant_id': clinic.actor.tenant_id, 'home_region': 'za'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://placeholder') as client:
        path = f'/street-law/sessions/{clinic.session}/questions'
        denied = await client.post(path, json={**base, 'question': 'My rental deposit?'})
        assert denied.status_code == 404
        foreign = await client.post(path, headers=headers, json={**base, 'home_region': 'us-east', 'question': 'My rental deposit?'})
        assert foreign.status_code == 403
        created = await client.post(path, headers=headers, json={**base, 'question': 'My rental deposit?'})
        assert created.status_code == 201
        answered = await client.post(path + '/' + created.json()['id'] + '/answer', headers=headers, json=base)
        assert answered.status_code == 200 and answered.json()['answer']['sources']
        queue = await client.get(f'/street-law/sessions/{clinic.session}', headers=headers, params=base)
        assert queue.status_code == 200 and len(queue.json()['answered']) == 1


@pytest.mark.asyncio
async def test_tank_derives_founder_and_forwards_only_to_local_za_room(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://placeholder@127.0.0.1/placeholder')
    monkeypatch.setenv('TANK_API_KEY', 'placeholder-workspace')
    monkeypatch.setenv('LITELLM_MASTER_KEY', 'placeholder-model-gateway')
    import main
    import admin
    from starlette.requests import Request
    monkeypatch.setenv('TANK_REGION', 'za')
    monkeypatch.setenv('TANK_API_KEY', 'placeholder-workspace')
    monkeypatch.setenv('TANK_KEY_ZENAS', 'placeholder-room')
    monkeypatch.setattr(admin, 'founder_allowed', lambda *args: False)
    outgoing = AsyncMock(return_value=httpx.Response(200, json={'ok': True}))
    monkeypatch.setattr(main, '_http', SimpleNamespace(request=outgoing))
    async def receive():
        return {'type': 'http.request', 'body': b'{"home_region":"za"}', 'more_body': False}
    headers = [(b'x-tank-key', b'placeholder-workspace'), (b'x-street-law-actor', b'placeholder-volunteer'),
               (b'x-street-law-tenant', b'11111111-1111-4111-8111-111111111111'),
               (b'x-street-law-founder', b'1')]
    request = Request({'type': 'http', 'method': 'POST', 'path': '/v1/zenas/street-law/sessions',
                       'query_string': b'', 'headers': headers}, receive)
    result = await main.zenas_proxy('street-law/sessions', request)
    assert result.status_code == 200
    assert outgoing.call_args.args[1] == 'http://agent-zenas:8000/street-law/sessions'
    assert outgoing.call_args.kwargs['headers']['X-Street-Law-Founder'] == '0'
    assert outgoing.call_args.kwargs['headers']['X-Tank-Key'] == 'placeholder-room'
    outgoing.reset_mock()
    monkeypatch.setenv('TANK_REGION', 'us-east')
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        await main.zenas_proxy('street-law/sessions', request)
    outgoing.assert_not_called()
