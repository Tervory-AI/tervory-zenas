"""Private, tenant-scoped Tervor identities; registration never enables serving."""
from __future__ import annotations

from datetime import date
import hashlib
import json
import math
import re
from uuid import UUID

CODES = frozenset({'tervor-lite', 'tervor-core', 'tervor-max', 'tervor-code'})
VERSION = re.compile(r'(tervor-(?:lite|core|max|code))-(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')
PRODUCT = re.compile(r'[a-z][a-z0-9_]{0,39}\Z')


def encoded(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def identity(version: str, product: str, trained_on: str) -> str:
    if not VERSION.fullmatch(version) or not PRODUCT.fullmatch(product):
        raise ValueError('Invalid Tervor version or product')
    day = date.fromisoformat(trained_on)
    if trained_on != day.isoformat():
        raise ValueError('Training date must be YYYY-MM-DD')
    return f'{version}-{product}-{day:%Y%m%d}'


def manifest(*, version: str, product: str, trained_on: str, data_hashes: list[str],
             model_card: dict, scores: dict, rung: str, rollback: str | None = None) -> dict:
    model_id = identity(version, product, trained_on)
    if not data_hashes or any(not isinstance(h, str) or not re.fullmatch('[a-f0-9]{64}', h) for h in data_hashes):
        raise ValueError('Data hashes must be SHA-256')
    if not isinstance(model_card, dict) or any(not isinstance(model_card.get(key), str)
            or not model_card[key].strip() for key in ('source', 'licence', 'base', 'base_revision')):
        raise ValueError('Model card needs source, licence, base and base revision')
    if not isinstance(scores, dict) or any(not isinstance(k, str) or type(v) not in (int, float)
            or not math.isfinite(v) or not 0 <= v <= 1 for k, v in scores.items()):
        raise ValueError('Scores must be finite values from zero to one')
    if rung not in ('local', 'own-trained'):
        raise ValueError('Invalid trained model rung')
    if rollback == model_id:
        raise ValueError('A model cannot roll back to itself')
    value = dict(id=model_id, version=version, product=product, trained_on=trained_on,
                 data_hashes=sorted(set(data_hashes)), model_card=model_card, scores=scores,
                 rung=rung, rollback=rollback)
    # Copy nested values and reject non-JSON metadata before any persistence.
    return json.loads(encoded(value))


async def get(conn, tenant: UUID, model_id: str) -> dict | None:
    value = await conn.fetchval('SELECT manifest FROM tank.tervor_models WHERE tenant_id=$1 AND id=$2',
                                tenant, model_id)
    return json.loads(value) if isinstance(value, str) else value


async def register(conn, tenant: UUID, value: dict) -> str:
    """Immutable registration is idempotent. No policy or route is changed."""
    UUID(str(tenant))
    checked = manifest(**{key: value[key] for key in (
        'version', 'product', 'trained_on', 'data_hashes', 'model_card', 'scores', 'rung', 'rollback')})
    if value != checked:
        raise ValueError('Manifest identity does not match its contents')
    async with conn.transaction():
        if checked['rollback']:
            previous = await get(conn, tenant, checked['rollback'])
            if not previous or previous['product'] != checked['product']:
                raise ValueError('Rollback must belong to the same tenant and product')
        await conn.execute('''INSERT INTO tank.tervor_models(tenant_id,id,product,version,manifest)
            VALUES ($1,$2,$3,$4,$5::jsonb) ON CONFLICT DO NOTHING''',
            tenant, checked['id'], checked['product'], checked['version'], encoded(checked))
        stored = await get(conn, tenant, checked['id'])
        if stored != checked:
            raise ValueError('This version is already registered with different evidence')
    return checked['id']


async def list_models(conn, tenant: UUID, product: str) -> list[dict]:
    if not PRODUCT.fullmatch(product):
        raise ValueError('Invalid product')
    rows = await conn.fetch('''SELECT manifest FROM tank.tervor_models
        WHERE tenant_id=$1 AND product=$2 ORDER BY created_at DESC,id''', tenant, product)
    return [json.loads(row['manifest']) if isinstance(row['manifest'], str) else row['manifest'] for row in rows]
