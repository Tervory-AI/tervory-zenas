"""Opli request path: free steps first, then the existing Tank agent path.

Call chain (code task):
  cli/src/client.mjs / code-mode.mjs (tervory code | -p)
    → Tank /v1/command/opli/code-turn (integrations/tank/opli_memory.py)
      → request_path.run_code_request
        → gates_memory.product_to_tank_agent_memory
          ORDER:
            (1) free: code_skill → recall → correction → identical_reuse / cache
            (2) brain: Tank existing agent path (Opli has NO product brain)
            (3) Tank for whatever is left
          → remember + receipt

Call chain (memory controls):
  cli memory view|edit|forget|off|control
    → Tank /v1/command/opli/memory.*
      → gates_memory view_memories / set_memory_control / apply_memory_edit / apply_memory_forget
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from command_tools._opli.gates_memory import (
    product_to_tank_agent_memory,
    require_authenticated_scope,
    resolve_memory_control,
    task_input_hash,
)


async def run_code_request(
    memory: Any,
    *,
    tenant_id: str,
    user_id: str,
    text: str,
    repo: str | None = None,
    task_type: str = "command.code",
    memory_control: str | None = None,
    tank_agent_call: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    code_free: Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]] | None = None,
) -> dict[str, Any]:
    """Code corridor: free steps then Tank code-turn / opli.code agent path."""
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    if not str(text).strip():
        raise ValueError("code request requires non-empty text")
    task = {
        "title": "code",
        "request": str(text).strip(),
        "task_type": task_type,
        "repo": repo,
        "text": str(text).strip(),
    }
    task_key = f"code:{task_type}:{task_input_hash(task)[:16]}"
    return await product_to_tank_agent_memory(
        memory,
        tenant_id=tenant,
        user_id=user,
        task_key=task_key,
        task=task,
        tank_agent_call=tank_agent_call,
        memory_control=memory_control,
        code_free=code_free,
    )


async def current_control(memory: Any, *, tenant_id: str, user_id: str) -> str:
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    return await resolve_memory_control(memory, tenant_id=tenant, user_id=user)


__all__ = [
    "current_control",
    "run_code_request",
]
