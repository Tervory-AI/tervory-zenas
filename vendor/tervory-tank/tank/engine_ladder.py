"""Eval-gated serving ladder. Existing Tank dispatch owns every call and receipt."""
from __future__ import annotations

import hashlib
import json
import asyncio
from typing import Any

from fastapi import HTTPException
import graduate
import engine_models
import engine_runtime

LOCAL = engine_models.LOCAL
OPEN = engine_models.OPEN


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def order(policy: dict) -> list[str]:
    """DECISION keep the configured model as the final frontier, never as a shortcut."""
    first = [policy['model']] if graduate.is_skill(policy['model']) else []
    frontier = policy.get('escalation_model') if first else policy['model']
    return [name for name in dict.fromkeys([*first, *LOCAL, *OPEN, *([frontier] if frontier else [])])
            if engine_runtime.available(name)]


def rung(model: str) -> str:
    if graduate.is_skill(model):
        return 'code'
    if model in LOCAL:
        return 'local small' if model == LOCAL[0] else 'local large'
    return 'open-weight' if model in OPEN else 'frontier'


async def cases(rt, req) -> list[dict]:
    async with rt.pool().acquire() as conn:
        rows = await conn.fetch("""SELECT id,input,expected,tolerance FROM tank.evals
            WHERE tenant_id=$1 AND task_type=$2 AND verdict='accepted'
            ORDER BY id""", req.tenant_id, req.task_type)
    result = []
    for row in rows:
        item = dict(row)
        for key in ('input', 'expected'):
            if isinstance(item[key], str):
                item[key] = json.loads(item[key])
        result.append(item)
    return result


def score(expected, content, tolerance: str) -> float:
    if tolerance == 'exact':
        if isinstance(expected, (dict, list)):
            try:
                return float(json.loads(content) == expected)
            except (ValueError, TypeError):
                return 0.0
        return float(str(expected).strip().casefold() == content.strip().casefold())
    if tolerance == 'contains':
        return float(str(expected).casefold() in content.casefold())
    return graduate.score_pair(expected, content)


async def evaluate(rt, req, policy, model, samples, revision, caller) -> float | None:
    if not samples:
        return None
    async with rt.pool().acquire() as conn:
        cached = await conn.fetchval("""SELECT agreement FROM tank.engine_evals
            WHERE tenant_id=$1 AND region=$2 AND task_type=$3 AND model=$4 AND revision=$5""",
            req.tenant_id, rt.box_region(), req.task_type, model, revision)
    if cached is not None:
        return float(cached)
    scores = []
    for sample in samples:
        payload = sample['input']
        messages = payload.get('messages') if isinstance(payload, dict) else None
        if not messages:
            text = payload.get('text') if isinstance(payload, dict) else payload
            messages = [{'role': 'user', 'content': text if isinstance(text, str) else json.dumps(payload)}]
        evaluation = rt.RouteRequest.model_validate({**req.model_dump(), 'messages': messages,
            'task_type': req.task_type + '#engine-eval', 'priority': 'standard'})
        selected = {**policy, 'model': model, 'rung': 3 if graduate.is_skill(model) else 1,
                    'cache_scope': 'none', 'candidate_model': None, 'shadow_rate': 0, 'semantic_cache': False}
        # No raw HTTP or unmetered paid screen. Existing privacy, admission and
        # ledger logic also runs for evaluation calls. Transport errors hold.
        if graduate.is_skill(model):
            try:
                async with asyncio.timeout(30):
                    content, _ = await rt._run_code_candidate(evaluation, model, messages)
                failure = graduate.code_failure(json.loads(content), float(policy.get('confidence_floor') or .8))
            except HTTPException:
                raise
            except (RuntimeError, ValueError, TimeoutError):
                content, failure = '', 'code_eval_failed'
            answer = await rt._record_local(evaluation, selected, model=model, rung=3, content=content,
                outcome='unverified' if failure else 'delivered', error=failure)
            if failure:
                scores.append(0.0)
                continue
        else:
            answer = await rt._run_route(evaluation, caller=caller, _paid_policy=selected)
        if answer.model != model:
            raise HTTPException(503, 'Engine evaluation did not run its requested model')
        scores.append(score(sample['expected'], answer.content, sample['tolerance']))
    agreement = sum(scores) / len(scores)
    async with rt.pool().acquire() as conn:
        await conn.execute("""INSERT INTO tank.engine_evals
            (tenant_id,region,task_type,model,revision,agreement,samples)
            VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING""",
            req.tenant_id, rt.box_region(), req.task_type, model, revision, agreement, len(scores))
    return agreement


async def select(rt, req, policy, caller=None) -> dict:
    if graduate.is_skill(policy['model']):
        # A missing configured package is a refusal, not evidence that permits
        # a model rung or a mixture of paid seats to replace it.
        rt.get_package(*graduate.parse_skill(policy['model']))
    if not graduate.is_skill(policy['model']) and hasattr(rt, 'pool'):
        async with rt.pool().acquire() as conn:
            approved = await conn.fetchrow("""SELECT s.id,s.version FROM tank.skills s
                LEFT JOIN tank.agent_skill_submissions a ON a.skill_id=s.id AND a.version=s.version
                WHERE s.id=$1 AND s.state='approved' AND (s.task_type IS NULL OR s.task_type='')
                  AND (a.tenant_id IS NULL OR (a.tenant_id=$2 AND a.region=$3))
                ORDER BY s.version DESC LIMIT 1""", req.task_type, req.tenant_id, rt.box_region())
        if approved:
            policy = {**policy, 'model': f"skill:{approved['id']}@{approved['version']}",
                      'escalation_model': policy['model']}
    samples = await cases(rt, req)
    revision = digest([policy, samples, 'local-runtime-b12173-qwen-instruct2507-v2', engine_models.SOURCE_BLOB])
    names = {row['name'] for row in await rt.list_models()}
    attempts = []
    for model in order(policy):
        if not graduate.is_skill(model) and model not in names:
            if model in LOCAL:
                continue  # An offline deployment is skipped, never scored as a failed eval.
            raise HTTPException(503, f'Engine rung unavailable: {model}')
        agreement = await evaluate(rt, req, policy, model, samples, revision, caller)
        attempts.append({'model': model, 'agreement': agreement})
        if agreement is None or agreement >= max(.99, float(policy.get('eval_threshold') or .99)):
            selected = {**policy, 'model': model, 'rung': 3 if graduate.is_skill(model) else 2 if model in LOCAL else 1,
                    # DECISION default to the existing conservative equivalence
                    # cache only where the task explicitly permits caching.
                    'semantic_cache': policy.get('cache_scope') in {'tenant', 'platform', 'region'} and req.task_type != 'command.turn' if req else False,
                    'escalation_model': next((m for m in order(policy) if not graduate.is_skill(m) and m in names), None) if graduate.is_skill(model) else None,
                    'fallbacks': [], '_engine': {'revision': revision, 'attempts': attempts, 'ladder': order(policy),
                    'free': model in LOCAL or graduate.is_skill(model), 'local_status': engine_runtime.local_status(),
                    'baseline_model': policy.get('escalation_model') if graduate.is_skill(policy['model']) else policy['model']}}
            if hasattr(rt, 'pool'):
                from engine_proof import retain_proven_rung, winner
                selected = await retain_proven_rung(rt, req, selected, caller)
                selected = await winner(rt, req, selected, caller)
                async with rt.pool().acquire() as conn:
                    await conn.execute('''INSERT INTO tank.engine_state(tenant_id,region,task_type,model,revision)
                        VALUES($1,$2,$3,$4,$5) ON CONFLICT(tenant_id,region,task_type)
                        DO UPDATE SET model=excluded.model,revision=excluded.revision,updated_at=now()''',
                        req.tenant_id, rt.box_region(), req.task_type, selected['model'], revision)
            return selected
    raise HTTPException(422, 'No engine rung passed this task type’s evals')


async def receipt(rt, req, policy, answer):
    info = policy.get('_engine')
    if not info or not isinstance(answer, rt.RouteResponse):
        return answer
    baseline = info['baseline_model']
    roster = {row['name']: row for row in await rt.list_models()}
    prices = roster.get(baseline, {})
    # Counterfactual estimate uses actual tokens, or a labelled character estimate
    # for a cache/code result. Never present infrastructure savings as API spend.
    tokens_in = answer.tokens_in or max(1, sum(len(m.content) for m in req.messages) // 4)
    tokens_out = answer.tokens_out or max(1, len(answer.content) // 4)
    a, b = prices.get('input_usd_per_m'), prices.get('output_usd_per_m')
    would = (tokens_in * a + tokens_out * b) / 1e6 if a is not None and b is not None else None
    value = {'local_status': info.get('local_status', engine_runtime.local_status()), 'rung': 'cache' if answer.rung == 4 else rung(answer.model),
             'would_have_cost_usd': would, 'baseline_model': baseline, 'estimated': True,
             'evals': info['attempts'], 'ledger_id': str(answer.ledger_id)}
    if info.get('graduated_model') == answer.model and info.get('free') and value['rung'] == 'frontier':
        value['rung'] = 'local trained'
    import engine_memory
    value['memory_hits'] = info.get('memory', {}).get('hits', 0)
    value['memory_status'] = info.get('memory', {}).get('status', 'unavailable')
    value['remembered'] = await engine_memory.remember(req, answer)
    async with rt.pool().acquire() as conn:
        await conn.execute("""INSERT INTO tank.engine_receipts(tenant_id,region,task_type,ledger_id,detail)
            VALUES($1,$2,$3,$4,$5::jsonb) ON CONFLICT(tenant_id,ledger_id) DO NOTHING""",
            req.tenant_id, rt.box_region(), req.task_type, answer.ledger_id, json.dumps(value))
    # The response remains valid if an optional candidate is unavailable. Every
    # successful replay still uses Tank's privacy, admission and ledger path.
    try:
        value['graduation'] = await graduate.engine_tick(rt, req, policy)
    except (HTTPException, RuntimeError, ValueError) as exc:
        value['graduation'] = {'state': 'held', 'reason': type(exc).__name__}
    return answer.model_copy(update={'engine': value})
