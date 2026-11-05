from unittest.mock import AsyncMock

import pytest

from agents.zenas.core import convert, judgment_training, regulations, selftest
from agents.zenas.pool import nightly
from test_source_freshness import database, room  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('region', ['za', 'us-east'])
async def test_scheduled_conversion_binds_owning_region_and_returns_real_result(database, monkeypatch, region):
    await room(database)
    expected = {'ok': True, 'converted': 0, 'region': region}

    async def convert_cases(law, ladder, model, *, region):
        assert law.home_region == region
        return expected

    monkeypatch.setattr(convert, 'convert_cases', convert_cases)
    monkeypatch.setattr(convert, '_night_model', lambda: (None, None))
    async with database.acquire() as conn:
        assert await convert.run_scheduled(conn, region=region) == expected


@pytest.mark.asyncio
async def test_nightly_failure_receipt_names_failed_jobs_and_continues(monkeypatch):
    monkeypatch.setattr(regulations, 'run_scheduled', AsyncMock(return_value={'sent': 0}))
    monkeypatch.setattr(convert, 'run_scheduled', AsyncMock(side_effect=RuntimeError('offline fault')))
    monkeypatch.setattr(judgment_training, 'run_scheduled', AsyncMock(return_value={'split': 0}))
    monkeypatch.setattr(selftest, 'run_scheduled', AsyncMock(side_effect=RuntimeError('offline fault')))
    report = {}
    await nightly._scheduled_jobs(None, report, region='us-east')
    assert report['failed_jobs'] == ['convert', 'selftest']
    assert report['regulations'] == {'sent': 0}
    assert report['judgment_training'] == {'split': 0}
