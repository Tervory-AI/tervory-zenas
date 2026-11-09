"""Tiger comparisons are estimates, separate from billable Tank ledger entries."""
import hashlib
import json
import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class Run(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: UUID
    id: UUID
    action: str = Field(pattern=r'^tiger\.[a-z_]+(?:\.[a-z_]+)*$')
    input: dict
    output: dict
    parts: list[UUID] = Field(default_factory=list)
    failed: bool = False


def packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)


def unpack(value):
    return json.loads(value) if isinstance(value, str) else value


def tokens(value):
    from agent_skills import input_encoding
    return len(input_encoding().encode_ordinary(packed(value)))


async def record(conn, body, *, component=False, ledger_ids=(), cache_hit=False):
    previous = await conn.fetchval('SELECT receipt FROM tank.tiger_savings WHERE tenant_id=$1 AND id=$2', body.tenant_id, body.id)
    if previous is not None:
        previous = unpack(previous)
        if previous['action'] != body.action:
            raise HTTPException(409, 'Receipt belongs to another action')
        return previous
    parts = list(dict.fromkeys(body.parts))
    children = await conn.fetch('SELECT receipt FROM tank.tiger_savings WHERE tenant_id=$1 AND id=ANY($2::uuid[]) AND component', body.tenant_id, parts) if parts else []
    if len(children) != len(parts):
        raise HTTPException(409, 'A Tiger source receipt is unavailable')
    children = [unpack(row['receipt']) for row in children]
    ids = list(dict.fromkeys(UUID(str(value)) for value in ledger_ids))
    ledger = await conn.fetch('''SELECT id,tokens_in,tokens_out,
        CASE WHEN cost_status='not_set' OR research_cost_status='unknown' THEN NULL ELSE cost_usd END AS cost_usd
        FROM tank.ledger WHERE tenant_id=$1 AND agent=$2 AND id=ANY($3::uuid[])''', body.tenant_id, 'tiger', ids) if ids else []
    if len(ledger) != len(ids):
        raise HTTPException(409, 'A Tiger cost receipt is unavailable')
    costs = [row['cost_usd'] for row in ledger] + [row['cost'] for row in children]
    cost = None if any(value is None for value in costs) else sum(float(value) for value in costs)
    if body.failed and not costs:
        cost = None  # A failed upstream call may have spent before disconnecting.
    actual = sum(row['tokens_in'] + row['tokens_out'] for row in ledger) + sum(row['model_tokens'] for row in children)
    # Output omits receipt metadata. No page, prompt, credential or response
    # body is retained: the row contains counts, prices and owned references.
    # Base64 bytes are not the vision model's billed image tokens. Without
    # that measurement, keep the comparison unknown instead of inflating it.
    image_input = bool(body.input.get('png_base64'))
    tin, tout = (None if image_input else tokens(body.input)), tokens(body.output)
    price = await conn.fetchrow('''WITH serving AS (
        SELECT model FROM tank.policy WHERE task_type=$1 AND (tenant_id=$2 OR tenant_id IS NULL)
        ORDER BY tenant_id NULLS LAST LIMIT 1)
        SELECT price.model,price.input_usd,price.output_usd FROM tank.proof_prices price
        WHERE price.active AND price.model=coalesce(
          (SELECT override_model FROM tank.baseline_settings WHERE task_type=$1),
          tank.standard_model_for((SELECT family FROM tank.proof_prices WHERE model=(SELECT model FROM serving))))''', body.action, body.tenant_id)
    estimate = float(tin * price['input_usd'] + tout * price['output_usd']) if price and tin is not None else None
    estimates = [row['would_have_cost'] for row in children]
    # For jobs with skill receipts the full source payload was already counted
    # by each component; don't count its summary a second time.
    if children:
        estimate = None if any(value is None for value in estimates) else sum(estimates)
        tin = None if any(row['reference_tokens_in'] is None for row in children) else sum(row['reference_tokens_in'] for row in children)
        tout = sum(row['reference_tokens_out'] for row in children)
    receipt = {'id': str(body.id), 'tenant_id': str(body.tenant_id), 'action': body.action,
        'status': 'failed' if body.failed else 'completed', 'cost': cost, 'would_have_cost': estimate,
        'cost_usd': cost, 'baseline_cost_usd': estimate, 'model_tokens': actual,
        'cache_hit': cache_hit,
        'reference_tokens_in': tin, 'reference_tokens_out': tout,
        'comparison': 'cl100k reference estimate',
        'baseline_models': sorted({model for row in children for model in row['baseline_models']}) if children else ([price['model']] if price else []),
        'pricing_status': 'estimated' if estimate is not None else 'unpriced',
        'missing_setting': None if estimate is not None else ('image token measurement' if tin is None else 'tank.policy / tank.baseline_settings / tank.proof_prices'),
        'parts': [str(value) for value in parts], 'ledger_ids': [str(value) for value in ids]}
    await conn.execute('''INSERT INTO tank.tiger_savings(tenant_id,id,action,component,receipt)
        VALUES($1,$2,$3,$4,$5::jsonb) ON CONFLICT(tenant_id,id) DO NOTHING''',
        body.tenant_id, body.id, body.action, component, packed(receipt))
    return unpack(await conn.fetchval('SELECT receipt FROM tank.tiger_savings WHERE tenant_id=$1 AND id=$2', body.tenant_id, body.id))


async def skill_receipt(tank, pkg, body, ctx, out):
    async with tank.pool().acquire() as conn:
        async with conn.transaction():
            ids = [{'id': UUID(str(value))} for value in getattr(ctx, '_tiger_ledger_ids', [])]
            if out.get('_ledger_id'):
                ids = [*ids, {'id': UUID(out['_ledger_id'])}]
            if not ids:
                raise HTTPException(503, 'Tiger cost receipt is unavailable')
            return await record(conn, Run(tenant_id=body.tenant_id, id=ctx._tiger_run_id,
                action=pkg.task_type or pkg.id, input=body.input,
                output={key: value for key, value in out.items() if not key.startswith('_')}),
                component=True, ledger_ids=[row['id'] for row in ids], cache_hit=getattr(ctx, '_tiger_cache_hit', False))


async def cached_extract(tank, pkg, body, ctx):
    # Only the pure, deterministic reader is cached. Approval/ownership checks
    # in _run_one_skill run first on EVERY request. Never cache a fetched URL.
    ctx._tiger_cache_hit = False
    if (os.environ.get('TIGER_BRAIN_ENABLED', 'false').lower() not in {'true', '1', 'yes'}
            or pkg.id != 'tiger.price_extract' or body.preview):
        return await tank.skala.run_skill(pkg, body.input, ctx)
    digest = hashlib.sha256(packed([pkg.ref, body.input]).encode())
    for path in sorted(pkg.path.rglob('*')):
        if path.is_file() and path.suffix in {'.py', '.json', '.yaml'}:
            digest.update(str(path.relative_to(pkg.path)).encode())
            digest.update(path.read_bytes())
    key = digest.hexdigest()
    lock_scope = str(body.tenant_id) + key
    async with tank.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', lock_scope)
            old = await conn.fetchval('SELECT output FROM tank.tiger_extract_cache WHERE tenant_id=$1 AND key=$2', body.tenant_id, key)
            if old is not None:
                ctx._tiger_cache_hit = True
                return unpack(old)
            out = await tank.skala.run_skill(pkg, body.input, ctx)
            if not ctx.calls and not ctx.cost_usd:
                await conn.execute('''INSERT INTO tank.tiger_extract_cache(tenant_id,key,output)
                    VALUES($1,$2,$3::jsonb) ON CONFLICT DO NOTHING''', body.tenant_id, key, packed(out))
            return out


async def listed(conn, tenant):
    rows = await conn.fetch('''SELECT receipt,created_at FROM tank.tiger_savings
        WHERE tenant_id=$1 AND NOT component ORDER BY created_at DESC,id DESC LIMIT 200''', tenant)
    return [{**public(unpack(row['receipt'])), 'created_at': row['created_at'].isoformat()} for row in rows]


def public(receipt):
    return {key: value for key, value in receipt.items() if key not in {'baseline_models', 'ledger_ids', 'parts'}}


async def command_comparison(conn, receipt, tenant, step, ids):
    value = await conn.fetchval('SELECT receipt FROM tank.tiger_savings WHERE tenant_id=$1 AND id=$2 AND NOT component', tenant, UUID(step))
    if value is None:
        return receipt
    saved = unpack(value)
    # A command can cite multiple ledger entries. Attribute its all-input
    # estimate once; actual charges remain on their original ledger entries.
    comparison = saved['would_have_cost']
    if comparison is not None and receipt['ledger_id'] != str(min(ids, key=str)):
        comparison = 0
    return {**receipt, 'baseline_cost_usd': comparison,
        'baseline_model': ', '.join(saved['baseline_models']) or None,
        'baseline_status': 'baseline pending' if comparison is None else 'estimated',
        'saved_usd': None if comparison is None or receipt['cost_usd'] is None else comparison-receipt['cost_usd']}


def build_router(tank):
    """Register Tiger's savings API through the product route registry."""
    router = APIRouter()
    router.add_api_route('/v1/tiger/savings', tank.tiger_savings_record, methods=['POST'])
    router.add_api_route('/v1/tiger/savings', tank.tiger_savings_list, methods=['GET'],
                         dependencies=[Depends(tank.require_key)])
    return router
