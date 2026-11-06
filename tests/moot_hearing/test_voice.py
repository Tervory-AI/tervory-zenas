"""Local voice turns retain the same authorization, citations and durable receipts."""
import asyncio
from copy import deepcopy
import json
from uuid import UUID,uuid4

import httpx
import pytest

from agents.zenas.core.moot import hearing
from agents.zenas.core.moot.hearing_api import register_routes
from agents.zenas.core.moot.hearing_ai import AIExecutor,validate_turn
from agents.zenas.core.moot.hearing_store import HearingStore
from agents.zenas.core.moot.voice import capture
from agents.zenas.core.moot.feedback import screen
from .test_hearing import OWNER,HUMANS,problem
from .test_reasoning import trace
from .test_store import new,transition,complete_ai
from .test_api import application,headers


class ScriptedJudge:
    def __init__(self): self.calls=[]
    async def route(self,tenant,task,messages):
        context=json.loads(messages[1]['content']);self.calls.append(context)
        assert task=='moot.judge' and tenant==OWNER.tenant_id
        assert 'voice' not in json.dumps(context) and 'never counsel' not in json.dumps(context)
        heard={t['seat'] for t in context['transcript'] if t['kind']=='submission'}
        payload={'text':'Address the selected issue','reasoning':trace(),'authority_ids':['case-1']}
        if len(heard)==1:payload.update(kind='question',target='respondent')
        else:payload.update(kind='ruling',order='Independent order',reasons=['Independent reason'],decision='uncertain')
        return {'content':json.dumps(payload),'ledger_id':str(uuid4()),'model':'offline-judge-double','cost_usd':.001}



def voice(text='Spoken civil argument'):
    return {'engine':'web_speech_local','language':'en-US','segments':[{'text':text,'start_ms':0,'end_ms':1500}]}


@pytest.mark.parametrize('change',[
    {'engine':'remote'}, {'language':'unknown'}, {'segments':[]},
    {'segments':[{'text':'different','start_ms':0,'end_ms':1}]},
    {'segments':[{'text':'Spoken civil argument','start_ms':True,'end_ms':1}]},
    {'segments':[{'text':'Spoken civil argument','start_ms':0,'end_ms':float('inf')}]},
    {'segments':[{'text':'Spoken civil argument','start_ms':0,'end_ms':10**400}]},
    {'segments':[{'text':'Spoken civil argument','start_ms':2,'end_ms':1}]},
    {'segments':[{'text':'Spoken civil','start_ms':0,'end_ms':2},{'text':'argument','start_ms':1,'end_ms':3}]},
])
def test_invalid_or_remote_voice_evidence_is_refused(change):
    data=voice();data.update(change)
    with pytest.raises(hearing.HearingError,match='invalid_voice'):
        capture(data,'Spoken civil argument')


@pytest.mark.asyncio
async def test_spoken_argument_uses_real_api_and_survives_retry_restart_and_handover(monkeypatch,pg_store):
    state=await new(pg_store)
    app=application(monkeypatch,pg_store)
    request=str(uuid4())
    body={'request_id':request,'revision':state['revision'],'payload':{'kind':'submission','text':'Spoken civil argument',
        'reasoning':trace(),'authority_ids':['case-1'],'voice':voice()}}
    path=f'/moot/hearings/{state["id"]}/turn?home_region=us-east'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://placeholder') as client:
        first=await client.post(path,json=body,headers=headers(HUMANS['applicant']))
        assert first.status_code==200,first.text
        retry=await client.post(path,json=body,headers=headers(HUMANS['applicant']))
        assert retry.json()==first.json()
        wrong=await client.post(path,json=body,headers=headers(HUMANS['respondent']))
        assert wrong.status_code in {403,409}
        fresh=await HearingStore(pg_store.pool).load(HUMANS['applicant'],state['id'])
        assert len(fresh['transcript'])==1 and fresh['transcript'][0]['voice']==voice()
        changed=await pg_store.mutate(OWNER,state['id'],str(uuid4()),fresh['revision'],'handover',
            {'seat':'applicant','controller':{'kind':'ai'}},transition('handover'))
        assert changed['transcript'][0]['voice']==voice()
    async with pg_store.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM agent_zenas.moot_hearing_events WHERE hearing_id=$1 AND kind='turn'",UUID(state['id']))==1


@pytest.mark.asyncio
async def test_student_voice_and_ai_judge_save_transcript_judgment_and_feedback(pg_store):
    seats={s:{'kind':'human','user_id':HUMANS[s].user_id} if s!='judge' else {'kind':'ai'} for s in hearing.SEATS}
    state=await pg_store.create(OWNER,str(uuid4()),problem(),seats)
    state=await pg_store.mutate(OWNER,state['id'],str(uuid4()),state['revision'],'lock',{},transition('lock'))
    state=await pg_store.create_pair(OWNER,state['id'],str(uuid4()),state['revision'],start=True)
    judge=ScriptedJudge()
    for seat in ('applicant','respondent'):
        payload={'kind':'submission','text':'Spoken civil argument','voice':voice(),'reasoning':trace(),'authority_ids':['case-1']}
        if state['pending_question']:payload['responds_to']=state['pending_question']['turn']
        state=await pg_store.mutate(HUMANS[seat],state['id'],str(uuid4()),state['revision'],'turn',validate_turn(state,payload),hearing.turn)
        state=await AIExecutor(pg_store,judge).run(OWNER,state['id'],str(uuid4()),state['revision'])
    assert state['phase']=='closed' and len(judge.calls)==2
    saved=await HearingStore(pg_store.pool).load(HUMANS['applicant'],state['id'])
    assert len(saved['transcript'])==4 and saved['ruling']['order']=='Independent order'
    assert saved['feedback']['fairness']['status']=='not_applicable'
    assert saved['feedback']['counsel'][0]['speech_ms']==1500
    assert saved['feedback']['counsel'][1]['questions']==[1]
    assert saved['feedback']['counsel'][1]['arguments'][0]['responds_to']==1
    result=await screen(pg_store,HUMANS['applicant'],state['id'],'feedback')
    assert result['feedback']['court_holding']==problem()['real_holding']
    assert 'never counsel' not in json.dumps(result)


@pytest.mark.asyncio
async def test_pair_fairness_is_saved_at_judgment_without_a_client_read(pg_store):
    state=await pg_store.create(OWNER,str(uuid4()),problem(),{seat:{'kind':'ai'} for seat in hearing.SEATS})
    state=await pg_store.mutate(OWNER,state['id'],str(uuid4()),0,'lock',{},transition('lock'))
    first=await pg_store.create_pair(OWNER,state['id'],str(uuid4()),state['revision'],start=True)
    pair=await pg_store.fairness(OWNER,first['id'])
    second=await pg_store.load(OWNER,pair['second_hearing'])
    second=await pg_store.create_pair(OWNER,second['id'],str(uuid4()),second['revision'],start=True)
    first=await complete_ai(pg_store,first,'applicant')
    projected=await screen(pg_store,OWNER,first['id'],'feedback')
    assert projected['feedback'] is None and 'reveal' not in projected
    assert problem()['real_holding'] not in json.dumps(projected)
    second=await complete_ai(pg_store,second,'respondent')
    async with pg_store.pool.acquire() as conn:
        report=await conn.fetchval('SELECT report FROM agent_zenas.moot_hearing_pairs WHERE id=$1',UUID(first['fairness_pair_id']))
    report=json.loads(report) if isinstance(report,str) else report
    assert report['status']=='measured' and report['same_seat_wins_both'] is True
    feedback=await screen(pg_store,OWNER,first['id'],'feedback')
    assert feedback['feedback']['fairness']['settings_verified'] is True
    other=hearing.Actor(str(uuid4()),OWNER.user_id,OWNER.region,True)
    with pytest.raises(hearing.HearingError,match='not_found'):await screen(pg_store,other,first['id'],'bench')


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['bench','citations','feedback'])
async def test_studio_views_require_identity_and_use_positive_private_projection(monkeypatch,pg_store,kind):
    state=await new(pg_store);app=application(monkeypatch,pg_store)
    url=f'/moot/hearings/{state["id"]}?home_region=us-east&screen={kind}'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://placeholder') as client:
        assert (await client.get(url)).status_code==401
        result=await client.get(url,headers=headers(HUMANS['applicant']))
        assert result.status_code==200
        assert result.json()['screen']['kind']==kind
        for private in ['never counsel','real holding hidden','actor_id','owner_id','bench_notes']:
            assert private not in result.text


@pytest.mark.asyncio
async def test_simultaneous_paired_judgments_save_the_final_fairness_once(pg_store):
    state=await pg_store.create(OWNER,str(uuid4()),problem(),{seat:{'kind':'ai'} for seat in hearing.SEATS})
    state=await pg_store.mutate(OWNER,state['id'],str(uuid4()),0,'lock',{},transition('lock'))
    first=await pg_store.create_pair(OWNER,state['id'],str(uuid4()),state['revision'],start=True)
    pair=await pg_store.fairness(OWNER,first['id'])
    second=await pg_store.load(OWNER,pair['second_hearing'])
    second=await pg_store.create_pair(OWNER,second['id'],str(uuid4()),second['revision'],start=True)
    first,second=await asyncio.gather(complete_ai(pg_store,first,'applicant',stop_before_ruling=True),
                                      complete_ai(pg_store,second,'respondent',stop_before_ruling=True))
    first,second=await asyncio.wait_for(asyncio.gather(complete_ai(pg_store,first,'applicant'),complete_ai(pg_store,second,'respondent')),10)
    async with pg_store.pool.acquire() as conn:
        report=await conn.fetchval('SELECT report FROM agent_zenas.moot_hearing_pairs WHERE id=$1',UUID(first['fairness_pair_id']))
    report=json.loads(report) if isinstance(report,str) else report
    assert report['status']=='measured' and report['same_seat_wins_both'] is True
    assert first['phase']==second['phase']=='closed'


def test_every_acceptance_row_has_a_collected_offline_test():
    import ast,re
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    rows=re.findall(r'^\| M\d{2} `([^`]+)`',(root/'docs/acceptance/moot.md').read_text(),re.M)
    functions={node.name for node in ast.parse((root/'tests/acceptance/test_moot.py').read_text()).body
               if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))}
    assert rows and len(rows)==len(set(rows)) and set(rows)<=functions
