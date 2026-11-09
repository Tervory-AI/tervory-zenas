"""Signed Radix memory controls through the existing room transport."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from uuid import UUID

import command
from ghost_routes import call_room
from product_identity import actor_scope


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    control: Literal["off", "view", "edit", "forget"]
    memory_id: UUID | None = None


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_id: UUID
    text: str = Field(min_length=1, max_length=20000, pattern=r"\S")
    memory_control: Literal["edit", "forget"] | None = None


class Forget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_id: UUID
    memory_control: Literal["forget"] = "forget"


def build_router():
    router = APIRouter(prefix="/v1/radix/memory")

    async def forward(request, actor, suffix="", body=None):
        # Use the existing tenant Radix grant, never caller-supplied user/role.
        command.require_tool(actor, "radix.changes")
        allowed = {"task_key", "memory_control", "memory_id"} if request.method == "GET" else set()
        if set(request.query_params) - allowed or any(len(request.query_params.getlist(k)) != 1 for k in allowed & set(request.query_params)):
            raise HTTPException(422, "The action arguments are invalid")
        with actor_scope(actor):
            response = await call_room(request.method, "radix", "/v1/radix/memory" + suffix,
                body=body, params=dict(request.query_params) or None,
                tenant=actor.tenant_id, user=actor.user_id, timeout=30)
        return Response(response.content, status_code=response.status_code, media_type="application/json",
                        headers={"Cache-Control": "private, no-store"})

    @router.get("")
    async def view(request: Request, actor: command.Actor = Depends(command.identity)):
        return await forward(request, actor)

    @router.post("/control")
    async def control(request: Request, body: Control, actor: command.Actor = Depends(command.identity)):
        return await forward(request, actor, "/control", body.model_dump(mode="json", exclude_none=True))

    @router.post("/edit")
    async def edit(request: Request, body: Edit, actor: command.Actor = Depends(command.identity)):
        return await forward(request, actor, "/edit", body.model_dump(mode="json", exclude_none=True))

    @router.post("/forget")
    async def forget(request: Request, body: Forget, actor: command.Actor = Depends(command.identity)):
        return await forward(request, actor, "/forget", body.model_dump(mode="json", exclude_none=True))

    return router
