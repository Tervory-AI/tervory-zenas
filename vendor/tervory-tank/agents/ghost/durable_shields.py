"""Durable shield table for one client's Enforce box.

Shields survive Enforce restart. Held under DATA_DIR. Writes are atomic
(temp + fsync + rename). A truncated or corrupt shields.json never silently
drops shields: the last good copy is kept and a receipt is written.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .fixture_edge import FixtureEdgeControl


SHIELDS_FILE = "shields.json"
SHIELDS_GOOD = "shields.json.good"
PENDING_FILE = "pending_approvals.json"


def shields_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / SHIELDS_FILE


def shields_good_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / SHIELDS_GOOD


def pending_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / PENDING_FILE


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> Path:
    """Write JSON atomically: temp file, fsync, rename over the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    data = json.dumps(payload, sort_keys=True, default=str)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def _parse_shields_blob(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    blocked = raw.get("blocked")
    if blocked is not None and not isinstance(blocked, dict):
        return None
    return raw


def read_shields_table(data_dir: Path | str) -> tuple[dict[str, Any] | None, str]:
    """Return (table_or_None, source). source is 'primary', 'good', 'missing', or 'corrupt'.

    On a corrupt primary file, never treat it as empty shields — fall back to the
    last good copy when present.
    """
    primary = shields_path(data_dir)
    good = shields_good_path(data_dir)
    if not primary.exists() and not good.exists():
        return None, "missing"
    if primary.exists():
        try:
            raw = json.loads(primary.read_text(encoding="utf-8"))
            parsed = _parse_shields_blob(raw)
            if parsed is not None:
                return parsed, "primary"
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            pass
        # Corrupt primary — try last good.
        if good.exists():
            try:
                raw = json.loads(good.read_text(encoding="utf-8"))
                parsed = _parse_shields_blob(raw)
                if parsed is not None:
                    return parsed, "good"
            except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
                pass
        return None, "corrupt"
    # Primary missing but good exists.
    try:
        raw = json.loads(good.read_text(encoding="utf-8"))
        parsed = _parse_shields_blob(raw)
        if parsed is not None:
            return parsed, "good"
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        pass
    return None, "corrupt"


def load_control(data_dir: Path | str, *, tenant_id: str, site: str = "") -> FixtureEdgeControl:
    """Build a FixtureEdgeControl restored from the client's durable box.

    Corrupt primary falls back to the last good copy (never an empty table).
    """
    control = FixtureEdgeControl(tenant_id=tenant_id, site=site)
    table, _source = read_shields_table(data_dir)
    if table is None:
        return control
    blocked = table.get("blocked") if isinstance(table, dict) else None
    if isinstance(blocked, dict):
        for rule_id, row in blocked.items():
            if isinstance(row, dict) and isinstance(rule_id, str):
                control.blocked[rule_id] = dict(row)
    if isinstance(table, dict) and "healthy" in table:
        control.healthy = bool(table["healthy"])
    if isinstance(table, dict) and not site and isinstance(table.get("site"), str):
        control.site = table["site"] or control.site
    return control


def persist_control(data_dir: Path | str, control: FixtureEdgeControl) -> Path:
    """Atomically persist shields and refresh the last-good copy."""
    path = shields_path(data_dir)
    payload = {
        "tenant_id": control.tenant_id,
        "site": control.site,
        "healthy": control.healthy,
        "blocked": {k: dict(v) for k, v in control.blocked.items()},
    }
    _atomic_write_json(path, payload)
    # Last-good mirror — only after a successful atomic write of primary.
    _atomic_write_json(shields_good_path(data_dir), payload)
    return path


def load_pending(data_dir: Path | str) -> dict[str, dict[str, Any]]:
    path = pending_path(data_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def persist_pending(data_dir: Path | str, pending: dict[str, dict[str, Any]]) -> Path:
    path = pending_path(data_dir)
    _atomic_write_json(path, pending)
    return path


def restart_drop_receipts(
    *,
    prior_blocked: dict[str, dict[str, Any]],
    restored_blocked: dict[str, dict[str, Any]],
    tenant_id: str,
    reason: str = "dropped_on_restart",
) -> list[dict[str, Any]]:
    """If a restart lost shields that were durable, emit a receipt per lost shield."""
    from datetime import datetime, timezone
    from uuid import uuid4

    lost = set(prior_blocked) - set(restored_blocked)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out: list[dict[str, Any]] = []
    for rule_id in sorted(lost):
        row = prior_blocked[rule_id]
        out.append({
            "id": str(uuid4()),
            "product": "Autumbra",
            "role": "Enforce",
            "kind": "restart_shield_drop",
            "outcome": reason,
            "timestamp": now,
            "tenant_id": tenant_id,
            "rule_id": rule_id,
            "address": row.get("address"),
            "site": row.get("site"),
            "note": "Shield could not be restored after Enforce restart.",
        })
    return out


def corrupt_shields_receipt(
    *,
    tenant_id: str,
    prior_blocked: dict[str, dict[str, Any]],
    restored_from_good: bool,
) -> dict[str, Any]:
    """One receipt when shields.json is truncated/corrupt (never a silent drop)."""
    from datetime import datetime, timezone
    from uuid import uuid4

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": str(uuid4()),
        "product": "Autumbra",
        "role": "Enforce",
        "kind": "restart_shield_drop",
        "outcome": "corrupt_shields_file",
        "timestamp": now,
        "tenant_id": tenant_id,
        "prior_shield_count": len(prior_blocked),
        "restored_from_good": restored_from_good,
        "note": (
            "shields.json was truncated or corrupt; "
            + (
                "kept the last good copy."
                if restored_from_good
                else "no last good copy was available."
            )
        ),
    }
