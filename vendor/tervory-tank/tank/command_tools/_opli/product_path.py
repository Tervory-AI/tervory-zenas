"""Opli live product path ORDER: free (code skill, memory, cache) → brain=Tank code_turn → remember.

Opli has NO product brain; the brain step is Tank's existing code_turn agent path.

This is the real request corridor used by live endpoints. The paid step always
calls Tank command.code_turn (existing agent path). Tests may fake only the
network/model edge (route_code / Hermes HTTP), never replace code_turn itself.
"""
from __future__ import annotations

from typing import Any

from command_tools._opli.gates_memory import (
    MemoryControlError,
    MemoryScopeError,
    product_to_tank_agent_memory,
    require_authenticated_scope,
    task_input_hash,
)


def _usage_from_code_turn_result(result: dict[str, Any]) -> tuple[float, int, int]:
    cost = 0.0
    tokens_in = 0
    tokens_out = 0
    receipt = result.get("receipt") if isinstance(result, dict) else None
    rows = (receipt or {}).get("rows") if isinstance(receipt, dict) else None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cost += float(row.get("cost_usd") or 0)
        tokens_in += int(row.get("tokens_in") or 0)
        tokens_out += int(row.get("tokens_out") or 0)
    # Some Tank fixtures expose a turn cost summary instead of per-row tokens.
    turn = result.get("turn") if isinstance(result, dict) else None
    if isinstance(turn, dict) and cost == 0.0 and turn.get("cost_usd") is not None:
        cost = float(turn.get("cost_usd") or 0)
    return cost, tokens_in, tokens_out


async def run_opli_code_turn_with_memory(
    memory: Any,
    command: Any,
    *,
    tenant_id: str,
    user_id: str,
    thread_id: Any,
    body: Any,
    actor: Any,
    memory_control: str | None = None,
    repo: str | None = None,
    code_free: Any | None = None,
) -> dict[str, Any]:
    """Live product request → recall/free → Tank command.code_turn → remember.

    ``command.code_turn`` is Tank's existing agent path (same entry the CLI hits
    at /v1/command/threads/{id}/code-turn). Memory uses the chassis MemoryClient
    passed in (app.state.memory).
    """
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    text = getattr(body, "text", None)
    if text is None and isinstance(body, dict):
        text = body.get("text")
    text = str(text or "").strip()
    if not text:
        raise ValueError("code turn requires non-empty text")

    model = getattr(body, "model", None)
    if model is None and isinstance(body, dict):
        model = body.get("model")
    model = str(model or "auto")

    task = {
        "title": "code",
        "request": text,
        "task_type": "command.code",
        "repo": repo,
        "text": text,
        "model": model,
        "thread_id": str(thread_id),
    }
    task_key = f"code:command.code:{task_input_hash(task)[:16]}"

    async def tank_agent_call(task_payload: dict[str, Any]) -> dict[str, Any]:
        # REAL Tank agent path — do not replace this with a label/fake in product code.
        result = await command.code_turn(thread_id, body, actor)
        cost, tokens_in, tokens_out = _usage_from_code_turn_result(result)
        answer = ""
        if isinstance(result, dict):
            answer = str(result.get("answer") or "")
            if not answer and isinstance(result.get("turn"), dict):
                answer = str(result["turn"].get("model") or "code-turn")
        # Remembered fields come from the real Tank code_turn result only —
        # never hardcoded empty, and never invent list fields when Tank omitted them.
        requests = result.get("requests") if isinstance(result, dict) else None
        commands = None
        if isinstance(requests, list):
            names = [
                str(item.get("name"))
                for item in requests
                if isinstance(item, dict) and item.get("name")
            ]
            commands = names or None
        decisions = None
        if isinstance(result, dict) and isinstance(result.get("decisions"), list) and result["decisions"]:
            decisions = [str(d) for d in result["decisions"] if d not in (None, "")]
            decisions = decisions or None
        outcome = (answer or "").strip()
        if not outcome and isinstance(result, dict):
            turn = result.get("turn") if isinstance(result.get("turn"), dict) else {}
            outcome = str(turn.get("model") or "").strip()
        if not outcome:
            outcome = f"code-turn:{task_payload.get('task_type') or 'command.code'}"
        return {
            "cost_usd": cost,
            "paid_calls": sum(1 for row in (result.get("receipt") or {}).get("rows", [])
                              if row.get("cost_usd") is not None and float(row["cost_usd"]) > 0),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "result": {
                "repo": repo or task_payload.get("repo") or None,
                "outcome": outcome[:500],
                "commands": commands,
                "decisions": decisions,
                "raw": result,
            },
        }

    receipt = await product_to_tank_agent_memory(
        memory,
        tenant_id=tenant,
        user_id=user,
        task_key=task_key,
        task=task,
        tank_agent_call=tank_agent_call,
        memory_control=memory_control,
        code_free=code_free,
    )
    result_obj = receipt.get("result") or {}
    raw = result_obj.get("raw") if isinstance(result_obj, dict) else None
    bracket = {
        "paid_calls": receipt["paid_calls"],
        "cost_usd": receipt["cost_usd"],
        "tokens_in": receipt["tokens_in"],
        "tokens_out": receipt["tokens_out"],
        "steps": receipt["steps"],
        "answered_from_memory": receipt.get("answered_from_memory"),
        "task_key": task_key,
        "input_hash": receipt.get("input_hash"),
        "memory_unavailable": receipt.get("memory_unavailable", False),
        "would_have_cost_usd": receipt.get("would_have_cost_usd", receipt["cost_usd"]),
        "would_have_tokens_in": receipt.get("would_have_tokens_in", receipt["tokens_in"]),
        "would_have_tokens_out": receipt.get("would_have_tokens_out", receipt["tokens_out"]),
    }
    if isinstance(raw, dict) and not receipt.get("answered_from_memory"):
        return {**raw, "memory_bracket": bracket}
    # A free answer is still a command turn: reserve the request in the existing
    # durable thread/task corridor, enforce ownership and replay guards, and
    # persist its zero-cost ledger receipt before exposing the answer.
    completed = await command.code_turn(
        thread_id, body, actor,
        _memory_answer=result_obj.get("outcome") or "answered from memory",
    )
    return {**completed, "memory_bracket": bracket}


__all__ = [
    "MemoryControlError",
    "MemoryScopeError",
    "run_opli_code_turn_with_memory",
]
