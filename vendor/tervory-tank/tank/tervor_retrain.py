"""Opt-in scheduled retraining; durable claims survive uncertain submission."""
import asyncio
from datetime import date, timedelta
from decimal import Decimal
import logging
import os
from pathlib import Path
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

import tervor_dataset
import tervor_gold
import tervor_registry
from training_jobs.round_one import PLATFORM, bundle

log = logging.getLogger('tank.tervor_retrain')


class Schedule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    product: Literal['zenas', 'base', 'tiger']
    version: str
    starts_on: date
    interval_days: int = Field(ge=1, le=365)
    minutes: int = Field(ge=1, le=1440)

    @field_validator('version')
    @classmethod
    def version_valid(cls, value):
        if not tervor_registry.VERSION.fullmatch(value):
            raise ValueError('An explicit Tervor version is required')
        return value

    def due(self, today):
        if today < self.starts_on:
            return None
        index = (today - self.starts_on).days // self.interval_days
        day = self.starts_on + timedelta(days=index * self.interval_days)
        code, major, minor = tervor_registry.VERSION.fullmatch(self.version).groups()
        return day.isoformat(), f'{code}-{major}.{int(minor) + index}'


class Plan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schedules: list[Schedule] = Field(min_length=1, max_length=3)
    receipts: list[Path] = Field(default_factory=list, max_length=100)

    @field_validator('schedules')
    @classmethod
    def unique_products(cls, value):
        if len({item.product for item in value}) != len(value):
            raise ValueError('Duplicate product schedule')
        return value


def enabled():
    return os.environ.get('TERVOR_RETRAIN_ENABLED') == 'true'


def read_plan():
    value = os.environ.get('TERVOR_RETRAIN_PLAN', '')
    if not value:
        raise ValueError('Missing setting: TERVOR_RETRAIN_PLAN')
    path = Path(value)
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError('Invalid retraining plan')
    return Plan.model_validate_json(path.read_text())


async def invoke(arguments):
    """Reuse the isolated HF client, 180-second deadline and redacted failures."""
    from training import _training_subprocess
    result = await _training_subprocess(*arguments)
    if result.get('status') not in ('ready', 'running', 'complete'):
        raise RuntimeError('Training command failed; inspect the reserved job before retrying')
    return result


async def once(rt, *, today=None):
    if not enabled():
        return {'status':'disabled'}
    required = ('TERVOR_RETRAIN_PLAN', 'HF_TRAIN_BASE_MODEL', 'HF_TRAIN_BASE_REVISION',
                'HF_TRAIN_MAX_USD', 'HF_ORG', 'HF_TOKEN', 'DATABASE_URL')
    missing = [name for name in required if not os.environ.get(name, '').strip()]
    if missing:
        return {'status':'not_configured', 'settings':missing}
    plan = read_plan()
    cap = Decimal(os.environ['HF_TRAIN_MAX_USD'])
    if not cap.is_finite() or cap <= 0:
        raise ValueError('HF_TRAIN_MAX_USD must be a positive total cap')
    if rt.box_region() != 'us-east':
        return {'status':'held', 'reason':'Shared retraining requires the US region'}
    today = today or date.today()
    async with rt.pool().acquire() as conn:
        if not await conn.fetchval('SELECT pg_try_advisory_lock(871229)'):
            return {'status':'busy'}
        try:
            for path in plan.receipts:
                if path.is_symlink() or path.stat().st_size > 65536:
                    raise ValueError('Invalid billing receipt file')
                result = await invoke(['--receipt', str(path)])
                if result.get('status') != 'complete':
                    return {'status':'held', 'reason':'Provider bill could not be reconciled'}
            if await conn.fetchval('''SELECT 1 FROM tank.training_jobs
                WHERE tenant_id=$1 AND ledger_id IS NULL LIMIT 1''', PLATFORM):
                return {'status':'held', 'reason':'Reconcile the previous training job before retraining'}
            for item in plan.schedules:
                slot = item.due(today)
                if slot is None:
                    continue
                day, version = slot
                model = tervor_registry.identity(version, item.product, day)
                marker = dict(tenant_id=str(PLATFORM), region=rt.box_region(), model_id=model)
                exists = await conn.fetchval('''SELECT 1 FROM tank.policy_log WHERE task_type='tervor.retrain'
                    AND detail @> $1::jsonb LIMIT 1''', tervor_registry.encoded(marker))
                if exists:
                    continue
                data = await tervor_dataset.collect(conn, PLATFORM, item.product, rt.box_region())
                gold = await tervor_gold.snapshot(conn, PLATFORM, item.product, rt.box_region())
                prepared = bundle(PLATFORM, item.product, data['rows'], gold)
                with tempfile.TemporaryDirectory(prefix='tervor-retrain-') as directory:
                    path = Path(directory) / 'bundle.json'
                    path.write_text(tervor_registry.encoded(prepared))
                    path.chmod(0o600)
                    args = ['--agent', item.product, '--day', day, '--model-version', version,
                            '--bundle', str(path), '--minutes', str(item.minutes), '--total-cap', str(cap)]
                    quote = await invoke([*args, '--dry-run'])
                    if quote.get('status') != 'ready':
                        return quote
                    spent = await conn.fetchval('''SELECT coalesce(sum(coalesce(actual_usd,estimated_usd)),0)
                        FROM tank.training_jobs WHERE tenant_id=$1''', PLATFORM)
                    if spent + Decimal(str(quote['cost_usd'])) > cap:
                        return {'status':'held', 'reason':'Cumulative training cost exceeds HF_TRAIN_MAX_USD'}
                    # Commit the claim before the paid boundary, including a process crash.
                    await conn.execute('''INSERT INTO tank.policy_log(task_type,action,by_user,detail)
                        VALUES('tervor.retrain','claimed','tank',$1::jsonb)''', tervor_registry.encoded(marker))
                    result = await invoke([*args, '--submit'])
                    await conn.execute('''INSERT INTO tank.policy_log(task_type,action,by_user,detail)
                        VALUES('tervor.retrain','submitted','tank',$1::jsonb)''',
                        tervor_registry.encoded({**marker, 'job_id':result.get('name'), 'status':result.get('status')}))
                    return result
            return {'status':'caught_up'}
        finally:
            await conn.execute('SELECT pg_advisory_unlock(871229)')


async def loop(rt):
    while enabled():
        try:
            result = await once(rt)
            if result.get('status') in ('held', 'not_configured'):
                log.warning('Tervor retraining paused: %s', result.get('settings') or result.get('reason'))
        except Exception:
            log.warning('Tervor retraining paused; check configuration, evidence and billing. No uncertain job is repeated.')
        await asyncio.sleep(60)
