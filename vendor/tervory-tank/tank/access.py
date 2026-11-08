"""Tenant connections, official feeds and human browser handoffs.

The room/Workspace APIs never return a credential. Browser RPCs are encrypted
end to end with a separate key held only by Tank and that room's browser.
"""
from __future__ import annotations

import asyncio
import base64
import csv
import io
import ipaddress
import json
import os
import re
import socket
import uuid
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

import vault
import rig_feed_mapping
from command import Actor, identity, key_matches

async def rotation_reminders():
    # Each inserted reminder retains the credential row's tenant. No credential
    # or client data is read into application memory by this maintenance job.
    async with db().acquire() as conn:
        await conn.execute("""INSERT INTO tank.access_queue(tenant_id,room,site,reason)
            SELECT c.tenant_id,c.room,c.site,'rotation_due' FROM tank.access_connections c
            LEFT JOIN tank.access_sites p ON p.tenant_id=$1 AND p.site=c.site
            WHERE NOT c.revoked AND c.updated_at + make_interval(days => COALESCE(p.rotation_days,90)) <= now()
            ON CONFLICT (tenant_id,room,site) WHERE status IN ('waiting','handling') DO NOTHING""", PLATFORM)


async def reminder_loop():
    import logging
    while True:
        await asyncio.sleep(3600)
        try:
            await rotation_reminders()
        except Exception:
            logging.getLogger(__name__).warning("Connection renewal reminders are unavailable.")


@asynccontextmanager
async def access_lifespan(app):
    from contextlib import suppress
    task = asyncio.create_task(reminder_loop())
    rig_feeds = asyncio.create_task(rig_feed_loop())
    try:
        yield
    finally:
        task.cancel()
        rig_feeds.cancel()
        with suppress(asyncio.CancelledError): await task
        with suppress(asyncio.CancelledError): await rig_feeds


router = APIRouter(prefix="/v1/access", lifespan=access_lifespan)
PLATFORM = uuid.UUID("00000000-0000-0000-0000-000000000001")
ROOMS = {"base": "http://base-browser-gateway:3000", "tiger": "http://tiger-browser-gateway:3000"}
SOCIAL = ("facebook.com", "instagram.com", "fb.com", "fb.me")
DISTRIBUTORS = ("zanders", "sports_south", "rsr", "jerrys")


def db():
    from main import pool
    return pool()


def origin(value: str, *, https: bool = True, allow_nondefault_port: bool = False) -> str:
    try:
        p = urlsplit(value)
        host = (p.hostname or "").lower().rstrip(".")
        port, default_port = p.port, 443 if p.scheme == "https" else 80
        if (p.scheme not in ({"https"} if https else {"https", "http"}) or not host
                or p.username or p.password or port == 0
                or (port not in (None, default_port) and not allow_nondefault_port)
                or host in {"localhost", "metadata.google.internal"}
                or host.endswith((".localhost", ".local", ".internal"))):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and (not address.is_global or address.is_multicast):
            raise ValueError()
        return p.scheme + "://" + ("[" + host + "]" if ":" in host else host) + (
            ":" + str(port) if port not in (None, default_port) else "")
    except ValueError:
        raise HTTPException(422, "Use a public site address without credentials.") from None


def site_name(value: str, *, allow_nondefault_port: bool = False) -> str:
    result = origin(value, allow_nondefault_port=allow_nondefault_port)
    parsed = urlsplit(value)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise HTTPException(422, "Use the site's HTTPS address without a path or query.")
    return result


def is_social(site: str) -> bool:
    host = urlsplit(site).hostname or ""
    return any(host == s or host.endswith("." + s) for s in SOCIAL)


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


async def parse(request: Request, model, limit: int = 16000):
    # FastAPI's default validation response includes rejected input. Credentials
    # must never enter that path, exception strings, tracing or response bodies.
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, "Connection request is too large.")
    try:
        return model.model_validate_json(bytes(data))
    except (ValidationError, ValueError):
        raise HTTPException(422, "Check the connection fields and try again.") from None


class SitePolicy(Body):
    site: str = Field(max_length=240)
    terms: Literal["yes", "no", "unknown"]
    rotation_days: int = Field(default=90, ge=1, le=365)
    login_path: str = Field(default="/login", max_length=500)
    username_selector: str = Field(default='input[autocomplete="username"]', max_length=200)
    password_selector: str = Field(default='input[type="password"]', max_length=200)
    submit_selector: str = Field(default='button[type="submit"]', max_length=200)
    success_selector: str = Field(default='a[href*="logout"]', max_length=200)
    price_selector: str = Field(default="", max_length=200)
    quantity_selector: str = Field(default="", max_length=200)
    totp_selector: str = Field(default='input[autocomplete="one-time-code"]', max_length=200)


class Credential(Body):
    room: Literal["base", "tiger"]
    site: str = Field(max_length=240)
    username: SecretStr = Field(min_length=1, max_length=200)
    password: SecretStr = Field(min_length=1, max_length=2048)
    totp: SecretStr = Field(default=SecretStr(""), max_length=128)
    dedicated_read_only: Literal[True]
    device_enrolled: bool = False


class Connection(Body):
    room: Literal["base", "tiger"]
    site: str = Field(max_length=240)


def permit_room(actor: Actor, room: str):
    if room not in ROOMS or (not actor.founder and room not in actor.squads):
        raise HTTPException(403, "This room is not enabled for this account.")


async def receipt(conn, tenant, room: str, site: str, actor: str, action: str, *, duration_ms=0, row_count=None):
    # An explicit projection, never a request body, exception or URL path.
    await conn.execute("""INSERT INTO tank.access_receipts
        (tenant_id,room,site,actor,action,duration_ms,row_count) VALUES($1,$2,$3,$4,$5,$6,$7)""",
        tenant, room, site, actor, action, duration_ms, row_count)


async def enqueue(conn, tenant, room: str, site: str, reason: str, handle=None, task=None):
    row = await conn.fetchrow("""INSERT INTO tank.access_queue(tenant_id,room,site,reason,browser_handle,task_id)
        VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT (tenant_id,room,site) WHERE status IN ('waiting','handling')
        DO UPDATE SET reason=EXCLUDED.reason,browser_handle=EXCLUDED.browser_handle,
        task_id=COALESCE(EXCLUDED.task_id,tank.access_queue.task_id),status='waiting',handler=NULL,started_at=NULL RETURNING id""",
        tenant, room, site, reason, handle, task)
    return str(row["id"])


async def resolve_checks(conn, tenant, room, site, *, reasons=None):
    await conn.execute("""UPDATE tank.access_queue SET status='done',finished_at=now(),browser_handle=NULL,
        handler=NULL,started_at=NULL WHERE tenant_id=$1 AND room=$2 AND site=$3
        AND status IN ('waiting','handling') AND ($4::text[] IS NULL OR reason=ANY($4))""",
        tenant, room, site, reasons)


@router.get("/connections")
async def connections(actor: Actor = Depends(identity)):
    async with db().acquire() as conn:
        rows = await conn.fetch("""SELECT c.room,c.site,c.updated_at,c.clear_pending,c.revoked,
            COALESCE(p.terms,'unknown') AS terms,COALESCE(p.rotation_days,90) AS rotation_days,
            c.updated_at + make_interval(days => COALESCE(p.rotation_days,90)) AS rotate_by
            FROM tank.access_connections c LEFT JOIN tank.access_sites p ON p.tenant_id=$2 AND p.site=c.site
            WHERE c.tenant_id=$1 ORDER BY c.site,c.room""", actor.tenant_id, PLATFORM)
        policies = await conn.fetch("SELECT site,terms,rotation_days FROM tank.access_sites WHERE tenant_id=$1 ORDER BY site", PLATFORM) if actor.founder else []
        feeds = await conn.fetch("""SELECT distributor,site,source,fetched_at,row_count FROM tank.access_feed_batches
            WHERE tenant_id=$1 AND distributor NOT LIKE 'rig:%' ORDER BY fetched_at DESC LIMIT 30""", actor.tenant_id)
    now = datetime.now(timezone.utc)
    return {"connections": [{**dict(r), "overdue": r["rotate_by"] <= now} for r in rows],
            "policies": [dict(r) for r in policies], "feeds": [dict(r) for r in feeds],
            "meta_enabled": bool(os.environ.get("META_APP_ID")) and os.environ.get("META_APP_REVIEW_APPROVED") == "true"}


@router.post("/sites")
async def policy(request: Request, actor: Actor = Depends(identity)):
    if not actor.founder:
        raise HTTPException(403, "Only the founder may set site terms.")
    body = await parse(request, SitePolicy)
    site = site_name(body.site)
    if is_social(site) or not body.login_path.startswith("/") or body.login_path.startswith("//") or any(c in body.login_path for c in "?#\\"):
        raise HTTPException(422, "Use a same-site login path; social logins are not supported.")
    async with db().acquire() as conn:
        await conn.execute("""INSERT INTO tank.access_sites(tenant_id,site,terms,rotation_days,login_config)
            VALUES($1,$2,$3,$4,$5::jsonb) ON CONFLICT(tenant_id,site) DO UPDATE SET
            terms=$3,rotation_days=$4,login_config=$5::jsonb,updated_at=now()""", PLATFORM, site,
            body.terms, body.rotation_days, json.dumps(body.model_dump(exclude={"site", "terms", "rotation_days"})))
        await receipt(conn, PLATFORM, "platform", site, actor.user_id, "terms_updated")
        # Terms are founder-managed platform policy. Resolve affected clients'
        # settings-only checks individually, preserving each tenant boundary.
        affected = await conn.fetch("""SELECT DISTINCT tenant_id,room FROM tank.access_queue
            WHERE (tenant_id=$1 OR $2::bool) AND site=$3 AND status IN ('waiting','handling')
            AND reason IN ('terms_no','terms_unknown','rotation_due')""", actor.tenant_id, actor.founder, site)
        for item in affected:
            await resolve_checks(conn, item["tenant_id"], item["room"], site, reasons=["terms_no", "terms_unknown", "rotation_due"])
    return {"ok": True}


async def control(room: str, data: dict) -> dict:
    key = os.environ.get("TANK_KEY_" + room.upper(), "")
    if not key:
        raise HTTPException(503, "The room's browser is not connected.")
    payload = vault.envelope(room, "control", data)
    try:
        async with httpx.AsyncClient(timeout=35, trust_env=False, event_hooks={"request": [__import__("product_identity").request_hook]}) as client:
            response = await client.post(ROOMS[room] + "/access/control", json=payload, headers={"X-Tank-Key": key})
        if response.status_code != 200:
            raise ValueError()
        result = vault.open_envelope(room, "result", response.json())
        if result.get("request_nonce") != vault.open_envelope(room, "control", payload)["nonce"]:
            raise ValueError()
        if result.get("error"):
            raise HTTPException(409, "The live page is no longer available; retry the page to open a new check.")
        return result
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "The room's browser is unavailable. Session removal has not been confirmed.") from None


async def clear_session(tenant, room, site, actor):
    result = await control(room, {"action": "revoke", "tenant_id": str(tenant), "site": site})
    if result.get("cleared") is not True:
        raise HTTPException(503, "Session removal has not been confirmed.")
    async with db().acquire() as conn:
        await conn.execute("UPDATE tank.access_connections SET clear_pending=false WHERE tenant_id=$1 AND room=$2 AND site=$3", tenant, room, site)
        await receipt(conn, tenant, room, site, actor, "logout")


@asynccontextmanager
async def connection_change(tenant, room, site):
    # Serialize rotate/revoke across Tank workers without holding a row lock
    # while the browser calls back for its current policy.
    name = f"{tenant}:{room}:{site}"
    async with db().acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock(hashtextextended($1,0))", name)
        try:
            yield
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1,0))", name)


@router.post("/credentials")
async def credentials(request: Request, actor: Actor = Depends(identity)):
    body = await parse(request, Credential)
    permit_room(actor, body.room)
    site = site_name(body.site)
    if is_social(site) or (body.totp.get_secret_value() and not body.device_enrolled):
        raise HTTPException(422, "Use a dedicated distributor account and enroll authenticator devices deliberately.")
    secret = {name: getattr(body, name).get_secret_value() for name in ("username", "password", "totp")}
    if secret["totp"]:
        import base64
        try:
            base64.b32decode(secret["totp"].upper(), casefold=True)
        except Exception:
            raise HTTPException(422, "Check the authenticator enrollment.") from None
    encrypted = vault.seal(secret, vault.context(actor.tenant_id, body.room, site, "login"))
    async with connection_change(actor.tenant_id, body.room, site):
        # Disable the old generation before clearing its cookies. A failed clear is
        # visible and leaves login denied; no old session is silently reused.
        async with db().acquire() as conn:
            await conn.execute("""INSERT INTO tank.access_connections(tenant_id,room,site,sealed,revoked,clear_pending)
                VALUES($1,$2,$3,NULL,true,true) ON CONFLICT(tenant_id,room,site)
                DO UPDATE SET sealed=NULL,revoked=true,clear_pending=true,version=gen_random_uuid()""", actor.tenant_id, body.room, site)
        await clear_session(actor.tenant_id, body.room, site, actor.user_id)
        async with db().acquire() as conn:
            await conn.execute("""UPDATE tank.access_connections SET sealed=$4,revoked=false,updated_at=now(),version=gen_random_uuid()
                WHERE tenant_id=$1 AND room=$2 AND site=$3""", actor.tenant_id, body.room, site, encrypted)
            await receipt(conn, actor.tenant_id, body.room, site, actor.user_id, "credential_updated")
            await resolve_checks(conn, actor.tenant_id, body.room, site)
        return {"ok": True}


@router.post("/revoke")
async def revoke(request: Request, actor: Actor = Depends(identity)):
    body = await parse(request, Connection)
    site = site_name(body.site)
    async with connection_change(actor.tenant_id, body.room, site):
        async with db().acquire() as conn:
            await conn.execute("""UPDATE tank.access_connections SET sealed=NULL,revoked=true,clear_pending=true,
                version=gen_random_uuid() WHERE tenant_id=$1 AND room=$2 AND site=$3""", actor.tenant_id, body.room, site)
            await conn.execute("""UPDATE tank.access_queue SET status='revoked' WHERE tenant_id=$1 AND room=$2
                AND site=$3 AND status IN ('waiting','handling')""", actor.tenant_id, body.room, site)
            await receipt(conn, actor.tenant_id, body.room, site, actor.user_id, "revoked")
        await clear_session(actor.tenant_id, body.room, site, actor.user_id)
        return {"ok": True, "credential_deleted": True, "session_deleted": True}


class Sealed(Body):
    sealed: str = Field(max_length=24000)


async def browser_policy(conn, tenant, room, site):
    row = await conn.fetchrow("""SELECT c.*,COALESCE(p.terms,'unknown') AS terms,
        COALESCE(p.rotation_days,90) AS rotation_days,p.login_config FROM tank.access_connections c
        LEFT JOIN tank.access_sites p ON p.tenant_id=$4 AND p.site=c.site
        WHERE c.tenant_id=$1 AND c.room=$2 AND c.site=$3""", tenant, room, site, PLATFORM)
    if row is None:
        return None, "public"
    if row["revoked"] or row["clear_pending"] or not row["sealed"]:
        return row, "revoked"
    if is_social(site):
        return row, "terms_no"
    if row["terms"] != "yes":
        return row, "terms_" + row["terms"]
    if row["updated_at"] + timedelta(days=row["rotation_days"]) <= datetime.now(timezone.utc):
        return row, "rotation_due"
    return row, "allowed"


@router.post("/browser/{room}")
async def browser_rpc(room: str, request: Request):
    body = await parse(request, Sealed)
    data = vault.open_envelope(room, "request", body.model_dump())
    try:
        tenant = uuid.UUID(data["tenant_id"])
        nonce = uuid.UUID(data["nonce"])
        site = origin(data["site"], https=False)
        action = data["action"]
        task = uuid.UUID(data["task_id"]) if data.get("task_id") else None
    except (ValueError, KeyError):
        raise HTTPException(422, "Invalid browser request.") from None
    browser_actor = f"{room}:task:{task}" if task else f"{room}:task:unassigned"
    async with db().acquire() as conn:
        inserted = await conn.fetchval("""INSERT INTO tank.access_nonces(tenant_id,nonce) VALUES($1,$2)
            ON CONFLICT DO NOTHING RETURNING nonce""", tenant, nonce)
        if inserted is None:
            raise HTTPException(409, "Browser request already used.")
        await conn.execute("DELETE FROM tank.access_nonces WHERE tenant_id=$1 AND created_at < now()-interval '5 minutes'", tenant)
        row, state = await browser_policy(conn, tenant, room, site)
        result = {"state": state, "version": str(row["version"]) if row else None}
        if state == "allowed":
            vault.key()  # A missing/invalid encryption setting must fail closed even for existing cookies.
            result["config"] = json.loads(row["login_config"]) if isinstance(row["login_config"], str) else row["login_config"]
        if action == "release":
            if state != "allowed":
                await receipt(conn, tenant, room, site, browser_actor, "login_refused")
            else:
                result["credential"] = vault.open_seal(row["sealed"], vault.context(tenant, room, site, "login"))
                result["config"] = json.loads(row["login_config"]) if isinstance(row["login_config"], str) else row["login_config"]
                await receipt(conn, tenant, room, site, browser_actor, "login_started")
        elif action == "queue":
            reason = data.get("reason")
            if reason not in {"captcha", "human_login", "terms_no", "terms_unknown", "rotation_due"}:
                raise HTTPException(422, "Invalid check reason.")
            handle = uuid.UUID(data["handle"]) if data.get("handle") else None
            result["queue_id"] = await enqueue(conn, tenant, room, site, reason, handle, task)
        elif action == "event":
            event = data.get("event")
            if event not in {"logged_in", "login_failed", "logout", "live_expired"}:
                raise HTTPException(422, "Invalid browser event.")
            await receipt(conn, tenant, room, site, browser_actor, event)
            if event == "live_expired":
                await conn.execute("UPDATE tank.access_queue SET browser_handle=NULL,handler=NULL,status='waiting' WHERE tenant_id=$1 AND room=$2 AND site=$3 AND status IN ('waiting','handling')", tenant, room, site)
        elif action != "policy":
            raise HTTPException(422, "Unknown browser operation.")
    return vault.envelope(room, "response", {**result, "request_nonce": str(nonce)})


@router.get("/queue")
async def queue(actor: Actor = Depends(identity)):
    async with db().acquire() as conn:
        rows = await conn.fetch("""SELECT id,tenant_id,room,site,reason,status,created_at,browser_handle,
            (browser_handle IS NOT NULL) AS live_available FROM tank.access_queue
            WHERE (tenant_id=$1 OR $2::bool) AND status IN ('waiting','handling') ORDER BY created_at LIMIT 100""",
            actor.tenant_id, actor.founder)
    async def current(row):
        value = dict(row)
        if row["browser_handle"] and row["status"] == "handling":
            try:
                state = await control(row["room"], {"action": "status", "tenant_id": str(row["tenant_id"]),
                    "site": row["site"], "handle": str(row["browser_handle"])})
                if state.get("available") is False:
                    await reset_check(row)
                    value.update(status="waiting", live_available=False)
            except HTTPException:
                pass  # A network failure cannot establish that the view was lost.
        value.pop("browser_handle", None)
        return value
    return await asyncio.gather(*(current(row) for row in rows))


async def reset_check(row):
    async with db().acquire() as conn:
        await conn.execute("""UPDATE tank.access_queue SET status='waiting',browser_handle=NULL,handler=NULL,started_at=NULL
            WHERE tenant_id=$1 AND id=$2 AND browser_handle=$3 AND status IN ('waiting','handling')""",
            row["tenant_id"], row["id"], row["browser_handle"])


class HumanAction(Body):
    tenant_id: uuid.UUID
    action: Literal["open", "frame", "click", "key", "text", "scroll", "hand_back", "dismiss"]
    x: int = Field(default=0, ge=0, le=1280)
    y: int = Field(default=0, ge=0, le=900)
    text: SecretStr = Field(default=SecretStr(""), max_length=2000)
    key: Literal["", "Enter", "Tab", "Backspace", "Escape", "ArrowDown", "ArrowUp"] = ""
    delta: int = Field(default=0, ge=-900, le=900)


@router.post("/queue/{item_id}")
async def human_action(item_id: uuid.UUID, request: Request, actor: Actor = Depends(identity)):
    if not actor.founder:
        raise HTTPException(403, "A person on our team must handle this check.")
    body = await parse(request, HumanAction)
    async with db().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM tank.access_queue WHERE id=$1 AND tenant_id=$2 AND status IN ('waiting','handling') FOR UPDATE", item_id, body.tenant_id)
            if not row:
                raise HTTPException(404, "This check is no longer waiting.")
            if body.action == "dismiss":
                if row["browser_handle"]:
                    await control(row["room"], {"action": "dismiss", "tenant_id": str(row["tenant_id"]),
                        "site": row["site"], "handle": str(row["browser_handle"])})
                await conn.execute("""UPDATE tank.access_queue SET status='done',finished_at=now(),browser_handle=NULL,
                    handler=NULL,started_at=NULL WHERE tenant_id=$1 AND id=$2""", row["tenant_id"], item_id)
                await receipt(conn, row["tenant_id"], row["room"], row["site"], actor.user_id, "dismissed")
                return {"dismissed": True}
            if not row["browser_handle"]:
                raise HTTPException(409, "Retry the page after the connection or site terms are updated.")
            if row["handler"] and row["handler"] != actor.user_id:
                raise HTTPException(409, "Another person is handling this check.")
            if row["status"] == "waiting" and body.action != "open":
                raise HTTPException(409, "Open the live view first.")
            await conn.execute("UPDATE tank.access_queue SET handler=$2,status='handling',started_at=COALESCE(started_at,now()) WHERE id=$1 AND tenant_id=$3", item_id, actor.user_id, row["tenant_id"])
    try:
        result = await control(row["room"], {"action": "live", "tenant_id": str(row["tenant_id"]), "site": row["site"],
            "handle": str(row["browser_handle"]), "input": {**body.model_dump(exclude={"text", "tenant_id"}), "text": body.text.get_secret_value()}})
    except HTTPException as exc:
        if exc.status_code == 409: await reset_check(row)
        raise
    if body.action == "hand_back":
        if not result.get("ready"):
            raise HTTPException(409, "The page still needs a person.")
        async with db().acquire() as conn:
            duration = await conn.fetchval("""UPDATE tank.access_queue SET status='done',finished_at=now()
                WHERE id=$1 AND tenant_id=$2 RETURNING (extract(epoch FROM now()-started_at)*1000)::bigint""", item_id, row["tenant_id"])
            await receipt(conn, row["tenant_id"], row["room"], row["site"], actor.user_id, "handled", duration_ms=duration)
    # Screenshot only: no DOM, remote URL, cookies or browser debug endpoint.
    return Response(json.dumps({k: result[k] for k in ("png", "ready") if k in result}), media_type="application/json",
                    headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


async def room_identity(request: Request) -> str:
    for room in ROOMS:
        if key_matches(request.headers.get("x-tank-key", ""), os.environ.get("TANK_KEY_" + room.upper(), "")):
            return room
    raise HTTPException(401, "Room authentication required.")


# Official distributor adapters accept the dealer's official export, including
# configurable column mappings; no guessed remote endpoints or dealer accounts.
FEED_COLUMNS = {
    "zanders": {"sku": ("itemnumber", "itemno", "sku"), "price": ("price", "dealerprice"), "quantity": ("quantity", "qty"), "upc": ("upc",)},
    "sports_south": {"sku": ("itemno", "itemnumber", "sku"), "price": ("prc", "price"), "quantity": ("qty", "quantity"), "upc": ("upc",)},
    "rsr": {"sku": ("rsrstocknumber", "rsrstocknbr", "sku"), "price": ("rsrregularprice", "price"), "quantity": ("inventoryquantity", "quantity"), "upc": ("upccode", "upc")},
    "jerrys": {"sku": ("itemnumber", "itemno", "sku"), "price": ("dealerprice", "price"), "quantity": ("qtyavailable", "quantity", "qty"), "upc": ("upc",)},
}


FeedProvider = Literal["zanders", "sports_south", "rsr", "jerrys", "shopify", "woocommerce", "google", "catalogue"]


class FeedOptions(Body):
    distributor: FeedProvider
    target: Literal["tiger", "rig"] = "tiger"
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    site: str = Field(max_length=240)
    format: Literal["csv", "tsv", "json", "edi846", "xml", "xlsx"]
    columns: dict[str, str] = Field(default_factory=dict, max_length=20)
    country: str = Field(default="US", pattern="^[A-Z]{2}$")
    price_basis: Literal["cost", "selling"] = "cost"
    feed_id: str = Field(default="", max_length=60, pattern="^[a-z0-9_-]*$")
    records_path: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9_.-]*$")

    @model_validator(mode="after")
    def target_options(self):
        if self.target == "tiger" and (self.distributor not in DISTRIBUTORS or self.format in {"xml", "xlsx"} or len(self.columns) > 5):
            raise ValueError("Use an existing Tiger distributor format and at most five column names.")
        if self.target == "rig" and any(len(key) > 40 or len(value) > 160 for key, value in self.columns.items()):
            raise ValueError("Column names are too long.")
        return self


class FeedImport(FeedOptions):
    source: Literal["dealer_api", "ftp_export", "edi_export"]
    contents: SecretStr = Field(max_length=10_666_668)
    encoding: Literal["text", "base64"] = "text"

    @model_validator(mode="after")
    def bounded_contents(self):
        raw = self.contents.get_secret_value()
        if self.encoding == "base64":
            if self.target != "rig" or self.format != "xlsx" or len(base64.b64decode(raw, validate=True)) > 8_000_000:
                raise ValueError("Use a bounded Excel file.")
        elif (len(raw.encode()) if self.target == "rig" else len(raw)) > 8_000_000:
            raise ValueError("File too large.")
        return self


def parse_feed(body: FeedImport) -> list[dict]:
    raw = body.contents.get_secret_value()
    if body.format == "json":
        rows = json.loads(raw)
        if not isinstance(rows, list):
            raise ValueError()
    elif body.format == "edi846":
        rows = []
        current = None
        for segment in raw.replace("\r", "").replace("\n", "").split("~"):
            parts = segment.split("*")
            if parts[0] == "LIN":
                current = {}
                for i in range(2, len(parts)-1, 2):
                    if parts[i] in {"VP", "SK"}: current["sku"] = parts[i+1]
                    if parts[i] in {"UP", "UK"}: current["upc"] = parts[i+1]
                rows.append(current)
            elif current is not None and parts[0] == "QTY" and len(parts) > 2 and parts[1] == "33":
                current["quantity"] = parts[2]
            elif current is not None and parts[0] == "CTP" and len(parts) > 3:
                current["price"] = parts[3]
    else:
        rows = list(csv.DictReader(io.StringIO(raw), delimiter="\t" if body.format == "tsv" else ",", strict=True))
    if len(rows) > 50000 or not rows:
        raise ValueError()
    result = []
    normalize = lambda value: re.sub(r"[^a-z0-9]", "", str(value).lower())
    for row in rows:
        if not isinstance(row, dict): raise ValueError()
        values = {normalize(k): v for k, v in row.items()}
        def field(name):
            keys = (normalize(body.columns[name]),) if name in body.columns else FEED_COLUMNS[body.distributor][name] + (name,)
            return next((str(values[k]).strip() for k in keys if k in values and values[k] is not None), "")
        sku, upc = field("sku"), field("upc")
        price = Decimal(field("price")) if field("price") else None
        quantity = Decimal(field("quantity")) if field("quantity") else None
        if not sku or len(sku) > 120 or len(upc) > 32 or any(v is not None and (not v.is_finite() or v < 0) for v in (price, quantity)):
            raise ValueError()
        result.append({"sku": sku, "upc": upc, "price": str(price) if price is not None else None,
                       "quantity": str(quantity) if quantity is not None else None, "currency": "USD"})
    if len({r["sku"] for r in result}) != len(result): raise ValueError()
    return result


@router.post("/feeds/import")
async def import_feed(request: Request, actor: Actor = Depends(identity)):
    permit_room(actor, "tiger")
    body = await parse(request, FeedImport, 8_020_000)
    if body.target != "tiger":
        raise HTTPException(403, "Use retailer settings for Rig feeds.")
    return await import_feed_body(actor, body)


async def import_feed_body(actor: Actor, body: FeedImport):
    site_name(body.site)
    if body.target == "rig":
        body = await rig_confirmed_settings(actor, body)
    try:
        rows = [] if body.target == "rig" else parse_feed(body)
    except (ValueError, TypeError, KeyError, InvalidOperation, csv.Error):
        if body.target == "rig":
            return await save_rig_feed(actor, body, None)
        raise HTTPException(422, "The feed could not be read. Check its format and column mapping.") from None
    return await save_feed(actor, body, rows)


@router.get("/feeds/price")
async def feed_price(tenant_id: uuid.UUID, site: str, sku: str = "", upc: str = "", url: str = "", room: str = Depends(room_identity)):
    if room != "tiger" or (not sku and not upc and not url):
        raise HTTPException(422, "Tiger must name the product identifier.")
    site = site_name(site, allow_nondefault_port=bool(url))
    if url:
        if origin(url, allow_nondefault_port=True) != site:
            raise HTTPException(422, "Product site differs.")
        # Feeds can only be configured on default HTTPS ports. This is a miss,
        # not a feed-service failure, so Tiger may continue its existing scrape.
        if urlsplit(url).port not in (None, 443): return {"found": False}
    async with db().acquire() as conn:
        if url:
            sku = await conn.fetchval("SELECT sku FROM tank.access_feed_links WHERE tenant_id=$1 AND url=$2", tenant_id, url) or ""
        row = await conn.fetchrow("""SELECT r.data,b.source,b.fetched_at,b.row_count,b.distributor,b.id FROM tank.access_feed_rows r
            JOIN tank.access_feed_batches b ON b.tenant_id=r.tenant_id AND b.id=r.batch_id
            WHERE r.tenant_id=$1 AND r.site=$2 AND (($3<>'' AND r.sku=$3) OR ($4<>'' AND r.upc=$4))
            ORDER BY b.fetched_at DESC LIMIT 1""", tenant_id, site, sku, upc)
    if not row: return {"found": False}
    data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
    return {"found": True, "item": data, "receipt": {k: row[k] for k in ("source", "fetched_at", "row_count", "distributor", "id")}}


@asynccontextmanager
async def public_stream(url: str, *, method: str = "GET", headers=None, content=None):
    """One pinned public HTTPS request; callers bound both duration and bytes.

    Shared by feeds and MCP, including metadata and OAuth POSTs. Resolve on
    every request, validate every answer, and preserve Host/SNI on the pinned IP.
    """
    if method not in {"GET", "POST"}:
        raise ValueError("Method refused")
    origin(url)
    p = urlsplit(url)
    answers = await asyncio.get_running_loop().getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses or any(not ipaddress.ip_address(a).is_global or ipaddress.ip_address(a).is_multicast for a in addresses):
        raise ValueError("Private address refused")
    target = httpx.URL(url).copy_with(host=addresses[0])
    async with httpx.AsyncClient(timeout=25, trust_env=False, follow_redirects=False) as client:
        async with client.stream(method, target, content=content, headers={**(headers or {}), "Host": p.hostname},
                                 extensions={"sni_hostname": p.hostname}) as response:
            if 300 <= response.status_code < 400:
                raise ValueError("Redirect refused")
            yield response


async def public_get(url: str, *, headers=None, limit=2_000_000) -> tuple[bytes, dict]:
    """Resolve, validate all answers, pin the connection, retain TLS hostname."""
    async with asyncio.timeout(25):
        async with public_stream(url, headers=headers) as response:
            if response.status_code not in {200, 206}:
                raise ValueError("Source unavailable")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("Source too large")
            return bytes(data), dict(response.headers)


class FeedConnection(FeedOptions):
    endpoint: str = Field(max_length=1000)
    token: SecretStr = Field(default=SecretStr(""), max_length=2048)
    auth_mode: Literal["none", "bearer", "basic", "shopify"] = "bearer"
    username: SecretStr = Field(default=SecretStr(""), max_length=2048)
    password: SecretStr = Field(default=SecretStr(""), max_length=2048)
    official_read_only: Literal[True]

    @model_validator(mode="after")
    def target_authentication(self):
        if self.target != "rig" and (self.auth_mode != "bearer" or self.username.get_secret_value() or self.password.get_secret_value()):
            raise ValueError("This authentication mode is only available for Rig feeds.")
        return self


@router.post("/feeds/connect")
async def connect_feed(request: Request, actor: Actor = Depends(identity)):
    permit_room(actor, "tiger")
    body = await parse(request, FeedConnection)
    if body.target != "tiger":
        raise HTTPException(403, "Use retailer settings for Rig feeds.")
    return await connect_feed_body(actor, body)


async def connect_feed_body(actor: Actor, body: FeedConnection):
    if body.target == "rig":
        body = await rig_confirmed_settings(actor, body)
        expected = {"shopify": {"json"}, "woocommerce": {"json"}, "google": {"xml"}}.get(body.distributor, {"csv", "tsv", "json", "xml", "xlsx"})
        if body.format not in expected:
            raise HTTPException(422, "Choose the matching catalogue format for this supplier.")
    site = site_name(body.site)
    if origin(body.endpoint) != site or urlsplit(body.endpoint).query or urlsplit(body.endpoint).fragment:
        raise HTTPException(422, "Use a same-site read-only HTTPS feed endpoint, without a query or credentials.")
    if body.auth_mode == "basic" and not (body.username.get_secret_value() and body.password.get_secret_value()):
        raise HTTPException(422, "Provide both read-only account credentials.")
    if body.auth_mode == "shopify" and not body.token.get_secret_value():
        raise HTTPException(422, "Provide the Shopify access token.")
    config = {**body.model_dump(exclude={"token", "username", "password"}), "token": body.token.get_secret_value(),
              "username": body.username.get_secret_value(), "password": body.password.get_secret_value()}
    encrypted = vault.seal(config, vault.context(actor.tenant_id, body.target, site, "feed:" + body.distributor + (":" + body.feed_id if body.target == "rig" and body.feed_id else "")))
    async with db().acquire() as conn:
        await conn.execute("""INSERT INTO tank.access_feed_connections(tenant_id,distributor,site,sealed)
            VALUES($1,$2,$3,$4) ON CONFLICT(tenant_id,distributor,site) DO UPDATE SET sealed=$4""",
            actor.tenant_id, feed_storage_name(body), site, encrypted)
        if body.target == "rig":
            await conn.execute("""UPDATE tank.access_feed_connections SET rig_refresh_at=
                date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' + interval '1 day 3 hours'
                WHERE tenant_id=$1 AND distributor=$2 AND site=$3""", actor.tenant_id, feed_storage_name(body), site)
            await receipt(conn, actor.tenant_id, "rig", site, actor.user_id, "feed_connected")
    return {"ok": True}


class FeedRefresh(Body):
    distributor: FeedProvider
    target: Literal["tiger", "rig"] = "tiger"
    site: str = Field(max_length=240)
    feed_id: str = Field(default="", max_length=60, pattern="^[a-z0-9_-]*$")


@router.post("/feeds/refresh")
async def refresh_feed(request: Request, actor: Actor = Depends(identity)):
    permit_room(actor, "tiger")
    body = await parse(request, FeedRefresh)
    if body.target != "tiger":
        raise HTTPException(403, "Use retailer settings for Rig feeds.")
    permit_feed(actor, "tiger", body.distributor)
    return await refresh_connected_feed(actor, body)


async def refresh_connected_feed(actor: Actor, body: FeedRefresh) -> dict[str, Any]:
    site = site_name(body.site)
    async with db().acquire() as conn:
        row = await conn.fetchrow("SELECT sealed FROM tank.access_feed_connections WHERE tenant_id=$1 AND distributor=$2 AND site=$3", actor.tenant_id, feed_storage_name(body), site)
    if not row: raise HTTPException(409, "Connect the official feed first.")
    config = vault.open_seal(row["sealed"], vault.context(actor.tenant_id, body.target, site, "feed:" + body.distributor + (":" + body.feed_id if body.target == "rig" and body.feed_id else "")))
    if body.target == "rig":
        # Old client-confirmed connections must be reviewed before any fetch or import.
        await rig_confirmed_settings(actor, FeedConnection.model_validate({k: v for k, v in config.items() if k != "mapping_confirmed"}))
    try:
        headers = feed_auth_headers(config)
        if body.target == "rig":
            raw = await read_rig_feed(config, headers)
        else:
            raw, _ = await public_get(config["endpoint"], headers=headers, limit=8_000_000)
    except Exception:
        raise HTTPException(503, "The official feed could not be refreshed. Existing data was kept.") from None
    try:
        encoded = config["format"] == "xlsx"
        parsed = FeedImport(distributor=body.distributor, site=site, source="dealer_api", format=config["format"],
                            contents=SecretStr(base64.b64encode(raw).decode() if encoded else raw.decode("utf-8-sig" if body.target == "rig" else "utf-8")),
                            columns=config["columns"], target=body.target, currency=config.get("currency", "USD"),
                            country=config.get("country", "US"), price_basis=config.get("price_basis", "cost"), feed_id=body.feed_id,
                            records_path=config.get("records_path", ""), encoding="base64" if encoded else "text")
        rows = [] if body.target == "rig" else parse_feed(parsed)
    except Exception:
        if body.target == "rig":
            failed = FeedImport(target="rig", distributor=body.distributor, site=site, source="dealer_api",
                                format=config["format"], contents=SecretStr(""), currency=config.get("currency", "USD"), feed_id=body.feed_id)
            return await save_rig_feed(actor, failed, None, refresh=True)
        raise HTTPException(503, "The official feed could not be refreshed. Existing data was kept.") from None
    if body.target == "rig":
        return await save_rig_feed(actor, parsed, rows, refresh=True)
    return await save_feed(actor, parsed, rows)


async def save_feed(actor, body, rows):
    if body.target == "rig":
        return await save_rig_feed(actor, body, rows)
    site, batch = site_name(body.site), uuid.uuid4()
    async with db().acquire() as conn:
        async with conn.transaction():
            await conn.execute("""INSERT INTO tank.access_feed_batches(tenant_id,id,distributor,site,source,row_count)
                VALUES($1,$2,$3,$4,$5,$6)""", actor.tenant_id, batch, body.distributor, site, body.source, len(rows))
            await conn.execute("DELETE FROM tank.access_feed_rows WHERE tenant_id=$1 AND distributor=$2 AND site=$3", actor.tenant_id, body.distributor, site)
            await conn.executemany("""INSERT INTO tank.access_feed_rows(tenant_id,distributor,site,sku,upc,data,batch_id)
                VALUES($1,$2,$3,$4,$5,$6::jsonb,$7)""", [(actor.tenant_id, body.distributor, site, r["sku"], r["upc"], json.dumps(r), batch) for r in rows])
            await receipt(conn, actor.tenant_id, "tiger", site, actor.user_id, "feed_fetched", row_count=len(rows))
    return {"batch_id": str(batch), "row_count": len(rows)}


# RIG-3 mapping: the existing ACCESS import/connect/refresh/vault and HTTP
# validation remain the sole supplier transport. Rig receives a server-to-server
# catalogue envelope and commits products, fits and its receipt together.
def permit_feed(actor: Actor, target: str, provider: str) -> None:
    if target == "rig":
        if not actor.founder and "rig" not in actor.squads:
            raise HTTPException(403, "Rig is not enabled for this account.")
    else:
        permit_room(actor, "tiger")
        if provider not in DISTRIBUTORS:
            raise HTTPException(422, "Choose an official distributor.")


def feed_storage_name(body: FeedImport | FeedConnection | FeedRefresh) -> str:
    return ("rig:" if body.target == "rig" else "") + body.distributor + (":" + body.feed_id if body.target == "rig" and body.feed_id else "")


RIG_SETTING_FIELDS = rig_feed_mapping.SETTING_FIELDS


async def rig_confirmed_settings(actor: Actor, body: FeedOptions):
    return await rig_feed_mapping.confirmed(db(), actor, body, feed_storage_name(body), site_name(body.site))


async def rig_request(actor: Actor, operation: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    key = os.environ.get("RIG_RETAILER_KEY", "")
    if not key:
        raise HTTPException(503, "Rig's retailer control connection is not configured.")
    try:
        async with httpx.AsyncClient(timeout=90, trust_env=False, event_hooks={"request": [__import__("product_identity").request_hook]}) as client:
            from rig_rooms import endpoint
            base = await endpoint(db(), actor) if os.environ.get("RIG_LIFECYCLE_KEY") else "http://agent-rig:8003"
            response = await client.post(base + "/internal/retailer/" + operation,
                extensions=__import__("product_identity").product_extensions("rig", base),
                headers={"x-rig-retailer-key": key}, json={"tenant_id": str(actor.tenant_id), "founder": actor.founder, "data": data or {}})
    except httpx.HTTPError:
        raise HTTPException(503, "Rig did not confirm this operation. Check its receipts before retrying.") from None
    if response.status_code >= 400:
        raise HTTPException(response.status_code if response.status_code in {403, 404, 409, 422} else 503,
                            "The retailer operation could not be completed. Check the fields and room connection.")
    try:
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except ValueError:
        raise HTTPException(503, "Rig returned an invalid response.") from None


async def save_rig_feed(actor: Actor, body: FeedImport, rows: list[dict[str, Any]] | None, *, refresh: bool = False) -> dict[str, Any]:
    site = site_name(body.site)
    parse_error = rows is None
    rows = rows or []
    metadata = {"fits": [{"part_sku": row["sku"], "fits_sku": row.get("fits_sku") or None,
                          "fits_family": row.get("fits_family") or None, "note": row.get("fit_evidence", "")}
                         for row in rows if row.get("fits_sku") or row.get("fits_family")]}
    result = await rig_request(actor, "import", {"provider": body.distributor, "site": site,
        **body.model_dump(include=RIG_SETTING_FIELDS | {"feed_id", "encoding"}),
        "contents": body.contents.get_secret_value() if not rows else "",
        "rows": rows, "metadata": metadata, "parse_error": parse_error, "refresh": refresh})
    # The room's receipt is durable even if the connection breaks here. No raw
    # supplier data, costs or credential fields are returned to the browser.
    summary = result["receipt"]
    async with db().acquire() as conn:
        async with conn.transaction():
            await conn.execute("""INSERT INTO tank.access_feed_batches(tenant_id,id,distributor,site,source,row_count,rig_summary)
                VALUES($1,$2,$3,$4,$5,$6,$7::jsonb) ON CONFLICT(tenant_id,id) DO NOTHING""", actor.tenant_id,
                uuid.UUID(summary["id"]), feed_storage_name(body), site, body.source,
                summary["added"] + summary["updated"], json.dumps({k: summary[k] for k in ("added", "updated", "rejected")}))
            await receipt(conn, actor.tenant_id, "rig", site, actor.user_id, "feed_imported" if result["ok"] else "feed_rejected",
                          row_count=summary["added"] + summary["updated"])
    return result


def feed_auth_headers(config: dict[str, Any]) -> dict[str, str]:
    mode = config.get("auth_mode", "bearer")
    if mode == "basic":
        encoded = base64.b64encode((config["username"] + ":" + config["password"]).encode()).decode()
        return {"Authorization": "Basic " + encoded}
    if mode == "shopify":
        return {"X-Shopify-Access-Token": config["token"]}
    if mode == "bearer" and config.get("token"):
        return {"Authorization": "Bearer " + config["token"]}
    return {}


async def read_rig_feed(config: dict[str, Any], headers: dict[str, str]) -> bytes:
    """Reuse ACCESS's pinned fetcher for bounded Shopify/WooCommerce pages."""
    endpoint = config["endpoint"]
    raw, response_headers = await public_get(endpoint, headers=headers, limit=8_000_000)
    if config["distributor"] not in {"shopify", "woocommerce"}:
        return raw
    total_bytes = len(raw)
    try:
        result = json.loads(raw)
    except ValueError:
        return raw  # Let the importer record an invalid-file receipt.
    kind = config["distributor"]
    products = result.get("products") if isinstance(result, dict) else result
    if not isinstance(products, list):
        return raw  # The catalogue mapper returns a rejected-file receipt.
    current = endpoint
    seen = {endpoint}
    original = urlsplit(endpoint)
    for page in range(1, 101):
        response_headers = {key.lower(): value for key, value in response_headers.items()}
        link = response_headers.get("link", "")
        match = re.search(r'<([^>]+)>\s*;\s*rel=[\"\x27]?next(?:[\"\x27]|\s|,|$)', link)
        next_url = match.group(1) if match else ""
        if kind == "woocommerce" and not next_url and page < int(response_headers.get("x-wp-totalpages", "1")):
            next_url = urlunsplit((original.scheme, original.netloc, original.path, urlencode({"page": page + 1}), ""))
        if not next_url:
            return json.dumps(result).encode()
        parsed = urlsplit(next_url)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if (page == 100 or next_url in seen or origin(next_url) != origin(endpoint) or parsed.path != original.path or parsed.fragment
                or any(key not in {"page", "per_page", "limit", "offset", "since_id", "page_info"} or len(value) > 512 for key, value in query)):
            raise ValueError("Invalid catalogue pagination")
        seen.add(next_url)
        current = next_url
        body, response_headers = await public_get(current, headers=headers, limit=8_000_000 - total_bytes)
        total_bytes += len(body)
        if total_bytes > 8_000_000:
            raise ValueError("Catalogue too large")
        loaded = json.loads(body)
        extra = loaded.get("products") if isinstance(loaded, dict) else loaded
        if not isinstance(extra, list):
            raise ValueError("Invalid catalogue page")
        products.extend(extra)
        if isinstance(result, dict) and isinstance(loaded, dict) and loaded.get("rig"):
            metadata = result.setdefault("rig", {})
            for field in ("families", "setup_templates"):
                metadata.setdefault(field, {}).update(loaded["rig"].get(field, {}))
            metadata.setdefault("fits", []).extend(loaded["rig"].get("fits", []))
    raise ValueError("Catalogue page limit exceeded")


@router.get("/rig/admin")
async def rig_admin_panel(actor: Actor = Depends(identity)):
    permit_feed(actor, "rig", "shopify")
    result = await rig_request(actor, "panel")
    async with db().acquire() as conn:
        rows = await conn.fetch("""SELECT coalesce(s.distributor,c.distributor) AS distributor,coalesce(s.site,c.site) AS site,
            s.settings,c.rig_refresh_at FROM tank.rig_feed_settings s FULL JOIN tank.access_feed_connections c
            ON c.tenant_id=s.tenant_id AND c.distributor=s.distributor AND c.site=s.site
            WHERE coalesce(s.tenant_id,c.tenant_id)=$1 AND coalesce(s.distributor,c.distributor) LIKE 'rig:%'
            ORDER BY site,distributor""", actor.tenant_id)
    result["feeds"] = []
    for row in rows:
        parts = row["distributor"].split(":", 2)
        settings = json.loads(row["settings"]) if isinstance(row["settings"], str) else row["settings"] or {}
        result["feeds"].append({"provider": parts[1], "feed_id": parts[2] if len(parts) > 2 else "", "site": row["site"],
                                "next_refresh": row["rig_refresh_at"], "mapping_confirmed": settings.get("confirmation_version") == 1, **{key: value for key, value in settings.items() if key in RIG_SETTING_FIELDS}})
    result["can_set_exchange_rate"] = actor.founder
    return result


class RigAdminAction(Body):
    data: dict[str, Any] = Field(default_factory=dict)


@router.post("/rig/admin/{operation}")
async def rig_admin_action(operation: str, request: Request, actor: Actor = Depends(identity)):
    if operation in {"commerce-connect", "commerce-orders", "store-delivery"}:
        if not actor.founder and not actor.tenant_admin:
            raise HTTPException(403, "Retailer administrator access is required.")
    if operation != "confirm-store-payment":
        permit_feed(actor, "rig", "shopify")
    if operation not in {"commerce-connect", "commerce-orders", "store-delivery", "confirm-store-payment", "import", "delete-products", "connect", "refresh", "mapping", "confirm-mapping", "settings", "embed-keys", "price-rule", "exchange-rate", "templates", "approve", "reject", "suggest"}:
        raise HTTPException(404, "Unknown retailer operation.")
    if operation == "exchange-rate" and not actor.founder:
        raise HTTPException(403, "Only the founder may set exchange rates.")
    body = await parse(request, RigAdminAction, 12_000_000 if operation in {"import", "mapping", "confirm-mapping", "delete-products"} else 16000)
    if operation == "confirm-store-payment":
        from command import RigConfirmPaymentArgs, require_tool
        from trigger_commands import confirmation
        from trigger_routes import code_receipt
        require_tool(actor, "rig.quote.confirm_payment")
        try:
            data = RigConfirmPaymentArgs.model_validate(body.data).model_dump(mode="json")
        except ValidationError:
            raise HTTPException(422, "The action arguments are invalid") from None
        confirmation(actor, "rig.quote.confirm_payment", data)
        result = await rig_request(actor, operation, data)
        return {**result, "ledger_id": str(await code_receipt(actor.tenant_id, "rig.quote.confirm_payment", agent="rig"))}
    if operation == "commerce-connect":
        data = dict(body.data)
        credential = data.pop("credential", None)
        if credential is not None:
            fields = {"shopify": {"access_token", "webhook_secret"},
                      "woocommerce": {"consumer_key", "consumer_secret", "webhook_secret"},
                      "bigcommerce": {"access_token", "webhook_secret"}}.get(data.get("platform"))
            name = data.get("vault_name")
            if (fields is None or not isinstance(credential, dict) or not credential or set(credential) - fields
                    or any(not isinstance(value, str) or not 1 <= len(value) <= 8000 for value in credential.values())
                    or not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name)):
                raise HTTPException(422, "The action arguments are invalid")
            context = f"plugins:v1:{actor.tenant_id}:rig.vault:secret:{name}"
            # Approved forms omit signing inputs for now. Preserve an existing
            # sealed signing value; future approved fields use this same path.
            async with db().acquire() as conn:
                previous = await conn.fetchval("SELECT sealed FROM tank.vault_items WHERE tenant_id=$1 AND vault='rig.vault' AND kind='secrets' AND name=$2",
                                               actor.tenant_id, name)
            if previous:
                try:
                    stored = json.loads(vault.open_seal(previous, context)["value"])
                    credential = {**{key: value for key, value in stored.items() if key in fields}, **credential}
                except Exception:
                    raise HTTPException(422, "The action arguments are invalid") from None
            if fields - {"webhook_secret"} - set(credential):
                raise HTTPException(422, "The action arguments are invalid")
            encrypted = vault.seal({"value": json.dumps(credential)}, context)
            metadata = json.dumps({"name": name, "purpose": "commerce", "mask": "••••••••",
                                   "saved_at": datetime.now(timezone.utc).isoformat()})
            async with db().acquire() as conn:
                await conn.execute("""INSERT INTO tank.vault_items(tenant_id,vault,kind,name,metadata,sealed)
                    VALUES($1,'rig.vault','secrets',$2,$3::jsonb,$4)
                    ON CONFLICT(tenant_id,vault,kind,name) DO UPDATE SET metadata=excluded.metadata,sealed=excluded.sealed""",
                    actor.tenant_id, name, metadata, encrypted)
        return await rig_request(actor, operation, data)
    if operation in {"mapping", "confirm-mapping"}:
        try:
            data = {**body.data, "target": "rig"}
            preview_id = data.pop("preview_id", None) if operation == "confirm-mapping" else None
            if not {"currency", "country", "price_basis"}.issubset(data):
                raise ValueError()
            feed = (FeedImport if "contents" in data else FeedConnection).model_validate(data)
        except (ValidationError, ValueError):
            raise HTTPException(422, "Declare this feed's currency, country, price type and fields.") from None
        if operation == "confirm-mapping":
            return await rig_feed_mapping.confirm(db(), actor, feed, feed_storage_name(feed), site_name(feed.site), preview_id, receipt)
        try:
            if isinstance(feed, FeedImport):
                contents = feed.contents.get_secret_value()
                encoding = feed.encoding
            else:
                if origin(feed.endpoint) != site_name(feed.site) or urlsplit(feed.endpoint).query or urlsplit(feed.endpoint).fragment:
                    raise ValueError()
                config = feed.model_dump(exclude={"token", "username", "password"})
                config.update({key: getattr(feed, key).get_secret_value() for key in ("token", "username", "password")})
                raw = await read_rig_feed(config, feed_auth_headers(config))
                encoding = "base64" if feed.format == "xlsx" else "text"
                contents = base64.b64encode(raw).decode() if encoding == "base64" else raw.decode("utf-8-sig")
        except Exception:
            raise HTTPException(422, "The feed could not be read. Check its address, format and credentials.") from None
        result = await rig_request(actor, "mapping", {**feed.model_dump(include=RIG_SETTING_FIELDS), "provider": feed.distributor,
                                                    "contents": contents, "encoding": encoding})
        return await rig_feed_mapping.preview(db(), actor, feed, feed_storage_name(feed), site_name(feed.site), result)
    if operation in {"import", "connect", "refresh"}:
        model = {"import": FeedImport, "connect": FeedConnection, "refresh": FeedRefresh}[operation]
        try:
            feed = model.model_validate({**body.data, "target": "rig"})
        except (ValidationError, ValueError):
            raise HTTPException(422, "Check the feed fields and try again.") from None
        handler = {"import": import_feed_body, "connect": connect_feed_body, "refresh": refresh_connected_feed}[operation]
        return await handler(actor, feed)
    return await rig_request(actor, operation, body.data)


class RigTigerUpdate(Body):
    tenant_id: uuid.UUID
    sku: str = Field(min_length=1, max_length=120)
    price: str | None = Field(default=None, max_length=32)
    currency: str | None = Field(default=None, pattern="^[A-Z]{3}$")
    active: bool | None = None
    url: str | None = Field(default=None, max_length=2000)
    observed_at: datetime | None = None


def require_tiger(request: Request) -> None:
    if not key_matches(request.headers.get("x-tank-key", ""), os.environ.get("TANK_KEY_TIGER", "")):
        raise HTTPException(401, "Tiger authentication required.")


@router.get("/rig/tiger/tenants")
async def rig_tiger_tenants(request: Request):
    require_tiger(request)
    result = await rig_request(Actor(PLATFORM, "tiger-price-watch", False), "watch-tenants")
    if os.environ.get("RIG_LIFECYCLE_KEY"):
        from rig_rooms import catalogue_tenants
        result["tenants"] = await catalogue_tenants(db(), result.get("tenants", []))
    return result


@router.get("/rig/tiger/products")
async def rig_tiger_products(request: Request, tenant_id: uuid.UUID):
    require_tiger(request)
    return await rig_request(Actor(tenant_id, "tiger-price-watch", False), "watch-products")


@router.post("/rig/tiger")
async def rig_tiger_update(request: Request):
    require_tiger(request)
    body = await parse(request, RigTigerUpdate)
    actor = Actor(body.tenant_id, "tiger-price-watch", False, frozenset({"rig"}))
    return await rig_request(actor, "tiger", body.model_dump(mode="json", exclude={"tenant_id"}, exclude_none=True))


@router.get("/rig/tiger/alert-context")
async def rig_tiger_alert_context(request: Request, tenant_id: uuid.UUID, url: str):
    require_tiger(request)
    # Reuse the client's explicit product match. A competitor's SKU or title
    # is not evidence that it is the client's product.
    async with db().acquire() as conn:
        sku = await conn.fetchval("SELECT sku FROM tank.access_feed_links WHERE tenant_id=$1 AND url=$2", tenant_id, url)
        urls = [] if sku is None else [r["url"] for r in await conn.fetch(
            "SELECT url FROM tank.access_feed_links WHERE tenant_id=$1 AND sku=$2 ORDER BY url", tenant_id, sku)]
    if sku is None:
        return {"tenant_id": str(tenant_id), "product": None, "urls": []}
    result = await rig_request(Actor(tenant_id, "tiger-price-watch", False), "watch-context", {"sku": sku})
    if result.get("tenant_id") != str(tenant_id):
        raise HTTPException(503, "Rig returned an invalid response.")
    return {"tenant_id": str(tenant_id), "product": result.get("product"), "urls": urls}


async def refresh_due_rig_feeds() -> None:
    async with db().acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch("""SELECT tenant_id,distributor,site FROM tank.access_feed_connections
                WHERE distributor LIKE 'rig:%' AND rig_refresh_at<=now()
                ORDER BY rig_refresh_at FOR UPDATE SKIP LOCKED LIMIT 20""")
            for row in rows:
                await conn.execute("""UPDATE tank.access_feed_connections SET rig_refresh_at=
                    date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' + interval '1 day 3 hours'
                    WHERE tenant_id=$1 AND distributor=$2 AND site=$3""", row["tenant_id"], row["distributor"], row["site"])
    for row in rows:
        actor = Actor(row["tenant_id"], "nightly-feed", False, frozenset({"rig"}))
        try:
            parts = row["distributor"].split(":", 2)
            await refresh_connected_feed(actor, FeedRefresh(target="rig", distributor=parts[1], feed_id=parts[2] if len(parts) > 2 else "", site=row["site"]))
        except Exception:
            async with db().acquire() as conn:
                await receipt(conn, actor.tenant_id, "rig", row["site"], actor.user_id, "feed_refresh_failed")


async def rig_feed_loop() -> None:
    import logging
    while True:
        await asyncio.sleep(60)
        try:
            await refresh_due_rig_feeds()
        except Exception:
            logging.getLogger(__name__).warning("Rig feed refresh is unavailable. Check its connection and receipts.")


class FeedLink(Body):
    url: str = Field(max_length=2048)
    sku: str = Field(min_length=1, max_length=120)


@router.post("/feeds/link")
async def link_feed(request: Request, actor: Actor = Depends(identity)):
    permit_room(actor, "tiger")
    body = await parse(request, FeedLink)
    origin(body.url)
    if urlsplit(body.url).query or urlsplit(body.url).fragment:
        raise HTTPException(422, "Use the product page address without a query or fragment.")
    async with db().acquire() as conn:
        await conn.execute("""INSERT INTO tank.access_feed_links(tenant_id,url,sku) VALUES($1,$2,$3)
            ON CONFLICT(tenant_id,url) DO UPDATE SET sku=$3""", actor.tenant_id, body.url, body.sku)
    return {"ok": True}


class PublicSource(Body):
    tenant_id: uuid.UUID
    url: str = Field(max_length=2048)


@router.post("/archives")
async def archived_page(request: Request, room: str = Depends(room_identity)):
    body = await parse(request, PublicSource)
    site = origin(body.url, https=False)
    # Never send logged-in URLs or private portal data to an archive service.
    async with db().acquire() as conn:
        connected = await conn.fetchval("SELECT 1 FROM tank.access_connections WHERE tenant_id=$1 AND room=$2 AND site=$3", body.tenant_id, room, site)
    if connected or urlsplit(body.url).query or urlsplit(body.url).fragment or is_social(site):
        return {"found": False, "next": "existing_paid_rung"}
    from urllib.parse import urlencode
    failures = []
    try:
        raw, _ = await public_get("https://archive.org/wayback/available?" + urlencode({"url": body.url}))
        snapshot = json.loads(raw).get("archived_snapshots", {}).get("closest", {})
        stamp = snapshot.get("timestamp", "")
        if snapshot.get("available") and re.fullmatch(r"\d{14}", stamp):
            # Construct the URL ourselves; never trust a server-provided host.
            url = "https://web.archive.org/web/" + stamp + "id_/" + body.url
            html, _ = await public_get(url)
            async with db().acquire() as conn:
                await receipt(conn, body.tenant_id, room, site, "room", "wayback_archived")
            return {"found": True, "archived": True, "label": "archived, dated", "date": stamp,
                    "source": "Wayback Machine", "html": html.decode(errors="replace")[:600000]}
    except Exception:
        failures.append("Wayback Machine")
    try:
        # Discover the current index, instead of freezing a crawl release.
        raw, _ = await public_get("https://index.commoncrawl.org/collinfo.json")
        indexes = json.loads(raw)
        index = indexes[0]["id"]
        if not re.fullmatch(r"CC-MAIN-\d{4}-\d{2}", index): raise ValueError()
        raw, _ = await public_get("https://index.commoncrawl.org/" + index + "-index?" + urlencode({"url": body.url, "output": "json", "filter": "status:200", "collapse": "urlkey"}))
        rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
        entry = max(rows, key=lambda r: r.get("timestamp", ""))
        filename = entry["filename"]
        if not re.fullmatch(r"crawl-data/CC-MAIN-[A-Za-z0-9/._-]+\.warc\.gz", filename) or ".." in filename: raise ValueError()
        start, size = int(entry["offset"]), int(entry["length"])
        if start < 0 or not 0 < size <= 2_000_000: raise ValueError()
        compressed, _ = await public_get("https://data.commoncrawl.org/" + filename, headers={"Range": f"bytes={start}-{start+size-1}"})
        import gzip
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            data = stream.read(2_000_001)
        if len(data) > 2_000_000: raise ValueError()
        warc, http = data.split(b"\r\n\r\n", 1)
        if (b"WARC-Target-URI: " + body.url.encode()) not in warc: raise ValueError()
        _, html = http.split(b"\r\n\r\n", 1)
        async with db().acquire() as conn:
            await receipt(conn, body.tenant_id, room, site, "room", "common_crawl_archived")
        return {"found": True, "archived": True, "label": "archived, dated", "date": entry["timestamp"],
                "source": "Common Crawl", "html": html.decode(errors="replace")[:600000]}
    except Exception:
        failures.append("Common Crawl")
    return {"found": False, "unavailable": failures, "next": "existing_paid_rung"}


class SocialPage(Body):
    tenant_id: uuid.UUID
    platform: Literal["facebook", "instagram"]
    business_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,100}$")
    business_username: str = Field(default="", pattern=r"^[A-Za-z0-9._]{0,60}$")


@router.post("/social")
async def social_page(request: Request, room: str = Depends(room_identity)):
    body = await parse(request, SocialPage)
    if room != "base": raise HTTPException(403, "Only Base reads business pages.")
    if body.platform == "facebook" and (".." in body.business_id or "/" in body.business_id):
        raise HTTPException(422, "Use a public business page identifier, not a path.")
    if not os.environ.get("META_APP_ID") or os.environ.get("META_APP_REVIEW_APPROVED") != "true":
        return {"enabled": False, "source": "search_results"}
    version = os.environ.get("META_GRAPH_VERSION", "")
    token = os.environ.get("META_ACCESS_TOKEN", "")
    if not token or not re.fullmatch(r"v\d+\.\d+", version):
        raise HTTPException(503, "The approved business-page API is not configured.")
    from urllib.parse import urlencode
    business_id = body.business_id
    fields = "id,name,category,website,link"
    if body.platform == "instagram":
        business_id = os.environ.get("META_IG_BUSINESS_ID", "")
        if not business_id.isdigit(): raise HTTPException(503, "The approved Instagram business account is not configured.")
        if not body.business_username: raise HTTPException(422, "A business username is required.")
        fields = "business_discovery.username(" + body.business_username + "){username,name,website,biography}"
    try:
        raw, _ = await public_get("https://graph.facebook.com/" + version + "/" + business_id + "?" + urlencode({"fields": fields}),
                                  headers={"Authorization": "Bearer " + token}, limit=100000)
        data = json.loads(raw)
        if body.platform == "facebook":
            if not data.get("category"): raise ValueError()
            result = {k: data.get(k) for k in ("id", "name", "category", "website", "link")}
        else:
            data = data["business_discovery"]
            result = {k: data.get(k) for k in ("username", "name", "website", "biography")}
    except Exception:
        raise HTTPException(503, "The public business page could not be read; use search evidence.") from None
    async with db().acquire() as conn:
        await receipt(conn, body.tenant_id, room, "https://graph.facebook.com", "room", "public_business_api")
    return {"enabled": True, "source": "Meta official API", "page": result}

# Tiger T1. Product evidence is code-only; names and competitor SKUs are never
# used as identifiers. The existing structured reader owns price parsing.
def tiger_gtin(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    digits = re.sub(r'[\s-]', '', value)
    if not re.fullmatch(r'(?:[0-9]{8}|[0-9]{12}|[0-9]{13}|[0-9]{14})', digits) or not digits.strip('0'):
        return None
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(digits[:-1])))
    return digits.zfill(14) if (10 - total % 10) % 10 == int(digits[-1]) else None


def tiger_identity_match(own: dict, seen: dict) -> tuple[str, str] | None:
    raw = seen.get('gtins') or []
    gtins = [tiger_gtin(v) for v in raw]
    if raw:
        # A bad or conflicting barcode never falls back to a title/MPN match.
        expected = tiger_gtin(own.get('gtin'))
        if expected and all(gtins) and set(gtins) == {expected}:
            return 'gtin', 'confirmed'
        return None
    brand, mpn = seen.get('brand'), seen.get('mpn')
    if (isinstance(brand, str) and isinstance(mpn, str) and brand.strip() and mpn.strip()
            and brand.strip().casefold() == str(own.get('brand') or '').strip().casefold()
            and mpn.strip() == str(own.get('mpn') or '').strip()):
        return 'brand_mpn', 'suggested'
    return None


def tiger_product_evidence(html: str, url: str) -> list[dict]:
    import importlib.util
    from pathlib import Path
    from urllib.parse import urljoin
    from selectolax.parser import HTMLParser
    # Reuse the published pure-code reader unchanged, without the model ladder.
    module = globals().get('_tiger_structured_reader')
    if module is None:
        path = Path(__file__).parent / 'skills/tiger.price_structured/1.0.0/run.py'
        spec = importlib.util.spec_from_file_location('tiger_t1_structured_reader', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        globals()['_tiger_structured_reader'] = module
    if not html or len(html) > 600_000:
        return []
    tree, nodes = HTMLParser(html), []
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            module._walk(json.loads(script.text()), nodes)
        except (ValueError, RecursionError):
            continue
    result = []
    for node in nodes:
        stated_url = module._http_url(node.get('url'))
        if stated_url is None and isinstance(node.get('url'), str) and node['url'].strip():
            stated_url = urljoin(url, node['url'])
        if stated_url and stated_url.rstrip('/') != url.rstrip('/'):
            continue
        # An unlocated Product on a multi-product listing is ambiguous.
        if not stated_url and len(nodes) != 1:
            continue
        brand = node.get('brand')
        if isinstance(brand, dict):
            brand = brand.get('name')
        facts = {'gtins': [node[k] for k in ('gtin','gtin8','gtin12','gtin13','gtin14') if node.get(k) is not None and node.get(k) != ''],
                 'brand': brand if isinstance(brand, str) else None,
                 'mpn': node.get('mpn') if isinstance(node.get('mpn'), str) else None}
        prices = module._from_product(node, url)
        facts['readings'] = [{k: p.get(k) for k in ('price','currency','availability','method')} for p in prices]
        result.append(facts)
    if nodes:
        return result
    products = [p for p in tree.css('[itemscope]') if any(t in {'https://schema.org/Product','http://schema.org/Product'}
                for t in (p.attributes.get('itemtype') or '').split())]
    for product in products:
        if len(products) != 1:
            continue
        fields = {}
        for el in product.css('[itemprop]'):
            owner = module._nearest_scope(el)
            prop = el.attributes.get('itemprop')
            if owner is not None and owner.mem_id == product.mem_id:
                fields[prop] = module._prop_value(el)
                if prop == 'brand' and 'itemscope' in el.attributes:
                    names = [n for n in el.css('[itemprop="name"]') if module._nearest_scope(n).mem_id == el.mem_id]
                    if len(names) == 1:
                        fields['brand'] = module._prop_value(names[0])
        if fields.get('url') and urljoin(url, fields['url']).rstrip('/') != url.rstrip('/'):
            continue
        result.append({'gtins': [fields[k] for k in ('gtin','gtin8','gtin12','gtin13','gtin14') if fields.get(k)],
                       'brand': fields.get('brand'), 'mpn': fields.get('mpn'),
                       'readings': [{k: p.get(k) for k in ('price','currency','availability','method')}
                                    for p in module.extract(product.html, url)['items']]})
    return result


async def tiger_catalogue(tenant) -> list[dict]:
    result = await rig_request(Actor(uuid.UUID(str(tenant)), 'tiger-match', False), 'watch-products', {'catalogue': True})
    if result.get('tenant_id') != str(tenant) or not isinstance(result.get('products'), list):
        raise HTTPException(503, 'Rig returned an invalid response.')
    return result['products']


async def tiger_sources(conn, tenant) -> list[str]:
    rows = await conn.fetch("SELECT metadata FROM tank.vault_items WHERE tenant_id=$1 AND vault='tiger.vault' AND kind='links' ORDER BY created_at,id", tenant)
    sites = []
    for row in rows:
        meta = json.loads(row['metadata']) if isinstance(row['metadata'], str) else row['metadata']
        try:
            site = origin(meta.get('url', ''))
        except HTTPException:
            continue
        if site not in sites:
            sites.append(site)
    return sites


class TigerMatchPage(Body):
    tenant_id: uuid.UUID
    site: str = Field(max_length=2048)
    url: str = Field(max_length=2048)
    html: str = Field(default='', max_length=600000)
    human_check: bool = False
    job_id: uuid.UUID | None = None
    root_job_id: uuid.UUID | None = None


async def tiger_link(tenant, match: dict) -> bool:
    from main import _tiger_call
    # Hold the evidence row and URL binding together through watch creation.
    # Concurrent confirmation/rejection cannot leave a rejected row linked.
    async with db().acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow('SELECT state FROM agent_tiger.matches WHERE tenant_id=$1 AND id=$2 FOR UPDATE', tenant, match['id'])
            if current is None or current['state'] == 'rejected':
                return False
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', str(tenant) + ':' + match['url'])
            linked = await conn.fetchval('SELECT sku FROM tank.access_feed_links WHERE tenant_id=$1 AND url=$2', tenant, match['url'])
            if linked is not None and linked != match['sku']:
                return False
            response = await _tiger_call('POST', '/watchlist', [], json.dumps({'tenant_id': str(tenant), 'url': match['url'], 'every': '6h',
                'alert_price': True, 'alert_stock': True, 'alert_new_product': True, 'alert_below_cost': True, 'alert_market_gap': True}).encode())
            if response.status_code not in {201,409}:
                raise HTTPException(503, 'Tiger is unreachable')
            await conn.execute('INSERT INTO tank.access_feed_links(tenant_id,url,sku) VALUES($1,$2,$3) ON CONFLICT(tenant_id,url) DO NOTHING', tenant, match['url'], match['sku'])
            await conn.execute("UPDATE agent_tiger.matches SET state='confirmed' WHERE tenant_id=$1 AND id=$2", tenant, match['id'])
            return True


@router.post('/rig/tiger/match-page')
async def tiger_match_page(body: TigerMatchPage, request: Request):
    result = await _tiger_match_page(body, request)
    from tiger_savings import Run, record, packed
    from tenant_db import tenant_scope
    import hashlib
    identity = uuid.uuid5(body.job_id or uuid.uuid4(), hashlib.sha256(packed(body.model_dump(mode='json')).encode()).hexdigest())
    with tenant_scope(str(body.tenant_id)):
        async with db().acquire() as conn:
            async with conn.transaction():
                receipt = await record(conn, Run(tenant_id=body.tenant_id, id=identity, action='tiger.match',
                    input=body.model_dump(mode='json'), output=result), component=True)
    return {**result, '_savings': receipt}


async def _tiger_match_page(body: TigerMatchPage, request: Request):
    require_tiger(request)
    from tenant_db import tenant_scope
    with tenant_scope(str(body.tenant_id)):
        site = origin(body.site)
        if origin(body.url) != site:
            raise HTTPException(422, 'Check the fields. Nothing was changed.')
        async with db().acquire() as conn:
            if site not in await tiger_sources(conn, body.tenant_id):
                raise HTTPException(403, 'This room is not enabled for this account.')
            if body.human_check:
                await conn.execute("""INSERT INTO tank.access_queue(tenant_id,room,site,reason)
                    VALUES($1,'tiger',$2,'captcha') ON CONFLICT (tenant_id,room,site)
                    WHERE status IN ('waiting','handling') DO NOTHING""", body.tenant_id, site)
                return {'matches': [], 'human_check': True}
        if body.job_id:
            async with db().acquire() as conn:
                valid = await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM agent_tiger.jobs
                    WHERE tenant_id=$1 AND id=$2 AND kind='competitor_match'
                    AND COALESCE(input->>'root_job_id',id::text)=$3)""",
                    body.tenant_id, body.job_id, str(body.root_job_id or body.job_id))
            if not valid:
                raise HTTPException(422, 'Check the fields. Nothing was changed.')
        from urllib.parse import parse_qsl
        parsed = urlsplit(body.url)
        if parsed.path.rstrip('/').lower() == '/search' or {'q','s','search','query','search_query'} & {k for k,_ in parse_qsl(parsed.query)}:
            return {'matches': []}
        products = await tiger_catalogue(body.tenant_id)
        matches = []
        facts = tiger_product_evidence(body.html, body.url)
        possible_skus = {p['sku'] for seen in facts for p in products if tiger_identity_match(p, seen)}
        if len(possible_skus) > 1:
            return {'matches': []}
        for seen in facts:
            candidates = [(p, tiger_identity_match(p, seen)) for p in products]
            candidates = [(p, rule) for p, rule in candidates if rule]
            # One URL cannot be auto-assigned to several catalogue SKUs.
            if len(candidates) != 1:
                continue
            own, (method, state) = candidates[0]
            evidence = {'own': {k: own.get(k) for k in ('gtin','brand','mpn')}, 'seen': seen}
            async with db().acquire() as conn:
                row = await conn.fetchrow("""INSERT INTO agent_tiger.matches(tenant_id,sku,url,method,identifiers,seen_on,state)
                    VALUES($1,$2,$3,$4,$5::jsonb,$3,'suggested') ON CONFLICT(tenant_id,sku,url)
                    DO UPDATE SET checked_at=now(),identifiers=EXCLUDED.identifiers,method=EXCLUDED.method
                    RETURNING *""", body.tenant_id, own['sku'], body.url, method, json.dumps(evidence))
            if state == 'confirmed' and row['state'] != 'rejected':
                await tiger_link(body.tenant_id, dict(row))
            async with db().acquire() as conn:
                current = await conn.fetchrow('SELECT * FROM agent_tiger.matches WHERE tenant_id=$1 AND id=$2', body.tenant_id, row['id'])
            snapshot = tiger_match_out(current)
            if body.job_id:
                async with db().acquire() as conn:
                    await conn.execute("""INSERT INTO agent_tiger.match_sightings(tenant_id,job_id,root_job_id,match_id,evidence)
                        VALUES($1,$2,$3,$4,$5::jsonb) ON CONFLICT(tenant_id,job_id,match_id)
                        DO UPDATE SET evidence=EXCLUDED.evidence""", body.tenant_id, body.job_id,
                        body.root_job_id or body.job_id, current['id'], json.dumps(snapshot))
            matches.append(snapshot)
        return {'matches': matches}


def tiger_match_out(row) -> dict:
    data = dict(row)
    data['id'], data['tenant_id'] = str(data['id']), str(data['tenant_id'])
    data['checked_at'] = data['checked_at'].isoformat()
    if isinstance(data.get('identifiers'), str):
        data['identifiers'] = json.loads(data['identifiers'])
    return data


class TigerMatchAction(Body):
    tenant_id: uuid.UUID | None = None
    match_id: uuid.UUID | None = None


async def tiger_actor(actor: Actor, tool: str) -> Actor:
    from command import authorized_actor, require_tool
    actor = await authorized_actor(actor)
    require_tool(actor, tool)
    return actor


@router.post('/tiger/match')
async def tiger_match_start(request: Request, actor: Actor = Depends(identity)):
    from trigger_commands import confirmation
    from trigger_routes import code_receipt
    from main import _tiger_call
    actor = await tiger_actor(actor, 'tiger.match')
    body = await parse(request, TigerMatchAction)
    if body.tenant_id not in {None, actor.tenant_id} or body.match_id is not None:
        raise HTTPException(422, 'Check the fields. Nothing was changed.')
    operation = confirmation(actor, 'tiger.match', {})
    async with db().acquire() as conn:
        sources = await tiger_sources(conn, actor.tenant_id)
        existing = await conn.fetchrow('SELECT * FROM agent_tiger.match_runs WHERE tenant_id=$1 AND operation_id=$2', actor.tenant_id, operation)
    if existing and existing['job_id']:
        ledger, job_id = existing['ledger_id'], existing['job_id']
    else:
        ledger = existing['ledger_id'] if existing else await code_receipt(actor.tenant_id, 'tiger.match', agent='tiger')
        async with db().acquire() as conn:
            await conn.execute('INSERT INTO agent_tiger.match_runs(tenant_id,operation_id,ledger_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING', actor.tenant_id, operation, ledger)
        products = await tiger_catalogue(actor.tenant_id)
        response = await _tiger_call('POST', '/jobs', [], json.dumps({'tenant_id': str(actor.tenant_id), 'kind': 'competitor_match',
            'input': {'sites': sources, 'products': products, 'operation_id': str(operation)}}).encode())
        if response.status_code != 201:
            raise HTTPException(503, 'Tiger is unreachable')
        job = response.json()
        if job.get('tenant_id') != str(actor.tenant_id):
            raise HTTPException(503, 'Tiger is unreachable')
        job_id = uuid.UUID(job['id'])
        async with db().acquire() as conn:
            await conn.execute('UPDATE agent_tiger.match_runs SET job_id=$3 WHERE tenant_id=$1 AND operation_id=$2', actor.tenant_id, operation, job_id)
    result = {'tenant_id': str(actor.tenant_id), 'job_id': str(job_id), 'status': 'queued', 'matches': [],
              'ledger_id': str(ledger), 'cost_usd': 0, 'model_calls': 0}
    return {**result, 'text': json.dumps(result, ensure_ascii=False)}


@router.post('/tiger/match/{decision}')
async def tiger_match_decide(decision: str, request: Request, actor: Actor = Depends(identity)):
    from trigger_commands import confirmation
    from trigger_routes import code_receipt
    if decision not in {'confirm','reject','undo'}:
        raise HTTPException(404, 'Not found')
    tool = 'tiger.match.' + decision
    actor = await tiger_actor(actor, tool)
    body = await parse(request, TigerMatchAction)
    if body.tenant_id not in {None, actor.tenant_id} or body.match_id is None:
        raise HTTPException(422, 'Check the fields. Nothing was changed.')
    confirmation(actor, tool, {'match_id': str(body.match_id)})
    ledger = await code_receipt(actor.tenant_id, tool, agent='tiger')
    async with db().acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM agent_tiger.matches WHERE tenant_id=$1 AND id=$2', actor.tenant_id, body.match_id)
    if row is None:
        raise HTTPException(404, 'Not found')
    expected = 'rejected' if decision == 'undo' else 'suggested'
    if row['state'] != expected:
        raise HTTPException(409, 'Confirmed action required')
    if decision == 'confirm':
        # Current catalogue identifiers and Source entitlement remain authoritative.
        async with db().acquire() as conn:
            if origin(row['url']) not in await tiger_sources(conn, actor.tenant_id):
                raise HTTPException(403, 'This room is not enabled for this account.')
        evidence = tiger_match_out(row)['identifiers']['seen']
        own = next((p for p in await tiger_catalogue(actor.tenant_id) if p['sku'] == row['sku']), None)
        if own is None or tiger_identity_match(own, evidence) is None:
            raise HTTPException(409, 'Confirmed action required')
        if not await tiger_link(actor.tenant_id, dict(row)):
            raise HTTPException(409, 'Confirmed action required')
    else:
        async with db().acquire() as conn:
            changed = await conn.fetchrow("""UPDATE agent_tiger.matches SET state=$3
                WHERE tenant_id=$1 AND id=$2 AND state=$4 RETURNING *""",
                actor.tenant_id, body.match_id, 'suggested' if decision == 'undo' else 'rejected', expected)
        if changed is None:
            raise HTTPException(409, 'Confirmed action required')
    async with db().acquire() as conn:
        current = await conn.fetchrow('SELECT * FROM agent_tiger.matches WHERE tenant_id=$1 AND id=$2', actor.tenant_id, body.match_id)
    result = {'match': tiger_match_out(current), 'ledger_id': str(ledger), 'cost_usd': 0, 'model_calls': 0}
    return {**result, 'text': json.dumps(result, ensure_ascii=False)}


@router.get('/tiger/matches')
async def tiger_matches(actor: Actor = Depends(identity)):
    from trigger_routes import code_receipt
    actor = await tiger_actor(actor, 'tiger.matches')
    ledger = await code_receipt(actor.tenant_id, 'tiger.matches', agent='tiger')
    async with db().acquire() as conn:
        rows = await conn.fetch("""SELECT * FROM agent_tiger.matches
            WHERE tenant_id=$1 AND state IN ('suggested','rejected')
            ORDER BY checked_at DESC,id DESC LIMIT 100""", actor.tenant_id)
        checks = await tiger_site_checks(conn, actor.tenant_id)
    try:
        catalogue = {row['sku']: row for row in await tiger_catalogue(actor.tenant_id)} if rows else {}
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        catalogue = {}  # Stored identifiers/SKUs remain readable during a Rig outage.
    matches = []
    for row in rows:
        product = catalogue.get(row['sku'], {})
        matches.append({**tiger_match_out(row), 'product': {key: product.get(key) for key in ('name','brand','mpn')}})
    result = {'tenant_id': str(actor.tenant_id), 'matches': matches, 'site_checks': checks,
              'ledger_id': str(ledger), 'cost_usd': 0, 'model_calls': 0}
    return tiger_bounded_card(result, 'matches')


async def tiger_site_checks(conn, tenant) -> list[dict]:
    """Latest saved discovery summaries; never infer checks from a vault link."""
    runs = await conn.fetch("""SELECT r.job_id,r.progress
        FROM agent_tiger.match_runs r JOIN agent_tiger.jobs j ON j.id=r.job_id AND j.tenant_id=$1
        WHERE r.tenant_id=$1 AND r.progress IS NOT NULL
        ORDER BY j.created_at DESC,j.id DESC LIMIT 100""", tenant)
    if not runs:
        return []
    roots = [r['job_id'] for r in runs]
    # A summary first appears in the slice that finished that site. Later slices
    # repeat it while checking other sites; they must not refresh its timestamp.
    times = await conn.fetch("""SELECT coalesce(j.input->>'root_job_id',j.id::text) AS root,
        site.value->>'site' AS site,min(j.finished_at) AS checked_at
        FROM agent_tiger.jobs j CROSS JOIN LATERAL jsonb_array_elements(
          CASE WHEN jsonb_typeof(j.result->'sites')='array' THEN j.result->'sites' ELSE '[]'::jsonb END) site(value)
        WHERE j.tenant_id=$1 AND j.kind='competitor_match'
        AND (j.id=ANY($2::uuid[]) OR j.input->>'root_job_id'=ANY($3::text[])) GROUP BY 1,2""",
        tenant, roots, [str(root) for root in roots])
    stamps = {}
    for row in times:
        try:
            stamps[row['root'], origin(row['site'])] = row['checked_at']
        except (HTTPException, ValueError, TypeError):
            continue
    sightings = await conn.fetch("""SELECT DISTINCT root_job_id,match_id,evidence->>'url' AS url
        FROM agent_tiger.match_sightings WHERE tenant_id=$1 AND root_job_id=ANY($2::uuid[])""",
        tenant, roots)
    found = {}
    for row in sightings:
        try:
            found.setdefault((row['root_job_id'], origin(row['url'])), set()).add(row['match_id'])
        except (HTTPException, ValueError, TypeError):
            continue
    saved = set(await tiger_sources(conn, tenant))
    result, seen = [], set()
    for run in runs:
        progress = json.loads(run['progress']) if isinstance(run['progress'], str) else run['progress']
        for site in progress.get('sites', []):
            try:
                url = origin(site['site'])
            except (HTTPException, ValueError, TypeError, KeyError):
                continue
            if url not in saved or url in seen:
                continue
            seen.add(url)
            warnings = site.get('warnings') or []
            skipped = {w.get('url') for w in warnings if w.get('reason') == 'robots' and w.get('url')}
            status = 'human_check' if site.get('status') == 'human_check' else 'robots' if skipped else 'checked' if site.get('status') == 'completed' else None
            pages = site.get('pages')
            result.append({'url': url, 'pages': pages if type(pages) is int and pages >= 0 else None,
                'matches': len(found.get((run['job_id'], url), set())),
                'checked_at': stamps[str(run['job_id']), url].isoformat() if stamps.get((str(run['job_id']), url)) else None,
                'status': status, 'skipped': len(skipped) if skipped else None})
            if len(result) == 100:
                return result
    return result


def tiger_positions(products: list[dict], matches: list[dict], observations: dict[str, dict]) -> list[dict]:
    rows = []
    for own in products:
        competitors = []
        for match in matches:
            if match['sku'] != own['sku'] or match['state'] != 'confirmed':
                continue
            reading = observations.get(match['url']) or {}
            competitors.append({'url': match['url'], 'price': reading.get('price'), 'currency': reading.get('currency'),
                'stock': reading.get('availability'), 'checked_at': reading.get('ts')})
        if not competitors:
            continue
        def money(value):
            try:
                n = Decimal(str(value))
                return n if n.is_finite() and n >= 0 else None
            except InvalidOperation:
                return None
        price = money(own.get('price'))
        comparable = [(money(c['price']),c['url']) for c in competitors if c['currency'] == own.get('currency') and money(c['price']) is not None]
        cheapest = min(comparable) if comparable else None
        rows.append({'sku': own['sku'], 'name': own.get('name'), 'own_price': own.get('price'), 'currency': own.get('currency'), 'competitors': competitors,
            'cheapest': {'price': str(cheapest[0]), 'url': cheapest[1]} if cheapest else None,
            'rank': 1 + sum(p < price for p,_ in comparable) if price is not None and comparable else None,
            'difference': str(price - cheapest[0]) if price is not None and cheapest else None})
    return rows


@router.get('/tiger/position')
async def tiger_position(actor: Actor = Depends(identity)):
    from trigger_routes import code_receipt
    actor = await tiger_actor(actor, 'tiger.position')
    ledger = await code_receipt(actor.tenant_id, 'tiger.position', agent='tiger')
    products = await tiger_catalogue(actor.tenant_id)
    async with db().acquire() as conn:
        skus = [p['sku'] for p in products]
        total = await conn.fetchval("SELECT count(DISTINCT sku) FROM agent_tiger.matches WHERE tenant_id=$1 AND state='confirmed' AND sku=ANY($2::text[])", actor.tenant_id, skus)
        selected = await conn.fetch("SELECT DISTINCT sku FROM agent_tiger.matches WHERE tenant_id=$1 AND state='confirmed' AND sku=ANY($2::text[]) ORDER BY sku LIMIT 100", actor.tenant_id, skus)
        selected = [r['sku'] for r in selected]
        readings = await conn.fetch("""SELECT m.sku,m.url,m.state,o.price,o.currency,o.availability,o.ts
            FROM agent_tiger.matches m LEFT JOIN LATERAL (
                SELECT price,currency,availability,ts FROM agent_tiger.observations
                WHERE tenant_id=$1 AND url=m.url ORDER BY ts DESC,id DESC LIMIT 1
            ) o ON true WHERE m.tenant_id=$1 AND m.state='confirmed' AND m.sku=ANY($2::text[])
            ORDER BY m.sku,m.url""", actor.tenant_id, selected)
        matches = [dict(row) for row in readings]
        observations = {row['url']: {k: str(row[k]) if isinstance(row[k], (Decimal,datetime)) else row[k]
                        for k in ('price','currency','availability','ts')} for row in readings}
    result = {'tenant_id': str(actor.tenant_id), 'products': tiger_positions(products, matches, observations),
              'ledger_id': str(ledger), 'cost_usd': 0, 'model_calls': 0}
    result.update(total=total, has_more=total > len(result['products']))
    return tiger_bounded_card(result, 'products')


def tiger_bounded_card(result, field):
    while True:
        card = {**result, 'text': json.dumps(result, ensure_ascii=False, default=str)}
        if len(json.dumps(card, ensure_ascii=False, default=str).encode()) <= 240000 or not result[field]:
            return card
        result[field].pop()
        result['has_more'] = True


async def tiger_card_result(tenant, result: dict) -> dict:
    if not result.get('job_id'):
        return result
    try:
        return await _tiger_card_result(tenant, result)
    except Exception:
        # A card read is optional enrichment; preserve the saved thread/card.
        import logging
        logging.getLogger(__name__).warning('Tiger match card enrichment unavailable')
        return result


async def _tiger_card_result(tenant, result):
    root = uuid.UUID(result['job_id'])
    async with db().acquire() as conn:
        job = await conn.fetchrow("SELECT status,result FROM agent_tiger.jobs WHERE tenant_id=$1 AND id=$2 AND kind='competitor_match'", tenant, root)
        if job is None:
            return result
        progress = await conn.fetchval('SELECT progress FROM agent_tiger.match_runs WHERE tenant_id=$1 AND job_id=$2', tenant, root)
        discovery = progress or job['result'] or {}
        if isinstance(discovery, str):
            discovery = json.loads(discovery)
        if discovery.get('status', job['status']) in {'running','queued'}:
            active = uuid.UUID(discovery['continuation_id']) if discovery.get('continuation_id') else root
            state = await conn.fetchval('SELECT status FROM agent_tiger.jobs WHERE tenant_id=$1 AND id=$2', tenant, active)
            if state == 'failed':
                discovery = {**discovery, 'status':'failed', 'failed_job_id':str(active)}
                await conn.execute('UPDATE agent_tiger.match_runs SET progress=$3::jsonb WHERE tenant_id=$1 AND job_id=$2', tenant, root, json.dumps(discovery))
        matches = await conn.fetch("""SELECT DISTINCT ON (match_id) match_id,evidence
            FROM agent_tiger.match_sightings WHERE tenant_id=$1 AND root_job_id=$2 ORDER BY match_id,job_id LIMIT 100""", tenant, root)
        count = await conn.fetchval('SELECT count(DISTINCT match_id) FROM agent_tiger.match_sightings WHERE tenant_id=$1 AND root_job_id=$2', tenant, root)
    update = {k:v for k,v in result.items() if k != 'text'}
    update.update(status=discovery.get('status', job['status']),
                  matches=[json.loads(r['evidence']) if isinstance(r['evidence'], str) else r['evidence'] for r in matches],
                  matches_count=count, has_more=count > len(matches), discovery=discovery)
    return tiger_bounded_card(update, 'matches')


from rig_rooms import register as register_rig_rooms
register_rig_rooms(router, identity, db)
