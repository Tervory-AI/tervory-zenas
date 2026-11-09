"""Price a Zenas run against existing Tank ledger and baseline settings."""
from decimal import Decimal
from uuid import UUID, uuid5, NAMESPACE_URL

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from tenant_db import tenant_scope


class Quote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    ledger_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    home_region: str | None = None
    baseline_ledger_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    tokens_in: int = Field(default=0,ge=0, le=100_000_000)
    tokens_out: int = Field(default=0,ge=0, le=100_000_000)
    reference: dict[str,str] | None = None
    uncertain: bool = False
    run_id: UUID | None = None


async def quote(conn, body, region):
    if body.reference is not None:
        if set(body.reference)!={'input','output'} or sum(len(t) for t in body.reference.values())>32*1024*1024:
            raise HTTPException(422,'invalid_reference')
        from agent_skills import input_encoding
        body=body.model_copy(update={'tokens_in':len(input_encoding().encode_ordinary(body.reference['input'])),
                                     'tokens_out':len(input_encoding().encode_ordinary(body.reference['output']))})
    ids=list(set(body.ledger_ids))
    if not ids and not body.uncertain and body.run_id:
        # Code/cache-only runs still participate in Command/MCP's existing
        # tenant-bound receipt linking. One stable ledger identity per run.
        ident=uuid5(NAMESPACE_URL,f'zenas:run:{body.tenant_id}:{body.run_id}')
        await conn.execute("""INSERT INTO tank.ledger(id,tenant_id,region,agent,task_type,rung,model,
            cost_usd,tokens_in,tokens_out,outcome) VALUES($1,$2,$3,'zenas','zenas.rule_card',3,'code',0,$4,$5,'delivered')
            ON CONFLICT(id) DO NOTHING""",ident,body.tenant_id,region,0,0)
        ids=[ident]
    rows = await conn.fetch("""SELECT id,cost_usd,cost_status,research_cost_status,tokens_in,tokens_out,model FROM tank.ledger
        WHERE tenant_id=$1 AND region=$2 AND agent='zenas' AND id=ANY($3::uuid[])""",
        body.tenant_id, region, ids)
    if len(rows) != len(ids):
        raise HTTPException(403, "Receipt evidence is missing or outside this tenant")
    unknown = body.uncertain or any(r['cost_usd'] is None or r['cost_status']=='not_set'
                                  or r['research_cost_status']=='unknown' for r in rows)
    cost = None if unknown else sum((Decimal(str(r['cost_usd'])) for r in rows), Decimal(0))
    baseline_rows = rows
    if body.baseline_ledger_ids:
        baseline_rows = await conn.fetch("""SELECT id,tokens_in,tokens_out FROM tank.ledger
            WHERE tenant_id=$1 AND region=$2 AND agent='zenas' AND id=ANY($3::uuid[])""",
            body.tenant_id, region, list(set(body.baseline_ledger_ids)))
        if len(baseline_rows) != len(set(body.baseline_ledger_ids)):
            raise HTTPException(403, "Receipt evidence is missing or outside this tenant")
    used_in=sum(r['tokens_in'] or 0 for r in baseline_rows)
    used_out=sum(r['tokens_out'] or 0 for r in baseline_rows)
    if used_in or used_out:
        # Scheduled batches include their whole recorded model workload, rather
        # than pricing only the small job-status response. Code/cache receipts
        # without routed usage retain the action's reference BPE comparison.
        body=body.model_copy(update={'tokens_in':used_in,'tokens_out':used_out})
    # Reuse the paid standard/override chosen for this product. Never select a
    # serving provider, scrape a tariff, or price a local model as a paid one.
    price = await conn.fetchrow("""SELECT b.model,b.input_usd,b.output_usd,b.updated_at
        FROM tank.policy p JOIN tank.proof_prices a ON a.model=p.model
        LEFT JOIN tank.baseline_settings s ON s.task_type=p.task_type
        JOIN tank.proof_prices b ON b.model=coalesce(s.override_model,tank.standard_model_for(a.family))
        WHERE p.task_type='zenas.rule_card' AND (p.tenant_id=$1 OR p.tenant_id IS NULL)
          AND b.active AND b.input_usd>=0 AND b.output_usd>=0
        ORDER BY p.tenant_id NULLS LAST LIMIT 1""", body.tenant_id)
    baseline = None if price is None else body.tokens_in*price['input_usd']+body.tokens_out*price['output_usd']
    return {"cost": float(cost) if cost is not None else None,
            "would have cost": float(baseline) if baseline is not None else None,
            "baseline_model": price['model'] if price else None,
            "priced_at": price['updated_at'].isoformat() if price else None,
            "ledger_ids": [str(i) for i in sorted(ids,key=str)],
            "tokens_in":body.tokens_in, "actual_tokens_in":sum(r['tokens_in'] or 0 for r in rows if r['model'] != "code"),
            "tokens_out":body.tokens_out, "actual_tokens_out":sum(r['tokens_out'] or 0 for r in rows if r['model'] != "code"),
            "paid_calls":sum(bool(r['cost_usd']) for r in rows)}


def build_router(tank):
    router = APIRouter(prefix="/v1/zenas/savings")

    @router.post('/quote')
    async def price_run(body: Quote, caller=Depends(tank.require_key)):
        if caller != 'zenas':
            raise HTTPException(403, 'Zenas room identity required')
        with tenant_scope(body.tenant_id):
            async with tank.pool().acquire() as conn:
                return await quote(conn, body, tank.box_region())

    return router


def install(tank):
    """Mount the specific evidence route before the existing room catch-all."""
    routes = tank.app.router.routes
    position = next(i for i, route in enumerate(routes) if getattr(route, "path", "") == "/v1/zenas/{path:path}")
    routes[position:position] = build_router(tank).routes
