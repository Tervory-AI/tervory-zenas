"""Reuse Tank's lifecycle request-signing (ghost_routes / enforce_api).

Tank signs: timestamp \\n method \\n path \\n tenant \\n user \\n + raw body
with the room key (HMAC-SHA256 hex). Headers:
  X-Lifecycle-Timestamp, X-Lifecycle-Signature,
  X-Tervory-Tenant, X-Tervory-User

The tenant and user in the signed message must be the subject Tank already
verified via Trigger sign-in (command.identity / signed_in_actor). Plain
X-Tervory-* headers or body ids are never identity on their own.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from uuid import UUID

# Same window as agents/ghost/enforce_api.py:verify_lifecycle
FRESH_SECONDS = 60


def room_key() -> str:
    return (os.environ.get("TANK_KEY") or "").strip()


def signing_message(
    *,
    timestamp: str,
    method: str,
    path: str,
    tenant_id: str,
    user_id: str,
    body: bytes,
) -> bytes:
    return (
        f"{timestamp}\n{method}\n{path}\n{tenant_id}\n{user_id}\n".encode()
        + body
    )


def sign_request(
    *,
    key: str,
    method: str,
    path: str,
    tenant_id: str,
    user_id: str,
    body: bytes = b"",
    timestamp: str | None = None,
) -> dict[str, str]:
    """Build lifecycle-style headers for a Tank→Trigger call. key is the room key."""
    if not key:
        raise ValueError("room key required to sign")
    ts = timestamp if timestamp is not None else str(int(time.time()))
    tenant = str(UUID(str(tenant_id)))
    user = str(user_id).strip()
    if not user:
        raise ValueError("user_id required to sign")
    message = signing_message(
        timestamp=ts, method=method.upper(), path=path,
        tenant_id=tenant, user_id=user, body=body,
    )
    signature = hmac.new(key.encode(), message, hashlib.sha256).hexdigest()
    return {
        "X-Tank-Key": key,
        "X-Lifecycle-Timestamp": ts,
        "X-Lifecycle-Signature": signature,
        "X-Tervory-Tenant": tenant,
        "X-Tervory-User": user,
    }


def verify_signed_actor(
    *,
    key: str,
    method: str,
    path: str,
    timestamp: str,
    signature: str,
    tenant_header: str,
    user_header: str,
    body: bytes,
    now: float | None = None,
) -> tuple[UUID, str]:
    """Return (tenant_id, user_id) only when the lifecycle signature verifies."""
    if not key or not timestamp or not signature:
        raise PermissionError("signed identity required")
    try:
        fresh = abs((now if now is not None else time.time()) - int(timestamp)) <= FRESH_SECONDS
    except ValueError as exc:
        raise PermissionError("signed identity required") from exc
    if not fresh:
        raise PermissionError("signed identity required")
    user = (user_header or "").strip()
    if not user:
        raise PermissionError("signed identity required")
    try:
        tenant = UUID((tenant_header or "").strip())
    except ValueError as exc:
        raise PermissionError("signed identity required") from exc
    message = signing_message(
        timestamp=timestamp, method=method.upper(), path=path,
        tenant_id=str(tenant), user_id=user, body=body,
    )
    expected = hmac.new(key.encode(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode(), signature.encode()):
        raise PermissionError("signed identity required")
    return tenant, user
