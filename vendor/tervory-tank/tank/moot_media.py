"""Authenticated Moot media gateway on Tank. Tenant/user/hearing isolated."""

from __future__ import annotations

import os
import re
import hashlib
import json
from typing import Any, Awaitable, Callable
from uuid import UUID

from moot_media_store import (
    AsyncOwnershipStore,
    MediaRecord,
    MemoryOwnershipStore,
    ReservationError,
)


async def _analytics(
    event: str,
    actor: "MootActor",
    *,
    properties: dict | None = None,
    environ: dict | None = None,
) -> None:
    """Best-effort Tank PostHog capture; never fails the media path."""
    try:
        from posthog_door import capture_event

        await capture_event(
            event=event,
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            properties=properties,
            environ=environ,
        )
    except Exception:
        return None


from moot_mux import (
    PLAYBACK_POLICY,
    MuxError,
    MuxNotConfigured,
    MuxProvider,
    SignedPlaybackUnconfigured,
    mint_playback_jwt,
    normalize_cors_origin,
    provider_from_env,
    require_signed_playback_ready,
    signed_playback_url,
)

HEARING_UUID = re.compile(
    r"^moot/hearings/([0-9a-fA-F-]{36})/media/(uploads|assets|playback)(?:/([0-9A-Za-z_-]{1,80}))?$"
)

HearingGate = Callable[[str, str, str], Awaitable[bool]]

# Soft guards before provider writes (env-overridable; fail closed on overflow).
DEFAULT_UPLOAD_RATE_PER_MIN = 6
DEFAULT_DAILY_UPLOAD_CAP = 40
IDEMPOTENCY_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")


class MootActor:
    __slots__ = ("tenant_id", "user_id", "founder")

    def __init__(self, tenant_id: str, user_id: str, founder: bool = False):
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.founder = founder


DEFAULT_STORE: AsyncOwnershipStore = MemoryOwnershipStore()


def match_media_path(path: str) -> dict[str, str] | None:
    cleaned = (path or "").strip().strip("/")
    matched = HEARING_UUID.fullmatch(cleaned)
    if not matched:
        return None
    hearing_id, kind, resource_id = matched.group(1), matched.group(2), matched.group(3)
    if kind in {"assets", "playback"} and not resource_id:
        return None
    if kind == "uploads" and resource_id is None:
        return {"hearing_id": hearing_id, "kind": kind, "resource_id": ""}
    return {"hearing_id": hearing_id, "kind": kind, "resource_id": resource_id or ""}


def passthrough_for(actor: MootActor, hearing_id: str) -> str:
    return f"moot:{actor.tenant_id}:{hearing_id}:{actor.user_id}"[:255]


def _int_env(name: str, default: int, environ: dict | None) -> int:
    env = os.environ if environ is None else environ
    raw = str(env.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


async def _owned(
    record: MediaRecord | None, actor: MootActor, hearing_id: str
) -> MediaRecord:
    if record is None:
        raise MuxError("not_found", 404)
    if record.tenant_id != actor.tenant_id or record.hearing_id != hearing_id:
        raise MuxError("not_found", 404)
    if record.user_id != actor.user_id and not actor.founder:
        raise MuxError("not_found", 404)
    return record


def public_upload_view(data: dict[str, Any]) -> dict[str, Any]:
    settings = (
        data.get("new_asset_settings")
        if isinstance(data.get("new_asset_settings"), dict)
        else {}
    )
    policies = (
        settings.get("playback_policies")
        or data.get("playback_policies")
        or [PLAYBACK_POLICY]
    )
    return {
        "upload_id": data.get("id"),
        "status": data.get("status"),
        "upload_url": data.get("url"),
        "timeout": data.get("timeout"),
        "asset_id": data.get("asset_id"),
        "playback_policy": PLAYBACK_POLICY
        if PLAYBACK_POLICY in policies
        else "unknown",
        "test": bool(data.get("test") or settings.get("test")),
    }


def public_asset_view(data: dict[str, Any]) -> dict[str, Any]:
    playback_ids = []
    for row in data.get("playback_ids") or []:
        if isinstance(row, dict) and row.get("id"):
            playback_ids.append(
                {"id": row["id"], "policy": row.get("policy") or PLAYBACK_POLICY}
            )
    return {
        "asset_id": data.get("id"),
        "status": data.get("status"),
        "duration": data.get("duration"),
        "playback_ids": playback_ids,
        "upload_id": data.get("upload_id"),
        "test": bool(data.get("test")),
    }


async def _hearing_access(
    actor: MootActor, hearing_id: str, hearing_allowed: HearingGate
) -> None:
    if not await hearing_allowed(actor.tenant_id, actor.user_id, hearing_id):
        raise MuxError("not_found", 404)


async def create_upload(
    *,
    actor: MootActor,
    hearing_id: str,
    cors_origin: str,
    provider: MuxProvider,
    store: AsyncOwnershipStore,
    hearing_allowed: HearingGate,
    test: bool = False,
    idempotency_key: str | None = None,
    environ: dict | None = None,
) -> dict[str, Any]:
    UUID(hearing_id)
    UUID(actor.tenant_id)
    origin = normalize_cors_origin(cors_origin)
    await _hearing_access(actor, hearing_id, hearing_allowed)
    if not idempotency_key or not IDEMPOTENCY_RE.fullmatch(idempotency_key):
        raise MuxError("invalid_idempotency_key", 422)
    digest = hashlib.sha256(
        json.dumps([origin, test], separators=(",", ":")).encode()
    ).hexdigest()
    try:
        reservation = await store.reserve_upload(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            hearing_id=hearing_id,
            idempotency_key=idempotency_key,
            input_hash=digest,
            rate=_int_env(
                "MOOT_MUX_UPLOAD_RATE_PER_MIN", DEFAULT_UPLOAD_RATE_PER_MIN, environ
            ),
            daily=_int_env(
                "MOOT_MUX_DAILY_UPLOAD_CAP", DEFAULT_DAILY_UPLOAD_CAP, environ
            ),
        )
    except ReservationError as exc:
        code = str(exc)
        raise MuxError(
            code, 429 if code in {"rate_limited", "upload_budget_exhausted"} else 409
        ) from None
    if reservation.upload_id:
        return await upload_status(
            actor=actor,
            hearing_id=hearing_id,
            upload_id=reservation.upload_id,
            provider=provider,
            store=store,
            hearing_allowed=hearing_allowed,
        )
    # The committed intent is recoverable even if the provider response is lost.
    # Do not assume the provider supports an idempotency header or retry a write.
    marker = "moot-request:" + reservation.request_id
    try:
        await _hearing_access(actor, hearing_id, hearing_allowed)
        created = await provider.create_direct_upload(
            cors_origin=origin, passthrough=marker, test=test
        )
        upload_id = str(created.get("id") or "")
        if not upload_id:
            raise MuxError("mux_upstream_error", 502)
        await store.complete_upload(
            reservation,
            MediaRecord(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                hearing_id=hearing_id,
                upload_id=upload_id,
                passthrough=marker,
                idempotency_key=idempotency_key,
            ),
        )
    except MuxError:
        raise
    except Exception:
        # Keep the committed reservation/quota charge on unknown outcomes.
        raise MuxError("upload_pending_or_uncertain", 409) from None
    await _analytics(
        "tank.media.upload_created",
        actor,
        properties={"outcome": "ok", "playback_policy": PLAYBACK_POLICY, "test": test},
        environ=environ,
    )
    await _hearing_access(actor, hearing_id, hearing_allowed)
    return public_upload_view(created)


async def upload_status(
    *,
    actor: MootActor,
    hearing_id: str,
    upload_id: str,
    provider: MuxProvider,
    store: AsyncOwnershipStore,
    hearing_allowed: HearingGate,
) -> dict[str, Any]:
    await _hearing_access(actor, hearing_id, hearing_allowed)
    record = await _owned(
        await store.by_upload(upload_id, tenant_id=actor.tenant_id), actor, hearing_id
    )
    data = await provider.retrieve_upload(upload_id)
    asset_id = data.get("asset_id")
    if isinstance(asset_id, str) and asset_id:
        asset = await provider.retrieve_asset(asset_id)
        playback_ids = [
            str(row["id"])
            for row in (asset.get("playback_ids") or [])
            if isinstance(row, dict) and row.get("id")
        ]
        await store.bind_asset(
            record.upload_id, asset_id, playback_ids, tenant_id=actor.tenant_id
        )
    await _hearing_access(actor, hearing_id, hearing_allowed)
    return public_upload_view(data)


async def asset_status(
    *,
    actor: MootActor,
    hearing_id: str,
    asset_id: str,
    provider: MuxProvider,
    store: AsyncOwnershipStore,
    hearing_allowed: HearingGate,
) -> dict[str, Any]:
    await _hearing_access(actor, hearing_id, hearing_allowed)
    await _owned(
        await store.by_asset(asset_id, tenant_id=actor.tenant_id), actor, hearing_id
    )
    data = await provider.retrieve_asset(asset_id)
    await _hearing_access(actor, hearing_id, hearing_allowed)
    return public_asset_view(data)


async def playback_access(
    *,
    actor: MootActor,
    hearing_id: str,
    playback_id: str,
    store: AsyncOwnershipStore,
    hearing_allowed: HearingGate,
    environ: dict | None = None,
    ttl_s: int | None = None,
) -> dict[str, Any]:
    await _hearing_access(actor, hearing_id, hearing_allowed)
    await _owned(
        await store.by_playback(playback_id, tenant_id=actor.tenant_id),
        actor,
        hearing_id,
    )
    require_signed_playback_ready(environ)
    token = mint_playback_jwt(playback_id, environ=environ, ttl_s=ttl_s or 3600)
    await _analytics(
        "tank.media.playback_issued",
        actor,
        properties={"outcome": "ok", "playback_policy": PLAYBACK_POLICY},
        environ=environ,
    )
    await _hearing_access(actor, hearing_id, hearing_allowed)
    return {
        "playback_id": playback_id,
        "playback_policy": PLAYBACK_POLICY,
        "token": token,
        "playback_url": signed_playback_url(playback_id, token),
        "expires_in": ttl_s or 3600,
    }


def error_payload(exc: MuxError) -> dict[str, Any]:
    body: dict[str, Any] = {"error": exc.code}
    if isinstance(exc, SignedPlaybackUnconfigured):
        body["required_secrets"] = list(exc.required_names)
        body["playback_policy"] = PLAYBACK_POLICY
    if isinstance(exc, MuxNotConfigured):
        body["required_secrets"] = ["MOOT_MUX_TOKEN_ID", "MOOT_MUX_TOKEN_SECRET"]
    # Never attach secret values or provider diagnostics.
    return body


async def deny_missing_hearing_gate(
    tenant_id: str, user_id: str, hearing_id: str
) -> bool:
    return False


async def handle_media_request(
    *,
    method: str,
    path: str,
    actor: MootActor,
    body: dict[str, Any] | None,
    provider: MuxProvider | None = None,
    store: AsyncOwnershipStore | None = None,
    hearing_allowed: HearingGate | None = None,
    environ: dict | None = None,
    allow_test_uploads: bool = False,
    idempotency_key: str | None = None,
) -> tuple[int, dict[str, Any]]:
    matched = match_media_path(path)
    if matched is None:
        return 404, {"error": "not_found"}
    hearing_id = matched["hearing_id"]
    kind = matched["kind"]
    resource_id = matched["resource_id"]
    store = store or DEFAULT_STORE
    hearing_allowed = hearing_allowed or deny_missing_hearing_gate
    try:
        if provider is None:
            provider = provider_from_env(environ)
        if method == "POST" and kind == "uploads" and not resource_id:
            cors = ""
            test = False
            if isinstance(body, dict):
                # Caller body cannot choose another user/hearing; only cors_origin (+ optional test).
                cors = str(body.get("cors_origin") or "").strip()
                test = bool(body.get("test")) and allow_test_uploads
                if any(k not in {"cors_origin", "home_region", "test"} for k in body):
                    raise MuxError("invalid_request", 422)
            result = await create_upload(
                actor=actor,
                hearing_id=hearing_id,
                cors_origin=cors,
                provider=provider,
                store=store,
                hearing_allowed=hearing_allowed,
                test=test,
                idempotency_key=idempotency_key,
                environ=environ,
            )
            return 201, result
        if method == "GET" and kind == "uploads" and resource_id:
            return 200, await upload_status(
                actor=actor,
                hearing_id=hearing_id,
                upload_id=resource_id,
                provider=provider,
                store=store,
                hearing_allowed=hearing_allowed,
            )
        if method == "GET" and kind == "assets" and resource_id:
            return 200, await asset_status(
                actor=actor,
                hearing_id=hearing_id,
                asset_id=resource_id,
                provider=provider,
                store=store,
                hearing_allowed=hearing_allowed,
            )
        if method == "GET" and kind == "playback" and resource_id:
            return 200, await playback_access(
                actor=actor,
                hearing_id=hearing_id,
                playback_id=resource_id,
                store=store,
                environ=environ,
                hearing_allowed=hearing_allowed,
            )
        return 404, {"error": "not_found"}
    except MuxError as exc:
        return exc.status, error_payload(exc)
