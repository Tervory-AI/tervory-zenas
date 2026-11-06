"""Offline room journeys and adversarial request/replay boundaries."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from agents.zenas.core import boundary
from agents.zenas.core.outbound import TankClient
from agents.zenas.core.store import PgStore

ROOT = Path(__file__).resolve().parents[2]
TENANT = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
HEADERS = {'x-tank-key': 'placeholder-room-key'}


def room():
    spec = importlib.util.spec_from_file_location('placeholder_gate5_room', ROOT/'agents/zenas/app.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_every_room_route_authenticates_before_validation_or_storage(monkeypatch):
    monkeypatch.setenv('TANK_KEY', HEADERS['x-tank-key'])
    app = room().app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://placeholder') as client:
        checked = set()
        for path, operations in app.openapi()['paths'].items():
            if path == '/health':
                continue
            for method in operations:
                method = method.upper()
                if method in {'HEAD', 'OPTIONS'}:
                    continue
                for key in ['', 'placeholder-wrong-key']:
                    response = await client.request(method, path, headers={'x-tank-key': key}, content=b'invalid private body')
                    assert response.status_code == 401, (method, path, response.status_code)
                    assert response.json() == {'detail': 'room_key_required'}
                checked.add(path)
        assert {'/jobs', '/matters', '/answers', '/tenants', '/consents', '/moot/published', '/street-law/sessions'} <= checked


@pytest.mark.asyncio
async def test_tenant_spoofing_duplicate_query_and_invalid_input_never_enter_handler(monkeypatch):
    monkeypatch.setenv('TANK_KEY', HEADERS['x-tank-key'])
    module = room()
    store = AsyncMock(side_effect=AssertionError('invalid request reached storage'))
    monkeypatch.setattr(module, '_store', store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url='http://placeholder', headers=HEADERS) as client:
        response = await client.post('/matters', headers={'x-zenas-tenant': TENANT}, json={'tenant_id': OTHER})
        assert response.status_code == 403
        response = await client.get(f'/matters?tenant_id={TENANT}&tenant_id={OTHER}')
        assert response.status_code == 403
        response = await client.post('/matters', json={'tenant_id': TENANT, 'title': {'private': 'placeholder-private-value'}})
        assert response.status_code == 422 and 'placeholder-private-value' not in response.text
    store.assert_not_called()


@pytest.mark.asyncio
async def test_durable_receipts_replay_once_bind_body_and_tenant_and_encrypt(database, monkeypatch):
    monkeypatch.setenv('TANK_KEY', HEADERS['x-tank-key'])
    monkeypatch.setenv('ZENAS_PII_KEY', '00'*32)
    await PgStore(database).apply_schema()
    app = FastAPI()
    app.state.pool = database
    app.middleware('http')(boundary.guard)
    calls = []

    @app.post('/action', status_code=201)
    async def action(body: dict):
        calls.append(body['tenant_id'])
        await asyncio.sleep(.03)
        return {'result': 'placeholder-private-value', 'receipt': len(calls), 'tenant': body['tenant_id']}

    command = str(uuid4())
    headers = {**HEADERS, 'idempotency-key': command, 'x-zenas-tenant': TENANT}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://placeholder') as client:
        first, concurrent = await asyncio.gather(*[client.post('/action', headers=headers, json={'tenant_id': TENANT}) for _ in range(2)])
        assert sorted([first.status_code, concurrent.status_code]) == [201,409]
        result = first if first.status_code == 201 else concurrent
        replay = await client.post('/action', headers=headers, json={'tenant_id': TENANT})
        assert replay.json() == result.json() and replay.status_code == 201
        assert len(calls) == 1
        changed = await client.post('/action', headers=headers, json={'tenant_id': TENANT, 'changed': True})
        assert changed.status_code == 409 and len(calls) == 1
        other = await client.post('/action', headers={**headers, 'x-zenas-tenant': OTHER}, json={'tenant_id': OTHER})
        assert other.status_code == 201 and other.json()['tenant'] == OTHER
    async with database.acquire() as conn:
        rows = await conn.fetch('SELECT * FROM agent_zenas.command_receipts')
    assert len(rows) == 2
    assert 'placeholder-private-value' not in str(rows)


@pytest.mark.asyncio
async def test_interruption_is_plain_redacted_and_cannot_reexecute(database, monkeypatch, caplog):
    monkeypatch.setenv('TANK_KEY', HEADERS['x-tank-key'])
    monkeypatch.setenv('ZENAS_PII_KEY', '00'*32)
    monkeypatch.setattr(boundary, 'REQUEST_SECONDS', .5)
    await PgStore(database).apply_schema()
    app = FastAPI()
    app.state.pool = database
    app.middleware('http')(boundary.guard)
    calls = []

    @app.post('/hang')
    async def hang(body: dict):
        calls.append(1)
        await asyncio.sleep(10)

    @app.get('/error')
    async def error():
        raise RuntimeError('https://placeholder.invalid/?token=placeholder-private-value')

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://placeholder', headers=HEADERS) as client:
        headers = {'idempotency-key': str(uuid4())}
        response = await client.post('/hang', headers=headers, json={'tenant_id': TENANT})
        assert response.status_code == 503 and response.json()['detail'] == boundary.ERROR
        replay = await client.post('/hang', headers=headers, json={'tenant_id': TENANT})
        assert replay.status_code == 409 and len(calls) == 1
        error = await client.get('/error')
        assert error.status_code == 503 and 'placeholder-private-value' not in error.text
    assert 'placeholder-private-value' not in caplog.text


@pytest.mark.asyncio
async def test_paid_approval_polling_has_total_deadline_and_never_reposts(monkeypatch):
    import chassis.paid_steps as steps
    monkeypatch.setattr(boundary, 'CALL_SECONDS', .02)
    monkeypatch.setattr(steps, 'POLL_SECONDS', .001)
    calls = []
    async def respond(req):
        calls.append(req.method)
        if req.method == 'POST':
            return httpx.Response(409, json={'detail': {'code': 'paid_step_approval_required', 'tenant_id': TENANT,
                'approval_id': str(uuid4()), 'run_id': str(uuid4())}})
        return httpx.Response(200, json={'state': 'waiting'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), timeout=1) as http:
        tank = TankClient('http://placeholder', 'placeholder-key', 'zenas', http)
        with pytest.raises(TimeoutError):
            await tank.route(TENANT, 'zenas.answer', [])
    assert calls.count('POST') == 1 and 1 <= calls.count('GET') <= 30


@pytest.mark.asyncio
async def test_source_failure_receipt_never_saves_exception_url():
    from agents.zenas.pool.bulk import _record_error
    from agents.zenas.pool.catalogue import CATALOGUE
    store = AsyncMock()
    await _record_error(store, CATALOGUE[0], RuntimeError('https://placeholder.invalid/?token=placeholder-private-value'), None)
    assert store.record_run.await_args.kwargs['detail'] == 'RuntimeError'


@pytest.mark.parametrize('name', ['../other/private.txt', '/tmp/private.txt', '..\\other\\private.txt', 'C:private.txt'])
def test_upload_cannot_escape_its_tenant(tmp_path, name):
    from chassis.files import save_file, UnsafePath
    with pytest.raises(UnsafePath):
        save_file(TENANT, name, b'private', root=tmp_path)


def test_symlinks_and_hardlinks_cannot_read_or_overwrite_another_tenant(tmp_path):
    from chassis.files import save_file, read_file, UnsafePath
    import os
    save_file(OTHER, 'private.txt', b'other tenant private', root=tmp_path)
    (tmp_path/TENANT).symlink_to(tmp_path/OTHER, target_is_directory=True)
    for operation in [lambda: read_file(TENANT,'private.txt',root=tmp_path),
                      lambda: save_file(TENANT,'private.txt',b'changed',root=tmp_path)]:
        with pytest.raises(UnsafePath): operation()
    (tmp_path/TENANT).unlink(); (tmp_path/TENANT).mkdir()
    (tmp_path/TENANT/'linked.txt').symlink_to(tmp_path/OTHER/'private.txt')
    with pytest.raises(UnsafePath): save_file(TENANT,'linked.txt',b'changed',root=tmp_path)
    os.link(tmp_path/OTHER/'private.txt',tmp_path/TENANT/'hard.txt')
    with pytest.raises(UnsafePath): save_file(TENANT,'hard.txt',b'changed',root=tmp_path)
    assert (tmp_path/OTHER/'private.txt').read_bytes()==b'other tenant private'
    (tmp_path/TENANT/'hard.txt').unlink()
    save_file(TENANT,'nested/safe.txt',b'own content',root=tmp_path)
    assert read_file(TENANT,'nested/safe.txt',root=tmp_path)==b'own content'


@pytest.mark.asyncio
async def test_scheduled_and_manual_job_copies_share_a_regional_lock_and_release_it(database):
    from agents.zenas.core.job_lock import exclusive_job
    entered=asyncio.Event()
    release=asyncio.Event()
    calls=[]
    @exclusive_job('placeholder-job')
    async def job(conn, *, region):
        calls.append(region)
        entered.set()
        await release.wait()
        return {'ok':True}
    first=asyncio.create_task(job(database,region='us-east'))
    await entered.wait()
    duplicate=await job(database,region='us-east')
    assert duplicate['status']=='already running' and calls==['us-east']
    release.set();assert await first=={'ok':True}
    assert await job(database,region='us-east')=={'ok':True}
    assert len(calls)==2
    @exclusive_job('placeholder-job')
    async def failed(conn, *, region):
        raise RuntimeError('offline failure')
    with pytest.raises(RuntimeError): await failed(database,region='za')
    assert await job(database,region='za')=={'ok':True}


def test_real_workspace_route_uses_session_tenant_and_stable_phase_command_ids():
    import subprocess
    result=subprocess.run(['node',str(ROOT/'tests/zenas_finish/gate5_server.mjs')],cwd=ROOT,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr


@pytest.mark.asyncio
async def test_authenticated_binary_upload_keeps_streaming_and_tenant_binding(monkeypatch):
    import chassis.storage_api as storage
    monkeypatch.setenv('TANK_KEY',HEADERS['x-tank-key'])
    uploaded=[]
    class LocalStorage:
        region='us-east'
        bucket='placeholder-bucket'
        def __init__(self,agent): assert agent=='zenas'
        async def put(self,tenant,category,name,data,**kwargs):
            uploaded.append((tenant,category,name,data))
            return 'placeholder-key'
        async def close(self): pass
    monkeypatch.setattr(storage,'Storage',LocalStorage)
    app=room().app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://placeholder',headers=HEADERS) as client:
        response=await client.put('/storage/objects/documents/offline.pdf?tenant_id='+TENANT,
            headers={'x-zenas-tenant':TENANT},content=b'%PDF-placeholder\x00binary')
        assert response.status_code==200 and response.json()['size_bytes']==23
        refused=await client.put('/storage/objects/documents/offline.pdf?tenant_id='+OTHER,
            headers={'x-zenas-tenant':TENANT},content=b'never uploaded')
        assert refused.status_code==403
    assert uploaded==[(TENANT,'documents','offline.pdf',b'%PDF-placeholder\x00binary')]


@pytest.mark.asyncio
async def test_command_body_uses_existing_storage_cap_before_parsing_or_dispatch(monkeypatch):
    monkeypatch.setenv('TANK_KEY',HEADERS['x-tank-key'])
    monkeypatch.setattr(boundary,'MAX_OBJECT_BYTES',8)
    app=FastAPI();app.middleware('http')(boundary.guard)
    calls=[]
    @app.post('/action')
    async def action(body:dict):
        calls.append(body)
        return {'ok':True}
    async def chunks():
        yield b'{"title"'
        yield b':"placeholder"}'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://placeholder',headers=HEADERS) as client:
        response=await client.post('/action',content=chunks())
        assert response.status_code==413 and calls==[]


@pytest.mark.asyncio
async def test_workers_receive_bounded_client_before_startup_awaits(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from agents.zenas.core.configuration import ROOM_SETTINGS
    for name in ROOM_SETTINGS:
        monkeypatch.setenv(name,'placeholder')
    monkeypatch.setenv('TANK_REGION','us-east')
    monkeypatch.setenv('ZENAS_PII_KEY','00'*32)
    for name in ['CHASSIS_ADMIN_DATABASE_URL','CHASSIS_RLS_ADMIN_ROLE']:
        monkeypatch.delenv(name,raising=False)
    module=room()
    @asynccontextmanager
    async def inner(application):
        application.state.pool=SimpleNamespace()
        application.state.http=SimpleNamespace()
        application.state.tank=object()
        yield
    monkeypatch.setattr(module,'_inner_lifespan',inner)
    checked=[]
    async def schema(self):
        assert isinstance(module.app.state.tank,TankClient)
        checked.append(True)
    monkeypatch.setattr(module.PgStore,'apply_schema',schema)
    @asynccontextmanager
    async def pool_store(): yield SimpleNamespace()
    monkeypatch.setattr(module,'_pool_store',pool_store)
    monkeypatch.setattr(module,'interrupt_abandoned_loads',AsyncMock())
    async with module.app.router.lifespan_context(module.app):
        assert module.app.state.configuration['ok']
    assert checked==[True]


@pytest.mark.asyncio
@pytest.mark.parametrize('product',['moot','street-law'])
async def test_private_receipt_cannot_cross_a_forwarded_actor_tenant_or_role(database,monkeypatch,product):
    from fastapi import Request
    monkeypatch.setenv('TANK_KEY',HEADERS['x-tank-key'])
    monkeypatch.setenv('ZENAS_PII_KEY','00'*32)
    await PgStore(database).apply_schema()
    app=FastAPI();app.state.pool=database;app.middleware('http')(boundary.guard)
    @app.post('/private')
    async def action(request:Request):
        return {'private':request.headers['x-'+product+'-actor']}
    key=str(uuid4())
    headers={**HEADERS,'idempotency-key':key,'x-'+product+'-tenant':TENANT,
             'x-'+product+'-actor':'placeholder-owner','x-'+product+'-founder':'1'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://placeholder') as client:
        first=await client.post('/private',headers=headers,json={})
        assert first.json()=={'private':'placeholder-owner'}
        for changed in [{'x-'+product+'-actor':'placeholder-other'}, {'x-'+product+'-founder':'0'}]:
            refused=await client.post('/private',headers={**headers,**changed},json={})
            assert refused.status_code==409 and 'placeholder-owner' not in refused.text
        other=await client.post('/private',headers={**headers,'x-'+product+'-tenant':OTHER,'x-'+product+'-actor':'placeholder-other'},json={})
        assert other.json()=={'private':'placeholder-other'}
    async with database.acquire() as conn:
        owners=await conn.fetch('SELECT tenant_id FROM agent_zenas.command_receipts')
        assert {str(row['tenant_id']) for row in owners}=={TENANT,OTHER}
