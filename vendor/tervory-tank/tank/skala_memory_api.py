"""Skala controls. Identity comes from Tank's verifier, never body selectors."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from skala_memory_client import memory_client_for_skala
from skala_gates_memory import (
    MemoryControlError, MemoryScopeError, MemoryUnavailable,
    apply_memory_edit, apply_memory_forget, clear_verified_actor,
    require_verified_memory_actor, set_memory_control, set_verified_actor, view_memories,
)


class ControlBody(BaseModel):
    model_config = {"extra": "forbid"}
    tenant_id: UUID
    user_id: str = Field(min_length=1, max_length=200)
    memory_id: str | None = Field(default=None, max_length=80)
    memory_control: str | None = None
    task_key: str = Field(default="", max_length=120)


class EditBody(ControlBody):
    memory_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=4000)


async def require_service_key(request: Request) -> None:
    # Authenticate before FastAPI validates caller-controlled body/query fields.
    # Actor/tenant/user verification still runs before any memory operation.
    from main import require_key
    await require_key(request, request.headers.get("X-Tank-Key", ""))


async def verified_actor(request: Request, tenant_id: str) -> dict[str, str]:
    from main import require_key, pool
    from product_identity import current
    from command_tools._user_assertion import signed_user, PostgresNonces
    from skala_memory_client import skala_agent_key_from_env
    await require_key(request, request.headers.get("X-Tank-Key", ""))
    actor = current.get()  # Set only by actual TriggerSigninMiddleware.
    if actor is None:
        actor = await signed_user(
            request, tenant_id=tenant_id, product="skala", required=True,
            key=skala_agent_key_from_env(), nonces=PostgresNonces(pool(), "tank"),
        )
    if str(actor.tenant_id) != tenant_id:
        raise MemoryScopeError("Skala memory tenant does not match signed-in caller")
    return {"tenant_id": tenant_id, "user_id": actor.user_id}


def attach_memory_routes(app: FastAPI, *, authorize=verified_actor) -> None:
    async def invoke(request, tenant, user, action, **kwargs):
        clear_verified_actor()
        try:
            proof = await authorize(request, str(tenant))
            set_verified_actor(**proof)
            require_verified_memory_actor()
            if proof["user_id"] != user:
                raise MemoryScopeError("Skala memory user does not match signed-in caller")
            # A supplied adapter is only a unit-test boundary. The actual Tank app
            # has no global memory client: every client is bound to this actor.
            memory = getattr(request.app.state, "memory", None)
            if memory is None:
                memory = memory_client_for_skala(user_id=proof["user_id"])
            return await action(memory, tenant_id=str(tenant), user_id=user, **kwargs)
        except MemoryScopeError as exc:
            raise HTTPException(401, str(exc)) from exc
        except MemoryControlError as exc:
            raise HTTPException(403, str(exc)) from exc
        except MemoryUnavailable as exc:
            raise HTTPException(503, "Skala memory unavailable") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            clear_verified_actor()

    @app.get("/v1/skala/memory", dependencies=[Depends(require_service_key)])
    async def view(request: Request, tenant_id: UUID, user_id: str = Query(min_length=1, max_length=200),
                   memory_id: str | None = None, task_key: str = "memory_control",
                   memory_control: str = "view") -> dict[str, Any]:
        return await invoke(request, tenant_id, user_id, view_memories, memory_id=memory_id, task_key=task_key)

    @app.post("/v1/skala/memory/edit", dependencies=[Depends(require_service_key)])
    async def edit(request: Request, body: EditBody) -> dict[str, Any]:
        return await invoke(request, body.tenant_id, body.user_id, apply_memory_edit,
                            memory_id=body.memory_id, text=body.text, memory_control="edit")

    @app.post("/v1/skala/memory/forget", dependencies=[Depends(require_service_key)])
    async def forget(request: Request, body: ControlBody) -> dict[str, bool]:
        if not body.memory_id:
            raise HTTPException(422, "memory_id required")
        await invoke(request, body.tenant_id, body.user_id, apply_memory_forget, memory_id=body.memory_id)
        return {"deleted": True}

    @app.post("/v1/skala/memory/off", dependencies=[Depends(require_service_key)])
    async def off(request: Request, body: ControlBody) -> dict[str, Any]:
        saved = await invoke(request, body.tenant_id, body.user_id, set_memory_control,
                             control="off", memory_id=body.memory_id)
        return {"control": "off", "memory_id": saved.get("id")}
