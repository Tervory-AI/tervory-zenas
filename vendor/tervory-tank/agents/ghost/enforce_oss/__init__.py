"""Autumbra Enforce OSS adapters (Ansible + Terraform).

Thin adapters over the staging vault at /workspace/autumbra-oss-vault — we do not
vendor multi-GB upstream trees. Stronger-than-stock defaults:

- ThreatPayload only (dirty inputs burn; never echoed)
- no raw shell / no subprocess apply
- dry-run / plan only until separately armed
- Cloudflare / firewall playbooks as templates (live CF stays on Shields Up)
- Watch air-gap handoff via receive_from_airgap → dry JSON only

Product copy: Autumbra Enforce / Autumbra Watch only.
Flags: ENFORCE_OSS_ENABLED / GHOST_ENFORCE_OSS_ENABLED (default OFF);
       ENFORCE_OSS_ARMED / autumbra_enforce_armed (default unarmed; needs enabled too).
"""
from __future__ import annotations

import os

from .common import accept_enforce_threat, armed, enabled
from .handoff import receive_from_airgap
from . import ansible, terraform

__all__ = [
    "accept_enforce_threat",
    "ansible",
    "armed",
    "enabled",
    "receive_from_airgap",
    "status",
    "terraform",
]


def status() -> dict:
    """Operator-facing status (Autumbra Enforce copy). Never claims apply is live."""
    return {
        "product": "Autumbra Enforce",
        "enabled": enabled(),
        "armed": armed(),
        "adapters": {
            "ansible": ansible.adapter_meta(),
            "terraform": terraform.adapter_meta(),
        },
        "handoff": {
            "source": "watch_airgap",
            "route": "POST /ghost/enforce/oss/plan",
            "triage_attach": "POST /ghost/triage/accept → enforce_oss when enabled",
            "apply": False,
        },
        "note": (
            "Adapters and dry-run templates only. No ansible-playbook / terraform apply "
            "from this module. Live edge changes still require founder Shield tap on "
            "Autumbra Watch Shields Up. Dual-LLM / GPU are not live here."
        ),
        "vault_primary": "ammosync@2.25.250.28:/home/ammosync/code-vault/autumbra-oss/",
        "vault_cache": os.environ.get(
            "AUTUMBRA_OSS_VAULT", "/workspace/autumbra-oss-vault"
        ),
    }
