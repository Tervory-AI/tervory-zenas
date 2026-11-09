"""Founder registration for a served trained checkpoint; registration never promotes."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from command import Actor, identity, require_founder
from tervor_serving import Serving


class Candidate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: uuid.UUID
    task_type: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    checkpoint: str = Field(pattern=r'^[a-f0-9]{64}$')
    serving: Serving | None = None


def build_router(rt):
    router = APIRouter(prefix='/v1/engine', tags=['engine'])
    __import__('codebase_index').register(router, rt, identity, require_founder)

    @router.get('/status')
    async def status(tenant_id: uuid.UUID, actor: Actor = Depends(identity)):
        require_founder(actor)
        return await engine_status(rt, tenant_id)

    @router.post('/candidates')
    async def register(body: Candidate, actor: Actor = Depends(identity)):
        require_founder(actor)
        if body.model.startswith('tervor-'):
            import tervor_serving
            return await tervor_serving.register(rt, body)
        roster = {row['name']: row for row in await rt.list_models()}
        if body.model not in roster or rt.is_build_only_model(body.model):
            raise HTTPException(422, 'Candidate must be on the serving roster')
        await rt.load_policy(body.task_type, body.tenant_id)
        async with rt.pool().acquire() as conn:
            await conn.execute('''INSERT INTO tank.engine_candidates(tenant_id,region,task_type,model,checkpoint)
                VALUES($1,$2,$3,$4,$5) ON CONFLICT(tenant_id,region,task_type,model)
                DO UPDATE SET checkpoint=excluded.checkpoint,created_at=now()''',
                body.tenant_id, rt.box_region(), body.task_type, body.model, body.checkpoint)
        return {'state': 'candidate', 'model': body.model, 'checkpoint': body.checkpoint}

    return router


async def engine_status(rt, tenant_id):
    import engine_ladder
    import engine_tracing
    import engine_runtime
    async with rt.pool().acquire() as conn:
        tasks = await conn.fetch('''SELECT task_type FROM tank.policy WHERE tenant_id=$1 OR tenant_id IS NULL
            UNION SELECT task_type FROM tank.engine_state WHERE tenant_id=$1 AND region=$2
            UNION SELECT task_type FROM tank.engine_receipts WHERE tenant_id=$1 AND region=$2''', tenant_id, rt.box_region())
        states = await conn.fetch('''SELECT task_type,model FROM tank.engine_state
            WHERE tenant_id=$1 AND region=$2''', tenant_id, rt.box_region())
        receipts = await conn.fetch('''SELECT r.task_type,count(*) AS requests,
            count(*) FILTER(WHERE (r.detail->>'memory_hits')::integer>0) AS memory_hits,
            count(*) FILTER(WHERE l.cache_hit) AS cache_hits,
            coalesce(sum((r.detail->>'would_have_cost_usd')::numeric),0) AS would_have_cost_usd
            FROM tank.engine_receipts r JOIN tank.ledger l ON l.id=r.ledger_id AND l.tenant_id=r.tenant_id
            WHERE r.tenant_id=$1 AND r.region=$2 GROUP BY r.task_type''', tenant_id, rt.box_region())
        costs = await conn.fetch('''SELECT split_part(task_type,'#engine-',1) AS task_type,
            coalesce(sum(cost_usd),0) AS cost_usd,
            coalesce(sum(cost_usd) FILTER(WHERE task_type LIKE '%#engine-%'),0) AS evaluation_cost_usd
            FROM tank.ledger l WHERE l.tenant_id=$1 AND l.region=$2
              AND (l.task_type LIKE '%#engine-eval' OR l.task_type LIKE '%#engine-shadow' OR l.task_type LIKE '%#engine-judge'
                   OR EXISTS (SELECT 1 FROM tank.engine_receipts r
                       WHERE r.tenant_id=l.tenant_id AND r.region=l.region AND r.ledger_id=l.id))
            GROUP BY split_part(task_type,'#engine-',1)''',
            tenant_id, rt.box_region())
        moves = await conn.fetch('''SELECT task_type,count(*) AS graduations FROM tank.engine_graduations
            WHERE tenant_id=$1 AND region=$2 GROUP BY task_type''', tenant_id, rt.box_region())
    current = {r['task_type']: r['model'] for r in states}
    usage, spend, graduations = ({r['task_type']: dict(r) for r in rows} for rows in (receipts, costs, moves))
    result = []
    roster = {r['name']: r for r in await rt.list_models()}
    cache_usage = await __import__('cache_metrics').status(rt, tenant_id)
    for name in sorted({r['task_type'] for r in tasks}):
        model = current.get(name)
        rung = engine_ladder.rung(model) if model else 'unselected'
        if model and rung == 'frontier' and all(roster.get(model, {}).get(k) == 0 for k in ('input_usd_per_m', 'output_usd_per_m')):
            rung = 'local trained'
        if model and not engine_runtime.available(model):
            rung = 'offline'
        row = {'task_type': name, 'model': model, 'rung': rung, 'local_status': engine_runtime.local_status(), 'requests': 0, 'memory_hits': 0,
               'cache_hits': 0, 'graduations': 0, 'cost_usd': 0., 'evaluation_cost_usd': 0.,
               'would_have_cost_usd': 0., **usage.get(name, {}), **spend.get(name, {}), **graduations.get(name, {})}
        for key in ('cost_usd', 'evaluation_cost_usd', 'would_have_cost_usd'):
            row[key] = float(row[key])
        row['saved_usd'] = max(0., row['would_have_cost_usd'] - row['cost_usd'])
        row['traces_url'] = engine_tracing.traces_url(tenant_id, name)
        row['cache_layers'] = cache_usage.get(name, __import__('cache_metrics').empty())
        result.append(row)
    return {'tenant_id': str(tenant_id), 'region': rt.box_region(), 'local_status': engine_runtime.local_status(), 'tasks': result}
