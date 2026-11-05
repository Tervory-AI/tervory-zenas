import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from agents.zenas.core import operations as ops
from agents.zenas.pool.region import RegionError

RECORDING = Path(__file__).parent / 'fixtures/frcp-rule1.html'


@pytest.mark.asyncio
async def test_recorded_real_rule_ingests_resolves_and_replays_without_network(database):
    blob = RECORDING.read_bytes()
    assert hashlib.sha256(blob).hexdigest() == '4935412f343e4f7e3da87b35f513a20f34b1d07f6a2afeb4122a17838caa5aea'

    async def get(url, headers):
        assert url == 'https://www.govinfo.gov/content/pkg/USCODE-2023-title28/html/USCODE-2023-title28-app-federalru-dup1-rule1.htm'
        assert headers['User-Agent'].startswith('TervoryZenasPool/')
        return blob

    async with database.acquire() as conn:
        result = await ops.sample(conn, region='us-east', selected=ops.sources('us-east', ['frcp']), http_get=get)
        assert result['ok']
        receipt = result['sources'][0]
        assert receipt['inserted'] == 1 and receipt['replay_inserted'] == 0
        assert receipt['documents'][0]['citation'] == 'Fed. R. Civ. P. 1'
        text = await conn.fetchval('SELECT text FROM zenas_pool.documents WHERE source_id=\'frcp\'')
        assert 'just, speedy, and inexpensive determination' in text


@pytest.mark.asyncio
async def test_foreign_source_refused_before_opening_database(monkeypatch):
    monkeypatch.setenv('TANK_REGION', 'us-east')
    connect = AsyncMock()
    monkeypatch.setattr(ops, 'connection', connect)
    with pytest.raises(RegionError):
        await ops.execute(ops.parser().parse_args(['sample', '--source', 'govza-acts']))
    connect.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('job', list(ops.JOBS))
async def test_each_scheduled_job_has_a_regional_command(database, monkeypatch, job):
    monkeypatch.setenv('TANK_REGION', 'us-east')
    monkeypatch.setenv('TANK_URL', 'http://tank:8000')
    monkeypatch.setenv('TANK_KEY', 'placeholder')
    monkeypatch.delenv('POOL_USE_FIXTURES', raising=False)
    scheduled = AsyncMock(return_value={'ok': True, 'completed': 1})
    module = __import__(ops.JOBS[job], fromlist=['run_scheduled'])
    monkeypatch.setattr(module, 'run_scheduled', scheduled)

    @asynccontextmanager
    async def connect(**kwargs):
        async with database.acquire() as conn:
            yield conn

    monkeypatch.setattr(ops, 'connection', connect)
    result = await ops.execute(ops.parser().parse_args([job]))
    assert result == {'ok': True, 'job': job, 'result': {'ok': True, 'completed': 1}}
    assert scheduled.await_args.kwargs == {'region': 'us-east'}


def training_settings(monkeypatch):
    for name, value in {'TANK_REGION': 'za', 'TANK_URL': 'http://tank:8000',
                        'TANK_API_KEY': 'placeholder', 'TRAINING_JOB_KEY': 'placeholder-job'}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(ops, 'prepare_court_text', AsyncMock(return_value={'split': 2000, 'excluded': 0}))


@pytest.mark.asyncio
async def test_single_training_command_uses_authorized_export_plan_and_existing_runner(monkeypatch):
    training_settings(monkeypatch)
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/jobs/export'):
            assert request.url.params['agent'] == 'zenas'
            assert request.headers['X-Tank-Key'] == 'placeholder-job'
            assert 'X-Tervory-User' not in request.headers
            return httpx.Response(200, json={'status': 'complete', 'exported': 2100})
        assert request.headers['X-Tervory-User'] == 'placeholder-founder'
        assert request.headers['X-Tervory-Tenant'] == '00000000-0000-0000-0000-000000000001'
        assert json.loads(request.content) == {'agent': 'zenas', 'minutes': 120}
        return httpx.Response(200, json={'status': 'ready' if request.url.path.endswith('/plan') else 'running', 'name': 'offline-job'})

    args = ops.parser().parse_args(['train', '--founder', 'placeholder-founder', '--submit'])
    result = await ops.training(args, transport=httpx.MockTransport(respond))
    assert result['ok'] and result['result']['name'] == 'offline-job'
    assert calls == ['/v1/training/run/plan', '/v1/training/jobs/export', '/v1/training/run']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['readiness', 'export', 'authorization'])
async def test_training_failure_cannot_submit_a_gpu_job(monkeypatch, failure):
    training_settings(monkeypatch)
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if failure == 'authorization':
            return httpx.Response(404)
        if request.url.path.endswith('/plan'):
            return httpx.Response(200, json={'status': 'fewer than 200 holdout rows' if failure == 'readiness' else 'ready'})
        return httpx.Response(200, json={'status': 'incomplete'})

    args = ops.parser().parse_args(['train', '--founder', 'placeholder-founder', '--submit'])
    if failure == 'authorization':
        with pytest.raises(RuntimeError):
            await ops.training(args, transport=httpx.MockTransport(respond))
    else:
        assert (await ops.training(args, transport=httpx.MockTransport(respond)))['ok'] is False
    assert '/v1/training/run' not in calls


@pytest.mark.asyncio
async def test_training_plan_is_read_only_and_never_exports_or_submits(monkeypatch):
    training_settings(monkeypatch)
    calls = []

    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={'status': 'ready'})

    result = await ops.training(ops.parser().parse_args(['train', '--founder', 'placeholder-founder']), transport=httpx.MockTransport(respond))
    assert result['ok'] and calls == ['/v1/training/run/plan']


@pytest.mark.asyncio
async def test_training_rejects_public_or_foreign_tank_before_authentication(monkeypatch):
    training_settings(monkeypatch)
    monkeypatch.setenv('TANK_URL', 'https://tank.tervory.com')
    with pytest.raises(RegionError):
        await ops.training(ops.parser().parse_args(['train', '--founder', 'placeholder-founder']))


def test_failed_command_receipt_never_prints_database_exception(monkeypatch, capsys, tmp_path):
    async def fault(args):
        raise RuntimeError('placeholder database private detail that must stay hidden')

    monkeypatch.setattr(ops, 'execute', fault)
    receipt = tmp_path / 'receipt.json'
    assert ops.main(['selftest', '--receipt', str(receipt)]) == 1
    assert 'private detail' not in capsys.readouterr().out
    assert json.loads(receipt.read_text())['error_type'] == 'RuntimeError'


def test_missing_settings_receipt_names_only(monkeypatch, capsys):
    monkeypatch.setenv('TANK_REGION', 'us-east')
    monkeypatch.delenv('TANK_URL', raising=False)
    monkeypatch.delenv('TANK_KEY', raising=False)
    assert ops.main(['selftest']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['needs_settings'] == ['TANK_URL', 'TANK_KEY']


def test_live_commands_refuse_fixture_mode(monkeypatch, capsys):
    monkeypatch.setenv('TANK_REGION', 'us-east')
    monkeypatch.setenv('POOL_USE_FIXTURES', 'true')
    assert ops.main(['sample', '--temporary']) == 1
    assert json.loads(capsys.readouterr().out)['error_type'] == 'ValueError'


@pytest.mark.asyncio
async def test_heartbeat_command_uses_the_existing_scheduled_memory_path(monkeypatch):
    from chassis.memory import MemoryClient
    monkeypatch.setenv('TANK_REGION', 'us-east')
    monkeypatch.setenv('TANK_KEY', 'placeholder')
    monkeypatch.delenv('POOL_USE_FIXTURES', raising=False)
    remember = AsyncMock(return_value={'seen': 9})
    monkeypatch.setattr(MemoryClient, 'remember', remember)
    result = await ops.execute(ops.parser().parse_args(['heartbeat']))
    assert result['ok'] and result['result']['heartbeats_seen'] == 9
    assert remember.await_args.args == ('00000000-0000-0000-0000-000000000001', 'zenas heartbeat', 'heartbeat', 'schedule:*/5')


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['load', 'nightly'])
async def test_source_commands_run_the_real_parser_store_and_replay(database, monkeypatch, command):
    from chassis import events

    from agents.zenas.pool import bulk, nightly
    monkeypatch.setenv('TANK_REGION', 'us-east')
    monkeypatch.setenv('TANK_URL', 'http://tank:8000')
    monkeypatch.setenv('TANK_KEY', 'placeholder')
    monkeypatch.delenv('POOL_USE_FIXTURES', raising=False)
    getter = AsyncMock(return_value=RECORDING.read_bytes())
    monkeypatch.setattr(bulk, 'urllib_get', getter)
    monkeypatch.setattr(nightly, 'urllib_get', getter)
    monkeypatch.setattr(events, 'publish', AsyncMock())
    jobs = []
    for module_name in ops.JOBS.values():
        module = __import__(module_name, fromlist=['run_scheduled'])
        scheduled = AsyncMock(return_value={'ok': True})
        monkeypatch.setattr(module, 'run_scheduled', scheduled)
        jobs.append(scheduled)

    @asynccontextmanager
    async def connect(**kwargs):
        async with database.acquire() as conn:
            yield conn

    monkeypatch.setattr(ops, 'connection', connect)
    args = ops.parser().parse_args([command, '--source', 'frcp', '--text-only'])
    first, second = await ops.execute(args), await ops.execute(args)
    assert first['ok'] and second['ok']
    async with database.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM zenas_pool.documents WHERE source_id='frcp'") == 1
        assert await conn.fetchval("SELECT text FROM zenas_pool.documents WHERE source_id='frcp'")
    assert getter.await_count == 2
    assert all(job.await_count == (2 if command == 'nightly' else 0) for job in jobs)


@pytest.mark.asyncio
async def test_training_receipt_exposes_config_gate_and_artifact_locations(monkeypatch):
    monkeypatch.setenv('TANK_REGION', 'za')
    monkeypatch.delenv('POOL_USE_FIXTURES', raising=False)
    monkeypatch.setattr(ops, 'training', AsyncMock(return_value={'ok': True}))
    result = await ops.execute(ops.parser().parse_args(['train', '--founder', 'placeholder-founder']))
    assert result['artifacts']['config'] == 'tank/training_jobs/train.py'
    assert result['artifacts']['qualification'] == {'method': 'exact_outcome', 'min_cases': 200, 'min_agreement': .99, 'auto_promote': False}
    assert result['artifacts']['results'] == ['evaluation.json', 'holdout.json', 'zenas-q4_k_m.gguf']


@pytest.mark.asyncio
async def test_frap_wave_uses_real_official_appendix_not_obsolete_dup3(database):
    blob = (RECORDING.parent / 'frap-rule1.html').read_bytes()
    assert hashlib.sha256(blob).hexdigest() == '91150beed747b6326a8aade72277ec1e6493b7e6f42fd01338416450a3bbb56e'

    async def get(url, headers):
        assert url.endswith('/USCODE-2023-title28-app.htm')
        return blob

    async with database.acquire() as conn:
        result = await ops.sample(conn, region='us-east', selected=ops.sources('us-east', ['frap']), http_get=get)
        assert result['sources'][0]['inserted'] == 1
        assert result['sources'][0]['documents'][0]['citation'] == 'Fed. R. App. P. 1'
        assert result['sources'][0]['replay_inserted'] == 0
        original = await conn.fetchrow("SELECT version_id,captured_at,effective_from,content_hash FROM zenas_pool.documents WHERE source_id='frap'")
        await conn.execute("UPDATE zenas_pool.documents SET url='https://www.govinfo.gov/content/pkg/USCODE-2023-title28/html/USCODE-2023-title28-app-federalru-dup3-rule1.htm' WHERE source_id='frap'")
        replayed = await ops.sample(conn, region='us-east', selected=ops.sources('us-east', ['frap']), http_get=get)
        assert replayed['sources'][0]['inserted'] == 0
        assert replayed['sources'][0]['documents'][0]['url'].endswith('/USCODE-2023-title28-app.htm')
        assert await conn.fetchrow("SELECT version_id,captured_at,effective_from,content_hash FROM zenas_pool.documents WHERE source_id='frap'") == original


@pytest.mark.asyncio
@pytest.mark.parametrize('bill', [None, '-1', 'NaN', 'Infinity'])
async def test_training_receipt_requires_actual_finite_nonnegative_bill(monkeypatch, bill):
    training_settings(monkeypatch)
    args = ops.parser().parse_args(['train', '--founder', 'placeholder-founder', '--job-id', 'offline-job'])
    args.billed_usd = bill
    with pytest.raises(ValueError):
        await ops.training(args, transport=httpx.MockTransport(lambda _: pytest.fail('invalid bill sent')))


@pytest.mark.asyncio
async def test_training_receipt_uses_existing_billing_and_holdout_reconciliation(monkeypatch):
    training_settings(monkeypatch)

    def respond(request):
        assert request.url.path == '/v1/training/receipt'
        assert json.loads(request.content) == {'job_id': 'offline-job', 'billed_usd': '0.91'}
        return httpx.Response(200, json={'status': 'complete', 'ledger_id': 'offline-ledger'})

    args = ops.parser().parse_args(['train', '--founder', 'placeholder-founder', '--job-id', 'offline-job', '--billed-usd', '0.91'])
    assert (await ops.training(args, transport=httpx.MockTransport(respond)))['ok']


@pytest.mark.asyncio
async def test_one_command_can_collect_missing_court_dataset_then_recheck_readiness(monkeypatch):
    training_settings(monkeypatch)
    plans, calls = 0, []

    def respond(request):
        nonlocal plans
        calls.append(request.url.path)
        if request.url.path.endswith('/plan'):
            plans += 1
            return httpx.Response(200, json={'status': 'fewer than 200 holdout rows' if plans == 1 else 'ready'})
        return httpx.Response(200, json={'status': 'complete' if request.url.path.endswith('/export') else 'running'})

    result = await ops.training(ops.parser().parse_args(['train', '--founder', 'placeholder-founder', '--submit']),
                                transport=httpx.MockTransport(respond))
    assert result['ok'] and result['court_text']['split'] == 2000
    ops.prepare_court_text.assert_awaited_once_with('za')
    assert calls == ['/v1/training/run/plan', '/v1/training/jobs/export', '/v1/training/run/plan', '/v1/training/run']


@pytest.mark.asyncio
async def test_court_text_preparation_drains_all_acknowledged_batches(monkeypatch):
    from agents.zenas.core import judgment_training
    monkeypatch.setenv('TANK_KEY', 'placeholder')
    split = AsyncMock(side_effect=[{'split': 500, 'excluded': 0}, {'split': 400, 'excluded': 100}, {'split': 0, 'excluded': 0}])
    monkeypatch.setattr(judgment_training, 'run_scheduled', split)

    @asynccontextmanager
    async def connect(**kwargs):
        yield None

    monkeypatch.setattr(ops, 'connection', connect)
    assert await ops.prepare_court_text('za') == {'split': 900, 'excluded': 100}
    assert split.await_count == 3 and split.await_args.kwargs == {'region': 'za'}
