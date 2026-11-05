"""Shared Autumbra Watch intake helpers: anomaly gate + ThreatPayload burn."""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..inventory import WATCHED_SITES
from ..shields import KINDS
from ..threat_payload import (
    ThreatAction,
    ThreatBurned,
    ThreatPayload,
    ThreatSeverity,
    accept_threat_payload,
    match_to_threat_payload,
    threat_to_shield_request,
)

DEFAULT_ANOMALY_THRESHOLD = 0.8
# Propose path needs high/critical — same bar as match_to_threat_payload.
PROPOSE_SCORE_FLOOR = 0.9
# Bounded keys only — never free-form log bodies.
_SITE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_SIGNAL_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_HOST_TO_APP = {host.lower(): app for app, host in WATCHED_SITES.items()}
# Watch OSS signal → shield kind (panel Shields tile / practice mode).
SIGNAL_TO_KIND: dict[str, str] = {
    "waf": "waf_managed",
    "waf_managed": "waf_managed",
    "rate": "rate_limit",
    "rate_limit": "rate_limit",
    "multipart": "block_multipart_post",
    "block_multipart_post": "block_multipart_post",
    "bots": "challenge_bots",
    "challenge_bots": "challenge_bots",
}


def enabled() -> bool:
    for name in ("WATCH_OSS_ENABLED", "GHOST_WATCH_OSS_ENABLED"):
        if os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


class AnomalyEvent(BaseModel):
    """Bounded Watch intake fact. No raw payload, headers, or shell."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(pattern=r"^(vector|fluent_bit|steampipe|opencti)$")
    site: str = Field(min_length=1, max_length=80)
    signal: str = Field(min_length=1, max_length=64)
    score: float = Field(ge=0.0, le=1.0)
    cve_ids: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(default="", max_length=300)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("site")
    @classmethod
    def site_shaped(cls, value: str) -> str:
        site = value.strip().lower()
        if not _SITE_RE.fullmatch(site) and "." not in site:
            if not re.fullmatch(r"^[a-z0-9][a-z0-9.-]{0,79}$", site):
                raise ValueError("bad site")
        return site

    @field_validator("signal")
    @classmethod
    def signal_shaped(cls, value: str) -> str:
        signal = value.strip().lower()
        if not _SIGNAL_RE.fullmatch(signal):
            raise ValueError("bad signal")
        return signal

    @field_validator("reason")
    @classmethod
    def reason_plain(cls, value: str) -> str:
        text = value.strip()
        if any(ord(ch) < 32 for ch in text):
            raise ValueError("control characters")
        return text


def anomaly_only(
    events: Iterable[AnomalyEvent | dict[str, Any]],
    *,
    threshold: float = DEFAULT_ANOMALY_THRESHOLD,
) -> list[AnomalyEvent]:
    """Keep only events at/above the anomaly threshold. Drops the rest silently."""
    kept: list[AnomalyEvent] = []
    for item in events:
        try:
            event = item if isinstance(item, AnomalyEvent) else AnomalyEvent.model_validate(item)
        except (ValueError, TypeError):
            continue
        if event.score >= threshold:
            kept.append(event)
    return kept


def _as_event(event: AnomalyEvent | dict[str, Any]) -> AnomalyEvent:
    try:
        if isinstance(event, AnomalyEvent):
            return event
        return AnomalyEvent.model_validate(event)
    except (ValueError, TypeError):
        raise ThreatBurned() from None


def site_to_app(site: str) -> str | None:
    """Map host or app key onto a WATCHED_SITES app key."""
    key = site.strip().lower()
    if key in WATCHED_SITES:
        return key
    return _HOST_TO_APP.get(key)


def kind_for_signal(signal: str) -> str | None:
    kind = SIGNAL_TO_KIND.get(signal.strip().lower())
    if kind in KINDS:
        return kind
    return None


def _severity_for_score(score: float) -> ThreatSeverity:
    if score >= 0.95:
        return ThreatSeverity.critical
    if score >= PROPOSE_SCORE_FLOOR:
        return ThreatSeverity.high
    if score >= DEFAULT_ANOMALY_THRESHOLD:
        return ThreatSeverity.medium
    return ThreatSeverity.unknown


def handoff_threat(event: AnomalyEvent | dict[str, Any]) -> ThreatPayload:
    """Normalize an anomaly into open_match ThreatPayload. Burns on failure; never echoes."""
    event = _as_event(event)
    if event.score < DEFAULT_ANOMALY_THRESHOLD:
        raise ThreatBurned()
    severity = _severity_for_score(event.score)
    candidate = {
        "schema_version": "ghost.threat.v1",
        "action": ThreatAction.open_match.value,
        "site": event.site,
        "kind": None,
        "path": None,
        "match_id": f"watch-oss:{event.source}:{event.signal}"[:200],
        "cve_ids": list(event.cve_ids),
        "severity": severity.value,
        "reason": (event.reason or f"Autumbra Watch anomaly via {event.source}")[:300],
        "confidence": max(event.confidence, event.score),
    }
    return accept_threat_payload(candidate)


def anomaly_to_match(event: AnomalyEvent | dict[str, Any]) -> dict[str, Any]:
    """Build a synthetic open-match shape for match_to_threat_payload (fixtures / OSS).

    Does not write the store — caller may open_match. Never copies free-form reason
    into match summary used for propose (match_to_threat_payload builds its own).
    """
    event = _as_event(event)
    app = site_to_app(event.site)
    if app is None:
        raise ThreatBurned()
    if event.score < PROPOSE_SCORE_FLOOR:
        raise ThreatBurned()
    kind = kind_for_signal(event.signal)
    if kind is None:
        raise ThreatBurned()
    severity = _severity_for_score(event.score)
    if severity not in {ThreatSeverity.critical, ThreatSeverity.high, ThreatSeverity.known_exploited}:
        raise ThreatBurned()
    cve = event.cve_ids[0] if event.cve_ids else None
    advisory_id = cve or f"WATCH-OSS-{event.source.upper()}-{event.signal.upper()}"
    # Fingerprint is stable per source/signal/app/cve — open_match dedupes.
    fingerprint = f"{advisory_id}|{app}|watch_oss|{event.signal}"[:200]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": str(uuid.uuid4()),
        "fingerprint": fingerprint,
        "advisory_id": advisory_id if (cve and cve.startswith("CVE-")) else f"WATCH-OSS-{event.source}",
        "source_ids": [c for c in event.cve_ids if isinstance(c, str)][:20],
        "summary": "Autumbra Watch OSS anomaly",  # never used by match_to_threat_payload reason
        "affected": {
            "app": app,
            "kind": "software",
            "name": event.signal,
            "version": "unknown",
            "ecosystem": "generic",
        },
        "severity": {
            "cvss": 9.0 if severity is ThreatSeverity.critical else 7.5,
            "label": severity.value,
            "known_exploited": False,
        },
        "state": "open",
        "flagged_at": now,
        "watch_oss": {"source": event.source, "signal": event.signal, "score": event.score},
    }


def handoff_propose(event: AnomalyEvent | dict[str, Any]) -> ThreatPayload:
    """Anomaly → synthetic match → match_to_threat_payload (propose_shield).

    Uses the #281/#284 ThreatPayload burn unchanged. Caller must still
    Shields.propose → founder Shield tap (practice mode when no CF token).
    """
    event = _as_event(event)
    kind = kind_for_signal(event.signal)
    if kind is None:
        raise ThreatBurned()
    match = anomaly_to_match(event)
    return match_to_threat_payload(match, kind)


async def propose_from_anomaly(shields: Any, store: Any, tenant: str, event: AnomalyEvent | dict[str, Any]) -> dict[str, Any]:
    """Open synthetic match (deduped) → ThreatPayload → Shields.propose.

    Returns public-facing dict with match_id, shield, payload action. Never raises
    the shield. Burns skip via ThreatBurned from helpers.
    """
    event = _as_event(event)
    kind = kind_for_signal(event.signal)
    if kind is None:
        raise ThreatBurned()
    match_template = anomaly_to_match(event)
    opened, _created = store.open_match(tenant, match_template["fingerprint"], match_template)
    payload = match_to_threat_payload(opened, kind)
    shield = await shields.propose(threat_to_shield_request(payload))
    return {
        "action": payload.action.value,
        "site": payload.site,
        "severity": payload.severity.value,
        "match_id": payload.match_id,
        "kind": kind,
        "shield": shield,
        "note": "Proposed only. Founder must tap Shield on the Autumbra Watch panel (practice mode changes nothing).",
    }
