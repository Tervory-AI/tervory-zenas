"""Autumbra Watch adapter — Fluent Bit (anomaly-only, onsite-first).

Vault pin: fluent/fluent-bit @ 05048e35d7106890006b7b593531bd51a16dfc93
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import DEFAULT_ANOMALY_THRESHOLD, AnomalyEvent, anomaly_only, handoff_threat
from ..threat_payload import ThreatPayload

VAULT_SHA = "05048e35d7106890006b7b593531bd51a16dfc93"
UPSTREAM = "https://github.com/fluent/fluent-bit"
PRESET_NAME = "fluent-bit-autumbra.conf"


def adapter_meta() -> dict[str, Any]:
    return {
        "name": "fluent_bit",
        "lane": "Autumbra Watch",
        "vault_sha": VAULT_SHA,
        "upstream": UPSTREAM,
        "stronger_than_stock": [
            "anomaly-only forward",
            "http_server Off (no metrics export unless operator opts in)",
            "onsite-first sinks",
            "ThreatPayload burn before Enforce handoff",
        ],
        "live_in_product": False,
    }


def preset_path() -> Path:
    return Path(__file__).resolve().parent.parent / "configs" / "watch" / PRESET_NAME


def render_preset(*, threshold: float = DEFAULT_ANOMALY_THRESHOLD) -> str:
    return f"""# Autumbra Watch — Fluent Bit preset (stronger-than-stock)
# Vault pin: {VAULT_SHA}
# Anomaly-only; onsite bus; metrics HTTP off.

[SERVICE]
    Flush           5
    Daemon          Off
    Log_Level       info
    HTTP_Server     Off
    Storage.Metrics Off
    Parsers_File    parsers.conf

# Operator-supplied onsite input. Dummy for local prove-it; replace in deploy.
[INPUT]
    Name            dummy
    Tag             autumbra.watch
    Dummy           {{"site":"coolify.tervory.com","signal":"anomaly","autumbra_score":0.0,"reason":"placeholder"}}
    Rate            60

# Keep only anomaly scores at/above threshold. Raw bodies are not forwarded.
[FILTER]
    Name            grep
    Match           autumbra.watch
    Regex           autumbra_score ^(0\\.[8-9]|0\\.9[0-9]|1(\\.0)?)

[FILTER]
    Name            modify
    Match           autumbra.watch
    Set             source fluent_bit
    Remove          log
    Remove          message
    Remove          raw

[OUTPUT]
    Name            http
    Match           autumbra.watch
    Host            127.0.0.1
    Port            8791
    URI             /ghost/watch/intake
    Format          json
    # Onsite Autumbra bus only — do not point at cloud collectors by default.
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
        body.setdefault("source", "fluent_bit")
        normalised.append(body)
    return anomaly_only(normalised, threshold=threshold)


def forward_to_threat(
    events: list[dict[str, Any] | AnomalyEvent],
    *,
    threshold: float = DEFAULT_ANOMALY_THRESHOLD,
) -> list[ThreatPayload]:
    out: list[ThreatPayload] = []
    for event in filter_anomalies(events, threshold=threshold):
        try:
            out.append(handoff_threat(event))
        except Exception:
            continue
    return out


def assert_telemetry_off(config_text: str) -> None:
    lowered = config_text.lower().replace(" ", "")
    if "http_serveron" in lowered or "http_server\ton" in config_text.lower().replace(" ", "\t"):
        # Accept only explicit Off in packaged preset.
        if "http_serveroff" not in lowered and "http_server\toff" not in config_text.lower().replace(" ", "\t"):
            raise ValueError("Fluent Bit HTTP_Server must be Off in Autumbra preset")
    if "http_server" in config_text.lower() and "off" not in config_text.lower():
        raise ValueError("Fluent Bit HTTP_Server must be Off in Autumbra preset")
    banned = ("fluentbit.io/metrics", "cloud.fluentbit.io", "honeycomb.io")
    for needle in banned:
        if needle in config_text.lower():
            raise ValueError(f"cloud telemetry sink not allowed: {needle}")
