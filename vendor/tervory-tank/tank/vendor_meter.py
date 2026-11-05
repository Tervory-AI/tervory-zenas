"""Paid HTTP receipts at the shared-client boundary; no provider keys or URLs stored."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import json
import logging
from typing import Any
import uuid

import httpx
from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

PLATFORM = uuid.UUID("00000000-0000-0000-0000-000000000001")
VENDORS = {
    "api.search.brave.com": ("brave.search", "brave.search.query"),
    "places.googleapis.com": ("google.places", "google.places.request"),
    "maps.googleapis.com": ("google.places", "google.places.request"),
}
PAID_HOSTS = frozenset(VENDORS)


@dataclass
class Run:
    tenant: uuid.UUID
    region: str
    agent: str
    skill: str
    task_id: uuid.UUID | None
    record_id: uuid.UUID | None
    model_ids: list[uuid.UUID] = field(default_factory=list)
    model_calls: int = 0


current: ContextVar[Run | None] = ContextVar("vendor_run", default=None)


class SafeHTTPLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            clean = []
            for value in record.args:
                if isinstance(value, httpx.URL) and value.host in PAID_HOSTS:
                    value = value.copy_with(query=None, username="", password="")
                clean.append(value)
            record.args = tuple(clean)
        return True


def runtime() -> Any:
    import main
    return main


def product(request: httpx.Request) -> tuple[str, str] | None:
    host = request.url.host.lower()
    if host == "maps.googleapis.com" and not request.url.path.startswith("/maps/api/place/"):
        return None
    return VENDORS.get(host)


async def price(conn: Any, code: str, at: datetime) -> dict[str, Any] | None:
    row = await conn.fetchrow("""SELECT id,unit_price,price_status,free_pass FROM tank.price_book
        WHERE tenant_id=$1 AND outcome_type=$2 AND currency='USD' AND effective_from <= $3
          AND (effective_to IS NULL OR effective_to > $3)
        ORDER BY effective_from DESC,created_at DESC,id DESC LIMIT 1""", PLATFORM, code, at)
    return dict(row) if row else None


async def link(conn: Any, run: Run, ledger_id: uuid.UUID) -> None:
    if run.record_id is not None:
        await conn.execute("""INSERT INTO tank.base_ledger_links(tenant_id,record_id,ledger_id)
            SELECT $1,$2,id FROM tank.ledger WHERE id=$3 AND tenant_id=$1
            ON CONFLICT DO NOTHING""", run.tenant, run.record_id, ledger_id)


async def request_hook(request: httpx.Request) -> None:
    item = product(request)
    if item is None:
        return
    if any(name.lower() in {"key", "api_key", "apikey", "token", "access_token", "password"} for name in request.url.params):
        raise HTTPException(422, "Paid tool credentials must use headers, never URLs.")
    run = current.get()
    if run is None:
        raise HTTPException(503, "Paid tool receipt context is unavailable.")
    vendor, code = item
    receipt = uuid.uuid4()
    async with runtime().pool().acquire() as conn:
        async with conn.transaction():
            quote = await price(conn, code, datetime.now(timezone.utc))
            await conn.execute("""INSERT INTO tank.ledger
                (id,tenant_id,region,agent,skill,task_type,task_id,model,vendor,units,rung,cost_usd,
                 outcome,cost_status,vendor_cost_status,vendor_unit_price,vendor_price_id)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,1,1,0,'unverified','not_set','pending',$10,$11)""",
                receipt, run.tenant, run.region, run.agent, run.skill, run.skill.split("@")[0], run.task_id,
                "vendor:" + vendor, vendor, quote["unit_price"] if quote and quote["price_status"] == "set" else None, quote["id"] if quote else None)
            await link(conn, run, receipt)
    # Reserve before the outbound request. A transport failure leaves an unknown
    # charge with a receipt instead of an unbooked paid attempt.
    request.extensions["vendor_receipt"] = (receipt, run.tenant, vendor)


async def response_hook(response: httpx.Response) -> None:
    reserved = response.request.extensions.pop("vendor_receipt", None)
    if reserved is None:
        return
    receipt, tenant, vendor = reserved
    successful = 200 <= response.status_code < 300
    # Brave bills successful queries, not HTTP errors:
    # https://api-dashboard.search.brave.com/documentation/guides/rate-limiting
    # Google Places bills successful requests, including ZERO_RESULTS; quota,
    # denied and invalid requests are not successful Places calls:
    # https://developers.google.com/maps/documentation/places/web-service/usage-and-billing
    billed = successful
    if vendor == "google.places" and successful:
        await response.aread()
        try:
            status = response.json().get("status")
            billed = status not in {"REQUEST_DENIED", "INVALID_REQUEST", "OVER_QUERY_LIMIT", "OVER_DAILY_LIMIT", "UNKNOWN_ERROR"}
            successful = billed
        except (ValueError, AttributeError):
            billed = True
    async with runtime().pool().acquire() as conn:
        await conn.execute("""UPDATE tank.ledger SET outcome=$3,
            cost_usd=CASE WHEN NOT $4 THEN 0 ELSE coalesce(vendor_unit_price,0) END,
            cost_status=CASE WHEN $4 AND vendor_unit_price IS NULL THEN 'not_set' ELSE 'set' END,
            vendor_cost_status=CASE WHEN NOT $4 THEN 'not_billed' WHEN vendor_unit_price IS NULL THEN 'unpriced' ELSE 'priced' END
            WHERE id=$1 AND tenant_id=$2 AND vendor_cost_status='pending'""",
            receipt, tenant, "delivered" if successful else "failed", billed)


def bind(ctx: Any, run: Run) -> None:
    """Attach attribution without changing model routing or approved skill code."""
    run.skill = ctx.skill_ref
    run.task_id = ctx.task_id
    import controls
    controls._task.set(run.skill)
    original = getattr(ctx, "_model", None)
    if original is None:
        return

    async def model(**kwargs: Any) -> dict[str, Any]:
        run.model_calls += 1
        try:
            result = await original(**kwargs)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            if detail.get("ledger_id"):
                async with runtime().pool().acquire() as conn:
                    await link(conn, run, uuid.UUID(detail["ledger_id"]))
            raise
        ledger_id = result.get("ledger_id")
        if ledger_id:
            identity = uuid.UUID(str(ledger_id))
            async with runtime().pool().acquire() as conn:
                await link(conn, run, identity)
            run.model_ids.append(identity)
        return result
    ctx._model = model


def install(rt: Any) -> None:
    """Decorate context creation for request receipts; routing decisions stay unchanged.

    Installed once by the Base route registration. Only contexts inside a
    validated Base request receive the observer. No skill package is altered.
    """
    original = rt.make_ctx
    if getattr(original, "_base_receipts", False):
        return

    @wraps(original)
    def context(*args: Any, **kwargs: Any) -> Any:
        ctx = original(*args, **kwargs)
        run = current.get()
        if run is not None and ctx.tenant_id == run.tenant:
            bind(ctx, run)
        return ctx
    context._base_receipts = True
    rt.make_ctx = context
    rt.app.add_middleware(RunReceipts)
    logging.getLogger("httpx").addFilter(SafeHTTPLog())


class RunReceipts:
    """Per-request context and code receipts; never intercept the queue worker."""
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or scope.get("method") != "POST" or not (
            path.startswith("/v1/skala/") and path.endswith("/run")
        ):
            await self.app(scope, receive, send)
            return
        import controls
        import hmac
        import os
        headers = dict(scope.get("headers", []))
        expected = os.environ.get("TANK_KEY_BASE", "")
        if not expected or not hmac.compare_digest(headers.get(b"x-tank-key", b""), expected.encode()):
            await self.app(scope, receive, send)
            return
        chunks = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.extend(message.get("body", b""))
            if not message.get("more_body"):
                break
        try:
            body = json.loads(chunks)
            raw = body.get("input", {}).pop("_base_record_id", None)
            run = Run(uuid.UUID(body["tenant_id"]), runtime().box_region(), "base",
                      path.split("/")[-2], None, uuid.UUID(str(raw)) if raw else None)
        except (ValueError, KeyError, TypeError, AttributeError):
            await send({"type": "http.response.start", "status": 422, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"detail":"Invalid Base receipt attribution."}'})
            return
        payload = json.dumps(body).encode()
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": payload, "more_body": False}
        import paid_steps
        digest = paid_steps.fingerprint({"request": {k: v for k, v in body.items() if k != "paid_run_id"}, "record_id": str(run.record_id), "path": scope["path"]})
        stable_id = uuid.uuid5(run.record_id, digest) if run.record_id else uuid.uuid4()
        try:
            run_id = uuid.UUID(str(body.get("paid_run_id") or stable_id))
        except (ValueError, TypeError):
            await send({"type": "http.response.start", "status": 422, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"detail":"Invalid paid-step run identity."}'})
            return
        paid_run = paid_steps.Run(run.tenant, run_id, digest)
        paid_token = paid_steps.current.set(paid_run)
        token, task_token = current.set(run), controls._task.set(run.skill)
        started: Message | None = None
        content = bytearray()

        async def capture(message: Message) -> None:
            nonlocal started
            run = current.get()
            if run is None:
                await send(message)
            elif message["type"] == "http.response.start":
                started = message
            elif message["type"] == "http.response.body":
                content.extend(message.get("body", b""))
                if not message.get("more_body"):
                    if not run.model_calls and started and started["status"] == 200:
                        result = json.loads(content)
                        # A code skill can call a separately metered vendor. The
                        # code step is free; its vendor receipt is not folded in.
                        identity = uuid.uuid4()
                        async with runtime().pool().acquire() as conn:
                            async with conn.transaction():
                                await conn.execute("""INSERT INTO tank.ledger
                                    (id,tenant_id,region,agent,skill,task_type,task_id,model,rung,cost_usd,outcome)
                                    VALUES($1,$2,$3,'base',$4,$5,$6,'code',3,0,$7)""",
                                    identity, run.tenant, run.region, run.skill, run.skill.split("@")[0], run.task_id,
                                    "failed" if result.get("error") else "delivered")
                                await link(conn, run, identity)
                    assert started is not None
                    await send(started)
                    await send({"type": "http.response.body", "body": bytes(content)})
            else:
                await send(message)
        try:
            await self.app(scope, replay, capture)
        finally:
            paid_steps.current.reset(paid_token)
            current.reset(token)
            controls._task.reset(task_token)
