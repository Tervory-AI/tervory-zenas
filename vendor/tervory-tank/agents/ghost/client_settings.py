"""Per-client Autumbra apply choices and optional connect hands.

Autumbra is agnostic: Cloudflare, Google Workspace, Shopify, host firewall,
etc. are optional client choices — not required. Three apply choices, stored
per client. Tervory never changes them. Default is "I approve each one".
Shown on every receipt.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApplyMode(str, Enum):
    approve_each = "I approve each one"
    auto_when_checks_pass = "Apply automatically when checks pass"
    never = "Never"


DEFAULT_MODE = ApplyMode.approve_each


class ClientAutumbraSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str = Field(min_length=1, max_length=80)
    site_name: str = Field(default="", max_length=120)
    owner_email: str = Field(default="", max_length=200)
    # IANA time zone for owner-facing pages (empty → UTC, and pages say "UTC").
    time_zone: str = Field(default="", max_length=80)
    # Separately for shields and for code fixes.
    shield_mode: ApplyMode = DEFAULT_MODE
    fix_mode: Literal[ApplyMode.approve_each, ApplyMode.never] = DEFAULT_MODE
    # When True, a single threat source may trigger a shield (default False = cross-check).
    act_on_single_source: bool = False
    # Hostnames this tenant has registered for shielding (e.g. shop-a.example.com).
    # ThreatPayload accepts dogfood Tervory hosts OR these registered client hosts.
    watched_hosts: list[str] = Field(default_factory=list, max_length=32)
    # Client's own CIDRs that must never be blocked.
    own_cidrs: list[str] = Field(default_factory=list, max_length=64)
    # Written-permission flag for red-team exercises against this client.
    red_team_permission: bool = False
    # Optional "hands" — client choice, like linking a tool. Autumbra stays agnostic.
    cloudflare_connected: bool = False
    google_workspace_connected: bool = False
    shopify_connected: bool = False
    host_firewall_connected: bool = False

    @field_validator("fix_mode", mode="before")
    @classmethod
    def fixes_always_need_approval(cls, value):
        # Read old automatic preferences safely; never carry them into execution.
        return ApplyMode.approve_each if value == ApplyMode.auto_when_checks_pass else value

    @field_validator("watched_hosts")
    @classmethod
    def hosts_must_be_shaped(cls, values: list[str]) -> list[str]:
        """Hostname labels only — no URLs, paths, or bare words."""
        host_re = re.compile(
            r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
        )
        out: list[str] = []
        for item in values or []:
            if not isinstance(item, str):
                raise ValueError("watched host must be a string")
            host = item.strip().lower().rstrip(".")
            if host.startswith("http://") or host.startswith("https://") or "/" in host:
                raise ValueError("watched host must be a hostname, not a URL")
            if not host_re.fullmatch(host):
                raise ValueError("watched host is not a valid hostname")
            if host not in out:
                out.append(host)
        return out

    @field_validator("time_zone")
    @classmethod
    def time_zone_must_be_iana_or_empty(cls, value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            return ""
        try:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        except ImportError as exc:  # pragma: no cover
            raise ValueError("time zone support unavailable") from exc
        try:
            ZoneInfo(raw)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("time zone must be a valid IANA name") from exc
        except Exception as exc:
            raise ValueError("time zone must be a valid IANA name") from exc
        return raw

    def receipt_stamp(self) -> dict[str, str]:
        """What every receipt must show about the client's choice."""
        return {
            "shield_mode": self.shield_mode.value,
            "fix_mode": self.fix_mode.value,
        }


def load_settings(store, tenant_id: str) -> ClientAutumbraSettings:
    """Read saved settings. Does not mutate any process-wide host allowlist."""
    raw = store.get_state(tenant_id, "autumbra_client_settings", None)
    if not isinstance(raw, dict):
        return ClientAutumbraSettings(tenant_id=tenant_id)
    if raw.get("fix_mode") == ApplyMode.auto_when_checks_pass.value:
        raw = {**raw, "fix_mode": ApplyMode.approve_each.value}
    try:
        return ClientAutumbraSettings.model_validate({**raw, "tenant_id": tenant_id})
    except Exception:
        return ClientAutumbraSettings(tenant_id=tenant_id)


def save_settings(store, settings: ClientAutumbraSettings) -> ClientAutumbraSettings:
    """Client (or founder-as-client) writes. Tervory automation must not call this.

    A host already registered by another tenant is refused with a plain message.
    """
    from .threat_payload import assert_hosts_free_for_tenant

    assert_hosts_free_for_tenant(store, settings)
    payload: dict[str, Any] = settings.model_dump()
    store.set_state(settings.tenant_id, "autumbra_client_settings", payload)
    return settings


def owner_time_zone(settings: ClientAutumbraSettings | None) -> str:
    """IANA zone for owner pages; UTC when unset."""
    if settings is None:
        return "UTC"
    raw = (getattr(settings, "time_zone", "") or "").strip()
    return raw or "UTC"


def format_owner_when(iso: str | None, settings: ClientAutumbraSettings | None = None) -> str:
    """Format a timestamp in the owner's time zone (label UTC when falling back)."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    text = (iso or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    zone_name = owner_time_zone(settings)
    try:
        local = moment.astimezone(ZoneInfo(zone_name))
    except Exception:
        local = moment.astimezone(timezone.utc)
        zone_name = "UTC"
    # Medium date + short time, then zone label (en-US-ish, portable).
    hour12 = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    stamp = f"{local.strftime('%b')} {local.day}, {local.year}, {hour12}:{local.strftime('%M')} {ampm}"
    return f"{stamp} {zone_name}"


def active_halves(settings: ClientAutumbraSettings) -> dict[str, bool]:
    """Which hands are lit. Stack always works; "block in seconds" needs an edge hand."""
    edge = bool(
        settings.cloudflare_connected
        or settings.host_firewall_connected
        or settings.shopify_connected
    )
    return {
        "edge": edge,
        "mailbox": bool(settings.google_workspace_connected),
        "stack": True,  # inventory / advisories / fix PRs / receipts always available
        "block_in_seconds": edge,  # plain copy: lights up when a hand is connected
    }


# Optional connect hands — client choice. No assumed provider. No Pro tier.
OPTIONAL_HANDS: tuple[str, ...] = (
    "cloudflare",
    "google_workspace",
    "shopify",
    "host_firewall",
)

_HAND_FIELD = {
    "cloudflare": "cloudflare_connected",
    "google_workspace": "google_workspace_connected",
    "shopify": "shopify_connected",
    "host_firewall": "host_firewall_connected",
}


def list_hands(settings: ClientAutumbraSettings) -> list[dict[str, Any]]:
    """Agnostic connect catalog. Never requires Cloudflare/Shopify/Google or Pro."""
    return [
        {
            "hand": hand,
            "connected": bool(getattr(settings, _HAND_FIELD[hand])),
            "required": False,
            "pro_required": False,
            "note": "Optional. You choose what to connect.",
        }
        for hand in OPTIONAL_HANDS
    ]


def set_hand(
    settings: ClientAutumbraSettings,
    hand: str,
    *,
    connected: bool,
) -> ClientAutumbraSettings:
    """Toggle one optional hand. Unknown hand fails. No Pro upsell."""
    key = str(hand or "").strip().lower()
    if key not in _HAND_FIELD:
        raise ValueError(
            f"Unknown hand {hand!r}. Optional hands: {', '.join(OPTIONAL_HANDS)}."
        )
    data = settings.model_dump()
    data[_HAND_FIELD[key]] = bool(connected)
    return ClientAutumbraSettings.model_validate(data)


def connect_copy(settings: ClientAutumbraSettings) -> dict[str, str | None]:
    """Client-facing copy. 'Block in seconds' claim only when an edge hand is connected."""
    halves = active_halves(settings)
    copy: dict[str, str | None] = {
        "logs": "We read your logs, acted, and deleted them. Nothing from your logs was kept.",
        "empty": "No work yet",
        "perimeter": (
            "Autumbra watches the edge as events pass. "
            "It does not import, copy or store your data."
        ),
        "agnostic": (
            "Autumbra is your 24/7 security guard. Cloudflare, Google, Shopify and "
            "host tools are optional — you choose what to connect."
        ),
        "cloudflare": (
            "A free Cloudflare account is enough for address blocks. "
            "We do not sell Cloudflare."
        ),
        "no_pro": "We never require a paid edge plan. Free is enough when you connect.",
    }
    if halves["block_in_seconds"]:
        copy["block_in_seconds"] = (
            "Blocking an attack in seconds is on — an edge tool is connected."
        )
        copy["block_in_seconds_gated"] = None
    else:
        copy["block_in_seconds"] = None
        copy["block_in_seconds_gated"] = (
            "Blocking an attack in seconds lights up when you connect an edge tool."
        )
    return copy
