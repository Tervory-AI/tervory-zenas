"""Exercise projected shared functions; neither Tank nor its vendor is modified."""
import ast
import asyncio
import hashlib
import json
import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from types import CodeType, FunctionType, ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
PLATFORM = UUID(int=1)


def projected(name):
    base = ROOT / 'vendor/tervory-tank'
    if not base.is_dir():
        base = ROOT  # The check runner reconstructs Tank's original layout.
    path = base / name
    if path.is_file():
        original = path.read_text().splitlines(keepends=True)
    else:
        assert name == 'tank/training_jobs/requirements.txt'
        original = []
    patch = (ROOT / 'tank-patches/finish.patch').read_text()
    section = patch.split(f'diff --git a/{name} b/{name}\n', 1)[1].split('diff --git ', 1)[0]
    result, cursor = [], 0
    for hunk in re.split(r'(?m)^@@ ', section)[1:]:
        header, body = hunk.split('\n', 1)
        start = max(0, int(re.match(r'-(\d+)', header)[1]) - 1)
        result.extend(original[cursor:start])
        cursor = start
        for line in body.splitlines(keepends=True):
            if line == '\n':
                line = ' \n'  # Git accepts a bare blank context line.
            if line.startswith((' ', '-')):
                assert original[cursor] == line[1:]
                cursor += 1
            if line.startswith((' ', '+')):
                result.append(line[1:])
    result.extend(original[cursor:])
    return ''.join(result)


def shared_functions(*names, runtime):
    tree = ast.parse(projected('tank/training.py'))
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    for node in selected:
        node.decorator_list = []
    namespace = {'Any': Any, 'TrainingRunRequest': SimpleNamespace, 'runtime': lambda: runtime,
                 'PLATFORM_TENANT': PLATFORM, 'OFFICIAL_ZA_CASE_SOURCES': {'concourt', 'sca'},
                 'os': os, 're': re, 'date': date, 'datetime': datetime, 'timezone': timezone,
                 'httpx': httpx, 'obj': lambda item: json.loads(item) if isinstance(item, str) else item,
                 'HTTPException': HTTPException}
    return functions(selected, namespace)


def functions(nodes, namespace):
    # Load trusted projected function bodies without applying a patch to any file
    # or importing Tank's service/module startup effects.
    code = compile(ast.Module(body=nodes, type_ignores=[]), 'projected_training.py', 'exec')
    bodies = {value.co_name: value for value in code.co_consts if isinstance(value, CodeType)}
    for node in nodes:
        defaults = tuple(ast.literal_eval(value) for value in node.args.defaults)
        function = FunctionType(bodies[node.name], namespace, node.name, defaults or None)
        function.__kwdefaults__ = {key.arg: ast.literal_eval(value)
                                  for key, value in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True)
                                  if value is not None}
        namespace[node.name] = function
    return namespace


def trainer():
    from tank.training_jobs import train
    tree = ast.parse(projected('tank/training_jobs/train.py'))
    return functions([node for node in tree.body if isinstance(node, ast.FunctionDef)],
                     {'hashlib': hashlib, 'json': json, 'os': os, 'Path': Path, 're': re, 'OUTCOMES': train.OUTCOMES})


@pytest.mark.asyncio
async def test_readiness_counts_only_this_regions_official_court_rows(database, monkeypatch):
    namespace = shared_functions('_training_readiness', runtime=SimpleNamespace(box_region=lambda: 'za'))
    monkeypatch.setenv('HF_TOKEN', 'placeholder')
    monkeypatch.setenv('HF_ORG', 'placeholder-org')
    async with database.acquire() as conn:
        await conn.execute('''CREATE SCHEMA tank;
            CREATE TABLE tank.training_pairs(tenant_id uuid,agent text,region text,active boolean DEFAULT true,
            public boolean DEFAULT true,client_matter boolean DEFAULT false,source text,source_ref text,
            task_type text DEFAULT 'zenas.reasoning',holdout boolean DEFAULT false);
            CREATE TABLE tank.training_jobs(tenant_id uuid,ledger_id uuid);''')
        for region, source, prefix, client in [('us-east', 'court_text', 'concourt', False),
                                             ('za', 'gold', 'concourt', False),
                                             ('za', 'court_text', 'unofficial', False),
                                             ('za', 'court_text', 'concourt', True)]:
            await conn.execute('''INSERT INTO tank.training_pairs(tenant_id,agent,region,source,source_ref,client_matter,holdout)
                SELECT $1,'zenas',$2,$3,$4||':'||i::text,$5,true FROM generate_series(1,2000) i''',
                PLATFORM, region, source, prefix, client)
        body = SimpleNamespace(agent='zenas')
        assert (await namespace['_training_readiness'](conn, body))['count'] == 0
        await conn.execute('''INSERT INTO tank.training_pairs(tenant_id,agent,region,source,source_ref,holdout)
            SELECT $1,'zenas','za','court_text','concourt:'||i::text,i<=200 FROM generate_series(1,2000) i''', PLATFORM)
        assert await namespace['_training_readiness'](conn, body) is None


@pytest.mark.asyncio
async def test_export_refreshes_old_daily_snapshot_and_excludes_retrieval_gold(monkeypatch):
    class Conn:
        fetchval = AsyncMock(return_value=True)  # Today's old export already exists.
        execute = AsyncMock()

        async def fetch(self, sql, *args):
            if 'SELECT DISTINCT tenant_id' in sql:
                return [{'tenant_id': PLATFORM}]
            held = 'p.active AND p.holdout' in sql
            return [row(held=held), row(held=held, source='gold', task_type='zenas.retrieve')]

    conn = Conn()

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield conn

    def row(*, held, source='court_text', task_type='zenas.reasoning'):
        return {'tenant_id': PLATFORM, 'region': 'za', 'agent': 'zenas', 'public': True,
                'client_matter': False, 'active': True, 'holdout': held, 'source': source,
                'source_ref': 'concourt:offline-case', 'task_type': task_type, 'batch_id': None,
                'currency': None, 'unit': None, 'input': {'facts': 'offline court text'}, 'output': {'outcome': 'dismissed'}}

    import gold
    monkeypatch.setattr(gold, 'AGENTS', ('base', 'zenas'))
    monkeypatch.setenv('HF_TOKEN', 'placeholder')
    monkeypatch.setenv('HF_ORG', 'placeholder-org')
    namespace = shared_functions('court_training_row', 'exportable', 'export_once',
                                 runtime=SimpleNamespace(pool=lambda: Pool(), box_region=lambda: 'za'))
    namespace['_batch_removed'] = AsyncMock(return_value=False)
    namespace['StorageError'] = type('StorageError', (Exception,), {})
    upload = AsyncMock()
    namespace['upload_dataset'] = upload
    result = await namespace['export_once'](day=date(2026, 11, 6), only_agent='zenas')
    assert result == {'status': 'complete', 'exported': 1}
    payload, holdouts = upload.await_args.args[4], upload.await_args.args[6]
    assert len(payload) == len(holdouts) == 1
    assert payload[0]['task_type'] == holdouts[0]['task_type'] == 'zenas.reasoning'
    assert payload[0]['holdout'] is False and holdouts[0]['holdout'] is True
    assert 'tenant_id' not in payload[0] and 'tenant_id' not in holdouts[0]
    assert upload.await_count == 1


@pytest.mark.asyncio
async def test_dedicated_court_export_does_not_collect_other_rooms():
    namespace = shared_functions('export_job', runtime=None)
    collect, export = AsyncMock(), AsyncMock(return_value={'status': 'complete'})
    namespace.update(collect_once=collect, export_once=export)
    assert await namespace['export_job']('zenas') == {'status': 'complete'}
    collect.assert_not_awaited()
    export.assert_awaited_once_with(only_agent='zenas')
    await namespace['export_job']()
    collect.assert_awaited_once()
    assert export.await_args.kwargs == {'only_agent': None}
    with pytest.raises(HTTPException) as exc:
        await namespace['export_job']('offline-invalid-agent')
    assert exc.value.status_code == 422


def hub(monkeypatch, tmp_path, rows):
    module, errors = ModuleType('huggingface_hub'), ModuleType('huggingface_hub.errors')
    errors.RepositoryNotFoundError = type('RepositoryNotFoundError', (Exception,), {})
    errors.EntryNotFoundError = type('EntryNotFoundError', (Exception,), {})
    path = tmp_path / 'snapshot.jsonl'
    path.write_text('\n'.join(json.dumps(row) for row in rows))
    calls = []

    def download(repo, name, **kwargs):
        assert '/za/' in name
        assert kwargs['revision'] == 'offline-pinned-revision'
        calls.append(name)
        return str(path)

    module.hf_hub_download = download
    monkeypatch.setitem(sys.modules, 'huggingface_hub', module)
    monkeypatch.setitem(sys.modules, 'huggingface_hub.errors', errors)
    monkeypatch.setenv('TRAIN_AGENT', 'zenas')
    api = SimpleNamespace(dataset_info=lambda _: SimpleNamespace(private=True, sha='offline-pinned-revision'))
    return api, calls


def court_row(number, *, held=False):
    return {'region': 'za', 'public': True, 'client_matter': False, 'scope': 'platform',
            'holdout': held, 'active': True, 'task_type': 'zenas.reasoning',
            'input': {'facts': f'offline court fixture {number}'}, 'output': {'outcome': 'dismissed'}}


def test_gpu_reads_only_private_za_court_snapshot(monkeypatch, tmp_path):
    namespace = trainer()
    api, calls = hub(monkeypatch, tmp_path, [court_row(1)])
    assert len(namespace['load_snapshots'](api, 'placeholder-org', '2026-11-06')) == 1
    assert calls == ['snapshots/za/2026-11-06.jsonl']


def test_gpu_refuses_old_retrieval_snapshot_before_trainer(monkeypatch, tmp_path):
    row = {**court_row(1), 'task_type': 'zenas.retrieve', 'output': {'citations': ['offline']}}
    api, _ = hub(monkeypatch, tmp_path, [row])
    with pytest.raises(ValueError, match='court outcomes'):
        trainer()['load_snapshots'](api, 'placeholder-org', '2026-11-06')


@pytest.mark.parametrize('change', [{'scope': 'client'}, {'tenant_id': str(PLATFORM)}, {'active': False},
                                   {'region': 'us-east'}, {'task_type': 'zenas.retrieve'}, {'output': {}}])
def test_gpu_refuses_unsafe_or_non_court_holdouts(monkeypatch, tmp_path, change):
    rows = [court_row(i, held=True) for i in range(200)]
    rows[0].update(change)
    api, _ = hub(monkeypatch, tmp_path, rows)
    with pytest.raises(ValueError, match='Unsafe holdout'):
        trainer()['load_holdouts'](api, 'placeholder-org', '2026-11-06')


def test_gpu_requires_200_distinct_holdouts(monkeypatch, tmp_path):
    rows = [court_row(i, held=True) for i in range(199)]
    rows.append(rows[0])
    api, _ = hub(monkeypatch, tmp_path, rows)
    with pytest.raises(ValueError, match='200 distinct'):
        trainer()['load_holdouts'](api, 'placeholder-org', '2026-11-06')


def test_gpu_accepts_200_distinct_public_court_holdouts(monkeypatch, tmp_path):
    api, calls = hub(monkeypatch, tmp_path, [court_row(i, held=True) for i in range(200)])
    assert len(trainer()['load_holdouts'](api, 'placeholder-org', '2026-11-06')) == 200
    assert calls == ['holdouts/za/2026-11-06.jsonl']


def test_overlap_is_refused_before_trainer_or_model_construction(monkeypatch):
    namespace = trainer()
    imports = {
        'datasets': SimpleNamespace(Dataset=SimpleNamespace()),
        'peft': SimpleNamespace(LoraConfig=SimpleNamespace()),
        'trl': SimpleNamespace(SFTConfig=SimpleNamespace(), SFTTrainer=lambda **kw: pytest.fail('trainer started')),
        'huggingface_hub': SimpleNamespace(HfApi=lambda **kw: SimpleNamespace(model_info=lambda _: SimpleNamespace(private=True))),
    }
    for name, value in imports.items():
        monkeypatch.setitem(sys.modules, name, value)
    for name, value in {'HF_TOKEN': 'placeholder', 'HF_ORG': 'placeholder-org', 'OUTPUT_REPO': 'placeholder-output',
                        'TRAIN_DAY': '2026-11-06', 'TRAIN_AGENT': 'zenas'}.items():
        monkeypatch.setenv(name, value)
    namespace['load_snapshots'] = lambda *args: [court_row(1)]
    namespace['load_holdouts'] = lambda *args: [court_row(1, held=True)]
    with pytest.raises(ValueError, match='overlap'):
        namespace['main']()


def test_hf_jobs_client_is_isolated_from_the_cpu_router_dependencies():
    dockerfile = projected('tank/Dockerfile')
    assert 'python -m venv /opt/hf-jobs' in dockerfile
    assert '/opt/hf-jobs/bin/pip install --no-cache-dir -r /tmp/hf-jobs-requirements.txt' in dockerfile
    assert 'ENV HF_JOBS_PYTHON=/opt/hf-jobs/bin/python' in dockerfile
    assert projected('tank/training_jobs/requirements.txt').splitlines() == ['huggingface-hub==1.7.2', 'asyncpg==0.30.0']


@pytest.mark.asyncio
async def test_training_subprocess_uses_jobs_sdk_without_changing_parent_offline_mode(monkeypatch):
    namespace = shared_functions('_training_subprocess', runtime=None)
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'{"status":"ready"}\n', b'')))
    create = AsyncMock(return_value=process)
    namespace.update(asyncio=SimpleNamespace(create_subprocess_exec=create, subprocess=asyncio.subprocess),
                     sys=sys, Path=Path, __file__=str(ROOT / 'tank/training.py'), json=json)
    monkeypatch.setenv('HF_JOBS_PYTHON', '/opt/hf-jobs/bin/python')
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    result = await namespace['_training_subprocess']('--agent', 'zenas')
    assert result == {'status': 'ready'}
    assert create.await_args.args == ('/opt/hf-jobs/bin/python', str(ROOT / 'tank/training_jobs/run.py'), '--agent', 'zenas', '--json')
    assert create.await_args.kwargs['env']['HF_HUB_OFFLINE'] == '0'
    assert os.environ['HF_HUB_OFFLINE'] == '1'
