"""Autumbra Watch OSS intake adapters (Vector / Fluent Bit / Steampipe / OpenCTI).

Thin adapters over the staging vault at /workspace/autumbra-oss-vault — we do not
vendor multi-GB upstream trees. Stronger-than-stock defaults:

- anomaly-only forward (raw client payloads never leave the onsite bus)
- telemetry / phone-home off
- ThreatPayload burn before any Watch→Enforce handoff
- Steampipe read-only; OpenCTI CE + feed allowlist

Product copy: Autumbra Watch / Autumbra Enforce only.
Flag: WATCH_OSS_ENABLED / GHOST_WATCH_OSS_ENABLED (default OFF).

Wire (on #284 stack): anomaly → synthetic match → ThreatPayload → Shields.propose
→ founder panel (practice mode). Aligns with Air Gap #286 (guard flag OFF; schema
unchanged). No live HF.
"""
from __future__ import annotations

import os

from .common import (
    DEFAULT_ANOMALY_THRESHOLD,
    PROPOSE_SCORE_FLOOR,
    SIGNAL_TO_KIND,
    AnomalyEvent,
    anomaly_only,
    anomaly_to_match,
    enabled,
    handoff_propose,
    handoff_threat,
    kind_for_signal,
    propose_from_anomaly,
    site_to_app,
)
from . import fluentbit, opencti, steampipe, vector

__all__ = [
    "DEFAULT_ANOMALY_THRESHOLD",
    "PROPOSE_SCORE_FLOOR",
    "SIGNAL_TO_KIND",
    "AnomalyEvent",
    "anomaly_only",
    "anomaly_to_match",
    "enabled",
    "fluentbit",
    "handoff_propose",
    "handoff_threat",
    "kind_for_signal",
    "opencti",
    "propose_from_anomaly",
    "site_to_app",
    "steampipe",
    "vector",
]


def status() -> dict:
    """Operator-facing status (Autumbra Watch copy). Never claims upstream is live."""
    return {
        "product": "Autumbra Watch",
        "enabled": enabled(),
        "wire": "anomaly→match→ThreatPayload→Shields.propose→panel (practice)",
        "stack_base": "PR #292 tip a374a48 (#281→#284→#286→#288 connected dogfood)",
        "adapters": {
            "vector": vector.adapter_meta(),
            "fluent_bit": fluentbit.adapter_meta(),
            "steampipe": steampipe.adapter_meta(),
            "opencti": opencti.adapter_meta(),
        },
        "note": (
            "Adapters and presets only. Upstream daemons are not started by this "
            "module. Dual-LLM / Wazuh triage are not live here. Guardrails (#286) "
            "stay flag-OFF; ThreatPayload burn still protects the handoff."
        ),
        "vault_primary": "ammosync@2.25.250.28:/home/ammosync/code-vault/autumbra-oss/",
        "vault_cache": os.environ.get(
            "AUTUMBRA_OSS_VAULT", "/workspace/autumbra-oss-vault"
        ),
    }
