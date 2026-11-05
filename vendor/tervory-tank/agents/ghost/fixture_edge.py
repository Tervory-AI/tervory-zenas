"""Fixture edge control for Autumbra Enforce dogfood.

Real apply/undo against an in-process control plane — not Cloudflare, not a live
edge API. Used until a real connected hand exists. Watch never writes here;
only Enforce calls this control.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class FixtureEdgeControl:
    """Per-process (or per-tenant) shield table. Apply and undo are real mutations."""

    tenant_id: str
    site: str = ""
    blocked: dict[str, dict[str, Any]] = field(default_factory=dict)
    healthy: bool = True
    _lock: Lock = field(default_factory=Lock)

    def apply_block_ip(self, *, site: str, address: str, ref: str) -> dict[str, Any]:
        if not address:
            raise ValueError("address required")
        with self._lock:
            rule_id = uuid4().hex[:32]
            row = {
                "rule_id": rule_id,
                "site": site,
                "address": address,
                "ref": ref,
                "kind": "block_ip",
                "up_at": _now(),
            }
            self.blocked[rule_id] = row
            return dict(row)

    def undo(self, rule_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.blocked.pop(rule_id, None)
            if row is None:
                raise LookupError("shield not found on fixture control")
            out = dict(row)
            out["ended_at"] = _now()
            out["state"] = "undone"
            return out

    def list_blocked(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self.blocked.values()]

    def set_healthy(self, value: bool) -> None:
        with self._lock:
            self.healthy = bool(value)

    def health_check(self) -> bool:
        """Fixture site health. False triggers Enforce auto-undo."""
        with self._lock:
            _ = list(self.blocked.values())
            return bool(self.healthy)


# Default dogfood control (one per Enforce process).
DEFAULT_CONTROL = FixtureEdgeControl(tenant_id="fixture")


def reset_default_control() -> FixtureEdgeControl:
    global DEFAULT_CONTROL
    DEFAULT_CONTROL = FixtureEdgeControl(tenant_id="fixture")
    return DEFAULT_CONTROL
