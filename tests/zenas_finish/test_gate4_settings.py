import ast
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from test_tank_training_patch import functions

from agents.zenas.core.configuration import room_readiness

ROOT = Path(__file__).resolve().parents[2]


def readiness():
    tree = ast.parse((ROOT / 'tank/training.py').read_text())
    selected = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == '_training_readiness']
    namespace = {'os': os, 'PLATFORM_TENANT': UUID(int=1), 'runtime': lambda: SimpleNamespace(box_region=lambda: 'za'),
                 'OFFICIAL_ZA_CASE_SOURCES': ['concourt']}
    return functions(selected, namespace)['_training_readiness']


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', [('HF_TOKEN',), ('HF_ORG',), ('HF_TOKEN', 'HF_ORG')])
async def test_hf_preflight_names_each_missing_binding_and_never_quotes_or_launches(monkeypatch, missing):
    monkeypatch.setenv('HF_TOKEN', 'placeholder')
    monkeypatch.setenv('HF_ORG', 'placeholder-org')
    for name in missing:
        monkeypatch.delenv(name)
    conn = SimpleNamespace(fetchval=AsyncMock(side_effect=[2000, 200, False]))
    result = await readiness()(conn, SimpleNamespace(agent='zenas'))
    assert result['needs_settings'] == list(missing)
    assert result['status'] == 'Hugging Face training is not configured'
    assert 'placeholder' not in str(result)


@pytest.mark.parametrize('missing', ['CHASSIS_ADMIN_DATABASE_URL', 'CHASSIS_RLS_ADMIN_ROLE'])
def test_optional_rls_configuration_reports_missing_partner(monkeypatch, missing):
    for name in ['DATABASE_URL', 'TANK_KEY', 'CHASSIS_ADMIN_DATABASE_URL', 'CHASSIS_RLS_ADMIN_ROLE']:
        monkeypatch.setenv(name, 'placeholder')
    monkeypatch.setenv('TANK_REGION', 'za')
    monkeypatch.setenv('ZENAS_PII_KEY', '00' * 32)
    monkeypatch.delenv(missing)
    assert room_readiness()['needs_settings'] == [missing]
