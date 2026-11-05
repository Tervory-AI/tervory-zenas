"""Autumbra NeMo-shaped rails (tank-only stubs).

Colang/config under this package are inspired by vault
`/workspace/autumbra-oss-vault/nemo-guardrails` (see TERVORY.md). Runtime today
uses Pydantic ThreatPayload burn — not a live LLMRails session.

Stronger-than-stock: anything that is not a validated ThreatPayload burns before
Enforce. Enforce stays flag-OFF by default (agents.ghost.wraith.enabled).
"""
from __future__ import annotations

from pathlib import Path

from ..threat_payload import ThreatBurned, ThreatPayload, accept_threat_payload

RAILS_DIR = Path(__file__).resolve().parent
CONFIG_YML = RAILS_DIR / "config.yml"
AIRGAP_CO = RAILS_DIR / "airgap.co"


def ensure_threat_payload_before_enforce(raw: str | bytes) -> ThreatPayload:
    """Burn anything that is not ThreatPayload before Enforce may see it.

    This is the Autumbra air-gap output rail in code form. Dirty logs / free text
    never reach Enforce weapons — only a validated ThreatPayload crosses.
    """
    return accept_threat_payload(raw)


def rails_config_paths() -> dict[str, str]:
    """Paths to Autumbra rail stubs (for docs/tests; not loaded by nemoguardrails yet)."""
    return {
        "config_yml": str(CONFIG_YML),
        "airgap_co": str(AIRGAP_CO),
    }


__all__ = [
    "ThreatBurned",
    "ThreatPayload",
    "ensure_threat_payload_before_enforce",
    "rails_config_paths",
    "RAILS_DIR",
]
