"""Call Zenas's room brain the way Base calls BASE_BRAIN_URL.

Code and free Tank routes run first. The brain is only asked when enabled and
only for gaps code cannot fill, via POST /tasks (when provisioned). Standby
brains expose /health only; callers then continue through Tank routing for
model work, with recall/remember/receipts owned by the memory bracket.
"""
from __future__ import annotations

import os
import uuid
from typing import Any


def brain_enabled() -> bool:
    raw = os.environ.get("ZENAS_BRAIN_ENABLED", "false").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def brain_url() -> str:
    return os.environ.get("ZENAS_BRAIN_URL", "http://zenas-brain:8001").rstrip("/")


async def ping_brain(http: Any) -> dict[str, Any]:
    """Health probe used before paid work when the brain switch is on."""
    if not brain_enabled():
        return {"ok": False, "enabled": False}
    response = await http.get(brain_url() + "/health", timeout=5)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Zenas brain health was not an object")
    return {**data, "enabled": True}


async def ask_brain_tasks(
    http: Any,
    *,
    tenant_id: str,
    task_type: str,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    """Ask the room brain /tasks for gaps only (Base pattern).

    Standby brains have no /tasks API; a 404/405 means fall through to Tank.
    """
    task_id = str(uuid.uuid4())
    payload = {
        "tenant_id": str(tenant_id),
        "task_id": task_id,
        "home_region": os.environ.get("TANK_REGION", "us-east"),
        "task_type": task_type,
        "messages": messages,
        "room": "zenas",
        "batch": task_type,
        "count": 1,
    }
    response = await http.post(brain_url() + "/tasks", json=payload, timeout=5)
    if response.status_code in {404, 405}:
        return {"status": "standby", "task_id": task_id, "gap_filled": False}
    if response.status_code == 202:
        poll = await http.get(
            brain_url() + "/tasks/" + task_id,
            params={"tenant_id": str(tenant_id), "home_region": payload["home_region"]}, timeout=5,
        )
        if poll.status_code == 202:
            return {"status": "running", "task_id": task_id, "gap_filled": False}
        poll.raise_for_status()
        data = poll.json()
        if not isinstance(data, dict):
            raise RuntimeError("Zenas brain task was not an object")
        content = data.get("summary") or data.get("content")
        return {
            "status": data.get("status") or "done",
            "task_id": task_id,
            "gap_filled": bool(content),
            "content": content,
            "receipt": data,
        }
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Zenas brain task was not an object")
    content = data.get("summary") or data.get("content")
    return {
        "status": data.get("status") or "done",
        "task_id": task_id,
        "gap_filled": bool(content),
        "content": content,
        "receipt": data,
    }


async def brain_then_route(
    http: Any,
    tank: Any,
    *,
    tenant_id: str,
    task_type: str,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    """Product paid path: free/code first, optional brain /tasks for gaps, then Tank route.

    Matches Base: code and free passes first; the brain only for what code cannot
    do; Tank routing carries model work and receipts. Recall/remember live in the
    memory bracket around this call.
    """
    brain_meta: dict[str, Any] = {
        "enabled": brain_enabled(),
        "free_first": True,
        "code_first": True,
    }
    if brain_enabled() and http is not None:
        try:
            brain_meta.update(await ping_brain(http))
            gap = await ask_brain_tasks(
                http, tenant_id=str(tenant_id), task_type=task_type, messages=messages
            )
            brain_meta["tasks"] = {
                "status": gap.get("status"),
                "task_id": gap.get("task_id"),
                "gap_filled": bool(gap.get("gap_filled")),
            }
            evidence = gap.get("receipt") or {}
            recorded = evidence.get("model_receipts") or []
            if gap.get("gap_filled") and gap.get("content"):
                if recorded and evidence.get('tenant_id') == str(tenant_id) and all(
                        isinstance(row, dict) and row.get('ledger_id') for row in recorded):
                    from agents.zenas.core.savings import observed
                    for row in recorded:
                        observed(tenant_id, row)
                    return {"content": gap["content"], "model": evidence.get('model'),
                            "cost_usd": evidence.get('cost_usd'), "brain": brain_meta}
                if evidence.get('model') == 'code' and all(evidence.get(k) == 0 for k in ('cost_usd','tokens_in','tokens_out')):
                    return {"content": gap["content"], "model": "code", "cost_usd": 0,
                            "tokens_in": 0, "tokens_out": 0, "brain": brain_meta}
        except Exception as exc:  # noqa: BLE001 — paid Tank route still proceeds
            brain_meta["error"] = type(exc).__name__
            brain_meta["tasks"] = {"status": "unavailable", "gap_filled": False}
    routed = await tank.route(str(tenant_id), task_type, messages)
    return {**routed, "brain": brain_meta}
