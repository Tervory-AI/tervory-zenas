"""Encrypt one identifier with AES-256-GCM before it is stored.

ZENAS_PII_KEY is exactly 32 bytes, hex. Each seal uses a fresh 12-byte nonce.
The stored value is base64(nonce + ciphertext). The ciphertext includes the
GCM tag, so a flipped byte or a wrong key fails closed.
"""

from __future__ import annotations

import base64
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LEN = 12
_KEY_LEN = 32


def key_bytes(value: str | None) -> bytes | None:
    if not value or not value.strip():
        return None
    raw = value.strip()
    if len(raw) != _KEY_LEN * 2:
        return None
    try:
        key = bytes.fromhex(raw)
    except ValueError:
        return None
    if len(key) != _KEY_LEN:
        return None
    return key


def key_from_env(env: dict[str, str] | None = None) -> bytes | None:
    source = env if env is not None else os.environ
    return key_bytes(source.get("ZENAS_PII_KEY"))


def seal(plaintext: str, key: bytes) -> str:
    if len(key) != _KEY_LEN:
        raise ValueError("pii key must be 32 bytes")
    nonce = secrets.token_bytes(_NONCE_LEN)
    body = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + body).decode("ascii")


def open_seal(token: str, key: bytes) -> str:
    if len(key) != _KEY_LEN:
        raise ValueError("pii key must be 32 bytes")
    try:
        blob = base64.b64decode(token, validate=True)
    except Exception as exc:
        raise ValueError("pii seal failed") from exc
    if len(blob) <= _NONCE_LEN:
        raise ValueError("pii seal failed")
    nonce, body = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
    try:
        raw = AESGCM(key).decrypt(nonce, body, None)
    except InvalidTag as exc:
        raise ValueError("pii seal failed") from exc
    return raw.decode("utf-8")
