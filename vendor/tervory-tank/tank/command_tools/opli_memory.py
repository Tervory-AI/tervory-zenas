"""Opli Tank command-tool packaging: live memory controls + code_turn wrap.

Memory controls take tenant+user ONLY from the authenticated actor chain
(identity → authorized_actor). Body/header user selectors are refused.
VIEW/EDIT/FORGET/SWITCH-OFF verify target record tenant+user match the caller.

Packaged into tank/command_tools/ by tank-patches/gates-7-8.patch (unapplied).
Product source of truth: agents/opli/{gates_memory,request_path,product_path,memory_api}.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field


TOOLS: dict = {}
FOUNDER_TOOLS: list = []
TENANT_ADMIN_TOOLS: list = []
CLIENT_TOOLS = (
    "opli.code-turn",
    "opli.memory.view",
    "opli.memory.control",
    "opli.memory.edit",
    "opli.memory.forget",
)
TENANT_TOOLS = CLIENT_TOOLS
REQUEST_BODY_TOOLS = frozenset(CLIENT_TOOLS)


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodeTurnBody(Empty):
    thread_id: UUID
    text: str = Field(min_length=1, max_length=20000)
    prompt_id: UUID | None = None
    model: str = Field(default="auto", min_length=1, max_length=100)
    memory_control: Literal["off", "view", "edit", "forget"] | None = None
    repo: str | None = Field(default=None, max_length=400)


class MemoryView(Empty):
    task_key: str = Field(default="memory_control", min_length=1, max_length=120)
    memory_control: Literal["off", "view", "edit", "forget"] = "view"
    memory_id: str | None = Field(default=None, min_length=1, max_length=80)


class MemoryControlSet(Empty):
    control: Literal["off", "view", "edit", "forget"]
    memory_id: str | None = Field(default=None, min_length=1, max_length=80)


class MemoryForget(Empty):
    memory_id: str = Field(min_length=1, max_length=80)
    memory_control: Literal["forget"] = "forget"


class MemoryEdit(Empty):
    memory_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=20000, pattern=r"\S")
    memory_control: Literal["edit", "forget"] | None = None


def adapters(command):
    return {
        "opli.code-turn": command.Adapter(
            "POST", "/v1/command/opli/code-turn", CodeTurnBody, "Code turn with memory", True, "paid_run"
        ),
        "opli.memory.view": command.Adapter(
            "GET", "/v1/command/opli/memory.view", MemoryView, "View Opli memories"
        ),
        "opli.memory.control": command.Adapter(
            "POST", "/v1/command/opli/memory.control", MemoryControlSet, "Set Opli memory control"
        ),
        "opli.memory.edit": command.Adapter(
            "POST", "/v1/command/opli/memory.edit", MemoryEdit, "Edit one Opli memory"
        ),
        "opli.memory.forget": command.Adapter(
            "POST", "/v1/command/opli/memory.forget", MemoryForget, "Forget one Opli memory"
        ),
    }


def _load_product():
    from command_tools._opli import gates_memory, product_path
    return gates_memory, product_path


def _actor_scope(actor) -> tuple[str, str]:
    """Bind memory scope from the signed-in command Actor only.

    identity → authorized_actor → verified_memory_actor → product require_authenticated_scope.
    Body/header user selectors never enter this path.
    """
    import command as command_mod
    tenant, user = command_mod.verified_memory_actor(actor)
    gates_memory, _ = _load_product()
    return gates_memory.require_authenticated_scope(tenant_id=tenant, user_id=user)


def _memory_client(command, actor):
    """Live path: chassis MemoryClient only — never FakeMemory, never the founder-only Tank memory corridor."""
    memory = getattr(getattr(command, "app", None), "state", None)
    memory = getattr(memory, "memory", None) if memory is not None else None
    if memory is not None:
        return memory
    return _load_product()[0].production_memory()


def install(command, router=None) -> None:
    """Mount /opli/* routes and wrap command.code_turn with the product memory path."""
    gates_memory, product_path = _load_product()
    target_router = router if router is not None else command.router

    if not getattr(command, "_opli_memory_code_turn_wrapped", False):
        original_code_turn = command.code_turn

        class _CommandProxy:
            """Expose original code_turn so product_path does not recurse into the wrap."""
            def __init__(self, wrapped):
                self._wrapped = wrapped
            def __getattr__(self, name):
                if name == "code_turn":
                    return original_code_turn
                return getattr(self._wrapped, name)

        async def code_turn_with_memory(thread_id, body, actor):
            if not gates_memory.memory_enabled():
                return await original_code_turn(thread_id, body, actor)
            actor = await command.authorized_actor(actor)
            tenant, user = _actor_scope(actor)
            memory = _memory_client(command, actor)
            return await product_path.run_opli_code_turn_with_memory(
                memory,
                _CommandProxy(command),
                tenant_id=tenant,
                user_id=user,
                thread_id=thread_id,
                body=body,
                actor=actor,
            )

        command.code_turn = code_turn_with_memory
        command._opli_memory_code_turn_wrapped = True
        command._opli_original_code_turn = original_code_turn

    @target_router.post("/opli/code-turn")
    async def opli_code_turn(body: CodeTurnBody, actor: Any = Depends(command.identity)):
        actor = await command.authorized_actor(actor)
        try:
            tenant, user = _actor_scope(actor)
        except gates_memory.MemoryScopeError as exc:
            raise HTTPException(401, str(exc)) from exc
        turn = command.CodeTurn(
            text=body.text,
            model=body.model,
            prompt_id=body.prompt_id or body.thread_id,
        )
        if not gates_memory.memory_enabled():
            original = getattr(command, "_opli_original_code_turn", command.code_turn)
            return await original(body.thread_id, turn, actor)
        memory = _memory_client(command, actor)
        proxy = type("P", (), {})()
        proxy.code_turn = getattr(command, "_opli_original_code_turn", command.code_turn)
        # Use original if wrap already applied
        if hasattr(command, "_opli_original_code_turn"):
            class _P:
                code_turn = staticmethod(command._opli_original_code_turn)
            cmd = _P()
            # bind other attrs from command for safety
            cmd.__dict__.update({k: getattr(command, k) for k in ("CodeTurn",) if hasattr(command, k)})
        else:
            cmd = command
        try:
            return await product_path.run_opli_code_turn_with_memory(
                memory,
                cmd if hasattr(command, "_opli_original_code_turn") else command,
                tenant_id=tenant,
                user_id=user,
                thread_id=body.thread_id,
                body=turn,
                actor=actor,
                memory_control=body.memory_control,
                repo=body.repo,
            )
        except gates_memory.MemoryScopeError as exc:
            raise HTTPException(401, str(exc)) from exc
        except gates_memory.MemoryControlError as exc:
            raise HTTPException(403, str(exc)) from exc

    @target_router.get("/opli/memory.view")
    async def opli_memory_view(
        task_key: str = "memory_control",
        memory_control: str = "view",
        memory_id: str | None = None,
        actor: Any = Depends(command.identity),
    ):
        # Identity from authenticated actor chain only — never a body user selector.
        actor = await command.authorized_actor(actor)
        try:
            tenant, user = _actor_scope(actor)
        except gates_memory.MemoryScopeError as exc:
            raise HTTPException(401, str(exc)) from exc
        memory = _memory_client(command, actor)
        try:
            return await gates_memory.view_memories(
                memory, tenant_id=tenant, user_id=user, task_key=task_key,
                memory_id=memory_id,
            )
        except gates_memory.MemoryScopeError as exc:
            raise HTTPException(403, str(exc)) from exc
        except RuntimeError as exc:
            if gates_memory.is_memory_outage(exc):
                raise HTTPException(503, "Memory is unavailable") from None
            raise

    @target_router.post("/opli/memory.control")
    async def opli_memory_control(body: MemoryControlSet, actor: Any = Depends(command.identity)):
        actor = await command.authorized_actor(actor)
        try:
            tenant, user = _actor_scope(actor)
        except gates_memory.MemoryScopeError as exc:
            raise HTTPException(401, str(exc)) from exc
        memory = _memory_client(command, actor)
        try:
            saved = await gates_memory.set_memory_control(
                memory, tenant_id=tenant, user_id=user, control=body.control,
                memory_id=body.memory_id,
            )
        except gates_memory.MemoryScopeError as exc:
            raise HTTPException(403, str(exc)) from exc
        except RuntimeError as exc:
            if gates_memory.is_memory_outage(exc):
                raise HTTPException(503, "Memory is unavailable") from None
            raise
        return {"memory_control": body.control, "id": saved.get("id")}

    @target_router.post("/opli/memory.edit")
    async def opli_memory_edit(body: MemoryEdit, actor: Any = Depends(command.identity)):
        actor = await command.authorized_actor(actor)
        try:
            tenant, user = _actor_scope(actor)
        except gates_memory.MemoryScopeError as exc:
            raise HTTPException(401, str(exc)) from exc
        memory = _memory_client(command, actor)
        try:
            return await gates_memory.apply_memory_edit(
                memory,
                memory_id=body.memory_id,
                tenant_id=tenant,
                user_id=user,
                text=body.text,
                memory_control=body.memory_control,
            )
        except gates_memory.MemoryControlError as exc:
            raise HTTPException(403, str(exc)) from exc
        except gates_memory.MemoryScopeError as exc:
            raise HTTPException(403, str(exc)) from exc
        except RuntimeError as exc:
            if gates_memory.is_memory_outage(exc):
                raise HTTPException(503, "Memory is unavailable") from None
            raise
        except ValueError:
            raise HTTPException(422, "Memory input is invalid") from None

    @target_router.post("/opli/memory.forget")
    async def opli_memory_forget(body: MemoryForget, actor: Any = Depends(command.identity)):
        actor = await command.authorized_actor(actor)
        try:
            tenant, user = _actor_scope(actor)
        except gates_memory.MemoryScopeError as exc:
            raise HTTPException(401, str(exc)) from exc
        memory = _memory_client(command, actor)
        try:
            await gates_memory.apply_memory_forget(
                memory,
                memory_id=body.memory_id,
                tenant_id=tenant,
                user_id=user,
                memory_control=body.memory_control,
            )
        except gates_memory.MemoryControlError as exc:
            raise HTTPException(403, str(exc)) from exc
        except gates_memory.MemoryScopeError as exc:
            raise HTTPException(403, str(exc)) from exc
        except RuntimeError as exc:
            if gates_memory.is_memory_outage(exc):
                raise HTTPException(503, "Memory is unavailable") from None
            raise
        return {"forgotten": body.memory_id}


def build_router():
    from fastapi import APIRouter
    import command
    router = APIRouter(prefix="/v1/command", tags=["opli"])
    install(command, router)
    return router
