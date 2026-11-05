"""Autumbra Enforce adapter — Ansible (dry plan only, flag OFF).

Vault pin: ansible/ansible @ 4761c59c9639ff70d4db2b91b7361e121d482301
Playbooks live under agents/ghost/configs/enforce/playbooks/; we do not fork
the upstream tree. Never runs ansible-playbook.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import accept_enforce_threat, armed, enabled
from ..threat_payload import ThreatAction, ThreatBurned, ThreatPayload

VAULT_SHA = "4761c59c9639ff70d4db2b91b7361e121d482301"
UPSTREAM = "https://github.com/ansible/ansible"
VAULT_REL = "ansible"

# Allowlisted playbook ids only — map ThreatPayload.kind / action → stub.
ALLOWLISTED_PLAYBOOKS = frozenset(
    {
        "cf_waf_managed",
        "cf_rate_limit",
        "cf_block_multipart_post",
        "cf_challenge_bots",
        "host_iptables_brief",
    }
)

KIND_TO_PLAYBOOK = {
    "waf_managed": "cf_waf_managed",
    "rate_limit": "cf_rate_limit",
    "block_multipart_post": "cf_block_multipart_post",
    "challenge_bots": "cf_challenge_bots",
}


def adapter_meta() -> dict[str, Any]:
    return {
        "name": "ansible",
        "lane": "Autumbra Enforce",
        "vault_sha": VAULT_SHA,
        "upstream": UPSTREAM,
        "vault_rel": VAULT_REL,
        "stronger_than_stock": [
            "ThreatPayload only (no raw shell)",
            "dry-run / plan only — never ansible-playbook from this adapter",
            "autumbra_enforce_armed: false on every packaged playbook",
            "no shell/command/raw/script Ansible modules in templates",
            "Galaxy auto-fetch off (gathering=explicit)",
        ],
        "live_in_product": False,
        "apply_path": None,
    }


def playbooks_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "configs" / "enforce" / "playbooks"


def playbook_path(playbook_id: str) -> Path:
    if playbook_id not in ALLOWLISTED_PLAYBOOKS:
        raise ValueError("playbook not allowlisted")
    return playbooks_dir() / f"{playbook_id}.yml"


def _resolve_playbook_id(payload: ThreatPayload) -> str:
    if payload.action == ThreatAction.write_brief:
        return "host_iptables_brief"
    if payload.action == ThreatAction.propose_shield and payload.kind:
        playbook = KIND_TO_PLAYBOOK.get(payload.kind)
        if playbook is None:
            raise ThreatBurned()
        return playbook
    # open_match has no Enforce playbook — burn rather than invent apply.
    raise ThreatBurned()


def plan_from_threat(
    raw: bytes | str | dict[str, Any] | ThreatPayload,
    *,
    playbook_id: str | None = None,
) -> dict[str, Any]:
    """Return a dry plan dict only. Never executes ansible-playbook.

    Rejects unknown playbook ids and burns dirty ThreatPayload input.
    """
    payload = accept_enforce_threat(raw)
    chosen = playbook_id or _resolve_playbook_id(payload)
    if chosen not in ALLOWLISTED_PLAYBOOKS:
        raise ValueError("playbook not allowlisted")
    path = playbook_path(chosen)
    return {
        "product": "Autumbra Enforce",
        "adapter": "ansible",
        "mode": "dry_plan",
        "would_run": [chosen],
        "playbook_path": str(path) if path.is_file() else None,
        "site": payload.site,
        "kind": payload.kind,
        "action": payload.action.value,
        "enabled": enabled(),
        "armed": armed(),
        "applied": False,
        "note": (
            "Dry plan only. No ansible-playbook. No host or Cloudflare change. "
            "Founder Shield tap remains the live edge path (Autumbra Watch)."
        ),
    }
