"""Autumbra Watch adapter — Steampipe read-only SQL inventory.

Vault pin: turbot/steampipe @ 103201b1aad459788fb7d798637295600ed82179
Write-capable plugins are refused. Update/telemetry phone-home disabled in wrappers.
"""
from __future__ import annotations

import re
from typing import Any

from .common import AnomalyEvent
from ..inventory import Component

VAULT_SHA = "103201b1aad459788fb7d798637295600ed82179"
UPSTREAM = "https://github.com/turbot/steampipe"
PRESET_NAME = "steampipe.spc"

# Read-oriented plugin short names allowed in Autumbra Watch inventory profiles.
READ_ONLY_PLUGINS = frozenset({
    "aws",
    "gcp",
    "azure",
    "cloudflare",
    "github",
    "kubernetes",
    "terraform",  # state read; not apply
})
# Names / substrings that imply mutation — blocked in Autumbra path.
WRITE_PLUGIN_DENY = (
    "exec",
    "shell",
    "local",
    "docker",  # container exec risk in some plugins
    "kubernetes_exec",
    "write",
    "apply",
)

_WRITE_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|do)\b",
    re.IGNORECASE,
)


def adapter_meta() -> dict[str, Any]:
    return {
        "name": "steampipe",
        "lane": "Autumbra Watch",
        "vault_sha": VAULT_SHA,
        "upstream": UPSTREAM,
        "stronger_than_stock": [
            "read-only SQL inventory",
            "write plugins blocked",
            "update_check = false",
            "telemetry phone-home disabled",
        ],
        "live_in_product": False,
    }


def env_for_autumbra() -> dict[str, str]:
    """Environment for Autumbra Watch Steampipe wrappers (no phone-home)."""
    return {
        "STEAMPIPE_UPDATE_CHECK": "false",
        "STEAMPIPE_TELEMETRY": "none",
        "STEAMPIPE_DIAGNOSTICS": "",
        # Never auto-install arbitrary plugins in product path.
        "STEAMPIPE_INSTALL_DIR": "/var/lib/autumbra-watch/steampipe",
    }


def render_preset() -> str:
    plugins = "\n".join(f'  plugin "{name}" {{ source = "{name}" }}' for name in sorted(READ_ONLY_PLUGINS))
    return f"""# Autumbra Watch — Steampipe options (stronger-than-stock)
# Vault pin: {VAULT_SHA}
# Read-only inventory. No write plugins.

options "general" {{
  update_check = false
  telemetry    = "none"
}}

# Allowlisted read plugins only (connections still operator-supplied).
{plugins}
"""


def assert_plugin_allowed(plugin: str) -> None:
    name = plugin.strip().lower().removeprefix("steampipe-plugin-")
    for deny in WRITE_PLUGIN_DENY:
        if deny in name:
            raise ValueError(f"write-capable plugin blocked in Autumbra Watch: {plugin}")
    if name not in READ_ONLY_PLUGINS:
        raise ValueError(f"plugin not on Autumbra Watch read allowlist: {plugin}")


def assert_query_readonly(sql: str) -> None:
    text = sql.strip()
    if not text:
        raise ValueError("empty query")
    if _WRITE_SQL.search(text):
        raise ValueError("mutating SQL blocked in Autumbra Watch Steampipe path")
    # Single-statement SELECT / WITH only.
    if not re.match(r"^(with|select)\b", text, re.IGNORECASE):
        raise ValueError("only SELECT/WITH allowed in Autumbra Watch Steampipe path")


def rows_to_components(rows: list[dict[str, Any]], *, app: str = "watch") -> list[Component]:
    """Map bounded inventory rows → Watch inventory Component facts (versions/names only)."""
    out: list[Component] = []
    for row in rows[:5000]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("arn") or row.get("id") or "").strip()
        version = str(row.get("version") or row.get("engine_version") or "current").strip()
        kind = str(row.get("kind") or "software").strip()
        if not name or not version:
            continue
        try:
            out.append(Component(app=app, kind=kind if kind else "software", name=name[:200], version=version[:120]))
        except (TypeError, ValueError):
            continue
    return out


def inventory_anomaly(
    *,
    site: str,
    finding: str,
    score: float,
    cve_ids: list[str] | None = None,
) -> AnomalyEvent:
    """Build a Watch anomaly from a read-only inventory finding (no raw SQL echoed)."""
    return AnomalyEvent(
        source="steampipe",
        site=site,
        signal="inventory",
        score=score,
        cve_ids=list(cve_ids or [])[:20],
        reason=finding[:300],
        confidence=score,
    )
