"""Rig code receipts: real zero spend and a separately counted paid comparison."""
import hashlib
import json
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from agent_skills import room_key


class Receipt(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: UUID
    tenant_id: UUID
    action: str = Field(pattern=r'^rig\.[a-z][a-z0-9_.]{0,79}$')
    outcome: Literal['delivered', 'failed']
    reference_tokens_in: int = Field(ge=0, le=4_000_000)
    reference_tokens_out: int = Field(ge=0, le=4_000_000)
    reference_status: Literal['bpe_reference', 'unavailable']
    cost: Decimal | None = Field(ge=0, le=10000)
    cost_status: Literal['recorded', 'unknown']
    model_ledger_ids: list[UUID] = Field(default_factory=list, max_length=32)


class Query(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: UUID
    id: UUID | None = None


async def refresh(conn, body, value):
    if value.get('baseline_status') != 'unpriced' or body.reference_status != 'bpe_reference':
        return value
    row = await conn.fetchrow('''UPDATE tank.ledger SET comparison_revision=comparison_revision+1
        WHERE tenant_id=$1 AND id=$2 AND agent='rig' RETURNING standard_cost_usd,standard_model''',
        body.tenant_id, body.id)
    if row and row['standard_cost_usd'] is not None:
        value.update(would_have_cost=str(row['standard_cost_usd']), baseline_model=row['standard_model'],
                     baseline_status='bpe_reference')
        await conn.execute('''UPDATE tank.agent_receipts SET receipt=$3::jsonb
            WHERE tenant_id=$1 AND ledger_id=$2 AND room='rig' ''', body.tenant_id, body.id, json.dumps(value))
    return value


def build_router(tank):
    router = APIRouter(prefix='/v1/agents/rig/savings')

    @router.post('/query')
    async def query(body: Query, request: Request):
        room_key('rig', request.headers.get('authorization', ''), request.headers.get('x-tank-key', ''))
        rows = await tank.pool().fetch('''SELECT l.*, r.receipt FROM tank.ledger l
            LEFT JOIN tank.agent_receipts r ON r.ledger_id=l.id AND r.tenant_id=l.tenant_id AND r.room='rig'
            WHERE l.tenant_id=$1 AND l.agent='rig' AND ($2::uuid IS NULL OR l.id=$2)
            ORDER BY l.ts DESC,l.id DESC LIMIT 100''', body.tenant_id, body.id)
        result = []
        for row in rows:
            raw = row['receipt'] or {}
            receipt = json.loads(raw) if isinstance(raw, str) else dict(raw)
            code_reference = row['model'] == 'code' and row['rig_reference_tokens_in'] is not None
            comparable = code_reference or row['model'] not in {'code', 'vendor:huggingface_jobs'}
            baseline = row['standard_cost_usd'] if comparable else None
            cost_status = receipt.get('cost_basis', 'recorded')
            if receipt.get('cost_status') in {'unknown', 'unknown_upper_bound'}:
                cost_status = 'unknown'
            elif receipt.get('cost_status') in {'estimated', 'estimated_upper_bound'}:
                cost_status = 'upper_bound'
            result.append({'id': str(row['id']), 'ledger_id': str(row['id']),
                'action': row['task_type'], 'created_at': row['ts'].isoformat(), 'outcome': row['outcome'],
                'cost': str(row['cost_usd']) if row['cost_usd'] is not None else None,
                'cost_status': cost_status if row['cost_usd'] is not None else 'unknown',
                'would_have_cost': str(baseline) if baseline is not None else None,
                'baseline_model': row['standard_model'] if comparable else None,
                'baseline_status': ('bpe_reference' if code_reference else 'measured') if baseline is not None else 'unpriced',
                'tokens_in': row['tokens_in'], 'tokens_out': row['tokens_out'], 'accounting': 'recorded'})
        return {'tenant_id': str(body.tenant_id), 'receipts': result}

    @router.post('/receipts')
    async def record(body: Receipt, request: Request):
        room_key('rig', request.headers.get('authorization', ''), request.headers.get('x-tank-key', ''))
        if body.model_ledger_ids:
            identities = set(body.model_ledger_ids)
            rows = await tank.pool().fetch('''SELECT id,cost_usd,standard_cost_usd,standard_model FROM tank.ledger
                WHERE tenant_id=$1 AND agent='rig' AND id=ANY($2::uuid[]) AND model NOT LIKE 'vendor:%' ''',
                body.tenant_id, list(identities))
            if len(rows) != len(identities) or body.cost_status != 'recorded' or sum(row['cost_usd'] for row in rows) != body.cost:
                raise HTTPException(422, 'The model receipt is not confirmed for this store.')
            baseline = (sum(row['standard_cost_usd'] for row in rows)
                        if all(row['standard_cost_usd'] is not None for row in rows) else None)
            return {'id': str(body.id), 'tenant_id': str(body.tenant_id), 'ledger_id': str(rows[0]['id']),
                'cost': str(body.cost), 'would_have_cost': str(baseline) if baseline is not None else None,
                'baseline_model': ', '.join(sorted({row['standard_model'] for row in rows if row['standard_model']})),
                'baseline_status': 'measured' if baseline is not None else 'unpriced'}
        # This endpoint accounts for code only. Model and vendor spend already
        # has an authoritative Tank ledger entry and must not be counted twice.
        if body.cost != 0 or body.cost_status != 'recorded':
            raise HTTPException(422, 'A code receipt cannot claim provider spend.')
        digest = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        async with tank.pool().acquire() as conn:
            async with conn.transaction():
                previous = await conn.fetchval('''SELECT receipt FROM tank.agent_receipts
                    WHERE tenant_id=$1 AND ledger_id=$2 AND room='rig' ''', body.tenant_id, body.id)
                if previous:
                    value = json.loads(previous) if isinstance(previous, str) else dict(previous)
                    if value.get('input_hash') != digest:
                        raise HTTPException(409, 'Receipt id has different inputs.')
                    return await refresh(conn, body, value)
                row = await conn.fetchrow('''INSERT INTO tank.ledger
                    (id,tenant_id,region,agent,task_type,rung,model,priority,cost_usd,tokens_in,tokens_out,
                     outcome,task_id,rig_reference_tokens_in,rig_reference_tokens_out)
                    VALUES($1,$2,$3,'rig',$4,3,'code','standard',0,0,0,$5,$1,$6,$7)
                    ON CONFLICT (id) DO NOTHING RETURNING *''', body.id, body.tenant_id,
                    tank.box_region(), body.action, body.outcome,
                    body.reference_tokens_in if body.reference_status == 'bpe_reference' else None,
                    body.reference_tokens_out if body.reference_status == 'bpe_reference' else None)
                if row is None:
                    # A racing delivery can observe its committed receipt here.
                    previous = await conn.fetchval('''SELECT receipt FROM tank.agent_receipts
                        WHERE tenant_id=$1 AND ledger_id=$2 AND room='rig' ''', body.tenant_id, body.id)
                    value = json.loads(previous) if isinstance(previous, str) else previous
                    if not value or value.get('input_hash') != digest:
                        raise HTTPException(409, 'Receipt id is already in use.')
                    return value
                baseline = row['standard_cost_usd'] if body.reference_status == 'bpe_reference' else None
                value = {'id': str(body.id), 'ledger_id': str(body.id), 'tenant_id': str(body.tenant_id),
                    'action': body.action, 'outcome': body.outcome, 'cost': '0', 'cost_usd': '0',
                    'would_have_cost': str(baseline) if baseline is not None else None,
                    'baseline_model': row['standard_model'],
                    'baseline_status': 'bpe_reference' if baseline is not None else 'unpriced',
                    'input_hash': digest, 'tokens_in': 0, 'tokens_out': 0,
                    'reference_tokens_in': body.reference_tokens_in, 'reference_tokens_out': body.reference_tokens_out}
                await conn.execute('''INSERT INTO tank.agent_receipts(ledger_id,tenant_id,room,receipt)
                    VALUES($1,$2,'rig',$3::jsonb)''', body.id, body.tenant_id, json.dumps(value))
                return value

    return router
