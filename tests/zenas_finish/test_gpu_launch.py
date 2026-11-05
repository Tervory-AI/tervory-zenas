import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from tank.training_jobs import run as gpu
from tank.training_jobs import train


@pytest.mark.asyncio
async def test_existing_gpu_launcher_reserves_before_hf_and_sends_no_database_credentials(monkeypatch):
    events, submitted = [], {}

    async def execute(sql, *args):
        events.append('reservation' if 'INSERT INTO tank.training_jobs' in sql else 'sql')

    conn = SimpleNamespace(execute=execute, fetchval=AsyncMock(return_value=False),
                           fetch=AsyncMock(return_value=[]), close=AsyncMock())

    class API:
        def __init__(self, *, token):
            assert token == 'placeholder'

        def list_jobs_hardware(self):
            return [SimpleNamespace(name='a10g-small', unit_label='minute', unit_cost_micro_usd=20000)]

        def create_repo(self, **kwargs):
            assert kwargs == {'repo_id': 'placeholder-org/tervory-own-zenas-za', 'private': True, 'repo_type': 'model'}

        def model_info(self, repo):
            return SimpleNamespace(private=True)

        def run_uv_job(self, script, **kwargs):
            assert 'reservation' in events
            events.append('hf')
            submitted.update(kwargs)
            assert Path(script).name == 'train.py'
            assert Path(script).is_file()
            return SimpleNamespace(id='offline-job')

    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(HfApi=API))
    import asyncpg
    monkeypatch.setattr(asyncpg, 'connect', AsyncMock(return_value=conn))
    for name, value in {'TANK_REGION': 'za', 'HF_ORG': 'placeholder-org', 'HF_TOKEN': 'placeholder',
                        'HF_TRAIN_MAX_USD': '50', 'DATABASE_URL': 'placeholder-local-db'}.items():
        monkeypatch.setenv(name, value)
    result = await gpu.run(SimpleNamespace(agent='zenas', receipt=None, job_id='', minutes=120, submit=True, day='2026-11-06'))
    assert result['status'] == 'running' and result['cost_usd'] == Decimal('2.4')
    assert submitted == {'namespace': 'placeholder-org', 'flavor': 'a10g-small', 'timeout': 7200,
                         'env': {'HF_ORG': 'placeholder-org', 'TRAIN_DAY': '2026-11-06',
                                 'OUTPUT_REPO': 'placeholder-org/tervory-own-zenas-za', 'TRAIN_AGENT': 'zenas'},
                         'secrets': {'HF_TOKEN': 'placeholder'}}
    assert events.index('reservation') < events.index('hf')
    conn.close.assert_awaited_once()


def test_training_config_and_exact_court_outcome_gate_are_the_existing_ones():
    assert train.context_length('zenas') == 4096
    rows = [{'output': {'outcome': 'dismissed'}} for _ in range(200)]
    result = train.score_outcomes(rows, ['dismissed'] * 198 + ['granted'] * 2)
    assert result['method'] == 'exact_outcome' and result['gold_holdout']
    assert result['n'] == 200 and result['agreement'] == .99
    assert train.score_outcomes(rows, ['dismissed'] * 197 + ['granted'] * 3)['agreement'] < .99
    with pytest.raises(ValueError, match='Every holdout row'):
        train.score_outcomes(rows, ['dismissed'])


def test_gpu_budget_is_checked_before_submission():
    with pytest.raises(ValueError, match='exceeds HF_TRAIN_MAX_USD'):
        gpu.estimate(Decimal(1), 120, Decimal(50))
