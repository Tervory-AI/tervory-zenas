"""ThreatPayload queue between Autumbra Watch and Enforce.

Watch writes validated ThreatPayload objects only. Enforce validates each item
before acting; a bad item is quarantined with a receipt and the queue keeps
flowing. No raw logs cross this door. No fixed CVE or severity is stamped —
severity comes only from a matched public advisory.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .client_settings import ClientAutumbraSettings
from .enforce_apply import apply_from_payload, approve_pending, undo_from_payload
from .fixture_edge import DEFAULT_CONTROL, FixtureEdgeControl
from .never_block import ActionCap, may_block_ip
from .speed_budgets import stamp_budget, summarize_budgets, utc_now
from .tenant_queue import push_receipt_to_founder_console
from .threat_payload import ThreatAction, ThreatPayload, ThreatSeverity, accept_threat_payload


def queue_root_from_env() -> Path:
    raw = (os.environ.get("THREAT_PAYLOAD_QUEUE") or "").strip()
    if not raw:
        raise RuntimeError("THREAT_PAYLOAD_QUEUE is not configured.")
    return Path(raw)


class ThreatPayloadQueue:
    """Directory queue: one JSON file per ThreatPayload (fields only)."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "inbox").mkdir(exist_ok=True)
        (self.root / "receipts").mkdir(exist_ok=True)
        (self.root / "quarantine").mkdir(exist_ok=True)

    def put(self, payload: ThreatPayload) -> str:
        if not isinstance(payload, ThreatPayload):
            raise TypeError("Queue accepts ThreatPayload only.")
        item_id = uuid.uuid4().hex
        path = self.root / "inbox" / f"{item_id}.json"
        data = payload.model_dump(mode="json")
        if not data.get("queued_at"):
            data["queued_at"] = utc_now()
        path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        return item_id

    def take(self) -> tuple[str, ThreatPayload] | None:
        """Validate via accept_threat_payload (same door as apply/undo). Quarantine bad items."""
        inbox = self.root / "inbox"
        while True:
            files = sorted(inbox.glob("*.json"))
            if not files:
                return None
            path = files[0]
            item_id = path.stem
            tenant_hint: str | None = None
            try:
                raw_text = path.read_text(encoding="utf-8")
                data = json.loads(raw_text)
                if isinstance(data, dict):
                    tid = data.get("tenant_id")
                    if isinstance(tid, str) and tid.strip():
                        tenant_hint = tid.strip()
                # Same validated door as HTTP apply/undo — action rules + injection pre-flight.
                payload = accept_threat_payload(data if isinstance(data, dict) else raw_text)
            except Exception as exc:
                self._quarantine(
                    path, item_id, reason=type(exc).__name__, tenant_id=tenant_hint
                )
                continue
            path.unlink(missing_ok=True)
            return item_id, payload

    def _quarantine(
        self,
        path: Path,
        item_id: str,
        *,
        reason: str,
        tenant_id: str | None = None,
    ) -> None:
        dest = self.root / "quarantine" / f"{item_id}.json"
        try:
            shutil.move(str(path), str(dest))
        except OSError:
            path.unlink(missing_ok=True)
            dest = self.root / "quarantine" / f"{item_id}.missing.json"
            dest.write_text("{}", encoding="utf-8")
        # Prefer tenant_id from the bad item; else from this Enforce process env.
        tid = (tenant_id or "").strip() or (os.environ.get("TENANT_ID") or "").strip() or None
        receipt = {
            "id": str(uuid.uuid4()),
            "product": "Autumbra",
            "role": "Enforce",
            "kind": "queue_quarantine",
            "outcome": "quarantined",
            "timestamp": utc_now(),
            "queue_item_id": item_id,
            "tenant_id": tid,
            "note": f"Invalid queue item quarantined ({reason}); queue kept flowing.",
            "applied": False,
            "raw_log_seen": False,
        }
        self.write_receipt(receipt)
        try:
            push_receipt_to_founder_console(self.root, receipt)
        except Exception:
            pass

    def write_receipt(self, receipt: dict[str, Any]) -> Path:
        summarize_budgets(receipt)
        if "timestamp" not in receipt:
            receipt["timestamp"] = utc_now()
        rid = str(receipt.get("id") or uuid.uuid4())
        path = self.root / "receipts" / f"{rid}.json"
        path.write_text(json.dumps(receipt, sort_keys=True, default=str), encoding="utf-8")
        try:
            push_receipt_to_founder_console(self.root, receipt)
        except Exception:
            pass
        return path

    def list_receipts(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in sorted((self.root / "receipts").glob("*.json")):
            out.append(json.loads(path.read_text(encoding="utf-8")))
        return out

    def list_quarantine(self) -> list[str]:
        return sorted(p.name for p in (self.root / "quarantine").glob("*.json"))


# Public advisory hosts Watch may link. Caller-invented URLs never stamp severity.
_PUBLIC_ADVISORY_HOSTS = frozenset({
    "www.cisa.gov",
    "cisa.gov",
    "api.osv.dev",
    "osv.dev",
    "api.github.com",
    "github.com",
    "services.nvd.nist.gov",
    "nvd.nist.gov",
    "www.php.net",
    "php.net",
    "nodejs.org",
    "www.cve.org",
    "cve.org",
})


def _advisory_url_is_public(url: str) -> bool:
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in _PUBLIC_ADVISORY_HOSTS:
        return True
    # Allow subdomains of allowlisted registrable hosts (e.g. advisories.github.com).
    return any(host.endswith("." + h) for h in _PUBLIC_ADVISORY_HOSTS)


def _advisory_fields(
    *,
    advisory: dict[str, Any] | None,
) -> dict[str, Any]:
    """Severity / CVE only when Autumbra itself matches a Market Lab public advisory.

    Caller matched / match_id / matched_component / from_match are ignored.
    Autumbra looks the hint up in the Market Lab catalog and stamps only what
    that row proves (allowlisted public URL + rating from Market Lab).
    """
    import re

    from .market_lab_intel import match_advisory_from_market_lab

    unknown = {
        "severity": ThreatSeverity.unknown.value,
        "cve_ids": [],
        "confidence": 0.0,
        "advisory_url": None,
        "source_name": "",
        "source_url": "",
        "component": "",
    }
    if not advisory or not isinstance(advisory, dict):
        return unknown
    # Strip caller assertions so they cannot smuggle a match.
    hint = {
        k: v for k, v in advisory.items()
        if k not in {"matched", "match_id", "matched_component", "from_match"}
    }
    ml = match_advisory_from_market_lab(hint)
    if not ml:
        return unknown
    url = str(ml.get("url") or "").strip()
    rating = str(ml.get("rating") or "").strip().lower()
    if not url or not rating:
        return unknown
    if not _advisory_url_is_public(url):
        return unknown
    cve = str(ml.get("advisory_id") or "").strip()
    cves: list[str] = []
    cve_re = re.compile(r"^CVE-\d{4}-\d{4,}$")
    if cve_re.fullmatch(cve):
        cves.append(cve)
    try:
        sev = ThreatSeverity(rating).value
    except ValueError:
        return unknown
    if sev == ThreatSeverity.unknown.value:
        return unknown
    return {
        "severity": sev,
        "cve_ids": cves[:20],
        "confidence": 0.85,
        "advisory_url": url,
        "source_name": str(ml.get("source_name") or "public advisory"),
        "source_url": str(ml.get("source_url") or url),
        "component": str(ml.get("component") or ""),
    }


def watch_enqueue_fixture_attack(
    *,
    queue: ThreatPayloadQueue,
    raw_log: str,
    site: str,
    address: str,
    reason: str = "fixture attacking address",
    tenant_id: str | None = None,
    advisory: dict[str, Any] | None = None,
    source_data: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Watch side: rules-first check on dirty log, then enqueue ThreatPayload only.

    Does not call Enforce. Returns a Watch receipt with measured decision budget.
    Never stamps a fixed CVE or severity without a matched public advisory.
    """
    t0 = time.perf_counter()
    ok, why = may_block_ip(address, raw_log=raw_log)
    if not ok:
        receipt = {
            "product": "Autumbra",
            "role": "Watch",
            "outcome": "refused",
            "note": why,
            "queued": False,
            "timestamp": utc_now(),
            "tenant_id": tenant_id,
        }
        stamp_budget(receipt, stage="event_to_decision", seconds=time.perf_counter() - t0)
        return receipt
    adv = _advisory_fields(advisory=advisory)
    payload_raw: dict[str, Any] = {
        "schema_version": "ghost.threat.v1",
        "action": "propose_shield",
        "site": site,
        "kind": "block_ip",
        "address": address,
        "severity": adv["severity"],
        "reason": reason[:300],
        "confidence": adv["confidence"],
        "cve_ids": adv["cve_ids"],
        "queued_at": utc_now(),
        "source_data": source_data or {},
    }
    if tenant_id:
        payload_raw["tenant_id"] = tenant_id
    if adv.get("advisory_url"):
        payload_raw["advisory_url"] = adv["advisory_url"]
    payload = accept_threat_payload(payload_raw)
    payload = payload.model_copy(update={"watch_decision_seconds": time.perf_counter() - t0})
    item_id = queue.put(payload)
    receipt = {
        "product": "Autumbra",
        "role": "Watch",
        "outcome": "queued",
        "queued": True,
        "queue_item_id": item_id,
        "threat_fields": sorted(payload.model_dump().keys()),
        "raw_log_kept": False,
        "timestamp": utc_now(),
        "tenant_id": tenant_id,
        "severity": payload.severity.value,
        "cve_ids": list(payload.cve_ids),
        "advisory_url": payload.advisory_url,
        "source_name": adv.get("source_name") or "",
        "source_url": adv.get("source_url") or "",
        "component": adv.get("component") or "",
        "stack": adv.get("component") or "",
    }
    stamp_budget(receipt, stage="event_to_decision", seconds=time.perf_counter() - t0)
    return receipt


def _undo_used_path(queue: ThreatPayloadQueue, prior_receipt_id: str) -> Path:
    folder = queue.root / "undo_used"
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in (prior_receipt_id or "") if ch.isalnum() or ch in "-_")[:120]
    return folder / f"{safe or 'unknown'}.json"


def read_undo_used(queue: ThreatPayloadQueue, prior_receipt_id: str) -> dict[str, Any] | None:
    """Return the used-undo marker for this receipt, if any."""
    rid = (prior_receipt_id or "").strip()
    if not rid:
        return None
    path = _undo_used_path(queue, rid)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("undone_at"):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    # Also treat a completed undo receipt as used.
    for row in queue.list_receipts():
        if str(row.get("prior_receipt_id") or "") != rid:
            continue
        if str(row.get("outcome") or "") in {"undone", "already_undone"}:
            return {
                "prior_receipt_id": rid,
                "undone_at": row.get("timestamp") or row.get("undone_at") or utc_now(),
                "tenant_id": row.get("tenant_id"),
            }
    return None


def mark_undo_used(
    queue: ThreatPayloadQueue,
    *,
    prior_receipt_id: str,
    tenant_id: str | None,
    undone_at: str | None = None,
) -> dict[str, Any]:
    marker = {
        "prior_receipt_id": prior_receipt_id,
        "tenant_id": tenant_id,
        "undone_at": undone_at or utc_now(),
    }
    path = _undo_used_path(queue, prior_receipt_id)
    path.write_text(json.dumps(marker, sort_keys=True), encoding="utf-8")
    return marker


def already_undone_message(undone_at: str, *, time_zone: str = "UTC") -> str:
    """Plain page copy for a second tap on a used Undo link."""
    from .client_settings import ClientAutumbraSettings, format_owner_when

    settings = ClientAutumbraSettings(
        tenant_id="already-undone",
        time_zone="" if time_zone == "UTC" else time_zone,
    )
    when = format_owner_when(undone_at, settings) or f"{undone_at} UTC"
    # Prefer a short date for the required sentence.
    short = when
    # "Oct 27, 2026, 2:45 AM Africa/Johannesburg" → keep as-is after "on "
    return f"This block was already undone on {short}."


def watch_enqueue_undo(
    *,
    queue: ThreatPayloadQueue,
    rule_id: str | None = None,
    prior_receipt_id: str | None = None,
    site: str,
    tenant_id: str | None = None,
    time_zone: str | None = None,
) -> dict[str, Any]:
    """Outside Enforce: undo travels as a validated ThreatPayload through the queue door.

    Second tap on a used Undo link: no second undo is queued; returns already_undone.
    """
    rid = (prior_receipt_id or "").strip() or None
    if rid:
        used = read_undo_used(queue, rid)
        if used:
            undone_at = str(used.get("undone_at") or utc_now())
            detail = already_undone_message(undone_at, time_zone=time_zone or "UTC")
            return {
                "product": "Autumbra",
                "role": "Watch",
                "outcome": "already_undone",
                "queued": False,
                "already_undone": True,
                "undone_at": undone_at,
                "detail": detail,
                "timestamp": utc_now(),
                "tenant_id": tenant_id,
                "prior_receipt_id": rid,
            }

    payload = accept_threat_payload({
        "schema_version": "ghost.threat.v1",
        "action": ThreatAction.undo_shield.value,
        "site": site,
        "rule_id": rule_id or "",
        "prior_receipt_id": prior_receipt_id,
        "tenant_id": tenant_id,
        "reason": "client undo",
        "queued_at": utc_now(),
    })
    item_id = queue.put(payload)
    if rid:
        mark_undo_used(queue, prior_receipt_id=rid, tenant_id=tenant_id)
    return {
        "product": "Autumbra",
        "role": "Watch",
        "outcome": "queued_undo",
        "queued": True,
        "queue_item_id": item_id,
        "timestamp": utc_now(),
        "tenant_id": tenant_id,
    }


def watch_enqueue_approve(
    *,
    queue: ThreatPayloadQueue,
    prior_receipt_id: str,
    site: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    payload = accept_threat_payload({
        "schema_version": "ghost.threat.v1",
        "action": ThreatAction.approve_shield.value,
        "site": site,
        "prior_receipt_id": prior_receipt_id,
        "tenant_id": tenant_id,
        "reason": "client approve",
        "queued_at": utc_now(),
    })
    item_id = queue.put(payload)
    return {
        "product": "Autumbra",
        "role": "Watch",
        "outcome": "queued_approve",
        "queued": True,
        "queue_item_id": item_id,
        "timestamp": utc_now(),
        "tenant_id": tenant_id,
    }


def _queue_transit_seconds(payload: ThreatPayload) -> float | None:
    if not payload.queued_at:
        return None
    try:
        # Support both ...Z and ...%fZ
        text = payload.queued_at.replace("Z", "+00:00")
        from datetime import datetime

        queued = datetime.fromisoformat(text)
        now = datetime.now(queued.tzinfo)
        return max(0.0, (now - queued).total_seconds())
    except Exception:
        return None


def enforce_drain_once(
    queue: ThreatPayloadQueue,
    *,
    settings: ClientAutumbraSettings,
    control: FixtureEdgeControl | None = None,
    action_cap: ActionCap | None = None,
    source_credits: list[dict[str, str]] | None = None,
    data_dir: Path | str | None = None,
    on_act_email: Any | None = None,
) -> dict[str, Any] | None:
    """Enforce worker: validate → act (apply/undo/approve) → receipt. Bad items already quarantined in take()."""
    t_drain = time.perf_counter()
    taken = queue.take()
    if taken is None:
        return None
    _item_id, payload = taken
    edge = control or DEFAULT_CONTROL
    cap = action_cap or ActionCap(data_dir=data_dir or queue.root)

    transit = _queue_transit_seconds(payload)

    from .cloudflare_edge import CloudflareEdgeError

    def retry_edge_failure(error: CloudflareEdgeError) -> None:
        owned_undo = getattr(error, "retry_undo", None)
        if owned_undo:
            queue.put(ThreatPayload.model_validate({**payload.model_dump(), **owned_undo,
                                                   "action": ThreatAction.undo_shield}))
        else:
            queue.put(payload)

    def record_edge_failure(error: CloudflareEdgeError) -> None:
        failure = {"id": str(uuid.uuid4()), "tenant_id": settings.tenant_id,
                   "timestamp": utc_now(), "product": "Autumbra", "role": "Enforce",
                   "action": payload.action.value, "control": "cloudflare", "applied": False,
                   "outcome": "edge_unconfirmed", "edge_state": "unconfirmed",
                   "note": "Edge outcome could not be confirmed. Inspect the owned rule before retrying.",
                   "undo_retry_queued": bool(getattr(error, "retry_undo", None)),
                   "edge_requests": list(getattr(edge, "measurements", [])),
                   **settings.receipt_stamp()}
        stamp_budget(failure, stage="edge_attempt", seconds=time.perf_counter() - t_drain)
        if transit is not None:
            stamp_budget(failure, stage="queue_transit", seconds=transit)
        queue.write_receipt(failure)

    # Undo / approve doors — ThreatPayload only.
    if payload.action is ThreatAction.undo_shield:
        try:
            receipt = undo_from_payload(payload, control=edge, settings=settings, data_dir=data_dir)
        except CloudflareEdgeError as error:
            retry_edge_failure(error)
            record_edge_failure(error)
            raise
        if transit is not None:
            stamp_budget(receipt, stage="queue_transit", seconds=transit)
        stamp_budget(receipt, stage="event_to_undo_e2e", seconds=time.perf_counter() - t_drain + (transit or 0))
        queue.write_receipt(receipt)
        return receipt

    if payload.action is ThreatAction.approve_shield:
        try:
            receipt = approve_pending(payload, control=edge, settings=settings, data_dir=data_dir)
        except CloudflareEdgeError as error:
            retry_edge_failure(error)
            record_edge_failure(error)
            raise
        if transit is not None:
            stamp_budget(receipt, stage="queue_transit", seconds=transit)
        shield = (receipt.get("budgets") or {}).get("event_to_shield_e2e")
        if shield is not None:
            stamp_budget(receipt, stage="event_to_shield_e2e", seconds=shield["seconds"] + (transit or 0))
        queue.write_receipt(receipt)
        if on_act_email and receipt.get("applied"):
            try:
                on_act_email(receipt, settings)
            except Exception:
                pass
        return receipt

    allowed, cap_why = cap.allow(settings.tenant_id)
    if not allowed:
        receipt = {
            "id": str(uuid.uuid4()),
            "product": "Autumbra",
            "role": "Enforce",
            "outcome": "refused_action_cap",
            "note": cap_why,
            "applied": False,
            "raw_log_seen": False,
            "timestamp": utc_now(),
            "tenant_id": settings.tenant_id,
            **settings.receipt_stamp(),
        }
        if transit is not None:
            stamp_budget(receipt, stage="queue_transit", seconds=transit)
        queue.write_receipt(receipt)
        return receipt

    try:
        receipt = apply_from_payload(
            payload,
            settings=settings,
            control=edge,
            source_credits=source_credits,
            data_dir=data_dir,
        )
    except CloudflareEdgeError as error:
        if getattr(error, "retry_undo", None):
            retry_edge_failure(error)
        record_edge_failure(error)
        raise
    if transit is not None:
        stamp_budget(receipt, stage="queue_transit", seconds=transit)
    watch_seconds = payload.watch_decision_seconds
    receipt["timing_origin"] = "watch_decision" if watch_seconds is not None else "queue"
    decision = (receipt.get("budgets") or {}).get("event_to_decision")
    if decision is not None and watch_seconds is not None:
        stamp_budget(receipt, stage="event_to_decision", seconds=decision["seconds"] + watch_seconds)
    # Actual confirmed-write timing, including Watch decision and queue transit; never health wait.
    shield = (receipt.get("budgets") or {}).get("event_to_shield_e2e")
    if shield is not None:
        stamp_budget(receipt, stage="event_to_shield_e2e", seconds=shield["seconds"] + (transit or 0) + (watch_seconds or 0))
    queue.write_receipt(receipt)
    if on_act_email and receipt.get("applied"):
        try:
            on_act_email(receipt, settings)
        except Exception:
            pass
    return receipt


def run_pipe_fixture_attack(
    *,
    queue: ThreatPayloadQueue,
    settings: ClientAutumbraSettings,
    raw_log: str,
    site: str,
    address: str,
    control: FixtureEdgeControl | None = None,
    action_cap: ActionCap | None = None,
    advisory: dict[str, Any] | None = None,
    data_dir: Path | str | None = None,
) -> dict[str, Any]:
    """One fixture attack: Watch → queue → Enforce → shield → health → receipt.

    Callers must not invoke apply_from_payload directly; this is the product path.
    """
    watch = watch_enqueue_fixture_attack(
        queue=queue,
        raw_log=raw_log,
        site=site,
        address=address,
        tenant_id=settings.tenant_id,
        advisory=advisory,
    )
    if not watch.get("queued"):
        return {"watch": watch, "enforce": None}
    enforce = enforce_drain_once(
        queue,
        settings=settings,
        control=control,
        action_cap=action_cap,
        data_dir=data_dir,
    )
    return {"watch": watch, "enforce": enforce}
