"""Tank-only PostHog capture door. Credentials never leave Tank / Coolify.

Fail closed when POSTHOG_PROJECT_API_KEY is absent: capture is a no-op and
status reports unconfigured. Events carry allowlisted properties only — no
emails, names, raw tokens, or request bodies. Distinct IDs are opaque hashes
of already-verified tenant/user ids, never raw identity strings.
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

API_KEY_NAME = "POSTHOG_PROJECT_API_KEY"
HOST_NAME = "POSTHOG_HOST"
DEFAULT_HOST = "https://us.i.posthog.com"
# Optional: personal API key is NOT used for capture; never required for live product events.
PERSONAL_KEY_NAME = "POSTHOG_PERSONAL_API_KEY"  # documented; unused by this door

ALLOWED_EVENT_NAMES = frozenset({
    "tank.media.upload_created",
    "tank.media.playback_issued",
    "tank.media.upload_denied",
    "tank.health.posthog_probe",
})

ALLOWED_PROP_KEYS = frozenset({
    "product",
    "region",
    "outcome",
    "playback_policy",
    "error_code",
    "test",
})


class PosthogError(Exception):
    def __init__(self, code: str, status: int = 502):
        self.code = code
        self.status = status
        super().__init__(code)


class PosthogNotConfigured(PosthogError):
    def __init__(self):
        super().__init__("posthog_credentials_missing", 503)
        self.required_names = (API_KEY_NAME,)


def credentials_present(environ: dict[str, str] | os._Environ | None = None) -> bool:
    env = os.environ if environ is None else environ
    return bool(str(env.get(API_KEY_NAME, "")).strip())


def host_from_env(environ: dict[str, str] | os._Environ | None = None) -> str:
    env = os.environ if environ is None else environ
    raw = str(env.get(HOST_NAME, "")).strip() or DEFAULT_HOST
    parsed = urlparse(raw)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
        raise PosthogError("invalid_posthog_host", 422)
    # Prefer ingestion hosts; map app hosts if founder pasted dashboard URL.
    host = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    if host in {"https://us.posthog.com", "https://app.posthog.com"}:
        return "https://us.i.posthog.com"
    if host == "https://eu.posthog.com":
        return "https://eu.i.posthog.com"
    return host


def opaque_distinct_id(tenant_id: str, user_id: str) -> str:
    """Stable opaque id; never the raw user/tenant string."""
    material = f"tank:{tenant_id}:{user_id}".encode()
    return "ph_" + hashlib.sha256(material).hexdigest()[:32]


def sanitize_properties(props: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {"product": "tank"}
    if not props:
        return out
    for key, value in props.items():
        if key not in ALLOWED_PROP_KEYS or key == "product":
            continue
        if isinstance(value, bool) or value is None:
            out[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = value
        elif isinstance(value, str) and len(value) <= 80 and "\n" not in value:
            # Reject anything that looks like an email or JWT.
            if "@" in value or value.count(".") >= 2 and len(value) > 40:
                continue
            out[key] = value
    return out


class PosthogProvider(Protocol):
    async def capture(
        self, *, event: str, distinct_id: str, properties: dict[str, Any]
    ) -> None: ...


class NullPosthogProvider:
    async def capture(self, *, event: str, distinct_id: str, properties: dict[str, Any]) -> None:
        return None


class HttpPosthogProvider:
    def __init__(self, api_key: str, host: str, client: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.host = host.rstrip("/")
        self.client = client

    async def capture(self, *, event: str, distinct_id: str, properties: dict[str, Any]) -> None:
        body = {
            "api_key": self.api_key,
            "event": event,
            "distinct_id": distinct_id,
            "properties": {**properties, "$lib": "tank-posthog-door"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        owns = self.client is None
        client = self.client or httpx.AsyncClient(timeout=5.0)
        try:
            response = await client.post(f"{self.host}/i/v0/e/", json=body)
        finally:
            if owns:
                await client.aclose()
        if response.status_code >= 400:
            raise PosthogError("posthog_upstream_error", 502)


class FakePosthogProvider:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def capture(self, *, event: str, distinct_id: str, properties: dict[str, Any]) -> None:
        self.events.append({"event": event, "distinct_id": distinct_id, "properties": dict(properties)})


def provider_from_env(
    environ: dict[str, str] | os._Environ | None = None,
    client: httpx.AsyncClient | None = None,
) -> PosthogProvider:
    env = os.environ if environ is None else environ
    if not credentials_present(env):
        return NullPosthogProvider()
    key = str(env.get(API_KEY_NAME, "")).strip()
    return HttpPosthogProvider(api_key=key, host=host_from_env(env), client=client)


async def capture_event(
    *,
    event: str,
    tenant_id: str,
    user_id: str,
    properties: dict[str, Any] | None = None,
    provider: PosthogProvider | None = None,
    environ: dict | None = None,
    require_configured: bool = False,
) -> dict[str, Any]:
    if event not in ALLOWED_EVENT_NAMES:
        raise PosthogError("event_not_allowed", 422)
    if require_configured and not credentials_present(environ):
        raise PosthogNotConfigured()
    client = provider if provider is not None else provider_from_env(environ)
    props = sanitize_properties(properties)
    distinct = opaque_distinct_id(tenant_id, user_id)
    await client.capture(event=event, distinct_id=distinct, properties=props)
    return {"ok": True, "event": event, "configured": credentials_present(environ) or provider is not None}


def status_payload(environ: dict[str, str] | os._Environ | None = None) -> dict[str, Any]:
    configured = credentials_present(environ)
    body: dict[str, Any] = {
        "configured": configured,
        "required_secrets": [API_KEY_NAME] if not configured else [],
        "optional_secrets": [HOST_NAME],
        "default_host": DEFAULT_HOST,
        "allowed_events": sorted(ALLOWED_EVENT_NAMES),
    }
    if configured:
        try:
            body["host"] = host_from_env(environ)
        except PosthogError as exc:
            body["host_error"] = exc.code
    return body
