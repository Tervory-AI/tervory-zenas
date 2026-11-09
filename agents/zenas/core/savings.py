"""Per-run evidence. Tank owns prices and model billing; this room owns runs."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import json
from uuid import UUID, uuid4

from chassis.tenant_db import tenant_scope
from agents.zenas.memory_api import is_moot_path


@dataclass
class Run:
    tenant: str
    action: str
    id: str = field(default_factory=lambda: str(uuid4()))
    ledgers: set[str] = field(default_factory=set)
    uncertain: bool = False
    attempts: int = 0
    baseline_ledgers: list[str] = field(default_factory=list)
    actor: object = None


current: ContextVar[Run | None] = ContextVar("zenas_savings_run", default=None)


@dataclass
class Batch:
    action: str
    runs: dict[str, Run] = field(default_factory=dict)


def get_run(tenant):
    scope = current.get()
    if isinstance(scope, Batch):
        tenant = str(UUID(str(tenant)))
        return scope.runs.setdefault(tenant, Run(tenant, scope.action))
    if scope is not None and UUID(str(tenant)) != UUID(scope.tenant):
        raise ValueError("tenant_mismatch")
    return scope


def observed(tenant, reply):
    run = get_run(tenant)
    if run is None:
        return
    try:
        ledger = str(UUID(str(reply["ledger_id"])))
    except (KeyError, ValueError, TypeError):
        run.uncertain = True
    else:
        run.ledgers.add(ledger)


async def save(pool, tank, run, inputs, outputs, *, failed=False):
    receipt = {"id": run.id, "action": run.action, "cost": None if run.uncertain else 0,
               "would have cost": None, "baseline_model": None,
               "tokens_in": None, "tokens_out": None,
               "ledger_ids": sorted(run.ledgers), "status": "failed" if failed else "completed",
               "basis": "Recorded routed tokens, or action input/output reference BPE for code/cache-only work, at Tank's paid default price; not a second model run."}
    try:
        reference={"input":json.dumps(inputs,ensure_ascii=False,separators=(',',':'),default=str),
                   "output":json.dumps(outputs,ensure_ascii=False,separators=(',',':'),default=str)}
        if run.baseline_ledgers:
            reference['baseline_ledger_ids'] = run.baseline_ledgers
        quote = await tank.savings_quote(run.tenant, sorted(run.ledgers), reference, run.uncertain, run.id)
        # Only an authenticated Tank quote can supply prices/actual model spend.
        receipt.update({k: quote[k] for k in ("cost", "would have cost", "baseline_model", "priced_at",'tokens_in','tokens_out','paid_calls') if k in quote})
        # Quote's legacy tokens describe the paid-default reference. Keep
        # product receipt tokens as actual model usage (code/cache is zero).
        for kind in ('in', 'out'):
            field = 'tokens_' + kind
            receipt['reference_' + field] = quote.get(field)
            if 'actual_' + field in quote:
                receipt[field] = quote['actual_' + field]
        run.ledgers.update(str(UUID(str(i))) for i in quote.get('ledger_ids',[]))
        receipt['ledger_ids']=sorted(run.ledgers)
    except Exception:
        # An unavailable price is never free model work. Keep the run receipt.
        if run.attempts or run.ledgers:
            receipt["cost"] = None
    with tenant_scope(run.tenant):
        await pool.execute("""INSERT INTO agent_zenas.savings_receipts(tenant_id,id,receipt)
            VALUES($1,$2,$3::jsonb) ON CONFLICT(tenant_id,id) DO NOTHING""",
            UUID(run.tenant), UUID(run.id), json.dumps(receipt, allow_nan=False))
    return receipt


async def command(request, call_next, tenant):
    actions=('/answers','/drafts','/contract-reviews','/ladder/','/rule-cards','/cases/',
             '/matters','/playbooks','/moot/','/street-law/','/regulations/','/pool/','/consents')
    if request.method != "POST" or not tenant or not hasattr(request.app.state, "pool") or not request.url.path.startswith(actions):
        return await call_next(request)
    from starlette.responses import Response
    run = Run(tenant, request.url.path, request.headers.get("idempotency-key") or str(uuid4()))
    run.actor = getattr(request.state, "command_actor", None) or getattr(request.state, "zenas_actor", None)
    token = current.set(run)
    try:
        from .memory_task import prepare
        from starlette.responses import JSONResponse
        from fastapi import HTTPException
        inputs = json.loads(getattr(request, "_body", b"") or b"{}")
        try:
            task = None if is_moot_path(request.url.path) else await prepare(request, inputs)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        cached = await task.cached(request) if task is not None else None
        if cached is not None:
            prior = task.hit.get('meta', {}).get('receipt') or {}
            run.baseline_ledgers = prior.get('ledger_ids') or []
            response = JSONResponse(cached, status_code=201 if request.url.path == '/drafts' else 200)
        else:
            response = await call_next(request)
        content = response.body if hasattr(response, 'body') else b"".join([chunk async for chunk in response.body_iterator])
        try:
            output = json.loads(content)
        except (ValueError, UnicodeDecodeError):
            output = {"status": response.status_code}
        # Failed validation/auth is not a run; external calls already made still count.
        if response.status_code < 400 or run.attempts:
            run.action = getattr(request.scope.get("route"), "path", request.url.path)
            inputs = json.loads(getattr(request, "_body", b"") or b"{}")
            receipt = await save(request.app.state.pool, getattr(request.app.state, "tank", None),
                                 run, inputs, output, failed=response.status_code >= 400)
            receipt['memory_status'] = task.status if task is not None else 'off'
            receipt['answered_from_memory'] = cached is not None
            receipt['memory_id'] = task.hit.get('id') if task is not None and task.hit else None
            receipt['cost_usd'] = receipt['cost']
            receipt['would_have_cost'] = {'cost_usd': receipt['would have cost']}
            if task is not None and response.status_code < 400 and isinstance(output, dict) and output.get('ok', True):
                try:
                    await task.remember(output, receipt)
                except HTTPException as exc:
                    # Payment already happened. Keep its receipt and durable command
                    # result; an auth failure is never labelled a storage outage.
                    return JSONResponse({'detail': exc.detail, 'savings_receipt': receipt}, status_code=exc.status_code)
                except Exception:
                    task.status = receipt['memory_status'] = 'memory unavailable'
            receipt['memory_id'] = task.hit.get('id') if task is not None and task.hit else None
            with tenant_scope(run.tenant):
                await request.app.state.pool.execute("UPDATE agent_zenas.savings_receipts SET receipt=$3::jsonb WHERE tenant_id=$1 AND id=$2",
                    UUID(run.tenant), UUID(run.id), json.dumps(receipt, allow_nan=False))
            if isinstance(output, dict) and "application/json" in response.headers.get("content-type", ""):
                output['memory'] = {'enabled': task.enabled if task is not None else False,
                                    'status': task.status if task is not None else 'off', 'hit': cached is not None,
                                    'worked_law': task.worked_law if task is not None else 0,
                                    'memory_id': receipt['memory_id']}
                output['memory_hit'] = cached is not None
                output["savings_receipt"] = receipt
                # Existing Command/MCP receipt linking reads explicit envelopes.
                output["ledger_ids"] = sorted(run.ledgers)
                content = json.dumps(output, default=str).encode()
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return Response(content, status_code=response.status_code, headers=headers, background=response.background)
    except BaseException:
        # Preserve uncertain charges even when a request times out or disconnects.
        if run.attempts:
            run.uncertain = run.uncertain or not run.ledgers
            await save(request.app.state.pool, getattr(request.app.state, "tank", None), run,
                       {"action": run.action}, {"status": "interrupted"}, failed=True)
        raise
    finally:
        current.reset(token)


async def receipts(pool, tenant):
    with tenant_scope(tenant):
        rows = await pool.fetch("""SELECT receipt,created_at FROM agent_zenas.savings_receipts
            WHERE tenant_id=$1 ORDER BY created_at DESC,id DESC LIMIT 100""", UUID(tenant))
    return [{**(json.loads(r["receipt"]) if isinstance(r["receipt"], str) else r["receipt"]),
             "created_at": r["created_at"].isoformat()} for r in rows]


async def job(conn, function, *, region, name):
    if current.get() is not None or not hasattr(conn, 'fetch'):
        return await function(conn, region=region)
    import httpx
    import os
    from .outbound import TankClient
    batch = Batch('jobs/' + name)
    token = current.set(batch)
    result, failed = {}, True
    try:
        result = await function(conn, region=region)
        failed = False
        return result
    finally:
        try:
            if not batch.runs:
                tenant = '00000000-0000-0000-0000-000000000001'
                batch.runs[tenant] = Run(tenant, batch.action)
            async with httpx.AsyncClient(timeout=120, follow_redirects=False) as http:
                tank = TankClient(os.environ.get('TANK_URL', 'http://tank:8000'), os.environ.get('TANK_KEY', ''), 'zenas', http)
                for run in batch.runs.values():
                    await save(conn, tank, run, {'job': name, 'region': region},
                               {'status': 'failed' if failed else 'completed', 'result': result} if len(batch.runs)==1
                               else {'status': 'failed' if failed else 'completed'}, failed=failed)
        finally:
            current.reset(token)
