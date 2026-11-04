"""One collected test per founder row in docs/acceptance/moot.md.

No model or external HTTP calls. The database fixture refuses non-loopback DSNs.
UI tests execute the real TSX pages; API tests execute the real ASGI routes.
"""
import asyncio
import json
from pathlib import Path
import subprocess
from uuid import UUID, uuid4

import httpx
import pytest

from agents.zenas.core.moot import hearing, hearing_sources
from agents.zenas.core.moot.fairness import evidence, public_evidence
from agents.zenas.core.moot.hearing_ai import AIExecutor
from agents.zenas.core.moot.hearing_store import HearingStore
from agents.zenas.core.moot.published import published_matches
from moot_hearing.test_ai import Store, Tank
from moot_hearing.test_api import application, headers, transported
from moot_hearing.test_fairness import completed, paired
from moot_hearing.test_hearing import HUMANS, OWNER, problem, started, submit
from moot_hearing.test_reasoning import trace
from moot_hearing.test_sources import row
from moot_hearing.test_store import complete_ai, new, transition

ROOT = Path(__file__).resolve().parents[2]
PLATFORM = UUID('00000000-0000-0000-0000-000000000001')


def render(page, result, region='us-east'):
    run = subprocess.run(['node', 'tests/acceptance/moot.cjs'], cwd=ROOT,
        input=json.dumps({'page': page, 'result': result, 'region': region}),
        text=True, capture_output=True, timeout=20, check=True)
    return json.loads(run.stdout)


def client(monkeypatch, store):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=application(monkeypatch, store)), base_url='http://test')


async def command(c, s, action, payload=None, actor=OWNER, request=None):
    path, body = transported(f"moot/hearings/{s['id']}/{action}?home_region=us-east",
        {'request_id': request or str(uuid4()), 'revision': s['revision'], 'payload': payload or {}})
    response = await c.post(path, json=body, headers=headers(actor))
    assert response.status_code == 200, response.text
    return response.json()['hearing']


async def finish_human(store):
    s = await new(store)
    for seat, kind, extra in [('applicant', 'submission', {}), ('judge', 'question', {'target': 'respondent'}),
        ('respondent', 'submission', {}), ('judge', 'ruling', {'order': 'Independent order',
            'reasons': ['Independent reason'], 'authority_ids': ['case-1']})]:
        s = await store.mutate(HUMANS[seat], s['id'], str(uuid4()), s['revision'], 'turn',
            {'kind': kind, 'text': seat + ' words', 'reasoning': trace(), **extra}, hearing.turn)
    return s


def test_m01_public_empty_index():
    result = render('index', {'status': 'empty', 'matches': []})
    assert '<h1>Moot Court</h1>' in result['html']
    assert '<h2>Published matches</h2>' in result['html']
    assert 'No published matches yet.' in result['html']
    assert 'Law students will use it later, for free.' in result['html']
    assert result['captures'] == ['us-east']


def test_m02_public_unavailable_and_missing():
    for state in ('unavailable', 'malformed'):
        for page in ('index', 'watch'):
            html = render(page, {'status': state})['html']
            assert 'role="alert"' in html and 'Room unavailable' in html
            assert 'No published matches yet.' not in html
    assert render('watch', {'status': 'missing'})['status'] == 404


@pytest.mark.asyncio
async def test_m03_civil_source_and_criminal_refusal(monkeypatch, pg_store):
    source = row()
    p = hearing_sources.build_problem(source, OWNER)
    assert p['facts'] == source['fact_pattern']['facts']
    assert p['real_holding'] == source['outcome']
    assert p['authorities'][0]['source_url'] == source['url']
    assert p['authorities'][0]['quote'] in source['text']
    async def selected(*_):
        return hearing_sources.build_problem(source, OWNER)
    monkeypatch.setattr('agents.zenas.core.moot.hearing_api.worked_problem', selected)
    async with client(monkeypatch, pg_store) as c:
        for changes in ({'nature_of_suit': 'criminal prosecution'}, {'holdout': True},
                        {'tenant_id': str(uuid4())}, {'outcome': 'A made-up holding'}):
            source = {**row(), **changes}
            path, body = transported('moot/hearings?home_region=us-east',
                {'request_id': str(uuid4()), 'worked_id': source['id'], 'seats': {seat: {'kind': 'ai'} for seat in HUMANS}})
            response = await c.post(path, json=body, headers=headers())
            assert response.status_code in (404, 422)
            assert 'hearing' not in response.json()


@pytest.mark.asyncio
async def test_m04_create_controller_modes(monkeypatch, pg_store):
    async def source(*_):
        return problem()
    monkeypatch.setattr('agents.zenas.core.moot.hearing_api.worked_problem', source)
    async with client(monkeypatch, pg_store) as c:
        for human_seats in ([], ['applicant'], ['respondent'], ['judge'], list(HUMANS)):
            seats = {s: {'kind': 'human', 'user_id': a.user_id} if s in human_seats else {'kind': 'ai'} for s, a in HUMANS.items()}
            path, body = transported('moot/hearings?home_region=us-east',
                {'request_id': str(uuid4()), 'worked_id': str(uuid4()), 'seats': seats})
            response = await c.post(path, json=body, headers=headers())
            assert response.status_code == 201, response.text
            s = response.json()['hearing']
            assert s['phase'] == 'draft'
            assert {seat for seat, value in s['seats'].items() if value['kind'] == 'human'} == set(human_seats)


@pytest.mark.asyncio
async def test_m05_save_and_lock_problem(monkeypatch, pg_store):
    async def source(*_):
        return problem()
    monkeypatch.setattr(hearing_sources, 'worked_problem', source)
    s = await pg_store.create(OWNER, str(uuid4()), problem(), started()['seats'])
    async with client(monkeypatch, pg_store) as c:
        s = await command(c, s, 'edit', {'changes': {'title': 'Civil appeal acceptance'}})
        assert s['problem']['title'] == 'Civil appeal acceptance'
        s = await command(c, s, 'lock')
        assert s['phase'] == 'locked'
        s = await command(c, s, 'start')
        assert s['phase'] == 'hearing' and s['floor'] == 'applicant'
        res = await c.post(f"/moot/hearings/{s['id']}/edit?home_region=us-east", headers=headers(),
            json={'request_id': str(uuid4()), 'revision': s['revision'], 'payload': {'changes': {'title': 'Changed'}}})
        assert res.status_code == 422 and res.json()['detail'] == 'problem_locked'


@pytest.mark.asyncio
async def test_m06_human_arguments_questions_judgment(monkeypatch, pg_store):
    s = await new(pg_store)
    async with client(monkeypatch, pg_store) as c:
        for seat, kind, extra in [('applicant', 'submission', {}), ('judge', 'question', {'target': 'respondent'}),
            ('respondent', 'submission', {}), ('judge', 'ruling', {'order': 'Independent order',
                'reasons': ['Independent reason'], 'decision': 'uncertain'})]:
            fields = {'kind': kind, 'text': seat + ' words', 'fact': 'facts', 'position': 'sourced',
                'relevance': 'This fact bears on the selected issue', 'authority': 'case-1',
                'application': 'Apply the selected rule to the selected fact', 'difference': '',
                'significance': 'The selected fact is material to applying the rule',
                'opposing': 'The other side disputes this application', 'response': 'Compare both positions against the record',
                'uncertainty': 'The interpretation remains uncertain', 'conclusion': 'No certain conclusion',
                'issue': '0', **extra}
            if 'reasons' in fields:
                fields['reasons'] = '\n'.join(fields['reasons'])
            payload = render('form', {'fields': fields, 'hearing': s, 'seat': seat})
            s = await command(c, s, 'turn', payload, HUMANS[seat])
        assert s['phase'] == 'closed' and s['floor'] == 'judge' and len(s['transcript']) == 4
        assert s['ruling']['order'] == 'Independent order'
        assert s['ruling']['reasons'] == ['Independent reason']
        assert s['cost_usd'] == 0


@pytest.mark.asyncio
async def test_m07_saved_reopen_and_retry(monkeypatch, pg_store):
    s = await new(pg_store)
    async with client(monkeypatch, pg_store) as c:
        payload = {'kind': 'submission', 'text': 'applicant words', 'reasoning': trace(), 'authority_ids': ['case-1']}
        request = str(uuid4())
        first = await command(c, s, 'turn', payload, HUMANS['applicant'], request)
        again = await command(c, s, 'turn', payload, HUMANS['applicant'], request)
        assert first['revision'] == again['revision'] and len(again['transcript']) == 1
        listing = await c.get('/moot/hearings?home_region=us-east', headers=headers())
        assert listing.json()['hearings'][0]['id'] == s['id']
        saved = await HearingStore(pg_store.pool).load(OWNER, s['id'])
        assert saved['transcript'][0]['text'] == 'applicant words'


@pytest.mark.asyncio
async def test_m08_roles_tenant_region_privacy(monkeypatch, pg_store):
    s = await new(pg_store)
    async with client(monkeypatch, pg_store) as c:
        path = f"/moot/hearings/{s['id']}?home_region=us-east"
        own = await c.get(path + '&seat=applicant', headers=headers(HUMANS['applicant']))
        assert own.status_code == 200
        assert 'never counsel' not in own.text and 'real holding hidden' not in own.text
        judge = await c.get(path + '&seat=judge', headers=headers(HUMANS['judge']))
        assert 'never counsel' in judge.text
        assert (await c.get(path + '&seat=judge', headers=headers(HUMANS['applicant']))).status_code == 403
        assert (await c.get(path, headers={**headers(), 'X-Moot-Actor': 'outsider', 'X-Moot-Founder': '0'})).status_code == 404
        assert (await c.get(path.replace('us-east', 'za'), headers=headers())).status_code == 403
        assert (await c.get(path, headers={**headers(), 'X-Moot-Tenant': str(uuid4())})).status_code == 403


@pytest.mark.asyncio
async def test_m09_handover_preserves_context(monkeypatch, pg_store):
    s = await new(pg_store)
    async with client(monkeypatch, pg_store) as c:
        s = await command(c, s, 'turn', {'kind': 'submission', 'text': 'applicant words', 'reasoning': trace()}, HUMANS['applicant'])
        before = [(t['seat'], t['text'], t.get('reasoning')) for t in s['transcript']]
        context = {key: s[key] for key in ('floor', 'current_issue', 'problem', 'pending_question')}
        for seat in HUMANS:
            s = await command(c, s, 'handover', {'seat': seat, 'controller': {'kind': 'ai'}})
            s = await command(c, s, 'handover', {'seat': seat, 'controller': {'kind': 'human', 'user_id': HUMANS[seat].user_id}})
            assert [(t['seat'], t['text'], t.get('reasoning')) for t in s['transcript']] == before
            assert s['seats'][seat]['kind'] == 'human'
            assert {key: s[key] for key in context} == context


@pytest.mark.asyncio
async def test_m10_ai_counsel_and_judge_through_tank():
    s = started({seat: 'ai' for seat in HUMANS})
    store = Store(s)
    class ScriptedTank(Tank):
        async def route(self, tenant, task, messages):
            result = await super().route(tenant, task, messages)
            assert tenant == OWNER.tenant_id
            context = json.loads(messages[1]['content'])
            assert 'never counsel' not in json.dumps(context) and 'real holding hidden' not in json.dumps(context)
            heard = {t['seat'] for t in context['transcript'] if t['kind'] == 'submission'}
            payload = {'text': 'Supported argument', 'authority_ids': ['case-1'], 'reasoning': trace()}
            if task == 'moot.judge':
                payload.update({'kind': 'ruling', 'order': 'Independent order', 'reasons': ['Independent reason'], 'decision': 'uncertain'}
                    if heard == {'applicant', 'respondent'} else {'kind': 'question', 'target': 'respondent'})
            else:
                assert task == 'moot.counsel'
                payload['kind'] = 'submission'
                if context.get('pending_question'):
                    payload['responds_to'] = context['pending_question']['turn']
            return {**result, 'content': json.dumps(payload)}
    tank = ScriptedTank()
    for _ in range(4):
        s = await AIExecutor(store, tank).run(OWNER, s['id'], str(uuid4()), s['revision'])
    assert s['phase'] == 'closed' and s['ruling']['order'] == 'Independent order'
    assert [call[1] for call in tank.calls] == ['moot.counsel', 'moot.judge', 'moot.counsel', 'moot.judge']
    assert len(s['receipts']) == len(s['transcript']) == 4


@pytest.mark.asyncio
async def test_m11_inflight_handover_cancels_stale_ai():
    s = started({seat: 'ai' for seat in HUMANS}); store = Store(s); tank = Tank(wait=True)
    pending = asyncio.create_task(AIExecutor(store, tank, poll_seconds=.001).run(OWNER, s['id'], str(uuid4()), s['revision']))
    await asyncio.wait_for(tank.started.wait(), 5)
    store.state = hearing.change(store.state, OWNER, store.state['revision'], 'handover',
        {'seat': 'applicant', 'controller': {'kind': 'human', 'user_id': HUMANS['applicant'].user_id}})
    result = await asyncio.wait_for(pending, 5)
    assert tank.cancelled and result['transcript'] == []
    assert result['floor'] == 'applicant' and result['pending_ai'] is None
    result = submit(result, 'applicant')
    assert result['transcript'][0]['text'] == 'applicant words'


@pytest.mark.asyncio
async def test_m12_pending_resolution_and_bad_response():
    s = started({seat: 'ai' for seat in HUMANS}); store = Store(s); tank = Tank(wait=True)
    request = str(uuid4())
    pending = asyncio.create_task(AIExecutor(store, tank).run(OWNER, s['id'], request, s['revision']))
    await asyncio.wait_for(tank.started.wait(), 5)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    replacement = Tank()
    result = await AIExecutor(store, replacement).run(OWNER, s['id'], request, s['revision'])
    assert result['pending_ai']['status'] == 'started' and not replacement.calls
    result = hearing.abandon_ai(result, OWNER, result['revision'], result['pending_ai']['token'])
    assert result['pending_ai'] is None and result['transcript'] == []
    bad = await AIExecutor(Store(s), Tank(malformed=True)).run(OWNER, s['id'], str(uuid4()), s['revision'])
    assert bad['transcript'] == [] and bad['ruling'] is None and bad['receipts'][0]['discarded']
    assert bad['receipts'][0]['cost_usd'] == .002


@pytest.mark.asyncio
async def test_m13_swapped_seats_and_fairness(monkeypatch, pg_store):
    s = await pg_store.create(OWNER, str(uuid4()), problem(), {seat: {'kind': 'ai'} for seat in HUMANS})
    async with client(monkeypatch, pg_store) as c:
        s = await command(c, s, 'lock')
        s = await command(c, s, 'start')
        report = (await c.get(f"/moot/hearings/{s['id']}/fairness?home_region=us-east", headers=headers())).json()['fairness']
    assert report['status'] == 'incomplete'
    other = await pg_store.load(OWNER, report['second_hearing'])
    assert other['floor'] == 'respondent' and other['phase'] == 'locked'
    a, b = paired(); a, b = completed(a, 'applicant'), completed(b, 'respondent')
    assert public_evidence(evidence(a, b))['reading'] == 'The same AI seat prevailed in both runs.'
    b['receipts'][0].pop('settings_hash')
    assert public_evidence(evidence(a, b))['reading'] == 'The model settings have not been verified.'


@pytest.mark.asyncio
async def test_m14_reveal_and_pair_publish(pg_store):
    s = await pg_store.create(OWNER, str(uuid4()), problem(), {seat: {'kind': 'ai'} for seat in HUMANS})
    s = await pg_store.mutate(OWNER, s['id'], str(uuid4()), 0, 'lock', {}, transition('lock'))
    s = await pg_store.create_pair(OWNER, s['id'], str(uuid4()), s['revision'], start=True)
    report = await pg_store.fairness(OWNER, s['id'])
    other = await pg_store.load(OWNER, report['second_hearing'])
    s = await complete_ai(pg_store, s, 'applicant')
    assert 'reveal' not in (await pg_store.project(OWNER, s['id']))['hearing']
    with pytest.raises(hearing.HearingError, match='paired_hearing_incomplete'):
        await pg_store.mutate(OWNER, s['id'], str(uuid4()), s['revision'], 'publish', {}, transition('publish'))
    other = await pg_store.create_pair(OWNER, other['id'], str(uuid4()), other['revision'], start=True)
    await complete_ai(pg_store, other, 'respondent')
    assert (await pg_store.project(OWNER, s['id']))['hearing']['reveal'] == 'real holding hidden until judgment'
    await pg_store.mutate(OWNER, s['id'], str(uuid4()), s['revision'], 'publish', {}, transition('publish'))
    rows = await published_matches(pg_store.pool, PLATFORM, 'us-east')
    assert len(rows) == 2 and all(r['fairnessReading'] == 'The same AI seat prevailed in both runs.' for r in rows)


@pytest.mark.asyncio
async def test_m15_publication_reader_and_unverified(pg_store):
    s = await finish_human(pg_store)
    assert not await published_matches(pg_store.pool, PLATFORM, 'us-east')
    await pg_store.mutate(OWNER, s['id'], str(uuid4()), s['revision'], 'publish', {}, transition('publish'))
    rows = await published_matches(pg_store.pool, PLATFORM, 'us-east', UUID(s['id']))
    assert len(rows) == 1 and rows[0]['judgmentOrder'] == 'Independent order'
    assert 'never counsel' not in json.dumps(rows) and 'actor_id' not in json.dumps(rows)
    html = render('watch', {'status': 'found', 'match': rows[0]})['html']
    for text in ('Transcript', 'Judgment', 'Order: Independent order', 'Fairness check',
                 'What the court actually held', 'real holding hidden until judgment', 'Authorities', 'unverified'):
        assert text in html
    assert 'href="https://example.invalid/case"' in html
    assert rows[0]['costUsd'] == 0 and '<h2>Cost</h2>' in html
    index = render('index', {'status': 'ok', 'matches': rows})['html']
    assert 'unverified' in index and f'/moot/{s["id"]}' in index


@pytest.mark.asyncio
async def test_m16_public_region_and_tenant_isolation(pg_store):
    s = await finish_human(pg_store)
    await pg_store.mutate(OWNER, s['id'], str(uuid4()), s['revision'], 'publish', {}, transition('publish'))
    assert not await published_matches(pg_store.pool, PLATFORM, 'za', UUID(s['id']))
    assert not await published_matches(pg_store.pool, uuid4(), 'us-east', UUID(s['id']))
    row_ = (await published_matches(pg_store.pool, PLATFORM, 'us-east'))[0]
    result = render('index', {'status': 'ok', 'matches': [row_]}, 'za')
    assert f'/moot/{s["id"]}?region=za' in result['html'] and result['captures'] == ['za']
    detail = render('watch', {'status': 'found', 'match': row_}, 'za')
    assert detail['captures'] == [[s['id'], 'za']]
    state = (await pg_store.project(OWNER, s['id']))['hearing']
    state['region'] = 'za'
    html = render('panel', {'hearing': state}, 'za')['html']
    assert f'href="/moot/{s["id"]}?region=za"' in html
    state['region'] = 'us-east'
    html = render('panel', {'hearing': state})['html']
    assert f'href="/moot/{s["id"]}"' in html


@pytest.mark.asyncio
async def test_m17_assumptions_and_reasoning_guard(monkeypatch, pg_store):
    from agents.zenas.core.moot.hearing_ai import validate_turn
    async def source(*_):
        return problem()
    monkeypatch.setattr(hearing_sources, 'worked_problem', source)
    s = await pg_store.create(OWNER, str(uuid4()), problem(), started()['seats'])
    async with client(monkeypatch, pg_store) as c:
        s = await command(c, s, 'edit', {'changes': {'fact_records': [
            {'id': 'facts', 'text': s['problem']['facts'], 'status': 'sourced'},
            {'id': 'assumption-1', 'text': 'An additional fact is assumed for this exercise.', 'status': 'assumed'}]}})
        s = await command(c, s, 'lock')
        s = await command(c, s, 'start')
    assert s['problem']['fact_records'][1] == {'id': 'assumption-1',
        'text': 'An additional fact is assumed for this exercise.', 'status': 'assumed'}
    payload = {'kind': 'submission', 'text': 'applicant words', 'reasoning': trace(), 'authority_ids': ['made-up-authority']}
    with pytest.raises(hearing.HearingError):
        hearing.turn(await pg_store.load(OWNER, s['id']), HUMANS['applicant'], s['revision'], validate_turn(s, payload))
    assert not s['transcript']


def test_m18_legacy_reader_empty_transcript_and_unknown_cost():
    row_ = {'matchId': str(uuid4()), 'published': True, 'title': 'Civil appeal', 'fairnessReading': '',
        'judgmentOrder': 'Independent order', 'reveal': 'Stored court holding', 'costUsd': None}
    html = render('watch', {'status': 'found', 'match': row_})['html']
    assert 'No transcript.' in html and 'Not an AI-against-AI match.' in html
    assert '<h2>Cost</h2>' not in html
    row_['costUsd'] = .008
    html = render('watch', {'status': 'found', 'match': row_})['html']
    assert '<h2>Cost</h2><p>0.008</p>' in html
