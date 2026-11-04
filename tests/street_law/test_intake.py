from unittest.mock import AsyncMock

import pytest
from agents.zenas.core.street_law import Actor, Refused, StreetLaw, triage
from agents.zenas.core.service import _LEGAL_NOTICE


@pytest.mark.asyncio
async def test_question_to_exact_sourced_draft_never_releases_even_with_review_off(clinic, monkeypatch):
    monkeypatch.setenv('ZENAS_REVIEW', 'off')
    case = await clinic.service.submit(clinic.actor, 'za', clinic.session, 'When must my landlord return the deposit?')
    result = await clinic.service.answer(clinic.actor, 'za', clinic.session, case['id'])
    assert result['status'] == 'answered' and result['released'] is False
    assert clinic.chunk.text in result['answer']['text'] and _LEGAL_NOTICE in result['answer']['text']
    assert result['answer']['sources'][0]['citation'] == clinic.chunk.citation
    assert result['answer']['sources'][0]['version_id'] == clinic.chunk.version_id
    assert clinic.matters.documents[0]['state'] == 'in_review'
    clinic.law.search_text.assert_awaited_once_with('rental deposit refund', jurisdiction='za', limit=3)


@pytest.mark.parametrize('question', ['Help me defend criminal charges.', 'How do I apply for bail?',
                                     'I was assaulted. How do I prosecute him?', 'Can I appeal my conviction?'])
def test_criminal_refusal_cannot_be_bypassed_by_claim_flag(question):
    with pytest.raises(Refused):
        triage(question, True)


def test_only_victims_own_explicit_civil_claim_is_accepted_for_handover():
    assert triage('I was assaulted. Can I claim compensation for medical bills?', True) == ('handed_over', '')
    with pytest.raises(Refused):
        triage('My friend was assaulted. Can he claim compensation?', True)


@pytest.mark.asyncio
async def test_criminal_and_foreign_region_never_read_database():
    class NoReads:
        def __getattr__(self, name):
            pytest.fail('refusal touched storage')
    clinic = StreetLaw(NoReads(), NoReads(), NoReads(), box_region='us-east')
    actor = Actor('11111111-1111-4111-8111-111111111111', 'placeholder')
    with pytest.raises(Refused):
        await clinic.submit(actor, 'us-east', 'placeholder', 'How do I apply for bail?')
    with pytest.raises(Refused):
        await clinic.submit(actor, 'za', 'placeholder', 'Return my deposit?')


@pytest.mark.asyncio
async def test_complex_and_victim_claims_go_to_volunteers_without_library_or_provider(clinic):
    for question, victim in [('My landlord sent an eviction summons.', False),
                             ('I was assaulted. Can I claim compensation?', True)]:
        result = await clinic.service.submit(clinic.actor, 'za', clinic.session, question, victim_civil_claim=victim)
        assert result['status'] == 'handed_over'
    clinic.law.search_text.assert_not_called()


@pytest.mark.asyncio
async def test_missing_or_unusable_library_hands_over_without_inventing_answer(clinic):
    clinic.law.search_text = AsyncMock(side_effect=TimeoutError('placeholder-private-body'))
    case = await clinic.service.submit(clinic.actor, 'za', clinic.session, 'What happens to my rental deposit?')
    result = await clinic.service.answer(clinic.actor, 'za', clinic.session, case['id'])
    assert result == {'id': case['id'], 'status': 'handed_over'}
    assert not clinic.matters.documents


@pytest.mark.asyncio
async def test_consent_and_volunteer_access_are_required(clinic):
    stranger = Actor(clinic.actor.tenant_id, 'placeholder-other')
    with pytest.raises(Refused):
        await clinic.service.submit(stranger, 'za', clinic.session, 'My rental deposit?')
    case = await clinic.service.submit(clinic.actor, 'za', clinic.session, 'My rental deposit?')
    with pytest.raises(Refused):
        await clinic.service.answer(stranger, 'za', clinic.session, case['id'])
    clinic.law.search_text.assert_not_called()


@pytest.mark.asyncio
async def test_client_identifiers_are_redacted_and_never_sent_to_library(clinic):
    question = 'My landlord holds my deposit. My phone is 0821234567.'
    case = await clinic.service.submit(clinic.actor, 'za', clinic.session, question)
    stored = clinic.sessions.rows[clinic.actor.tenant_id, clinic.session]['cases'][0]
    assert '0821234567' not in stored['question']
    await clinic.service.answer(clinic.actor, 'za', clinic.session, case['id'])
    assert clinic.law.search_text.call_args.args == ('rental deposit refund',)


@pytest.mark.asyncio
async def test_foreign_tenant_cannot_read_session(clinic):
    other = Actor('22222222-2222-4222-8222-222222222222', 'placeholder-other', True)
    await clinic.matters.register_tenant(other.tenant_id, 'za')
    with pytest.raises(Refused):
        await clinic.service.answer(other, 'za', clinic.session, 'placeholder-case')
