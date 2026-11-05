"""Shared Autumbra Enforce helpers: flags, ThreatPayload gate, no raw shell."""
from __future__ import annotations

import os
from typing import Any

from ..threat_payload import (
    ThreatBurned,
    ThreatPayload,
    accept_threat_payload,
)

# Explicit shell / apply markers — burn or reject if triage tries to smuggle them.
SHELL_MARKERS = (
    "ansible-playbook",
    "terraform apply",
    "terraform destroy",
    "/bin/sh",
    "/bin/bash",
    "rm -rf",
    "os.system",
    "subprocess",
    "__import__",
    "curl |",
    "wget |",
    "base64 -d",
    "powershell",
)


def enabled() -> bool:
    for name in ("ENFORCE_OSS_ENABLED", "GHOST_ENFORCE_OSS_ENABLED"):
        if os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def armed() -> bool:
    """Armed only when BOTH the feature flag is on AND an explicit arm token is set.

    Defaults unarmed. Playbooks ship with autumbra_enforce_armed: false; this env
    gate is a second lock so templates cannot be applied by accident.
    """
    if not enabled():
        return False
    for name in ("ENFORCE_OSS_ARMED", "autumbra_enforce_armed"):
        if os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def _looks_like_shell(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in SHELL_MARKERS)


def accept_enforce_threat(raw: bytes | str | dict[str, Any] | ThreatPayload) -> ThreatPayload:
    """Accept only a burned ThreatPayload. Rejects raw shell markers; never echoes."""
    if isinstance(raw, ThreatPayload):
        payload = raw
    else:
        if isinstance(raw, (bytes, bytearray)):
            try:
                probe = raw.decode("utf-8", errors="replace")
            except Exception:
                raise ThreatBurned() from None
            if _looks_like_shell(probe):
                raise ThreatBurned()
        elif isinstance(raw, str):
            if _looks_like_shell(raw):
                raise ThreatBurned()
        elif isinstance(raw, dict):
            blob = " ".join(str(v) for v in raw.values() if isinstance(v, str))
            if _looks_like_shell(blob):
                raise ThreatBurned()
        payload = accept_threat_payload(raw)

    if _looks_like_shell(payload.reason or ""):
        raise ThreatBurned()
    return payload
