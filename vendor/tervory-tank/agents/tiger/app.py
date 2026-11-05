"""Tiger — watches pages and keeps a price history. It knows no industry."""

from __future__ import annotations

import asyncio
import json
import hmac
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

import httpx
from fastapi import Header, HTTPException, Query
from gold_sources import candidates
from pydantic import BaseModel

from alert_email import deliver_pending
from rig_watch import RigWatches, TankRig, check_now, CatalogueDiscovery, DiscoveryGate
from chassis.app import create_app
from chassis.jobs import JobContext
from watch import (
    SCHEMA,
    BadWatch,
    DomainLimiter,
    DuplicateWatch,
    FeedUnavailable,
    PgStore,
    check_every,
    check_text,
    check_threshold,
    check_url,
    dispatch_tenant,
    run_check,
    ensure_tables,
)

AGENT = os.environ.get("AGENT_NAME", "tiger")
TIGER_URL = os.environ.get("TIGER_URL", "http://tiger:3000")
HEARTBEAT_TENANT = "00000000-0000-0000-0000-000000000001"
INTERVAL_S = float(os.environ.get("DOMAIN_INTERVAL_S", "10"))
ROBOTS_UA = "Tervory/1.0 (+https://tervory.com)"

log = logging.getLogger("agent")
_limiter = DomainLimiter(INTERVAL_S)


async def ping(ctx: JobContext) -> dict:
    result: dict = {"pong": True, "agent": ctx.agent}
    if ctx.input.get("heartbeat"):
        try:
            saved = await ctx.memory.remember(
                ctx.tenant_id,
                f"{ctx.agent} heartbeat",
                "heartbeat",
                "schedule:*/5",
            )
            result["heartbeats_seen"] = saved.get("seen")
        except Exception as exc:  # noqa: BLE001 — class name only, never the message
            log.warning("heartbeat memory failed (%s)", type(exc).__name__)
            result["memory"] = "unreachable"
    return result


CATALOGUE_FETCH_BUDGET_S = 12
CATALOGUE_RENDER_BUDGET_S = 15


async def _render(url: str, session: str, agent: str, tenant: str, task: str = "", *, catalogue_match: bool = False) -> dict[str, Any]:
    async with asyncio.timeout(CATALOGUE_RENDER_BUDGET_S if catalogue_match else None):
        return await _render_request(url, session, agent, tenant, task, catalogue_match=catalogue_match)


async def _render_request(url: str, session: str, agent: str, tenant: str, task: str = "", *, catalogue_match: bool = False) -> dict[str, Any]:
    response = await app.state.http.post(
        f"{TIGER_URL.rstrip('/')}/render",
        json={"url": url, "catalogue_match": catalogue_match, "screenshot": not catalogue_match, "settle_ms": 0 if catalogue_match else 10000, "max_ms": 10000 if catalogue_match else 25000,
              "request_timeout_ms": 15000 if catalogue_match else 45000, "price_reader": True},
        headers={"X-Tank-Key": os.environ.get("TANK_KEY", ""), "X-Tenant-ID": tenant,
                 "X-Home-Region": os.environ.get("TANK_REGION", "us-east"), "X-Task-ID": task},
        timeout=20 if catalogue_match else 50,
    )
    if response.status_code >= 400 or (catalogue_match and not 200 <= response.status_code < 300):
        return {"ok": False, "url": url, "challenge": not catalogue_match and response.status_code in {401, 403, 429},
                "error": f"browser answered {response.status_code}"}
    data = response.json()
    if not isinstance(data, dict):
        return {"ok": False, "challenge": False, "error": "browser answer was not an object"}
    if not catalogue_match and data.get("login_status") == "logged in" and not data.get("portal_item"):
        # Login itself returns status only. Read numeric fields in a second
        # request, with the session cookie in the same room's own browser.
        response = await app.state.http.post(f"{TIGER_URL.rstrip('/')}/render",
            json={"url": url, "max_ms": 25000, "request_timeout_ms": 45000}, headers={"X-Tank-Key": os.environ.get("TANK_KEY", ""),
            "X-Tenant-ID": tenant, "X-Home-Region": os.environ.get("TANK_REGION", "us-east"), "X-Task-ID": task}, timeout=50)
        if response.status_code != 200: return {"ok": False, "error": "Private page unavailable."}
        data = response.json()
    return data


async def _fetch_text(url: str, *, catalogue: bool = False):
    async with asyncio.timeout(CATALOGUE_FETCH_BUDGET_S if catalogue else None):
        return await _fetch_text_request(url, catalogue=catalogue)


async def _fetch_text_request(url: str, *, catalogue: bool = False):
    from urllib.parse import urlsplit
    if catalogue:
        await asyncio.to_thread(check_url, url)
        cap = 100_000 if urlsplit(url).path == "/robots.txt" else 4_000_000
        async with app.state.http.stream("GET", url, timeout=8, headers={"User-Agent": ROBOTS_UA}, follow_redirects=False) as response:
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk[:cap - len(raw)])
                if len(raw) >= cap:
                    break
            return {"status": response.status_code, "location": response.headers.get("location"),
                    "text": raw.decode("utf-8-sig", errors="replace"), "truncated": len(raw) >= cap}
    response = await app.state.http.get(
        url,
        timeout=8,
        headers={"User-Agent": ROBOTS_UA},
        follow_redirects=True,
    )
    if response.status_code != 200:
        return ""
    return response.text[:100_000]


async def official_feed(tenant: str, url: str) -> dict:
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if parsed.scheme != "https": return {"found": False}
    try:
        response = await app.state.http.get(
            os.environ.get("TANK_URL", "http://tank:8000") + "/v1/access/feeds/price",
            params={"tenant_id": tenant, "site": parsed.scheme + "://" + parsed.netloc, "url": url},
            headers={"X-Tank-Key": os.environ.get("TANK_KEY", "")}, timeout=30)
    except httpx.HTTPError as exc:
        raise FeedUnavailable("Official feed lookup is unavailable.") from exc
    if response.status_code != 200:
        raise FeedUnavailable("Official feed lookup is unavailable.")
    try:
        data = response.json()
    except ValueError as exc:
        raise FeedUnavailable("Official feed lookup returned an unreadable answer.") from exc
    if not isinstance(data, dict) or type(data.get("found")) is not bool:
        raise FeedUnavailable("Official feed lookup returned an unreadable answer.")
    return data


async def check(ctx: JobContext) -> dict:
    url = str(ctx.input.get("url") or "").strip()
    store = PgStore(app.state.pool, SCHEMA)
    async def playbook(payload):
        return await ctx.tank.approved_skill(ctx.tenant_id, "tiger.playbook", payload)
    store.skill_runner = playbook
    rig = RigWatches(store)
    managed = await rig.managed(ctx.tenant_id, url)
    if managed:
        await rig.sync(ctx.tenant_id, TankRig(app.state.http))
    watch = await store.get_watch(ctx.tenant_id, url)
    if watch is None or not watch.get("enabled"):
        return {"status": "skipped", "url": url, "alerts": []}

    async def extract(html: str, page_url: str) -> dict[str, Any]:
        return await ctx.tank.run_skill(ctx.tenant_id, "tiger.price_extract", {"html": html, "url": page_url})

    async def ocr(png_base64: str, page_url: str) -> dict[str, Any]:
        return await ctx.tank.run_skill(ctx.tenant_id, "tiger.price_ocr", {"png_base64": png_base64, "url": page_url})

    async def render(url, session, agent, tenant):
        return await _render(url, session, agent, tenant, str(ctx.job_id))

    result = await run_check(
        store,
        ctx.tenant_id,
        watch,
        ctx.agent,
        render,
        extract,
        ocr,
        _fetch_text,
        _limiter,
        files=ctx.files,
        feed=official_feed,
        product_context=None if managed else TankRig(app.state.http).alert_context,
        skills=playbook,
    )
    try:
        await rig.deliver(ctx.tenant_id, TankRig(app.state.http))
    except (httpx.HTTPError, ValueError):
        log.warning("Rig watch delivery is unavailable; saved readings remain pending.")
    return result


async def dispatch(ctx: JobContext) -> dict:
    store = PgStore(app.state.pool, SCHEMA)
    try:
        await RigWatches(store).deliver(ctx.tenant_id, TankRig(app.state.http))
    except (httpx.HTTPError, ValueError):
        log.warning("Rig watch discovery is unavailable; managed reads will recheck eligibility.")
    result = await dispatch_tenant(store, ctx.tenant_id, datetime.now(timezone.utc))
    result["email"] = await deliver_pending(store, ctx.tenant_id)
    return result


async def rig_discover(ctx: JobContext) -> dict:
    if str(ctx.tenant_id) != HEARTBEAT_TENANT:
        return {"tenants": 0}
    count = await RigWatches(PgStore(app.state.pool, SCHEMA)).discover(TankRig(app.state.http))
    return {"tenants": count}


async def competitor_match(ctx: JobContext) -> dict:
    root = str(ctx.input.get('root_job_id') or ctx.job_id)
    try:
        return await _competitor_match_slice(ctx, root)
    except (Exception, asyncio.CancelledError):
        try:
            await asyncio.shield(_record_match_failure(ctx, root))
        except Exception:
            log.warning('Could not record Tiger match failure; card loading will reconcile job status.')
        raise


async def _record_match_failure(ctx, root):
    # This includes cancellation by the chassis timeout. Do not mark retries
    # failed; the card also reconciles a worker crash/reclaim from jobs.status.
    async with asyncio.timeout(3):
        async with app.state.pool.acquire() as conn:
            last = await conn.fetchval('SELECT attempts>=max_attempts FROM agent_tiger.jobs WHERE tenant_id=$1 AND id=$2',
                                       UUID(str(ctx.tenant_id)), UUID(str(ctx.job_id)))
            if last:
                await conn.execute("""UPDATE agent_tiger.match_runs SET progress=COALESCE(progress,'{}'::jsonb)||$3::jsonb
                    WHERE tenant_id=$1 AND (job_id=$2 OR operation_id=(
                        SELECT (input->>'operation_id')::uuid FROM agent_tiger.jobs WHERE tenant_id=$1 AND id=$2))""",
                    UUID(str(ctx.tenant_id)), UUID(root), json.dumps({'status':'failed','failed_job_id':str(ctx.job_id)}))


async def _competitor_match_slice(ctx, root):
    from uuid import uuid5, NAMESPACE_URL
    tenant, current_id, root_id = UUID(str(ctx.tenant_id)), UUID(str(ctx.job_id)), UUID(root)
    async with app.state.pool.acquire() as conn:
        previous = await conn.fetchval('SELECT result FROM agent_tiger.jobs WHERE tenant_id=$1 AND id=$2', tenant, current_id)
        if isinstance(previous, str):
            previous = json.loads(previous)
        if previous and previous.get('slice_saved'):
            return previous
        # The immutable catalogue/sites live once in the original job input.
        original = await conn.fetchval("SELECT input FROM agent_tiger.jobs WHERE tenant_id=$1 AND id=$2 AND kind='competitor_match'", tenant, root_id)
        original = json.loads(original) if isinstance(original, str) else original
        if original is None:
            raise ValueError('Missing matching run')
        operation = UUID(original['operation_id']) if original.get('operation_id') else None
        run = await conn.fetchrow('SELECT cursor,progress FROM agent_tiger.match_runs WHERE tenant_id=$1 AND (job_id=$2 OR operation_id=$3)', tenant, root_id, operation)
        if run is None:
            raise ValueError('Missing matching run')
        cursor = json.loads(run['cursor']) if isinstance(run['cursor'], str) else run['cursor']
        progress = json.loads(run['progress']) if isinstance(run['progress'], str) else run['progress']
        saved_sites = {site['site']:site for site in (progress or {}).get('sites', [])}
    corridor = TankRig(app.state.http)
    async def fetch(url):
        return await _fetch_text(url, catalogue=True)
    async def render(url):
        return await _render(url, "", AGENT, ctx.tenant_id, str(ctx.job_id), catalogue_match=True)
    async def publish(site, url, html, human_check):
        return await corridor.request('POST', '/match-page', json={
            'tenant_id': str(ctx.tenant_id), 'site': site, 'url': url,
            'job_id': str(ctx.job_id), 'root_job_id': root,
            'html': html, 'human_check': human_check})
    cursor, done = await CatalogueDiscovery(fetch, render, DiscoveryGate(app.state.pool), publish).step(
        original.get('sites', []), original.get('products', []), cursor)
    continuation = None if done else uuid5(NAMESPACE_URL, str(ctx.job_id) + ':continuation')
    # Full finished-site warnings live only in this run's mutable progress row.
    # Neither continuation results nor the cursor repeat those lists.
    summaries, detailed_sites = [], []
    for site in cursor['sites']:
        warnings = site.get('warnings', saved_sites.get(site['site'], {}).get('warnings', []))
        summary = {**{key:value for key,value in site.items() if key != 'warnings'}, 'warning_count':len(warnings)}
        summaries.append(summary)
        detailed_sites.append({**summary, 'warnings':warnings})
    cursor['sites'] = summaries
    result = {'slice_saved': True, 'status': 'completed' if done else 'running',
              'sites': summaries, 'model_calls': 0, 'continuation_id': str(continuation) if continuation else None}
    progress = {**result, 'sites':detailed_sites}
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            if continuation:
                await conn.execute("""INSERT INTO agent_tiger.jobs(id,tenant_id,kind,input,timeout_s,run_at)
                    VALUES($1,$2,'competitor_match',$3::jsonb,45,now()+interval '1 second') ON CONFLICT(id) DO NOTHING""",
                    continuation, tenant, json.dumps({'root_job_id':root}))
            await conn.execute('UPDATE agent_tiger.jobs SET result=$3::jsonb WHERE tenant_id=$1 AND id=$2', tenant, current_id, json.dumps(result))
            await conn.execute("""UPDATE agent_tiger.match_runs SET job_id=COALESCE(job_id,$2),progress=$3::jsonb,cursor=$4::jsonb
                WHERE tenant_id=$1 AND (job_id=$2 OR operation_id=$5)""",
                tenant, root_id, json.dumps(progress), None if done else json.dumps(cursor), operation)
    return result


app = create_app(
    agent=AGENT,
    handlers={"ping": ping, "check": check, "dispatch": dispatch, "rig_discover": rig_discover, "competitor_match": competitor_match},
    job_timeouts={"competitor_match": 45},
    schedules=[
        {"tenant_id": HEARTBEAT_TENANT, "cron": "* * * * *", "kind": "rig_discover", "input": {}},
        {
            "tenant_id": HEARTBEAT_TENANT,
            "cron": "*/5 * * * *",
            "kind": "ping",
            "input": {"heartbeat": True},
        }
    ],
)


_tiger_inner = app.router.lifespan_context


@asynccontextmanager
async def _tiger_lifespan(instance):
    async with _tiger_inner(instance):
        # Room DDL runs as the room owner, even when administrative reads use
        # a separate BYPASSRLS pool. Install it before the room serves traffic.
        pool = getattr(instance.state.pool, 'raw', instance.state.pool)
        async with pool.acquire() as conn:
            await ensure_tables(conn, SCHEMA)
        yield


app.router.lifespan_context = _tiger_lifespan


class WatchIn(BaseModel):
    tenant_id: UUID
    url: str
    label: str = ""
    engine: str = ""
    every: str
    enabled: bool = True
    threshold: float = 0
    alert_price: bool = True
    alert_stock: bool = True
    alert_new_product: bool = True
    alert_below_cost: bool = True
    alert_market_gap: bool = True


class WatchPatch(BaseModel):
    tenant_id: UUID
    url: str | None = None
    label: str | None = None
    engine: str | None = None
    every: str | None = None
    enabled: bool | None = None
    threshold: float | None = None
    alert_price: bool | None = None
    alert_stock: bool | None = None
    alert_new_product: bool | None = None
    alert_below_cost: bool | None = None
    alert_market_gap: bool | None = None


def _store() -> PgStore:
    return PgStore(app.state.pool, SCHEMA)


def _watch_http(exc: BadWatch) -> HTTPException:
    status = 400 if str(exc) == "url not allowed" else 422
    return HTTPException(status_code=status, detail=str(exc))


def _fields(body: WatchIn | WatchPatch, partial: bool) -> dict[str, Any]:
    try:
        fields: dict[str, Any] = {"tenant_id": str(body.tenant_id)}
        if body.url is not None:
            fields["url"] = check_url(body.url)
        elif not partial:
            raise BadWatch("url is required")
        if body.label is not None or not partial:
            fields["label"] = check_text(body.label or "", 200)
        if body.engine is not None or not partial:
            fields["engine"] = check_text(body.engine or "", 80)
        if body.every is not None:
            fields["every"] = check_every(body.every)
        elif not partial:
            raise BadWatch("every is required")
        if body.enabled is not None or not partial:
            fields["enabled"] = True if body.enabled is None else bool(body.enabled)
        if body.threshold is not None or not partial:
            fields["threshold"] = check_threshold(0 if body.threshold is None else body.threshold)
        for name in ("alert_price", "alert_stock", "alert_new_product", "alert_below_cost", "alert_market_gap"):
            value = getattr(body, name)
            if value is not None or not partial:
                fields[name] = True if value is None else value
        return fields
    except BadWatch as exc:
        raise _watch_http(exc) from exc


@app.post("/watchlist", status_code=201)
async def create_watch(body: WatchIn) -> dict[str, Any]:
    fields = _fields(body, partial=False)
    try:
        return await _store().create_watch(fields)
    except DuplicateWatch as exc:
        raise HTTPException(status_code=409, detail="that url is already watched") from exc


@app.get("/watchlist")
async def list_watches(tenant_id: UUID = Query()) -> dict[str, Any]:
    rows = await _store().list_watches(str(tenant_id))
    return {"watches": rows}


@app.get("/watchlist/{watch_id}")
async def get_watch(watch_id: UUID, tenant_id: UUID = Query()) -> dict[str, Any]:
    row = await _store().get_watch_by_id(str(tenant_id), str(watch_id))
    if row is None:
        raise HTTPException(status_code=404, detail="no such watch")
    return row


@app.patch("/watchlist/{watch_id}")
async def update_watch(watch_id: UUID, body: WatchPatch) -> dict[str, Any]:
    fields = _fields(body, partial=True)
    fields.pop("tenant_id", None)
    try:
        row = await _store().update_watch(str(body.tenant_id), str(watch_id), fields)
    except DuplicateWatch as exc:
        raise HTTPException(status_code=409, detail="that url is already watched") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="no such watch")
    return row


@app.delete("/watchlist/{watch_id}", status_code=204)
async def delete_watch(watch_id: UUID, tenant_id: UUID = Query()):
    if not await _store().delete_watch(str(tenant_id), str(watch_id)):
        raise HTTPException(status_code=404, detail="no such watch")


class CheckNowIn(BaseModel):
    tenant_id: UUID
    url: str


@app.post("/rig/check-now")
async def rig_check_now(body: CheckNowIn, x_rig_check_key: str = Header(default="")) -> dict[str, Any]:
    """Rig Locate confirm: one live read of a page this tenant's Tiger watches."""
    expected = os.environ.get("RIG_TIGER_CHECK_KEY", "")
    if not expected or not hmac.compare_digest(expected.encode(), x_rig_check_key.encode()):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        page = check_url(body.url)
    except BadWatch as exc:
        raise _watch_http(exc) from exc
    tenant = str(body.tenant_id)

    async def render(url: str) -> dict[str, Any]:
        return await _render(url, "", AGENT, tenant)

    async def extract(html: str, url: str) -> dict[str, Any]:
        return await app.state.tank.run_skill(tenant, "tiger.price_structured", {"html": html, "url": url})

    return await check_now(_store(), tenant, page, render, extract, _fetch_text)


@app.get("/observations")
async def list_observations(tenant_id: UUID = Query(), url: str = Query()) -> dict[str, Any]:
    try:
        page = check_url(url)
    except BadWatch as exc:
        raise _watch_http(exc) from exc
    rows = await _store().list_observations(str(tenant_id), page)
    return {"observations": rows}


@app.get("/alerts")
async def list_alerts(tenant_id: UUID = Query(), url: str | None = Query(default=None),
                      kind: Literal["price", "stock", "new_product", "below_cost", "market_gap"] | None = Query(default=None)) -> dict[str, Any]:
    page = None
    if url is not None:
        try:
            page = check_url(url)
        except BadWatch as exc:
            raise _watch_http(exc) from exc
    rows = await _store().list_alerts(str(tenant_id), page, kind)
    return {"alerts": rows}


@app.get("/gold/candidates")
async def gold_candidates(
    limit: int = Query(default=200, ge=1, le=200),
    x_tank_key: str = Header(default=""),
) -> list[dict]:
    key = os.environ.get("TANK_KEY", "")
    if not key or not hmac.compare_digest(key.encode("utf-8"), x_tank_key.encode("utf-8")):
        raise HTTPException(status_code=401, detail="room key required")
    return candidates(
        limit, region=os.environ.get("TANK_REGION", "us-east"),
    )
