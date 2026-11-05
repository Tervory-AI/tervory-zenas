"""Signed, expiring Autumbra tokens for owner email links.

Undo tokens name tenant_id + shield (rule_id) + receipt_id.
Receipts tokens name tenant_id only (weekly receipt page / API).

Both require AUTUMBRA_UNDO_TOKEN_SECRET — no fallback to any other secret.
Unset secret → fail closed (sign/verify raise).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any


DEFAULT_TTL_SEC = 7 * 24 * 3600


def _secret() -> bytes:
    raw = (os.environ.get("AUTUMBRA_UNDO_TOKEN_SECRET") or "").strip()
    if not raw:
        raise RuntimeError("AUTUMBRA_UNDO_TOKEN_SECRET is not configured.")
    return raw.encode("utf-8")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign_payload(payload: dict[str, Any]) -> str:
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64url(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def _verify_raw(token: str, *, now: float | None = None) -> dict[str, Any]:
    raw = (token or "").strip()
    if not raw or "." not in raw:
        raise ValueError("token missing")
    body, _, sig = raw.partition(".")
    if not body or not sig:
        raise ValueError("token malformed")
    expected = _b64url(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise ValueError("token signature invalid")
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception as exc:
        raise ValueError("token payload invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("token payload invalid")
    exp = int(payload.get("exp") or 0)
    if exp < int(now if now is not None else time.time()):
        raise ValueError("token expired")
    return payload


def sign_undo_token(
    *,
    tenant_id: str,
    receipt_id: str,
    rule_id: str = "",
    site: str = "",
    ttl_sec: int = DEFAULT_TTL_SEC,
    now: float | None = None,
) -> str:
    """Return a compact signed token naming tenant + shield (+ receipt)."""
    tid = (tenant_id or "").strip()
    rid = (receipt_id or "").strip()
    if not tid or not rid:
        raise ValueError("undo token requires tenant_id and receipt_id")
    payload = {
        "purpose": "undo",
        "tenant_id": tid,
        "receipt_id": rid,
        "rule_id": (rule_id or "").strip(),
        "site": (site or "").strip(),
        "exp": int((now if now is not None else time.time()) + max(60, int(ttl_sec))),
    }
    return _sign_payload(payload)


def verify_undo_token(token: str, *, now: float | None = None) -> dict[str, Any]:
    """Verify signature + expiry. Returns undo payload or raises ValueError."""
    payload = _verify_raw(token, now=now)
    purpose = str(payload.get("purpose") or "undo").strip()
    if purpose != "undo":
        raise ValueError("undo token purpose invalid")
    tenant_id = str(payload.get("tenant_id") or "").strip()
    receipt_id = str(payload.get("receipt_id") or "").strip()
    if not tenant_id or not receipt_id:
        raise ValueError("undo token missing tenant or receipt")
    return {
        "tenant_id": tenant_id,
        "receipt_id": receipt_id,
        "rule_id": str(payload.get("rule_id") or "").strip(),
        "site": str(payload.get("site") or "").strip(),
        "exp": int(payload.get("exp") or 0),
    }


def sign_receipts_token(
    *,
    tenant_id: str,
    ttl_sec: int = DEFAULT_TTL_SEC,
    now: float | None = None,
) -> str:
    """Signed token authorizing weekly receipts for exactly this tenant."""
    tid = (tenant_id or "").strip()
    if not tid:
        raise ValueError("receipts token requires tenant_id")
    payload = {
        "purpose": "receipts",
        "tenant_id": tid,
        "exp": int((now if now is not None else time.time()) + max(60, int(ttl_sec))),
    }
    return _sign_payload(payload)


def verify_receipts_token(token: str, *, now: float | None = None) -> dict[str, Any]:
    """Verify a receipts access token. Returns {tenant_id, exp} or raises ValueError."""
    payload = _verify_raw(token, now=now)
    if str(payload.get("purpose") or "").strip() != "receipts":
        raise ValueError("receipts token purpose invalid")
    tenant_id = str(payload.get("tenant_id") or "").strip()
    if not tenant_id:
        raise ValueError("receipts token missing tenant")
    return {"tenant_id": tenant_id, "exp": int(payload.get("exp") or 0)}
