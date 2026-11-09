"""Explicit and automatic cheaper-window jobs; only the existing Tank router/batch gateway sends work."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager, nullcontext
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from weakref import WeakKeyDictionary

from fastapi import HTTPException
from pydantic import BaseModel

import batch_providers
import offpeak_schedule
import paid_steps
from tenant_db import tenant_scope


_worker_locks = WeakKeyDictionary()


def worker_lock():
    # Keep one maintenance operation per process: it holds an advisory-lock
    # connection while the existing router acquires its own short-lived ones.
    # Per-loop locks also allow isolated application/test lifespans.
    loop = asyncio.get_running_loop()
    return _worker_locks.setdefault(loop, asyncio.Lock())


class WindowClosed(Exception):
    """The clock crossed a window boundary during preflight; nothing was sent."""
    _paid_unspent = True


class QueuedResponse(BaseModel):
    id: uuid.UUID
    ledger_id: uuid.UUID
    status: str = 'queued'
    priority: str = 'offpeak'


def now() -> datetime:
    return datetime.now(timezone.utc)


def unpack(value):
    return json.loads(value) if isinstance(value, str) else value


def window_open(settings: dict | None, at: datetime) -> bool:
    if settings is None:
        return True
    local = at.astimezone(ZoneInfo(settings['timezone']))
    if local.date().isoformat() in settings.get('dates', []):
        return True
    minute = local.hour * 60 + local.minute
    for window in settings['windows']:
        start, end = (sum(int(n) * scale for n, scale in zip(window[key].split(':'), (60, 1)))
                      for key in ('start', 'end'))
        if not 0 <= start < end <= 1440:
            raise ValueError('Invalid offpeak window; split windows crossing midnight')
        if local.weekday() in window['days'] and start <= minute < end:
            return True
    return False


async def model_info(tank, model: str) -> dict:
    row = next((r for r in await tank._roster_models() if r['model_name'] == model), None)
    if row is None:
        raise HTTPException(409, 'Selected model is unavailable')
    return row['model_info']


def usage_cost(info: dict, usage: dict, at: datetime) -> float | None:
    """Configured provider rates × reported usage, including DeepSeek cache hits.

    LiteLLM's static peak-rate header cannot prove a time-of-day discount.
    Missing/invalid usage remains unknown; never turn missing evidence into zero.
    """
    settings = info.get('offpeak')
    if settings is None:
        return None
    try:
        count_in, count_out = int(usage['prompt_tokens']), int(usage['completion_tokens'])
        cached = usage.get('prompt_cache_hit_tokens', (usage.get('prompt_tokens_details') or {}).get('cached_tokens'))
        if cached is None:
            return None
        cached = int(cached)
        if min(count_in, count_out, cached) < 0 or cached > count_in:
            return None
        multiplier = Decimal(str(settings['charge_multiplier'])) if window_open(settings, at) else Decimal(1)
        if not 0 < multiplier <= 1:
            return None
        return float(multiplier * (
            (count_in-cached)*Decimal(str(info['input_cost_per_token']))
            + cached*Decimal(str(settings['cache_read_input_cost_per_token']))
            + count_out*Decimal(str(info['output_cost_per_token']))))
    except (KeyError, TypeError, ValueError):
        return None


async def automatic_request(tank, req, policy, messages, generation):
    """I8 uses only published discounts for work that may finish asynchronously."""
    if os.environ.get('ENGINE_I8_ENABLED') != '1':
        return req
    priority = req.priority or policy['priority']
    # Internal evaluations and seats must answer their controller immediately;
    # a parent's background eligibility is not permission to defer its grading.
    if '#' in req.task_type:
        return req
    if (priority in {'urgent', 'offpeak'} or req.interactive or not policy.get('batch_eligible')
            or policy.get('moa_enabled') or policy.get('_engine', {}).get('free')
            or policy['model'] in {'local', 'local-large'}):
        return req
    try:
        prices = await tank._batch_price(policy['model'])
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        return req  # No published price proves a cheaper plan.
    if prices['input'] == prices['output'] == 0:
        return req
    info = await model_info(tank, policy['model'])
    batched = batch_providers.compatible(prices, messages, generation)
    # DECISION reuse the existing 24h provider completion window plus a 120s
    # dispatch allowance when a background caller supplies no deadline.
    # Only a strictly cheaper, feasible plan changes the foreground path.
    at = now()
    deadline = req.deadline or at + timedelta(hours=24, seconds=120)
    try:
        plan = offpeak_schedule.select(info, at, deadline,
            batch_discount=prices['discount'] if batched else None)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if plan['charge_multiplier'] >= 1:
        return req
    return req.model_copy(update={'priority': 'offpeak', 'deadline': deadline})


@contextmanager
def job_actor(snapshot, tenant_id):
    """Restore server-owned authentication, never a user field from request JSON."""
    import product_identity
    saved = snapshot.get('actor')
    actor = None
    if saved is not None:
        if (not isinstance(saved, dict) or saved.get('tenant_id') != str(tenant_id)
                or not all(isinstance(saved.get(k), str) and saved[k].strip() for k in ('user_id', 'role'))):
            raise HTTPException(409, 'Deferred actor does not match its tenant')
        actor = product_identity.User(saved['tenant_id'], saved['user_id'], saved['role'])
    token = product_identity.current.set(actor)
    try:
        yield
    finally:
        product_identity.current.reset(token)


async def enqueue(tank, item, *, caller=None, task_id=None, model_override=None) -> QueuedResponse:
    req, policy = item.req, item.policy
    prices = await tank._batch_price(policy['model'])
    # Reserve/check with peak prices; a window closing must not circumvent a cap.
    batched = not policy.get('moa_enabled') and batch_providers.compatible(prices, item.messages, item.generation)
    reserve = tank._call_upper(item.messages, item.max_tokens, prices) * (prices['discount'] if batched else 1)
    if reserve > float(policy['max_cost_usd']):
        raise HTTPException(409, 'Task cost cap prevents this request')
    # Validate scheduling metadata before storing a request, including deployed
    # /model/info metadata rather than assuming a config file exists in Tank.
    info = await model_info(tank, policy['model'])
    window_open(info.get('offpeak'), now())
    schedule = None
    if req.deadline is not None:
        try:
            schedule = offpeak_schedule.select(info, now(), req.deadline,
                                               batch_discount=prices['discount'] if batched else None)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        batched = schedule['mode'] == 'batch'
    reserve = tank._call_upper(item.messages, item.max_tokens, prices) * (prices['discount'] if batched else 1)
    if reserve > float(policy['max_cost_usd']):
        raise HTTPException(409, 'Task cost cap prevents this request')
    job_id = uuid.uuid4()
    snapshot = {'routing': policy, 'messages': item.messages, 'max_tokens': item.max_tokens,
                'generation': item.generation, 'escalation': item.escalation,
                'semantic_cache': item.cache_snapshot, 'schedule': schedule}
    import product_identity
    actor = product_identity.current.get()
    if actor is not None and (os.environ.get('ENGINE_C1_ENABLED') == '1' or os.environ.get('ENGINE_I8_ENABLED') == '1'):
        if actor.tenant_id != str(req.tenant_id):
            raise HTTPException(403, 'Deferred actor does not match its tenant')
        snapshot['actor'] = vars(actor)
    run = paid_steps.current.get()
    if run is not None:
        if run.tenant_id != req.tenant_id or run.run_id != req.paid_run_id:
            raise HTTPException(409, 'Deferred paid run does not match its request')
        snapshot['paid_run'] = {'tenant_id': str(run.tenant_id), 'run_id': str(run.run_id),
                                'request_hash': run.request_hash, 'user_id': run.user_id,
                                'thread_id': str(run.thread_id) if run.thread_id else None,
                                'root_run_id': str(run.root_run_id) if run.root_run_id else None}
    async with tank.pool().acquire() as conn, conn.transaction():
        await conn.execute("""INSERT INTO tank.offpeak_jobs
            (id,tenant_id,region,request,policy,caller,task_id,model_override)
            VALUES($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7,$8)""",
            job_id, req.tenant_id, tank.box_region(), req.model_dump_json(), json.dumps(snapshot, default=str),
            caller, task_id, model_override)
        await conn.execute("""INSERT INTO tank.ledger
            (id,tenant_id,region,agent,skill,task_type,rung,model,priority,outcome,cost_status,task_id,offpeak_job_id)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,'offpeak','queued','not_set',$9,$1)""",
            job_id, req.tenant_id, tank.box_region(), req.agent, req.skill, req.task_type,
            policy['rung'], policy['model'], task_id)
    return QueuedResponse(id=job_id, ledger_id=job_id)


def dispatch_ready(req, info, at):
    if req.deadline is None:
        return window_open(info.get('offpeak'), at)
    try:
        plan = offpeak_schedule.select(info, at, req.deadline)
        return datetime.fromisoformat(plan['dispatch_at']) <= at
    except ValueError:
        return False


async def label_batch(tank, queued: QueuedResponse, tenant_id) -> QueuedResponse:
    """Mark a queued receipt as background batch work (it queued as offpeak).

    Only the label differs: same job, same receipt, same dispatch and pricing.
    The router's receipt updates never rewrite priority, so it stays 'batch'.
    """
    async with tank.pool().acquire() as conn:
        await conn.execute("""UPDATE tank.ledger SET priority='batch'
            WHERE id=$1 AND tenant_id=$2 AND offpeak_job_id=$1""", queued.id, tenant_id)
    return queued.model_copy(update={'priority': 'batch'})


async def status(tank, job_id, tenant_id, *, caller=None) -> dict:
    # The Workspace key (caller None) reads any job; an agent key reads only
    # the jobs it queued itself.
    async with tank.pool().acquire() as conn:
        row = await conn.fetchrow("""SELECT j.id,j.status,j.created_at,j.sent_at,j.finished_at,j.result,j.error,
            coalesce(l.priority,'offpeak') AS priority,j.caller,j.policy->'schedule' AS schedule
            FROM tank.offpeak_jobs j LEFT JOIN tank.ledger l ON l.id=j.id AND l.tenant_id=j.tenant_id
            WHERE j.id=$1 AND j.tenant_id=$2 AND j.region=$3""", job_id, tenant_id, tank.box_region())
    if row is None or (caller is not None and row['caller'] != caller):
        raise HTTPException(404, 'No job for this tenant')
    view = {key: value for key, value in dict(row).items() if key != 'caller'}
    return {**view, 'result': unpack(row['result']), 'schedule': unpack(row['schedule']), 'ledger_id': job_id}


async def _finish(tank, conn, job, result=None, *, error=None, uncertain=False):
    state = 'uncertain' if uncertain else 'failed' if error else 'completed'
    await conn.execute("""UPDATE tank.offpeak_jobs SET status=$3,result=$4::jsonb,error=$5,finished_at=now()
        WHERE id=$1 AND tenant_id=$2""", job['id'], job['tenant_id'], state,
        json.dumps(result, default=str) if result is not None else None, error)
    if job.get('task_id'):
        content = result.get('content') if isinstance(result, dict) else None
        receipt_id = uuid.UUID(str(result.get('ledger_id') or job['id'])) if isinstance(result, dict) else job['id']
        await conn.execute("""UPDATE tank.tasks SET status=$4,result=$5,error=$6,
            ledger_id=$3,finished_at=now()
            WHERE id=$1 AND tenant_id=$2
              AND (ledger_id IS NULL OR ledger_id=$7 OR ledger_id=$3)""",
            job['task_id'], job['tenant_id'], receipt_id,
            'done' if state == 'completed' else 'failed', content, error, job['id'])
    if error:
        await conn.execute("""UPDATE tank.ledger SET outcome=$3,error=$4
            WHERE id=$1 AND tenant_id=$2 AND outcome IN ('queued','sent')""",
            job['id'], job['tenant_id'], 'unverified' if uncertain else 'failed', error)


async def _process(tank, conn, job):
    if job['status'] == 'dispatching':
        # Only a new lock holder can reach this. Do not replay a possibly charged call.
        await _finish(tank, conn, job, error='interrupted_dispatch', uncertain=True)
        return
    if job['status'] == 'batch':
        batch = await tank.batch_status(job['id'], job['tenant_id'])
        if batch['status'] in {'completed', 'failed'}:
            result = batch['results'][0] if batch['results'] else None
            await _finish(tank, conn, job, result, error='batch_failed' if batch['status'] == 'failed' else None)
        return
    req = tank.RouteRequest.model_validate(unpack(job['request']))
    snapshot = unpack(job['policy'])
    policy = snapshot['routing']
    current = await tank.load_policy(req.task_type.partition('#moa.')[0], req.tenant_id)
    if str(current['updated_at']) != str(policy['updated_at']):
        await _finish(tank, conn, job, error='policy_changed')
        return
    prices = await tank._batch_price(policy['model'])
    batched = not policy.get('moa_enabled') and batch_providers.compatible(prices, snapshot['messages'], snapshot['generation'])
    info = await model_info(tank, policy['model'])
    if req.deadline is not None:
        try:
            schedule = offpeak_schedule.select(info, now(), req.deadline,
                                               batch_discount=prices['discount'] if batched else None)
        except ValueError:
            await _finish(tank, conn, job, error='deadline_exceeded')
            return
        if datetime.fromisoformat(schedule['dispatch_at']) > now():
            return
        batched = schedule['mode'] == 'batch'
    elif not batched and not window_open(info.get('offpeak'), now()):
        return
    if job['task_id']:
        await tank._check_route_task(req, job['task_id'])
    if batched:
        # Queue an already-screened item through the SAME reservation/submission
        # implementation as /route/batch. Explicit offpeak is consent to wait;
        # foreground batch eligibility/minimums are otherwise untouched.
        item = tank._DeferredRoute(req, policy, snapshot['messages'], snapshot['max_tokens'],
                                   snapshot['generation'], snapshot['escalation'], snapshot.get('semantic_cache'))
        item.position, item.offpeak_job_id = 0, job['id']
        body = tank.BatchRequest(id=job['id'], requests=[req])
        await tank._enqueue_batch(body, job['caller'], [], [item], str(job['id']))
        await conn.execute("UPDATE tank.offpeak_jobs SET status='batch' WHERE id=$1 AND tenant_id=$2",
                           job['id'], job['tenant_id'])
        return
    sent_at = now()
    await conn.execute("""UPDATE tank.offpeak_jobs SET status='dispatching',sent_at=$3
        WHERE id=$1 AND tenant_id=$2""", job['id'], job['tenant_id'], sent_at)
    try:
        saved_run = snapshot.get('paid_run')
        scope = nullcontext()
        if saved_run is not None:
            run = paid_steps.Run(uuid.UUID(saved_run['tenant_id']), uuid.UUID(saved_run['run_id']),
                                 saved_run['request_hash'], saved_run.get('user_id'),
                                 uuid.UUID(saved_run['thread_id']) if saved_run.get('thread_id') else None,
                                 root_run_id=uuid.UUID(saved_run['root_run_id']) if saved_run.get('root_run_id') else None)
            if run.tenant_id != job['tenant_id'] or run.tenant_id != req.tenant_id or run.run_id != req.paid_run_id:
                raise HTTPException(409, 'Deferred paid run does not match its request')
            # This server-owned snapshot preserves the original Command fingerprint.
            # Fresh admission still checks the current controls and existing grant.
            scope = paid_steps.run_scope(run)
        restore_actor = os.environ.get('ENGINE_C1_ENABLED') == '1' or os.environ.get('ENGINE_I8_ENABLED') == '1'
        with scope, job_actor(snapshot, req.tenant_id) if restore_actor else nullcontext():
            work = tank.run_route(req, caller=job['caller'], task_id=job['task_id'],
                model_override=job['model_override'], _paid_policy=policy, _prior_escalation=snapshot['escalation'],
                _paid_limit=float(policy['max_cost_usd']), _offpeak_dispatch=True, _receipt_id=job['id'], _deferred_snapshot=snapshot)
            result = (await asyncio.wait_for(work, timeout=max(0, (req.deadline-now()).total_seconds()))
                      if req.deadline is not None else await work)
        await _finish(tank, conn, job, result.model_dump(mode='json'))
    except (WindowClosed, __import__("paid_steps").ApprovalRequired):
        await conn.execute("UPDATE tank.offpeak_jobs SET status='queued',sent_at=NULL WHERE id=$1 AND tenant_id=$2", job['id'], job['tenant_id'])
    except Exception as exc:
        tank.log.exception('Offpeak dispatch failed')
        detail = exc.detail if isinstance(exc, HTTPException) else None
        known = isinstance(detail, dict) and detail.get('ledger_id')
        await _finish(tank, conn, job, error='dispatch_failed', uncertain=not isinstance(exc, HTTPException) or
                      (exc.status_code >= 500 and not known))


async def process_once(tank, *, job_id=None, tenant_id=None):
    async with worker_lock():
        await _process_once(tank, job_id=job_id, tenant_id=tenant_id)


@asynccontextmanager
async def owner_connection(tank, discovery, tenant):
    with tenant_scope(tenant):
        source = tank.pool()
        if getattr(tank, "_admin_pool", None) is not None or getattr(source, "admin_pool", None) is not None:
            async with source.acquire(autocommit=True) as conn:
                yield conn
        else:
            # Legacy pre-activation/native test pools retain their checkout count.
            yield discovery


async def _process_once(tank, *, job_id=None, tenant_id=None):
    async with getattr(tank, "admin_pool", tank.pool)().acquire() as conn:
        # Platform worker discovers work across tenants, then every operation is
        # scoped by that row's tenant and box, as in the existing batch worker.
        rows = await conn.fetch("""SELECT id,tenant_id FROM tank.offpeak_jobs WHERE region=$1
            AND ($2::uuid IS NULL OR id=$2) AND ($3::uuid IS NULL OR tenant_id=$3)
            AND status IN ('queued','dispatching','batch') ORDER BY checked_at NULLS FIRST,created_at LIMIT 100""",
            tank.box_region(), job_id, tenant_id)
        for row in rows:
            # Separate advisory namespace from provider batches (which share id).
            lock = int.from_bytes(uuid.uuid5(row['id'], 'offpeak').bytes[:8], 'big', signed=True)
            if not await conn.fetchval('SELECT pg_try_advisory_lock($1)', lock):
                continue
            try:
                async with owner_connection(tank, conn, row['tenant_id']) as owned:
                    job = await owned.fetchrow('SELECT * FROM tank.offpeak_jobs WHERE id=$1 AND tenant_id=$2', row['id'], row['tenant_id'])
                    if job['status'] in {'queued', 'dispatching', 'batch'}:
                        await owned.execute('UPDATE tank.offpeak_jobs SET checked_at=now() WHERE id=$1 AND tenant_id=$2', row['id'], row['tenant_id'])
                        await _process(tank, owned, job)
            except HTTPException as exc:
                if exc.status_code < 500:
                    async with owner_connection(tank, conn, row['tenant_id']) as owned:
                        await _finish(tank, owned, row, error='route_refused')
                else:
                    tank.log.warning('Offpeak maintenance will retry status')
            except Exception:
                tank.log.warning('Offpeak maintenance will retry status')
            finally:
                await conn.execute('SELECT pg_advisory_unlock($1)', lock)


async def notify_commands(tank):
    async with worker_lock():
        await _notify_commands(tank)


async def _notify_commands(tank):
    """Resume an existing Command turn after a durable job completes, without a model call."""
    import command
    from types import SimpleNamespace
    async with getattr(tank, "admin_pool", tank.pool)().acquire() as conn:
        rows = await conn.fetch("""SELECT id,tenant_id FROM tank.offpeak_jobs WHERE region=$1
            AND status IN ('completed','failed','uncertain') AND command_context IS NOT NULL
            AND NOT notified ORDER BY created_at LIMIT 100""", tank.box_region())
        for row in rows:
            lock = int.from_bytes(uuid.uuid5(row['id'], 'offpeak').bytes[:8], 'big', signed=True)
            if not await conn.fetchval('SELECT pg_try_advisory_lock($1)', lock):
                continue
            try:
                async with owner_connection(tank, conn, row['tenant_id']) as owned:
                    job = await owned.fetchrow('SELECT * FROM tank.offpeak_jobs WHERE id=$1 AND tenant_id=$2', row['id'], row['tenant_id'])
                    if job['notified']:
                        continue
                    context = unpack(job['command_context'])
                    actor = await command.authorized_actor(command.Actor(job['tenant_id'], context['user_id'], False,
                                                          tenant_admin=context.get('tenant_admin', False)))
                    result = unpack(job['result'])
                    answer = SimpleNamespace(content=result['content'], ledger_id=uuid.UUID(str(result['ledger_id']))) if job['status'] == 'completed' else None
                    error = HTTPException(502, {'ledger_id': str(job['id'])}) if answer is None else None
                    await command.finish_turn(uuid.UUID(context['thread_id']), actor, str(job['task_id']), [],
                                              model=job['model_override'], answer=answer, deferred_error=error)
                    await owned.execute('UPDATE tank.offpeak_jobs SET notified=true WHERE id=$1 AND tenant_id=$2', job['id'], job['tenant_id'])
            finally:
                await conn.execute('SELECT pg_advisory_unlock($1)', lock)


def kick(tank, job_id, tenant_id):
    # Independent of the request connection; a browser closing cannot cancel a
    # paid dispatch. The durable maintenance loop recovers jobs after a restart.
    async def work():
        try:
            await process_once(tank, job_id=job_id, tenant_id=tenant_id)
            await notify_commands(tank)
        except Exception:
            tank.log.warning('Offpeak job remains available to maintenance')
    task = asyncio.create_task(work())
    tank._background.add(task)
    task.add_done_callback(tank._background.discard)
