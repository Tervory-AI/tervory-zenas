"""No live GPU, HF credentials or client exports are needed to validate training."""
from decimal import Decimal
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import uuid
import pytest
from fastapi import HTTPException
import training
from gold import PLATFORM_TENANT
from test_train1 import database as _database

database = _database
ROOT = Path(__file__).resolve().parents[1]

def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tank/training_jobs' / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

@pytest.mark.parametrize('rate,minutes,cap', [('1',51,'50'), ('NaN',2,'50'), ('1',-1,'50'), ('1',2,'-1')])
def test_refuses_bad_or_over_budget_job(rate, minutes, cap):
    with pytest.raises(ValueError):
        load('run').estimate(Decimal(rate), minutes, Decimal(cap))


def test_accepts_capped_estimate():
    assert load('run').estimate(Decimal('0.025'), 120, Decimal('50')) == Decimal('3')


def test_zenas_uses_allowlisted_smallest_a10g_that_fits():
    mod = load('run')
    assert mod.HARDWARE == frozenset({'a10g-small', 'a10g-large'})
    assert mod.training_hardware('zenas') == 'a10g-small'
    with pytest.raises(ValueError, match='Unsupported training agent'):
        mod.training_hardware('base')
    trainer = load('train')
    assert trainer.context_length('') == 1024
    assert trainer.context_length('zenas') == 4096


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('values', 'configured', 'status'),
    [
        ([1999, 200], True, 'fewer than 2,000 public zenas training rows'),
        ([2000, 199], True, 'fewer than 200 holdout rows'),
        ([2000, 200, True], True, 'reconcile the previous training job'),
        ([2000, 200, False], False, 'Hugging Face training is not configured'),
    ],
)
async def test_zenas_training_run_plain_refusals(monkeypatch, values, configured, status):
    from unittest.mock import AsyncMock
    conn = SimpleNamespace(fetchval=AsyncMock(side_effect=values))
    if configured:
        monkeypatch.setenv('HF_TOKEN', 'placeholder-token')
        monkeypatch.setenv('HF_ORG', 'placeholder-org')
    else:
        monkeypatch.delenv('HF_TOKEN', raising=False)
        monkeypatch.delenv('HF_ORG', raising=False)
    body = training.TrainingRunRequest(agent='zenas', minutes=120)
    assert (await training._training_readiness(conn, body))['status'] == status


@pytest.mark.asyncio
async def test_training_run_and_receipt_refuse_tenant_admin_before_provider(monkeypatch):
    actor = training.Reviewer('placeholder-admin', uuid.uuid4(), False)
    with pytest.raises(HTTPException) as run_refused:
        await training.training_run(training.TrainingRunRequest(agent='zenas', minutes=120), actor)
    with pytest.raises(HTTPException) as receipt_refused:
        await training.training_receipt(
            training.TrainingReceiptRequest(job_id='placeholder-job', billed_usd=1), actor,
        )
    assert run_refused.value.status_code == receipt_refused.value.status_code == 403


@pytest.mark.asyncio
async def test_zenas_plan_reads_live_hf_price(monkeypatch):
    monkeypatch.setenv('HF_TOKEN', 'placeholder-token')
    monkeypatch.setenv('HF_TRAIN_MAX_USD', '50')
    async def child(*args):
        assert args == ('--agent', 'zenas', '--minutes', '120')
        return {'status': 'ready', 'cost_usd': '3', 'hardware': 'a10g-small'}
    monkeypatch.setattr(training, '_training_subprocess', child)
    result = await training._training_quote(training.TrainingRunRequest(agent='zenas', minutes=120))
    assert result == {'status': 'ready', 'cost_usd': '3', 'hardware': 'a10g-small'}


@pytest.mark.asyncio
async def test_tank_stays_hf_offline_while_training_child_is_online(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'{"status":"ready"}\n', b'')))
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(training.asyncio, 'create_subprocess_exec', launch)
    assert await training._training_subprocess('--agent', 'zenas') == {'status': 'ready'}
    assert os.environ['HF_HUB_OFFLINE'] == '1'
    assert launch.await_args.kwargs['env']['HF_HUB_OFFLINE'] == '0'

@pytest.mark.parametrize('change', [{'tenant_id': uuid.uuid4()}, {'public': False}, {'client_matter': True},
                                    {'region': 'za'}, {'holdout': True}, {'active': False}])
def test_private_or_cross_region_never_exports(change, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(training, "runtime", lambda: SimpleNamespace(box_region=lambda: "us-east"))
    row = dict(tenant_id=PLATFORM_TENANT, public=True, client_matter=False, region='us-east', holdout=False, active=True)
    assert training.exportable(row)
    assert not training.exportable(row | change)

@pytest.mark.parametrize('change', [{'scope': 'client'}, {'public': False}, {'public': None}, {'holdout': True}, {'holdout': None}, {'client_matter': True}, {'region': 'unknown'}, {'tenant_id': 'any'}])
def test_training_aborts_if_export_contains_one_private_row(change):
    row = dict(public=True, client_matter=False, scope='platform', region='us-east', holdout=False,
               active=True, task_type='base.fixture', input={}, output={})
    load('train').validate_rows([row])
    with pytest.raises(ValueError):
        load('train').validate_rows([row, row | change])

@pytest.mark.asyncio
async def test_billed_receipt_is_recorded_once_and_estimate_is_not_spend(database):
    pool, _ = database
    mod = load('run')
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO tank.training_jobs(id,tenant_id,region,model_repo,estimated_usd) VALUES ('fixture',$1,'us-east','fixture/model',50)", PLATFORM_TENANT)
        assert await conn.fetchval('SELECT count(*) FROM tank.ledger') == 0
        await mod.record_receipt(conn, 'fixture', Decimal('2.125'))
        await mod.record_receipt(conn, 'fixture', Decimal('2.125'))
        assert await conn.fetchval('SELECT sum(cost_usd) FROM tank.ledger') == Decimal('2.125')
        assert await conn.fetchval('SELECT count(*) FROM tank.ledger') == 1
        with pytest.raises(ValueError):
            await mod.record_receipt(conn, 'fixture', Decimal('3'))
        with pytest.raises(ValueError):
            await mod.record_receipt(conn, 'unknown', Decimal('3'))

@pytest.mark.asyncio
async def test_actual_export_payload_cannot_contain_private_rows(database, monkeypatch):
    import secrets
    pool, _ = database
    monkeypatch.setenv('HF_TOKEN', secrets.token_urlsafe(24))
    monkeypatch.setenv('HF_ORG', 'fixture')
    async with pool.acquire() as conn:
        for n, (tenant, public, client) in enumerate([(PLATFORM_TENANT, True, False), (PLATFORM_TENANT, False, False), (uuid.uuid4(), True, False), (PLATFORM_TENANT, True, True)]):
            await training.save_pair(conn, tenant=tenant, agent='base', task='base.fixture', value={'n': n}, output={'ok': True},
                                     approved_by='fixture', source='gold', ref=str(n), public=public, client_matter=client)
    captured = []
    async def upload(client, org, agent, day, rows, region, holdouts=None):
        load('train').validate_rows(rows)  # Any forbidden row fails the export test.
        captured.extend(rows)
    monkeypatch.setattr(training, 'upload_dataset', upload)
    result = await training.export_once()
    assert result['exported'] == 1
    assert [r['input'] for r in captured] == [{'n': 0}]

@pytest.mark.asyncio
@pytest.mark.parametrize('submit,cap,expected_jobs,agent,region', [
    (False,'50',0,'','us-east'), (True,'50',1,'','us-east'),
    (True,'50',1,'zenas','za'), (True,'1',0,'zenas','za')])
async def test_launcher_estimates_before_any_submission_and_passes_secrets_only(monkeypatch, capsys, submit, cap, expected_jobs, agent, region):
    import secrets
    import sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    token = secrets.token_urlsafe(24)
    monkeypatch.setenv('HF_TOKEN', token)
    monkeypatch.setenv('HF_ORG', 'fixture')
    monkeypatch.setenv('HF_TRAIN_MAX_USD', cap)
    monkeypatch.setenv('TANK_REGION', region)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://fixture.invalid/fixture')
    jobs, repos = [], []
    def launch(script, **kwargs):
        assert kwargs['timeout'] == 7200
        assert kwargs['secrets'] == {'HF_TOKEN': token}
        assert token not in str(kwargs['env'])
        assert 'DATABASE_URL' not in kwargs['secrets']
        assert Path(script).name == 'train.py'
        assert kwargs['env']['TRAIN_AGENT'] == agent
        jobs.append(kwargs)
        return SimpleNamespace(id='fixture-job')
    api = SimpleNamespace(list_jobs_hardware=lambda: [SimpleNamespace(name='a10g-small',unit_label='minute',unit_cost_micro_usd=16667)],
                          create_repo=lambda **kw: repos.append(kw), model_info=lambda repo: SimpleNamespace(private=True), run_uv_job=launch)
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(HfApi=lambda **kw: api))
    import asyncpg
    conn = SimpleNamespace(execute=AsyncMock(), fetchval=AsyncMock(return_value=False), fetch=AsyncMock(return_value=[]), close=AsyncMock())
    monkeypatch.setattr(asyncpg, 'connect', AsyncMock(return_value=conn))
    args = SimpleNamespace(receipt=None, minutes=120, submit=submit, day='2026-10-20', agent=agent)
    if cap == '1':
        with pytest.raises(ValueError):
            await load('run').run(args)
    else:
        await load('run').run(args)
    assert len(jobs) == expected_jobs
    assert len(repos) == expected_jobs
    assert all(r['private'] is True for r in repos)
    assert token not in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [{'public': False}, {'public': None}, {'holdout': True}, {'holdout': None},
                                    {'scope': 'client'}, {'tenant_id': 'private'}, {'region': 'za'}])
async def test_upload_rejects_unsafe_rows_before_network(change, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(training, "runtime", lambda: SimpleNamespace(box_region=lambda: "us-east"))
    from unittest.mock import AsyncMock
    client = AsyncMock()
    row = dict(public=True, holdout=False, active=True, client_matter=False, scope='platform',
               region='us-east', task_type='base.fixture', input={}, output={})
    with pytest.raises(ValueError, match='Unsafe training export'):
        await training.upload_dataset(client, 'fixture', 'base', '2026-10-20', [row, row | change])
    client.post.assert_not_called()
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_ledger_and_package_pairs_stay_private_after_upgrade(database, monkeypatch):
    import secrets
    pool, _ = database
    monkeypatch.setenv('HF_TOKEN', secrets.token_urlsafe(24))
    monkeypatch.setenv('HF_ORG', 'fixture')
    async with pool.acquire() as conn:
        # Reproduce the pre-upgrade table only in this disposable test database.
        await conn.execute('ALTER TABLE tank.training_pairs DROP COLUMN public')
        for source in ('ledger', 'package'):
            for held in (False, True):
                await conn.execute("""INSERT INTO tank.training_pairs
                    (tenant_id,agent,task_type,region,input,output,approved_by,source,source_ref,holdout)
                    VALUES ($1,'base','base.fixture','us-east',$2::jsonb,'{}','fixture',$3,$4,$5)""",
                    PLATFORM_TENANT, training.encoded({'source': source, 'held': held}), source, str(held), held)
        schema = (ROOT / 'tank/schema.sql').read_text()
        await conn.execute(schema)
        await conn.execute(schema)
        legacy = await conn.fetch('SELECT public,holdout FROM tank.training_pairs')
        assert len(legacy) == 4 and all(row['public'] is False for row in legacy)
        assert sum(row['holdout'] for row in legacy) == 2
        # Even explicitly public legacy provenance must still exclude holdouts.
        for source in ('ledger', 'package'):
            for held in (False, True):
                await training.save_pair(conn, tenant=PLATFORM_TENANT, agent='base', task='base.fixture',
                    value={'public_source': source, 'held': held}, output={}, approved_by='fixture',
                    source=source, ref='public-' + str(held), public=True, holdout=held)
    captured = []
    async def upload(client, org, agent, day, rows, region, holdouts=None):
        load('train').validate_rows(rows)
        captured.extend(rows)
    monkeypatch.setattr(training, 'upload_dataset', upload)
    result = await training.export_once()
    assert result['exported'] == 2
    assert {row['input']['public_source'] for row in captured} == {'ledger', 'package'}
    assert all(row['public'] is True and row['holdout'] is False and row['input']['held'] is False for row in captured)


@pytest.mark.asyncio
@pytest.mark.parametrize('failed_submission', [False, True])
async def test_reservation_blocks_second_submission_even_after_hf_error(database, monkeypatch, failed_submission):
    import asyncpg
    import secrets
    import sys
    from types import SimpleNamespace
    from test_train1 import URL
    pool, _ = database
    async with pool.acquire() as conn:
        dbname = await conn.fetchval('SELECT current_database()')
    real_connect = asyncpg.connect
    async def connect(_url):
        return await real_connect(URL, database=dbname)
    monkeypatch.setattr(asyncpg, 'connect', connect)
    for name, value in {'HF_TOKEN': secrets.token_urlsafe(24), 'HF_ORG': 'fixture',
                        'TANK_REGION': 'us-east', 'DATABASE_URL': URL}.items():
        monkeypatch.setenv(name, value)
    jobs = []
    def submit(*args, **kwargs):
        jobs.append(kwargs)
        if failed_submission:
            raise RuntimeError('Uncertain submission')
        return SimpleNamespace(id='fixture-job')
    api = SimpleNamespace(list_jobs_hardware=lambda: [SimpleNamespace(name='a10g-small', unit_label='minute', unit_cost_micro_usd=16667)],
                          create_repo=lambda **kw: None, model_info=lambda repo: SimpleNamespace(private=True), run_uv_job=submit)
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(HfApi=lambda **kw: api))
    args = SimpleNamespace(receipt=None, minutes=120, submit=True, day='2026-10-20')
    if failed_submission:
        with pytest.raises(RuntimeError, match='Uncertain submission'):
            await load('run').run(args)
    else:
        await load('run').run(args)
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT * FROM tank.training_jobs')
        assert len(rows) == 1 and rows[0]['ledger_id'] is None and rows[0]['estimated_usd'] > 0
        assert rows[0]['id'].startswith('pending-') if failed_submission else rows[0]['id'] == 'fixture-job'
        assert await conn.fetchval('SELECT pg_try_advisory_lock(871228)') is True
        await conn.execute('SELECT pg_advisory_unlock(871228)')
    with pytest.raises(ValueError, match='Reconcile the previous training job'):
        await load('run').run(args)
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_public_za_decided_case_exports_but_client_matter_does_not(database, monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(training, "archive_training_snapshot", AsyncMock())
    import base64
    import gold
    import httpx
    import json
    import secrets
    pool, rt = database
    rt.box_region = lambda: 'za'
    monkeypatch.setenv('HF_TOKEN', secrets.token_urlsafe(24))
    monkeypatch.setenv('HF_ORG', 'fixture')
    monkeypatch.setattr(gold, 'is_holdout', lambda *args: False)
    async with pool.acquire() as conn:
        await training.save_pair(conn, tenant=PLATFORM_TENANT, agent='zenas', task='zenas.reasoning',
            value={'facts': 'Public ZA decision', 'issues': 'placeholder issue'},
            output={'reasoning': 'placeholder reasons', 'order': 'The application is granted.',
                    'outcome': 'granted', 'costs': 'without costs'},
            approved_by='court decision', source='court_text', ref='concourt:placeholder-version',
            public=True, client_matter=False, region='za')
        await training.save_pair(conn, tenant=uuid.uuid4(), agent='zenas', task='zenas.reasoning',
            value={'text': 'Client matter'}, output={}, approved_by='fixture', source='zenas', ref='client',
            public=True, client_matter=True)
    captured = []
    def respond(request):
        if request.url.path.endswith('/commit/main'):
            ops = [json.loads(line) for line in request.content.splitlines()]
            file = ops[1]['value']
            assert file['path'].startswith('snapshots/za/')
            captured.extend(json.loads(line) for line in base64.b64decode(file['content']).splitlines())
        return httpx.Response(200, json={'private': True})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(training.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    assert (await training.export_once())['exported'] == 1
    assert captured[0]['input'] == {'facts': 'Public ZA decision', 'issues': 'placeholder issue'}
    assert captured[0]['region'] == 'za'
    load('train').validate_rows(captured)


@pytest.mark.parametrize("missing_us", [True, False])
def test_trainer_reads_both_regions_even_when_us_snapshot_is_missing(monkeypatch, tmp_path, missing_us):
    import json
    import sys
    from types import SimpleNamespace
    class MissingRepo(Exception):
        pass
    class MissingEntry(Exception):
        pass
    seen = []
    def info(dataset):
        if not dataset.endswith('-zenas'):
            raise MissingRepo()
        return SimpleNamespace(private=True, sha='pinned')
    def download(dataset, filename, **kwargs):
        seen.append(filename)
        assert kwargs['revision'] == 'pinned'
        if '/us-east/' in filename and missing_us:
            raise MissingEntry()
        region = filename.split('/')[1]
        path = tmp_path / 'za.jsonl'
        path.write_text(json.dumps(dict(public=True, client_matter=False, scope='platform', region=region,
                                       holdout=False, active=True, task_type='zenas.reasoning', input={}, output={})))
        return str(path)
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(hf_hub_download=download))
    monkeypatch.setitem(sys.modules, 'huggingface_hub.errors', SimpleNamespace(RepositoryNotFoundError=MissingRepo, EntryNotFoundError=MissingEntry))
    rows = load('train').load_snapshots(SimpleNamespace(dataset_info=info), 'fixture', '2026-10-20')
    assert seen == ['snapshots/us-east/2026-10-20.jsonl', 'snapshots/za/2026-10-20.jsonl']
    assert [row['region'] for row in rows] == (['za'] if missing_us else ['us-east', 'za'])
