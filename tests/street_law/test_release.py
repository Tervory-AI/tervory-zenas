import asyncio

import httpx
import pytest
from fastapi import FastAPI

from agents.zenas.core.street_law import Actor, Refused
from agents.zenas.core.matters import CURRENT_WORDING_VERSION
from agents.zenas.core import street_law_api as api


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['on', 'off'])
async def test_offline_session_question_sources_human_release_and_client_read(clinic, monkeypatch, mode):
    monkeypatch.setenv('ZENAS_REVIEW', mode)
    monkeypatch.setenv('TANK_KEY', 'placeholder-room')
    monkeypatch.setattr(api, 'PgSessions', lambda pool: clinic.sessions)
    client_actor = Actor(clinic.actor.tenant_id, 'placeholder-client')
    await clinic.matters.add_consent({'tenant_id': client_actor.tenant_id, 'user_id': client_actor.user_id,
                                    'wording_version': CURRENT_WORDING_VERSION})
    app = FastAPI()
    app.state.pool, app.state.law_pool = object(), clinic.law
    api.register_routes(app, matters_factory=lambda: clinic.matters, box_region=lambda: 'za')
    base = {'tenant_id': client_actor.tenant_id, 'home_region': 'za'}
    headers = {'X-Tank-Key': 'placeholder-room', 'X-Street-Law-Tenant': client_actor.tenant_id,
               'X-Street-Law-Actor': client_actor.user_id}
    reviewer = {**headers, 'X-Street-Law-Actor': clinic.actor.user_id, 'X-Street-Law-Founder': '1'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://placeholder') as client:
        path = f'/street-law/sessions/{clinic.session}/questions'
        response = await client.post(path, headers=headers, json={**base, 'question': 'When is my rental deposit refunded?'})
        assert response.status_code == 201
        path += '/' + response.json()['id']
        draft = await client.post(path + '/answer', headers=reviewer, json=base)
        assert draft.status_code == 200 and draft.json()['answer']['sources']
        hidden = await client.get(path, headers=headers, params=base)
        assert hidden.json()['answer'] is None and not hidden.json()['released']
        denied = await client.post(path + '/release', headers=headers, json={**base, 'confirmed': True})
        assert denied.status_code == 403 and not clinic.matters.reviews
        unconfirmed = await client.post(path + '/release', headers=reviewer, json=base)
        assert unconfirmed.status_code == 403 and not clinic.matters.reviews
        released = await client.post(path + '/release', headers=reviewer, json={**base, 'confirmed': True})
        assert released.status_code == 200 and released.json()['released'] and released.json()['reviewed']
        seen = await client.get(path, headers=headers, params=base)
        assert seen.json()['released'] and seen.json()['answer'] == draft.json()['answer']
        assert len(clinic.matters.reviews) == 1
        assert clinic.matters.reviews[0]['reviewer'] == clinic.actor.user_id
        assert clinic.matters.documents[0]['state'] == 'released' and clinic.matters.documents[0]['reviewed']


@pytest.mark.asyncio
async def test_unqualified_volunteer_cannot_release_and_body_cannot_supply_identity(clinic):
    await clinic.service.volunteers(clinic.actor, 'za', clinic.session, 'placeholder-helper')
    helper = Actor(clinic.actor.tenant_id, 'placeholder-helper')
    case = await clinic.service.submit(clinic.actor, 'za', clinic.session, 'My rental deposit?')
    await clinic.service.answer(helper, 'za', clinic.session, case['id'])
    with pytest.raises(Refused):
        await clinic.service.release(helper, 'za', clinic.session, case['id'], confirmed=True)
    assert not clinic.matters.reviews and clinic.matters.documents[0]['state'] == 'in_review'


@pytest.mark.asyncio
async def test_concurrent_release_is_one_audited_review(clinic):
    case = await clinic.service.submit(clinic.actor, 'za', clinic.session, 'My rental deposit?')
    await clinic.service.answer(clinic.actor, 'za', clinic.session, case['id'])
    results = await asyncio.gather(*[clinic.service.release(clinic.actor, 'za', clinic.session,
                               case['id'], confirmed=True) for _ in range(2)])
    assert all(row['released'] for row in results) and len(clinic.matters.reviews) == 1


@pytest.mark.asyncio
async def test_changed_document_or_failed_write_never_releases(clinic, monkeypatch):
    case = await clinic.service.submit(clinic.actor, 'za', clinic.session, 'My rental deposit?')
    await clinic.service.answer(clinic.actor, 'za', clinic.session, case['id'])
    original = clinic.matters.documents[0]['body']
    clinic.matters.documents[0]['body'] = 'placeholder changed after review started'
    with pytest.raises(Refused):
        await clinic.service.release(clinic.actor, 'za', clinic.session, case['id'], confirmed=True)
    clinic.matters.documents[0]['body'] = original
    async def failed(*args):
        raise RuntimeError('placeholder database failure')
    monkeypatch.setattr(clinic.matters, 'set_document_state', failed)
    with pytest.raises(RuntimeError):
        await clinic.service.release(clinic.actor, 'za', clinic.session, case['id'], confirmed=True)
    assert not clinic.matters.reviews
    row = clinic.sessions.rows[clinic.actor.tenant_id, clinic.session]['cases'][0]
    assert row['review_id'] is None and clinic.matters.documents[0]['state'] == 'in_review'


@pytest.mark.asyncio
async def test_registered_reviewer_can_release_without_founder_privilege(clinic):
    await clinic.service.volunteers(clinic.actor, 'za', clinic.session,
                                    'placeholder-lawyer', 'placeholder-ZA-admission')
    reviewer = Actor(clinic.actor.tenant_id, 'placeholder-lawyer')
    case = await clinic.service.submit(clinic.actor, 'za', clinic.session, 'My rental deposit?')
    await clinic.service.answer(reviewer, 'za', clinic.session, case['id'])
    released = await clinic.service.release(reviewer, 'za', clinic.session, case['id'], confirmed=True)
    assert released['released']
    assert clinic.matters.reviews[0]['reviewer_kind'] == 'reviewer'
    assert clinic.matters.reviews[0]['admission_no'] == 'placeholder-ZA-admission'


@pytest.mark.asyncio
async def test_foreign_question_hands_over_without_za_answer(clinic):
    result = await clinic.service.submit(clinic.actor, 'za', clinic.session,
                                        'My landlord in New York keeps my deposit?')
    assert result['status'] == 'handed_over'
    clinic.law.search_text.assert_not_called()
