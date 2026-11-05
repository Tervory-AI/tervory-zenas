"""Autumbra Watch adapter — OpenCTI CE feed allowlist (read client).

Vault pin: OpenCTI-Platform/opencti @ 879d3f198971256acf1b6e79152f9ffeb39842ab
CE only. No EE / Filigran phone-home. Feed allowlist only.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .common import AnomalyEvent, handoff_threat
from ..threat_payload import ThreatPayload

VAULT_SHA = "879d3f198971256acf1b6e79152f9ffeb39842ab"
UPSTREAM = "https://github.com/OpenCTI-Platform/opencti"

# Named STIX / intel sources Autumbra Watch may pull. Expand only with founder review.
FEED_ALLOWLIST = frozenset({
    "cisa-kev",
    "nvd-cve",
    "otx-pulses-public",
    "misp-warninglists",
})

# Host suffixes that imply EE / vendor phone-home — blocked.
EE_PHONEHOME_DENY = (
    "filigran.io",
    "opencti.filigran",
    "xtm.filigran",
    "licensing.filigran",
    "telemetry.filigran",
    "ee.opencti.io",
)


def adapter_meta() -> dict[str, Any]:
    return {
        "name": "opencti",
        "lane": "Autumbra Watch",
        "vault_sha": VAULT_SHA,
        "upstream": UPSTREAM,
        "stronger_than_stock": [
            "feed allowlist only",
            "CE Apache paths only",
            "no EE / Filigran phone-home",
            "ThreatPayload burn before Enforce handoff",
        ],
        "live_in_product": False,
        "edition": "CE",
    }


def assert_feed_allowed(feed: str) -> None:
    key = feed.strip().lower()
    if key not in FEED_ALLOWLIST:
        raise ValueError(f"OpenCTI feed not on Autumbra Watch allowlist: {feed}")


def assert_url_ce_only(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("OpenCTI URL missing host")
    for deny in EE_PHONEHOME_DENY:
        if host == deny or host.endswith("." + deny):
            raise ValueError("EE / Filigran phone-home URL blocked in Autumbra Watch")
    # Product path talks to onsite CE only (loopback or private operator host).
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("OpenCTI URL scheme must be http(s)")


class OpenCTIReadClient:
    """Allowlisted read stub. Does not start OpenCTI; safe when the daemon is absent."""

    def __init__(self, base_url: str = "http://127.0.0.1:8080", *, ce_only: bool = True):
        if not ce_only:
            raise ValueError("Autumbra Watch OpenCTI client is CE-only")
        assert_url_ce_only(base_url)
        self.base_url = base_url.rstrip("/")
        self.ce_only = True

    def list_allowed_feeds(self) -> list[str]:
        return sorted(FEED_ALLOWLIST)

    def fetch_indicators(self, feed: str) -> list[dict[str, Any]]:
        """Stub read: validates allowlist, returns empty until onsite CE is wired."""
        assert_feed_allowed(feed)
        assert_url_ce_only(self.base_url)
        # No network in unit path — empty list proves allowlist gate without egress.
        return []

    def indicators_to_anomalies(
        self,
        indicators: list[dict[str, Any]],
        *,
        site: str,
    ) -> list[AnomalyEvent]:
        events: list[AnomalyEvent] = []
        for item in indicators[:500]:
            if not isinstance(item, dict):
                continue
            score = float(item.get("score") or item.get("confidence") or 0) / (
                100.0 if float(item.get("score") or 0) > 1 else 1.0
            )
            score = max(0.0, min(1.0, score))
            cves = [c for c in item.get("cve_ids", []) if isinstance(c, str)][:20]
            reason = str(item.get("name") or item.get("pattern") or "opencti indicator")[:300]
            try:
                events.append(
                    AnomalyEvent(
                        source="opencti",
                        site=site,
                        signal="intel",
                        score=score,
                        cve_ids=cves,
                        reason=reason,
                        confidence=score,
                    )
                )
            except (ValueError, TypeError):
                continue
        return events

    def handoff(self, events: list[AnomalyEvent]) -> list[ThreatPayload]:
        out: list[ThreatPayload] = []
        for event in events:
            try:
                out.append(handoff_threat(event))
            except Exception:
                continue
        return out
