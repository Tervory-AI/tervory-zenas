"""Additive routes; reuse the public-key/origin and administrator boundaries."""
from __future__ import annotations

import hmac
import os
import re
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import Field, model_validator

from rigbox.api import _admin, _auth, _ok, _tenant
from rigbox.service import RigError
from .contracts import Assembly, Job, NAME, Review, Strict

router = APIRouter(prefix="/v1/rig")


class Entitlement(Strict):
    tenant_id: UUID
    enabled: bool
    price_amount: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")


class Mount(Strict):
    part: str = Field(pattern=NAME)
    centre: str = Field(pattern=NAME)
    centre_anchor: str = Field(pattern=NAME)
    part_anchor: str = Field(pattern=NAME)


class Quote(Strict):
    notice_version: str | None = Field(default=None, min_length=64, max_length=64)
    assembly: Assembly
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=32)

    @model_validator(mode="after")
    def contact(self):
        self.name, self.email, self.phone = self.name.strip(), self.email.strip(), self.phone.strip()
        if not self.name or not (self.email or self.phone):
            raise ValueError("Enter your name and a phone number or email")
        if self.email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", self.email):
            raise ValueError("Enter a valid email")
        if self.phone and (not re.fullmatch(r"\+?[0-9 ()-]+", self.phone)
                           or not 7 <= sum(c.isdigit() for c in self.phone) <= 15):
            raise ValueError("Enter a valid phone number")
        return self


def engine(request):
    value = getattr(request.app.state, "rig_three_d", None)
    if value is None:
        raise RigError("unavailable", "3D is not ready.", 503)
    return value


@router.get("/3d/capabilities")
async def capabilities(request: Request):
    await _auth(request)
    return _ok(request, await engine(request).capabilities(_tenant(request)))


@router.post("/3d/assembly")
async def assembly(request: Request, body: Assembly):
    await _auth(request)
    value = await engine(request).assembly(_tenant(request), body)
    value.pop("setup", None)  # Internal repricing/quote data never goes to the viewer.
    return _ok(request, value)


@router.get("/3d/assets/{job_id}")
async def asset(request: Request, job_id: UUID):
    await _auth(request)
    data = await engine(request).asset(_tenant(request), job_id)
    response = _ok(request, {})
    return Response(data, media_type="model/gltf-binary", headers={
        **{k: v for k, v in response.headers.items() if k not in {"content-length", "content-type"}},
        "Cache-Control": "private, no-store", "Vary": "Origin, X-Rig-Key", "X-Content-Type-Options": "nosniff",
    })


@router.post("/3d/quote")
async def quote(request: Request, body: Quote):
    await _auth(request)
    # Always revalidate current fits/prices online. Offline contact is never
    # queued or cached on a shared kiosk, and no receipt means no success UI.
    value = engine(request)
    built = await value.assembly(_tenant(request), body.assembly)
    if not built["available"]:
        raise RigError("unavailable", "The quote could not be confirmed. Reconnect and try again.", 409)
    from rigbox.quotes.notice import notice, receipt
    retailer = await value.catalogue.store.retailer(_tenant(request)) or {}
    shown = receipt(notice(retailer.get("quote_notice")), body.notice_version)
    payload = {"kind": "setup", "setup": built["setup"]}
    if shown:
        payload["notice_receipt"] = shown
    # assembly() already rebuilds current approved fits and prices. Reuse the
    # existing durable quote/handoff interface, which also accepts phone contacts.
    contact = {key: value for key, value in {"name": body.name, "email": body.email, "phone": body.phone}.items() if value}
    ack = await value.catalogue._store_quote(_tenant(request), payload, contact)
    return _ok(request, ack)


@router.get("/3d/kiosks/{kiosk_id}")
async def kiosk(request: Request, kiosk_id: UUID):
    await _auth(request)
    await engine(request).require(_tenant(request))
    return _ok(request, {"revision": await engine(request).store.kiosk(_tenant(request), kiosk_id)})


@router.post("/admin/3d/kiosks/{kiosk_id}/reset")
async def reset(request: Request, kiosk_id: UUID):
    await _admin(request)
    await engine(request).require(_tenant(request))
    return _ok(request, {"revision": await engine(request).store.kiosk(_tenant(request), kiosk_id, reset=True)})


@router.post("/internal/3d/entitlement")
async def entitlement(request: Request, body: Entitlement):
    # Retailer admin and publishable keys cannot grant a paid entitlement.
    expected = os.environ.get("TANK_KEY", "")
    presented = request.headers.get("x-tank-key", "")
    if not expected or not presented or not hmac.compare_digest(expected.encode(), presented.encode()):
        raise RigError("unauthorized", "Room authorization is required.", 401)
    await engine(request).store.set_entitlement(body.tenant_id, body.enabled, body.price_amount, body.currency)
    return {"saved": True}


@router.post("/admin/3d/jobs", status_code=202)
async def create_job(request: Request, body: Job):
    await _admin(request)
    response = _ok(request, await engine(request).submit(_tenant(request), body))
    response.status_code = 202
    return response


@router.get("/admin/3d/jobs/{job_id}")
async def job(request: Request, job_id: UUID):
    await _admin(request)
    return _ok(request, await engine(request).poll(_tenant(request), job_id))


@router.get("/admin/3d/review")
async def review_queue(request: Request):
    await _admin(request)
    await engine(request).require(_tenant(request))
    rows = await engine(request).store.review_queue(_tenant(request))
    return _ok(request, {"drafts": [{**r, "id": str(r["id"])} for r in rows]})


@router.get("/admin/3d/preview/{job_id}")
async def preview(request: Request, job_id: UUID):
    await _admin(request)
    return Response(await engine(request).asset(_tenant(request), job_id, preview=True),
                    media_type="model/gltf-binary", headers={"Cache-Control": "private, no-store"})


@router.post("/admin/3d/jobs/{job_id}/approve")
async def approve(request: Request, job_id: UUID, body: Review):
    row = await _admin(request)
    # The stored key hash identifies the authenticated reviewer, never raw key material.
    reviewer = str(row.get("key_hash") or "tenant-admin")
    return _ok(request, await engine(request).approve(_tenant(request), job_id, body, reviewer))


@router.post("/admin/3d/mounts")
async def mounts(request: Request, body: Mount):
    await _admin(request)
    return _ok(request, await engine(request).mount_points(_tenant(request), **body.model_dump()))


def mount(app):
    app.include_router(router)
    from .internal import router as workspace_router
    app.include_router(workspace_router)
