"""Autumbra Watch adapter — Vector (anomaly-only, telemetry off).

Vault pin: vectordotdev/vector @ bdd8842daf787497ae65f7e11e4eda6f72df8196
Configs live under agents/ghost/configs/watch/; we do not fork the upstream tree.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import DEFAULT_ANOMALY_THRESHOLD, AnomalyEvent, anomaly_only, handoff_threat
from ..threat_payload import ThreatPayload

VAULT_SHA = "bdd8842daf787497ae65f7e11e4eda6f72df8196"
UPSTREAM = "https://github.com/vectordotdev/vector"
PRESET_NAME = "vector-autumbra.yaml"


def adapter_meta() -> dict[str, Any]:
    return {
        "name": "vector",
        "lane": "Autumbra Watch",
        "vault_sha": VAULT_SHA,
        "upstream": UPSTREAM,
        "stronger_than_stock": [
            "anomaly-only forward",
            "telemetry / usage analytics off",
            "onsite Autumbra bus sink (no cloud default)",
            "ThreatPayload burn before Enforce handoff",
        ],
        "live_in_product": False,
    }


def preset_path() -> Path:
    return Path(__file__).resolve().parent.parent / "configs" / "watch" / PRESET_NAME


def render_preset(*, threshold: float = DEFAULT_ANOMALY_THRESHOLD) -> str:
    """Return the Autumbra Vector preset (YAML). Telemetry sinks omitted on purpose."""
    return f"""# Autumbra Watch — Vector preset (stronger-than-stock)
# Vault pin: {VAULT_SHA}
# Do not enable cloud sinks or usage analytics.
data_dir: "/var/lib/autumbra-watch/vector"

# Internal API / acknowledgements stay local. No phone-home.
api:
  enabled: false

# No `sources` that scrape foreign hosts. Dogfood sources are operator-supplied
# onsite (file / journald / socket). Placeholder stdin for local prove-it only.
sources:
  onsite_demo:
    type: "demo_logs"
    format: "json"
    interval: 60

transforms:
  anomaly_gate:
    type: "filter"
    inputs: ["onsite_demo"]
    condition: '.autumbra_score >= {threshold}'

  strip_raw:
    type: "remap"
    inputs: ["anomaly_gate"]
    source: |
      # Drop raw message bodies — keep bounded Watch facts only.
      . = {{
        "source": "vector",
        "site": string!(.site) ?? "unknown",
        "signal": string!(.signal) ?? "anomaly",
        "score": to_float!(.autumbra_score) ?? 0.0,
        "reason": truncate(string!(.reason) ?? "vector anomaly", 300) ?? "vector anomaly",
        "cve_ids": [],
        "confidence": to_float!(.autumbra_score) ?? 0.0
      }}

sinks:
  # Onsite Autumbra bus — localhost only. Never a cloud HTTP sink by default.
  autumbra_bus:
    type: "http"
    inputs: ["strip_raw"]
    uri: "http://127.0.0.1:8791/ghost/watch/intake"
    method: "post"
    encoding:
      codec: "json"
    batch:
      max_events: 1
"""


def filter_anomalies(
    events: list[dict[str, Any] | AnomalyEvent],
    *,
    threshold: float = DEFAULT_ANOMALY_THRESHOLD,
) -> list[AnomalyEvent]:
    normalised: list[dict[str, Any]] = []
    for item in events:
        if isinstance(item, AnomalyEvent):
            normalised.append(item.model_dump())
            continue
        body = dict(item)
        body.setdefault("source", "vector")
        normalised.append(body)
    return anomaly_only(normalised, threshold=threshold)


def forward_to_threat(
    events: list[dict[str, Any] | AnomalyEvent],
    *,
    threshold: float = DEFAULT_ANOMALY_THRESHOLD,
) -> list[ThreatPayload]:
    """Anomaly-only → ThreatPayload. Failures burn per-event (skipped, not echoed)."""
    out: list[ThreatPayload] = []
    for event in filter_anomalies(events, threshold=threshold):
        try:
            out.append(handoff_threat(event))
        except Exception:
            # ThreatBurned and friends — never echo; drop this event.
            continue
    return out


def assert_telemetry_off(config_text: str) -> None:
    """Raise ValueError if a config enables phone-home / cloud analytics sinks."""
    lowered = config_text.lower()
    banned = (
        "api.vector.dev",
        "timber.io",
        "usage_metrics",
        "datadog_metrics",  # cloud metrics by default path — not onsite bus
        "honeycomb",
    )
    for needle in banned:
        if needle in lowered:
            raise ValueError(f"telemetry sink not allowed in Autumbra Watch preset: {needle}")
    if "api:\n  enabled: true" in config_text.replace("\r\n", "\n"):
        # Local API can be useful for operators; Autumbra default is off.
        raise ValueError("Vector API must stay disabled in Autumbra packaged preset")
