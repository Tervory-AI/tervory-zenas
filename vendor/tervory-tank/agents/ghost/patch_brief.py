"""Autumbra patch brief (schema ghost.patch.v1): advisory match → dep-bump PR plan.

Product: Autumbra (Watch/Enforce). Internal room path: agents/ghost.
Burns on fail with a fixed detail string; never echoes rejected input.
Never carries merge/deploy flags.
"""
from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .inventory import WATCHED_SITES

SCHEMA_VERSION = "ghost.patch.v1"
BURN_DETAIL = "Patch brief burned. No action was taken."
ALLOWED_SITES = frozenset({*WATCHED_SITES.keys(), *WATCHED_SITES.values()})
DOGFOOD_REPOS = frozenset({"Tervory-AI/tervory-tank"})
CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$")
ADVISORY_RE = re.compile(r"^(GHSA-[A-Za-z0-9-]{3,}|OSV-[A-Za-z0-9._-]{3,}|CVE-\d{4}-\d{4,})$")
PACKAGE_RE = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9@/._+-]{0,199}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9._+~:!*-]{1,80}$")
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


class PatchBurned(ValueError):
    """Raised when a patch brief is refused. Message is always BURN_DETAIL."""

    def __init__(self) -> None:
        super().__init__(BURN_DETAIL)


class PatchAction(str, Enum):
    open_dep_bump_pr = "open_dep_bump_pr"


class PatchSeverity(str, Enum):
    known_exploited = "known_exploited"
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class PatchEcosystem(str, Enum):
    npm = "npm"
    pip = "pip"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PatchBrief(Strict):
    """Bounded dep-bump facts only. No free-form shell, merge, or deploy flags."""

    schema_version: Literal["ghost.patch.v1"] = SCHEMA_VERSION
    action: Literal["open_dep_bump_pr"] = "open_dep_bump_pr"
    site: str = Field(min_length=1, max_length=80)
    repo: str = Field(min_length=3, max_length=120)
    ecosystem: PatchEcosystem
    package: str = Field(min_length=1, max_length=200)
    from_version: str = Field(min_length=1, max_length=80)
    to_version: str = Field(min_length=1, max_length=80)
    cve_ids: list[str] = Field(default_factory=list, max_length=20)
    advisory_ids: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(default="", max_length=300)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    severity: PatchSeverity = PatchSeverity.medium

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


def map_ecosystem(raw: str) -> PatchEcosystem:
    """Map inventory ecosystem labels onto patch brief ecosystems."""
    key = (raw or "").strip().lower()
    if key in {"pip", "pypi"}:
        return PatchEcosystem.pip
    if key == "npm":
        return PatchEcosystem.npm
    raise PatchBurned()


def _preflight_raw(raw: bytes | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > 8_000:
            raise PatchBurned()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PatchBurned() from exc
    elif isinstance(raw, str):
        if len(raw) > 8_000:
            raise PatchBurned()
        text = raw
    else:
        raise PatchBurned()
    lowered = text.lower()
    if any(marker in lowered for marker in INJECTION_MARKERS):
        raise PatchBurned()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise PatchBurned() from exc
    if not isinstance(data, dict):
        raise PatchBurned()
    return data


def accept_patch_brief(raw: bytes | str | dict[str, Any]) -> PatchBrief:
    """Parse and validate a patch brief. Burns on any failure — never echoes input."""
    try:
        data = _preflight_raw(raw)
        # Reject merge/deploy flags even if somehow nested before model validate.
        for forbidden in ("merge", "auto_merge", "deploy", "auto_deploy"):
            if forbidden in data:
                raise PatchBurned()
        brief = PatchBrief.model_validate(data)
    except PatchBurned:
        raise
    except (ValidationError, ValueError, TypeError):
        raise PatchBurned() from None
    if brief.action != PatchAction.open_dep_bump_pr.value:
        raise PatchBurned()
    return brief


def passes_severity_bar(brief: PatchBrief) -> bool:
    """Critical/High/KEV always; Medium only if confidence ≥ 0.8; Low never."""
    if brief.severity in {
        PatchSeverity.critical,
        PatchSeverity.high,
        PatchSeverity.known_exploited,
    }:
        return True
    if brief.severity is PatchSeverity.medium:
        return brief.confidence >= 0.8
    return False
