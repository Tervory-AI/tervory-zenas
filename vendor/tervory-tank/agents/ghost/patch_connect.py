"""Autumbra CONNECT: advisory match → ThreatPayload-shaped facts → PatchBrief → draft PR plan.

Product: Autumbra (Watch/Enforce). Internal room path: agents/ghost.
Deterministic — no LLM, no HF Inference. Flag OFF dry-run via open_dep_bump_pr.
Never merges. Never deploys. Burns with fixed detail; never echoes rejected input.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .inventory import WATCHED_SITES
from .patch_brief import (
    CVE_RE,
    DOGFOOD_REPOS,
    INJECTION_MARKERS,
    PACKAGE_RE,
    VERSION_RE,
    ADVISORY_RE,
    PatchBrief,
    PatchBurned,
    PatchSeverity,
    accept_patch_brief,
    map_ecosystem,
    passes_severity_bar,
)
from .patch_pr import open_dep_bump_pr

DEFAULT_DOGFOOD_REPO = "Tervory-AI/tervory-tank"
ALLOWED_SITES = frozenset({*WATCHED_SITES.keys(), *WATCHED_SITES.values()})
FORBIDDEN_KEYS = frozenset({"merge", "auto_merge", "deploy", "auto_deploy", "shell", "cmd", "command"})


class ConnectAction(str, Enum):
    open_dep_bump_pr = "open_dep_bump_pr"
    write_brief = "write_brief"
    open_match = "open_match"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThreatConnect(Strict):
    """Minimal connect shape when ThreatPayload is not on this branch.

    Allows Watch triage facts + allowlisted patch fields so advisory → brief
    can land before #281 merges. extra=forbid; no merge/deploy flags.
    """

    action: ConnectAction
    site: str = Field(min_length=1, max_length=80)
    package: str = Field(min_length=1, max_length=200)
    ecosystem: str = Field(min_length=1, max_length=40)
    from_version: str = Field(min_length=1, max_length=80)
    to_version: str = Field(min_length=1, max_length=80)
    repo: str = Field(default=DEFAULT_DOGFOOD_REPO, min_length=3, max_length=120)
    cve_ids: list[str] = Field(default_factory=list, max_length=20)
    advisory_ids: list[str] = Field(default_factory=list, max_length=20)
    severity: PatchSeverity = PatchSeverity.medium
    reason: str = Field(default="", max_length=300)
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    match_id: str | None = Field(default=None, max_length=200)
    schema_version: Literal["ghost.threat.v1", "ghost.patch.v1"] | None = None

    @field_validator("site")
    @classmethod
    def site_must_be_ours(cls, value: str) -> str:
        site = value.strip().lower()
        if site not in {s.lower() for s in ALLOWED_SITES}:
            raise ValueError("site not allowlisted")
        return site

    @field_validator("repo")
    @classmethod
    def repo_must_be_dogfood(cls, value: str) -> str:
        repo = value.strip()
        if repo not in DOGFOOD_REPOS:
            raise ValueError("repo not allowlisted")
        return repo

    @field_validator("package")
    @classmethod
    def package_must_be_shaped(cls, value: str) -> str:
        name = value.strip()
        if not PACKAGE_RE.fullmatch(name):
            raise ValueError("invalid package")
        return name

    @field_validator("from_version", "to_version")
    @classmethod
    def versions_must_be_shaped(cls, value: str) -> str:
        ver = value.strip()
        if not VERSION_RE.fullmatch(ver):
            raise ValueError("invalid version")
        return ver

    @field_validator("cve_ids")
    @classmethod
    def cves_must_be_shaped(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in values:
            if not isinstance(item, str) or not CVE_RE.fullmatch(item):
                raise ValueError("invalid cve")
            cleaned.append(item)
        return cleaned

    @field_validator("advisory_ids")
    @classmethod
    def advisories_must_be_shaped(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in values:
            if not isinstance(item, str) or not ADVISORY_RE.fullmatch(item):
                raise ValueError("invalid advisory")
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


def _reject_forbidden(data: dict[str, Any]) -> None:
    if FORBIDDEN_KEYS.intersection(data):
        raise PatchBurned()


def _scan_injection(text: str) -> None:
    lowered = text.lower()
    if any(marker in lowered for marker in INJECTION_MARKERS):
        raise PatchBurned()


def _preflight_dict(raw: bytes | str | dict[str, Any] | Any) -> dict[str, Any]:
    if hasattr(raw, "model_dump") and callable(raw.model_dump):
        try:
            dumped = raw.model_dump()
        except Exception as exc:
            raise PatchBurned() from exc
        if not isinstance(dumped, dict):
            raise PatchBurned()
        _reject_forbidden(dumped)
        return dumped
    if isinstance(raw, dict):
        _reject_forbidden(raw)
        return raw
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > 12_000:
            raise PatchBurned()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PatchBurned() from exc
    elif isinstance(raw, str):
        if len(raw) > 12_000:
            raise PatchBurned()
        text = raw
    else:
        raise PatchBurned()
    _scan_injection(text)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise PatchBurned() from exc
    if not isinstance(data, dict):
        raise PatchBurned()
    _reject_forbidden(data)
    return data


def _resolve_site(app_or_host: str) -> str:
    key = (app_or_host or "").strip().lower()
    if not key:
        raise PatchBurned()
    if key in WATCHED_SITES:
        return WATCHED_SITES[key]
    if key in {h.lower() for h in WATCHED_SITES.values()}:
        return key
    raise PatchBurned()


def _severity_from_match(severity: Any) -> PatchSeverity:
    if not isinstance(severity, dict):
        raise PatchBurned()
    if severity.get("known_exploited"):
        return PatchSeverity.known_exploited
    label = str(severity.get("label") or "").strip().lower()
    if label == "moderate":
        label = "medium"
    try:
        return PatchSeverity(label)
    except ValueError as exc:
        raise PatchBurned() from exc


def _ids_from_match(match: dict) -> tuple[list[str], list[str]]:
    cves: list[str] = []
    advisories: list[str] = []
    source_ids = match.get("source_ids") if isinstance(match.get("source_ids"), list) else []
    for item in source_ids:
        if not isinstance(item, str):
            continue
        if CVE_RE.fullmatch(item) and item not in cves:
            cves.append(item)
        elif ADVISORY_RE.fullmatch(item) and item not in advisories:
            advisories.append(item)
    advisory_id = match.get("advisory_id")
    if isinstance(advisory_id, str):
        if CVE_RE.fullmatch(advisory_id) and advisory_id not in cves:
            cves.insert(0, advisory_id)
        elif ADVISORY_RE.fullmatch(advisory_id) and advisory_id not in advisories:
            advisories.insert(0, advisory_id)
    return cves[:20], advisories[:20]


def _confidence_for(sev: PatchSeverity) -> float:
    if sev is PatchSeverity.known_exploited:
        return 1.0
    if sev in {PatchSeverity.critical, PatchSeverity.high}:
        return 0.85
    if sev is PatchSeverity.medium:
        return 0.8
    return 0.0


def match_to_patch_brief(match: dict) -> PatchBrief:
    """Map a GHOST-2 advisory match dict onto ghost.patch.v1. Burns on fail."""
    if not isinstance(match, dict):
        raise PatchBurned()
    _reject_forbidden(match)
    # Scan string leaves for injection; never copy free-form summary into reason.
    for key in ("summary", "fingerprint", "advisory_id"):
        val = match.get(key)
        if isinstance(val, str):
            _scan_injection(val)
    affected = match.get("affected")
    if not isinstance(affected, dict):
        raise PatchBurned()
    app = affected.get("app")
    if not isinstance(app, str):
        raise PatchBurned()
    site = _resolve_site(app)
    kind = affected.get("kind")
    if kind != "package":
        raise PatchBurned()
    name = affected.get("name")
    version = affected.get("version")
    eco_raw = affected.get("ecosystem")
    if not isinstance(name, str) or not isinstance(version, str) or not isinstance(eco_raw, str):
        raise PatchBurned()
    try:
        ecosystem = map_ecosystem(eco_raw)
    except PatchBurned:
        raise
    what = match.get("what_to_do") if isinstance(match.get("what_to_do"), dict) else {}
    to_version = what.get("patch_to") or what.get("fixed_version") or match.get("fixed_version")
    if not isinstance(to_version, str) or not to_version.strip():
        raise PatchBurned()
    sev = _severity_from_match(match.get("severity"))
    cves, advisories = _ids_from_match(match)
    # Fixed plain reason — never echo advisory summary (injection surface).
    advisory_id = match.get("advisory_id") if isinstance(match.get("advisory_id"), str) else "advisory"
    reason = f"{advisory_id} on {app}: bump {name}"
    raw = {
        "schema_version": "ghost.patch.v1",
        "action": "open_dep_bump_pr",
        "site": site,
        "repo": DEFAULT_DOGFOOD_REPO,
        "ecosystem": ecosystem.value,
        "package": name,
        "from_version": version,
        "to_version": to_version.strip(),
        "cve_ids": cves,
        "advisory_ids": advisories,
        "severity": sev.value,
        "reason": reason[:300],
        "confidence": _confidence_for(sev),
    }
    return accept_patch_brief(raw)


def _try_import_accept_threat():
    try:
        from .threat_payload import accept_threat_payload  # type: ignore
    except ImportError:
        return None
    return accept_threat_payload


def threat_to_patch_brief(raw_or_payload: Any) -> PatchBrief:
    """Map ThreatPayload-shaped (or ThreatConnect) facts onto PatchBrief.

    When agents.ghost.threat_payload is importable, optionally validate via
    accept_threat_payload first. On this branch ThreatPayload is absent, so
    ThreatConnect (extra=forbid) validates connect fields only.
    Never merges. Burns without echoing input.
    """
    try:
        data = _preflight_dict(raw_or_payload)
    except PatchBurned:
        raise
    accept_threat = _try_import_accept_threat()
    # Prefer open_dep_bump_pr; allow write_brief/open_match when patch fields present.
    action = data.get("action")
    has_patch_fields = all(
        isinstance(data.get(k), str) and data.get(k)
        for k in ("package", "ecosystem", "from_version", "to_version")
    )
    if accept_threat is not None and not has_patch_fields:
        # Pure ThreatPayload without patch fields cannot become a dep-bump brief.
        try:
            accept_threat(data)
        except Exception:
            pass
        raise PatchBurned()
    if action == "open_dep_bump_pr" or (
        action in {"write_brief", "open_match"} and has_patch_fields
    ):
        try:
            conn = ThreatConnect.model_validate(
                {
                    **data,
                    "repo": data.get("repo") or DEFAULT_DOGFOOD_REPO,
                    "action": action if action in {a.value for a in ConnectAction} else "open_dep_bump_pr",
                }
            )
        except (ValidationError, ValueError, TypeError):
            raise PatchBurned() from None
        try:
            ecosystem = map_ecosystem(conn.ecosystem)
        except PatchBurned:
            raise
        site = WATCHED_SITES.get(conn.site, conn.site)
        brief_raw = {
            "schema_version": "ghost.patch.v1",
            "action": "open_dep_bump_pr",
            "site": site,
            "repo": conn.repo,
            "ecosystem": ecosystem.value,
            "package": conn.package,
            "from_version": conn.from_version,
            "to_version": conn.to_version,
            "cve_ids": conn.cve_ids,
            "advisory_ids": conn.advisory_ids,
            "severity": conn.severity.value,
            "reason": conn.reason or f"Watch connect: bump {conn.package}",
            "confidence": conn.confidence,
        }
        return accept_patch_brief(brief_raw)
    raise PatchBurned()


def connect_match_to_plan(match: dict, *, open_pr: bool = True) -> dict[str, Any]:
    """match → PatchBrief → severity bar → open_dep_bump_pr (usually dry-run).

    Returns {connected: true, brief: ..., result: ...} or raises PatchBurned.
    """
    brief = match_to_patch_brief(match)
    if not passes_severity_bar(brief):
        raise PatchBurned()
    result: dict[str, Any]
    if open_pr:
        result = open_dep_bump_pr(brief)
    else:
        from .patch_pr import plan_bump

        result = {
            "mode": "planned",
            "would_open": plan_bump(brief),
            "note": "open_pr=False; plan only.",
            "draft": True,
        }
    return {
        "connected": True,
        "brief": brief.model_dump(mode="json"),
        "result": result,
    }
