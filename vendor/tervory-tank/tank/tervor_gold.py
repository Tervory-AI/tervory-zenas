"""Held-out product evidence. Gold answers are never training completions."""
from __future__ import annotations

import json
from uuid import UUID

from tervor_registry import PRODUCT, digest

FIRST_PRODUCTS = ('zenas', 'base', 'tiger')


def obj(value):
    return json.loads(value) if isinstance(value, str) else value


async def forbidden_inputs(conn, tenant: UUID, region: str) -> list:
    # A renamed product, source or corrected output cannot launder a gold input.
    # Include unchecked held-out rows: review may change their label, not split.
    rows = await conn.fetch('''SELECT input FROM tank.gold_examples
        WHERE tenant_id=$1 AND region=$2 AND holdout
        UNION ALL SELECT input FROM tank.training_pairs
        WHERE tenant_id=$1 AND region=$2 AND holdout''', tenant, region)
    return [obj(row['input']) for row in rows]


async def snapshot(conn, tenant: UUID, product: str, region: str) -> dict:
    if not PRODUCT.fullmatch(product) or region not in ('us-east', 'za'):
        raise ValueError('Invalid gold product or region')
    rows = await conn.fetch('''SELECT id,skill_id,input,expected,source,source_ref,tolerance
        FROM tank.gold_examples WHERE tenant_id=$1 AND agent=$2 AND region=$3
        AND holdout AND status='checked' AND (NOT auto_label OR auto_approved)
        ORDER BY id''', tenant, product, region)
    cases = []
    seen = {}
    for row in rows:
        value, expected = obj(row['input']), obj(row['expected'])
        fingerprint = digest(value)
        if fingerprint in seen and seen[fingerprint] != expected:
            raise ValueError('Gold labels conflict for the same input')
        if fingerprint in seen:
            continue
        seen[fingerprint] = expected
        cases.append(dict(id=str(row['id']), input=value, expected=expected, task_type=row['skill_id'],
            tolerance=row['tolerance'], source=row['source'], source_ref=row['source_ref'],
            provider='reviewed gold', licence='unknown', holdout=True))
    cases.sort(key=lambda case: digest(case['input']))
    return dict(tenant_id=str(tenant), product=product, region=region, holdout=True,
                cases=cases, count=len(cases), sha256=digest(cases))


def verify(gold: dict, tenant, product: str) -> list[dict]:
    if (gold.get('tenant_id') != str(tenant) or gold.get('product') != product
            or gold.get('holdout') is not True or not gold.get('cases')):
        raise ValueError('A nonempty gold set for this tenant and product is required')
    cases = gold['cases']
    if (gold.get('count') != len(cases) or gold.get('sha256') != digest(cases)
            or any(case.get('holdout') is not True for case in cases)
            or len({digest(case['input']) for case in cases}) != len(cases)):
        raise ValueError('Gold set integrity check failed')
    return cases
