"""Per-run evidence. Tank owns prices and model billing; this room owns runs."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import json
from uuid import UUID, uuid4

from chassis.tenant_db import tenant_scope


@dataclass
class Run:
    tenant: str
    action: str
    id: str = field(default_factory=lambda: str(uuid4()))
    ledgers: set[str] = field(default_factory=set)
    uncertain: bool = False
    attempts: int = 0


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
        quote = await tank.savings_quote(run.tenant, sorted(run.ledgers), reference, run.uncertain, run.id)
        # Only an authenticated Tank quote can supply prices/actual model spend.
        receipt.update({k: quote[k] for k in ("cost", "would have cost", "baseline_model", "priced_at",'tokens_in','tokens_out') if k in quote})
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
    token = current.set(run)
    try:
        response = await call_next(request)
        content = b"".join([chunk async for chunk in response.body_iterator])
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
            if isinstance(output, dict) and "application/json" in response.headers.get("content-type", ""):
                output["savings_receipt"] = receipt
                # Existing Command/MCP receipt linking reads explicit envelopes.
                output["ledger_ids"] = sorted(run.ledgers)
                content = json.dumps(output, default=str).encode()
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return Response(content, status_code=response.status_code, headers=headers, background=response.background)
    except BaseException:
        # Preserve uncertain charges even when a request times out or disconnects.
        if run.attempts:
            run.uncertain = True
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
