"""Fail closed at the room boundary; durable, encrypted command receipts."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
from uuid import UUID

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from chassis.tenant_db import tenant_scope
from chassis.storage import MAX_OBJECT_BYTES
from agents.zenas.pool.region import RegionError
from .seal import key_from_env, open_seal, seal

REQUEST_SECONDS = 600  # Existing regional proxy / training deadline.
CALL_SECONDS = 120  # Existing scheduled model-client deadline.
ERROR = "Zenas could not be reached. Check your connection and try again."
IDENTITY_HEADERS = (
    "x-moot-tenant", "x-moot-actor", "x-moot-founder",
    "x-street-law-tenant", "x-street-law-actor", "x-street-law-founder",
)
log = logging.getLogger("agent")


async def bounded_call(call):
    # No transport replay: an interrupted mutation may already have committed.
    async with asyncio.timeout(CALL_SECONDS):
        return await call


async def guard(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    expected = os.environ.get("TANK_KEY", "")
    supplied = request.headers.get("x-tank-key", "")
    if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
        return JSONResponse({"detail": "room_key_required"}, status_code=401)
    try:
        async with asyncio.timeout(REQUEST_SECONDS):
            return await _command(request, call_next)
    except RegionError:
        return JSONResponse({"detail": "wrong_region"}, status_code=403)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    except Exception as exc:
        # Exception strings can contain URLs, query credentials, SQL or source text.
        log.warning("Zenas request failed (%s)", type(exc).__name__)
        return JSONResponse({"detail": ERROR}, status_code=503)


async def _command(request, call_next):
    # Binary storage PUTs keep the existing streaming size check. Only JSON
    # commands need buffering for validation and the replay fingerprint.
    body = b""
    if request.method == "POST":
        buffer = bytearray()
        async for chunk in request.stream():
            if len(buffer) + len(chunk) > MAX_OBJECT_BYTES:
                return JSONResponse({"detail": "Storage object exceeds 32 MiB"}, status_code=413)
            buffer.extend(chunk)
        body = bytes(buffer)
        request._body = body  # Preserve Starlette's body cache for downstream validation.
    try:
        parsed = json.loads(body) if body else {}
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"detail": "invalid_request"}, status_code=422)
    if not isinstance(parsed, dict):
        return JSONResponse({"detail": "invalid_request"}, status_code=422)
    tenants = request.query_params.getlist("tenant_id")
    if "tenant_id" in parsed:
        tenants.append(parsed["tenant_id"])
    attested = request.headers.get("x-zenas-tenant")
    if attested:
        tenants.append(attested)
    for name in ("x-moot-tenant", "x-street-law-tenant"):
        if request.headers.get(name):
            tenants.append(request.headers[name])
    try:
        owners = {str(UUID(str(value))) for value in tenants}
    except (ValueError, TypeError):
        return JSONResponse({"detail": "invalid_request"}, status_code=422)
    if len(owners) > 1:
        return JSONResponse({"detail": "tenant_mismatch"}, status_code=403)
    # Consume the shared assertion before any personal read or cached replay.
    from agents.zenas.memory_api import is_moot_path, verified_request_actor, refuse_scope_widen
    actor = await verified_request_actor(request, next(iter(owners), None),
                                 required=request.url.path.startswith("/memory/"))
    request.state.command_actor = actor
    request.state.zenas_actor = None if is_moot_path(request.url.path) else actor
    if actor is not None:
        refuse_scope_widen(actor, body_tenant_id=next(iter(owners), None), body_user_id=parsed.get("user_id"))
    command = request.headers.get("idempotency-key")
    if request.method != "POST" or not command:
        from .savings import command as run_command
        return await run_command(request, call_next, next(iter(owners), None))
    try:
        command = str(UUID(command))
    except ValueError:
        return JSONResponse({"detail": "invalid_request"}, status_code=422)
    tenant = next(iter(owners), "00000000-0000-0000-0000-000000000001")
    key = key_from_env()
    if key is None:
        return JSONResponse({"needs_settings": ["ZENAS_PII_KEY"]}, status_code=503)
    # Include the route and full query. HMAC prevents guessing private questions.
    identity = json.dumps({"legacy": {name: request.headers.get(name, "") for name in IDENTITY_HEADERS},
        "verified": {"tenant": str(actor.tenant_id), "user": actor.user_id, "role": actor.role} if actor else None}, sort_keys=True).encode()
    fingerprint = hmac.new(key, request.url.path.encode() + b"?" + request.url.query.encode() + b"\0" + identity + b"\0" + body,
                           hashlib.sha256).hexdigest()
    pool = request.app.state.pool
    with tenant_scope(tenant):
        async with pool.acquire() as conn:
            claimed = await conn.fetchval("""INSERT INTO agent_zenas.command_receipts
                (tenant_id, request_id, fingerprint, user_id) VALUES ($1,$2,$3,$4)
                ON CONFLICT (tenant_id,request_id) DO NOTHING RETURNING request_id""",
                UUID(tenant), UUID(command), fingerprint, actor.user_id if actor else None)
            if not claimed:
                row = await conn.fetchrow("""SELECT fingerprint,status,receipt FROM agent_zenas.command_receipts
                    WHERE tenant_id=$1 AND request_id=$2""", UUID(tenant), UUID(command))
                if row is None or row["fingerprint"] != fingerprint:
                    return JSONResponse({"detail": "idempotency_conflict"}, status_code=409)
                if row["receipt"] is None:
                    return JSONResponse({"detail": ERROR, "code": "request_pending"}, status_code=409)
                payload = json.loads(open_seal(row["receipt"], key))
                return Response(base64.b64decode(payload["body"]), status_code=row["status"],
                                headers={"content-type": payload["type"], "idempotency-key": command})
    # The claim commits before any external effect. A process loss leaves it
    # pending, never eligible for automatic execution a second time.
    from .savings import command as run_command
    response = await run_command(request, call_next, tenant)
    content = response.body if hasattr(response, "body") else b"".join([chunk async for chunk in response.body_iterator])
    token = seal(json.dumps({"body": base64.b64encode(content).decode(),
                             "type": response.headers.get("content-type", "application/json")}), key)
    with tenant_scope(tenant):
        async with pool.acquire() as conn:
            await conn.execute("""UPDATE agent_zenas.command_receipts SET status=$3,receipt=$4
                WHERE tenant_id=$1 AND request_id=$2""", UUID(tenant), UUID(command), response.status_code, token)
    return Response(content, status_code=response.status_code,
                    headers={**response.headers, "idempotency-key": command}, background=response.background)
