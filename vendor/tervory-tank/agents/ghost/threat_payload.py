"""ThreatPayload: the only shape that may cross from LLM triage into an execute path.

Ghost may read dirty logs and advisory text. Nothing from that side may raise a
shield, restart a room, or open a worker brief until it validates as ThreatPayload.
On any failure we burn: raise ThreatBurned with a fixed message and never echo the
rejected input (it may carry secrets or injection).

Founder tap is still required for Shields Up. This schema is the air-gap spike
(Phase 4), not auto-execute.
"""
from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .inventory import WATCHED_SITES
from .shields import KINDS, ShieldRequest

SCHEMA_VERSION = "ghost.threat.v1"
BURN_DETAIL = "Threat payload burned. No action was taken."
# Site keys and hosts Ghost is allowed to name (Tervory dogfood).
ALLOWED_SITES = frozenset({*WATCHED_SITES.keys(), *WATCHED_SITES.values()})
# Client hosts are never open-world. A payload naming a non-Tervory site passes
# only when THAT payload's tenant has the host in its saved settings (read at
# check time — not an in-memory set filled since restart).
_HOST_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$")
HOST_TAKEN_MSG = "That site is already registered by another client."

# Optional Store bound by Watch/Enforce at boot. accept_threat_payload reads
# the tenant's saved settings through this (or an explicit store= kwarg).
_SETTINGS_STORE = None


def bind_settings_store(store) -> None:
    """Watch/Enforce lifespan: point site checks at the durable settings store."""
    global _SETTINGS_STORE
    _SETTINGS_STORE = store


def clear_settings_store() -> None:
    """Test helper — drop the bound store."""
    global _SETTINGS_STORE
    _SETTINGS_STORE = None


def _normalize_host(value: str) -> str | None:
    host = (value or "").strip().lower().rstrip(".")
    if not host or "/" in host or host.startswith("http:") or host.startswith("https:"):
        return None
    if not _HOST_RE.fullmatch(host):
        return None
    return host


def hosts_claimed_by_settings(settings) -> list[str]:
    """Hostname list a tenant claims: watched_hosts + hostname-shaped site_name."""
    out: list[str] = []
    for item in getattr(settings, "watched_hosts", None) or []:
        host = _normalize_host(str(item))
        if host and host not in out:
            out.append(host)
    site_name = str(getattr(settings, "site_name", "") or "")
    host = _normalize_host(site_name)
    if host and host not in out:
        out.append(host)
    return out


def find_host_owner(store, host: str) -> str | None:
    """Which tenant already registered this host in saved settings, if any."""
    want = _normalize_host(host)
    if not want or store is None:
        return None
    try:
        rows = store.db.execute(
            "SELECT tenant, value FROM ghost_state WHERE key=?",
            ("autumbra_client_settings",),
        ).fetchall()
    except Exception:
        return None
    for tenant, value in rows:
        try:
            raw = json.loads(value) if isinstance(value, str) else value
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        claimed = []
        for item in raw.get("watched_hosts") or []:
            h = _normalize_host(str(item))
            if h:
                claimed.append(h)
        h = _normalize_host(str(raw.get("site_name") or ""))
        if h:
            claimed.append(h)
        if want in claimed:
            return str(tenant)
    return None


def assert_hosts_free_for_tenant(store, settings) -> None:
    """Refuse hosts already registered by another tenant (plain message)."""
    tid = str(getattr(settings, "tenant_id", "") or "").strip()
    for host in hosts_claimed_by_settings(settings):
        owner = find_host_owner(store, host)
        if owner and owner != tid:
            raise ValueError(HOST_TAKEN_MSG)


def _tenant_hosts_from_store(store, tenant_id: str) -> set[str]:
    """Read THAT tenant's saved settings and return its claimed hosts."""
    tid = (tenant_id or "").strip()
    if not tid or store is None:
        return set()
    try:
        raw = store.get_state(tid, "autumbra_client_settings", None)
    except Exception:
        return set()
    if not isinstance(raw, dict):
        return set()
    hosts: set[str] = set()
    for item in raw.get("watched_hosts") or []:
        h = _normalize_host(str(item))
        if h:
            hosts.add(h)
    h = _normalize_host(str(raw.get("site_name") or ""))
    if h:
        hosts.add(h)
    return hosts


def _tenant_hosts_from_mirror(tenant_id: str) -> set[str]:
    """Enforce path: read mirrored client_settings.json for this tenant's queue."""
    tid = (tenant_id or "").strip()
    if not tid:
        return set()
    try:
        from .tenant_queue import load_mirrored_settings, queue_path_for_tenant
        settings = load_mirrored_settings(queue_path_for_tenant(tid), fallback_tenant_id=tid)
    except Exception:
        # Single-queue env fallback.
        try:
            from .tenant_queue import load_mirrored_settings
            from .threat_queue import queue_root_from_env
            settings = load_mirrored_settings(queue_root_from_env(), fallback_tenant_id=tid)
        except Exception:
            return set()
    if settings is None:
        return set()
    return set(hosts_claimed_by_settings(settings))


def is_allowed_site(site: str, *, tenant_id: str | None = None, store=None) -> bool:
    """True for Tervory dogfood, or a host registered by THAT tenant in saved settings."""
    s = (site or "").strip().lower()
    if not s:
        return False
    if s in {x.lower() for x in ALLOWED_SITES}:
        return True
    tid = (tenant_id or "").strip() or None
    if not tid:
        return False
    st = store if store is not None else _SETTINGS_STORE
    if st is not None and s in _tenant_hosts_from_store(st, tid):
        return True
    # Enforce (or tests with mirrored settings) without Watch's sqlite.
    if s in _tenant_hosts_from_mirror(tid):
        return True
    return False


# Back-compat no-ops for older call sites / tests that still import these.
def register_client_watched_hosts(hosts) -> list[str]:
    """Deprecated: registration is via save_settings only. Returns normalized hosts."""
    added: list[str] = []
    for item in hosts or []:
        host = _normalize_host(str(item))
        if host and host not in added:
            added.append(host)
    return added


def clear_client_watched_hosts() -> None:
    """Deprecated no-op — registry is saved settings, not an in-memory set."""
    return None


def sync_client_watched_hosts_from_settings(settings) -> list[str]:
    """Deprecated: does not mutate a process-wide set. Returns hosts from settings."""
    return hosts_claimed_by_settings(settings)
# Patterns that mean "this is not a structured threat fact" — burn without parsing further.
INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "disregard previous",
    "system prompt",
    "you are now",
    "</",
    "<script",
    "```",
    "rm -rf",
    "/bin/sh",
    "powershell",
    "curl |",
    "wget |",
    "base64 -d",
    "os.system",
    "subprocess",
    "__import__",
)


class ThreatBurned(ValueError):
    """Raised when triage output is refused. Message is always BURN_DETAIL."""

    def __init__(self) -> None:
        super().__init__(BURN_DETAIL)


class ThreatAction(str, Enum):
    propose_shield = "propose_shield"
    open_match = "open_match"
    write_brief = "write_brief"
    undo_shield = "undo_shield"
    approve_shield = "approve_shield"


class ThreatSeverity(str, Enum):
    known_exploited = "known_exploited"
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThreatPayload(Strict):
    """Bounded facts only. No free-form commands, patches, or shell."""

    schema_version: Literal["ghost.threat.v1"] = SCHEMA_VERSION
    action: ThreatAction
    site: str = Field(min_length=1, max_length=80)
    kind: Literal["waf_managed", "rate_limit", "block_multipart_post", "challenge_bots", "block_ip", "revoke_sessions"] | None = None
    path: str | None = Field(default=None, max_length=120, pattern=r"^/[A-Za-z0-9._~/-]*$")
    address: str | None = Field(default=None, max_length=64, pattern=r"^[0-9A-Fa-f:.]*$")
    match_id: str | None = Field(default=None, max_length=200)
    cve_ids: list[str] = Field(default_factory=list, max_length=20)
    severity: ThreatSeverity = ThreatSeverity.unknown
    reason: str = Field(default="", max_length=300)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # Undo / approve door fields (ThreatPayload-only; never raw HTTP bytes).
    rule_id: str | None = Field(default=None, max_length=64, pattern=r"^[0-9A-Fa-f]*$")
    prior_receipt_id: str | None = Field(default=None, max_length=80)
    tenant_id: str | None = Field(default=None, max_length=80)
    source_data: dict[Literal["autumbra.shared_blocks"], int] = Field(default_factory=dict)
    advisory_url: str | None = Field(default=None, max_length=500)
    queued_at: str | None = Field(default=None, max_length=40)
    watch_decision_seconds: float | None = Field(default=None, ge=0, le=60, allow_inf_nan=False)

    @field_validator("source_data")
    @classmethod
    def source_counts_are_bounded(cls, values):
        if any(type(value) is not int or not 1 <= value <= 1_000_000 for value in values.values()):
            raise ValueError("invalid source count")
        return values

    @field_validator("site")
    @classmethod
    def site_must_be_shaped(cls, value: str) -> str:
        """Shape check only. Tenant ownership is enforced in accept_threat_payload."""
        site = value.strip().lower()
        if not site:
            raise ValueError("site empty")
        if site in {x.lower() for x in ALLOWED_SITES}:
            return site
        if _normalize_host(site):
            return site
        raise ValueError("site not allowlisted")

    @field_validator("cve_ids")
    @classmethod
    def cves_must_be_shaped(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in values:
            if not isinstance(item, str) or not CVE_RE.fullmatch(item):
                raise ValueError("invalid cve")
            cleaned.append(item)
        return cleaned

    @field_validator("reason")
    @classmethod
    def reason_must_be_plain(cls, value: str) -> str:
        text = value.strip()
        if any(ord(ch) < 32 for ch in text):
            raise ValueError("control characters")
        lowered = text.lower()
        if any(marker in lowered for marker in INJECTION_MARKERS):
            raise ValueError("injection marker")
        return text


def _preflight_raw(raw: bytes | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > 8_000:
            raise ThreatBurned()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ThreatBurned() from exc
    elif isinstance(raw, str):
        if len(raw) > 8_000:
            raise ThreatBurned()
        text = raw
    else:
        raise ThreatBurned()
    lowered = text.lower()
    if any(marker in lowered for marker in INJECTION_MARKERS):
        raise ThreatBurned()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ThreatBurned() from exc
    if not isinstance(data, dict):
        raise ThreatBurned()
    return data


def accept_threat_payload(
    raw: bytes | str | dict[str, Any],
    *,
    store=None,
) -> ThreatPayload:
    """Parse and validate triage output. Burns on any failure — never echoes input.

    Client (non-Tervory) sites pass only when registered by THAT payload's tenant
    in saved settings (read now — not an in-memory allowlist from an earlier load).
    """
    try:
        data = _preflight_raw(raw)
        payload = ThreatPayload.model_validate(data)
    except ThreatBurned:
        raise
    except (ValidationError, ValueError, TypeError):
        raise ThreatBurned() from None
    if not is_allowed_site(payload.site, tenant_id=payload.tenant_id, store=store):
        raise ThreatBurned()
    if payload.action == ThreatAction.propose_shield:
        if payload.kind is None or payload.kind not in KINDS:
            raise ThreatBurned()
    if payload.action == ThreatAction.undo_shield:
        if not (payload.rule_id or payload.prior_receipt_id):
            raise ThreatBurned()
    if payload.action == ThreatAction.approve_shield:
        if not payload.prior_receipt_id:
            raise ThreatBurned()
    return payload


def threat_to_shield_request(payload: ThreatPayload) -> ShieldRequest:
    """Map a validated ThreatPayload onto the existing shield propose shape.

    Caller must still go through Shields.propose → founder Shield tap. This does
    not apply anything.
    """
    if payload.action != ThreatAction.propose_shield or payload.kind is None:
        raise ThreatBurned()
    # Prefer host form when the triage named an app key.
    site = WATCHED_SITES.get(payload.site, payload.site)
    return ShieldRequest(
        site=site,
        kind=payload.kind,
        path=payload.path,
        address=payload.address,
        match_id=payload.match_id,
        reason=payload.reason or f"triage:{payload.severity.value}",
    )


def match_to_threat_payload(match: dict, kind: str, path: str | None = None) -> ThreatPayload:
    """Convert an open advisory match into a ThreatPayload for propose_shield.

    Burns when the match is not allowlisted / not shield-worthy. Never applies
    anything — caller must still propose → founder Shield tap.
    """
    if not isinstance(match, dict) or kind not in KINDS:
        raise ThreatBurned()
    affected = match.get("affected") or {}
    app = affected.get("app") if isinstance(affected, dict) else None
    if not isinstance(app, str) or not app:
        raise ThreatBurned()
    site = WATCHED_SITES.get(app)
    if site is None:
        raise ThreatBurned()
    severity = match.get("severity") if isinstance(match.get("severity"), dict) else {}
    if severity.get("known_exploited"):
        sev = ThreatSeverity.known_exploited
    else:
        label = str(severity.get("label") or "unknown")
        try:
            sev = ThreatSeverity(label)
        except ValueError:
            sev = ThreatSeverity.unknown
    # Serious matches only — same bar as proposals_for_match.
    if sev not in {ThreatSeverity.known_exploited, ThreatSeverity.critical, ThreatSeverity.high}:
        raise ThreatBurned()
    source_ids = match.get("source_ids") if isinstance(match.get("source_ids"), list) else []
    cves: list[str] = []
    for item in source_ids:
        if isinstance(item, str) and CVE_RE.fullmatch(item) and item not in cves:
            cves.append(item)
    advisory_id = match.get("advisory_id")
    if isinstance(advisory_id, str) and CVE_RE.fullmatch(advisory_id) and advisory_id not in cves:
        cves.insert(0, advisory_id)
    match_id = match.get("id")
    if match_id is not None and not isinstance(match_id, str):
        raise ThreatBurned()
    # Fixed plain reason — never copy free-form advisory summary (injection surface).
    reason = f"{advisory_id or 'advisory'} on {app}"
    raw = {
        "schema_version": SCHEMA_VERSION,
        "action": ThreatAction.propose_shield.value,
        "site": site,
        "kind": kind,
        "path": path,
        "match_id": match_id,
        "cve_ids": cves[:20],
        "severity": sev.value,
        "reason": reason[:300],
        "confidence": 1.0 if sev is ThreatSeverity.known_exploited else 0.85,
    }
    return accept_threat_payload(raw)
