"""Room boundaries and public-web URL validation, without provider credentials."""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import os
import socket
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException


def authenticate(key: str) -> None:
    expected = os.environ.get("TANK_KEY", "")
    if not expected or not key or not hmac.compare_digest(key.encode(), expected.encode()):
        raise HTTPException(401, "Base room key required.")


def scope(tenant: str, region: str) -> str:
    if region != "us-east" or os.environ.get("TANK_REGION", "us-east") != region:
        raise HTTPException(403, "This task belongs to another region.")
    try:
        return str(uuid.UUID(tenant))
    except ValueError:
        raise HTTPException(422, "A tenant id is required.") from None


def tenant_dir(root: Path, tenant: str) -> Path:
    directory = root / "tenants" / str(uuid.UUID(tenant))
    if directory.is_symlink():
        raise RuntimeError("Tenant directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


def public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_global and not ip.is_multicast and not ip.is_unspecified


def public_url(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise ValueError("Only public web pages without credentials are allowed")
    if port != (443 if parsed.scheme == "https" else 80):
        raise ValueError("Only standard web ports are allowed")
    if host in {"localhost", "metadata.google.internal"} or host.endswith((".localhost", ".internal", ".local")):
        raise ValueError("Private host refused")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not public_address(host):
            raise ValueError("Private address refused")
    return host, port


async def public_destination(url: str) -> tuple[str, int, list[str]]:
    host, port = public_url(url)
    answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses or not all(public_address(address) for address in addresses):
        raise ValueError("Private or internal address refused")
    return host, port, addresses
