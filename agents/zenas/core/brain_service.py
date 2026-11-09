"""Opt-in signed Zenas worker. Tank owns execution, policy and receipts."""
from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import os
from uuid import UUID

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from agents.zenas.zenas_brain import brain_enabled
from chassis.tank import TankClient
from chassis.user_assertion import signed_user


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[dict[str, str]] = Field(min_length=1, max_length=512)


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_key: str = Field(pattern=r"^zenas\.[a-z_]+$", max_length=120)
    input: TaskInput
    recall: list[dict]
    run_id: UUID


def create_app(*, pool=None, http=None) -> FastAPI:
    """Only infrastructure is injectable; authorization and work stay real."""
    @asynccontextmanager
    async def lifespan(app):
        app.state.pool = pool
        app.state.tank = None
        owned_pool = owned_http = None
        if brain_enabled() and os.environ.get("DATABASE_URL") and os.environ.get("TANK_KEY"):
            try:
                if pool is None:
                    owned_pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2,
                                                          timeout=5, command_timeout=5)
                    app.state.pool = owned_pool
                client = http
                if client is None:
                    owned_http = client = httpx.AsyncClient(timeout=70, follow_redirects=False)
                app.state.tank = TankClient(os.environ.get("TANK_URL", "http://tank:8000"),
                                            os.environ["TANK_KEY"], "zenas", client)
            except (OSError, TimeoutError, asyncpg.PostgresError):
                # No credentials, exception details or connection strings leave this process.
                app.state.tank = None
        try:
            yield
        finally:
            if owned_http is not None:
                await owned_http.aclose()
            if owned_pool is not None:
                await owned_pool.close()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"ok": True, "room": "zenas", "enabled": brain_enabled(),
                "ready": getattr(app.state, "tank", None) is not None,
                "missing_settings": [name for name in ("DATABASE_URL", "TANK_KEY", "TANK_REGION")
                                     if brain_enabled() and not os.environ.get(name)]}

    @app.post("/tasks")
    async def task(body: Task, request: Request, tenant_id: UUID, user_id: str):
        if not brain_enabled():
            raise HTTPException(409, "Zenas brain is off")
        key = os.environ.get("TANK_KEY", "")
        presented = request.headers.get("x-tank-key", "")
        if not key or not presented or not hmac.compare_digest(key.encode(), presented.encode()):
            raise HTTPException(401, "Owning room key required")
        region = os.environ.get("TANK_REGION", "")
        if region not in {"us-east", "za"} or request.headers.get("x-home-region") != region:
            raise HTTPException(403, "Zenas task must stay in its home region")
        actor = await signed_user(request, tenant_id=str(tenant_id), product="zenas", required=True)
        if actor.user_id != user_id:
            raise HTTPException(403, "Actor mismatch")
        client = getattr(app.state, "tank", None)
        if client is None:
            raise HTTPException(503, "Zenas worker requires DATABASE_URL and TANK_KEY")
        try:
            options = {"task_key": "zenas.scope"} if body.task_key == "zenas.scope" else {}
            data = await client.room_task(actor, str(body.run_id), body.input.messages, **options)
            return {"result": {"content": data["choices"][0]["message"]["content"]},
                    "receipt": data["receipt"]}
        except httpx.HTTPStatusError as exc:
            # Keep billed failures visible; a failed worker never permits a second paid route.
            detail = "Zenas task unavailable"
            try:
                bill = exc.response.json().get("detail", {}).get("receipt")
                if bill:
                    detail = {"message": detail, "receipt": bill}
            except (ValueError, AttributeError):
                pass
            raise HTTPException(exc.response.status_code, detail) from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
            raise HTTPException(503, "Zenas task unavailable") from None

    return app


app = create_app()
