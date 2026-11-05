"""Autumbra Enforce adapter — Terraform (dry plan only, flag OFF).

Vault pin: hashicorp/terraform @ 55988303ba5651e41e2c9097e208468c8eb3da4b
License: BSL-1.1 (IBM) — product-embed review before any hosted competitive use.
Templates under agents/ghost/configs/enforce/terraform/. Never runs terraform apply.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import accept_enforce_threat, armed, enabled
from ..threat_payload import ThreatAction, ThreatBurned, ThreatPayload

VAULT_SHA = "55988303ba5651e41e2c9097e208468c8eb3da4b"
UPSTREAM = "https://github.com/hashicorp/terraform"
VAULT_REL = "terraform"
LICENSE = "BSL-1.1"

ALLOWLISTED_MODULES = frozenset(
    {
        "cloudflare_waf_rule",
        "cloudflare_rate_limit",
    }
)

KIND_TO_MODULE = {
    "waf_managed": "cloudflare_waf_rule",
    "rate_limit": "cloudflare_rate_limit",
}


def adapter_meta() -> dict[str, Any]:
    return {
        "name": "terraform",
        "lane": "Autumbra Enforce",
        "vault_sha": VAULT_SHA,
        "upstream": UPSTREAM,
        "vault_rel": VAULT_REL,
        "license": LICENSE,
        "legal_note": (
            "BSL-1.1 — do not embed Terraform as a hosted competitive offering "
            "without product-legal review."
        ),
        "stronger_than_stock": [
            "ThreatPayload only (no raw shell)",
            "dry-run / plan only — never terraform apply from this adapter",
            "no remote state backend that phones home in packaged templates",
            "modules are .tf.tpl templates, not auto-applied stacks",
        ],
        "live_in_product": False,
        "apply_path": None,
    }


def modules_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "configs" / "enforce" / "terraform"


def module_path(module_id: str) -> Path:
    if module_id not in ALLOWLISTED_MODULES:
        raise ValueError("module not allowlisted")
    return modules_dir() / f"{module_id}.tf.tpl"


def _resolve_module_id(payload: ThreatPayload) -> str:
    if payload.action != ThreatAction.propose_shield or not payload.kind:
        raise ThreatBurned()
    module = KIND_TO_MODULE.get(payload.kind)
    if module is None:
        # block_multipart_post / challenge_bots are Ansible-only stubs for now.
        raise ThreatBurned()
    return module


def plan_from_threat(
    raw: bytes | str | dict[str, Any] | ThreatPayload,
    *,
    module_id: str | None = None,
) -> dict[str, Any]:
    """Return a dry plan dict only. Never runs terraform apply / destroy."""
    payload = accept_enforce_threat(raw)
    chosen = module_id or _resolve_module_id(payload)
    if chosen not in ALLOWLISTED_MODULES:
        raise ValueError("module not allowlisted")
    path = module_path(chosen)
    return {
        "product": "Autumbra Enforce",
        "adapter": "terraform",
        "mode": "dry_plan",
        "would_plan": [chosen],
        "module_path": str(path) if path.is_file() else None,
        "site": payload.site,
        "kind": payload.kind,
        "action": payload.action.value,
        "enabled": enabled(),
        "armed": armed(),
        "applied": False,
        "license": LICENSE,
        "note": (
            "Dry plan only. No terraform apply. No state backend phone-home. "
            "Live edge changes still require founder Shield tap on Autumbra Watch."
        ),
    }
