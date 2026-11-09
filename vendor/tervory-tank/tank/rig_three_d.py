"""RIG-5 Workspace proxy. No direct browser or model-service access."""
import json
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response


def build_router(tank):
    router = APIRouter()
    operations = {"capabilities", "dashboard", "submit", "approve", "mounts", "reset", "preview", "runtime", "poll", "page-settings"}

    @router.post("/v1/rig/3d/workspace/{operation}", dependencies=[Depends(tank.require_key)])
    async def call(operation: str, request: Request):
        if operation not in operations:
            raise HTTPException(404, "This action is not available.")
        body = await tank._rig_body(request)
        tenant = tank._rig_tenant(request, body)
        actor = request.headers.get("x-tervory-user", "")
        if operation != "capabilities" and not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", actor):
            raise HTTPException(403, "Administrator authorization is required.")
        # The authenticated Workspace derives actor and tenant from Trigger sign-in; never
        # forward browser-provided headers, paths, credentials or an entitlement write.
        payload = body.get("payload", {})
        if not isinstance(payload, dict) or set(body) - {"tenant_id", "payload"}:
            raise HTTPException(422, "The request is not valid.")
        if operation == "submit":
            try:
                if UUID(str(payload.get("tenant_id"))) != tenant:
                    raise ValueError
            except ValueError:
                raise HTTPException(403, "This request belongs to another shop.") from None
        content = json.dumps({"tenant_id": str(tenant), "actor": actor, "payload": payload}).encode()
        response = await tank._rig_call("POST", "/v1/rig/internal/3d/workspace/" + operation, [], content, timeout=30)
        if response.status_code >= 400:
            status = response.status_code if response.status_code in {401, 403, 404, 409, 422, 503} else 502
            raise HTTPException(status, "The 3D request could not be completed. Check the details and try again.")
        if operation in {"preview", "runtime"}:
            data = response.content
            media = response.headers.get("content-type", "").split(";")[0]
            allowed = {"model/gltf-binary"} if operation == "preview" else {"text/javascript", "application/wasm"}
            if media not in allowed or not data or len(data) > 5_000_000:
                raise HTTPException(502, "The product preview is not available.")
            return Response(data, media_type=media, headers={"Cache-Control": "private, no-store",
                                                            "X-Content-Type-Options": "nosniff"})
        result = tank._rig_response(response, tenant)
        result.headers["Cache-Control"] = "private, no-store"
        return result

    return router
