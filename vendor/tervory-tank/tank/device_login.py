"""Trigger sign-in device grants and existing hash-only credential rotation."""
from __future__ import annotations

import re
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

import command
import personal_tokens as pat
from tenant_db import tenant_scope

router = APIRouter(prefix="/v1/command/device", tags=["cli device login"])

CODE_TTL = timedelta(minutes=10)
POLL_INTERVAL_S = 5
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
MAX_PENDING = 500  # per box; bounds unauthenticated row creation
# Unambiguous consonants only: no vowels (no words), no 0/O or 1/I confusion.
ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
USER_CODE = re.compile(r"[BCDFGHJKLMNPQRSTVWXZ]{4}-[BCDFGHJKLMNPQRSTVWXZ]{4}\Z")
DEVICE_CODE = re.compile(r"tvd1\.(us-east|za)\.([A-Za-z0-9_-]{43})\Z")
REFRESH = re.compile(r"tvr1\.(us-east|za)\.([0-9a-f]{32})\.([A-Za-z0-9_-]{43})\Z")
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def workspace_url() -> str:
    """Shared public Workspace origin, also used by private report links."""
    return (os.environ.get("TERVORY_WORKSPACE_URL") or "https://app.tervory.com").rstrip("/")


class NewCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_name: str = Field(min_length=1, max_length=100)
    approval: Literal["trigger-signin"] | None = None


class Poll(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_code: str = Field(min_length=1, max_length=200)


class Refresh(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(min_length=1, max_length=200)


def now() -> datetime:
    return datetime.now(timezone.utc)


def region() -> str:
    from main import box_region
    value = box_region()
    if value not in {"us-east", "za"}:
        raise HTTPException(503, "CLI sign-in is unavailable on this box")
    return value


def clean_name(value: str) -> str:
    name = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", value).strip()
    if not name:
        raise HTTPException(422, "A device name is required")
    return name[:100]


def user_code() -> str:
    letters = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"{letters[:4]}-{letters[4:]}"


def error(code: str, status: int = 400) -> JSONResponse:
    # RFC 8628 section 3.5 error names; never echo the presented secret.
    return JSONResponse({"error": code}, status_code=status, headers=NO_STORE)


def credential_pair(box: str, tenant: uuid.UUID) -> tuple[str, str]:
    access = f"tv1.{box}.{tenant.hex}.{secrets.token_urlsafe(32)}"
    refresh = f"tvr1.{box}.{tenant.hex}.{secrets.token_urlsafe(32)}"
    return access, refresh


def grant(access: str, refresh: str, token_id: uuid.UUID, box: str, issued: datetime) -> dict[str, Any]:
    return {"token_type": "Bearer", "access_token": access, "refresh_token": refresh,
            "expires_in": int(ACCESS_TTL.total_seconds()), "refresh_expires_in": int(REFRESH_TTL.total_seconds()),
            "expires_at": (issued + ACCESS_TTL).isoformat(), "refresh_expires_at": (issued + REFRESH_TTL).isoformat(),
            "region": box, "credential_id": str(token_id)}


def platform():
    # Lookups keyed by a secret hash (device code) or by a short-lived user code
    # under a signed-in Workspace approval, before the tenant is known.
    return tenant_scope(None, administrative=True)


@router.post("/code")
async def create_code(body: NewCode) -> JSONResponse:
    region()
    import identity_oidc
    await identity_oidc.registry.refresh()
    if not identity_oidc.configured():
        return error("temporarily_unavailable", 503)
    from identity_device import create
    return await create(clean_name(body.device_name))


@router.post("/token")
async def poll(body: Poll) -> JSONResponse:
    region()
    import identity_oidc
    await identity_oidc.registry.refresh()
    if not identity_oidc.configured() or not body.device_code.startswith('tzd1.'):
        return error("invalid_grant")
    from identity_device import poll as identity_poll
    return await identity_poll(body.device_code)


@router.post("/refresh")
async def refresh(body: Refresh) -> JSONResponse:
    return await rotate_refresh(body.refresh_token, audience="cli")


async def rotate_refresh(presented: str, *, audience: str, client_id: str | None = None) -> JSONResponse:
    """CLI and OAuth share token format, lifetimes and atomic one-use rotation."""
    box = region()
    match = REFRESH.fullmatch(presented)
    if match is None or match.group(1) != box:
        return error("invalid_grant")
    tenant = uuid.UUID(hex=match.group(2))
    access, rotated = credential_pair(box, tenant)
    issued = now()
    expiries = (issued + ACCESS_TTL, issued + REFRESH_TTL)
    with tenant_scope(tenant):
        async with command.db().acquire() as conn:
            # Rotation: the presented refresh token and the old access token stop working.
            token_id = await conn.fetchval(
                """UPDATE tank.personal_tokens SET token_hash=$4,expires_at=$5,refresh_hash=$6,refresh_expires_at=$7
                WHERE tenant_id=$1 AND region=$2 AND refresh_hash=$3 AND revoked_at IS NULL
                AND audience=$8 AND client_id IS NOT DISTINCT FROM $9 AND trigger_signin
                AND refresh_expires_at > now() RETURNING token_id""",
                tenant, box, pat.digest(presented), pat.digest(access), expiries[0],
                pat.digest(rotated), expiries[1], audience, client_id)
    if token_id is None:
        return error("invalid_grant")
    return JSONResponse(grant(access, rotated, token_id, box, issued), headers=NO_STORE)


@router.post("/revoke")
async def revoke(request: Request) -> JSONResponse:
    """Revoke the credential the caller holds: bearer access token or body refresh token."""
    box = region()
    token = None
    authorization = request.headers.get("authorization") or ""
    scheme, _, bearer = authorization.partition(" ")
    if scheme.lower() == "bearer" and pat.TOKEN.fullmatch(bearer):
        token, column = bearer, "token_hash"
    else:
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 - any unreadable body is a refusal
            payload = None
        presented = payload.get("refresh_token") if isinstance(payload, dict) else None
        if isinstance(presented, str) and REFRESH.fullmatch(presented):
            token, column = presented, "refresh_hash"
    if token is None:
        return error("invalid_request")
    parts = token.split(".")
    if parts[1] != box:
        return error("invalid_grant")
    tenant = uuid.UUID(hex=parts[2])
    with tenant_scope(tenant):
        async with command.db().acquire() as conn:
            await conn.execute(
                """UPDATE tank.personal_tokens SET revoked_at=coalesce(revoked_at,now())
                WHERE tenant_id=$1 AND region=$2 AND audience='cli'
                AND (($4='token_hash' AND token_hash=$3) OR ($4='refresh_hash' AND refresh_hash=$3))""",
                tenant, box, pat.digest(token), column)
    # RFC 7009: the same answer whether or not the token existed.
    return JSONResponse({"revoked": True}, headers=NO_STORE)
