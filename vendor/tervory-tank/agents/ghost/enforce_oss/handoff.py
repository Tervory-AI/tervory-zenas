"""Watch air-gap → Autumbra Enforce OSS dry handoff.

Accepts only a burned ThreatPayload (via accept_enforce_threat). Returns dry JSON
plans for Ansible + Terraform adapters. Never applies, never shells, never calls HF.
"""
from __future__ import annotations

from typing import Any

from ..threat_payload import ThreatBurned, ThreatPayload
from . import ansible, terraform
from .common import accept_enforce_threat, armed, enabled


def receive_from_airgap(raw: bytes | str | dict[str, Any] | ThreatPayload) -> dict[str, Any]:
    """Build dry Enforce OSS JSON from a Watch air-gap ThreatPayload.

    Requires ENFORCE_OSS_ENABLED (route turns disable into 404). Armed is reported
    but never required for dry plans. Per-adapter ThreatBurned/ValueError → that
    plan is null; the handoff still succeeds.
    """
    if not enabled():
        raise RuntimeError("Autumbra Enforce OSS is disabled.")
    payload = accept_enforce_threat(raw)

    plans: dict[str, Any] = {"ansible": None, "terraform": None}
    try:
        plans["ansible"] = ansible.plan_from_threat(payload)
    except (ThreatBurned, ValueError):
        plans["ansible"] = None
    try:
        plans["terraform"] = terraform.plan_from_threat(payload)
    except (ThreatBurned, ValueError):
        plans["terraform"] = None

    return {
        "product": "Autumbra Enforce",
        "source": "watch_airgap",
        "enforced": False,
        "applied": False,
        "enabled": True,
        "armed": armed(),
        "payload": {
            "action": payload.action.value,
            "site": payload.site,
            "kind": payload.kind,
            "match_id": payload.match_id,
            "severity": payload.severity.value,
        },
        "plans": plans,
        "note": (
            "Dry JSON only. No apply. Founder Shield tap still required for live edge."
        ),
    }
