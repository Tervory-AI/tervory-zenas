"""Price a structured code decision using Tank's existing paid default; no model call."""
from __future__ import annotations

import json
from uuid import UUID, uuid5
import hashlib

from agent_skills import input_encoding, input_tokens
from pydantic import BaseModel, ConfigDict, Field


class SavingsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: UUID
    receipt_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,200}$')
    reference: dict[str, str | bool] = Field(max_length=7)


async def quote(core, body: SavingsRequest):
    # Watch sends only the bounded decision, never the client's raw log.
    allowed = {'kind', 'action', 'outcome', 'step', 'signal', 'severity', 'applied'}
    if set(body.reference) - allowed or any(len(str(v)) > 200 for v in body.reference.values()):
        from fastapi import HTTPException
        raise HTTPException(422, 'Invalid savings reference.')
    value = json.dumps(body.reference, sort_keys=True, separators=(',', ':'))
    incoming = input_tokens({'messages': [{'role': 'user', 'content': value}]})
    outgoing = len(input_encoding().encode_ordinary(value))
    try:
        ledger = UUID(body.receipt_id)
    except ValueError:
        ledger = uuid5(body.tenant_id, 'autumbra:' + body.receipt_id)
    digest = hashlib.sha256(value.encode()).hexdigest()
    async with core.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("""INSERT INTO tank.ledger(id,tenant_id,region,agent,task_type,rung,model,cost_usd)
                VALUES($1,$2,$3,'ghost','ghost.savings',3,'code',0) ON CONFLICT(id) DO NOTHING""",
                ledger, body.tenant_id, core.box_region())
            current = await conn.fetchrow("""SELECT autumbra_reference_hash,baseline_cost_usd FROM tank.ledger
                WHERE id=$1 AND tenant_id=$2 AND agent='ghost' AND cost_usd=0 FOR UPDATE""", ledger, body.tenant_id)
            if current is None or current['autumbra_reference_hash'] not in (None, digest):
                from fastapi import HTTPException
                raise HTTPException(409, 'Receipt identity conflict.')
            if current['autumbra_reference_hash'] is None or current['baseline_cost_usd'] is None:
                await conn.execute("""UPDATE tank.ledger SET comparison_tokens_in=$3,comparison_tokens_out=$4,
                    autumbra_reference_hash=$5
                    WHERE id=$1 AND tenant_id=$2 AND agent='ghost' AND cost_usd=0""",
                    ledger, body.tenant_id, incoming, outgoing, digest)
            row = await conn.fetchrow("""SELECT baseline_model,baseline_cost_usd FROM tank.ledger
                WHERE id=$1 AND tenant_id=$2 AND agent='ghost'""", ledger, body.tenant_id)
    cost = float(row['baseline_cost_usd']) if row['baseline_cost_usd'] is not None else None
    return {'tenant_id': str(body.tenant_id), 'receipt_id': body.receipt_id, 'cost_usd': 0,
            'ledger_id': str(ledger), 'would_have_cost_usd': cost, 'baseline_model': row['baseline_model'],
            'baseline_status': 'priced' if cost is not None else 'unpriced',
            'reference_tokens_in': incoming, 'reference_tokens_out': outgoing,
            'baseline_basis': 'structured decision; Tank reference BPE budget, not provider billed usage'}
