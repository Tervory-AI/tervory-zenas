"""Explicit adapter vs current-rung gold evaluation through Tank and DeepEval."""
from __future__ import annotations

import asyncio
import json
import math
from uuid import uuid4

from fastapi import HTTPException
import engine_quality
import tervor_gold
import tervor_registry

MIN_CASES = 200
THRESHOLD = .99
METRICS = ('correctness', 'faithfulness', 'task')


def valid_scores(value):
    scores = value.get('scores', {})
    return (set(scores) == set(METRICS) and all(type(scores[k]) in (int, float)
        and math.isfinite(scores[k]) and 0 <= scores[k] <= 1 for k in METRICS))


async def reports(conn, tenant, model_id, *, region: str):
    if region not in ('us-east', 'za'):
        raise ValueError('Invalid evaluation region')
    rows = await conn.fetch('''SELECT detail FROM tank.policy_log
        WHERE task_type='tervor.evaluation' AND detail->>'tenant_id'=$1 AND detail->>'model_id'=$2
        AND detail->>'region'=$3
        ORDER BY created_at DESC,id DESC''', str(tenant), model_id, region)
    return [json.loads(row['detail']) if isinstance(row['detail'], str) else row['detail'] for row in rows]


async def evaluate(rt, req, model_id: str, gold: dict, *, caller=None) -> dict:
    """No promotion here. Every evaluated model is explicit evidence, not a default."""
    async with rt.pool().acquire() as conn:
        model = await tervor_registry.get(conn, req.tenant_id, model_id)
    if model is None:
        raise ValueError('Model is not registered for this tenant')
    cases = tervor_gold.verify(gold, req.tenant_id, model['product'])
    region = rt.box_region()
    if region not in ('us-east', 'za') or gold.get('region') != region:
        raise ValueError('Gold region does not match')
    report = dict(id=str(uuid4()), tenant_id=str(req.tenant_id), model_id=model_id, region=region,
        version=model['version'], gold_sha256=gold['sha256'], quality_version=engine_quality.VERSION,
        count=len(cases), completed=0, eligible=False, scores={}, baselines={}, ledger_ids=[],
        cost_usd=0., status='held', reason='At least 200 gold cases are required')
    if hasattr(rt, '_roster_models'):
        roster = {row['model_name']: row for row in await rt._roster_models()}
        report['artifact_sha256'] = roster.get(model_id, {}).get('model_info', {}).get('tervor_serving', {}).get('sha256')
    if len(cases) >= MIN_CASES:
        report['reason'] = 'Evaluation unavailable; no promotion'
        results = []
        try:
            async with asyncio.timeout(900):
                for case in cases:
                    if not case['task_type'].startswith(model['product'] + '.'):
                        raise ValueError('Gold task does not match the model product')
                    policy = await rt.load_policy(case['task_type'], req.tenant_id)
                    async with rt.pool().acquire() as conn:
                        current = await conn.fetchval('''SELECT model FROM tank.engine_state
                            WHERE tenant_id=$1 AND region=$2 AND task_type=$3''',
                            req.tenant_id, rt.box_region(), case['task_type'])
                    if current:
                        policy = {**policy, 'model': current}
                    if policy['model'] == model_id:
                        raise ValueError('Candidate must differ from the current rung')
                    report['baselines'][case['task_type']] = policy['model']
                    messages = case['input'].get('messages') if isinstance(case['input'], dict) else None
                    if not messages:
                        messages = [{'role': 'user', 'content': tervor_registry.encoded(case['input'])}]
                    request = req.model_copy(update={'task_type': case['task_type'], 'priority': 'standard',
                        'messages': [rt.Message(**m) for m in messages]})
                    answers = []
                    for selected_model in (policy['model'], model_id):
                        selected = {**policy, 'model': selected_model, 'candidate_model': None,
                            'shadow_rate': 0, 'cache_scope': 'none', 'semantic_cache': False, 'fallbacks': []}
                        if selected_model == model_id:
                            selected['rung'] = 1
                        async with asyncio.timeout(120):
                            answer = await rt._run_route(request, caller=caller, _paid_policy=selected)
                        report['ledger_ids'].append(str(answer.ledger_id))
                        if answer.cost_usd is None or not math.isfinite(answer.cost_usd) or answer.cost_usd < 0:
                            report['cost_usd'] = None
                            raise ValueError('Evaluation cost unavailable')
                        report['cost_usd'] += float(answer.cost_usd)
                        if answer.model != selected_model:
                            raise ValueError('Evaluation used an unexpected model')
                        expected = case['expected'] if isinstance(case['expected'], str) else tervor_registry.encoded(case['expected'])
                        quality = await engine_quality.assess(rt, request, policy, model_id, messages,
                            expected, answer.content, caller)
                        report['ledger_ids'].extend(quality.get('ledger_ids', []))
                        cost = quality.get('cost_usd')
                        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
                            report['cost_usd'] = None
                            raise ValueError('Judge cost unavailable')
                        report['cost_usd'] += cost
                        if not valid_scores(quality):
                            raise ValueError('DeepEval returned no valid evidence')
                        answers.append(quality['scores'])
                    results.append(answers)
                    report['completed'] += 1
            baseline = {key: sum(pair[0][key] for pair in results) / len(results) for key in METRICS}
            candidate = {key: sum(pair[1][key] for pair in results) / len(results) for key in METRICS}
            report.update(scores=candidate, baseline_scores=baseline, status='evaluated',
                eligible=all(candidate[key] >= max(THRESHOLD, baseline[key]) for key in METRICS),
                reason='Gold evaluation complete; shadow evidence still required')
        except (RuntimeError, ValueError, TimeoutError, HTTPException, OSError):
            report['status'] = 'failed'
    async with rt.pool().acquire() as conn:
        await conn.execute('''INSERT INTO tank.policy_log(task_type,action,by_user,detail)
            VALUES ('tervor.evaluation','report','tank',$1::jsonb)''', tervor_registry.encoded(report))
    return report
