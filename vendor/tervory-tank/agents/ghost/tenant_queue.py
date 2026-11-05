"""Per-client ThreatPayload queue paths and settings mirror.

Watch reaches each client's own queue — never one shared inbox for all tenants.
Client modes saved through Watch are mirrored into that client's queue box so
the matching Enforce process can load and obey them.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .client_settings import ClientAutumbraSettings

_TENANT_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
SETTINGS_MIRROR = "client_settings.json"
FOUNDER_CONSOLE = "founder_console"


def queue_root_base() -> Path | None:
    """Watch-side root that holds one subdirectory per tenant."""
    raw = (os.environ.get("THREAT_PAYLOAD_QUEUE_ROOT") or "").strip()
    if raw:
        return Path(raw)
    # Fall back: if THREAT_PAYLOAD_QUEUE is set, its parent is the root and the
    # final component is treated as a default single-tenant queue (compat).
    single = (os.environ.get("THREAT_PAYLOAD_QUEUE") or "").strip()
    if single:
        return Path(single).parent
    return None


def queue_path_for_tenant(tenant_id: str) -> Path:
    tid = (tenant_id or "").strip()
    if not tid or not _TENANT_RE.fullmatch(tid):
        raise ValueError("tenant_id must be a UUID")
    # Per-Enforce process: THREAT_PAYLOAD_QUEUE points at THIS client's queue.
    single = (os.environ.get("THREAT_PAYLOAD_QUEUE") or "").strip()
    enforce_tenant = (os.environ.get("TENANT_ID") or "").strip()
    if single and enforce_tenant and enforce_tenant == tid:
        return Path(single)
    base = queue_root_base()
    if base is None:
        if single:
            # Single-queue mode: only the env tenant (or explicit match) may use it.
            return Path(single)
        raise RuntimeError(
            "THREAT_PAYLOAD_QUEUE_ROOT or THREAT_PAYLOAD_QUEUE is not configured."
        )
    return base / tid


def mirror_settings(settings: ClientAutumbraSettings, queue_root: Path | str) -> Path:
    root = Path(queue_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / SETTINGS_MIRROR
    path.write_text(
        json.dumps(settings.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    return path


def load_mirrored_settings(
    queue_root: Path | str,
    *,
    fallback_tenant_id: str,
) -> ClientAutumbraSettings | None:
    path = Path(queue_root) / SETTINGS_MIRROR
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("fix_mode") == "Apply automatically when checks pass":
        raw = {**raw, "fix_mode": "I approve each one"}
    try:
        return ClientAutumbraSettings.model_validate(
            {**raw, "tenant_id": raw.get("tenant_id") or fallback_tenant_id}
        )
    except Exception:
        return None


def founder_console_dir(queue_root: Path | str) -> Path:
    path = Path(queue_root) / FOUNDER_CONSOLE
    path.mkdir(parents=True, exist_ok=True)
    return path


def push_receipt_to_founder_console(queue_root: Path | str, receipt: dict[str, Any]) -> Path:
    """Product path: receipts reach the founder console (queue box), not only disk inbox."""
    console = founder_console_dir(queue_root)
    rid = str(receipt.get("id") or "unknown")
    path = console / f"{rid}.json"
    path.write_text(json.dumps(receipt, sort_keys=True, default=str), encoding="utf-8")
    return path


def list_founder_console_receipts(queue_root: Path | str) -> list[dict[str, Any]]:
    console = Path(queue_root) / FOUNDER_CONSOLE
    if not console.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(console.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out
