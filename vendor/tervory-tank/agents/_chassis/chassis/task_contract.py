"""Typed room-task scope and receipt validation shared with Tank."""
from __future__ import annotations

import math
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid5


class TaskActor(Protocol):
    tenant_id: str
    user_id: str


def origin(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise ValueError("Invalid Tank origin")
    _ = parsed.port
    return value.rstrip("/")


def context(actor: TaskActor, run_id: str) -> tuple[str, str]:
    if not isinstance(actor.user_id, str) or not actor.user_id.strip() or len(actor.user_id) > 200:
        raise ValueError("Verified user required")
    return str(UUID(actor.tenant_id)), str(UUID(str(run_id)))


def paid_identity(tenant: str, user: str, run: str) -> str:
    return str(uuid5(UUID(tenant), user + ":" + str(UUID(run))))


def check_receipt(data: dict[str, Any], tenant: str, user: str, run: str, room: str) -> None:
    receipt = data["receipt"]
    if room == "rig" and "status" in receipt:
        return check_rig_worker(receipt, tenant, user, run)
    if (receipt["tenant_id"] != tenant or receipt["room"] != room
            or receipt["task_id"] != paid_identity(tenant, user, run)
            or receipt["task_type"] != "agent." + room + ".think"):
        raise ValueError("Receipt scope mismatch")
    UUID(receipt["ledger_id"])
    for field in ("tokens_in", "tokens_out"):
        if type(receipt[field]) is not int or receipt[field] < 0:
            raise ValueError("Invalid token receipt")
    cost = receipt["cost_usd"]
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
        raise ValueError("Invalid cost receipt")


def check_rig_worker(receipt: dict[str, Any], tenant: str, user: str, run: str) -> None:
    """Validate Rig's existing aggregate worker receipt without inventing a model row."""
    task = paid_identity(tenant, user, run)
    if (receipt.get("tenant_id"), receipt.get("user_id"), receipt.get("task_id"),
            receipt.get("room"), receipt.get("region")) != (tenant, user, task, "rig", "us-east"):
        raise ValueError("Receipt scope mismatch")
    if receipt.get("status") not in {"running", "done", "failed", "cancelled"}:
        raise ValueError("Invalid worker status")
    models = receipt.get("model_receipts", [])
    for row in [*models, *receipt.get("tank_receipts", [])]:
        if (row.get("tenant_id"), row.get("task_id"), row.get("room")) != (tenant, task, "rig"):
            raise ValueError("Receipt scope mismatch")
        UUID(row["ledger_id"])
    for row in models:
        if row.get("task_type") != "agent.rig.think" or row.get("user_id") != user:
            raise ValueError("Model receipt scope mismatch")
    for field in ("tokens_in", "tokens_out", "paid_calls"):
        value = receipt.get(field)
        if value is None and receipt["status"] != "done":
            continue
        if type(value) is not int or value < 0:
            raise ValueError("Invalid worker usage")
    cost = receipt.get("cost_usd")
    if cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int, float))
                             or not math.isfinite(cost) or cost < 0):
        raise ValueError("Invalid worker cost")
    if receipt["status"] == "done" and (not receipt.get("tank_receipts") or
            (not models and not receipt.get("answered_from_memory"))):
        raise ValueError("Missing worker receipts")
    if receipt["status"] == "done" and not models and (
            cost != 0 or any(receipt.get(field) != 0 for field in ("tokens_in", "tokens_out", "paid_calls"))):
        raise ValueError("Invalid code-only receipt")
    if models:
        if receipt.get("paid_calls") != len(models):
            raise ValueError("Model count mismatch")
        for field in ("tokens_in", "tokens_out"):
            if receipt.get(field) != sum(row[field] for row in models):
                raise ValueError("Model usage mismatch")
        actual = None if any(row.get("cost_usd") is None for row in models) else sum(row["cost_usd"] for row in models)
        if (actual is None) != (cost is None) or (actual is not None and not math.isclose(cost, actual)):
            raise ValueError("Model cost mismatch")
