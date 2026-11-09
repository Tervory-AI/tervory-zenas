"""Catch stale proposal replay over the verified shared runtime."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('name', [
    'agents/_chassis/chassis/tank.py',
    'tank/agent_tasks.py',
    'tank/zenas_law.py',
    'tank/zenas_savings.py',
    'tank/main.py',
    'tank/routes_registry.py',
    'tank/training.py',
    'tank/training_jobs/run.py',
    'tank/training_jobs/train.py',
    'tank/Dockerfile',
])
def test_shared_runtime_is_exact_verified_pin(name):
    vendor = ROOT / 'vendor/tervory-tank'
    assert (ROOT / name).read_bytes() == (vendor / name).read_bytes()


def test_historical_launcher_bootstrap_is_not_replayed():
    assert not (ROOT / 'tank/training_jobs/hf_jobs_runtime.py').exists()
    assert 'ensure_jobs_runtime' not in (ROOT / 'tank/training_jobs/run.py').read_text()
