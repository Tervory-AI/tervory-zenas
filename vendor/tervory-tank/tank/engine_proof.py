"""Tenant-scoped traffic proof; graduate.py owns the automatic decision."""
import json
from fastapi import HTTPException

import engine_ladder as ladder
import graduate
import engine_quality
import engine_runtime
import tervor_serving


def unpack(value):
    return json.loads(value) if isinstance(value, str) else value


def affordable(roster, candidate, serving):
    if graduate.is_skill(candidate):
        return True
    a, b = roster.get(candidate, {}), roster.get(serving, {})
    keys = ('input_usd_per_m', 'output_usd_per_m')
    return all(isinstance(a.get(k), (float, int)) and isinstance(b.get(k), (float, int))
               and a[k] <= b[k] for k in keys)


async def options(rt, req, policy):
    info = policy['_engine']
    roster = {r['name']: r for r in await rt.list_models()}
    async with rt.pool().acquire() as conn:
        rows = await conn.fetch('''SELECT model,checkpoint FROM tank.engine_candidates
            WHERE tenant_id=$1 AND region=$2 AND task_type=$3 ORDER BY created_at DESC''',
            req.tenant_id, rt.box_region(), req.task_type)
    rows = await tervor_serving.pinned_candidates(rt, rows)
    names = info['ladder']
    earlier = names[:names.index(policy['model'])] if policy['model'] in names else []
    rows = [*rows, *({'model': name, 'checkpoint': 'runtime-b12173'} for name in earlier)]
    return [{'model': row['model'], 'revision': ladder.digest([info['revision'], row['model'], row['checkpoint'], engine_quality.revision(req.task_type)])}
            for row in rows if engine_runtime.available(row['model']) and row['model'] != policy['model'] and affordable(roster, row['model'], policy['model'])
            and not rt.is_build_only_model(row['model'])]


async def evidence(rt, req, candidate):
    async with rt.pool().acquire() as conn:
        row = await conn.fetchrow('''SELECT count(*) AS samples,avg(p.agreement) AS agreement,
            count(*) FILTER(WHERE l.feedback='rejected') AS rejected,
            count(*) FILTER(WHERE l.feedback IS NOT NULL) AS judged
            FROM tank.engine_proofs p JOIN tank.ledger l ON l.id=p.ledger_id AND l.tenant_id=p.tenant_id
            WHERE p.tenant_id=$1 AND p.region=$2 AND p.task_type=$3 AND p.model=$4 AND p.revision=$5''',
            req.tenant_id, rt.box_region(), req.task_type, candidate['model'], candidate['revision'])
    return graduate.Evidence(samples=row['samples'], agreement=float(row['agreement']) if row['agreement'] is not None else None,
                             rejected=row['rejected'], judged=row['judged'])


def proven(policy, proof):
    # DECISION no tenant can lower the existing 99% / 200 distinct-input floor.
    return graduate.proven_reason({**policy, 'eval_threshold': max(.99, float(policy.get('eval_threshold') or .99)),
        'min_shadow_samples': max(200, int(policy.get('min_shadow_samples') or 200)),
        'max_reject_rate': min(.05, float(policy.get('max_reject_rate') or .05))}, proof)


async def winner(rt, req, policy, caller=None):
    samples = await ladder.cases(rt, req)
    if not samples:
        return policy
    for candidate in await options(rt, req, policy):
        async with rt.pool().acquire() as conn:
            graduated = await conn.fetchval('''SELECT true FROM tank.engine_graduations
                WHERE tenant_id=$1 AND region=$2 AND task_type=$3 AND model=$4 AND revision=$5''',
                req.tenant_id, rt.box_region(), req.task_type, candidate['model'], candidate['revision'])
        if (not graduated or not proven(policy, await evidence(rt, req, candidate))[0]
                or not await engine_quality.proof_passes(rt, req, candidate)):
            continue
        score = await ladder.evaluate(rt, req, policy, candidate['model'], samples, candidate['revision'], caller)
        if score is None or score < max(.99, float(policy.get('eval_threshold') or .99)):
            continue
        model = candidate['model']
        prices = {r['name']: r for r in await rt.list_models()}.get(model, {})
        return {**policy, 'model': model, 'rung': 3 if graduate.is_skill(model) else 2,
            '_engine': {**policy['_engine'], 'graduated_model': model, 'candidate_revision': candidate['revision'],
                        'attempts': [*policy['_engine']['attempts'], {'model': model, 'agreement': score}],
                        'free': graduate.is_skill(model) or all(prices.get(k) == 0
                            for k in ('input_usd_per_m', 'output_usd_per_m'))}}
    return policy


async def retain_proven_rung(rt, req, policy, caller=None):
    """A new gold revision alone cannot silently promote an established task."""
    async with rt.pool().acquire() as conn:
        current = await conn.fetchval('''SELECT model FROM tank.engine_state
            WHERE tenant_id=$1 AND region=$2 AND task_type=$3''', req.tenant_id, rt.box_region(), req.task_type)
    names = policy['_engine']['ladder']
    if current in names and names.index(current) > names.index(policy['model']):
        agreement = await ladder.evaluate(rt, req, policy, current, await ladder.cases(rt, req),
                                          policy['_engine']['revision'], caller)
        if agreement is not None and agreement < max(.99, float(policy.get('eval_threshold') or .99)):
            raise HTTPException(422, 'Current rung failed evals; cheaper rung still needs traffic proof')
        return {**policy, 'model': current, 'rung': 2 if current in ladder.LOCAL else 1,
                '_engine': {**policy['_engine'], 'free': current in ladder.LOCAL}}
    return policy


async def tick(rt, req, policy, caller=None, limit=1):
    """Replay bounded accepted recorded traffic, never count repetitions as proof."""
    samples = await ladder.cases(rt, req)
    if not samples:
        return {'state': 'held', 'reason': 'no tenant gold evals'}
    for candidate in await options(rt, req, policy):
        model, revision = candidate['model'], candidate['revision']
        score = await ladder.evaluate(rt, req, policy, model, samples, revision, caller)
        if score is None or score < max(.99, float(policy.get('eval_threshold') or .99)):
            continue
        async with rt.pool().acquire() as conn:
            rows = await conn.fetch('''SELECT l.id,t.messages,t.content FROM tank.ledger l
                JOIN tank.transcripts t ON t.ledger_id=l.id AND t.tenant_id=l.tenant_id
                WHERE l.tenant_id=$1 AND l.region=$2 AND l.task_type=$3 AND l.model=$4
                  AND l.outcome='delivered' AND NOT l.cache_hit AND l.feedback IS DISTINCT FROM 'rejected'
                  AND (l.feedback='accepted' OR l.accept_after<=now()) ORDER BY l.ts DESC LIMIT 1000''',
                req.tenant_id, rt.box_region(), req.task_type, policy['model'])
            seen = set(await conn.fetchval('''SELECT coalesce(array_agg(input_hash),'{}'::text[]) FROM tank.engine_proofs
                WHERE tenant_id=$1 AND region=$2 AND task_type=$3 AND model=$4 AND revision=$5''',
                req.tenant_id, rt.box_region(), req.task_type, model, revision))
        processed = 0
        for row in rows:
            messages = unpack(row['messages'])
            fingerprint = ladder.digest(graduate.semantic_form(messages))
            if fingerprint in seen:
                continue
            replay = req.model_copy(update={'messages': [rt.Message.model_validate(m) for m in messages],
                'task_type': req.task_type + '#engine-shadow', 'priority': 'standard'})
            selected = {**policy, 'model': model, 'rung': 3 if graduate.is_skill(model) else 1,
                'system_prompt': '', 'cache_scope': 'none', 'semantic_cache': False,
                'candidate_model': None, 'shadow_rate': 0, 'fallbacks': []}
            if graduate.is_skill(model):
                content, _ = await rt._run_code_candidate(replay, model, messages)
                answer = await rt._record_local(replay, selected, model=model, rung=3, content=content)
            else:
                answer = await rt._run_route(replay, caller=caller, _paid_policy=selected)
            if answer.model != model:
                continue
            agreement = ladder.score(row['content'], answer.content, 'exact')
            quality = await engine_quality.assess(rt, req, policy, model, messages, row['content'], answer.content, caller)
            if not quality.get('scores'):
                return {'state': 'held', 'model': model, 'reason': quality['reason']}
            async with rt.pool().acquire() as conn:
                await conn.execute('''INSERT INTO tank.engine_proofs
                    (tenant_id,region,task_type,model,revision,input_hash,ledger_id,candidate_ledger_id,agreement,quality)
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb) ON CONFLICT DO NOTHING''',
                    req.tenant_id, rt.box_region(), req.task_type, model, revision, fingerprint,
                    row['id'], answer.ledger_id, agreement, json.dumps(quality))
            seen.add(fingerprint)
            processed += 1
            if processed >= limit:
                break
        proof = await evidence(rt, req, candidate)
        passed, reason = proven(policy, proof)
        if passed and not await engine_quality.proof_passes(rt, req, candidate):
            passed, reason = False, 'DeepEval correctness, faithfulness or task criteria below 99%'
        if passed:
            async with rt.pool().acquire() as conn:
                await conn.execute('''INSERT INTO tank.engine_graduations
                    (tenant_id,region,task_type,model,revision,previous_model,samples,agreement)
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING''',
                    req.tenant_id, rt.box_region(), req.task_type, model, revision, policy['model'], proof.samples, proof.agreement)
        return {'state': 'graduated' if passed else 'candidate', 'model': model,
                'samples': proof.samples, 'agreement': proof.agreement, 'reason': reason}
    return {'state': 'held', 'reason': 'no cheaper candidate passed gold evals'}
