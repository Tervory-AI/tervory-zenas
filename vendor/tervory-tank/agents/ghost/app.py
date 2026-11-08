"""Ghost's room: private state, fixed probes, authenticated entry points."""
from __future__ import annotations

import asyncio
import json
import hashlib
import hmac
import logging
import re
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import advisories as advisory_watch
from . import inventory as inv
from . import shields as shield_room
from .patch_brief import BURN_DETAIL as PATCH_BURN_DETAIL, PatchBurned, accept_patch_brief, passes_severity_bar
from .patch_connect import connect_match_to_plan, threat_to_patch_brief
from .patch_pr import open_dep_bump_pr
from .gold_sources import candidates
from .investigate import Investigator
from .models import Look, Message, Observation, PLATFORM_TENANT, Resolution, SITES, ROOMS, Signal, classify_message
from .threat_payload import BURN_DETAIL, ThreatBurned, ThreatAction, accept_threat_payload, threat_to_shield_request
from .guardrails import GUARD_BURN_DETAIL, GuardBurned, guard_inbound_if_enabled
from . import wraith as wraith_room
from . import rails as airgap_rails
from . import watch_oss as watch_oss_room
from .watch_oss import AnomalyEvent, handoff_threat, propose_from_anomaly
from . import enforce_oss as enforce_oss_room
from .client_settings import (
    ClientAutumbraSettings,
    active_halves,
    connect_copy,
    list_hands,
    load_settings,
    save_settings,
    set_hand,
)
from .market_lab_intel import (
    accept_ioc_bundle,
    backend_status as intel_backend_status,
    receipt_for_bundle_consume,
    register_advisories_from_ioc_bundle,
)
from .webhooks import WebhookRejected, accept_webhook, drop_counts
from .red_team import list_exercises, run_exercise
from .owner_email import send_owner_update
from .smtp_email import smtp_available_since, transport_status
from .durable_state import locked_state
from .owner_email_jobs import (
    build_owner_update_from_receipts,
    default_receipt_url,
    list_clients_with_owner,
    run_weekly_owner_updates,
)
from .probes import Probes
from .store import Store
from .tenant_queue import (
    list_founder_console_receipts,
    mirror_settings,
    queue_path_for_tenant,
    queue_root_base,
)
from .threat_queue import (
    ThreatPayloadQueue,
    queue_root_from_env,
    watch_enqueue_approve,
    watch_enqueue_fixture_attack,
    watch_enqueue_undo,
)
from .never_block import may_block_ip
from .undo_token import verify_receipts_token
from .configuration import interval_seconds, missing_settings, setting_status


async def require_tank(x_tank_key: str = Header(default="")) -> None:
    expected = os.environ.get("TANK_KEY", "")
    if not expected or not hmac.compare_digest(expected.encode(), x_tank_key.encode()):
        raise HTTPException(404, "Not found.")


class Tank:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http

    async def call(self, method: str, path: str, body: dict | None = None) -> dict:
        key = os.environ.get("TANK_KEY", "")
        if not key:
            raise RuntimeError("Autumbra Watch's Tank key is not configured.")
        try:
            response = await self.http.request(method, "http://tank:8000" + path,
                                               headers={"X-Tank-Key": key}, json=body, timeout=45)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Invalid response")
            return result
        except (httpx.HTTPError, ValueError):
            raise RuntimeError("Tank did not accept the request.") from None

    async def record(self, receipt_id: str, step: str, outcome: str, task_type: str = "ghost.incident") -> dict:
        if task_type not in {"ghost.incident", "ghost.advisory", "ghost.shield"}:
            raise ValueError("Unknown Autumbra Watch receipt type.")
        result = await self.call("POST", "/v1/ledger/vendor", {
            "id": receipt_id, "tenant_id": PLATFORM_TENANT, "agent": "ghost",
            "task_type": task_type, "vendor": "ghost." + step, "units": 1,
            "cost_usd": 0, "outcome": outcome, "region": os.environ.get("TANK_REGION", "us-east"),
        })
        if result.get("recorded") is not True or result.get("ledger_id") != receipt_id:
            raise RuntimeError("Tank did not record the receipt.")
        from .savings import price_receipt
        return await price_receipt(self, {"id": receipt_id, "tenant_id": PLATFORM_TENANT,
                                         "step": step, "outcome": outcome}, PLATFORM_TENANT)


def monitoring_checks() -> list[Look]:
    checks = []
    for target in (*SITES, *ROOMS, "tank"):
        signals = [Signal.health, Signal.slow]
        if target in SITES:
            signals += [Signal.dns, Signal.certificate, Signal.headers]
        if target != "za":
            signals += [Signal.deploy]
        if target in {"workspace", "tank"}:
            signals += [Signal.errors]
        if target == "tank":
            signals += [Signal.ci, Signal.radix]
        checks.extend(Look(target=target, signal=signal) for signal in signals)
    return checks


async def monitor_once(app: FastAPI) -> None:
    failures = 0
    for check in monitoring_checks():
        try:
            async with app.state.lock:
                observation = await app.state.probes.reproduce(check)
                await app.state.investigator.alert(observation)
        except Exception:
            failures += 1
    app.state.monitor = {"running": True, "unavailable_checks": failures}
    if getattr(app.state, "store", None) is not None:
        app.state.store.record_run(PLATFORM_TENANT, "monitor", "unavailable" if failures else "checked")


async def watch(app: FastAPI) -> None:
    while True:
        await monitor_once(app)
        await asyncio.sleep(300)


async def refresh_inventory(app: FastAPI) -> dict:
    """Coolify (read-only, only when its token is set) and our own sites' headers."""
    store, http = app.state.store, app.state.http
    seen = advisory_watch.iso(advisory_watch.now_utc())
    result: dict = {}
    try:
        components = await inv.fetch_coolify(http)
        if components is None:
            result["coolify"] = "not_connected"
        else:
            store.replace_inventory(PLATFORM_TENANT, "coolify", [vars(c) for c in components], seen)
            result["coolify"] = "ok"
    except Exception:
        result["coolify"] = "unavailable"
    failed = 0
    for app_name, host in inv.WATCHED_SITES.items():
        try:
            headers = await inv.fetch_headers(http, app_name, host)
            store.replace_inventory(PLATFORM_TENANT, "headers", [vars(c) for c in headers], seen,
                                    apps={app_name})
        except Exception:
            failed += 1
    result["headers"] = "ok" if not failed else f"{len(inv.WATCHED_SITES) - failed}_of_{len(inv.WATCHED_SITES)}"
    store.set_state(PLATFORM_TENANT, "inventory_refreshed", {
        "at": seen, "complete": not failed and result["coolify"] != "unavailable", **result})
    store.record_run(PLATFORM_TENANT, "inventory", "unavailable" if failed or result["coolify"] == "unavailable" else "checked")
    return result


async def nightly(app: FastAPI) -> None:
    while True:
        last = app.state.store.get_state(PLATFORM_TENANT, "inventory_refreshed", {})
        moment = advisory_watch.parse_time(last.get("at"))
        if not last.get("complete", True) or moment is None or (advisory_watch.now_utc() - moment).total_seconds() > 86400:
            try:
                async with app.state.advisory_lock:
                    result = await refresh_inventory(app)
                    app.state.inventory_status = "connected" if isinstance(result, dict) and result.get("headers") == "ok" and result.get("coolify") != "unavailable" else "unavailable"
            except Exception:
                app.state.inventory_status = "unavailable"
        await asyncio.sleep(1800)


async def bounded_json(request: Request, limit: int) -> bytes:
    raw = bytearray()
    async with asyncio.timeout(30):
        async for part in request.stream():
            if len(raw) + len(part) > limit:
                raise HTTPException(413, "Request too large.")
            raw.extend(part)
    request._body = bytes(raw)  # Authentication and handler reuse the same bounded bytes.
    return request._body


def watch_queue_configured() -> bool:
    return bool(
        (os.environ.get("THREAT_PAYLOAD_QUEUE_ROOT") or "").strip()
        or (os.environ.get("THREAT_PAYLOAD_QUEUE") or "").strip()
    )


def watch_threat_queue(tenant_id: str | None = None) -> ThreatPayloadQueue:
    """Production Watch queue door — per-tenant path when tenant_id is given."""
    if tenant_id:
        return ThreatPayloadQueue(queue_path_for_tenant(tenant_id))
    # Legacy single-queue env (one client dogfood).
    return ThreatPayloadQueue(queue_root_from_env())



def _require_tenant_uuid(tenant_id: str | None) -> str:
    """Non-UUID tenant_id → ValueError (mapped to clean 4xx by callers)."""
    tid = (tenant_id or "").strip()
    try:
        return str(UUID(tid))
    except (TypeError, ValueError):
        raise ValueError("tenant_id must be a UUID") from None


def _receipts_for_tenant(tenant_id: str) -> list[dict]:
    """Load Enforce/Watch receipts for one client from their queue box."""
    if (not watch_queue_configured() and transport_status() == "fixture"
            and os.environ.get("AUTUMBRA_FIXTURE_COMPOSE_ROOT")):
        # The explicit local lifecycle fixture has no Enforce queue yet. This
        # preview can use the fixture sender; SMTP jobs always require storage.
        return []
    try:
        q = watch_threat_queue(tenant_id)
        return list(q.list_receipts())
    except Exception:
        raise RuntimeError("Receipt storage is unavailable.") from None


def enqueue_fixture_attack_for_watch(
    *,
    raw_log: str,
    site: str,
    address: str,
    reason: str = "fixture attacking address",
    tenant_id: str | None = None,
    advisory: dict | None = None,
    queue: ThreatPayloadQueue | None = None,
    source_data: dict[str, int] | None = None,
) -> dict:
    """Production Watch enqueue entrypoint used by /ghost/autumbra/queue/fixture-attack.

    Validates via never-block rules, then puts a ThreatPayload on that client's queue.
    Does not call Enforce. Never keeps the raw log on the queue.
    """
    if queue is not None:
        q = queue
    elif tenant_id:
        q = watch_threat_queue(tenant_id)
    else:
        q = watch_threat_queue()
    return watch_enqueue_fixture_attack(
        queue=q,
        raw_log=raw_log,
        site=site,
        address=address,
        reason=reason,
        tenant_id=tenant_id,
        advisory=advisory,
        source_data=source_data,
    )


async def deliver_weekly_owner_updates(app: FastAPI, *, now: datetime | None = None) -> dict:
    """Send the last completed UTC week; persisted delivery claims survive restarts."""
    from .owner_email_jobs import week_bounds
    result = {"sent": 0, "unavailable": 0}
    if transport_status() != "smtp":
        return result
    end, _ = week_bounds(now=now)
    start = end - timedelta(days=7)
    store = app.state.store
    for client in list_clients_with_owner(store):
        previous = store.get_state(client.tenant_id, "owner_weekly_sent", {})
        if previous.get("week_end") == end.isoformat():
            continue
        try:
            receipts = _receipts_for_tenant(client.tenant_id)
            rows = await asyncio.to_thread(run_weekly_owner_updates, [client],
                receipts_by_tenant={client.tenant_id: receipts}, week_start=start,
                week_end=end - timedelta(microseconds=1))
            row = rows[0]
            if row.get("sent") or row.get("delivery_state") == "sent":
                store.set_state(client.tenant_id, "owner_weekly_sent", {"week_end": end.isoformat()})
                result["sent"] += int(bool(row.get("sent")))
            else:
                result["unavailable"] += 1
        except Exception:
            result["unavailable"] += 1
    return result


async def _weekly_owner_email_loop(app: "FastAPI") -> None:
    interval = interval_seconds("AUTUMBRA_WEEKLY_INTERVAL_SEC", 3600, 1, 3600)
    while True:
        try:
            app.state.owner_weekly_status = await deliver_weekly_owner_updates(app)
        except Exception:
            app.state.owner_weekly_status = {"unavailable": 1}
        await asyncio.sleep(interval)


async def deliver_act_owner_updates(app: FastAPI) -> None:
    """The closed Enforce box emits receipts; Watch alone owns real SMTP delivery."""
    app.state.owner_act_status = {"sent": 0, "unavailable": 0}
    if transport_status() != "smtp":
        return
    from .owner_email_jobs import _parse_receipt_time, week_bounds
    _, end = week_bounds()
    available_since = smtp_available_since()
    for settings in list_clients_with_owner(app.state.store):
        try:
            receipts = _receipts_for_tenant(settings.tenant_id)
        except RuntimeError:
            app.state.owner_act_status["unavailable"] += 1
            continue
        for receipt in receipts:
            moment = _parse_receipt_time(receipt)
            identity = str(receipt.get("id") or "")
            if (not receipt.get("applied") or not identity or moment is None
                    or receipt.get("kind") == "exercise"
                    or receipt.get("tenant_id", settings.tenant_id) != settings.tenant_id
                    or moment.timestamp() <= available_since or moment > end):
                continue
            try:
                email = build_owner_update_from_receipts(settings=settings, receipts=[receipt],
                    week_start=datetime.fromtimestamp(available_since, timezone.utc), week_end=end)
                result = await asyncio.to_thread(send_owner_update, email, to=settings.owner_email,
                                                tenant_id=settings.tenant_id, message_key="act:" + identity)
                app.state.owner_act_status["sent"] += int(bool(result.get("sent")))
                if not result.get("sent") and result.get("delivery_state") != "sent":
                    app.state.owner_act_status["unavailable"] += 1
            except Exception:
                app.state.owner_act_status["unavailable"] += 1
                continue


async def _act_owner_email_loop(app: FastAPI) -> None:
    while True:
        try:
            await deliver_act_owner_updates(app)
        except Exception:
            # Durable SMTP claims remain sent/uncertain; never automatically resend.
            app.state.owner_act_status = {"sent": 0, "unavailable": 1}
        await asyncio.sleep(30)


async def refresh_receipt_savings(app: FastAPI) -> dict:
    """Price durable code receipts out of band; reading a receipt never waits on Tank."""
    from .savings import price_receipt
    status = {"priced": 0, "unavailable": 0}
    if not watch_queue_configured():
        return status
    tenants = {str(row[0]) for row in app.state.store.db.execute(
        "SELECT tenant FROM ghost_state WHERE key=?", ("autumbra_client_settings",))}
    tenants.add(PLATFORM_TENANT)
    if os.environ.get("TENANT_ID"):
        tenants.add(os.environ["TENANT_ID"])
    for tenant in tenants:
        try:
            queue = watch_threat_queue(_require_tenant_uuid(tenant))
            stored = {row["id"]: row for row in app.state.store.run_receipts(tenant)}
            receipts = {str(row.get("id")): row for row in queue.list_receipts()}
            receipts.update(stored)
            for receipt in receipts.values():
                if (receipt.get("tenant_id", tenant) != tenant or receipt.get("fixture")
                    or receipt.get("kind") == "exercise" or receipt.get("control") == "fixture_edge"
                    or receipt.get("baseline_status") == "priced"):
                    continue
                await price_receipt(Tank(app.state.http), receipt, tenant)
                queue.write_receipt(receipt)
                if receipt.get("id") in stored:
                    app.state.store.save_run(tenant, receipt)
                status["priced" if receipt.get("baseline_status") == "priced" else "unavailable"] += 1
        except (RuntimeError, OSError, ValueError):
            status["unavailable"] += 1
    return status


async def _receipt_savings_loop(app: FastAPI) -> None:
    while True:
        try:
            app.state.savings_status = await refresh_receipt_savings(app)
        except Exception:
            app.state.savings_status = {"priced": 0, "unavailable": 1}
        await asyncio.sleep(30)


async def refresh_market_lab_intel(app: FastAPI) -> dict:
    """Fetch through Tank only; use the existing validator, catalog and receipts."""
    from uuid import NAMESPACE_URL, uuid5
    from .market_lab_intel import load_market_lab_advisories
    raw = await Tank(app.state.http).call("GET", "/v1/market-lab/intel")
    bundle = accept_ioc_bundle(raw)
    load_market_lab_advisories([])
    register_advisories_from_ioc_bundle(bundle)
    tenants = {PLATFORM_TENANT}
    tenants.update(str(row[0]) for row in app.state.store.db.execute(
        "SELECT DISTINCT tenant FROM ghost_state WHERE key=$1", {"1": "autumbra_client_settings"}).fetchall())
    if watch_queue_configured():
        for tenant in tenants:
            queue = watch_threat_queue(tenant)
            ident = str(uuid5(NAMESPACE_URL, "market_lab.ioc.v1:" + tenant + ":" + bundle["bundle_id"]))
            if any(str(r.get("id")) == ident for r in queue.list_receipts()):
                continue
            receipt = receipt_for_bundle_consume(bundle, tenant_id=tenant)
            receipt["id"] = ident
            queue.write_receipt(receipt)
    return {"bundle_id": bundle["bundle_id"], "indicator_count": len(bundle["indicators"])}


async def _market_lab_intel_loop(app: FastAPI) -> None:
    while True:
        try:
            await refresh_market_lab_intel(app)
            app.state.intel_status = "connected"
        except Exception:
            app.state.intel_status = "unavailable"  # Keep the previous catalog; no fixture on live screens.
        await asyncio.sleep(3600)


async def _exercise_loop(app: FastAPI) -> None:
    from .red_team import scheduled_exercises
    while True:
        try:
            scheduled_exercises(app.state.store, PLATFORM_TENANT)
        except Exception:
            app.state.store.set_state(PLATFORM_TENANT, "exercise_status", "unavailable")
        await asyncio.sleep(3600)


def _block_memory_unavailable(app):
    app.state.block_memory_failures += 1
    logging.getLogger(__name__).warning("Autumbra block-memory reconciliation unavailable")


async def _standing_blocks_loop(app):
    while True:
        root = queue_root_base()
        if root and root.exists():
            for path in root.iterdir():
                try:
                    tenant = str(UUID(path.name))
                except ValueError:
                    continue
                try:
                    await app.state.shared_blocks.reconcile(tenant)
                except Exception:
                    _block_memory_unavailable(app)
        await asyncio.sleep(60)


USER_ID = r"^[A-Za-z0-9_.:@-]{1,128}$"


class BrainTaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: uuid.UUID
    user_id: str | None = Field(default=None, pattern=USER_ID)
    run_id: uuid.UUID | None = None
    task_type: str = Field(max_length=80)
    text: str = Field(min_length=1, max_length=4000)


class CorrectionIn(BrainTaskIn):
    corrected: str = Field(min_length=1, max_length=4000)


class MemorySettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: uuid.UUID
    user_id: str = Field(pattern=USER_ID)
    off: bool


class MemoryEditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: uuid.UUID
    user_id: str = Field(pattern=USER_ID)
    text: str = Field(min_length=1, max_length=4000)


class MemoryWriteIn(MemoryEditIn):
    kind: str = Field(max_length=40)
    evidence: list[str] = Field(min_length=1, max_length=20)


class MemoryRecallIn(MemoryEditIn):
    pass


def _brain_on() -> bool:
    from .brain import brain_enabled
    return brain_enabled()


def _brain_service(http: httpx.AsyncClient):
    from .brain_service import tank_client
    async def call(body: dict, actor=None) -> dict:
        if actor is None or str(actor.tenant_id) != body["tenant_id"] or actor.user_id != body["user_id"]:
            raise ValueError("Verified brain actor required")
        try:
            data = await tank_client(http).delegate_task(actor, body["task_id"], body["task_type"],
                {"text": body["text"], "case_ref": body["case_ref"], "home_region": body["home_region"]}, body["memory"])
        except (httpx.HTTPError, ValueError, AttributeError):
            raise RuntimeError("Autumbra brain unavailable") from None
        return {"tenant_id": body["tenant_id"], "task_id": body["task_id"], "status": "done",
                "answer": data["result"], "model_receipts": [{**data["receipt"], "cache": data["receipt"]["model"] == "cache"}],
                "steps": [{"action": "tank_route", "task_type": body["task_type"]}]}
    return call


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.missing_settings = missing_settings("watch")
        app.state.invalid_settings = []
        region = os.environ.get("TANK_REGION") or "us-east"
        if region not in {"us-east", "za"}:
            app.state.invalid_settings.append("TANK_REGION")
        if app.state.missing_settings or app.state.invalid_settings:
            app.state.ready = False
            yield
            return
        try:
            store = Store(Path(os.environ.get("DATA_DIR") or "/data") / "ghost.sqlite3", region)
        except (OSError, sqlite3.Error, ValueError):
            app.state.invalid_settings = ["DATA_DIR"]
            app.state.ready = False
            yield
            return
        app.state.ready = True
        from .threat_payload import bind_settings_store
        bind_settings_store(store)
        async with httpx.AsyncClient(follow_redirects=False) as http:
            tank = Tank(http)
            app.state.store = store
            app.state.probes = Probes(http, tank, os.environ["TANK_KEY"])
            app.state.investigator = Investigator(store, app.state.probes, tank)
            app.state.lock = asyncio.Lock()
            app.state.monitor = {"running": False, "unavailable_checks": 0}
            app.state.http = http
            app.state.advisory_lock = asyncio.Lock()
            app.state.watcher = advisory_watch.Watcher(store, advisory_watch.Feeds(http), tank)
            app.state.shields = shield_room.Shields(store, http, tank)
            app.state.threat_queue = None
            if watch_queue_configured():
                # Prefer root; per-tenant queues opened on demand.
                try:
                    app.state.threat_queue = watch_threat_queue()
                except Exception:
                    app.state.threat_queue = None
            app.state.threat_queue_root = queue_root_base()
            from chassis.memory import MemoryClient
            from .shared_blocks import StandingBlocks
            app.state.block_memory_failures = 0
            app.state.shared_blocks = StandingBlocks(
                MemoryClient(base_url="http://tank:8000/v1/ghost/autumbra/memory", http=http),
                tank, store, watch_threat_queue)
            from .brain import Brain, UserMemory, classify_alert_code
            app.state.user_memory = UserMemory(
                MemoryClient(base_url="http://tank:8000/v1/ghost/autumbra/memory", http=http), store)
            app.state.brain = Brain(app.state.user_memory, _brain_service(http), [classify_alert_code])

            tasks = [
                asyncio.create_task(watch(app)),
                asyncio.create_task(_standing_blocks_loop(app)),
                asyncio.create_task(nightly(app)),
                asyncio.create_task(advisory_watch.watch(app)),
                asyncio.create_task(_weekly_owner_email_loop(app)),
                asyncio.create_task(_act_owner_email_loop(app)),
                asyncio.create_task(_market_lab_intel_loop(app)),
                asyncio.create_task(_exercise_loop(app)),
            ]
            try:
                yield
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                store.close()

    app = FastAPI(title="Autumbra Watch", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def require_ready(request, call_next):
        if request.url.path != "/health" and getattr(app.state, "ready", True) is False:
            return JSONResponse(status_code=503, content={
                "detail": "Configure the named settings before starting Autumbra Watch.",
                "missing_settings": app.state.missing_settings,
                "invalid_settings": app.state.invalid_settings})
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_request, _error):
        # Pydantic's default errors include the rejected input (possibly a secret).
        return JSONResponse(status_code=422, content={"detail": "Invalid Autumbra Watch request."})

    @app.exception_handler(RuntimeError)
    async def unavailable(_request, _error):
        return JSONResponse(status_code=503, content={"detail": "A required service is unavailable. No production action was taken."})

    @app.exception_handler(TimeoutError)
    @app.exception_handler(OSError)
    async def storage_unavailable(_request, _error):
        return JSONResponse(status_code=503, content={"detail": "A required service is unavailable. No production action was taken."})

    @app.get("/health")
    async def health():
        return {"ok": True, "agent": "ghost",
                "ready": getattr(app.state, "ready", hasattr(app.state, "store")),
                "missing_settings": getattr(app.state, "missing_settings", missing_settings("watch")),
                "invalid_settings": getattr(app.state, "invalid_settings", []),
                "settings": {name: setting_status(name) for name in ("watch", "inventory", "owner_email", "owner_links")},
                "owner_email_transport": transport_status(),
                "owner_weekly": getattr(app.state, "owner_weekly_status", {}),
                "owner_actions": getattr(app.state, "owner_act_status", {}),
                "inventory": getattr(app.state, "inventory_status", "unavailable"),
                "market_lab_intel": getattr(app.state, "intel_status", "unavailable")}

    @app.get("/incidents", dependencies=[Depends(require_tank)])
    async def incidents():
        background_failures = sum(bool(getattr(app.state, name, {}).get("unavailable"))
                                  for name in ("owner_weekly_status", "owner_act_status"))
        background_failures += int(getattr(app.state, "inventory_status", None) == "unavailable")
        monitor = {**app.state.monitor, "unavailable_checks": app.state.monitor.get("unavailable_checks", 0) + background_failures}
        return {"incidents": app.state.store.list(PLATFORM_TENANT), "monitor": monitor,
                "integrations": {"brain": "autumbra-brain" if _brain_on() else "off", "bucket": "awaiting_bucket_1",
                                 "workers": "founder_approval_required", "logs_ci_deploys": "awaiting_read_only_feed"}}

    @app.post("/look", dependencies=[Depends(require_tank)])
    async def look(body: Look):
        async with app.state.lock:
            return await app.state.investigator.look(body)

    @app.post("/message", dependencies=[Depends(require_tank)])
    async def message(body: Message):
        async with app.state.lock:
            return await app.state.investigator.look(classify_message(body.text), "message")

    async def memory_actor(request, tenant, user=None, *, required=True):
        from .brain_state import verified_actor
        return await verified_actor(request, tenant, user, required=required)

    def require_brain_enabled():
        if not _brain_on():
            raise HTTPException(503, "GHOST_BRAIN_ENABLED is off.")

    @app.post("/brain/tasks", dependencies=[Depends(require_tank)])
    async def brain_task(body: BrainTaskIn, request: Request):
        actor = await memory_actor(request, body.tenant_id, body.user_id, required=False)
        try:
            return await app.state.brain.run(str(body.tenant_id), actor.user_id if actor else None,
                                            body.task_type, body.text, str(body.run_id) if body.run_id else None, actor=actor)
        except PermissionError:
            raise HTTPException(403, "Signed memory refused.") from None
        except ValueError:
            raise HTTPException(422, "Invalid Autumbra brain task.") from None

    @app.post("/brain/corrections", dependencies=[Depends(require_tank)])
    async def brain_correction(body: CorrectionIn, request: Request):
        actor = await memory_actor(request, body.tenant_id, body.user_id)
        require_brain_enabled()
        try:
            saved = await app.state.brain.correct(str(body.tenant_id), actor.user_id, body.task_type, body.text, body.corrected)
        except ValueError:
            raise HTTPException(422, "Invalid Autumbra brain task.") from None
        return {"saved": saved is not None}

    @app.get("/memory", dependencies=[Depends(require_tank)])
    async def memory_list(request: Request, tenant_id: uuid.UUID, user_id: str):
        await memory_actor(request, tenant_id, user_id)
        mem = app.state.user_memory
        return {"off": mem.is_off(str(tenant_id), user_id),
                "memories": await mem.list(str(tenant_id), user_id)}

    @app.post("/memory", dependencies=[Depends(require_tank)])
    async def memory_write(body: MemoryWriteIn, request: Request):
        await memory_actor(request, body.tenant_id, body.user_id)
        require_brain_enabled()
        from .brain import contains_secret
        if any(len(e) > 1000 or contains_secret(e) for e in body.evidence):
            raise HTTPException(422, "Invalid memory evidence.")
        try:
            saved = await app.state.user_memory.remember(str(body.tenant_id), body.user_id,
                        body.kind, body.text, evidence=body.evidence)
        except ValueError:
            raise HTTPException(422, "Invalid memory kind.") from None
        return {"saved": saved is not None, **(saved or {})}

    @app.post("/memory/recall", dependencies=[Depends(require_tank)])
    async def memory_recall(body: MemoryRecallIn, request: Request):
        await memory_actor(request, body.tenant_id, body.user_id)
        require_brain_enabled()
        return {"memories": await app.state.user_memory.recall(str(body.tenant_id), body.user_id, body.text)}

    @app.get("/memory/{record_id}", dependencies=[Depends(require_tank)])
    async def memory_get(request: Request, record_id: uuid.UUID, tenant_id: uuid.UUID, user_id: str):
        await memory_actor(request, tenant_id, user_id)
        rows = await app.state.user_memory.list(str(tenant_id), user_id)
        row = next((r for r in rows if str(r.get("id")) == str(record_id)), None)
        if row is None:
            raise HTTPException(404, "Not found.")
        return row

    @app.put("/memory/settings", dependencies=[Depends(require_tank)])
    async def memory_settings(body: MemorySettingsIn, request: Request):
        await memory_actor(request, body.tenant_id, body.user_id)
        require_brain_enabled()
        app.state.user_memory.set_off(str(body.tenant_id), body.user_id, body.off)
        return {"off": body.off}

    @app.patch("/memory/{record_id}", dependencies=[Depends(require_tank)])
    async def memory_edit(record_id: uuid.UUID, body: MemoryEditIn, request: Request):
        await memory_actor(request, body.tenant_id, body.user_id)
        require_brain_enabled()
        try:
            return await app.state.user_memory.edit(str(body.tenant_id), body.user_id, str(record_id), body.text)
        except KeyError:
            raise HTTPException(404, "Not found.") from None
        except ValueError:
            raise HTTPException(422, "Memory cannot hold secrets.") from None

    @app.delete("/memory/{record_id}", dependencies=[Depends(require_tank)])
    async def memory_forget(request: Request, record_id: uuid.UUID, tenant_id: uuid.UUID, user_id: str):
        await memory_actor(request, tenant_id, user_id)
        require_brain_enabled()
        try:
            await app.state.user_memory.forget(str(tenant_id), user_id, str(record_id))
        except KeyError:
            raise HTTPException(404, "Not found.") from None
        return {"forgotten": True}

    @app.post("/alert", dependencies=[Depends(require_tank)])
    async def alert(body: Observation):
        async with app.state.lock:
            return {"incident": await app.state.investigator.alert(body)}

    @app.post("/incidents/{incident}/resolve", dependencies=[Depends(require_tank)])
    async def resolve(incident: UUID, body: Resolution):
        try:
            async with app.state.lock:
                return await app.state.investigator.resolve(str(incident), body)
        except LookupError:
            raise HTTPException(404, "Issue not found.") from None
        except ValueError:
            raise HTTPException(409, "The original check must pass before solving the issue.") from None

    @app.get("/gold/candidates", dependencies=[Depends(require_tank)])
    async def gold(limit: int = Query(default=200, ge=1, le=200)):
        return candidates(app.state.store, os.environ.get("TANK_REGION", "us-east"), limit)

    # ---------- GHOST-2: inventory and advisory watcher ----------

    @app.post("/ghost/inventory/report", dependencies=[Depends(require_tank)])
    async def inventory_report(request: Request):
        """Host and repo facts arrive here (Ghost has no Docker socket). Replaces that source's snapshot."""
        raw = await bounded_json(request, 2_000_000)
        try:
            report = inv.InventoryReport.from_body(raw)
            components = report.to_components()
        except ValueError:
            raise HTTPException(422, "Invalid Autumbra Watch request.") from None
        if not components:
            raise HTTPException(422, "The report has no version facts.")
        seen = advisory_watch.iso(advisory_watch.now_utc())
        async with app.state.advisory_lock:
            stored = app.state.store.replace_inventory(PLATFORM_TENANT, report.source, [vars(c) for c in components], seen)
        return {"accepted": True, **stored}

    @app.get("/ghost/inventory", dependencies=[Depends(require_tank)])
    async def inventory_list(history: bool = False):
        return {"components": app.state.store.inventory(PLATFORM_TENANT, current_only=not history),
                "refreshed": app.state.store.get_state(PLATFORM_TENANT, "inventory_refreshed")}

    @app.post("/ghost/inventory/refresh", dependencies=[Depends(require_tank)])
    async def inventory_refresh():
        """Called after each deploy (deploy-on-merge receipt); also runs nightly."""
        async with app.state.advisory_lock:
            return await refresh_inventory(app)

    @app.post("/ghost/advisories/pull", dependencies=[Depends(require_tank)])
    async def advisories_pull():
        async with app.state.advisory_lock:
            summary = await app.state.watcher.cycle()
            if getattr(app.state, "shields", None) is not None:
                await app.state.shields.after_cycle()
            return summary

    @app.get("/ghost/advisories/matches", dependencies=[Depends(require_tank)])
    async def advisory_matches(state: str = Query(default="open", pattern="^(open|resolved|all)$")):
        rows = app.state.store.matches(PLATFORM_TENANT, None if state == "all" else state)
        return {"matches": rows, "last_cycle": app.state.store.get_state(PLATFORM_TENANT, "last_cycle")}

    # ---------- GHOST-2 D3: Shields Up (Watch dry-run only; live apply is Enforce) ----------

    @app.get("/ghost/shields", dependencies=[Depends(require_tank)])
    async def shields_list():
        return {"mode": shield_room.mode(),
                "shields": [shield_room.public_view(s) for s in app.state.store.shields(PLATFORM_TENANT)]}

    @app.post("/ghost/triage/accept", dependencies=[Depends(require_tank)])
    async def triage_accept(request: Request):
        """Autumbra Dual-LLM air gap: optional guard (flag OFF), then ThreatPayload.

        Untrusted body hits guard_inbound_if_enabled first (no-op unless
        GHOST_GUARDRAILS_ENABLED / AUTUMBRA_GUARDRAILS_ENABLED=1), then
        accept_threat_payload. Burn on fail (422, fixed detail, never echo body).
        propose_shield only builds a dry-run/founder proposal — never raises a
        shield without the Shield tap. Watch has no weapons; Enforce stays behind
        the Enforce flag + founder tap.
        """
        raw = await bounded_json(request, 8_000)
        try:
            guard_inbound_if_enabled(raw)
            payload = accept_threat_payload(raw)
        except GuardBurned:
            raise HTTPException(422, GUARD_BURN_DETAIL) from None
        except ThreatBurned:
            raise HTTPException(422, BURN_DETAIL) from None
        if payload.action == ThreatAction.propose_shield:
            async with app.state.advisory_lock:
                try:
                    shield = await app.state.shields.propose(threat_to_shield_request(payload))
                except ValueError:
                    raise HTTPException(422, BURN_DETAIL) from None
            out = {"accepted": True, "action": payload.action.value,
                   "shield": shield_room.public_view(shield),
                   "note": "Proposed only. Founder must tap Shield before anything changes."}
            # Optional dry Enforce OSS plans (flags OFF by default). Never raise/apply.
            if enforce_oss_room.enabled():
                try:
                    out["enforce_oss"] = enforce_oss_room.receive_from_airgap(payload)
                except ThreatBurned:
                    pass
            return out
        # open_match / write_brief: acknowledge schema pass; no auto-execute in this spike.
        return {"accepted": True, "action": payload.action.value,
                "severity": payload.severity.value, "site": payload.site,
                "note": "Schema passed. No execute path for this action yet."}

    @app.post("/ghost/wraith/enforce", dependencies=[Depends(require_tank)])
    async def wraith_enforce(request: Request):
        """Autumbra Enforce stub: ThreatPayload-only rail → would_enforce dry JSON.

        Disabled unless GHOST_WRAITH_ENABLED=1 (default OFF → 404). Never changes
        the edge and never auto-patches. Keep using Tank; no Dual-LLM bunker here.
        Stronger-than-stock: rails.ensure_threat_payload_before_enforce burns
        anything that is not ThreatPayload before Enforce sees it.
        """
        if not wraith_room.enabled():
            raise HTTPException(404, "Not found.")
        raw = await bounded_json(request, 8_000)
        try:
            guard_inbound_if_enabled(raw)
            payload = airgap_rails.ensure_threat_payload_before_enforce(raw)
        except GuardBurned:
            raise HTTPException(422, GUARD_BURN_DETAIL) from None
        except ThreatBurned:
            raise HTTPException(422, BURN_DETAIL) from None
        return wraith_room.would_enforce(payload)

    @app.get("/ghost/enforce/oss", dependencies=[Depends(require_tank)])
    async def enforce_oss_status():
        """Autumbra Enforce OSS adapter status (Ansible + Terraform stubs). Always readable.

        Does not enable weapons. Live edge changes still require founder Shield tap.
        """
        return enforce_oss_room.status()

    @app.post("/ghost/enforce/oss/plan", dependencies=[Depends(require_tank)])
    async def enforce_oss_plan(request: Request):
        """Watch air-gap → Autumbra Enforce OSS dry plans (Ansible + Terraform).

        Disabled unless ENFORCE_OSS_ENABLED=1 (default OFF → 404). Never applies
        playbooks/modules, never calls HF, never changes the edge. Founder Shield
        tap remains the live path.
        """
        if not enforce_oss_room.enabled():
            raise HTTPException(404, "Not found.")
        raw = await bounded_json(request, 8_000)
        try:
            return enforce_oss_room.receive_from_airgap(raw)
        except ThreatBurned:
            raise HTTPException(422, BURN_DETAIL) from None

    @app.post("/ghost/shields/propose", dependencies=[Depends(require_tank)])
    async def shields_propose(request: Request):
        try:
            body = shield_room.ShieldRequest.model_validate_json(await bounded_json(request, 10_000))
        except ValueError:
            raise HTTPException(422, "Invalid Autumbra Watch request.") from None
        async with app.state.advisory_lock:
            try:
                return shield_room.public_view(await app.state.shields.propose(body))
            except ValueError:
                raise HTTPException(422, "Autumbra Watch only shields our own sites.") from None

    @app.post("/ghost/shields/{shield_id}/raise", dependencies=[Depends(require_tank)])
    async def shields_raise(shield_id: UUID):
        async with app.state.advisory_lock:
            try:
                return shield_room.public_view(await app.state.shields.raise_shield(str(shield_id)))
            except LookupError:
                raise HTTPException(404, "not found") from None

    @app.post("/ghost/shields/{shield_id}/undo", dependencies=[Depends(require_tank)])
    async def shields_undo(shield_id: UUID):
        async with app.state.advisory_lock:
            try:
                return shield_room.public_view(await app.state.shields.undo(str(shield_id)))
            except LookupError:
                raise HTTPException(404, "not found") from None


    # ---------- Autumbra Watch OSS intake (Vector / Fluent Bit / Steampipe / OpenCTI) ----------

    @app.get("/ghost/watch/oss", dependencies=[Depends(require_tank)])
    async def watch_oss_status():
        """Adapter status. Autumbra Watch copy; does not start upstream daemons."""
        return watch_oss_room.status()

    @app.post("/ghost/watch/intake", dependencies=[Depends(require_tank)])
    async def watch_oss_intake(request: Request):
        """Anomaly-only intake → match → ThreatPayload → Shields.propose.

        Flag OFF by default. Raw log bodies rejected by AnomalyEvent (extra=forbid).
        Signals that map to a shield kind (waf/multipart/…) open a synthetic match
        and propose for the founder panel (practice mode when no CF token). Other
        anomalies acknowledge as open_match only. Never raises a shield / Enforce.
        Air Gap #286 guard stays optional and OFF — ThreatPayload burn unchanged.
        """
        if not watch_oss_room.enabled():
            raise HTTPException(404, "Not found.")
        raw = await bounded_json(request, 8_000)
        try:
            # #286 Dual-LLM guard: no-op unless GHOST_GUARDRAILS_ENABLED / AUTUMBRA_GUARDRAILS_ENABLED.
            guard_inbound_if_enabled(raw)
            data = json.loads(raw)
            events = data if isinstance(data, list) else [data]
            accepted = []
            async with app.state.advisory_lock:
                for item in events[:50]:
                    try:
                        event = AnomalyEvent.model_validate(item)
                        # Prefer propose path when signal maps to a shield kind.
                        try:
                            result = await propose_from_anomaly(
                                app.state.shields, app.state.store, PLATFORM_TENANT, event
                            )
                            accepted.append({
                                "action": result["action"],
                                "site": result["site"],
                                "severity": result["severity"],
                                "match_id": result["match_id"],
                                "kind": result["kind"],
                                "shield": shield_room.public_view(result["shield"]),
                                "note": result["note"],
                            })
                            continue
                        except ThreatBurned:
                            payload = handoff_threat(event)
                    except (ThreatBurned, ValueError, TypeError):
                        continue
                    accepted.append({
                        "action": payload.action.value,
                        "site": payload.site,
                        "severity": payload.severity.value,
                        "match_id": payload.match_id,
                    })
            return {
                "accepted": len(accepted),
                "events": accepted,
                "product": "Autumbra Watch",
                "note": (
                    "Anomalies may propose shields for the founder panel. "
                    "Practice mode changes nothing. Founder Shield tap required to raise. "
                    "No Enforce path from this intake."
                ),
            }
        except GuardBurned:
            raise HTTPException(422, GUARD_BURN_DETAIL) from None
        except (ThreatBurned, json.JSONDecodeError, TypeError, ValueError):
            raise HTTPException(422, BURN_DETAIL) from None

    @app.post("/ghost/patch/accept", dependencies=[Depends(require_tank)])
    async def patch_accept(request: Request):
        """Autumbra patch-as-PR: validate ghost.patch.v1 brief, open draft dep-bump PR or dry-run.

        Flag GHOST_PATCH_PR_ENABLED/PATCH_PR_ENABLED OFF by default. Never merges. Never deploys.
        """
        raw = await bounded_json(request, 10_000)
        try:
            brief = accept_patch_brief(raw)
        except PatchBurned:
            raise HTTPException(422, PATCH_BURN_DETAIL) from None
        if not passes_severity_bar(brief):
            raise HTTPException(422, PATCH_BURN_DETAIL)
        result = open_dep_bump_pr(brief)
        return {
            "accepted": True,
            "action": brief.action,
            "schema_version": brief.schema_version,
            **result,
        }

    @app.get("/ghost/autumbra/blocks", dependencies=[Depends(require_tank)])
    async def standing_blocks(tenant_id: UUID):
        return await app.state.shared_blocks.items(str(tenant_id))

    @app.get("/ghost/patch/fixes", dependencies=[Depends(require_tank)])
    async def patch_fixes():
        from .fix_approval import fixes
        return fixes(app.state.store, PLATFORM_TENANT)

    @app.post("/ghost/patch/from-match", dependencies=[Depends(require_tank)])
    async def patch_from_match(request: Request):
        """CONNECT: advisory match fixture → PatchBrief → draft/dry-run plan. Never merges."""
        raw = await bounded_json(request, 20_000)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            raise HTTPException(422, PATCH_BURN_DETAIL) from None
        if not isinstance(data, dict):
            raise HTTPException(422, PATCH_BURN_DETAIL)
        try:
            from .fix_approval import stored_match, brief_for
            if set(data) == {"match_id"}:
                data = stored_match(app.state.store, PLATFORM_TENANT, str(data["match_id"]))
            brief_for(data)
            result = connect_match_to_plan(data)
            pr_url = (result.get("result") or {}).get("pr_url")
            brief = result["brief"]
            return {**result, "name": brief["package"], "status": result["result"]["mode"],
                    "summary": brief["from_version"] + " → " + brief["to_version"],
                    **({"pr_url": pr_url} if pr_url else {})}
        except PatchBurned:
            raise HTTPException(422, PATCH_BURN_DETAIL) from None

    @app.post("/ghost/patch/from-threat", dependencies=[Depends(require_tank)])
    async def patch_from_threat(request: Request):
        """CONNECT: ThreatPayload-shaped facts → PatchBrief → draft/dry-run. Never merges."""
        raw = await bounded_json(request, 12_000)
        try:
            brief = threat_to_patch_brief(raw)
        except PatchBurned:
            raise HTTPException(422, PATCH_BURN_DETAIL) from None
        if not passes_severity_bar(brief):
            raise HTTPException(422, PATCH_BURN_DETAIL)
        result = open_dep_bump_pr(brief)
        return {
            "connected": True,
            "action": brief.action,
            "schema_version": brief.schema_version,
            "brief": brief.model_dump(mode="json"),
            **result,
        }


    # ---------- Autumbra product surfaces (settings, intel consume, webhooks, lifecycle) ----------

    @app.get("/ghost/autumbra/settings", dependencies=[Depends(require_tank)])
    async def autumbra_settings_get(tenant_id: str = Query(default=PLATFORM_TENANT)):
        settings = load_settings(app.state.store, tenant_id)
        halves = active_halves(settings)
        return {
            "settings": settings.model_dump(),
            "halves": halves,
            "hands": list_hands(settings),
            "copy": connect_copy(settings),
        }

    @app.post("/ghost/autumbra/settings", dependencies=[Depends(require_tank)])
    async def autumbra_settings_set(request: Request):
        raw = await bounded_json(request, 8_000)
        try:
            data = json.loads(raw.decode("utf-8"))
            settings = ClientAutumbraSettings.model_validate(data)
        except Exception:
            raise HTTPException(422, "Settings were not understood.") from None
        try:
            saved = save_settings(app.state.store, settings)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        # Mirror into that client's queue box so Enforce reloads and obeys the mode.
        mirrored = False
        try:
            qpath = queue_path_for_tenant(saved.tenant_id)
            mirror_settings(saved, qpath)
            mirrored = True
        except Exception:
            # The legacy single queue belongs only to its configured tenant.
            try:
                if watch_queue_configured() and os.environ.get("TENANT_ID") == saved.tenant_id:
                    mirror_settings(saved, queue_root_from_env())
                    mirrored = True
            except Exception:
                mirrored = False
        receipt = {
            **app.state.store.record_run(saved.tenant_id, "settings_change", "saved"),
            "product": "Autumbra",
            "kind": "settings_change",
            "tenant_id": saved.tenant_id,
            "shield_mode": saved.shield_mode.value,
            "fix_mode": saved.fix_mode.value,
            "mirrored_to_enforce_box": mirrored,
            "undo": {"restore_defaults": True},
        }
        return {
            "settings": saved.model_dump(),
            "halves": active_halves(saved),
            "hands": list_hands(saved),
            "copy": connect_copy(saved),
            "receipt": receipt,
        }

    @app.get("/ghost/autumbra/hands", dependencies=[Depends(require_tank)])
    async def autumbra_hands_get(tenant_id: str = Query(default=PLATFORM_TENANT)):
        """Agnostic optional connects. No assumed provider. No Pro."""
        settings = load_settings(app.state.store, tenant_id)
        return {
            "hands": list_hands(settings),
            "halves": active_halves(settings),
            "copy": connect_copy(settings),
            "note": "Optional hands only. Stack/advisories/receipts work with zero connects.",
        }

    @app.post("/ghost/autumbra/hands", dependencies=[Depends(require_tank)])
    async def autumbra_hands_set(request: Request):
        """Connect or disconnect one optional hand. Client modes stay the only clamp."""
        raw = await bounded_json(request, 4_000)
        try:
            data = json.loads(raw.decode("utf-8"))
            tenant_id = str(data.get("tenant_id") or PLATFORM_TENANT)
            hand = str(data["hand"])
            connected = data["connected"]
            if type(connected) is not bool:
                raise ValueError("Invalid connection state")
        except Exception:
            raise HTTPException(422, "Hand change was not understood.") from None
        settings = load_settings(app.state.store, tenant_id)
        try:
            updated = set_hand(settings, hand, connected=connected)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        saved = save_settings(app.state.store, updated)
        return {
            "receipt": app.state.store.record_run(saved.tenant_id, "connection_change", "saved"),
            "settings": saved.model_dump(),
            "hands": list_hands(saved),
            "halves": active_halves(saved),
            "copy": connect_copy(saved),
        }

    @app.get("/ghost/autumbra/sources", dependencies=[Depends(require_tank)])
    async def autumbra_sources(tenant_id: str = Query(default=PLATFORM_TENANT)):
        # Clients never see a feed list. Intel backend is Market Lab.
        _ = tenant_id
        return {"sources": [], "client_visible_feed_list": False, **intel_backend_status()}

    @app.post("/ghost/autumbra/intel/consume", dependencies=[Depends(require_tank)])
    async def autumbra_intel_consume(request: Request):
        """Consume a Market Lab IOC bundle. Name sources on the receipt. No Autumbra registry."""
        raw = await bounded_json(request, 64_000)
        try:
            data = json.loads(raw.decode("utf-8"))
            tenant_id = str(data.get("tenant_id") or PLATFORM_TENANT)
            bundle_raw = data.get("bundle") if isinstance(data.get("bundle"), dict) else data
        except Exception:
            raise HTTPException(422, "Intel bundle was not understood.") from None
        try:
            bundle = accept_ioc_bundle(bundle_raw)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        register_advisories_from_ioc_bundle(bundle)
        receipt = receipt_for_bundle_consume(bundle, tenant_id=tenant_id)
        return {
            "accepted": True,
            "bundle_id": bundle["bundle_id"],
            "indicator_count": len(bundle.get("indicators") or []),
            "receipt": receipt,
            "client_visible_feed_list": False,
        }

    from .provider_intake import register as register_provider_intake
    register_provider_intake(app, require_tank, bounded_json, Tank)

    @app.post("/ghost/autumbra/webhooks/{provider}")
    async def autumbra_webhook(provider: str, request: Request):
        """Signature check FIRST — before any Tank auth that would 404.

        HMAC-authenticated webhook → never-block check → client's queue.
        Native provider authentication records metadata only, never a tenant action.
        Replay refused; raw bodies are never stored.
        """
        raw = await bounded_json(request, 64_000)
        try:
            envelope = await accept_webhook(
                provider, raw, headers=request.headers,
                tank=Tank(getattr(app.state, "http", None)),
            )
        except WebhookRejected:
            return JSONResponse(
                {"accepted": False, "stored": False, "drops": drop_counts()},
                status_code=401,
            )
        if envelope.get("authenticated_by") == "hmac":
            await require_tank(request.headers.get("x-tank-key", ""))
        if envelope.get("authenticated_by") != "hmac":
            # Provider authentication does not establish any client's authority.
            # Retain only the latest verified event metadata per native provider.
            path = Path(os.environ.get("DATA_DIR", "/data")) / "verified-webhook-events.json"
            with locked_state(path) as events:
                events[envelope["requested_provider"]] = {
                    "provider": envelope["requested_provider"], "bytes": envelope["bytes"],
                    "body_sha256": hashlib.sha256(raw).hexdigest(),
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                }
            return {"accepted": True, "stored": True, "provider": envelope["provider"],
                    "bytes": envelope["bytes"], "queued": False, "queue_receipt": None,
                    "action": "accepted_no_action"}
        # Parse working-memory JSON only after HMAC OK. Never persist raw body.
        queued = False
        queue_receipt = None
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            address = str(data.get("address") or data.get("ip") or "").strip()
            site = str(data.get("site") or data.get("host") or "").strip()
            tenant_id = str(data.get("tenant_id") or "").strip()
            raw_log = str(data.get("raw_log") or data.get("message") or f"webhook {provider}")
            if address and site and tenant_id and watch_queue_configured():
                try:
                    tenant_id = _require_tenant_uuid(tenant_id)
                except ValueError:
                    return JSONResponse(
                        {"accepted": False, "stored": False, "detail": "tenant_id must be a UUID"},
                        status_code=400,
                    )
                settings = load_settings(app.state.store, tenant_id)
                ok, why = may_block_ip(address, raw_log=raw_log, client_own_cidrs=settings.own_cidrs)
                if ok:
                    advisory = data.get("advisory") if isinstance(data.get("advisory"), dict) else None
                    source_data = {}
                    blocks = getattr(app.state, "shared_blocks", None)
                    if blocks is not None:
                        try:
                            count = await blocks.count(tenant_id, address)
                            if count:
                                source_data = {"autumbra.shared_blocks": count}
                        except Exception:
                            _block_memory_unavailable(app)
                    try:
                        queue_receipt = enqueue_fixture_attack_for_watch(
                            raw_log=raw_log,
                            site=site,
                            address=address,
                            reason=str(data.get("reason") or f"signed {provider} webhook")[:300],
                            tenant_id=tenant_id,
                            advisory=advisory,
                            source_data=source_data,
                        )
                    except ValueError as exc:
                        # Fixed copy only — never echo exception/stack to the caller (CodeQL).
                        detail = BURN_DETAIL if isinstance(exc, ThreatBurned) else "invalid threat payload"
                        return JSONResponse(
                            {"accepted": False, "stored": False, "detail": detail},
                            status_code=400,
                        )
                    queued = bool(queue_receipt.get("queued"))
                else:
                    queue_receipt = {"queued": False, "note": why, "outcome": "refused_never_block"}
        return {
            "accepted": True,
            "stored": False,
            "provider": envelope["provider"],
            "bytes": envelope["bytes"],
            "queued": queued,
            "queue_receipt": queue_receipt,
            "action": "enqueue_threat_payload" if queued else "accepted_no_action",
        }

    @app.get("/ghost/autumbra/exercises", dependencies=[Depends(require_tank)])
    async def autumbra_exercises():
        return {"exercises": list_exercises(), "note": "Tervory's own sites only; client needs written permission."}

    @app.post("/ghost/autumbra/exercises/run", dependencies=[Depends(require_tank)])
    async def autumbra_exercise_run(request: Request):
        raw = await bounded_json(request, 8_000)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise HTTPException(422, "Exercise was not understood.") from None
        tenant_id = str(data.get("tenant_id") or PLATFORM_TENANT)
        settings = load_settings(app.state.store, tenant_id)
        try:
            receipt = run_exercise(
                str(data.get("exercise_id") or ""),
                target_site=str(data.get("target_site") or ""),
                settings=settings,
                fixture_log=str(data.get("fixture_log") or ""),
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        app.state.store.set_state(tenant_id, "exercise_last", receipt)
        return receipt


    @app.post("/ghost/autumbra/queue/fixture-attack", dependencies=[Depends(require_tank)])
    async def autumbra_queue_fixture_attack(request: Request):
        """Watch product door: dirty log → ThreatPayload → that client's queue.

        Does not call Enforce. Burned/unknown-site input → clean 4xx + burn receipt.
        """
        if not watch_queue_configured() and getattr(app.state, "threat_queue", None) is None:
            raise HTTPException(503, "THREAT_PAYLOAD_QUEUE is not configured.")
        raw = await bounded_json(request, 32_000)
        try:
            data = json.loads(raw.decode("utf-8"))
            raw_log = str(data["raw_log"])
            site = str(data["site"])
            address = str(data["address"])
            reason = str(data.get("reason") or "fixture attacking address")
            tenant_id = str(data.get("tenant_id") or "").strip() or None
            advisory = data.get("advisory") if isinstance(data.get("advisory"), dict) else None
        except Exception:
            raise HTTPException(422, "Fixture attack was not understood.") from None
        if tenant_id:
            try:
                tenant_id = _require_tenant_uuid(tenant_id)
            except ValueError:
                raise HTTPException(400, "tenant_id must be a UUID") from None
            try:
                queue = watch_threat_queue(tenant_id)
            except ValueError:
                raise HTTPException(400, "tenant_id must be a UUID") from None
            except Exception:
                raise HTTPException(422, "Invalid Autumbra Watch request.") from None
        else:
            queue = getattr(app.state, "threat_queue", None)
        try:
            receipt = enqueue_fixture_attack_for_watch(
                raw_log=raw_log,
                site=site,
                address=address,
                reason=reason,
                tenant_id=tenant_id,
                advisory=advisory,
                queue=queue,
            )
        except ThreatBurned:
            burn_receipt = {
                "product": "Autumbra",
                "role": "Watch",
                "kind": "burn",
                "outcome": "burned",
                "queued": False,
                "detail": BURN_DETAIL,
                "tenant_id": tenant_id,
            }
            return JSONResponse(burn_receipt, status_code=400)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return receipt

    @app.post("/ghost/autumbra/queue/undo", dependencies=[Depends(require_tank)])
    async def autumbra_queue_undo(request: Request):
        """Client undo: validated ThreatPayload (undo_shield) into that client's queue."""
        if not watch_queue_configured():
            raise HTTPException(503, "THREAT_PAYLOAD_QUEUE is not configured.")
        raw = await bounded_json(request, 8_000)
        try:
            data = json.loads(raw.decode("utf-8"))
            tenant_id = str(data["tenant_id"])
            site = str(data["site"])
            rule_id = data.get("rule_id")
            prior_receipt_id = data.get("prior_receipt_id")
        except Exception:
            raise HTTPException(422, "Undo was not understood.") from None
        try:
            from .client_settings import load_settings as _load_tz, owner_time_zone

            tenant_id = _require_tenant_uuid(tenant_id)
            q = watch_threat_queue(tenant_id)
            tz = owner_time_zone(_load_tz(app.state.store, tenant_id))
            result = watch_enqueue_undo(
                queue=q,
                rule_id=str(rule_id) if rule_id else None,
                prior_receipt_id=str(prior_receipt_id) if prior_receipt_id else None,
                site=site,
                tenant_id=tenant_id,
                time_zone=tz,
            )
            blocks = getattr(app.state, "shared_blocks", None)
            if blocks is not None:
                try:
                    await blocks.reconcile(tenant_id)
                except Exception:
                    _block_memory_unavailable(app)
            return result
        except ThreatBurned:
            return JSONResponse(
                {"outcome": "burned", "detail": BURN_DETAIL, "queued": False},
                status_code=400,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except Exception:
            raise HTTPException(422, "Invalid Autumbra Watch request.") from None

    @app.get("/ghost/autumbra/undo/status", dependencies=[Depends(require_tank)])
    async def autumbra_undo_status(
        tenant_id: str = Query(default=""),
        prior_receipt_id: str = Query(default=""),
    ):
        """Whether this Undo link was already used (for the owner page)."""
        from .client_settings import load_settings as _load_tz, owner_time_zone
        from .threat_queue import already_undone_message, read_undo_used

        try:
            tid = _require_tenant_uuid(tenant_id)
        except ValueError:
            raise HTTPException(400, "tenant_id must be a UUID") from None
        rid = (prior_receipt_id or "").strip()
        if not rid:
            raise HTTPException(400, "prior_receipt_id is required") from None
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", rid):
            raise HTTPException(400, "Invalid receipt identifier.")
        if not watch_queue_configured():
            raise HTTPException(503, "THREAT_PAYLOAD_QUEUE is not configured.")
        q = watch_threat_queue(tid)
        used = read_undo_used(q, rid)
        if not used:
            return {"already_undone": False, "queued": False, "tenant_id": tid, "prior_receipt_id": rid}
        undone_at = str(used.get("undone_at") or "")
        tz = owner_time_zone(_load_tz(app.state.store, tid))
        return {
            "already_undone": True,
            "queued": False,
            "undone_at": undone_at,
            "detail": already_undone_message(undone_at, time_zone=tz),
            "time_zone": tz,
            "tenant_id": tid,
            "prior_receipt_id": rid,
        }

    @app.post("/ghost/autumbra/queue/approve", dependencies=[Depends(require_tank)])
    async def autumbra_queue_approve(request: Request):
        """Approve a pending shield via ThreatPayload into that client's queue."""
        if not watch_queue_configured():
            raise HTTPException(503, "THREAT_PAYLOAD_QUEUE is not configured.")
        raw = await bounded_json(request, 8_000)
        try:
            data = json.loads(raw.decode("utf-8"))
            tenant_id = str(data["tenant_id"])
            site = str(data["site"])
            prior_receipt_id = str(data["prior_receipt_id"])
        except Exception:
            raise HTTPException(422, "Approve was not understood.") from None
        try:
            tenant_id = _require_tenant_uuid(tenant_id)
            q = watch_threat_queue(tenant_id)
            return watch_enqueue_approve(
                queue=q,
                prior_receipt_id=prior_receipt_id,
                site=site,
                tenant_id=tenant_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except ThreatBurned:
            return JSONResponse(
                {"outcome": "burned", "detail": BURN_DETAIL, "queued": False},
                status_code=400,
            )

    @app.get("/ghost/autumbra/receipts", dependencies=[Depends(require_tank)])
    async def autumbra_receipts(tenant_id: str = Query(...)):
        """Founder console path: receipts for one client (timestamps + tenant_id)."""
        try:
            q = watch_threat_queue(tenant_id)
        except Exception:
            raise HTTPException(404, "Not found.") from None
        disk = q.list_receipts()
        console = list_founder_console_receipts(q.root)
        # Prefer console view; fall back to disk receipts.
        by_id = {str(r.get("id")): r for r in disk if r.get("id")}
        for r in console:
            by_id[str(r.get("id"))] = r
        return {
            "tenant_id": tenant_id,
            "receipts": list(by_id.values()),
            "count": len(by_id),
            "founder_console": True,
        }

    @app.get("/ghost/autumbra/receipts/weekly/{tenant_id}")
    async def autumbra_weekly_receipt_page(
        tenant_id: str,
        token: str = Query(default=""),
        x_tank_key: str = Header(default=""),
    ):
        """Weekly receipts — tank key OR valid receipts token for THIS tenant. Else 401 empty."""
        from .owner_email_jobs import default_undo_url, week_bounds, _parse_receipt_time

        try:
            tid = _require_tenant_uuid(tenant_id)
        except ValueError:
            raise HTTPException(400, "tenant_id must be a UUID") from None

        expected = os.environ.get("TANK_KEY", "")
        tank_ok = bool(expected) and hmac.compare_digest(
            expected.encode(), (x_tank_key or "").encode()
        )
        token_ok = False
        raw_token = (token or "").strip()
        if raw_token:
            try:
                payload = verify_receipts_token(raw_token)
                token_ok = payload.get("tenant_id") == tid
            except Exception:
                token_ok = False
        if not tank_ok and not token_ok:
            raise HTTPException(status_code=401, detail="Unauthorized")

        week_start, week_end = week_bounds()
        rows = []
        for receipt in _receipts_for_tenant(tid):
            if receipt.get("tenant_id", tid) != tid:
                continue
            moment = _parse_receipt_time(receipt)
            if moment is None or moment < week_start or moment > week_end:
                continue
            from .savings import code_cost
            code_cost(receipt)
            rid = str(receipt.get("id") or "")
            rule_id = str((receipt.get("undo") or {}).get("rule_id") or "")
            site = str(receipt.get("site") or "")
            undo_url = ""
            if rid and receipt.get("can_undo", True):
                undo_url = default_undo_url(
                    rid, tenant_id=tid, rule_id=rule_id, site=site
                )
            rows.append({
                "id": rid,
                "timestamp": receipt.get("timestamp") or receipt.get("at"),
                "outcome": receipt.get("outcome"),
                "severity": receipt.get("severity"),
                "advisory_url": receipt.get("advisory_url"),
                "site": receipt.get("site"),
                "address": receipt.get("address"),
                "cost_usd": receipt.get("cost_usd"),
                "would_have_cost_usd": receipt.get("would_have_cost_usd"),
                "baseline_status": receipt.get("baseline_status"),
                "url": undo_url if (undo_url and "token=" in undo_url) else "",
                "receipt": receipt,
            })
        from .client_settings import load_settings as _load_owner_settings, owner_time_zone

        owner_settings = _load_owner_settings(app.state.store, tid)
        return {
            "product": "Autumbra",
            "kind": "weekly_receipts",
            "tenant_id": tid,
            "page": "exists",
            "week_start": week_start.isoformat().replace("+00:00", "Z"),
            "week_end": week_end.isoformat().replace("+00:00", "Z"),
            "time_zone": owner_time_zone(owner_settings),
            "receipts": rows,
            "count": len(rows),
            "note": "Weekly owner-update receipt page.",
        }

    from .enforce_api import register as register_enforce
    register_enforce(app, require_tank, bounded_json)

    @app.post("/ghost/autumbra/owner-update", dependencies=[Depends(require_tank)])
    async def autumbra_owner_update(request: Request):
        """Build the owner email from receipts in code and call the sender. No model.

        Recipient is ONLY the owner_email stored in the client's settings.
        A request body can never name or override the recipient.
        Every count is built from receipts — never from the request body.
        """
        raw = await bounded_json(request, 20_000)
        try:
            data = json.loads(raw.decode("utf-8"))
            tenant_id = str(
                data.get("tenant_id")
                or (data.get("settings") or {}).get("tenant_id")
                or ""
            ).strip()
            if not tenant_id:
                raise ValueError("tenant_id required")
            tenant_id = _require_tenant_uuid(tenant_id)
            # Stored settings only — ignore any owner_email in the request body.
            settings = load_settings(app.state.store, tenant_id)
            to = str(settings.owner_email or "").strip()
            if not to:
                raise ValueError("recipient must come from the client's settings")
            if "owner_email" in (data.get("settings") or {}):
                requested = str((data.get("settings") or {}).get("owner_email") or "").strip()
                if requested and requested != to:
                    raise ValueError("request cannot name the recipient")
            # Counts from receipts only. Request body numbers are ignored.
            receipts = _receipts_for_tenant(tenant_id)
            email = build_owner_update_from_receipts(
                settings=settings,
                receipts=receipts,
                receipt_url=default_receipt_url(tenant_id),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except Exception:
            raise HTTPException(422, "Owner update was not understood.") from None
        from .owner_email_jobs import week_bounds
        ident = hashlib.sha256(json.dumps(sorted(str(r.get("id") or "") for r in receipts)).encode()).hexdigest()
        sent = await asyncio.to_thread(send_owner_update, email, to=to, tenant_id=tenant_id,
                                       message_key="receipts:" + week_bounds()[0].isoformat() + ":" + ident)
        return {**email, **sent}

    @app.get("/ghost/autumbra/jobs", dependencies=[Depends(require_tank)])
    async def autumbra_jobs():
        """Scheduled Autumbra jobs (weekly owner update is a real entry)."""
        from .owner_email_jobs import list_jobs
        return {"jobs": list_jobs()}

    @app.get("/ghost/summary", dependencies=[Depends(require_tank)])
    async def ghost_summary():
        return {**app.state.shields.summary(), "last_cycle": app.state.store.get_state(PLATFORM_TENANT, "last_cycle")}

    return app


app = create_app()
