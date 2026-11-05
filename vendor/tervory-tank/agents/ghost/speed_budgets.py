"""Speed budgets for Autumbra shields. Model opinion never sits on the critical path."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

# Seconds.
EVENT_TO_DECISION_RULE = 2.0
DECISION_TO_SHIELD = 10.0
HEALTH_CHECK = 30.0
AUTO_UNDO_E2E = 60.0
QUEUE_TRANSIT = 5.0
EVENT_TO_SHIELD_E2E = 15.0


LIMITS = {"event_to_decision": EVENT_TO_DECISION_RULE, "decision_to_shield": DECISION_TO_SHIELD,
          "health_check": HEALTH_CHECK, "auto_undo_e2e": AUTO_UNDO_E2E,
          "queue_transit": QUEUE_TRANSIT, "event_to_shield_e2e": EVENT_TO_SHIELD_E2E,
          "event_to_undo_e2e": AUTO_UNDO_E2E, "edge_attempt": DECISION_TO_SHIELD}


def summarize_budgets(receipt: dict[str, Any]) -> None:
    entries = receipt.get("budgets") or {}
    receipt["speed_budget_summary"] = {
        stage: {"limit_seconds": limit, "status":
                ("passed" if entries[stage]["seconds"] <= limit else "exceeded") if stage in entries else "not_run"}
        for stage, limit in LIMITS.items()
    }
    receipt["measurement_path"] = "real_edge" if receipt.get("control") == "cloudflare" else "fixture" if receipt.get("control") == "fixture_edge" else "no_edge"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def stamp_budget(receipt: dict[str, Any], *, stage: str, seconds: float) -> dict[str, Any]:
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Invalid speed measurement.")
    budgets = dict(receipt.get("budgets") or {})
    budgets[stage] = {
        "seconds": float(seconds),
        "limit_seconds": LIMITS.get(stage),
        "within_budget": seconds <= LIMITS[stage] if stage in LIMITS else None,
        "at": utc_now(),  # real timestamp, not the stage name
        "stage": stage,
    }
    receipt["budgets"] = budgets
    summarize_budgets(receipt)
    return receipt


def budgets_ok(receipt: dict[str, Any]) -> tuple[bool, list[str]]:
    """Assert fixture/live receipts meet the product budgets."""
    budgets = receipt.get("budgets") or {}
    fails: list[str] = []
    for key, limit in LIMITS.items():
        entry = budgets.get(key)
        if entry is None:
            continue  # Only assert stages that ran.
        seconds = float(entry.get("seconds", 9999))
        if seconds > limit:
            fails.append(f"{key} {seconds}s > {limit}s")
    return (not fails), fails
