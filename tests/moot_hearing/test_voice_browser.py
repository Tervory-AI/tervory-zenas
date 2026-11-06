"""Actual local-speech UI -> real hearing API/store -> judge fake -> saved result."""
import asyncio
import json
from pathlib import Path
from uuid import UUID,uuid4
from urllib.parse import urlsplit,parse_qsl,urlencode

import httpx
import pytest
from fastapi import FastAPI
from playwright.async_api import async_playwright,expect

from agents.zenas.core.moot import hearing
from agents.zenas.core.moot.hearing_api import register_routes
from agents.zenas.core.moot.hearing_store import HearingStore
from agents.zenas.core.moot.published import published_matches
from .test_api import headers
from .test_hearing import OWNER,HUMANS,problem
from .test_store import transition
from .test_voice import ScriptedJudge
from zenas_finish.test_gate5_journeys import client_bundle,screen

SPEECH=(Path(__file__).parent/'local_speech.js').read_text()+"\nwindow.browserErrors=[];window.addEventListener('error',event=>window.browserErrors.push(event.message));"


def application(monkeypatch,store,judge):
    monkeypatch.setenv('TANK_KEY','disposable-room-test-key')
    app=FastAPI()
    class Tenants:
        async def get_tenant(self,tenant):
            return {'tenant_id':tenant,'home_region':'us-east'} if tenant==OWNER.tenant_id else None
    register_routes(app,lambda:store.pool,Tenants,lambda:'us-east',lambda:judge)
    return app


def bridge(client,actor):
    async def route(route):
        req=route.request;uri=urlsplit(req.url)
        tail=uri.path.removeprefix('/api/zenas/moot/')
        if tail in {'sources','participants'}:return await route.fulfill(status=404,json={'error':'not_found'})
        query=dict(parse_qsl(uri.query));query['home_region']='us-east'
        response=await client.request(req.method,'/moot/'+tail+'?'+urlencode(query),
            headers={**headers(actor),'content-type':'application/json'},content=req.post_data)
        body=response.json()
        if not response.is_success:body={'error':body.get('detail','room_unavailable')}
        await route.fulfill(status=response.status_code,json=body)
    return route


async def start(store,judge_ai=True):
    seats={s:{'kind':'ai'} if s=='judge' and judge_ai else {'kind':'human','user_id':HUMANS[s].user_id} for s in hearing.SEATS}
    state=await store.create(OWNER,str(uuid4()),problem(),seats)
    state=await store.mutate(OWNER,state['id'],str(uuid4()),0,'lock',{},transition('lock'))
    return await store.create_pair(OWNER,state['id'],str(uuid4()),state['revision'],start=True)


async def select(page,state,seat):
    try:
        await expect(page.get_by_role('combobox',name='The hearing',exact=True)).to_be_visible(timeout=3000)
    except Exception as exc:
        raise AssertionError((await page.locator('body').inner_text(),await page.evaluate('window.browserErrors??[]'))) from exc
    await page.get_by_role('combobox',name='The hearing',exact=True).select_option(state['id'])
    await page.get_by_role('combobox',name='Your role',exact=True).select_option(seat)
    await expect(page.get_by_role('textbox',name='Submission',exact=True)).to_be_visible()


async def argument(page):
    field=page.get_by_role('textbox',name='Submission',exact=True)
    await field.focus();await page.keyboard.press('Alt+m')
    await page.wait_for_function('window.recognitionStarts===1')
    await page.evaluate('window.emitVoice("Uncommitted partial",false)')
    await expect(field).to_have_value('')
    await page.evaluate('window.emitVoice("Spoken civil argument")')
    # Duplicate final notifications must not duplicate words.
    await page.evaluate('window.emitVoice("Spoken civil argument")')
    await expect(field).to_have_value('Spoken civil argument')
    await page.keyboard.press('Alt+m')
    await page.get_by_role('combobox',name='Governing authority',exact=True).select_option('case-1')
    for label,value in [('Why this fact matters','This fact bears on the selected issue'),
        ('Apply the rule','Apply the selected rule to the selected fact'),('Legal significance','The selected fact is material to applying the rule'),
        ('Opposing argument','The other side disputes this application'),('Response to the challenge','Compare both positions against the record'),
        ('Uncertainty','The interpretation remains uncertain'),('Conclusion','No certain conclusion')]:
        await page.get_by_role("textbox",name=label,exact=True).fill(value)
    await page.get_by_role('button',name='Submit',exact=True).click()


@pytest.mark.asyncio
async def test_voice_hearing_judge_audio_saved_feedback_studio_and_public_replay(monkeypatch,pg_store,client_bundle):
    state=await start(pg_store);judge=ScriptedJudge();app=application(monkeypatch,pg_store,judge)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://placeholder') as client:
      async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
        contexts=[]
        for seat in ('applicant','respondent'):
            context,page,errors=await screen(browser,client_bundle,'moot',api=bridge(client,HUMANS[seat]),init_script=SPEECH,width=1440)
            contexts.append(context)
            await select(page,state,seat)
            await argument(page)
            await page.wait_for_function('window.spoken.length>0',timeout=3000)
            spoken=await page.evaluate('window.spoken')
            assert ('Address the selected issue' if seat=='applicant' else 'Independent order') in ' '.join(spoken)
            assert 'never counsel' not in ' '.join(spoken) and not errors
            state=await HearingStore(pg_store.pool).load(OWNER,state['id'])
        assert state['phase']=='closed' and len(state['transcript'])==4 and len(judge.calls)==2
        assert all(turn.get('voice',{}).get('engine')=='web_speech_local' for turn in state['transcript'] if turn['seat']!='judge')
        assert state['feedback']['fairness']['status']=='not_applicable'
        for kind,width in [('bench',1920),('citations',1440),('feedback',375)]:
            context,page,errors=await screen(browser,client_bundle,'moot-screen',props={'id':state['id'],'kind':kind},api=bridge(client,HUMANS['applicant']),width=width)
            contexts.append(context)
            await expect(page.get_by_role('heading',name='Offline civil appeal')).to_be_visible()
            if kind=='feedback':
                await expect(page.get_by_text('Not an AI-against-AI match.')).to_be_visible()
                pdf=await page.pdf(format='A4')
                assert pdf.startswith(b'%PDF') and len(pdf)>1000
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            assert 'never counsel' not in await page.locator('body').inner_text() and not errors
        state=await pg_store.mutate(OWNER,state['id'],str(uuid4()),state['revision'],'publish',{},transition('publish'))
        rows=await published_matches(pg_store.pool,UUID('00000000-0000-0000-0000-000000000001'),'us-east')
        assert len(rows)==1
        context,page,errors=await screen(browser,client_bundle,'moot-public',props={'row':rows[0]},init_script=SPEECH)
        contexts.append(context)
        await page.keyboard.press('Alt+j')
        await page.wait_for_function('window.spoken.length===2')
        assert await page.evaluate('window.spoken')==['Independent order','Independent reason']
        assert 'never counsel' not in await page.locator('body').inner_text() and not errors
        for context in contexts:await context.close()
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['unsupported','unavailable','pending'])
async def test_no_local_speech_never_falls_back_to_a_remote_microphone(monkeypatch,pg_store,client_bundle,mode):
    state=await start(pg_store,judge_ai=False);app=application(monkeypatch,pg_store,ScriptedJudge())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://placeholder') as client:
      async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
        context,page,errors=await screen(browser,client_bundle,'moot',api=bridge(client,HUMANS['applicant']),
            init_script=SPEECH+'\nwindow.speechMode='+json.dumps(mode)+';',width=1440)
        await select(page,state,'applicant')
        if mode=='pending':await page.clock.install()
        await page.get_by_role('textbox',name='Submission',exact=True).focus();await page.keyboard.press('Alt+m')
        if mode=='pending':await page.clock.fast_forward(120_001)
        await expect(page.get_by_role('alert')).to_have_text('Room unavailable')
        assert await page.evaluate('window.recognitionStarts')==0
        await page.get_by_role('textbox',name='Submission',exact=True).fill('Typed fallback remains available')
        assert not errors
        await context.close();await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['remote-tts','tts-error'])
async def test_playback_failure_keeps_saved_judge_text_and_never_uses_remote_speech(monkeypatch,pg_store,client_bundle,mode):
    state=await start(pg_store);judge=ScriptedJudge();app=application(monkeypatch,pg_store,judge)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://placeholder') as client:
      async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
        context,page,errors=await screen(browser,client_bundle,'moot',api=bridge(client,HUMANS['applicant']),
            init_script=SPEECH+'\nwindow.speechMode='+json.dumps(mode)+';',width=1440)
        await select(page,state,'applicant');await argument(page)
        await expect(page.get_by_role('alert')).to_have_text('Room unavailable')
        await expect(page.get_by_text('Address the selected issue',exact=True)).to_have_count(2)
        await expect(page.get_by_text('Address the selected issue',exact=True).first).to_be_visible()
        assert await page.evaluate('window.spoken.length')==0 and not errors
        saved=await pg_store.load(OWNER,state['id'])
        assert len(saved['transcript'])==2 and len(judge.calls)==1
        await context.close();await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['denied','handover'])
async def test_microphone_permission_and_revocation_leave_no_spurious_turn(monkeypatch,pg_store,client_bundle,mode):
    state=await start(pg_store,judge_ai=False);app=application(monkeypatch,pg_store,ScriptedJudge())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://placeholder') as client:
      async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
        context,page,errors=await screen(browser,client_bundle,'moot',api=bridge(client,HUMANS['applicant']),
            init_script=SPEECH+'\nwindow.speechMode='+json.dumps(mode)+';',width=1440)
        await select(page,state,'applicant')
        await page.get_by_role('textbox',name='Submission',exact=True).focus();await page.keyboard.press('Alt+m')
        await page.wait_for_function('window.recognitionStarts===1')
        if mode=='denied':await expect(page.get_by_role('alert')).to_have_text('Room unavailable')
        else:
            await pg_store.mutate(OWNER,state['id'],str(uuid4()),state['revision'],'handover',
                {'seat':'applicant','controller':{'kind':'ai'}},transition('handover'))
            await page.wait_for_function('window.recognitionAborts>0')
            await page.evaluate('window.emitVoice("Late transcript must be discarded")')
        assert (await pg_store.load(OWNER,state['id']))['transcript']==[] and not errors
        await context.close();await browser.close()
