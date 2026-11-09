"""Tenant-owned watch commands through Tiger's existing room corridor."""

import json
from typing import Annotated, Literal
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator


class BulkArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pages: list[str] = Field(min_length=1, max_length=50)
    every: Literal["15m", "1h", "6h", "12h", "1d"] = "1d"

    @model_validator(mode="after")
    def watch_arguments(self):
        from command import WatchArgs
        for page in self.pages:
            WatchArgs(url=page, every=self.every)
        return self


async def room(actor, method, path, *, body=None):
    from main import _tiger_call, _tiger_response
    params = [("tenant_id", str(actor.tenant_id))] if body is None else []
    content = json.dumps({**body, "tenant_id": str(actor.tenant_id)}).encode() if body is not None else None
    response = _tiger_response(await _tiger_call(method, path, params, content), actor.tenant_id)
    data = json.loads(response.body) if response.body else {}
    if response.status_code >= 400:
        raise HTTPException(response.status_code, data.get("detail"))
    return data


async def watches(actor):
    return (await room(actor, "GET", "/watchlist"))["watches"]


async def result(actor, tool, data):
    from trigger_routes import code_receipt
    return {**data, "ledger_id": str(await code_receipt(actor.tenant_id, tool, agent="tiger")), "cost_usd": 0}


def build_router() -> APIRouter:
    from command import Actor, UrlArgs, WatchArgs, identity, require_tool
    from trigger_commands import confirmation
    router = APIRouter(prefix="/v1/command/tiger/watches", tags=["tiger"])

    @router.get("")
    async def listed(request: Request, actor: Annotated[Actor, Depends(identity)]):
        from main import _tiger_tenant
        require_tool(actor, "tiger.watches")
        # Cross-check any named tenant, as the existing Tiger alert command does.
        query = [(key, value) for key, value in request.query_params.multi_items() if key == "tenant_id"]
        query.append(("tenant_id", str(actor.tenant_id)))
        _tiger_tenant(Request({**request.scope, "query_string": urlencode(query).encode()}))
        rows = await watches(actor)
        return await result(actor, "tiger.watches", {"total": len(rows), "items": [{**row, "name": row["label"]} for row in rows]})

    @router.get('/receipts')
    async def receipts(request: Request, actor: Annotated[Actor, Depends(identity)]):
        from main import _tiger_tenant, pool
        from tiger_savings import listed
        require_tool(actor, 'tiger.receipts')
        query = list(request.query_params.multi_items()) + [('tenant_id', str(actor.tenant_id))]
        _tiger_tenant(Request({**request.scope, 'query_string': urlencode(query).encode()}))
        async with pool().acquire() as conn:
            return {'receipts': await listed(conn, actor.tenant_id)}

    async def change(actor, tool, args, enabled=None):
        require_tool(actor, tool)
        confirmation(actor, tool, args.model_dump(mode="json", exclude_none=True))
        row = next((row for row in await watches(actor) if row["url"] == args.url), None)
        if row is None:
            raise HTTPException(404, "no such watch")
        path = "/watchlist/" + str(UUID(row["id"]))
        if enabled is None:
            await room(actor, "DELETE", path)
        else:
            row = await room(actor, "PATCH", path, body={"enabled": enabled})
        return await result(actor, tool, {**row, "name": row["label"]})

    @router.post("/pause")
    async def pause(args: UrlArgs, actor: Annotated[Actor, Depends(identity)]):
        return await change(actor, "tiger.watch.pause", args, False)

    @router.post("/resume")
    async def resume(args: UrlArgs, actor: Annotated[Actor, Depends(identity)]):
        return await change(actor, "tiger.watch.resume", args, True)

    @router.post("/remove")
    async def remove(args: UrlArgs, actor: Annotated[Actor, Depends(identity)]):
        return await change(actor, "tiger.watch.remove", args)

    @router.post("/bulk")
    async def bulk(args: BulkArgs, actor: Annotated[Actor, Depends(identity)]):
        tool = "tiger.watch.bulk"
        require_tool(actor, tool)
        confirmation(actor, tool, args.model_dump(mode="json", exclude_none=True))
        existing = {row["url"] for row in await watches(actor)}
        created, already = 0, 0
        for url in args.pages:
            if url in existing:
                already += 1
                continue
            try:
                watch = WatchArgs(url=url, every=args.every)
                await room(actor, "POST", "/watchlist", body=watch.model_dump(mode="json"))
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                already += 1  # The room also catches concurrent duplicate creates.
            else:
                created += 1
            existing.add(url)
        return await result(actor, tool, {"total": created, "count": already})

    async def memory_forward(actor, method, path, value=None):
        from main import _tiger_call, _tiger_response
        from product_identity import actor_scope
        require_tool(actor, "tiger.watches")
        params = [("tenant_id", str(actor.tenant_id)), ("user_id", actor.user_id)]
        content = json.dumps(value).encode() if value is not None else None
        # The existing paid_http adapter signs only this verified actor and the
        # configured product origin, binding the exact body and encoded target.
        with actor_scope(actor):
            response = _tiger_response(await _tiger_call(method, path, params, content), actor.tenant_id)
        if response.status_code >= 400:
            raise HTTPException(response.status_code, "Tiger memory request refused")
        return json.loads(response.body) if response.body else {}

    async def memory_body(request, actor):
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(422, "JSON object required") from None
        if not isinstance(body, dict):
            raise HTTPException(422, "JSON object required")
        role = "founder" if actor.founder else "org:admin" if actor.tenant_admin else "member"
        if (body.get("tenant_id", str(actor.tenant_id)) != str(actor.tenant_id)
                or body.get("user_id", actor.user_id) != actor.user_id
                or body.get("role", role) != role):
            raise HTTPException(403, "Memory owner mismatch")
        return {k: v for k, v in body.items() if k not in ("tenant_id", "user_id", "role")}

    @router.get("/memory")
    async def memory_view(actor: Annotated[Actor, Depends(identity)]):
        return await memory_forward(actor, "GET", "/memory")

    @router.get("/memory/{record_id}")
    async def memory_get(record_id: UUID, actor: Annotated[Actor, Depends(identity)]):
        return await memory_forward(actor, "GET", "/memory/" + str(record_id))

    @router.patch("/memory/{record_id}")
    async def memory_edit(record_id: UUID, request: Request, actor: Annotated[Actor, Depends(identity)]):
        return await memory_forward(actor, "PATCH", "/memory/" + str(record_id), await memory_body(request, actor))

    @router.delete("/memory/{record_id}")
    async def memory_forget(record_id: UUID, actor: Annotated[Actor, Depends(identity)]):
        return await memory_forward(actor, "DELETE", "/memory/" + str(record_id))

    @router.post("/memory/setting")
    async def memory_setting(request: Request, actor: Annotated[Actor, Depends(identity)]):
        return await memory_forward(actor, "POST", "/memory/setting", await memory_body(request, actor))

    @router.post("/memory/correction")
    async def memory_correction(request: Request, actor: Annotated[Actor, Depends(identity)]):
        return await memory_forward(actor, "POST", "/memory/correction", await memory_body(request, actor))

    @router.post("/brain/tasks")
    async def brain_task(request: Request, actor: Annotated[Actor, Depends(identity)]):
        return await memory_forward(actor, "POST", "/brain/tasks", await memory_body(request, actor))

    return router
