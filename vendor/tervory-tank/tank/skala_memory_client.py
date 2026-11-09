"""Skala production MemoryClient: own agent key only.

Field NAMES: HERMES_URL → base_url, TANK_KEY_SKALA → api_key (X-Tank-Key),
The agent is always skala. Never reads shared TANK_KEY / TANK_API_KEY for writes.
Chassis MemoryClient defaults to TANK_KEY when api_key is omitted — this factory
always passes an explicit key from TANK_KEY_SKALA and refuses if unset/empty.
"""
from __future__ import annotations

import os
from typing import Any

import httpx


class SkalaAgentKeyError(RuntimeError):
    """Skala memory refuses to start without TANK_KEY_SKALA (field name only)."""


def skala_agent_key_from_env() -> str:
    """Return TANK_KEY_SKALA value for MemoryClient; never fall back to shared keys."""
    # Read ONLY the Skala agent key env name. Do not consult TANK_KEY or TANK_API_KEY.
    key = os.environ.get("TANK_KEY_SKALA", "")
    if not isinstance(key, str) or not key.strip():
        raise SkalaAgentKeyError(
            "TANK_KEY_SKALA is required for Skala memory; shared TANK_KEY is not used"
        )
    # Refuse if someone aliased the shared workspace key into TANK_KEY_SKALA by mistake
    # when both are set and equal — product must use a distinct agent key.
    shared = os.environ.get("TANK_KEY", "")
    workspace = os.environ.get("TANK_API_KEY", "")
    stripped = key.strip()
    if shared and stripped == shared.strip():
        raise SkalaAgentKeyError(
            "TANK_KEY_SKALA must not equal shared TANK_KEY"
        )
    if workspace and stripped == workspace.strip():
        raise SkalaAgentKeyError(
            "TANK_KEY_SKALA must not equal shared TANK_API_KEY"
        )
    return stripped


def memory_client_for_skala(
    *,
    base_url: str | None = None,
    http: httpx.AsyncClient | None = None,
    user_id: str | None = None,
) -> Any:
    """Build chassis MemoryClient with explicit Skala agent key (no TANK_KEY default)."""
    from command_tools._shared_memory import MemoryClient

    key = skala_agent_key_from_env()
    url = base_url if base_url is not None else os.environ.get("HERMES_URL", "")
    return MemoryClient(
        base_url=(url or "http://hermes:8000"),
        api_key=key,  # explicit — chassis must not fall back to TANK_KEY
        agent="skala",
        user_id=user_id,
        http=http,
    )
