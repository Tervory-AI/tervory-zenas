"""Explicit per-tenant/product training snapshots; no export or model calls."""
from __future__ import annotations

import json
from uuid import UUID

from tervor_registry import PRODUCT, digest, encoded

KINDS = frozenset({'shadow', 'correction', 'teacher', 'public'})
MAX_ROWS = 100_000
MAX_ROW_BYTES = 64 * 1024


def obj(value):
    return json.loads(value) if isinstance(value, str) else value


def row(*, tenant, product, kind, source, provider, licence, source_ref, task_type,
        input, output, holdout=False) -> dict:
    if not PRODUCT.fullmatch(product) or kind not in KINDS or not task_type.startswith(product + '.'):
        raise ValueError('Dataset product or source kind does not match')
    if any(not isinstance(v, str) or not v.strip() or len(v) > 2000
           for v in (source, provider, licence, source_ref, task_type)):
        raise ValueError('Each row needs source, provider, licence and reference tags')
    if holdout is not False:
        raise ValueError('Gold rows cannot enter training')
    value = dict(tenant_id=str(UUID(str(tenant))), product=product, kind=kind, source=source,
                 provider=provider, licence=licence, source_ref=source_ref, task_type=task_type,
                 input=input, output=output, holdout=False)
    raw = encoded(value)
    if len(raw.encode()) > MAX_ROW_BYTES:
        raise ValueError('Training row exceeds the size limit')
    return json.loads(raw)


def build(tenant, product: str, rows, *, gold_inputs=()) -> dict:
    tenant = str(UUID(str(tenant)))
    if not PRODUCT.fullmatch(product):
        raise ValueError('Invalid dataset product')
    forbidden = {digest(value) for value in gold_inputs}
    unique = {}
    for number, item in enumerate(rows, 1):
        if number > MAX_ROWS:
            raise ValueError('Dataset exceeds the row limit')
        if item.get('tenant_id') != tenant or item.get('product') != product:
            raise ValueError('Dataset tenant or product does not match')
        checked = row(tenant=tenant, **{k: item[k] for k in (
            'product', 'kind', 'source', 'provider', 'licence', 'source_ref', 'task_type',
            'input', 'output', 'holdout')})
        if digest(checked['input']) in forbidden:
            raise ValueError('Gold input overlaps training')
        unique[digest(checked)] = checked
    ordered = [unique[key] for key in sorted(unique)]
    return dict(tenant_id=tenant, product=product, rows=ordered, count=len(ordered),
                sha256=digest(ordered), source_counts={k: sum(r['kind'] == k for r in ordered) for k in sorted(KINDS)})


async def collect(conn, tenant: UUID, product: str, region: str, *, public_rows=()) -> dict:
    """Read masked training evidence and explicit corrections under one tenant.

    Missing historic provenance is tagged unknown for counsel, never filtered by
    provider or licence. Corpus imports must already carry an explicit tenant.
    """
    if not PRODUCT.fullmatch(product) or region not in ('us-east', 'za'):
        raise ValueError('Invalid dataset product or region')
    rows = list(public_rows)
    pairs = await conn.fetch('''SELECT * FROM tank.training_pairs
        WHERE tenant_id=$1 AND agent=$2 AND region=$3 AND active AND NOT holdout
        AND polarity='positive' ORDER BY id LIMIT 100001''', tenant, product, region)
    for item in pairs:
        rows.append(row(tenant=tenant, product=product, kind='teacher', source=item['source'],
            provider=item['teacher_model'] or 'unknown', licence='unknown', source_ref=item['source_ref'],
            task_type=item['task_type'], input=obj(item['input']), output=obj(item['output'])))
    shadows = await conn.fetch('''SELECT s.*,t.messages,l.region FROM tank.shadow s
        JOIN tank.ledger l ON l.id=s.ledger_id AND l.tenant_id=s.tenant_id
        JOIN tank.transcripts t ON t.ledger_id=l.id AND t.tenant_id=l.tenant_id
        WHERE s.tenant_id=$1 AND l.agent=$2 AND l.region=$3 AND l.outcome='delivered'
        AND l.feedback IS DISTINCT FROM 'rejected' AND s.agreement>=.99
        ORDER BY s.id LIMIT 100001''', tenant, product, region)
    for item in shadows:
        messages = obj(item['messages'])
        if not isinstance(messages, list):
            raise ValueError('Shadow transcript is unavailable')
        rows.append(row(tenant=tenant, product=product, kind='shadow', source='tank.shadow',
            provider=item['primary_model'], licence='unknown', source_ref=str(item['id']),
            task_type=item['task_type'], input={'messages': messages}, output=item['primary_content']))
    memories = await conn.fetch('''SELECT id,claim,meta FROM agent_tank.memory_records
        WHERE tenant_id=$1 AND kind='correction' AND meta->>'product'=$2
        ORDER BY id LIMIT 100001''', tenant, product)
    for item in memories:
        meta = obj(item['meta'])
        if 'input' not in meta or 'task_type' not in meta:
            raise ValueError('Memory correction needs its original input and task')
        rows.append(row(tenant=tenant, product=product, kind='correction', source='hermes.memory',
            provider=meta.get('provider', 'human'), licence=meta.get('licence', 'unknown'),
            source_ref=str(item['id']), task_type=meta['task_type'], input=meta['input'], output=item['claim']))
    from tervor_gold import forbidden_inputs
    return build(tenant, product, rows, gold_inputs=await forbidden_inputs(conn, tenant, region))
