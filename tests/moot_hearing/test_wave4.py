"""Recorded SCA appeal through the real store, API, Tank client and public reader."""
import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from chassis.tank import TankClient
from agents.zenas.core.moot import hearing
from agents.zenas.core.moot.hearing_api import register_routes
from agents.zenas.core.moot.hearing_sources import worked_problem
from agents.zenas.core.moot.published import published_matches
from .test_hearing import TENANT

ROOT = Path(__file__).resolve().parents[2]
OWNER = hearing.Actor(TENANT, 'founder', 'za', True)
PLATFORM = UUID('00000000-0000-0000-0000-000000000001')
URL = 'https://www.supremecourtofappeal.org.za/index.php/component/jdownloads/summary/23-judgments-2010/1452-kuzwayo-v-estate-late-masilela-28-10-2010-zasca-167-1-december-2010'


async def install(pool):
    source = (ROOT / 'tests/fixtures/za/precedent/kuzwayo-zasca-167.txt').read_text()
    facts = ' '.join(source[source.index('[3]'):source.index('[6]')].split())
    rule = ' '.join(source[source.index('[27]'):source.index('Administrative Action: Review')].split())
    holding = 'The appeal is dismissed with costs, including those of two counsel.'
    identity = uuid4()
    async with pool.acquire() as conn:
        await conn.execute((ROOT / 'agents/zenas/pool/schema.sql').read_text())
        schema = (ROOT / 'agents/zenas/schema.sql').read_text()
        start = schema.index('CREATE TABLE IF NOT EXISTS agent_zenas.worked_matters')
        await conn.execute(schema[start:schema.index('CREATE UNIQUE INDEX IF NOT EXISTS worked_case_once_idx', start)])
        await conn.execute('ALTER TABLE agent_zenas.worked_matters ADD COLUMN IF NOT EXISTS holdout boolean NOT NULL DEFAULT false')
        await conn.execute("INSERT INTO zenas_pool.sources(source_id,name,region,jurisdiction,licence,terms,bulk_use,store_full_text) VALUES('sca','Supreme Court of Appeal','za','za','recorded court source','recorded terms','allowed',true)")
        await conn.execute("""INSERT INTO zenas_pool.documents(version_id,document_id,source_id,jurisdiction,level,body,kind,citation,citation_key,title,status,url,licence,text,content_hash,court,appeal)
            VALUES('kuzwayo-v1','kuzwayo','sca','za','national','SCA','case','Kuzwayo v Estate late Masilela [2010] ZASCA 167','kuzwayo-167','Kuzwayo v Estate late Masilela','current',$1,'recorded source',$2,'recorded-source-hash','Supreme Court of Appeal',true)""", URL, source)
        await conn.execute("""INSERT INTO agent_zenas.worked_matters(id,region,source,jurisdiction,material_type,pool_doc_id,pool_version_id,fact_pattern,outcome,element_findings)
            VALUES($1,'za','case','za','public_record','kuzwayo','kuzwayo-v1',$2::jsonb,$3,$4::jsonb)""", identity,
            json.dumps({'facts': facts, 'issues': ['Did the permit history justify cancellation and a statutory inquiry?']}), holding,
            json.dumps([{'basis': [{'citation': 'Kuzwayo v Estate late Masilela [2010] ZASCA 167', 'quote': rule}]}]))
    return identity, holding


def answer(context):
    authority = context['problem']['authorities'][0]['id']
    floor = context['floor']
    trace = {'facts': [{'id': 'facts', 'position': 'sourced', 'relevance': 'The permit history bears on entitlement.'}],
        'rules': [{'authority_id': authority, 'court_weight': 'unknown', 'application': 'Consider the statutory inquiry against the permit history.'}],
        'comparisons': [{'authority_id': authority, 'similarities': ['facts'], 'differences': [], 'legal_significance': 'The passage addresses entitlement to request an inquiry.'}],
        'opposing_argument': 'The other side challenges entitlement to cancellation.', 'response': 'Test that challenge against the supplied history.',
        'uncertainty': ['The limited bundle omits some evidentiary disputes.'], 'conclusion': 'The inquiry requirement needs independent consideration.'}
    payload = {'kind': 'submission', 'text': 'The applicant challenges cancellation.' if floor == 'applicant' else 'The respondent seeks an inquiry.', 'authority_ids': [authority], 'reasoning': trace}
    if floor == 'judge':
        heard = {t['seat'] for t in context['transcript'] if t['kind'] == 'submission'}
        if heard == {'applicant', 'respondent'}:
            payload.update(kind='ruling', text='Both submissions have been considered.', order='The limited record leaves entitlement unresolved.', reasons=['Assess the permit history against the inquiry requirement.'], decision='uncertain')
        else:
            payload.update(kind='question', text='Address the statutory inquiry requirement.', target=next(side for side in ('applicant', 'respondent') if side not in heard))
    if context['pending_question'] and floor != 'judge':
        payload['responds_to'] = context['pending_question']['turn']
    return payload


@pytest.mark.asyncio
async def test_real_stored_appeal_all_ai_saved_and_published_unverified(monkeypatch, pg_store):
    worked, holding = await install(pg_store.pool)
    calls = []
    async def transport(request):
        assert request.url.path == '/v1/route'
        body = json.loads(request.content)
        assert body['tenant_id'] == TENANT and body['agent'] == 'zenas' and 'model' not in body
        context = json.loads(body['messages'][1]['content'])
        assert holding not in json.dumps(context) and 'real_holding' not in json.dumps(context) and 'bench_notes' not in json.dumps(context)
        calls.append(body)
        ledger, model = str(uuid4()), 'offline-script-not-a-quality-eval'
        return httpx.Response(200, json={'content': json.dumps(answer(context)), 'ledger_id': ledger, 'model': model,
            'cost_usd': None, 'settings_hash': None, 'execution': {'version': 1, 'tenant_id': TENANT, 'task_type': body['task_type'], 'ledger_id': ledger, 'model': model}})
    class Tenants:
        async def get_tenant(self, tenant):
            return {'tenant_id': tenant, 'home_region': 'za'} if tenant == TENANT else None
    monkeypatch.setenv('TANK_KEY', 'disposable-room-key')
    headers = {'X-Tank-Key': 'disposable-room-key', 'X-Moot-Tenant': TENANT, 'X-Moot-Actor': 'founder', 'X-Moot-Founder': '1'}
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        tank = TankClient('http://tank', 'disposable-agent-key', 'zenas', http)
        app = FastAPI()
        register_routes(app, lambda: pg_store.pool, Tenants, lambda: 'za', lambda: tank)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://room', headers=headers) as client:
            sources = await client.get('/moot/hearing-sources?home_region=za')
            assert sources.json()['sources'] == [{'id': str(worked), 'title': 'Kuzwayo v Estate late Masilela'}]
            result = await client.post('/moot/hearings?home_region=za', json={'worked_id': str(worked), 'request_id': str(uuid4()), 'seats': {s: {'kind': 'ai'} for s in ('applicant', 'respondent', 'judge')}})
            assert result.status_code == 201, result.text
            state = await pg_store.load(OWNER, result.json()['hearing']['id'])
            async def act(state, action):
                response = await client.post(f'/moot/hearings/{state["id"]}/{action}?home_region=za', json={'revision': state['revision'], 'request_id': str(uuid4()), 'payload': {}})
                assert response.status_code == 200, response.text
                return await pg_store.load(OWNER, state['id'])
            state = await act(state, 'lock')
            state = await act(state, 'start')
            pair = await pg_store.fairness(OWNER, state['id'])
            other = await pg_store.load(OWNER, pair['second_hearing'])
            for current in (state, other):
                if current['phase'] != 'hearing': current = await act(current, 'start')
                for _ in range(8):
                    if current['phase'] == 'closed': break
                    current = await act(current, 'ai')
                assert current['phase'] == 'closed'
                assert {t['seat'] for t in current['transcript']} == {'applicant', 'respondent', 'judge'}
                assert len(current['receipts']) == len(current['transcript']) == 4
            assert await published_matches(pg_store.pool, PLATFORM, 'za') == []
            await act(await pg_store.load(OWNER, state['id']), 'publish')
            rows = await published_matches(pg_store.pool, PLATFORM, 'za')
            assert len(rows) == 2 and len(calls) == 8
            assert {c['task_type'] for c in calls} == {'moot.counsel', 'moot.judge'}
            assert all(r['verificationStatus'] == 'unverified' and r['reveal'] == holding and r['costUsd'] is None for r in rows)
            assert await published_matches(pg_store.pool, PLATFORM, 'us-east') == []
            async with pg_store.pool.acquire() as conn:
                await conn.execute("UPDATE zenas_pool.documents SET title='S v Test',nature_of_suit='criminal appeal' WHERE document_id='kuzwayo'")
            with pytest.raises(hearing.HearingError, match='civil_only'):
                await worked_problem(pg_store.pool, OWNER, str(worked))
            assert len(calls) == 8


def test_public_reader_never_promotes_a_stored_verification_claim():
    import subprocess
    result = subprocess.run(['node', 'tests/moot_hearing/public_label.cjs'], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
