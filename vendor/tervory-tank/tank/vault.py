"""Authenticated encryption for ACCESS-1. Plaintext exists only during use.

Scopes are authenticated additional data: moving ciphertext to another tenant,
room, site or purpose cannot make it decrypt. Never log exceptions or payloads.
"""
from __future__ import annotations

import base64
import json
import os
import time
import uuid

from fastapi import HTTPException


def key(name: str = "TANK_VAULT_KEY") -> bytes:
    try:
        value = base64.urlsafe_b64decode(os.environ.get(name, ""))
        if len(value) != 32:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise HTTPException(503, "Secure connections are not configured.") from None


def seal(value: dict, context: str, *, key_name: str = "TANK_VAULT_KEY") -> str:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise HTTPException(503, "Secure connections are not installed on this server.") from None
    nonce = os.urandom(12)
    data = json.dumps(value, separators=(",", ":")).encode()
    encrypted = AESGCM(key(key_name)).encrypt(nonce, data, context.encode())
    return base64.urlsafe_b64encode(nonce + encrypted).decode()


def open_seal(value: str, context: str, *, key_name: str = "TANK_VAULT_KEY") -> dict:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise HTTPException(503, "Secure connections are not installed on this server.") from None
    try:
        raw = base64.urlsafe_b64decode(value)
        data = AESGCM(key(key_name)).decrypt(raw[:12], raw[12:], context.encode())
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(403, "Secure connection refused.") from None


def context(tenant: uuid.UUID, room: str, site: str, purpose: str) -> str:
    return f"access:v1:{tenant}:{room}:{site}:{purpose}"


def browser_key(room: str) -> str:
    if room not in {"base", "tiger"}:
        raise HTTPException(404, "Room not found.")
    return f"ACCESS_BROWSER_KEY_{room.upper()}"


def envelope(room: str, direction: str, data: dict) -> dict:
    return {"sealed": seal({**data, "at": int(time.time()), "nonce": str(uuid.uuid4())},
                            f"access:browser:{room}:{direction}", key_name=browser_key(room))}


def open_envelope(room: str, direction: str, body: dict) -> dict:
    data = open_seal(body.get("sealed", ""), f"access:browser:{room}:{direction}", key_name=browser_key(room))
    try:
        uuid.UUID(data["nonce"])
        if abs(time.time() - data["at"]) > 60:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise HTTPException(403, "Secure request expired.") from None
    return data
