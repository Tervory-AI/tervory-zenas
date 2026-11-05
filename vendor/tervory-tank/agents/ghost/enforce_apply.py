"""Autumbra Enforce apply path — ThreatPayload objects only. Never raw logs.

Watch quarantines dirty text into ThreatPayload. Enforce reads the validated
ThreatPayload type only (not raw bytes/str/dict) and applies, approves, or
undoes shields against the fixture edge control (until a real connected hand
exists). A model is never called from Enforce. No Tank key required.
"""
from __future__ import annotations

import os

import httpx
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .cloudflare_edge import CloudflarePlanRefused
from .client_settings import ApplyMode, ClientAutumbraSettings
from .durable_shields import load_pending, persist_control, persist_pending
from .fixture_edge import DEFAULT_CONTROL, FixtureEdgeControl
from .never_block import classify_never_block
from .shields import plan_for, zone_of
from .speed_budgets import stamp_budget, utc_now
from .threat_payload import ThreatAction, ThreatPayload

# Pending approvals expire if never approved (seconds). Overridable in tests.
PENDING_APPROVAL_TTL_SEC = 7 * 24 * 3600


def control_name(control: FixtureEdgeControl) -> str:
    return getattr(control, "control_name", "fixture_edge")


def select_control(*, site: str, settings: ClientAutumbraSettings,
                   control: FixtureEdgeControl | None = None,
                   required_control: str | None = None) -> FixtureEdgeControl:
    """Select live control only behind all four approved gates."""
    from .cloudflare_edge import (
        CloudflareEdgeControl, CloudflareEdgeError, CloudflareProxyTransport, TOKEN_ENV, proxy_answers,
    )
    from .site_health import health_configured, read_health

    edge = control or DEFAULT_CONTROL
    if isinstance(edge, CloudflareEdgeControl) and isinstance(edge._client._transport, httpx.MockTransport):
        if edge.tenant_id != settings.tenant_id or edge.site != site:
            raise ValueError("Cloudflare control does not belong to this client and site.")
        return edge
    # Existing shield ownership survives probe/configuration outages. Only new
    # proposals may fall back to the fixture; approval and Undo fail and retry.
    if required_control == "cloudflare":
        if isinstance(edge, CloudflareEdgeControl):
            if edge.tenant_id != settings.tenant_id or edge.site != site:
                raise ValueError("Cloudflare control does not belong to this client and site.")
            return edge
        if not os.environ.get(TOKEN_ENV, "").strip():
            raise CloudflareEdgeError("Cloudflare request failed.")
        probe_result = {}
        live = CloudflareEdgeControl(
            tenant_id=settings.tenant_id, site=site, transport=CloudflareProxyTransport(),
            token=os.environ[TOKEN_ENV], health_probe=lambda: read_health(site),
            fresh_health_probe=lambda after: read_health(site, after=after, wait_seconds=30, result_out=probe_result),
        )
        live.last_health_result = probe_result
        live.blocked.update(edge.blocked)
        return live
    configured = bool(os.environ.get(TOKEN_ENV, "").strip())
    if not configured or zone_of(site) is None or not health_configured(site) or not proxy_answers():
        if isinstance(edge, CloudflareEdgeControl):
            fixture = FixtureEdgeControl(tenant_id=settings.tenant_id, site=site)
            fixture.blocked.update(edge.blocked)
            return fixture
        return edge
    if isinstance(edge, CloudflareEdgeControl):
        if edge.tenant_id != settings.tenant_id or edge.site != site:
            raise ValueError("Cloudflare control does not belong to this client and site.")
        return edge
    probe_result = {}
    live = CloudflareEdgeControl(
        tenant_id=settings.tenant_id, site=site, transport=CloudflareProxyTransport(),
        token=os.environ[TOKEN_ENV], health_probe=lambda: read_health(site),
        fresh_health_probe=lambda after: read_health(site, after=after, wait_seconds=30, result_out=probe_result),
    )
    live.last_health_result = probe_result
    live.blocked.update(edge.blocked)
    return live


def _health_after_apply(edge: FixtureEdgeControl, row: dict | None = None) -> bool:
    from .cloudflare_edge import CloudflareEdgeControl
    if row is not None and isinstance(edge, CloudflareEdgeControl):
        healthy = edge.health_check(applied_after=row["applied_at"])
        from .site_health import _read_result
        result = getattr(edge, "last_health_result", None)
        if not result:
            result = _read_result(edge.site)
        if result is not None and result["checked_at"] > row["applied_at"]:
            edge.last_health_result = result
        return healthy
    return edge.health_check()


def _plan_refusal(receipt: dict[str, Any], error: CloudflarePlanRefused) -> dict[str, Any]:
    receipt.update(applied=False, outcome="refused_plan", note=str(error),
                   control="cloudflare", cloudflare_plan=error.plan)
    return receipt


def _apply_shield(edge: FixtureEdgeControl, *, site: str, kind: str,
                  address: str | None, path: str | None, ref: str) -> dict[str, Any]:
    if kind == "rate_limit":
        return edge.apply_rate_limit(site=site, path=path, ref=ref)
    return edge.apply_block_ip(site=site, address=address, ref=ref)


def payload_fields_only(payload: ThreatPayload) -> dict[str, Any]:
    """The only input Enforce may see."""
    # mode=json so severity is "critical", never ThreatSeverity.critical str()
    return payload.model_dump(mode="json")


def _measure_raw_log_seen(payload: object) -> bool:
    """Measured at the Enforce boundary — not a hard-coded constant."""
    return not isinstance(payload, ThreatPayload)


def _persist(data_dir: Path | str | None, control: FixtureEdgeControl) -> None:
    if data_dir is None:
        return
    persist_control(data_dir, control)


def apply_from_payload(
    payload: object,
    *,
    settings: ClientAutumbraSettings,
    control: FixtureEdgeControl | None = None,
    source_credits: list[dict[str, str]] | None = None,
    data_dir: Path | str | None = None,
) -> dict[str, Any]:
    """ThreatPayload only → never-block → client shield_mode → fixture apply."""
    t0 = time.perf_counter()
    raw_log_seen = _measure_raw_log_seen(payload)
    if raw_log_seen:
        raise TypeError(
            "Enforce accepts ThreatPayload only. Quarantine dirty input in Watch first."
        )
    assert isinstance(payload, ThreatPayload)

    fields = payload_fields_only(payload)
    receipt: dict[str, Any] = {
        "id": str(uuid4()),
        "product": "Autumbra",
        "role": "Enforce",
        "action": fields.get("action"),
        "site": fields.get("site"),
        "kind": fields.get("kind"),
        "address": fields.get("address"),
        "input_fields": sorted(fields.keys()),
        "raw_log_seen": raw_log_seen,
        "timestamp": utc_now(),
        "tenant_id": settings.tenant_id,
        "advisory_url": fields.get("advisory_url"),
        "cve_ids": list(fields.get("cve_ids") or []),
        "severity": fields.get("severity"),
        **settings.receipt_stamp(),
    }
    if payload.source_data:
        receipt["source_data"] = dict(payload.source_data)
    if source_credits:
        receipt["sources"] = source_credits
        receipt["intel_via"] = "Market Lab"

    edge = select_control(site=payload.site, settings=settings, control=control)
    name = control_name(edge)
    receipt["control"] = name
    if name == "cloudflare":
        edge.measurements = []
        receipt["edge_requests"] = edge.measurements

    decision_seconds = time.perf_counter() - t0
    stamp_budget(receipt, stage="event_to_decision", seconds=decision_seconds)

    if payload.kind == "block_ip":
        address = payload.address or ""
        why = classify_never_block(address, client_own_cidrs=settings.own_cidrs)
        if why:
            receipt["outcome"] = "refused_never_block"
            receipt["note"] = f"never-block: {why}"
            receipt["applied"] = False
            return receipt

    mode = settings.shield_mode
    receipt["outcome"] = {
        ApplyMode.never: "report_only",
        ApplyMode.approve_each: "awaiting_approval",
        ApplyMode.auto_when_checks_pass: "auto_apply",
    }[mode]

    site = payload.site
    ref = f"enforce-{receipt['id'].replace('-', '')[:16]}"
    if payload.kind:
        receipt["plan"] = plan_for(
            payload.kind,
            site if "." in site else site,
            payload.path,
            ref=ref,
            address=payload.address,
        )

    if name == "cloudflare" and payload.kind == "waf_managed" and mode is not ApplyMode.never:
        try:
            edge.check_managed_plan()
        except CloudflarePlanRefused as error:
            return _plan_refusal(receipt, error)

    supported = (payload.kind == "block_ip" and bool(payload.address)) or (
        name == "cloudflare" and payload.kind == "rate_limit" and bool(payload.path)
    )
    t1 = time.perf_counter()
    applied = False
    if (
        mode is ApplyMode.auto_when_checks_pass
        and supported
    ):
        try:
            row = _apply_shield(edge, site=site, kind=payload.kind, address=payload.address, path=payload.path, ref=ref)
        except CloudflarePlanRefused as error:
            return _plan_refusal(receipt, error)
        receipt["applied_record"] = row
        receipt["undo"] = {"rule_id": row["rule_id"], "control": name}
        applied = True
        receipt["outcome"] = "applied"
        _persist(data_dir, edge)
    elif mode is ApplyMode.approve_each and supported:
        receipt["undo"] = {"rule_id": None, "control": name, "pending": True}
        if data_dir is not None:
            pending = load_pending(data_dir)
            pending[receipt["id"]] = {
                "receipt_id": receipt["id"],
                "site": site,
                "address": payload.address,
                "kind": payload.kind,
                "path": payload.path,
                "control": name,
                "tenant_id": settings.tenant_id,
                "created_at": utc_now(),
                "expires_at_epoch": time.time() + PENDING_APPROVAL_TTL_SEC,
                "payload": fields,
            }
            persist_pending(data_dir, pending)
    elif mode is ApplyMode.never:
        receipt["undo"] = {"rule_id": None, "control": name, "report_only": True}
    stamp_budget(receipt, stage="decision_to_shield", seconds=time.perf_counter() - t1)
    if applied:
        stamp_budget(receipt, stage="event_to_shield_e2e", seconds=time.perf_counter() - t0)

    t2 = time.perf_counter()
    healthy = _health_after_apply(edge, receipt.get("applied_record") if applied else None)
    stamp_budget(receipt, stage="health_check", seconds=time.perf_counter() - t2)
    receipt["health_ok"] = healthy
    if getattr(edge, "last_health_result", None):
        receipt["health_probe"] = dict(edge.last_health_result)

    if applied and not healthy:
        # Full event→undo window starts at apply begin for budget honesty.
        undone = undo_applied(receipt, control=edge, data_dir=data_dir)
        stamp_budget(undone, stage="auto_undo_e2e", seconds=time.perf_counter() - t0)
        receipt["outcome"] = "auto_undone_health_failed"
        receipt["applied"] = False
        receipt["auto_undo"] = undone
        receipt["budgets"] = {
            **(receipt.get("budgets") or {}),
            **(undone.get("budgets") or {}),
        }
        receipt["control"] = name
        return receipt

    receipt["applied"] = applied
    receipt["control"] = name
    return receipt


def undo_applied(
    receipt: dict[str, Any],
    *,
    control: FixtureEdgeControl | None = None,
    data_dir: Path | str | None = None,
) -> dict[str, Any]:
    """One-tap undo for an Enforce receipt that was applied on the fixture control."""
    t0 = time.perf_counter()
    if not isinstance(receipt, dict):
        raise TypeError("undo_applied accepts the prior Enforce receipt dict only.")
    undo = receipt.get("undo") or {}
    if not isinstance(undo, dict):
        raise TypeError("undo_applied: receipt.undo must be a dict.")
    rule_id = undo.get("rule_id")
    if not rule_id:
        raise LookupError("no applied shield to undo")
    edge = control or DEFAULT_CONTROL
    name = control_name(edge)
    if undo.get("control", name) != name:
        raise LookupError("shield belongs to another control")
    if name == "cloudflare":
        edge.measurements = []
    stored = next((r for r in edge.list_blocked() if r.get("rule_id") == str(rule_id)), {})
    if stored.get("control", name) != name:
        raise LookupError("shield belongs to another control")
    from .cloudflare_edge import CloudflareEdgeError
    try:
        row = edge.undo(str(rule_id))
    except CloudflareEdgeError as exc:
        # Preserve the exact owned Undo when a health-triggered Undo loses its
        # proxy. Queue retry must not replay an already completed approval.
        exc.retry_undo = {"rule_id": str(rule_id), "site": stored["site"],
                          "tenant_id": edge.tenant_id, "prior_receipt_id": receipt.get("id")}
        raise
    _persist(data_dir, edge)
    out = {
        "id": str(uuid4()),
        "product": "Autumbra",
        "role": "Enforce",
        "action": "undo",
        "outcome": "undone",
        "undone": row,
        "prior_receipt_id": receipt.get("id"),
        "applied": False,
        "control": name,
        "timestamp": utc_now(),
        "tenant_id": receipt.get("tenant_id"),
    }
    if name == "cloudflare":
        out["edge_requests"] = list(edge.measurements)
    stamp_budget(out, stage="auto_undo_e2e", seconds=time.perf_counter() - t0)
    return out


def undo_from_payload(
    payload: ThreatPayload,
    *,
    control: FixtureEdgeControl | None = None,
    settings: ClientAutumbraSettings,
    data_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Undo via validated ThreatPayload door (from outside Enforce through the queue)."""
    if not isinstance(payload, ThreatPayload) or payload.action is not ThreatAction.undo_shield:
        raise TypeError("undo_from_payload requires action=undo_shield")
    edge = control or DEFAULT_CONTROL
    rule_id = (payload.rule_id or "").strip()
    if not rule_id and payload.prior_receipt_id and data_dir is not None:
        # Resolve rule_id from founder console / receipts on the queue box later;
        # for durable box, scan blocked by prior ref if needed.
        for row in edge.list_blocked():
            if payload.prior_receipt_id.replace("-", "")[:16] in str(row.get("ref") or ""):
                rule_id = row["rule_id"]
                break
    if not rule_id:
        return {
            "id": str(uuid4()),
            "product": "Autumbra",
            "role": "Enforce",
            "action": "undo_shield",
            "outcome": "undo_failed",
            "note": "no applied shield matched",
            "applied": False,
            "timestamp": utc_now(),
            "tenant_id": settings.tenant_id,
            **settings.receipt_stamp(),
        }
    stored = next((row for row in edge.list_blocked() if row.get("rule_id") == rule_id), {})
    if not stored and data_dir is not None:
        from .durable_shields import read_shields_table
        table, _ = read_shields_table(data_dir)
        if table and table.get("tenant_id") == settings.tenant_id:
            candidate = (table.get("blocked") or {}).get(rule_id)
            if isinstance(candidate, dict) and candidate.get("site") == payload.site:
                stored = candidate
                edge.blocked[rule_id] = dict(candidate)
    edge = select_control(site=payload.site, settings=settings, control=edge,
                          required_control=stored.get("control"))
    prior = {"id": payload.prior_receipt_id, "undo": {"rule_id": rule_id}, "tenant_id": settings.tenant_id}
    try:
        out = undo_applied(prior, control=edge, data_dir=data_dir)
    except LookupError:
        return {
            "id": str(uuid4()),
            "product": "Autumbra",
            "role": "Enforce",
            "action": "undo_shield",
            "outcome": "undo_failed",
            "note": "shield not found",
            "applied": False,
            "timestamp": utc_now(),
            "tenant_id": settings.tenant_id,
            **settings.receipt_stamp(),
        }
    out["action"] = "undo_shield"
    out["tenant_id"] = settings.tenant_id
    out.update(settings.receipt_stamp())
    return out


def approve_pending(
    payload: ThreatPayload,
    *,
    control: FixtureEdgeControl | None = None,
    settings: ClientAutumbraSettings,
    data_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Apply a pending shield on approval (ThreatPayload door)."""
    if not isinstance(payload, ThreatPayload) or payload.action is not ThreatAction.approve_shield:
        raise TypeError("approve_pending requires action=approve_shield")
    if data_dir is None:
        return {
            "id": str(uuid4()),
            "product": "Autumbra",
            "role": "Enforce",
            "action": "approve_shield",
            "outcome": "approve_failed",
            "note": "no durable pending store",
            "applied": False,
            "timestamp": utc_now(),
            "tenant_id": settings.tenant_id,
            **settings.receipt_stamp(),
        }
    pending = load_pending(data_dir)
    rid = str(payload.prior_receipt_id or "")
    row = pending.pop(rid, None)
    if row is None:
        return {
            "id": str(uuid4()),
            "product": "Autumbra",
            "role": "Enforce",
            "action": "approve_shield",
            "outcome": "approve_failed",
            "note": "pending shield not found or already expired",
            "applied": False,
            "timestamp": utc_now(),
            "tenant_id": settings.tenant_id,
            **settings.receipt_stamp(),
        }
    if float(row.get("expires_at_epoch") or 0) < time.time():
        persist_pending(data_dir, pending)
        return {
            "id": str(uuid4()),
            "product": "Autumbra",
            "role": "Enforce",
            "action": "approve_shield",
            "outcome": "expired",
            "note": "pending shield expired before approval",
            "prior_receipt_id": rid,
            "applied": False,
            "timestamp": utc_now(),
            "tenant_id": settings.tenant_id,
            **settings.receipt_stamp(),
        }
    if row.get("tenant_id", settings.tenant_id) != settings.tenant_id:
        raise ValueError("pending shield belongs to another client")
    address = str(row.get("address") or "")
    site = str(row.get("site") or payload.site)
    edge = select_control(site=site, settings=settings, control=control,
                          required_control=row.get("control"))
    name = control_name(edge)
    if row.get("control", "fixture_edge") != name:
        raise LookupError("pending shield belongs to another control")
    if site != payload.site:
        raise ValueError("approval site does not match pending shield")
    if row.get("kind", "block_ip") == "block_ip":
        why = classify_never_block(address, client_own_cidrs=settings.own_cidrs)
        if why:
            raise ValueError("pending address is on the never-block list")
    if name == "cloudflare":
        edge.measurements = []
    ref = f"approve-{rid.replace('-', '')[:16]}"
    t0 = time.perf_counter()
    try:
        applied_row = _apply_shield(edge, site=site, kind=row.get("kind", "block_ip"), address=address, path=row.get("path"), ref=ref)
    except CloudflarePlanRefused as error:
        persist_pending(data_dir, pending)
        return _plan_refusal({
            "id": str(uuid4()), "product": "Autumbra", "role": "Enforce",
            "action": "approve_shield", "prior_receipt_id": rid, "site": site,
            "kind": row.get("kind"), "timestamp": utc_now(),
            "tenant_id": settings.tenant_id, **settings.receipt_stamp(),
        }, error)
    shield_seconds = time.perf_counter() - t0
    _persist(data_dir, edge)
    persist_pending(data_dir, pending)
    receipt = {
        "id": str(uuid4()),
        "product": "Autumbra",
        "role": "Enforce",
        "action": "approve_shield",
        "outcome": "applied",
        "applied": True,
        "applied_record": applied_row,
        "undo": {"rule_id": applied_row["rule_id"], "control": name},
        "prior_receipt_id": rid,
        "address": address,
        "site": site,
        "timestamp": utc_now(),
        "tenant_id": settings.tenant_id,
        "control": name,
        **settings.receipt_stamp(),
    }

    if (row.get("payload") or {}).get("source_data"):
        receipt["source_data"] = dict(row["payload"]["source_data"])

    if name == "cloudflare":
        receipt["edge_requests"] = list(edge.measurements)

    stamp_budget(receipt, stage="event_to_shield_e2e", seconds=shield_seconds)
    stamp_budget(receipt, stage="decision_to_shield", seconds=shield_seconds)
    t_health = time.perf_counter()
    receipt["health_ok"] = _health_after_apply(edge, applied_row)
    if getattr(edge, "last_health_result", None):
        receipt["health_probe"] = dict(edge.last_health_result)
    stamp_budget(receipt, stage="health_check", seconds=time.perf_counter() - t_health)
    if not receipt["health_ok"]:
        receipt["auto_undo"] = undo_applied(receipt, control=edge, data_dir=data_dir)
        receipt["outcome"] = "auto_undone_health_failed"
        receipt["applied"] = False
        stamp_budget(receipt, stage="auto_undo_e2e", seconds=time.perf_counter() - t0)
    return receipt


def expire_pending_approvals(
    *,
    data_dir: Path | str,
    settings: ClientAutumbraSettings,
    queue_write: Any | None = None,
) -> list[dict[str, Any]]:
    """Expire unapproved pending shields with a receipt each."""
    pending = load_pending(data_dir)
    now = time.time()
    expired: list[dict[str, Any]] = []
    keep: dict[str, dict[str, Any]] = {}
    for rid, row in pending.items():
        if float(row.get("expires_at_epoch") or 0) < now:
            receipt = {
                "id": str(uuid4()),
                "product": "Autumbra",
                "role": "Enforce",
                "action": "approve_shield",
                "outcome": "expired",
                "note": "pending shield expired; never approved",
                "prior_receipt_id": rid,
                "address": row.get("address"),
                "site": row.get("site"),
                "applied": False,
                "timestamp": utc_now(),
                "tenant_id": settings.tenant_id,
                **settings.receipt_stamp(),
            }
            expired.append(receipt)
            if queue_write is not None:
                queue_write(receipt)
        else:
            keep[rid] = row
    persist_pending(data_dir, keep)
    return expired
