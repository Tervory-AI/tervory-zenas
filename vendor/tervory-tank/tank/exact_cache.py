"""C1 eligibility and identities shared by Tank's fast path and LiteLLM cache."""
import hashlib
import json
import os

import product_identity


def enabled():
    return os.environ.get('ENGINE_C1_ENABLED') == '1'


def sql_ttl(policy, legacy):
    # DECISION the off path retains main's SQL cache; only C1 uses task TTLs.
    return int(policy.get('cache_ttl_s', 0)) if enabled() else legacy


class CachedAnswer(str):
    def __new__(cls, value, layer):
        result = super().__new__(cls, value)
        result.layer = layer
        return result


def user_scope(req):
    actor = product_identity.current.get()
    if actor is None or actor.tenant_id != str(req.tenant_id):
        return None
    return actor.user_id


def policy_eligible(req, policy):
    return (policy.get('cache_scope') in {'tenant', 'platform', 'region'}
            and int(policy.get('cache_ttl_s', 0)) > 0
            and (not policy.get('matter_matching') or bool(req.case_ref)))


def eligible(req, policy):
    return user_scope(req) is not None and policy_eligible(req, policy)


def request_options(req, policy, identity, model, *, safe=True, streaming=False):
    if not enabled():
        return {}
    # DECISION reuse the regional Redis; response caching is opt-in and user-scoped.
    # A service call without a verified actor cannot opt into a personal answer cache.
    if not safe or streaming or not eligible(req, policy):
        return {'cache': {'no-cache': True, 'no-store': True, 'use-cache': False}}
    scope = [str(req.tenant_id), user_scope(req), req.task_type, *identity[:2], model]
    digest = hashlib.sha256(json.dumps(scope, separators=(',', ':')).encode()).hexdigest()
    ttl = int(policy.get('cache_ttl_s', 0))
    return {'cache': {'use-cache': True, 'ttl': ttl, 's-maxage': ttl,
                      'namespace': 'tank:C1:v1:' + digest}}


async def gateway_options(rt, req, policy, identity, model, **kwargs):
    options = request_options(req, policy, identity, model, **kwargs)
    if options.get('cache', {}).get('use-cache'):
        async with rt.pool().acquire() as conn:
            rejected = await conn.fetchval('''SELECT l.feedback='rejected'
                FROM tank.route_cache c JOIN tank.ledger l
                  ON l.tenant_id=c.tenant_id AND l.id=c.ledger_id
                WHERE c.tenant_id=$1 AND c.region=$2 AND c.task_type=$3
                  AND c.context_hash=$4 AND c.input_hash=$5''',
                req.tenant_id, rt.box_region(), req.task_type, identity[0], identity[1])
        if rejected:
            # Refresh Redis as well as SQL after negative feedback; never serve
            # a gateway copy of an answer already rejected in Tank.
            options['cache']['no-cache'] = True
    return options


async def record(rt, req, policy, answer):
    """Keep cache receipts for every task, including policies outside engine auto-routing."""
    if not enabled() or not isinstance(answer, rt.RouteResponse):
        return answer
    # Include the existing service-scoped SQL fast path in the denominator too.
    # Unsigned calls still cannot opt into LiteLLM's personal response cache.
    attempted = int(policy_eligible(req, policy))
    hit = answer.cache_layer == 'C1'
    if not attempted and not hit:
        return answer
    row = next((r for r in await rt._roster_models() if r['model_name'] == answer.model), {})
    prices = row.get('model_info', {})
    a, b = prices.get('input_cost_per_token'), prices.get('output_cost_per_token')
    tokens_in = answer.tokens_in or max(1, sum(len(m.content) for m in req.messages) // 4)
    tokens_out = answer.tokens_out or max(1, len(answer.content) // 4)
    would = (tokens_in * a + tokens_out * b) if a is not None and b is not None else None
    metrics = {'attempts': max(attempted, int(hit)), 'hits': int(hit),
               'saved_usd': would if hit else 0., 'estimated': True}
    detail = {'cache_layer': answer.cache_layer, 'cache_backend': answer.cache_backend,
              'cache_layers': {'C1': metrics}}
    if not answer.engine:
        detail.update(would_have_cost_usd=would, estimated=True, baseline_model=answer.model)
    async with rt.pool().acquire() as conn:
        await conn.execute('''INSERT INTO tank.engine_receipts(tenant_id,region,task_type,ledger_id,detail)
            VALUES($1,$2,$3,$4,$5::jsonb) ON CONFLICT(tenant_id,ledger_id)
            DO UPDATE SET detail=(tank.engine_receipts.detail || EXCLUDED.detail)
              || jsonb_build_object('cache_layers', coalesce(tank.engine_receipts.detail->'cache_layers','{}'::jsonb)
                || (EXCLUDED.detail->'cache_layers'))''',
            req.tenant_id, rt.box_region(), req.task_type, answer.ledger_id, json.dumps(detail))
    engine = {**(answer.engine or {}), **detail}
    engine['cache_layers'] = {**(answer.engine or {}).get('cache_layers', {}), **detail['cache_layers']}
    return answer.model_copy(update={'engine': engine})


async def status(rt, tenant_id):
    async with rt.pool().acquire() as conn:
        rows = await conn.fetch('''SELECT task_type,
            coalesce(sum((detail->'cache_layers'->'C1'->>'attempts')::integer),0) AS attempts,
            coalesce(sum((detail->'cache_layers'->'C1'->>'hits')::integer),0) AS hits,
            coalesce(sum((detail->'cache_layers'->'C1'->>'saved_usd')::numeric),0) AS saved_usd
            FROM tank.engine_receipts WHERE tenant_id=$1 AND region=$2 GROUP BY task_type''',
            tenant_id, rt.box_region())
    return {r['task_type']: {'C1': {'attempts': r['attempts'], 'hits': r['hits'],
             'hit_rate': r['hits'] / r['attempts'] if r['attempts'] else 0.,
             'saved_usd': float(r['saved_usd']), 'estimated': True}} for r in rows}
