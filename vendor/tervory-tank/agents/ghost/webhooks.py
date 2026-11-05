"""Signed inbound webhooks only. Unsigned/replayed dropped and counted, never stored.

HMAC covers timestamp + nonce + body. Timestamp window + nonce refuse replay.
Founder correction: no Cloudflare-named secrets yet. Env names are agnostic.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from typing import Any


class WebhookRejected(ValueError):
    """Unsigned, badly signed, or replayed — drop, count, never store the body."""


EDGE_WEBHOOK_SECRET_ENV = "AUTUMBRA_EDGE_WEBHOOK_SECRET"
MAILBOX_WEBHOOK_SECRET_ENV = "AUTUMBRA_MAILBOX_WEBHOOK_SECRET"
CF_SECRET_ENV = EDGE_WEBHOOK_SECRET_ENV
GOOGLE_SECRET_ENV = MAILBOX_WEBHOOK_SECRET_ENV

_drops: dict[str, int] = {
    "edge": 0,
    "mailbox": 0,
    "cloudflare": 0,
    "google": 0,
    "unknown": 0,
    "replay": 0,
}
_nonce_lock = threading.Lock()
_seen_nonces: dict[str, float] = {}
_NONCE_TTL_SEC = 600


def drop_counts() -> dict[str, int]:
    return dict(_drops)


def reset_drop_counts() -> None:
    for key in _drops:
        _drops[key] = 0
    with _nonce_lock:
        _seen_nonces.clear()


def _secret(*env_names: str) -> str:
    for name in env_names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _prune_nonces(now: float) -> None:
    dead = [n for n, exp in _seen_nonces.items() if exp < now]
    for n in dead:
        _seen_nonces.pop(n, None)


def _signing_base(timestamp: str, nonce: str, body: bytes) -> bytes:
    return f"{timestamp}.{nonce}.".encode("utf-8") + body


def sign_body(*, secret: str, body: bytes, timestamp: str, nonce: str) -> str:
    """Helper for tests / senders: HMAC-SHA256 over timestamp.nonce.body."""
    digest = hmac.new(
        secret.encode("utf-8"),
        _signing_base(timestamp, nonce, body),
        hashlib.sha256,
    ).hexdigest()
    return digest


def _verify(
    body: bytes,
    *,
    secret: str,
    signature: str | None,
    timestamp: str | None,
    nonce: str | None,
    bucket: str,
    max_skew_seconds: int = 300,
) -> None:
    if not secret:
        _drops[bucket] += 1
        raise WebhookRejected(f"{bucket} webhook secret is not configured.")
    if not signature:
        _drops[bucket] += 1
        raise WebhookRejected("missing signature")
    if not timestamp:
        _drops[bucket] += 1
        raise WebhookRejected("missing timestamp")
    if not nonce or not str(nonce).strip():
        _drops[bucket] += 1
        raise WebhookRejected("missing nonce")
    try:
        ts = int(timestamp)
    except (TypeError, ValueError) as exc:
        _drops[bucket] += 1
        raise WebhookRejected("bad timestamp") from exc
    now = int(time.time())
    if abs(now - ts) > max_skew_seconds:
        _drops[bucket] += 1
        raise WebhookRejected("stale timestamp")
    nonce_key = f"{bucket}:{nonce.strip()}"
    with _nonce_lock:
        _prune_nonces(float(now))
        if nonce_key in _seen_nonces:
            _drops[bucket] += 1
            _drops["replay"] += 1
            raise WebhookRejected("replayed nonce")
    expected = sign_body(secret=secret, body=body, timestamp=str(ts), nonce=nonce.strip())
    provided = signature.removeprefix("sha256=").strip()
    if not hmac.compare_digest(expected, provided):
        _drops[bucket] += 1
        raise WebhookRejected("bad signature")
    with _nonce_lock:
        _seen_nonces[nonce_key] = float(now) + _NONCE_TTL_SEC


def verify_edge(
    body: bytes,
    *,
    signature: str | None,
    timestamp: str | None = None,
    nonce: str | None = None,
    max_skew_seconds: int = 300,
) -> None:
    secret = _secret(EDGE_WEBHOOK_SECRET_ENV)
    try:
        _verify(
            body,
            secret=secret,
            signature=signature,
            timestamp=timestamp,
            nonce=nonce,
            bucket="edge",
            max_skew_seconds=max_skew_seconds,
        )
    finally:
        _drops["cloudflare"] = _drops["edge"]


def verify_mailbox(
    body: bytes,
    *,
    signature: str | None,
    timestamp: str | None = None,
    nonce: str | None = None,
    max_skew_seconds: int = 300,
) -> None:
    secret = _secret(MAILBOX_WEBHOOK_SECRET_ENV)
    try:
        _verify(
            body,
            secret=secret,
            signature=signature,
            timestamp=timestamp,
            nonce=nonce,
            bucket="mailbox",
            max_skew_seconds=max_skew_seconds,
        )
    finally:
        _drops["google"] = _drops["mailbox"]




def accept_signed(
    provider: str,
    body: bytes,
    *,
    signature: str | None,
    timestamp: str | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    """Verify then return a tiny envelope. Body is not persisted by this helper."""
    key = str(provider or "").strip().lower()
    if key in {"edge", "host_firewall"}:
        verify_edge(body, signature=signature, timestamp=timestamp, nonce=nonce)
        named = "edge"
    elif key == "mailbox":
        verify_mailbox(body, signature=signature, timestamp=timestamp, nonce=nonce)
        named = "mailbox"
    else:
        _drops["unknown"] += 1
        raise WebhookRejected("unknown provider")
    return {
        "provider": named,
        "requested_provider": key,
        "bytes": len(body),
        "accepted": True,
        "stored": False,
        "note": "Verified at the Watch edge. Body stays in working memory only.",
    }


async def accept_webhook(provider: str, body: bytes, *, headers, tank) -> dict[str, Any]:
    """Provider URLs accept only provider authentication; HMAC is internal only."""
    signature = headers.get('x-autumbra-signature')
    if provider.strip().lower() in {'edge', 'mailbox', 'host_firewall'}:
        envelope = accept_signed(provider, body, signature=signature,
                                 timestamp=headers.get('x-autumbra-timestamp'),
                                 nonce=headers.get('x-autumbra-nonce'))
        return {**envelope, 'authenticated_by': 'hmac'}
    from .native_webhooks import verify_cloudflare_notification, verify_google_push
    key = provider.strip().lower()
    bucket = 'edge' if key == 'cloudflare' else 'mailbox' if key in {'google', 'google_workspace'} else 'unknown'
    try:
        if key == 'cloudflare':
            verify_cloudflare_notification(body, headers.get('cf-webhook-auth'))
        elif key in {'google', 'google_workspace'}:
            await verify_google_push(body, headers.get('authorization'), tank)
        else:
            raise WebhookRejected('unknown provider')
    except WebhookRejected:
        _drops[bucket] += 1
        _drops['cloudflare'] = _drops['edge']
        _drops['google'] = _drops['mailbox']
        raise
    return {'provider': bucket, 'requested_provider': key, 'bytes': len(body),
            'accepted': True, 'stored': False, 'authenticated_by': 'native'}
