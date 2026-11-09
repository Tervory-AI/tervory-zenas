"""Mux Video door for confidential Moot media. Credentials stay on Tank only.

Playback is signed-only. Playable stream URLs require MOOT_MUX_SIGNING_KEY_ID and
MOOT_MUX_SIGNING_PRIVATE_KEY (owner-approved secrets; not created by this module).
"""
from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import jwt

TOKEN_ID_NAME = "MOOT_MUX_TOKEN_ID"
TOKEN_SECRET_NAME = "MOOT_MUX_TOKEN_SECRET"
SIGNING_KEY_ID_NAME = "MOOT_MUX_SIGNING_KEY_ID"
SIGNING_PRIVATE_KEY_NAME = "MOOT_MUX_SIGNING_PRIVATE_KEY"
WEBHOOK_SECRET_NAME = "MOOT_MUX_WEBHOOK_SECRET"  # documented only; unused here

MUX_API = "https://api.mux.com"
PLAYBACK_POLICY = "signed"
DEFAULT_PLAYBACK_TTL_S = 3600
# Browser origins allowed to receive Mux direct-upload CORS headers.
ALLOWED_CORS_ORIGINS = frozenset({
    "https://zenas.tervory.com",
    "https://app.tervory.com",
})


class MuxError(Exception):
    def __init__(self, code: str, status: int = 502):
        self.code = code
        self.status = status
        super().__init__(code)


class MuxNotConfigured(MuxError):
    def __init__(self):
        super().__init__("mux_credentials_missing", 503)


class SignedPlaybackUnconfigured(MuxError):
    def __init__(self):
        super().__init__("signed_playback_unconfigured", 503)
        self.required_names = (SIGNING_KEY_ID_NAME, SIGNING_PRIVATE_KEY_NAME)


def credentials_present(environ: dict[str, str] | os._Environ | None = None) -> bool:
    env = os.environ if environ is None else environ
    return bool(str(env.get(TOKEN_ID_NAME, "")).strip() and str(env.get(TOKEN_SECRET_NAME, "")).strip())


def credentials_from_env(environ: dict[str, str] | os._Environ | None = None) -> tuple[str, str]:
    env = os.environ if environ is None else environ
    token_id = str(env.get(TOKEN_ID_NAME, "")).strip()
    token_secret = str(env.get(TOKEN_SECRET_NAME, "")).strip()
    if not token_id or not token_secret:
        raise MuxNotConfigured()
    return token_id, token_secret


def signing_configured(environ: dict[str, str] | os._Environ | None = None) -> bool:
    env = os.environ if environ is None else environ
    return bool(
        str(env.get(SIGNING_KEY_ID_NAME, "")).strip()
        and str(env.get(SIGNING_PRIVATE_KEY_NAME, "")).strip()
    )


def require_signed_playback_ready(environ: dict[str, str] | os._Environ | None = None) -> None:
    if not signing_configured(environ):
        raise SignedPlaybackUnconfigured()


def normalize_cors_origin(raw: str) -> str:
    value = (raw or "").strip().rstrip("/")
    if not value or len(value) > 200:
        raise MuxError("invalid_cors_origin", 422)
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
        raise MuxError("invalid_cors_origin", 422)
    # Production allowlist is https product hosts only; http only for explicit local fixtures.
    origin = f"{parsed.scheme}://{parsed.netloc}".lower()
    if origin not in ALLOWED_CORS_ORIGINS and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    ):
        raise MuxError("cors_origin_forbidden", 403)
    return origin


def mint_playback_jwt(
    playback_id: str,
    *,
    environ: dict[str, str] | os._Environ | None = None,
    ttl_s: int = DEFAULT_PLAYBACK_TTL_S,
    now: int | None = None,
) -> str:
    """Create a Mux playback JWT (RS256). Never logs key material."""
    require_signed_playback_ready(environ)
    env = os.environ if environ is None else environ
    if not playback_id or len(playback_id) > 128 or any(c.isspace() for c in playback_id):
        raise MuxError("invalid_playback_id", 422)
    if ttl_s < 60 or ttl_s > 86400:
        raise MuxError("invalid_ttl", 422)
    key_id = str(env.get(SIGNING_KEY_ID_NAME, "")).strip()
    private_b64 = str(env.get(SIGNING_PRIVATE_KEY_NAME, "")).strip()
    try:
        private_key = base64.b64decode(private_b64, validate=True)
    except Exception as exc:
        raise SignedPlaybackUnconfigured() from exc
    if not private_key:
        raise SignedPlaybackUnconfigured()
    issued = int(time.time() if now is None else now)
    try:
        token = jwt.encode(
            {"sub": playback_id, "aud": "v", "exp": issued + ttl_s},
            private_key,
            algorithm="RS256",
            headers={"kid": key_id},
        )
    except Exception as exc:
        # Bad key material must fail closed without leaking details.
        raise SignedPlaybackUnconfigured() from exc
    return token if isinstance(token, str) else token.decode("ascii")


def signed_playback_url(playback_id: str, token: str) -> str:
    return f"https://stream.mux.com/{playback_id}.m3u8?token={token}"


class MuxProvider(Protocol):
    async def create_direct_upload(
        self, *, cors_origin: str, passthrough: str, test: bool = False
    ) -> dict[str, Any]: ...

    async def retrieve_upload(self, upload_id: str) -> dict[str, Any]: ...

    async def retrieve_asset(self, asset_id: str) -> dict[str, Any]: ...


@dataclass
class HttpMuxProvider:
    token_id: str
    token_secret: str
    client: httpx.AsyncClient | None = None

    def _auth_header(self) -> str:
        raw = f"{self.token_id}:{self.token_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    async def _request(self, method: str, path: str, json_body: dict | None = None) -> dict[str, Any]:
        headers = {"Authorization": self._auth_header(), "Accept": "application/json"}
        owns = self.client is None
        client = self.client or httpx.AsyncClient(timeout=30.0)
        try:
            response = await client.request(
                method, f"{MUX_API}{path}", headers=headers, json=json_body
            )
        finally:
            if owns:
                await client.aclose()
        if response.status_code >= 400:
            raise MuxError("mux_upstream_error", 502)
        payload = response.json()
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            raise MuxError("mux_upstream_error", 502)
        return data

    async def create_direct_upload(
        self, *, cors_origin: str, passthrough: str, test: bool = False
    ) -> dict[str, Any]:
        if len(passthrough) > 255:
            raise MuxError("passthrough_too_long", 422)
        body: dict[str, Any] = {
            "cors_origin": cors_origin,
            "new_asset_settings": {
                "playback_policies": [PLAYBACK_POLICY],
                "passthrough": passthrough,
            },
        }
        if test:
            body["test"] = True
            body["new_asset_settings"]["test"] = True
        return await self._request("POST", "/video/v1/uploads", body)

    async def retrieve_upload(self, upload_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/video/v1/uploads/{upload_id}")

    async def retrieve_asset(self, asset_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/video/v1/assets/{asset_id}")


class FakeMuxProvider:
    def __init__(self) -> None:
        self.uploads: dict[str, dict[str, Any]] = {}
        self.assets: dict[str, dict[str, Any]] = {}
        self.create_calls = 0

    async def create_direct_upload(
        self, *, cors_origin: str, passthrough: str, test: bool = False
    ) -> dict[str, Any]:
        self.create_calls += 1
        upload_id = "upload_" + uuid4().hex[:16]
        asset_id = "asset_" + uuid4().hex[:16]
        playback_id = "playback_" + uuid4().hex[:16]
        row = {
            "id": upload_id,
            "cors_origin": cors_origin,
            "status": "waiting",
            "timeout": 3600,
            "url": f"https://storage.example.test/mux-upload/{upload_id}",
            "new_asset_settings": {
                "playback_policies": [PLAYBACK_POLICY],
                "passthrough": passthrough,
                "test": test,
            },
            "_asset_id": asset_id,
            "_playback_id": playback_id,
        }
        self.uploads[upload_id] = row
        self.assets[asset_id] = {
            "id": asset_id,
            "status": "preparing",
            "passthrough": passthrough,
            "playback_ids": [{"id": playback_id, "policy": PLAYBACK_POLICY}],
            "upload_id": upload_id,
            "test": test,
        }
        return {k: v for k, v in row.items() if not k.startswith("_")}

    async def retrieve_upload(self, upload_id: str) -> dict[str, Any]:
        row = self.uploads.get(upload_id)
        if row is None:
            raise MuxError("not_found", 404)
        out = {k: v for k, v in row.items() if not k.startswith("_")}
        if row["status"] == "asset_created":
            out["asset_id"] = row["_asset_id"]
        return out

    async def retrieve_asset(self, asset_id: str) -> dict[str, Any]:
        row = self.assets.get(asset_id)
        if row is None:
            raise MuxError("not_found", 404)
        return dict(row)

    def complete_upload(self, upload_id: str) -> None:
        row = self.uploads[upload_id]
        row["status"] = "asset_created"
        row["asset_id"] = row["_asset_id"]
        self.assets[row["_asset_id"]]["status"] = "ready"


def provider_from_env(
    environ: dict[str, str] | os._Environ | None = None,
    client: httpx.AsyncClient | None = None,
) -> HttpMuxProvider:
    token_id, token_secret = credentials_from_env(environ)
    return HttpMuxProvider(token_id=token_id, token_secret=token_secret, client=client)
