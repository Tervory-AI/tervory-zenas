"""Separately runnable Autumbra Enforce process.

Own entrypoint — not ghost.app. Starts without a Tank key. Does not parse raw
logs: HTTP apply/undo never read request bodies. Production path drains
ThreatPayloads from THREAT_PAYLOAD_QUEUE (background loop + drain_once_for_enforce).
Undo and approve travel as validated ThreatPayloads through that queue door.
Shields are held durably in this client's DATA_DIR box and survive restart.
Client shield mode is loaded from the settings mirror Watch writes into the
queue box (not a private store nothing else writes).
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .client_settings import ClientAutumbraSettings, load_settings
from .durable_shields import (
    corrupt_shields_receipt,
    load_control,
    persist_control,
    read_shields_table,
    restart_drop_receipts,
)
from .enforce_apply import expire_pending_approvals, select_control
from .fixture_edge import DEFAULT_CONTROL, FixtureEdgeControl
from .never_block import ActionCap
from .store import Store
from .tenant_queue import load_mirrored_settings
from .threat_payload import ThreatPayload
from .threat_queue import ThreatPayloadQueue, enforce_drain_once, queue_root_from_env


def _content_length(request: Request) -> int:
    raw = request.headers.get("content-length")
    if raw is None or raw == "":
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return -1


def _refuse_raw_http() -> JSONResponse:
    return JSONResponse(
        {
            "accepted": False,
            "raw_log_seen": True,
            "detail": (
                "Enforce does not parse request bodies. "
                "Hand a ThreatPayload via the queue door "
                "(propose_shield / undo_shield / approve_shield). "
                "Production path drains THREAT_PAYLOAD_QUEUE via "
                "drain_once_for_enforce()."
            ),
        },
        status_code=415,
    )


def data_dir_path() -> Path:
    return Path(os.environ.get("DATA_DIR") or "/data")


def load_enforce_client_settings(
    *,
    queue: ThreatPayloadQueue | None = None,
) -> ClientAutumbraSettings:
    """Load this Enforce room's client modes — prefer Watch's settings mirror."""
    tenant_id = (os.environ.get("TENANT_ID") or "").strip() or (
        "00000000-0000-0000-0000-0000000000ef"
    )
    site_name = (os.environ.get("SITE_NAME") or "").strip() or "coolify.tervory.com"
    # 1) Mirror written by Watch into this client's queue box.
    qroot = None
    if queue is not None:
        qroot = queue.root
    elif queue_configured():
        try:
            qroot = queue_root_from_env()
        except RuntimeError:
            qroot = None
    if qroot is not None:
        mirrored = load_mirrored_settings(qroot, fallback_tenant_id=tenant_id)
        if mirrored is not None:
            if not mirrored.site_name and site_name:
                mirrored = ClientAutumbraSettings.model_validate(
                    {**mirrored.model_dump(), "site_name": site_name}
                )
            return mirrored
    # 2) Legacy local store (compat only).
    data_dir = data_dir_path()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        store = Store(data_dir / "autumbra_enforce.sqlite3")
        settings = load_settings(store, tenant_id)
    except Exception:
        settings = ClientAutumbraSettings(tenant_id=tenant_id, site_name=site_name)
    if not settings.site_name and site_name:
        settings = ClientAutumbraSettings.model_validate(
            {**settings.model_dump(), "site_name": site_name}
        )
    try:
        from .threat_payload import bind_settings_store
        bind_settings_store(store)
    except Exception:
        pass
    return settings


def queue_configured() -> bool:
    return bool((os.environ.get("THREAT_PAYLOAD_QUEUE") or "").strip())


def enforce_threat_queue() -> ThreatPayloadQueue:
    return ThreatPayloadQueue(queue_root_from_env())


def _boot_durable_control(app: FastAPI) -> list[dict[str, Any]]:
    """Restore shields from this client's box. Never silently drop on corrupt file."""
    tenant_id = (os.environ.get("TENANT_ID") or "").strip() or "fixture"
    data_dir = data_dir_path()
    data_dir.mkdir(parents=True, exist_ok=True)
    table, source = read_shields_table(data_dir)
    prior_blocked: dict[str, dict[str, Any]] = {}
    if isinstance(table, dict) and isinstance(table.get("blocked"), dict):
        prior_blocked = {
            str(k): dict(v)
            for k, v in table["blocked"].items()
            if isinstance(v, dict)
        }
    receipts: list[dict[str, Any]] = []
    if source == "corrupt":
        # No usable table at all — prior_blocked empty; still write a receipt.
        receipts.append(
            corrupt_shields_receipt(
                tenant_id=tenant_id,
                prior_blocked={},
                restored_from_good=False,
            )
        )
    elif source == "good":
        # Primary was bad; we kept last good. Receipt names the event.
        receipts.append(
            corrupt_shields_receipt(
                tenant_id=tenant_id,
                prior_blocked=prior_blocked,
                restored_from_good=True,
            )
        )
    control = load_control(data_dir, tenant_id=tenant_id)
    cfg = load_enforce_client_settings()
    site = control.site or cfg.site_name
    control = select_control(site=site, settings=cfg, control=control)
    restored = {r["rule_id"]: r for r in control.list_blocked()}
    # If somehow restored is smaller than the table we read, emit per-shield drops.
    receipts.extend(
        restart_drop_receipts(
            prior_blocked=prior_blocked,
            restored_blocked=restored,
            tenant_id=tenant_id,
        )
    )
    # Re-persist (atomic) so primary + last-good stay aligned with restored table.
    persist_control(data_dir, control)
    app.state.control = control
    app.state.data_dir = data_dir
    return receipts


def drain_once_for_enforce(
    *,
    app: FastAPI | None = None,
    queue: ThreatPayloadQueue | None = None,
    settings: ClientAutumbraSettings | None = None,
    control=None,
    action_cap: ActionCap | None = None,
    source_credits: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Production Enforce drain entrypoint used by the process drain loop."""
    target = app
    if target is None:
        target = globals().get("app")
    q = queue
    if q is None:
        if target is not None and getattr(target.state, "threat_queue", None) is not None:
            q = target.state.threat_queue
        else:
            q = enforce_threat_queue()
    # Prefer Watch settings mirror when present; else the process's current settings.
    cfg = settings
    if cfg is None:
        mirrored = None
        if q is not None:
            mirrored = load_mirrored_settings(
                q.root,
                fallback_tenant_id=(os.environ.get("TENANT_ID") or "").strip()
                or "00000000-0000-0000-0000-0000000000ef",
            )
        if mirrored is not None:
            cfg = mirrored
        elif target is not None and getattr(target.state, "settings", None) is not None:
            cfg = target.state.settings
        else:
            cfg = load_enforce_client_settings(queue=q)
        if target is not None:
            target.state.settings = cfg
    edge = control
    if edge is None and target is not None:
        edge = getattr(target.state, "control", None)
    if edge is None:
        edge = DEFAULT_CONTROL
    # Empty queue polls must not consume the approved proxy's tunnel budget.
    # A fixture can be promoted once work arrives after the proxy started.
    if getattr(edge, "control_name", "fixture_edge") != "cloudflare" and any((q.root / "inbox").glob("*.json")):
        edge = select_control(site=edge.site or cfg.site_name, settings=cfg, control=edge)
        if target is not None:
            target.state.control = edge
    cap = action_cap
    if cap is None and target is not None:
        cap = getattr(target.state, "action_cap", None)
    if cap is None:
        cap = ActionCap(data_dir=data_dir_path())
    data_dir = None
    if target is not None:
        data_dir = getattr(target.state, "data_dir", None)
    if data_dir is None:
        data_dir = data_dir_path()
    # Expire pending approvals that were never approved.
    for expired in expire_pending_approvals(
        data_dir=data_dir,
        settings=cfg,
        queue_write=q.write_receipt,
    ):
        _ = expired
    on_act = None
    if target is not None:
        on_act = getattr(target.state, "on_act_email", None)
    return enforce_drain_once(
        q,
        settings=cfg,
        control=edge,
        action_cap=cap,
        source_credits=source_credits,
        data_dir=data_dir,
        on_act_email=on_act,
    )


async def _queue_drain_loop(app: FastAPI) -> None:
    interval = float(os.environ.get("ENFORCE_DRAIN_INTERVAL_SEC") or "1.0")
    if interval < 0.05:
        interval = 0.05
    while True:
        await asyncio.sleep(interval)
        try:
            if getattr(app.state, "threat_queue", None) is not None:
                drain_once_for_enforce(app=app)
        except Exception:
            pass



def _default_on_act_email(receipt: dict[str, Any], settings: ClientAutumbraSettings) -> None:
    """When Autumbra acts: build owner email from this week's receipts and send to owner."""
    to = (settings.owner_email or "").strip()
    if not to:
        return
    from .owner_email import DEFAULT_FIXTURE_TRANSPORT, send_owner_update
    from .owner_email_jobs import build_owner_update_from_receipts, default_undo_url

    # Counts from the week's receipts (same source as the weekly email), not one receipt.
    receipts: list[dict[str, Any]] = [receipt]
    try:
        q = enforce_threat_queue()
        listed = q.list_receipts()
        if listed:
            by_id = {str(r.get("id")): r for r in listed if isinstance(r, dict) and r.get("id")}
            rid = str(receipt.get("id") or "")
            if rid:
                by_id[rid] = receipt
            receipts = list(by_id.values())
    except Exception:
        receipts = [receipt]

    undo = default_undo_url(
        str(receipt.get("id") or ""),
        tenant_id=settings.tenant_id,
        rule_id=str((receipt.get("undo") or {}).get("rule_id") or ""),
        site=str(receipt.get("site") or ""),
    )
    email = build_owner_update_from_receipts(
        settings=settings,
        receipts=receipts,
        # Signed undo only — never an unsigned legacy URL that overrides week_stats.
        undo_shield_url=undo if "token=" in undo else "",
    )
    send_owner_update(email, to=to, transport=DEFAULT_FIXTURE_TRANSPORT)


def create_enforce_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        drop_receipts = _boot_durable_control(app)
        app.state.action_cap = ActionCap(data_dir=data_dir_path())
        app.state.threat_queue = None
        app.state.drain_loop_running = False
        app.state.on_act_email = _default_on_act_email
        drain_task: asyncio.Task | None = None
        if queue_configured():
            app.state.threat_queue = enforce_threat_queue()
            app.state.settings = load_enforce_client_settings(queue=app.state.threat_queue)
            for receipt in drop_receipts:
                app.state.threat_queue.write_receipt(receipt)
            drain_task = asyncio.create_task(_queue_drain_loop(app))
            app.state.drain_loop_running = True
        else:
            app.state.settings = load_enforce_client_settings()
        try:
            yield
        finally:
            # Persist shields before exit.
            control = getattr(app.state, "control", None)
            data_dir = getattr(app.state, "data_dir", None)
            if isinstance(control, FixtureEdgeControl) and data_dir is not None:
                persist_control(data_dir, control)
            app.state.drain_loop_running = False
            if drain_task is not None:
                drain_task.cancel()
                await asyncio.gather(drain_task, return_exceptions=True)

    app = FastAPI(title="Autumbra Enforce", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.control = DEFAULT_CONTROL
    app.state.settings = ClientAutumbraSettings(
        tenant_id=(os.environ.get("TENANT_ID") or "").strip()
        or "00000000-0000-0000-0000-0000000000ef"
    )
    app.state.action_cap = ActionCap(data_dir=data_dir_path())
    app.state.threat_queue = None
    app.state.drain_loop_running = False
    app.state.data_dir = data_dir_path()
    app.state.on_act_email = _default_on_act_email

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "role": "enforce",
            "tank_key_configured": bool(os.environ.get("TANK_KEY", "").strip()),
            "control": getattr(app.state.control, "control_name", "fixture_edge"),
            "blocked": len(app.state.control.list_blocked()),
            "shield_mode": app.state.settings.shield_mode.value,
            "tenant_id": app.state.settings.tenant_id,
            "threat_payload_queue_configured": queue_configured(),
            "drain_loop_running": bool(getattr(app.state, "drain_loop_running", False)),
            "drain_entrypoint": "ghost.enforce_app:drain_once_for_enforce",
            "durable_shields": True,
        }

    @app.post("/v1/apply")
    async def apply_http(request: Request) -> JSONResponse:
        if _content_length(request) != 0:
            return _refuse_raw_http()
        raise HTTPException(415, "Enforce does not parse request bodies.")

    @app.post("/v1/undo")
    async def undo_http(request: Request) -> JSONResponse:
        """Undo is via ThreatPayload on the queue — never raw HTTP body parse."""
        if _content_length(request) != 0:
            return _refuse_raw_http()
        raise HTTPException(
            415,
            "Undo via ThreatPayload (action=undo_shield) on THREAT_PAYLOAD_QUEUE.",
        )

    @app.post("/v1/drain-once")
    async def drain_once_http(request: Request) -> JSONResponse:
        if _content_length(request) != 0:
            return _refuse_raw_http()
        if not queue_configured() and getattr(app.state, "threat_queue", None) is None:
            raise HTTPException(503, "THREAT_PAYLOAD_QUEUE is not configured.")
        receipt = drain_once_for_enforce(app=app)
        if receipt is None:
            return JSONResponse({"drained": False, "receipt": None})
        return JSONResponse({"drained": True, "receipt": receipt})

    return app


def submit_payload(
    payload: ThreatPayload,
    *,
    app: FastAPI | None = None,
    settings: ClientAutumbraSettings | None = None,
    source_credits: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """In-process door: ThreatPayload object only → queue put + drain (product path)."""
    if not isinstance(payload, ThreatPayload):
        raise TypeError("Enforce accepts ThreatPayload only.")
    target = app or create_enforce_app()
    q = getattr(target.state, "threat_queue", None)
    if q is None:
        # Ephemeral in-process queue for unit helpers.
        import tempfile
        from .threat_queue import ThreatPayloadQueue

        q = ThreatPayloadQueue(Path(tempfile.mkdtemp(prefix="autumbra-q-")))
        target.state.threat_queue = q
    q.put(payload)
    return drain_once_for_enforce(
        app=target,
        queue=q,
        settings=settings,
        source_credits=source_credits,
    )


def submit_undo(
    payload: ThreatPayload,
    *,
    app: FastAPI | None = None,
) -> dict[str, Any]:
    """In-process undo door: ThreatPayload with action=undo_shield only."""
    if not isinstance(payload, ThreatPayload):
        raise TypeError("submit_undo accepts ThreatPayload (action=undo_shield) only.")
    if payload.action.value != "undo_shield":
        raise TypeError("submit_undo requires action=undo_shield")
    return submit_payload(payload, app=app)


app = create_enforce_app()


def main() -> None:
    import uvicorn

    if os.environ.get("AUTUMBRA_ENFORCE_DROP_TANK_KEY") == "1":
        os.environ.pop("TANK_KEY", None)
    host = os.environ.get("ENFORCE_HOST", "127.0.0.1")
    port = int(os.environ.get("ENFORCE_PORT", "8000"))
    uvicorn.run("ghost.enforce_app:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
