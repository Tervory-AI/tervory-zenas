"""Workspace request path: free steps first, then the existing Tank agent path.

Call chain (chat):
  workspace/app/api/chat/route.ts
    → workspace/lib/workspace-gates.ts (chatWithMemory)
      → Tank /v1/command/workspace/chat (integrations/tank/workspace.py)
        → request_path.run_chat_request
          → gates_memory.product_to_tank_agent_memory
            → free: code_skill → recall → correction → identical_reuse
            → paid: Tank agent route (main.route / create_task)
            → remember + receipt

Call chain (tasks POST):
  workspace/app/api/tasks/route.ts
    → workspace/lib/workspace-gates.ts (createTaskWithMemory)
      → Tank /v1/command/workspace/task.create
        → request_path.run_task_request
          → gates_memory.product_to_tank_agent_memory
            → free steps then Tank agent create_task → receipt
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from command_tools._workspace.gates_memory import (
    FIXED_FAKE_PER_CALL,
    product_to_tank_agent_memory,
    require_authenticated_scope,
    resolve_memory_control,
    task_input_fingerprint,
)


async def run_chat_request(
    memory: Any,
    *,
    tenant_id: str,
    user_id: str,
    messages: list[dict[str, Any]],
    task_type: str = "tier0.answer",
    memory_control: str | None = None,
    tank_route: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    code_free: Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]] | None = None,
) -> dict[str, Any]:
    """Chat corridor: free steps then Tank /v1/route agent path."""
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    content = "\n".join(
        str(m.get("content", "")).strip()
        for m in messages
        if isinstance(m, dict) and str(m.get("content", "")).strip()
    )
    if not content:
        raise ValueError("chat request requires non-empty messages")
    task = {
        "title": "chat",
        "request": content,
        "task_type": task_type,
        "messages": messages,
    }
    task_key = f"chat:{task_type}:{task_input_fingerprint(task)[:16]}"
    return await product_to_tank_agent_memory(
        memory,
        tenant_id=tenant,
        user_id=user,
        task_key=task_key,
        task=task,
        tank_agent_call=tank_route,
        memory_control=memory_control,
        code_free=code_free,
    )


async def run_task_request(
    memory: Any,
    *,
    tenant_id: str,
    user_id: str,
    title: str,
    request: str,
    task_type: str = "tier0.answer",
    memory_control: str | None = None,
    tank_create: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    code_free: Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]] | None = None,
) -> dict[str, Any]:
    """Tasks corridor: free steps then Tank /v1/tasks agent path."""
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    if not str(title).strip() or not str(request).strip():
        raise ValueError("task requires non-empty title and request")
    task = {
        "title": str(title).strip(),
        "request": str(request).strip(),
        "task_type": task_type,
        "messages": [{"role": "user", "content": str(request).strip()}],
    }
    task_key = f"task:{task_type}:{task_input_fingerprint(task)[:16]}"
    return await product_to_tank_agent_memory(
        memory,
        tenant_id=tenant,
        user_id=user,
        task_key=task_key,
        task=task,
        tank_agent_call=tank_create,
        memory_control=memory_control,
        code_free=code_free,
    )


async def current_control(memory: Any, *, tenant_id: str, user_id: str) -> str:
    tenant, user = require_authenticated_scope(tenant_id=tenant_id, user_id=user_id)
    return await resolve_memory_control(memory, tenant_id=tenant, user_id=user)


__all__ = [
    "FIXED_FAKE_PER_CALL",
    "current_control",
    "run_chat_request",
    "run_task_request",
]
