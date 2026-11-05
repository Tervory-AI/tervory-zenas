"""Who is calling. Keys live in the environment, compared as SHA-256 hashes.

Same rule as Tank's require_key: TANK_KEY_<AGENT> for an agent, TANK_API_KEY
for the Workspace. The database is not read. The raw key is never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import os

from fastapi import HTTPException

_AGENT_ID = r"^[a-z][a-z0-9_]{0,60}$"


def _same(left: bytes, right: bytes) -> bool:
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def agent_key_hashes() -> dict[str, bytes]:
    """SHA-256 of each non-empty TANK_KEY_<AGENT>. The suffix is the agent id."""
    found: dict[str, bytes] = {}
    for env_name, env_val in os.environ.items():
        if not env_name.startswith("TANK_KEY_"):
            continue
        value = env_val.strip()
        if not value:
            continue
        agent = env_name[len("TANK_KEY_"):].lower()
        if not agent or not all(ch.isalnum() or ch == "_" for ch in agent):
            continue
        found[agent] = hashlib.sha256(value.encode()).digest()
    return found


def identify(presented: str) -> str | None:
    """Return the agent id, or None for the Workspace key.

    Raises 401 when the header is missing or matches nothing. An empty
    TANK_API_KEY does not match an empty header.
    """
    if not presented:
        raise HTTPException(status_code=401, detail="missing or invalid X-Tank-Key")
    workspace = os.environ.get("TANK_API_KEY", "")
    try:
        presented_bytes = presented.encode()
        workspace_bytes = workspace.encode()
    except UnicodeError:
        raise HTTPException(status_code=401, detail="missing or invalid X-Tank-Key") from None
    if workspace and _same(presented_bytes, workspace_bytes):
        return None
    if presented_bytes:
        presented_hash = hashlib.sha256(presented_bytes).digest()
        for agent_name, digest in agent_key_hashes().items():
            if _same(digest, presented_hash):
                return agent_name
    raise HTTPException(status_code=401, detail="missing or invalid X-Tank-Key")
