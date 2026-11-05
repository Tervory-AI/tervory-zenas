"""Platform-only nightly roster bench. No policy.model writes or provider keys.

The router lifespan owns the worker. PostgreSQL locks serialize replicas; durable
reservations survive a crash and remain charged until a known receipt settles them.
Only platform evals and platform held-out gold from this box are replayed.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import logging
import math
import os
import re
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
import httpx
import asyncpg

import graduate
import controls
from gold import PLATFORM_TENANT, encoded, obj, runtime

log = logging.getLogger(__name__)
LOCK_ID = 218001
# Serializes proof_prices writers (startup refresh, paused ticks, bench ticks, replicas).
PRICE_LOCK_ID = 218002


def price_book(rows: list[dict]) -> dict[str, dict]:
    book = {}
    seen = set()
    for row in rows:
        name = row["model_name"]
        if name in seen:
            continue
        seen.add(name)
        info = row.get('model_info') or {}
        a, b = info.get('input_cost_per_token'), info.get('output_cost_per_token')
        if a is None or b is None or not all(math.isfinite(float(x)) and float(x) >= 0 for x in (a, b)):
            continue
        provider = str((row.get('litellm_params') or {}).get('model') or '')
        if info.get('open_weights') and info.get('family'):
            # Hosted weights must not inherit the routing provider's API baselines.
            product = re.sub(r'[-\d].*$', '', str(info.get('hf_model_id') or '').rsplit('/', 1)[-1])
            family = f"{info['family']}/open-{product}"
        else:
            # Provider + product line, not an alias (luna/sol/astra -> openai/gpt).
            family = re.sub(r'[-\d].*$', '', provider.rsplit('/', 1)[-1])
            family = provider.split('/')[0] + '/' + family if '/' in provider else provider
        if not family:
            family = row['model_name']
        book[row['model_name']] = {'input': Decimal(str(a)), 'output': Decimal(str(b)), 'family': family}
    return book


def fingerprints(book: dict) -> tuple[str, str]:
    digest = lambda value: hashlib.sha256(encoded(value).encode()).hexdigest()
    return digest(sorted(book)), digest(book)


def due(state: dict | None, now: datetime, roster: str, prices: str) -> bool:
    return not state or not state.get('bench_at') or (
        state['bench_at'].date() < now.date()
        or state.get('bench_roster_fingerprint') != roster
        or state.get('bench_price_fingerprint') != prices
    )


def choose_candidate(results: list[dict], serving: str) -> str | None:
    complete = {r['model']: r for r in results if r['status'] == 'complete'}
    current = complete.get(serving)
    if not current:
        return None
    eligible = [r for r in complete.values() if r['model'] != serving
                and r['score'] >= current['score'] and r['cost_per_call'] < current['cost_per_call']]
    return min(eligible, key=lambda r: (r['cost_per_call'], r['model']))['model'] if eligible else None


def messages_for(value: Any, system: str | None) -> list[dict]:
    raw = value.get('messages') if isinstance(value, dict) else value if isinstance(value, list) else None
    if isinstance(raw, list) and raw and all(isinstance(m, dict) and m.get('role') in ('user','assistant','system')
                                             and isinstance(m.get('content'), str) for m in raw):
        messages = [{'role': m['role'], 'content': m['content']} for m in raw]
    else:
        messages = [{'role': 'user', 'content': value if isinstance(value, str) else encoded(value)}]
    return ([{'role': 'system', 'content': system}] if system else []) + messages


def reserve_quote(messages: list[dict], max_tokens: int, price: dict) -> Decimal:
    # UTF-8 bytes bound text token counts; allowance covers chat framing.
    # Calls disable gateway fallbacks and reject a mismatched returned model.
    upper_input = len(encoded(messages).encode('utf-8')) + 1024
    return price['input'] * upper_input + price['output'] * max_tokens


async def sync_prices(conn, book: dict) -> None:
    async with conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock($1)', PRICE_LOCK_ID)
        # Avoid transient false/true flips: an unchanged book must not invalidate
        # every client cache when this transaction commits.
        await conn.execute('UPDATE tank.proof_prices SET active=false WHERE active AND NOT (model=ANY($1::text[]))',list(book))
        for name, p in book.items():
            await conn.execute("""INSERT INTO tank.baseline_price_changes(model,old_input,old_output,new_input,new_output)
                SELECT $1,input_usd,output_usd,$2,$3 FROM tank.proof_prices WHERE model=$1
                AND (input_usd,output_usd) IS DISTINCT FROM ($2,$3)""",name,p['input'],p['output'])
            await conn.execute('''INSERT INTO tank.proof_prices(model,family,input_usd,output_usd)
                VALUES ($1,$2,$3,$4) ON CONFLICT(model) DO UPDATE SET family=$2,input_usd=$3,output_usd=$4,
                active=true,updated_at=CASE WHEN (tank.proof_prices.input_usd,tank.proof_prices.output_usd)
                IS DISTINCT FROM ($3,$4) THEN now() ELSE tank.proof_prices.updated_at END''',
                name, p['family'], p['input'], p['output'])
        # Price changes make unavailable comparisons eligible for a new backfill.
        # Defaults come from tank.standard_model_for; prices never select the headline.


async def backfill(conn, tenant: uuid.UUID, region: str) -> None:
    while True:
        changed = await conn.execute("""WITH pending AS (
            SELECT id FROM tank.ledger WHERE tenant_id=$1 AND region=$2 AND baseline_status='pending'
            ORDER BY id LIMIT 5000 FOR UPDATE SKIP LOCKED)
            UPDATE tank.ledger l SET comparison_revision=l.comparison_revision
            FROM pending p WHERE l.id=p.id AND l.tenant_id=$1 AND l.region=$2""", tenant, region)
        if changed == 'UPDATE 0':
            break


async def scheduled_backfill(pool, region: str, price_fp: str) -> None:
    state = await pool.fetchrow("SELECT * FROM tank.search_state WHERE task_type=$1", '@backfill:'+region)
    now = datetime.now(timezone.utc)
    if state and not due(dict(state), now, price_fp, price_fp):
        return
    if not state or state['bench_price_fingerprint'] != price_fp:
        # Retry previously unavailable prices in bounded batches only on price change.
        unpriced = await pool.fetch("SELECT DISTINCT tenant_id FROM tank.ledger WHERE region=$1 AND (baseline_status='unpriced' OR (standard_cost_usd IS NOT NULL AND top_cost_usd IS NULL AND baseline_status<>'pending'))", region)
        for row in unpriced:
            while True:
                changed = await pool.execute("""WITH batch AS (
                    SELECT id FROM tank.ledger WHERE tenant_id=$1 AND region=$2 AND (baseline_status='unpriced' OR (standard_cost_usd IS NOT NULL AND top_cost_usd IS NULL AND baseline_status<>'pending'))
                    ORDER BY id LIMIT 5000 FOR UPDATE SKIP LOCKED)
                    UPDATE tank.ledger l SET baseline_status='pending' FROM batch b
                    WHERE l.id=b.id AND l.tenant_id=$1 AND l.region=$2""", row['tenant_id'], region)
                if changed == 'UPDATE 0':
                    break
    tenants = await pool.fetch("SELECT DISTINCT tenant_id FROM tank.ledger WHERE region=$1 AND baseline_status='pending'", region)
    for tenant in tenants:
        await backfill(pool, tenant['tenant_id'], region)
    await pool.execute("""INSERT INTO tank.search_state(task_type,bench_at,bench_roster_fingerprint,bench_price_fingerprint)
        VALUES($1,now(),$2,$2) ON CONFLICT(task_type) DO UPDATE SET bench_at=now(),
        bench_roster_fingerprint=$2,bench_price_fingerprint=$2""", '@backfill:'+region, price_fp)


async def cases_for(conn, task: str, region: str) -> list[dict]:
    rows = await conn.fetch('''SELECT id,input,expected,'eval' AS kind FROM tank.evals
        WHERE task_type=$1 AND (tenant_id=$2 OR tenant_id IS NULL) AND verdict='accepted' ORDER BY id''', task, PLATFORM_TENANT)
    cases = [{'ref': str(r['id']), 'kind': r['kind'], 'input': obj(r['input']), 'expected': obj(r['expected'])} for r in rows]
    # Approved package evals are valid without any live traffic or checked gold.
    for pkg in sorted(runtime()._packages.values(), key=lambda p: p.ref):
        if pkg.task_type == task:
            state = await conn.fetchval('SELECT state FROM tank.skills WHERE id=$1 AND version=$2', pkg.id, pkg.version)
            if state != 'approved':
                continue
            for i, case in enumerate(pkg.cases):
                cases.append({'ref': f'{pkg.ref}:{i}', 'kind': 'package', 'input': case['input'], 'expected': case['expected']})
    gold = await conn.fetch('''SELECT id,input,expected FROM tank.gold_examples WHERE tenant_id=$1 AND region=$2
        AND status='checked' AND holdout AND (NOT auto_label OR auto_approved)
        AND (skill_id=$3 OR skill_id IN (SELECT id FROM tank.skills WHERE task_type=$3)) ORDER BY id''', PLATFORM_TENANT, region, task)
    cases.extend({'ref': str(r['id']), 'kind': 'holdout', 'input': obj(r['input']), 'expected': obj(r['expected'])} for r in gold)
    return cases


async def reserve(conn, run_id: uuid.UUID, region: str, case: dict, amount: Decimal, cap: Decimal) -> uuid.UUID | None:
    async with conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock($1)', LOCK_ID + 1)
        spent = await conn.fetchval('''SELECT coalesce(sum(coalesce(charged_usd,reserved_usd)),0)
            FROM tank.bench_calls WHERE tenant_id=$1 AND region=$2 AND day=(now() AT TIME ZONE 'UTC')::date''', PLATFORM_TENANT, region)
        if Decimal(spent) + amount > cap:
            return None
        return await conn.fetchval('''INSERT INTO tank.bench_calls(run_id,tenant_id,region,day,case_ref,case_kind,reserved_usd)
            VALUES($1,$2,$3,(now() AT TIME ZONE 'UTC')::date,$4,$5,$6) RETURNING id''', run_id, PLATFORM_TENANT, region, case['ref'], case['kind'], amount)


async def call_model(model: str, messages: list[dict], max_tokens: int) -> dict:
    rt = runtime()
    if str(model or "").strip().lower().startswith("build-"):
        raise ValueError("This model is for Tervory's own build and test work only.")
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(f'{rt.LITELLM_URL}/v1/chat/completions',
            headers={'Authorization': f'Bearer {rt.LITELLM_MASTER_KEY}'},
            json={'model': model, 'messages': messages, 'max_tokens': max_tokens,
                  'temperature': 0, 'disable_fallbacks': True, 'num_retries': 0})
    # Never retain upstream error bodies: they can contain prompt data or keys.
    data = response.json() if response.is_success else {}
    usage = data.get('usage') or {}
    raw_cost = response.headers.get('x-litellm-response-cost')
    cost = Decimal(raw_cost) if raw_cost is not None else None
    if cost is not None and (not cost.is_finite() or cost < 0):
        cost = None
    return {'model': response.headers.get('x-litellm-model-group') or model,
            'content': rt._choice_answer(data)[0] if response.is_success else '',
            'tokens_in': int(usage.get('prompt_tokens') or 0), 'tokens_out': int(usage.get('completion_tokens') or 0),
            'cost': cost, 'ok': response.is_success and bool(usage), 'http_status': response.status_code}


async def bench_model(pool, policy: dict, model: str, cases: list[dict], book: dict, region: str, cap: Decimal, comparison: str) -> dict:
    import paid_steps
    payload = [policy, model, cases, region, str(cap), comparison]
    try:
        approval = await paid_steps.background_admission(policy['task_type'] + '.benchmark', model, payload)
    except paid_steps.ApprovalRequired as pause:
        await paid_steps.save_background(pause, {"kind": "benchmark", "policy": policy, "model": model,
            "cases": cases, "region": region, "cap": str(cap), "comparison": comparison})
        return {'model': model, 'score': None, 'cost_per_call': None, 'status': 'paused'}
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        return {'model':model,'score':None,'cost_per_call':None,'status':'paused'}
    if approval is None:
        return await _bench_model(pool, policy, model, cases, book, region, cap, comparison, paid=False)
    with paid_steps.run_scope(paid_steps.Run(PLATFORM_TENANT, approval["run_id"], approval["request_hash"])):
        paid_steps.remember(paid_steps.current.get(), approval)
        return await paid_steps.checkpoint(paid_steps.fingerprint(["benchmark", payload]),
            lambda: _bench_model(pool, policy, model, cases, book, region, cap, comparison))


async def _bench_model(pool, policy: dict, model: str, cases: list[dict], book: dict, region: str, cap: Decimal, comparison: str, *, paid: bool = True) -> dict:
    suite_hash = hashlib.sha256(encoded({'cases': cases, 'system_prompt': policy.get('system_prompt'),
        'max_tokens_out': policy['max_tokens_out'], 'tolerance': policy.get('number_tolerance')}).encode()).hexdigest()
    run_id = await pool.fetchval('''INSERT INTO tank.bench_runs(tenant_id,region,task_type,model,comparison_model,suite_hash,expected_n)
        VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING id''', PLATFORM_TENANT, region, policy['task_type'], model, comparison, suite_hash, len(cases))
    status, error = 'complete', None
    scores, costs = [], []
    for case in cases:
        try:
            await controls.refuse_paid(paid=paid,
                                       task=policy['task_type'], path='bench')
        except HTTPException:
            status, error = 'paused', 'Paid benchmark work is paused or pause state is unavailable.'
            break
        messages = messages_for(case['input'], policy.get('system_prompt'))
        ceiling = reserve_quote(messages, policy['max_tokens_out'], book[model])
        call_id = await reserve_call(pool, run_id, region, case, ceiling, cap)
        if call_id is None:
            status, error = 'capped', 'Daily bench budget exhausted.'
            break
        try:
            result = await call_model(model, messages, policy['max_tokens_out'])
        except Exception:
            result = {'model': model, 'content': '', 'tokens_in': 0, 'tokens_out': 0, 'cost': None, 'ok': False}
        valid = result['ok'] and result['model'] == model and result['cost'] is not None and result['cost'] <= ceiling
        score = graduate.score_pair(case['expected'], result['content'], tolerance=float(policy.get('number_tolerance') or .01)) if valid else None
        # Even failures leave a receipt; unknown cost retains its budget reservation.
        async with pool.acquire() as conn, conn.transaction():
            ledger_id = None
            if result['cost'] is not None:
                ledger_id = await conn.fetchval('''INSERT INTO tank.ledger
                    (tenant_id,region,agent,task_type,model,tokens_in,tokens_out,cost_usd,outcome,error,eval_score)
                    VALUES($1,$2,'bench',$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id''',
                    PLATFORM_TENANT, region, policy['task_type'], result['model'], result['tokens_in'], result['tokens_out'],
                    result['cost'], 'delivered' if valid else 'unverified',
                    None if valid else 'Bench call unverified; unknown spend remains reserved.', score)
            await conn.execute('''UPDATE tank.bench_calls SET ledger_id=$2,charged_usd=$3,score=$4,status=$5
                WHERE id=$1 AND tenant_id=$6 AND region=$7''', call_id, ledger_id, result['cost'], score,
                'complete' if valid else 'unverified', PLATFORM_TENANT, region)
        import paid_steps
        paid_run = paid_steps.current.get()
        if ledger_id and paid_run:
            for decision in list(paid_run.admissions.values()):
                await paid_steps.attach_receipt(ledger_id, decision)
        if not valid:
            status, error = 'failed', 'Unverified model, usage, price or provider response; no proposal.'
            break
        scores.append(score)
        costs.append(result['cost'])
    score = sum(scores)/len(scores) if scores and status == 'complete' else None
    cost = sum(costs)/len(costs) if costs and status == 'complete' else None
    await pool.execute('''UPDATE tank.bench_runs SET score=$2,cost_per_call=$3,status=$4,error=$5,finished_at=now()
        WHERE id=$1 AND tenant_id=$6 AND region=$7''', run_id, score, cost, status, error, PLATFORM_TENANT, region)
    if status == 'complete':
        await pool.execute('''INSERT INTO tank.route_evidence(task_type,model,rung,bench_run_id,bench_score,bench_cost_per_call,bench_n,bench_at)
            VALUES($1,$2,1,$3,$4,$5,$6,now()) ON CONFLICT(task_type,model,rung) DO UPDATE SET
            bench_run_id=$3,bench_score=$4,bench_cost_per_call=$5,bench_n=$6,bench_at=now()''',
            policy['task_type'], model, run_id, score, cost, len(scores))
    return {'model': model, 'score': score, 'cost_per_call': cost, 'status': status}


async def reserve_call(pool, *args):
    async with pool.acquire() as conn:
        return await reserve(conn, *args)


async def tick() -> str | None:
    # Read only: a long pause must not emit one run/audit row per task per tick.
    # Per-call checks below still catch a pause enabled while a tick is running.
    async with asyncio.timeout(controls.STATE_TIMEOUT_S):
        paused = await controls.paid_paused()
    if paused:
        # Reading LiteLLM's price list is free; a paused box must still price
        # receipts and the Auto comparison. Only the paid bench stops.
        with suppress(Exception):
            await refresh_prices()
        return 'paused'
    rt = runtime()
    cap = Decimal(os.environ.get('TANK_BENCH_MAX_USD') or '5')
    if not cap.is_finite() or cap < 0:
        raise ValueError('TANK_BENCH_MAX_USD must be finite and nonnegative')
    if not rt.LITELLM_MASTER_KEY:
        raise RuntimeError('LiteLLM connection is not configured')
    region = rt.box_region()
    lock = await asyncpg.connect(rt.DATABASE_URL)
    try:
        conn = rt.pool()
        if not await lock.fetchval('SELECT pg_try_advisory_lock($1)', LOCK_ID):
            return
        try:
            roster = await rt._roster_models()
            book = price_book(roster)
            if not book:
                raise RuntimeError('No priced roster available; bench unchanged')
            async with conn.acquire() as connection:
                await sync_prices(connection, book)
            # Explicit tenant scopes: no client rows are used as platform examples.
            roster_fp, price_fp = fingerprints(book)
            roster_fp = hashlib.sha256(encoded(sorted(roster, key=lambda r: r['model_name'])).encode()).hexdigest()
            await scheduled_backfill(conn, region, price_fp)
            policies = await conn.fetch("SELECT * FROM tank.policy WHERE tenant_id IS NULL AND rung IN (1,2) ORDER BY task_type")
            for row in policies:
                policy = dict(row)
                try:
                    await __import__('moa').bench_task(policy, book, region, cap)
                except Exception:
                    log.exception('MoA bench failed for task %s; continuing CORE-1 bench', policy['task_type'])
                state = await conn.fetchrow('SELECT * FROM tank.search_state WHERE task_type=$1', policy['task_type'])
                if state and state['bench_status'] != 'paused' and not due(dict(state), datetime.now(timezone.utc), roster_fp, price_fp):
                    continue
                cases = await cases_for(conn, policy['task_type'], region)
                comparison = policy['model']
                results = []
                if cases:
                    # Unknown prices cannot be budgeted safely; retain an explicit
                    # failure for every skipped roster entry instead of hiding it.
                    for entry in roster:
                        if entry['model_name'] not in book:
                            await conn.execute('''INSERT INTO tank.bench_runs
                                (tenant_id,region,task_type,model,comparison_model,suite_hash,expected_n,status,error,finished_at)
                                VALUES($1,$2,$3,$4,$5,$6,$7,'failed','Token prices unavailable; no call made.',now())''',
                                PLATFORM_TENANT, region, policy['task_type'], entry['model_name'], comparison,
                                hashlib.sha256(encoded(cases).encode()).hexdigest(), len(cases))
                            results.append({'model': entry['model_name'], 'status': 'failed'})
                    for model in sorted(book, key=lambda m: (m != policy['model'], book[m]['input']+book[m]['output'], m)):
                        result = await bench_model(conn, policy, model, cases, book, region, cap, comparison)
                        results.append(result)
                        if result['status'] in ('capped', 'paused'):
                            break
                proposal = None if any(r['status']=='paused' for r in results) else choose_candidate(results, policy['model'])
                status = 'no_cases' if not cases else 'paused' if any(r['status']=='paused' for r in results) else 'capped' if any(r['status']=='capped' for r in results) else 'failed' if any(r['status']=='failed' for r in results) else 'complete'
                async with conn.acquire() as transaction, transaction.transaction():
                    if proposal:
                        # Compare-and-set: never overwrite a concurrent founder choice.
                        await transaction.execute('''UPDATE tank.policy SET candidate_model=$2,auto_promote=false,updated_at=now()
                            WHERE task_type=$1 AND tenant_id IS NULL AND model=$3 AND candidate_model IS NOT DISTINCT FROM $4''',
                            policy['task_type'], proposal, policy['model'], policy.get('candidate_model'))
                    await transaction.execute('''INSERT INTO tank.search_state(task_type,bench_roster_fingerprint,bench_price_fingerprint,bench_at,bench_status,bench_error)
                        VALUES($1,$2,$3,now(),$4,$5) ON CONFLICT(task_type) DO UPDATE SET
                        bench_roster_fingerprint=$2,bench_price_fingerprint=$3,bench_at=now(),bench_status=$4,bench_error=$5''',
                        policy['task_type'], roster_fp, price_fp, status,
                        'See benchmark receipts.' if status in ('failed','capped','paused') else 'No platform evals or held-out gold.' if status=='no_cases' else None)
                if status == 'paused':
                    return 'paused'
        finally:
            await lock.execute('SELECT pg_advisory_unlock($1)', LOCK_ID)
    finally:
        await lock.close()


async def worker_status(error: str | None, status: str = 'running') -> None:
    async with runtime().pool().acquire() as conn:
        await conn.execute("""INSERT INTO tank.search_state(task_type,bench_status,bench_error)
            VALUES('@bench',$1,$2) ON CONFLICT(task_type) DO UPDATE SET bench_status=$1,bench_error=$2
            WHERE (tank.search_state.bench_status,tank.search_state.bench_error) IS DISTINCT FROM ($1,$2)""",
            'unavailable' if error else status, error)


async def refresh_prices() -> int:
    """Populate tank.proof_prices from the live roster. Idempotent; costs no model call."""
    rt = runtime()
    if not rt.LITELLM_MASTER_KEY:
        raise RuntimeError('LiteLLM connection is not configured')
    book = price_book(await rt._roster_models())
    if not book:
        raise RuntimeError('No priced roster available; prices unchanged')
    async with rt.pool().acquire() as conn:
        await sync_prices(conn, book)
    return len(book)


async def initial_prices(delays=(5, 10, 20, 40, 80, 160)) -> bool:
    """After a fresh deploy LiteLLM may still be starting (compose waits only for
    service_started). Retry quickly instead of leaving proof_prices empty until
    the next five-minute tick, which never synced prices while paid work was paused."""
    for delay in (0, *delays):
        await asyncio.sleep(delay)
        try:
            count = await refresh_prices()
            log.info('Price roster synced at startup (%d models)', count)
            return True
        except Exception as exc:  # noqa: BLE001 - logged by type only; never the key
            log.warning('Price roster not ready at startup (%s); retrying', type(exc).__name__)
    return False


async def loop() -> None:
    await initial_prices()
    while True:
        try:
            status = await tick()
            await worker_status(None, status or 'running')
        except Exception as exc:
            log.error('Bench unavailable (%s); retrying in five minutes', type(exc).__name__)
            with suppress(Exception):
                await worker_status('Benchmark unavailable. Check the LiteLLM connection, price roster and daily budget.')
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


router = APIRouter(lifespan=lifespan)


def moa_shapes(config: dict) -> list[dict]:
    """The same suite must compare every shape and both aggregation modes.

    A benchmark configuration never enables a live policy. The caller must use
    the existing platform budget reservations and ledger path for every call.
    """
    from moa import Policy
    policy = Policy.model_validate(config)
    shapes = [policy.model_copy(update={'shape': 'single', 'enabled': False}).model_dump()]
    for shape in ('self', 'diverse', 'optimised'):
        for mode in ('select', 'synthesize'):
            shapes.append(policy.model_copy(update={'shape': shape, 'mode': mode, 'enabled': False}).model_dump())
    return shapes


def choose_moa_shape(rows: list[dict]) -> dict | None:
    """Only comparable, complete suites can propose a cheaper shape.

    No policy writes, and no partial suite or unknown cost becomes a winner.
    """
    if len(rows) != 7 or any(r.get('status') != 'complete' for r in rows):
        return None
    if len({r.get('suite_hash') for r in rows}) != 1 or not rows[0].get('suite_hash'):
        return None
    if any(not isinstance(r.get(k), (int, float, Decimal)) or not math.isfinite(float(r[k])) or r[k] < 0
           for r in rows for k in ('score', 'cost_per_call')):
        return None
    singles = [r for r in rows if r.get('shape') == 'single']
    expected = {(s, m) for s in ('self', 'diverse', 'optimised') for m in ('select', 'synthesize')}
    if len(singles) != 1 or {(r.get('shape'), r.get('mode')) for r in rows if r.get('shape') != 'single'} != expected:
        return None
    baseline = singles[0]
    eligible = [r for r in rows if r['score'] >= baseline['score']]
    winner = min(eligible, key=lambda r: (r['cost_per_call'], -r['score'], r['shape'], r.get('mode', '')))
    return {'shape': winner['shape'], 'mode': winner.get('mode'), 'score': winner['score'],
            'cost_per_call': winner['cost_per_call'], 'status': 'proposed', 'requires_founder': True}
