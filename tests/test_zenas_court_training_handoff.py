"""Actual nightly splitter -> authenticated Tank writer -> save_pair, without I/O."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
import json

from fastapi import FastAPI
import httpx
import pytest

import training
from agents.zenas.core import judgment_training
from test_zenas_judgment_training import HEADED


class Connection:
    def __init__(self):
        self.writes = []

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self, **_options):
        yield self

    async def execute(self, sql, *args):
        self.writes.append((sql, args))
        return 'INSERT 0 1'

    async def fetchval(self, sql, *args):
        assert sql in {'SELECT pg_try_advisory_lock($1)', 'SELECT pg_advisory_unlock($1)'}
        assert len(args) == 1 and isinstance(args[0], int)
        return True

    async def fetch(self, sql, sources, cap):
        assert 'd.source_id=ANY($1::text[])' in sql and cap == 500
        assert set(sources) == training.OFFICIAL_ZA_CASE_SOURCES
        return [{'source_id': 'concourt', 'document_id': 'fixture-document',
                 'version_id': 'fixture-version', 'title': 'Civil supply dispute', 'text': HEADED}]


@pytest.mark.asyncio
async def test_splitter_reaches_writer_with_owning_room_key(monkeypatch):
    room_key = 'fixture-zenas-room-key'
    monkeypatch.setenv('TANK_KEY', room_key)
    monkeypatch.setenv('TANK_KEY_ZENAS', room_key)
    from tenant_db import TenantPool, bind_tenant_endpoints
    room, tank = Connection(), Connection()
    scoped_pool = TenantPool(tank)
    monkeypatch.setattr(training, 'runtime', lambda: SimpleNamespace(box_region=lambda: 'za', pool=lambda: scoped_pool))
    app = FastAPI()
    app.include_router(training.router)
    bind_tenant_endpoints(app)
    client = httpx.AsyncClient
    monkeypatch.setattr(judgment_training.httpx, 'AsyncClient',
                        lambda **kwargs: client(**kwargs, transport=httpx.ASGITransport(app=app)))
    assert await judgment_training.run_scheduled(room, region='za') == {'split': 1, 'excluded': 0}
    saved = [args for sql, args in tank.writes if 'INSERT INTO tank.training_pairs' in sql]
    assert len(saved) == 1
    assert any("set_config('app.tenant_id'" in sql and args == (str(training.PLATFORM_TENANT),)
               for sql, args in tank.writes)
    pair = saved[0]
    assert pair[:4] == (training.PLATFORM_TENANT, 'zenas', 'zenas.reasoning', 'za')
    assert json.loads(pair[4]) == {'facts': 'The placeholder supplier delivered machinery.',
                                  'issues': 'Whether the placeholder agreement was binding.'}
    assert json.loads(pair[5]) == {'reasoning': 'The signed writing established agreement.',
                                  'order': 'The application is granted with costs.',
                                  'outcome': 'granted', 'costs': 'with costs'}
    assert pair[7:9] == ('court_text', 'concourt:fixture-version')
    assert pair[10] == training.is_holdout(training.PLATFORM_TENANT, 'zenas.reasoning', json.loads(pair[4]))
    assert pair[11:13] == (False, True)  # client_matter, public
    assert any('judgment_splits' in sql for sql, _ in tank.writes)
    assert any('judgment_split_exports' in sql for sql, _ in room.writes)


@pytest.mark.asyncio
@pytest.mark.parametrize('key,configured,region,source,status', [
    ('', True, 'za', 'concourt', 401),
    ('fixture-workspace-key', True, 'za', 'concourt', 401),
    ('fixture-tiger-key', True, 'za', 'concourt', 401),
    ('fixture-zenas-key', False, 'za', 'concourt', 401),
    ('fixture-zenas-key', True, 'us-east', 'concourt', 403),
    ('fixture-zenas-key', True, 'za', 'laws-africa', 403),
    ('fixture-zenas-key', True, 'za', 'saflii', 403),
])
async def test_wrong_key_region_and_training_source_stop_before_sql(monkeypatch, key, configured, region, source, status):
    monkeypatch.setenv('TANK_KEY_ZENAS', 'fixture-zenas-key' if configured else '')

    def no_database():
        pytest.fail('refusal touched the database')

    monkeypatch.setattr(training, 'runtime', lambda: SimpleNamespace(box_region=lambda: region, pool=no_database))
    app = FastAPI()
    app.include_router(training.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://tank') as client:
        response = await client.post('/v1/training/zenas/court-text',
            headers={'X-Tank-Key': key}, json={'source_id': source, 'document_id': 'fixture-document',
                'version_id': 'fixture-version', 'status': 'excluded', 'reason': 'headings'})
    assert response.status_code == status
