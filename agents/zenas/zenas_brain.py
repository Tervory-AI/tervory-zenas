"""Optional room work uses the shared signed, receipted Tank task client.

Tank owns private service discovery, bounded dispatch and receipt verification.
The normal legal policy route remains the fallback for an absent task API.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from uuid import UUID, uuid4, uuid5

import httpx

from agents.zenas.core.answer_failures import TaskBoundaryError


def brain_enabled() -> bool:
    return os.environ.get("ZENAS_BRAIN_ENABLED", "false").strip().lower() in {"true", "1", "yes"}


async def ask_brain_tasks(tank: Any, *, actor: Any, run_id: str,
                          tenant_id: str, task_type: str,
                          messages: list[dict[str, str]]) -> dict[str, Any]:
    """Discover/dispatch through Tank; a standby has no task API to execute."""
    if actor is None or str(actor.tenant_id) != str(tenant_id):
        raise PermissionError("Verified task owner required")
    try:
        data = await tank.delegate_task(actor, run_id, task_type, {"messages": messages}, [])
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {404, 405}:
            return {"status": "standby", "task_id": run_id, "gap_filled": False}
        # Auth, a running task, malformed evidence and uncertain dispatch never
        # trigger another paid run. The room boundary preserves the receipt.
        raise TaskBoundaryError(exc.response.status_code if exc.response.status_code in {401, 403} else 503) from None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise TaskBoundaryError(503) from None
    result = data["result"]
    content = result.get("content") if isinstance(result, dict) and "content" in result else (
        json.dumps(result, separators=(',', ':')) if isinstance(result, (dict, list)) else result)
    if not isinstance(content, str) or not content.strip():
        # A completed receipt may already carry spend. Empty output cannot
        # authorize another model call for this same step.
        raise TaskBoundaryError(503)
    return {"status": "done", "task_id": run_id, "gap_filled": bool(content),
            "content": content, "receipt": data["receipt"]}


async def brain_then_route(http: Any, tank: Any, *, tenant_id: str,
                           task_type: str, messages: list[dict[str, str]],
                           actor: Any = None, run_id: str | None = None) -> dict[str, Any]:
    """Code/free work precedes optional room work; every model stays in Tank."""
    del http  # Retain callers' signature; product code owns no private transport.
    from agents.zenas.core.savings import Batch, current
    run = current.get()
    if run is not None and not isinstance(run, Batch):
        actor = actor or run.actor
        # Distinct legal steps within one command must not reuse each other's
        # result. Same-step retries retain the same person/run/input identity.
        digest = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
        run_id = run_id or str(uuid5(UUID(run.id), task_type + ':' + digest))
    meta: dict[str, Any] = {"enabled": brain_enabled(), "free_first": True, "code_first": True}
    if brain_enabled() and actor is not None and task_type.startswith("zenas."):
        gap = await ask_brain_tasks(tank, actor=actor, run_id=run_id or str(uuid4()),
                                    tenant_id=tenant_id, task_type=task_type, messages=messages)
        meta["tasks"] = {key: gap[key] for key in ("status", "task_id", "gap_filled")}
        if gap["gap_filled"]:
            bill = gap["receipt"]
            return {"content": gap["content"], "model": bill["model"],
                    **{k: bill[k] for k in ("ledger_id", "cost_usd", "tokens_in", "tokens_out")},
                    "brain": meta}
    routed = await tank.route(str(tenant_id), task_type, messages)
    return {**routed, "brain": meta}
