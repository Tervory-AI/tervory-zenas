import asyncio
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from agents.zenas.core import boundary, savings
from agents.zenas.core.outbound import TankClient
from agents.zenas.core.store import PgStore
HEADERS={'x-tank-key':'placeholder-room-key'}
TENANT='11111111-1111-4111-8111-111111111111'
OTHER='22222222-2222-4222-8222-222222222222'

ROOT=Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_run_receipt_is_durable_private_and_replayed_without_a_second_route(database,monkeypatch):
    monkeypatch.setenv('TANK_KEY',HEADERS['x-tank-key'])
    monkeypatch.setenv('ZENAS_PII_KEY','00'*32)
    await PgStore(database).apply_schema()
    calls=[]
    ledger=str(uuid4())
    def network(request):
        calls.append(request.url.path)
        body=json.loads(request.content)
        assert body['tenant_id']==TENANT
        if request.url.path=='/v1/route':
            return httpx.Response(200,json={'ledger_id':ledger,'model':'local-legal','rung':2,
                 'cost_usd':0,'tokens_in':30,'tokens_out':4,'content':'Source-backed answer'})
        assert request.url.path=='/v1/zenas/savings/quote'
        assert body['ledger_ids']==[ledger] and json.loads(body['reference']['input'])['question']=='placeholder private matter'
        assert json.loads(body['reference']['output'])=={'text':'Source-backed answer'}
        return httpx.Response(200,json={'cost':0,'would have cost':.004,'baseline_model':'paid-default','priced_at':'2026-11-06',
                                        'tokens_in':30,'tokens_out':4})
    async with httpx.AsyncClient(transport=httpx.MockTransport(network)) as http:
        app=FastAPI();app.state.pool=database;app.state.tank=TankClient('http://tank','placeholder','zenas',http)
        app.middleware('http')(boundary.guard)
        @app.post('/answers')
        async def answer(body:dict):
            result=await app.state.tank.route(body['tenant_id'],'zenas.apply',[{'role':'user','content':body['question']}])
            return {'text':result['content']}
        headers={**HEADERS,'idempotency-key':str(uuid4()),'x-zenas-tenant':TENANT}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://room') as client:
            response=await client.post('/answers',headers=headers,json={'tenant_id':TENANT,'question':'placeholder private matter'})
            assert response.status_code==200,response.text
            replay=await client.post('/answers',headers=headers,json={'tenant_id':TENANT,'question':'placeholder private matter'})
            assert replay.json()==response.json()
        receipt=response.json()['savings_receipt']
        assert receipt['cost']==0 and receipt['would have cost']==.004
        assert calls==['/v1/route','/v1/zenas/savings/quote']
    rows=await savings.receipts(database,TENANT)
    assert len(rows)==1 and rows[0]['ledger_ids']==[ledger]
    assert 'placeholder private matter' not in json.dumps(rows)
    assert await savings.receipts(database,OTHER)==[]


@pytest.mark.asyncio
async def test_code_only_and_missing_price_receipts_do_not_invent_savings(database):
    await PgStore(database).apply_schema()
    run=savings.Run(TENANT,'/ladder/cite-check')
    result=await savings.save(database,None,run,{'citation':'Rule 1'},{'verified':True})
    assert result['cost']==0 and result['would have cost'] is None
    assert len(await savings.receipts(database,TENANT))==1
    run=savings.Run(TENANT,'/answers');run.attempts=1;run.uncertain=True
    result=await savings.save(database,None,run,{}, {},failed=True)
    assert result['cost'] is None and result['status']=='failed'
    assert result['would have cost'] is None


@pytest.mark.asyncio
async def test_cancelled_model_call_has_unknown_cost_and_cannot_cross_tenant():
    async def hung(request):
        await asyncio.sleep(10)
    async with httpx.AsyncClient(transport=httpx.MockTransport(hung)) as http:
        client=TankClient('http://tank','placeholder','zenas',http)
        run=savings.Run(TENANT,'/answers');token=savings.current.set(run)
        try:
            with pytest.raises(ValueError,match='tenant_mismatch'):
                await client.route(OTHER,'zenas.apply',[])
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(.01):await client.route(TENANT,'zenas.apply',[])
            assert run.attempts==1 and run.uncertain
        finally:savings.current.reset(token)


@pytest.mark.asyncio
async def test_native_tank_quote_uses_tenant_ledgers_and_existing_paid_price(database):
    import zenas_savings as prices
    async with database.acquire() as conn:
        await conn.execute('''CREATE SCHEMA tank;
          CREATE TABLE tank.ledger(id uuid PRIMARY KEY,tenant_id uuid,region text,agent text,cost_usd numeric,cost_status text DEFAULT 'set',research_cost_status text,
              task_type text,rung int,model text,tokens_in int,tokens_out int,outcome text);
          CREATE TABLE tank.policy(task_type text,tenant_id uuid,model text);
          CREATE TABLE tank.proof_prices(model text,family text,input_usd numeric,output_usd numeric,updated_at timestamptz DEFAULT now(),active boolean DEFAULT true);
          CREATE TABLE tank.baseline_settings(task_type text,override_model text);
          CREATE FUNCTION tank.standard_model_for(text) RETURNS text LANGUAGE sql AS $$ SELECT 'paid-default'::text $$;
          INSERT INTO tank.policy VALUES('zenas.rule_card',NULL,'serving');
          INSERT INTO tank.proof_prices(model,family,input_usd,output_usd) VALUES('serving','paid',.000001,.000002),('paid-default','paid',.00001,.00002);''')
        ledger=uuid4()
        await conn.execute("INSERT INTO tank.ledger(id,tenant_id,region,agent,cost_usd,cost_status) VALUES($1,$2,'za','zenas',.001,'set')",ledger,UUID(TENANT))
        body=prices.Quote(tenant_id=TENANT,ledger_ids=[ledger],tokens_in=100,tokens_out=50)
        quote=await prices.quote(conn,body,'za')
        assert quote['cost']==.001 and quote['would have cost']==.002 and quote['baseline_model']=='paid-default'
        await conn.execute('UPDATE tank.ledger SET tokens_in=200,tokens_out=100')
        billed=await prices.quote(conn,body,'za')
        assert billed['tokens_in']==200 and billed['tokens_out']==100 and billed['would have cost']==.004
        for scope,region in [(OTHER,'za'),(TENANT,'us-east')]:
            with pytest.raises(HTTPException) as error:
                await prices.quote(conn,body.model_copy(update={'tenant_id':UUID(scope)}),region)
            assert error.value.status_code==403
        await conn.execute("UPDATE tank.ledger SET cost_status='not_set'")
        assert (await prices.quote(conn,body,'za'))['cost'] is None
        await conn.execute("DELETE FROM tank.proof_prices WHERE model='paid-default'")
        assert (await prices.quote(conn,body,'za'))['would have cost'] is None
        await conn.execute("INSERT INTO tank.proof_prices(model,family,input_usd,output_usd) VALUES('paid-default','paid',.00001,.00002)")
        coded=prices.Quote(tenant_id=OTHER,run_id=uuid4(),reference={'input':'{"citation":"Rule 1"}','output':'{"verified":true}'})
        first=await prices.quote(conn,coded,'za');again=await prices.quote(conn,coded,'za')
        assert first==again and len(first['ledger_ids'])==1 and first['cost']==0
        assert first['tokens_in']>0 and first['tokens_out']>0 and first['would have cost']>0
        assert await conn.fetchval('SELECT count(*) FROM tank.ledger WHERE tenant_id=$1',UUID(OTHER))==1


@pytest.mark.asyncio
async def test_every_scheduled_job_records_each_tenant_separately(database,monkeypatch):
    await PgStore(database).apply_schema()
    monkeypatch.delenv('TANK_KEY',raising=False)
    async def work(conn,*,region):
        for tenant in [TENANT,OTHER]:
            run=savings.get_run(tenant);run.attempts+=1
            savings.observed(tenant,{'ledger_id':str(uuid4())})
        return {'done':2}
    assert await savings.job(database,work,region='za',name='regulations')=={'done':2}
    a,b=await savings.receipts(database,TENANT),await savings.receipts(database,OTHER)
    assert len(a)==len(b)==1 and a[0]['ledger_ids']!=b[0]['ledger_ids']
    assert savings.current.get() is None


@pytest.mark.asyncio
async def test_receipt_view_is_empty_at_day_zero_and_shows_measured_costs(client_bundle):
    from playwright.async_api import async_playwright,expect
    from zenas_finish.test_gate5_journeys import screen
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        context,page,errors=await screen(browser,client_bundle,'receipts')
        await expect(page.get_by_role('columnheader',name='Cost',exact=True)).to_be_visible()
        await expect(page.get_by_role('columnheader',name='Would have cost',exact=True)).to_be_visible()
        await expect(page.get_by_text('Ask Zenas',exact=True)).to_be_visible()
        assert '$' not in await page.locator('body').inner_text()
        await page.evaluate('window.renderScreen("receipts",{receipts:[{id:"one",action:"/answers",cost:0.001,"would have cost":0.02,baseline_model:"paid-default"}]})')
        await expect(page.get_by_text('$0.00100000',exact=True)).to_be_visible()
        await expect(page.get_by_text('$0.02000000 · paid-default',exact=True)).to_be_visible()
        await page.evaluate('window.renderScreen("receipts",{receipts:[{id:"other-product",action:"/moot/hearings/one/ai",cost:77,"would have cost":88,baseline_model:"paid-default"}]})')
        await expect(page.get_by_text('Ask Zenas',exact=True)).to_be_visible()
        assert '$' not in await page.locator('body').inner_text()
        assert 'Moot Court' not in await page.locator('body').inner_text()
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        await page.evaluate('window.renderScreen("receipts",{receipts:Array.from({length:100},(_,i)=>({id:String(i),action:"/answers",cost:i+.001,"would have cost":i+.02,baseline_model:"paid-default"}))})')
        panel=page.get_by_role('region',name='Receipts',exact=True)
        await panel.focus();await page.keyboard.press('End')
        await expect(panel.get_by_role('row').last).to_be_in_viewport()
        assert not errors
        await context.close();await browser.close()


from zenas_finish.test_gate5_journeys import client_bundle


@pytest.mark.asyncio
async def test_recorded_workflow_tokens_cache_and_code_only_composition():
    from agent_skills import input_encoding
    from test_zenas_ladder import Model,PASSAGE,CARD_JSON,QUESTION,TENANT_A
    from agents.zenas.core.ladder_store import LadderMemory
    from agents.zenas.core import service
    recorded=json.loads((ROOT/'tests/zenas_finish/fixtures/savings-workflow.json').read_text())
    count=lambda calls:sum(len(input_encoding().encode_ordinary(m['content'])) for c in calls for m in c)
    assert count(recorded['before_messages'])==recorded['before_tokens']==375
    ladder=LadderMemory();await ladder.register_tenant(TENANT_A,'us-east')
    proposal=json.loads(json.dumps(CARD_JSON));proposal['decision_table']=[{'element_id':'written','when':{'form':'written'},'finding':'met'}]
    model=Model(json.dumps(proposal))
    args=dict(tenant_id=TENANT_A,home_region='us-east',box_region='us-east',jurisdiction='us',question=QUESTION,passages=[PASSAGE])
    first=await service.build_rule_card(ladder,None,model,**args)
    assert first['ok']
    card=first['rule_card']['id']
    assert (await service.approve_rule_card(ladder,tenant_id=TENANT_A,home_region='us-east',box_region='us-east',card_id=card,
        reviewer='Ada Counsel',admission_no='12345',jurisdiction='us',passages=[PASSAGE]))['ok']
    for smooth in [False,True]:
        assert (await service.build_rule_card(ladder,None,model,**args))['reused']
        memo=await service.write_memo(ladder,model,tenant_id=TENANT_A,home_region='us-east',box_region='us-east',
                                      card_id=card,features={'form':'written'},question=QUESTION,smooth=smooth)
        assert memo['ok'] and memo['model_calls']==0 and memo['cost_usd']==0
        assert '8 Del. C. § 101' in memo['memo']['text'] and not memo['smoothed']
    assert model.calls==1
    assert count([c['messages'] for c in model.sent])==184


@pytest.mark.asyncio
async def test_command_and_mcp_export_main_actions_with_strict_scope(monkeypatch):
    import command,mcp_server
    from command_tools import zenas
    actor=command.Actor(UUID(TENANT),'placeholder-user',False,frozenset({'zenas'}))
    listed={t['name']:t for t in mcp_server.tools(actor)['tools']}
    required={'zenas.ask','zenas.draft','zenas.review','zenas.receipts','zenas.retrieve','zenas.apply',
              'zenas.compose','zenas.plain'}
    assert required<=set(listed)
    assert not any(name.startswith(('zenas.moot.', 'moot.')) for name in listed)
    outsider=command.Actor(UUID(OTHER),'placeholder-other',False)
    assert not required & {t['name'] for t in mcp_server.tools(outsider)['tools']}
    for name in required:
        schema=listed[name]['inputSchema']
        assert schema.get('additionalProperties') is False
        assert not {'tenant_id','user_id','founder','home_region'} & set(schema.get('properties',{}))
    monkeypatch.setattr(command,'authorized_actor',AsyncMock(return_value=actor))
    import trigger_routes
    monkeypatch.setattr(trigger_routes,'code_receipt',AsyncMock(return_value=uuid4()))
    plan=await zenas.code_plan(actor,[{'role':'user','content':'zenas.receipts {}'}],None)
    assert json.loads(plan.content)['actions'][0]['tool']=='zenas.receipts'
    assert command.explicit_ledger_ids({'savings_receipt':{'ledger_ids':[str(uuid4())]}})==[]
    with pytest.raises((HTTPException,ValueError)):
        await zenas.code_plan(actor,[{'role':'user','content':'zenas.receipts {"tenant_id":"'+OTHER+'"}'}],None)
    # Keep the caller/region binding proof on Zenas's actual legal action.
    # A Zenas tool grant must never become a Moot grant.
    action=command.PlannedAction(tool='zenas.apply',arguments={'card_id':str(uuid4()),
        'features':{'form':'written'},'question':'placeholder legal question'})
    monkeypatch.setenv('TANK_REGION','za')
    import sys
    monkeypatch.setitem(sys.modules,'main',SimpleNamespace(box_region=lambda:'za'))
    method,path,query,body=command.adapter_request(action,actor)
    assert method=='POST' and path=='/v1/zenas/ladder/apply'
    assert query=={'tenant_id':TENANT,'home_region':'za'}
    assert body['tenant_id']==TENANT and body['home_region']=='za'
    assert not {'founder','actor_id','user_id'} & set(body)
    with pytest.raises((HTTPException,ValueError)):
        command.validate_action(command.PlannedAction(tool='zenas.moot.command',arguments={}),actor)
    assert await zenas.code_plan(actor,[{'role':'user','content':'zenas.moot.list {}'}],None) is None


def test_model_paths_have_no_direct_provider_clients():
    import ast
    forbidden={'openai','anthropic','litellm','google.generativeai','google.genai','ollama','transformers'}
    for file in (ROOT/'agents/zenas').rglob('*.py'):
        tree=ast.parse(file.read_text())
        for node in ast.walk(tree):
            imports=([n.name for n in node.names] if isinstance(node,ast.Import) else
                     [node.module or ''] if isinstance(node,ast.ImportFrom) else [])
            assert not any(n==p or n.startswith(p+'.') for n in imports for p in forbidden),str(file)


@pytest.mark.asyncio
async def test_receipt_route_and_database_rls_bind_the_attested_tenant(database,monkeypatch):
    from chassis.tenant_db import TenantPool
    from zenas_finish.test_gate5_boundary import room
    await PgStore(database).apply_schema()
    await database.execute('''ALTER TABLE agent_zenas.savings_receipts ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent_zenas.savings_receipts FORCE ROW LEVEL SECURITY;
        CREATE POLICY savings_test ON agent_zenas.savings_receipts
        USING(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)
        WITH CHECK(tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid)''')
    scoped=TenantPool(database)
    for tenant in [TENANT,OTHER]:
        await database.execute("INSERT INTO agent_zenas.tenants VALUES($1,'us-east')",UUID(tenant))
        await savings.save(scoped,None,savings.Run(tenant,'/answers'),{}, {})
    assert await database.fetchval('SELECT count(*) FROM agent_zenas.savings_receipts')==0
    assert len(await savings.receipts(scoped,TENANT))==len(await savings.receipts(scoped,OTHER))==1
    monkeypatch.setenv('TANK_KEY',HEADERS['x-tank-key'])
    module=room();module.app.state.pool=scoped
    monkeypatch.setattr(module,'_box_region',lambda:'us-east')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),base_url='http://room') as client:
        assert (await client.get('/receipts',params={'tenant_id':TENANT,'home_region':'us-east'})).status_code==401
        headers={**HEADERS,'x-zenas-tenant':TENANT}
        own=await client.get('/receipts',params={'tenant_id':TENANT,'home_region':'us-east'},headers=headers)
        assert own.status_code==200 and len(own.json()['receipts'])==1
        assert (await client.get('/receipts',params={'tenant_id':OTHER,'home_region':'us-east'},headers=headers)).status_code==403


@pytest.mark.asyncio
async def test_skala_model_and_code_receipts_reach_the_same_run():
    ids=[str(uuid4()),str(uuid4())]
    replies=[{'in_scope':True,'_ledger_ids':ids,'_cost_usd':.003},
             {'in_scope':True,'_ledger_id':ids[0],'_ledger_ids':[],'_cost_usd':0}]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=replies.pop(0)))) as http:
        tank=TankClient('http://tank','placeholder','zenas',http)
        run=savings.Run(TENANT,'/answers');token=savings.current.set(run)
        try:
            for _ in range(2):assert (await tank.run_skill(TENANT,'zenas.scope',{'text':'civil contract'}))['in_scope']
            assert run.ledgers==set(ids) and run.attempts==2 and not run.uncertain
        finally:savings.current.reset(token)


@pytest.mark.asyncio
async def test_real_zenas_skill_corridor_keeps_every_routed_model_ledger(tmp_path,monkeypatch):
    import ast,sys
    import skala,skala_intake,agent_skills
    from zenas_finish.test_tank_training_patch import functions
    import zenas_savings
    module=SimpleNamespace()
    monkeypatch.setitem(sys.modules,'placeholder_savings_tank',module)
    monkeypatch.setattr(skala_intake,'guard_preview',lambda *args:None)
    monkeypatch.setattr(agent_skills,'guard_submission_owner',AsyncMock())
    (tmp_path/'skill.yaml').write_text('agent: zenas\n')
    package=SimpleNamespace(agent='zenas',task_type='zenas.scope',id='zenas.scope',ref='zenas.scope/1.0.0',path=tmp_path)
    ids=[str(uuid4()),str(uuid4())]
    ctx=SimpleNamespace(_model=AsyncMock(side_effect=[{'ledger_id':i,'cost_usd':.003} for i in ids]),calls=0,cost_usd=0,task_id=None)
    async def skill(pkg,inputs,context):
        for _ in range(2):await context._model(messages=[],task_type='zenas.scope')
        context.calls=2;context.cost_usd=.006
        return {'in_scope':True}
    monkeypatch.setattr(skala,'run_skill',skill)
    namespace={'__name__':'placeholder_savings_tank','make_ctx':lambda *a,**k:ctx,
        'get_package':lambda *a:package,'skill_state':AsyncMock(return_value='approved'),
        'RouteRequest':lambda **kw:SimpleNamespace(**kw),'Message':lambda **kw:SimpleNamespace(model_dump=lambda:kw,**kw),
        'load_policy':AsyncMock(return_value={}),'_preflight_mask':AsyncMock(),
        'skala':skala,'json':json,'HTTPException':HTTPException}
    tree=ast.parse((ROOT/'tank/main.py').read_text())
    selected=[n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_run_one_skill']
    function=functions(selected,namespace)['_run_one_skill']
    body=SimpleNamespace(version='1.0.0',preview=False,tenant_id=UUID(TENANT),input={})
    result=await function('zenas.scope',body,'zenas')
    assert result['_ledger_ids']==ids and result['_cost_usd']==.006
