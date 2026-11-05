"""Perimeter, not import. After a run, no client payload may remain on Tervory's side."""
from __future__ import annotations

from typing import Any, Iterable

# Markers that would mean we stored client content (forbidden).
FORBIDDEN_RESIDUAL_KEYS = (
    "raw_log",
    "client_payload",
    "mailbox_body",
    "request_body",
    "webhook_body",
    "log_line",
)


def receipt_allowed_keys() -> frozenset[str]:
    return frozenset({
        "id", "product", "role", "at", "time", "action", "addresses_blocked",
        "advisory_matched", "client_setting", "shield_mode", "fix_mode",
        "source_credits", "budgets", "outcome", "kind", "site", "tenant_id",
        "undo", "exercise_id", "plain", "blocked_addresses", "note",
        "stack_inventory", "versions", "package_names", "receipts",
        "zero_ingestion", "stored", "secret_value_present",
    })


def assert_no_client_payload(residual: Any, *, path: str = "root") -> None:
    """Walk a post-run residual structure; fail if client payload keys/values linger."""
    if isinstance(residual, dict):
        for key, value in residual.items():
            lowered = str(key).lower()
            if lowered in FORBIDDEN_RESIDUAL_KEYS:
                raise AssertionError(f"client payload key at {path}.{key}")
            assert_no_client_payload(value, path=f"{path}.{key}")
    elif isinstance(residual, (list, tuple)):
        for i, item in enumerate(residual):
            assert_no_client_payload(item, path=f"{path}[{i}]")
    elif isinstance(residual, (str, bytes)):
        # Heuristic: long raw-looking blobs are not allowed as residuals.
        if isinstance(residual, bytes) and len(residual) > 256:
            raise AssertionError(f"large bytes residual at {path}")
        if isinstance(residual, str) and len(residual) > 4000:
            raise AssertionError(f"large string residual at {path}")


def stack_inventory_only(components: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """Version numbers and package names only — never code or content."""
    out = []
    for c in components:
        out.append({
            "name": str(c.get("name") or ""),
            "version": str(c.get("version") or ""),
            "ecosystem": str(c.get("ecosystem") or ""),
        })
    return out
