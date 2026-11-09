"""Workspace product memory client: Hermes via the vendored chassis MemoryClient.

Never calls Tank /v1/memory. Never authenticates writes with the shared
TANK_API_KEY. User identity is the verified signed-in actor only — passed
through to chassis.MemoryClient / Hermes. This module does not filter by user.

Uses the merged chassis client and centrally enforced SQL user ownership.
"""
from __future__ import annotations

import hmac
import os
from typing import Any

# Explicit tenant-wide kinds for Workspace. Personal kinds always require user_id.
# Do not silently widen unscoped records.
TENANT_WIDE_KINDS: frozenset[str] = frozenset({"workspace.project"})

AGENT_NAME = "workspace"
AGENT_KEY_ENV = "TANK_KEY_WORKSPACE"
FALLBACK_KEY_ENV = "TANK_KEY"


def workspace_agent_key() -> str | None:
    for name in (AGENT_KEY_ENV, FALLBACK_KEY_ENV):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def refuse_shared_workspace_key(presented: str | None) -> None:
    shared = os.environ.get("TANK_API_KEY", "").strip()
    if not presented or not shared:
        return
    if hmac.compare_digest(presented.encode(), shared.encode()):
        raise PermissionError(
            "Workspace memory refuses the shared TANK_API_KEY; use TANK_KEY_WORKSPACE"
        )


def build_workspace_memory_client(
    *,
    http: Any = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> Any | None:
    """Construct vendored chassis.MemoryClient for Workspace → Hermes (agent key only)."""
    key = api_key if api_key is not None else workspace_agent_key()
    if not key:
        return None
    refuse_shared_workspace_key(key)
    try:
        from chassis.memory import MemoryClient
    except ModuleNotFoundError:
        from command_tools._shared_memory import MemoryClient
    import inspect

    kwargs: dict[str, Any] = {
        "base_url": base_url if base_url is not None else os.environ.get("HERMES_URL", "http://hermes:8000"),
        "api_key": key,
        "agent": AGENT_NAME,
        "http": http,
    }
    # tenant_wide_kinds is UNAPPLIED chassis proposal; pass only when supported.
    if "tenant_wide_kinds" in inspect.signature(MemoryClient.__init__).parameters:
        kwargs["tenant_wide_kinds"] = TENANT_WIDE_KINDS

    return MemoryClient(**kwargs)
