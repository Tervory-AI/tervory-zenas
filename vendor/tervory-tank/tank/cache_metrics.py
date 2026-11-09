"""Receipt-backed per-task cache counters; no prompt or identity enters metrics."""
import json

LAYERS = ('C1', 'C4', 'C5')


def empty():
    return {layer: dict(attempts=0, hits=0, hit_rate=0., saved_usd=0.,
        saved_compute_ms=0., cached_tokens=0, input_tokens=0, token_hit_rate=0., unpriced_calls=0) for layer in LAYERS}


async def record(rt, req, answer, layer, metrics, *, conn=None):
    async def write(conn):
        await conn.execute('''INSERT INTO tank.engine_receipts(tenant_id,region,task_type,ledger_id,detail)
            VALUES($1,$2,$3,$4,jsonb_build_object('cache_layers',jsonb_build_object($5::text,$6::jsonb)))
            ON CONFLICT(tenant_id,ledger_id) DO UPDATE SET detail=jsonb_set(tank.engine_receipts.detail,
              '{cache_layers}',coalesce(tank.engine_receipts.detail->'cache_layers','{}'::jsonb)
                || (EXCLUDED.detail->'cache_layers'))''',
            req.tenant_id, rt.box_region(), req.task_type, answer.ledger_id, layer, json.dumps(metrics))
    if conn is None:
        async with rt.pool().acquire() as connection:
            await write(connection)
    else:
        await write(conn)
    engine = dict(answer.engine or {})
    engine['cache_layers'] = {**engine.get('cache_layers', {}), layer: metrics}
    return answer.model_copy(update={'engine': engine})


async def status(rt, tenant_id):
    async with rt.pool().acquire() as conn:
        rows = await conn.fetch('''SELECT r.task_type,c.key AS layer,
            coalesce(sum((c.value->>'attempts')::bigint),0) AS attempts,
            coalesce(sum((c.value->>'hits')::bigint),0) AS hits,
            coalesce(sum((c.value->>'saved_usd')::numeric),0) AS saved_usd,
            coalesce(sum((c.value->>'saved_compute_ms')::numeric),0) AS saved_compute_ms,
            coalesce(sum((c.value->>'cached_tokens')::bigint),0) AS cached_tokens,
            coalesce(sum((c.value->>'unpriced_calls')::bigint),0) AS unpriced_calls,
            coalesce(sum((c.value->>'input_tokens')::bigint),0) AS input_tokens
            FROM tank.engine_receipts r CROSS JOIN LATERAL jsonb_each(coalesce(r.detail->'cache_layers','{}'::jsonb)) c
            WHERE r.tenant_id=$1 AND r.region=$2 GROUP BY r.task_type,c.key''', tenant_id, rt.box_region())
    result = {}
    for row in rows:
        value = {k: float(row[k]) if k in {'saved_usd', 'saved_compute_ms'} else int(row[k])
                 for k in ('attempts', 'hits', 'saved_usd', 'saved_compute_ms', 'cached_tokens', 'input_tokens', 'unpriced_calls')}
        value['hit_rate'] = value['hits'] / value['attempts'] if value['attempts'] else 0.
        value['token_hit_rate'] = value['cached_tokens'] / value['input_tokens'] if value['input_tokens'] else 0.
        result.setdefault(row['task_type'], empty())[row['layer']] = value
    return result
