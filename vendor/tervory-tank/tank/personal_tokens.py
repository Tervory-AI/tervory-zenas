"""CLI-1 personal tokens: server-derived identity, local box, hash-only storage.

The tenant in the opaque token envelope selects a database scope only. No
identity is accepted until the complete token hash matches an unrevoked row.
Management endpoints deliberately require the original Workspace dependency.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

import command
from tenant_db import tenant_scope

router = APIRouter(prefix="/v1/personal-tokens", tags=["personal tokens"])
TOKEN = re.compile(r"tv1\.(us-east|za)\.([0-9a-f]{32})\.([A-Za-z0-9_-]{43})\Z")


class NewToken(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


async def workspace_actor(request: Request) -> command.Actor:
    # Do not permit a PAT to mint new PATs or manage Workspace credentials.
    return await command.identity(request)


def sessions_key() -> str:
    """The ZA own-session list/revoke key, or '' when unset or not distinct."""
    from main import TANK_API_KEY
    key = os.environ.get("TRIGGER_SIGNIN_SESSIONS_KEY", "").strip()
    others = {TANK_API_KEY.strip(), os.environ.get("TANK_HOMES_KEY", "").strip()} - {""}
    return "" if not key or key in others else key


SIGN_IN_PATHS = {"GET": re.compile(r"/v1/personal-tokens\Z"),
                 "DELETE": re.compile(r"/v1/personal-tokens/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")}


def sessions_key_may_manage(request: Request) -> bool:
    from main import box_region
    key = sessions_key()
    pattern = SIGN_IN_PATHS.get(request.method)
    return (bool(key) and box_region() == "za" and pattern is not None
            and pattern.fullmatch(request.url.path) is not None
            and command.key_matches(request.headers.get("x-tank-key", ""), key))


async def sign_in_actor(request: Request) -> command.Actor:
    """Workspace key, or (ZA box, list and revoke only) the dedicated key; never a CLI token."""
    if sessions_key_may_manage(request):
        return await command.signed_in_actor(request)
    return await command.identity(request)


# A CLI credential authenticates the CLI corridor and nothing else. register() puts
# this dependency in place of command.identity app-wide, and on ZA the CLI talks to
# the public Tank directly, so without this list a CLI token would also reach every
# other route built on command.identity (access, event bus, triggers, case studies,
# squads, repo, attachments, worker follow-ups...). It mirrors the Workspace corridor
# (workspace/lib/tank.ts personalCommandRequest) plus the founder-only model roster,
# which the route itself answers 404 for everyone else. Review of #506, self-review (b)/(c).
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
CLI_ROUTES = {
    "GET": re.compile(rf"/v1/command/(models|tools|code-mcp-policy|code-models|code-sessions|code-sessions/{_UUID}|threads|threads/{_UUID}(/events)?|tasks/{_UUID}/receipts)\Z"),
    "POST": re.compile(rf"/v1/command/(code-attachments|complete|threads|threads/{_UUID}/(turn|actions|code-turn|code-stream)|cards/{_UUID}|build)\Z"),
    "PATCH": re.compile(rf"/v1/command/threads/{_UUID}\Z"),
    "PUT": re.compile(r"/v1/command/code-mcp-policy\Z"),
}


def cli_route(request: Request) -> bool:
    pattern = CLI_ROUTES.get(request.method)
    return pattern is not None and pattern.fullmatch(request.url.path) is not None


async def identity(request: Request) -> command.Actor:
    authorization = request.headers.get("authorization")
    if authorization is None:
        return await command.identity(request)
    if not cli_route(request):
        # Same answer as an unknown route; no database lookup, nothing about the token.
        raise HTTPException(404, "Not Found")
    actor, _ = await bearer_identity(request, audience="cli")
    return actor


async def bearer_identity(request: Request, *, audience: str) -> tuple[command.Actor, Any]:
    """One hash lookup for both corridors; audience is a server-owned constant."""
    if audience not in {"cli", "mcp"}:
        raise ValueError("unknown credential audience")
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    match = TOKEN.fullmatch(token) if scheme.lower() == "bearer" else None
    if match is None:
        raise HTTPException(401, "Personal token refused")
    from main import box_region
    region, tenant_hex, _ = match.groups()
    if region != box_region():
        raise HTTPException(403, "Personal token belongs to another box")
    tenant = uuid.UUID(hex=tenant_hex)
    with tenant_scope(tenant):
        async with command.db().acquire() as conn:
            row = await conn.fetchrow(
                """SELECT token_id,user_id,client_id,name,identity_tenant_admin,trigger_signin_issuer,last_used_at < now() - interval '1 minute' AS stale FROM tank.personal_tokens
                WHERE tenant_id=$1 AND region=$2 AND token_hash=$3 AND revoked_at IS NULL
                AND audience=$4 AND trigger_signin AND (expires_at IS NULL OR expires_at > now())""",
                tenant, region, digest(token), audience,
            )
            if row is not None and row.get("stale") is not False:
                # At most one write a minute per credential; NULL means never used.
                await conn.execute("UPDATE tank.personal_tokens SET last_used_at=now() WHERE tenant_id=$1 AND token_id=$2",
                                   tenant, row["token_id"])
        if row is None:
            raise HTTPException(401, "Personal token refused")
        import trigger_signin_config as registry
        if row['trigger_signin_issuer'] not in registry.ISSUERS.values():
            raise HTTPException(401, "Personal token refused")
        await registry.refresh()
        registry._issuer.set(row['trigger_signin_issuer'])
        # Headers for user, tenant, role and squad are never used on this path.
        # Existing Tank authorization re-reads founder membership and squad grants.
        tenant_admin = row.get("identity_tenant_admin") is True
        return await command.authorized_actor(command.Actor(tenant, row["user_id"], False, tenant_admin=tenant_admin)), row


@router.post("", status_code=201)
async def create_token(body: NewToken, response: Response,
                       actor: command.Actor = Depends(workspace_actor)) -> dict[str, Any]:
    from main import box_region
    region = box_region()
    if region not in {"us-east", "za"}:
        raise HTTPException(503, "Personal tokens unavailable on this box")
    token = f"tv1.{region}.{actor.tenant_id.hex}.{secrets.token_urlsafe(32)}"
    with tenant_scope(actor.tenant_id):
        async with command.db().acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO tank.personal_tokens(tenant_id,user_id,region,name,token_hash,trigger_signin,identity_tenant_admin,trigger_signin_issuer) "
                "VALUES($1,$2,$3,$4,$5,true,$6,$7) RETURNING token_id,name,region,created_at",
                actor.tenant_id, actor.user_id, region, body.name, digest(token), actor.tenant_admin, __import__('trigger_signin_config')._issuer.get(),
            )
    response.headers["Cache-Control"] = "no-store"
    return {**dict(row), "token": token}


@router.get("")
async def list_tokens(response: Response,
                      actor: command.Actor = Depends(sign_in_actor)) -> list[dict[str, Any]]:
    with tenant_scope(actor.tenant_id):
        async with command.db().acquire() as conn:
            rows = await conn.fetch(
                "SELECT token_id,name,region,device_name,created_at,expires_at,refresh_expires_at,last_used_at,revoked_at "
                "FROM tank.personal_tokens "
                "WHERE tenant_id=$1 AND user_id=$2 AND trigger_signin AND trigger_signin_issuer=$3 ORDER BY created_at DESC,token_id",
                actor.tenant_id, actor.user_id, __import__('trigger_signin_config').issuer(),
            )
    response.headers["Cache-Control"] = "no-store"
    return [dict(row) for row in rows]


@router.delete("/{token_id}", status_code=204)
async def revoke_token(token_id: uuid.UUID, actor: command.Actor = Depends(sign_in_actor)) -> Response:
    with tenant_scope(actor.tenant_id):
        async with command.db().acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE tank.personal_tokens SET revoked_at=COALESCE(revoked_at,now()) "
                "WHERE tenant_id=$1 AND user_id=$2 AND token_id=$3 AND trigger_signin AND trigger_signin_issuer=$4 RETURNING token_id",
                actor.tenant_id, actor.user_id, token_id, __import__('trigger_signin_config').issuer(),
            )
    if row is None:
        raise HTTPException(404, "Personal token not found")
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


def register(app: Any) -> None:
    app.include_router(router)
    # CLI sign-in by device approval issues rows in this same table.
    app.include_router(__import__("device_login").router)
    # The same command endpoints, validation, confirms and receipts serve both
    # clients. Do not broaden Tank's global service-key dependency.
    app.dependency_overrides[command.identity] = identity
