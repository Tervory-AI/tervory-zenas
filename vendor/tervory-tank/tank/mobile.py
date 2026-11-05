"""Tenant-owned mobile sessions, device registrations and unsent chat drafts."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from command import Actor, identity, key_matches
from tenant_db import tenant_scope
import vault

router = APIRouter(prefix="/v1/mobile")
CALLBACK = "https://app.tervory.com/mobile/callback"
LIMIT = 5 * 1024 * 1024


@router.get("/bootstrap")
async def bootstrap(actor: Actor = Depends(identity)):
    from mobile_push import push_configured
    return {"push_enabled": push_configured()}


def db():
    from main import pool
    return pool()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def unpack(value):
    return json.loads(value) if isinstance(value, str) else dict(value)


async def workspace_key(request: Request):
    from main import TANK_API_KEY
    if not key_matches(request.headers.get("x-tank-key", ""), TANK_API_KEY):
        raise HTTPException(401)


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Handshake(Body):
    challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    org_id: str = Field(max_length=200)
    org_name: str = Field(max_length=200)


class Exchange(Body):
    code: str = Field(pattern=r"^[a-f0-9-]{36}_[A-Za-z0-9_-]{43}$")
    code_verifier: str = Field(pattern=r"^[A-Za-z0-9_-]{43,128}$")
    redirect_uri: Literal[CALLBACK]


class Session(Body):
    token: str = Field(pattern=r"^[a-f0-9-]{36}_[A-Za-z0-9_-]{43}$")


async def checked_principal(principal, tenant):
    import trigger_signin_config as registry
    from main import box_region
    await registry.refresh()
    try:
        if (not isinstance(principal, dict) or principal.get("tenantId") != str(tenant)
                or principal.get("issuer") != registry.ISSUER
                or principal.get("boxRegion") != box_region()
                or type(principal.get("tenantAdmin")) is not bool
                or not isinstance(principal.get("userId"), str)
                or not 1 <= len(principal["userId"]) <= 200
                or registry.config(box_region())["organisations"].get(principal.get("orgId")) != str(tenant)):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise HTTPException(401, "Mobile session refused") from None
    return principal


async def session_identity(token: str):
    if not re.fullmatch(r"[a-f0-9-]{36}_[A-Za-z0-9_-]{43}", token):
        raise HTTPException(401, "Mobile session refused")
    try:
        tenant = uuid.UUID(token[:36])
    except ValueError:
        raise HTTPException(401, "Mobile session refused") from None
    with tenant_scope(tenant):
        async with db().acquire() as conn, conn.transaction():
            row = await conn.fetchrow("SELECT identity,user_id FROM tank.mobile_sessions WHERE tenant_id=$1 AND token_hash=$2 AND expires_at>now() AND NOT revoked", tenant, digest(token))
    if row is None:
        raise HTTPException(401, "Mobile session refused")
    principal = await checked_principal(unpack(row["identity"]), tenant)
    if principal["userId"] != row["user_id"]:
        raise HTTPException(401, "Mobile session refused")
    return principal


async def native_identity(request: Request):
    if (getattr(request.state, "trigger_signin", {}).get("credential_type") != "native"
            or not request.headers.get("x-trigger-signin-token")
            or request.headers.get("authorization")):
        raise HTTPException(403, "Native sign-in required")
    return await identity(request)


@router.post("/handshake")
async def handshake(body: Handshake, actor: Actor = Depends(native_identity)):
    import trigger_signin_config as registry
    from main import box_region
    code = f"{actor.tenant_id}_{secrets.token_urlsafe(32)}"
    principal = {"tenantId": str(actor.tenant_id), "userId": actor.user_id,
                 "orgId": body.org_id, "orgName": body.org_name, "tenantAdmin": actor.tenant_admin,
                 "issuer": registry.issuer(), "boxRegion": box_region()}
    await checked_principal(principal, actor.tenant_id)
    async with db().acquire() as conn, conn.transaction():
        await conn.execute("INSERT INTO tank.mobile_codes(tenant_id,code_hash,challenge,identity) VALUES($1,$2,$3,$4::jsonb)", actor.tenant_id, digest(code), body.challenge, json.dumps(principal))
    return {"code": code}


@router.post("/session/exchange", dependencies=[Depends(workspace_key)])
async def exchange(body: Exchange):
    try:
        tenant = uuid.UUID(body.code[:36])
    except ValueError:
        raise HTTPException(401) from None
    challenge = base64.urlsafe_b64encode(hashlib.sha256(body.code_verifier.encode()).digest()).decode().rstrip("=")
    token = f"{tenant}_{secrets.token_urlsafe(32)}"
    with tenant_scope(tenant):
        async with db().acquire() as conn, conn.transaction():
            row = await conn.fetchrow("SELECT challenge,identity FROM tank.mobile_codes WHERE tenant_id=$1 AND code_hash=$2 AND expires_at>now() FOR UPDATE", tenant, digest(body.code))
            if row is None or not hmac.compare_digest(row["challenge"], challenge):
                raise HTTPException(401)
            await checked_principal(unpack(row["identity"]), tenant)
            await conn.execute("DELETE FROM tank.mobile_codes WHERE tenant_id=$1 AND code_hash=$2", tenant, digest(body.code))
            await conn.execute("INSERT INTO tank.mobile_sessions(tenant_id,token_hash,user_id,identity) VALUES($1,$2,$3,$4::jsonb)", tenant, digest(token), unpack(row["identity"])["userId"], json.dumps(unpack(row["identity"])))
    return {"token": token}


@router.post("/session/resolve", dependencies=[Depends(workspace_key)])
async def resolve(body: Session):
    principal = await session_identity(body.token)
    from command import authorized_actor
    import trigger_signin_config as registry
    context = registry._issuer.set(principal["issuer"])
    try:
        with tenant_scope(uuid.UUID(principal["tenantId"])):
            await authorized_actor(Actor(uuid.UUID(principal["tenantId"]), principal["userId"], False,
                                         tenant_admin=principal["tenantAdmin"]))
    finally:
        registry._issuer.reset(context)
    return principal


@router.post("/session/revoke", dependencies=[Depends(workspace_key)])
async def revoke(body: Session):
    principal = await session_identity(body.token)
    tenant = uuid.UUID(principal["tenantId"])
    with tenant_scope(tenant):
        async with db().acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM tank.mobile_devices WHERE tenant_id=$1 AND session_hash=$2", tenant, digest(body.token))
            await conn.execute("UPDATE tank.mobile_sessions SET revoked=true WHERE tenant_id=$1 AND token_hash=$2", tenant, digest(body.token))
    return {"revoked": True}


class Device(Body):
    platform: Literal["ios", "android"]
    token: str = Field(min_length=16, max_length=4096, pattern=r"^[A-Za-z0-9_:\-]+$")


@router.post("/devices")
async def register_device(body: Device, request: Request, actor: Actor = Depends(identity)):
    session = request.headers.get("x-mobile-session", "")
    principal = await session_identity(session)
    if principal["userId"] != actor.user_id or principal["tenantId"] != str(actor.tenant_id):
        raise HTTPException(403)
    token_hash = digest(body.token)
    encrypted = vault.seal({"token": body.token}, f"mobile:{actor.tenant_id}:{actor.user_id}:{token_hash}")
    async with db().acquire() as conn, conn.transaction():
        try:
            await conn.execute("INSERT INTO tank.mobile_devices(tenant_id,user_id,token_hash,token,platform,session_hash) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(token_hash) DO UPDATE SET user_id=EXCLUDED.user_id,token=EXCLUDED.token,platform=EXCLUDED.platform,session_hash=EXCLUDED.session_hash,registered_at=now()", actor.tenant_id, actor.user_id, token_hash, encrypted, body.platform, digest(session))
        except (__import__("asyncpg").UniqueViolationError, __import__("asyncpg").InsufficientPrivilegeError):
            raise HTTPException(409, "Sign out of the previous workspace before registering this device.") from None
    return {"registered": True}


class SharedFile(Body):
    name: str = Field(min_length=1, max_length=180)
    data: str = Field(max_length=7 * 1024 * 1024)


class Share(Body):
    share_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,80}$")
    text: str = Field(default="", max_length=20000)
    files: list[SharedFile] = Field(default_factory=list, max_length=5)


def share_files(body: Share) -> list[tuple[str, bytes]]:
    result = []
    size = len(body.text.encode())
    for file in body.files:
        if any(ord(char) < 32 or ord(char) == 127 or char in "/\\" for char in file.name) or file.name in {".", ".."}:
            raise HTTPException(422, "Invalid filename")
        try:
            content = base64.b64decode(file.data, validate=True)
        except ValueError:
            raise HTTPException(422, "Invalid file") from None
        size += len(content)
        result.append((file.name, content))
    if size > LIMIT:
        raise HTTPException(413)
    if not body.text and not result:
        raise HTTPException(422)
    return result


@router.post("/shares")
async def create_share(body: Share, actor: Actor = Depends(identity)):
    files = share_files(body)
    async with db().acquire() as conn, conn.transaction():
        lock_key = ":".join(("mobile", str(actor.tenant_id), actor.user_id, body.share_id))
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", lock_key)
        existing = await conn.fetchrow("SELECT id FROM tank.mobile_drafts WHERE tenant_id=$1 AND user_id=$2 AND share_id=$3", actor.tenant_id, actor.user_id, body.share_id)
        if existing:
            return {"draft_id": str(existing["id"])}
        thread_id, draft_id = uuid.uuid4(), uuid.uuid4()
        state = {"messages": [], "steps": {}, "cards": {}, "receipts": [], "active_turn": None}
        await conn.execute("INSERT INTO tank.command_threads(thread_id,tenant_id,user_id,title,state,trigger_signin_issuer) VALUES($1,$2,$3,'Shared with Tervory',$4::jsonb,$5)", thread_id, actor.tenant_id, actor.user_id, json.dumps(state), __import__("trigger_signin_config").issuer())
        attachments = []
        for name, content in files:
            attachment_id = uuid.uuid4()
            await conn.execute("INSERT INTO tank.command_attachments(attachment_id,thread_id,tenant_id,user_id,name,sha256,content) VALUES($1,$2,$3,$4,$5,$6,$7)", attachment_id, thread_id, actor.tenant_id, actor.user_id, name, hashlib.sha256(content).hexdigest(), content)
            attachments.append({"attachment_id": str(attachment_id), "name": name, "content_type": "application/octet-stream", "size": len(content), "model_readable": False})
        payload = {"thread_id": str(thread_id), "text": body.text, "attachments": attachments}
        await conn.execute("INSERT INTO tank.mobile_drafts(id,tenant_id,user_id,share_id,payload) VALUES($1,$2,$3,$4,$5::jsonb)", draft_id, actor.tenant_id, actor.user_id, body.share_id, json.dumps(payload))
    return {"draft_id": str(draft_id)}


@router.get("/shares/{draft_id}")
async def get_share(draft_id: uuid.UUID, actor: Actor = Depends(identity)):
    async with db().acquire() as conn, conn.transaction():
        row = await conn.fetchrow("SELECT payload FROM tank.mobile_drafts WHERE tenant_id=$1 AND user_id=$2 AND id=$3 AND expires_at>now()", actor.tenant_id, actor.user_id, draft_id)
    if row is None:
        raise HTTPException(404)
    return unpack(row["payload"])
