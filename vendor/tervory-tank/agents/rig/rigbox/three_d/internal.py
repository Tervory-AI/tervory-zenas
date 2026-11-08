"""Workspace corridor: room key, explicit tenant, bounded operations only."""
from pathlib import Path
from uuid import UUID
import hmac
import os

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import Field, ValidationError
from chassis.tenant_db import activate_tenant

from rigbox.service import RigError
from .api import engine, Mount
from .contracts import Job, Review, Strict

router = APIRouter(prefix="/v1/rig/internal/3d/workspace")


class Call(Strict):
    tenant_id: UUID
    actor: str = Field(default="", max_length=160, pattern=r"^[A-Za-z0-9_-]*$")
    payload: dict = Field(default_factory=dict)


class Item(Strict):
    id: UUID


class Approval(Item):
    review: Review


class Runtime(Strict):
    name: str


@router.post("/{operation}")
async def workspace(operation: str, body: Call, request: Request):
    expected = os.environ.get("TANK_KEY", "")
    presented = request.headers.get("x-tank-key", "")
    if not expected or not presented or not hmac.compare_digest(expected.encode(), presented.encode()):
        raise RigError("unauthorized", "Room authorization is required.", 401)
    from rigbox.rooms import require_owner
    require_owner(body.tenant_id)
    activate_tenant(body.tenant_id)
    value, tenant = engine(request), str(body.tenant_id)
    if operation == "capabilities" and not body.payload:
        return await value.capabilities(tenant)
    if not body.actor:
        raise RigError("unauthorized", "Administrator authorization is required.", 403)
    if operation == 'page-settings':
        from .page import PageConfig, save
        try:
            config = PageConfig.model_validate(body.payload)
        except ValidationError:
            raise RigError('invalid', 'The request is not valid.', 422) from None
        return await save(value.catalogue, tenant, config)
    await value.require(tenant)
    try:
        if operation == "dashboard" and not body.payload:
            rows = await value.store.jobs(tenant)
            # Bounded polling; conversion itself runs in the separate service.
            for row in [r for r in rows if r["status"] in {"queued", "running"}][:10]:
                await value.poll(tenant, row["id"])
            return {"jobs": [value.job_view(r) for r in await value.store.jobs(tenant)]}
        if operation == "submit":
            return await value.submit(tenant, Job.model_validate(body.payload))
        if operation == 'poll':
            item = Item.model_validate(body.payload)
            return await value.poll(tenant, item.id)
        if operation == "approve":
            item = Approval.model_validate(body.payload)
            return await value.approve(tenant, item.id, item.review, body.actor)
        if operation == "mounts":
            return await value.mount_points(tenant, **Mount.model_validate(body.payload).model_dump())
        if operation == "reset":
            item = Item.model_validate(body.payload)
            return {"revision": await value.store.kiosk(tenant, item.id, reset=True)}
        if operation == "preview":
            item = Item.model_validate(body.payload)
            return Response(await value.asset(tenant, item.id, preview=True), media_type="model/gltf-binary",
                            headers={"Cache-Control": "private, no-store"})
        if operation == "runtime":
            name = Runtime.model_validate(body.payload).name
            allowed = {"runtime.js": "text/javascript", "basis/basis_transcoder.js": "text/javascript",
                       "basis/basis_transcoder.wasm": "application/wasm"}
            if name in allowed:
                data = (Path(__file__).with_name("widget") / "preview" / name).read_bytes()
                return Response(data, media_type=allowed[name], headers={"Cache-Control": "private, no-store"})
    except ValidationError:
        raise RigError("invalid", "Check the product, file names, dimensions and review choices.", 422) from None
    except OSError:
        raise RigError("unavailable", "The product preview is not available.", 503) from None
    raise RigError("invalid", "This action is not available.", 422)
