"""Lifecycle API shared by offline fixture routes and the isolated control service."""
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request

from .enforce_lifecycle import (
    CoolifyEnforceBackend,
    FixtureComposeBackend,
    Refused,
    coolify_configured,
    plan_create,
    plan_destroy,
    receipt_for,
)


def register(app, require_tank, bounded_json):
    @app.post("/ghost/autumbra/enforce/plan-create", dependencies=[Depends(require_tank)])
    async def autumbra_enforce_plan_create(request: Request):
        raw = await bounded_json(request, 4_000)
        try:
            data = json.loads(raw.decode("utf-8"))
            tenant_id = str(data["tenant_id"])
            existing = int(data.get("existing_count") or 0)
            execute = bool(data.get("execute"))
        except Exception:
            raise HTTPException(422, "Create plan was not understood.") from None
        if execute and coolify_configured():
            try:
                async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as http:
                    backend = CoolifyEnforceBackend.from_env(http)
                    plan = await backend.create_plan(tenant_id)
                    receipt = await backend.create(plan)
                return {"plan": plan, "receipt": receipt}
            except (ValueError, RuntimeError) as exc:
                raise HTTPException(422, str(exc)) from None
        try:
            plan = plan_create(tenant_id, existing_count=existing)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from None
        if execute:
            root = os.environ.get("AUTUMBRA_FIXTURE_COMPOSE_ROOT", "").strip()
            if not root:
                raise HTTPException(422, "Fixture compose root is not configured.")
            receipt = FixtureComposeBackend(Path(root)).create(plan)
        else:
            receipt = receipt_for(plan)
        return {"plan": plan, "receipt": receipt}

    @app.post("/ghost/autumbra/enforce/plan-destroy", dependencies=[Depends(require_tank)])
    async def autumbra_enforce_plan_destroy(request: Request):
        raw = await bounded_json(request, 4_000)
        try:
            data = json.loads(raw.decode("utf-8"))
            plan = plan_destroy(str(data["tenant_id"]))
            execute = bool(data.get("execute"))
        except Exception:
            raise HTTPException(422, "Destroy plan was not understood.") from None
        if execute and coolify_configured():
            try:
                async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as http:
                    receipt = await CoolifyEnforceBackend.from_env(http).destroy(plan)
                return {"plan": plan, "receipt": receipt}
            except (ValueError, RuntimeError) as exc:
                raise HTTPException(422, str(exc)) from None
        if execute:
            root = os.environ.get("AUTUMBRA_FIXTURE_COMPOSE_ROOT", "").strip()
            if not root:
                raise HTTPException(422, "Fixture compose root is not configured.")
            receipt = FixtureComposeBackend(Path(root)).destroy(plan)
        else:
            receipt = receipt_for(plan)
        return {"plan": plan, "receipt": receipt}

    @app.get("/ghost/autumbra/enforce/list", dependencies=[Depends(require_tank)])
    async def autumbra_enforce_list():
        if not coolify_configured():
            raise HTTPException(422, "Autumbra Coolify settings are not configured.")
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as http:
                rows = await CoolifyEnforceBackend.from_env(http).list()
                return {"applications": [{key: row[key] for key in ("name", "uuid", "status")} for row in rows]}
        except Refused as exc:
            raise HTTPException(422, str(exc)) from None


async def verify_lifecycle(request: Request, setting: str):
    key = os.environ.get(setting, "")
    timestamp = request.headers.get("X-Lifecycle-Timestamp", "")
    signature = request.headers.get("X-Lifecycle-Signature", "")
    try:
        fresh = abs(time.time() - int(timestamp)) <= 60
    except ValueError:
        fresh = False
    if not key or not fresh or not signature:
        raise HTTPException(401, "Lifecycle authentication required.")
    from .app import bounded_json
    body = await bounded_json(request, 4_000)
    # Historical Autumbra/Rig form (timestamp\nmethod\npath\n + body) stays valid.
    # When Tank also sends verified tenant+user headers, accept only the bound form
    # so a shared lifecycle key alone cannot assert a forged caller.
    tenant = request.headers.get("X-Tervory-Tenant", "").strip()
    user = request.headers.get("X-Tervory-User", "").strip()
    candidates = [f"{timestamp}\n{request.method}\n{request.url.path}\n".encode() + body]
    if tenant and user:
        candidates = [f"{timestamp}\n{request.method}\n{request.url.path}\n{tenant}\n{user}\n".encode() + body]
    if not any(hmac.compare_digest(hmac.new(key.encode(), message, hashlib.sha256).hexdigest().encode(), signature.encode())
               for message in candidates):
        raise HTTPException(401, "Lifecycle authentication required.")


async def require_lifecycle(request: Request):
    await verify_lifecycle(request, "AUTUMBRA_LIFECYCLE_KEY")


async def require_rig_lifecycle(request: Request):
    await verify_lifecycle(request, "RIG_LIFECYCLE_KEY")


def create_app():
    from .app import bounded_json
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    @app.get("/health")
    async def health():
        from .configuration import setting_status
        status = setting_status("lifecycle")
        return {"ok": True, "ready": status["configured"], **status}
    register(app, require_lifecycle, bounded_json)
    from .rig_lifecycle import register as register_rig
    register_rig(app, require_rig_lifecycle)
    return app
