"""Reused Rig primitives for the Tank image, which has no agents/rig package.

Rig limits.py is unchanged except table namespace; key issuer, hash/error and
SMTP class are verbatim. Do not fork their behavior. See B1 evidence for pins.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

QUOTE_ROUTES = frozenset({"/v1/rig/quote", "/v1/rig/link-quote", "/v1/trigger/bot/handover"})


def trusted_proxy_networks() -> list:
    networks = []
    for value in os.environ.get("TRIGGER_BOT_TRUSTED_PROXY_CIDRS", "").split(","):
        if value.strip():
            network = ipaddress.ip_network(value.strip(), strict=False)
            if network.prefixlen == 0:
                raise ValueError("A specific trusted proxy network is required")
            networks.append(network)
    return networks


def proxy_configured() -> bool:
    try:
        return bool(trusted_proxy_networks())
    except ValueError:
        return False


def client_address(request: Any) -> str:
    peer = request.client.host if request.client else "unknown"
    networks = trusted_proxy_networks()

    def trusted(value: str) -> bool:
        try:
            address = ipaddress.ip_address(value)
            return any(address in network for network in networks)
        except ValueError:
            return False

    if not trusted(peer):
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    try:
        chain = [str(ipaddress.ip_address(value.strip())) for value in forwarded.split(",")]
    except ValueError:
        return peer
    if len(chain) > 16:
        return peer
    # Walk from the known peer, never trust the leftmost header blindly.
    for address in reversed(chain):
        peer = address
        if not trusted(address):
            break
    return peer


def budgets(route: str, read_limit: int = 60) -> tuple[str, int, str, int]:
    if route in QUOTE_ROUTES:
        return "quotes", 5, "quotes", 600
    limit = 10 if route == "/v1/rig/ask" else read_limit
    return route, limit, "browse", 6000


def fingerprint(salt: bytes, address: str, *, ipv6_prefix: int = 64) -> bytes:
    try:
        ip = ipaddress.ip_address(address)
        if isinstance(ip, ipaddress.IPv6Address):
            address = str(ip.ipv4_mapped or ipaddress.ip_network(f"{ip}/{ipv6_prefix}", strict=False))
        else:
            address = str(ip)
    except ValueError:
        pass
    return hmac.new(salt, address.encode(), hashlib.sha256).digest()


@dataclass(frozen=True)
class LimitDecision:
    retry_after: int = 0
    hold: bool = False


class FailedKeyLimits:
    """Bound failed lookups before touching Postgres, per process and client.

    Successful lookups release their reservation. In-flight lookups count so a
    concurrent burst cannot bypass the guard. No key or raw address is retained.
    """

    def __init__(self, limit: int = 60, clock=time.time):
        self.limit, self.clock = limit, clock
        self.window = -1
        self.salt = b""
        self.counts: dict[bytes, int] = {}

    def reserve(self, address: str) -> tuple[int, bytes | None] | None:
        window = int(self.clock()) // 60
        if window != self.window:
            self.window, self.salt, self.counts = window, secrets.token_bytes(32), {}
        identity = fingerprint(self.salt, address, ipv6_prefix=48)
        if self.counts.get(identity, 0) >= self.limit:
            return None
        if identity not in self.counts and len(self.counts) >= 10000:
            # This optional pre-auth guard must never lock out every retailer.
            # Evict the oldest bucket and leave this lookup untracked. Normal
            # key authentication and tenant/shopper rate limits still apply.
            self.counts.pop(next(iter(self.counts)))
            return window, None
        self.counts[identity] = self.counts.get(identity, 0) + 1
        return window, identity

    def release(self, reservation: tuple[int, bytes | None]) -> None:
        window, identity = reservation
        if identity is not None and window == self.window:
            remaining = self.counts.get(identity, 1) - 1
            if remaining:
                self.counts[identity] = remaining
            else:
                self.counts.pop(identity, None)


class MemoryRateLimits:
    def __init__(self, read_limit: int = 60, clock=time.time):
        self.read_limit, self.clock = read_limit, clock
        self.windows: dict[tuple[str, int], tuple[bytes, dict]] = {}
        self.lock = asyncio.Lock()

    async def consume(self, tenant: str, address: str, route: str) -> LimitDecision:
        now = int(self.clock())
        window = now // 60
        async with self.lock:
            self.windows = {key: value for key, value in self.windows.items() if key[1] >= window - 1}
            salt, counts = self.windows.setdefault((tenant, window), (secrets.token_bytes(32), {}))
            client_route, limit, group, ceiling = budgets(route, self.read_limit)
            keys = [(group, b""), (client_route, fingerprint(salt, address))]
            hold = counts.get(keys[0], 0) >= ceiling and route in QUOTE_ROUTES
            if counts.get(keys[1], 0) >= limit or (counts.get(keys[0], 0) >= ceiling and not hold):
                return LimitDecision(retry_after=60 - now % 60)
            counts[keys[1]] = counts.get(keys[1], 0) + 1
            if not hold:
                counts[keys[0]] = counts.get(keys[0], 0) + 1
        return LimitDecision(hold=hold)


class _Full(Exception):
    pass


class PgRateLimits:
    def __init__(self, pool: Any, read_limit: int = 60):
        self.pool, self.read_limit = pool, read_limit

    async def consume(self, tenant: str, address: str, route: str) -> LimitDecision:
        retry_after = 60
        hold = False
        try:
            async with self.pool.acquire() as conn, conn.transaction():
                now = int(await conn.fetchval("SELECT floor(extract(epoch FROM clock_timestamp()))::bigint"))
                window = now // 60
                retry_after = 60 - now % 60
                await conn.execute("""DELETE FROM tank.trigger_bot_windows
                    WHERE tenant_id=$1 AND window_id < $2""", UUID(tenant), window - 1)
                await conn.execute("""INSERT INTO tank.trigger_bot_windows (tenant_id, window_id, salt)
                    VALUES ($1,$2,$3) ON CONFLICT DO NOTHING""", UUID(tenant), window, secrets.token_bytes(32))
                salt = bytes(await conn.fetchval("""SELECT salt FROM tank.trigger_bot_windows
                    WHERE tenant_id=$1 AND window_id=$2""", UUID(tenant), window))
                client_route, limit, group, ceiling = budgets(route, self.read_limit)
                for bucket, identity, cap in [(group, b"", ceiling),
                                              (client_route, fingerprint(salt, address), limit)]:
                    accepted = await conn.fetchval("""INSERT INTO tank.trigger_bot_limits
                        (tenant_id, window_id, route, client_hash, hits) VALUES ($1,$2,$3,$4,1)
                        ON CONFLICT (tenant_id, window_id, route, client_hash) DO UPDATE
                        SET hits=tank.trigger_bot_limits.hits + 1 WHERE tank.trigger_bot_limits.hits < $5
                        RETURNING hits""", UUID(tenant), window, bucket, identity, cap)
                    if accepted is None:
                        if identity == b"" and route in QUOTE_ROUTES:
                            hold = True
                            continue  # Still enforce the client's allowance for held quotes.
                        raise _Full  # Roll back BOTH counters; rejected clients cannot drain the tenant.
        except _Full:
            return LimitDecision(retry_after=retry_after)
        return LimitDecision(hold=hold)


import smtplib
import ssl
from email.message import EmailMessage


class RigError(Exception):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

async def issue_embed_key(store: Any, tenant: str, origins: list[str]) -> dict:
    if not isinstance(origins, list) or not 1 <= len(origins) <= 10:
        raise RigError("invalid", "List one to ten exact HTTPS origins without paths.", 422)
    for origin in origins:
        try:
            parsed = urlsplit(origin)
            port = parsed.port
        except (ValueError, TypeError, AttributeError):
            raise RigError("invalid", "List exact HTTPS origins without paths.", 422) from None
        if (not isinstance(origin, str) or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                or any(c.isspace() for c in origin) or "*" in origin or port == 0):
            raise RigError("invalid", "List exact HTTPS origins without paths.", 422)
    key = "rk_pub_" + secrets.token_hex(24)
    await store.put_key(tenant, hash_key(key), "publishable", origins)
    return {"publishable_key": key, "origins": origins}

class SmtpMailer:
    """Sends one plain-text reservation. Built only when SMTP_HOST is set."""

    def __init__(self, host: str, port: int, username: str, password: str, sender: str) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender

    @classmethod
    def from_env(cls) -> SmtpMailer | None:
        host = (os.environ.get("SMTP_HOST") or "").strip()
        if not host:
            return None
        port = int(os.environ.get("SMTP_PORT") or "587")
        return cls(
            host,
            port,
            (os.environ.get("SMTP_USER") or "").strip(),
            os.environ.get("SMTP_PASSWORD") or "",
            (os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or "").strip(),
        )

    async def send(self, letter: dict[str, str]) -> None:
        if not letter.get("to") or not self.sender:
            raise RuntimeError("mail is not configured")
        message = EmailMessage()
        message["Subject"] = letter.get("subject") or ""
        message["From"] = self.sender
        message["To"] = letter["to"]
        if letter.get("message_id"):
            message["Message-ID"] = letter["message_id"]
        reply_to = str(letter.get("reply_to") or "").strip()
        if reply_to:
            message["Reply-To"] = reply_to
        message.set_content(letter.get("text") or "")
        await asyncio.to_thread(self._deliver, message)

    def _deliver(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(message)
