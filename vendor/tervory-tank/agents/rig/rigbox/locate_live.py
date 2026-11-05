"""Locate confirm: re-read the chosen offer live, then issue a short-lived hold.

A located offer is evidence from an earlier observation. Before a shopper can
accept, the other store's page (or the approved source's own API) is read
again. Only an explicit in-stock answer with a stated price becomes a hold.
Unknown, blocked, unreadable or stale answers are never shown as available.

Checkers are pluggable. The product path has two: the approved-source API
adapter and Tiger's one-shot check-now route. Test doubles live in tests only.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import quote as urlquote, urlsplit
from uuid import UUID

from rigbox.locate import SupplyStore, gtin, locate, offer_price, stamp

STATUSES = ("available", "unavailable", "unknown")
HOLD_SECONDS = 600
HOLD_MAX_SECONDS = 900
# Tiger check-now reuses the browser's 45 second request budget (#458).
TIGER_BUDGET_SECONDS = 45


class LocateRefused(Exception):
    """Fixed, shopper-safe code. Never a remote response or a credential."""


@dataclass(frozen=True)
class LiveResult:
    status: str
    price: Decimal | None = None
    currency: str | None = None
    checked_at: datetime | None = None
    gtin: str | None = None
    method: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def unknown(cls, method: str, reason: str) -> "LiveResult":
        return cls("unknown", method=method, evidence={"reason": reason})


class LiveChecker(Protocol):
    async def check(self, tenant: str, source: dict, offer: dict) -> LiveResult: ...


def _https(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    return value


def _env_suffix(source_id: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", source_id.upper()).strip("_")


def _price(value: Any) -> Decimal | None:
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return price if price.is_finite() and price >= 0 else None


def _result(method: str, data: Any, now: datetime) -> LiveResult:
    """Strict reading of a checker answer. Anything not explicit is unknown."""
    if not isinstance(data, dict) or data.get("status") not in STATUSES:
        return LiveResult.unknown(method, "unreadable")
    if data["status"] != "available":
        return LiveResult(data["status"], method=method, checked_at=now,
                          evidence={"reason": str(data.get("reason") or data["status"])[:80]})
    price = _price(data.get("price"))
    currency = str(data.get("currency") or "").upper()
    if price is None or not re.fullmatch("[A-Z]{3}", currency):
        return LiveResult.unknown(method, "no_price")
    try:
        checked = stamp(data.get("checked_at")) if data.get("checked_at") else now
    except ValueError:
        return LiveResult.unknown(method, "no_time")
    code = gtin(data.get("gtin")) or None
    return LiveResult("available", price, currency, checked, code, method,
                      {key: data[key] for key in ("url", "method") if isinstance(data.get(key), str)})


class SourceApiChecker:
    """Approved-source API adapter.

    Contract: GET {RIG_SUPPLY_API_URL_<SOURCE>}/stock/{offer sku} with
    Authorization: Bearer {RIG_SUPPLY_API_KEY_<SOURCE>}, answering
    {"status": "available"|"unavailable"|"unknown", "price", "currency",
    "checked_at", "gtin"}. No configured adapter means unknown, never available.
    """

    def __init__(self, http: Any, env: dict[str, str] | None = None, clock=lambda: datetime.now(timezone.utc)):
        self.http, self.env, self.clock = http, os.environ if env is None else env, clock

    async def check(self, tenant: str, source: dict, offer: dict) -> LiveResult:
        suffix = _env_suffix(str(source.get("id") or ""))
        base = _https(self.env.get("RIG_SUPPLY_API_URL_" + suffix, ""))
        key = self.env.get("RIG_SUPPLY_API_KEY_" + suffix, "")
        if not suffix or not base or not key:
            return LiveResult.unknown("source_api", "not_configured")
        try:
            response = await self.http.get(base.rstrip("/") + "/stock/" + urlquote(str(offer["sku"]), safe=""),
                                           headers={"Authorization": "Bearer " + key}, timeout=20,
                                           follow_redirects=False)
            if response.status_code != 200:
                return LiveResult.unknown("source_api", "unavailable")
            return _result("source_api", response.json(), self.clock())
        except Exception:
            return LiveResult.unknown("source_api", "unavailable")


class TigerChecker:
    """Tiger's one-shot check-now on a store page Tiger already watches.

    Tiger re-reads the page with its browser (45 second budget), the
    deterministic structured price reader and its confirmed_value rule.
    """

    def __init__(self, http: Any, env: dict[str, str] | None = None, clock=lambda: datetime.now(timezone.utc)):
        self.http, self.env, self.clock = http, os.environ if env is None else env, clock

    async def check(self, tenant: str, source: dict, offer: dict) -> LiveResult:
        url = _https(str(offer.get("url") or ""))
        base = self.env.get("RIG_TIGER_URL", "")
        key = self.env.get("RIG_TIGER_CHECK_KEY", "")
        if not url:
            return LiveResult.unknown("tiger", "no_page")
        if not base or not key:
            return LiveResult.unknown("tiger", "not_configured")
        try:
            response = await self.http.post(base.rstrip("/") + "/rig/check-now",
                                            json={"tenant_id": str(tenant), "url": url},
                                            headers={"x-rig-check-key": key},
                                            timeout=TIGER_BUDGET_SECONDS + 5, follow_redirects=False)
            if response.status_code != 200:
                return LiveResult.unknown("tiger", "unavailable")
            return _result("tiger", response.json(), self.clock())
        except Exception:
            return LiveResult.unknown("tiger", "unavailable")


class Checkers:
    """Select the checker by the approved source's provider. No fallback to cached data."""

    def __init__(self, by_provider: dict[str, LiveChecker]):
        self.by_provider = by_provider

    async def check(self, tenant: str, source: dict, offer: dict) -> LiveResult:
        checker = self.by_provider.get(str(source.get("provider") or ""))
        if checker is None:
            return LiveResult.unknown("none", "no_live_checker")
        return await checker.check(tenant, source, offer)


def product_checkers(http: Any) -> Checkers:
    return Checkers({"api": SourceApiChecker(http), "tiger": TigerChecker(http)})


def hold_secret(env: dict[str, str] | None = None) -> bytes:
    value = (os.environ if env is None else env).get("RIG_LOCATE_HOLD_SECRET", "")
    if len(value) < 32:
        raise LocateRefused("hold_not_configured")
    return value.encode()


def _sign(secret: bytes, tenant: str, hold_id: str, expires: int) -> str:
    message = f"rig-locate-hold:v1:{tenant}:{hold_id}:{expires}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def hold_token(secret: bytes, tenant: str, hold_id: str, expires: int) -> str:
    return f"{hold_id}.{expires}.{_sign(secret, tenant, hold_id, expires)}"


def read_token(secret: bytes, tenant: str, token: str, now: datetime) -> str:
    parts = str(token or "").split(".")
    if len(parts) != 3 or not re.fullmatch(r"h_[0-9a-f]{32}", parts[0]) or not parts[1].isdigit():
        raise LocateRefused("hold_invalid")
    if not hmac.compare_digest(_sign(secret, tenant, parts[0], int(parts[1])), parts[2]):
        raise LocateRefused("hold_invalid")
    if int(parts[1]) <= int(now.timestamp()):
        raise LocateRefused("hold_expired")
    return parts[0]


class Holds:
    """Tenant-scoped, single-use holds. Memory fallback mirrors SupplyStore."""

    def __init__(self, store: Any):
        self.supply = SupplyStore(store)
        self.pool = self.supply.pool
        self.store = store

    async def save(self, tenant: str, hold_id: str, product_id: str, public: dict, private: dict,
                   expires_at: datetime) -> None:
        if self.pool is None:
            rows = self.store.supply_state.setdefault(str(tenant), {}).setdefault("holds", {})
            rows[hold_id] = {"product_id": product_id, "public": public, "private": private,
                             "expires_at": expires_at, "used_at": None}
            return
        import json

        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM agent_rig.supply_holds WHERE tenant_id=$1 AND expires_at < now() - interval '1 day'",
                               UUID(str(tenant)))
            await conn.execute("""INSERT INTO agent_rig.supply_holds(tenant_id,id,product_id,public,private,expires_at)
                VALUES($1,$2,$3,$4::jsonb,$5::jsonb,$6)""", UUID(str(tenant)), hold_id, product_id,
                json.dumps(public), json.dumps(private), expires_at)

    async def peek(self, tenant: str, hold_id: str, now: datetime) -> dict | None:
        """Read a usable hold without consuming it (for refusals that must not burn it)."""
        if self.pool is None:
            row = self.store.supply_state.get(str(tenant), {}).get("holds", {}).get(hold_id)
            if row is None or row["used_at"] is not None or row["expires_at"] <= now:
                return None
            return {key: row[key] for key in ("product_id", "public", "private")}
        import json

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""SELECT product_id, public, private FROM agent_rig.supply_holds
                WHERE tenant_id=$1 AND id=$2 AND used_at IS NULL AND expires_at > $3""", UUID(str(tenant)), hold_id, now)
        if row is None:
            return None
        load = lambda value: json.loads(value) if isinstance(value, str) else value  # noqa: E731
        return {"product_id": row["product_id"], "public": load(row["public"]), "private": load(row["private"])}

    async def take(self, tenant: str, hold_id: str, now: datetime) -> dict | None:
        """Consume once. Expired, used or another tenant's hold returns None."""
        if self.pool is None:
            row = self.store.supply_state.get(str(tenant), {}).get("holds", {}).get(hold_id)
            if row is None or row["used_at"] is not None or row["expires_at"] <= now:
                return None
            row["used_at"] = now
            return {key: row[key] for key in ("product_id", "public", "private")}
        import json

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""UPDATE agent_rig.supply_holds SET used_at=$3
                WHERE tenant_id=$1 AND id=$2 AND used_at IS NULL AND expires_at > $3
                RETURNING product_id, public, private""", UUID(str(tenant)), hold_id, now)
        if row is None:
            return None
        load = lambda value: json.loads(value) if isinstance(value, str) else value  # noqa: E731
        return {"product_id": row["product_id"], "public": load(row["public"]), "private": load(row["private"])}


def hold_seconds(env: dict[str, str] | None = None) -> int:
    raw = (os.environ if env is None else env).get("RIG_LOCATE_HOLD_SECONDS", "")
    try:
        value = int(raw) if raw else HOLD_SECONDS
    except ValueError:
        value = HOLD_SECONDS
    return max(60, min(value, HOLD_MAX_SECONDS))


async def confirm(store: Any, tenant: str, product: dict, checker: LiveChecker, *,
                  secret: bytes, now: datetime | None = None, ttl: int | None = None) -> dict:
    """Re-read offer, re-check freshness and live stock, then hold one price."""
    moment = now or datetime.now(timezone.utc)
    located = await locate(store, tenant, product, now=moment)
    if located["public"]["status"] != "available_to_order":
        return {"public": located["public"]}
    offer = located["store"]
    sources, _, _ = await SupplyStore(store).snapshot(tenant)
    source = next((s for s in sources if s["id"] == offer["source_id"] and s.get("approved") is True
                   and str(s.get("tenant_id")) == str(tenant)), None)
    if source is None:
        return {"public": {"status": "unavailable"}}
    live = await checker.check(tenant, source, offer)
    evidence = {"method": live.method, "status": live.status, **live.evidence}
    if live.status == "unavailable":
        return {"public": {"status": "unavailable"}, "store": {"offer": offer, "live": evidence}}
    if live.status != "available" or live.price is None or not live.currency or live.checked_at is None:
        return {"public": {"status": "check_with_store"}, "store": {"offer": offer, "live": evidence}}
    age = (moment - live.checked_at).total_seconds()
    if age < -5 or age > int(source["max_offer_age_seconds"]):
        return {"public": {"status": "check_with_store"}, "store": {"offer": offer, "live": {**evidence, "reason": "stale"}}}
    if live.gtin and gtin(product.get("gtin")) and live.gtin != gtin(product.get("gtin")):
        return {"public": {"status": "check_with_store"}, "store": {"offer": offer, "live": {**evidence, "reason": "identity"}}}
    settings = await store.rule(tenant)
    try:
        amount, landed = await offer_price(store, tenant, settings, source, offer, live.price, live.currency,
                                           product["currency"])
    except (ValueError, TypeError):
        return {"public": {"status": "check_with_store"}, "store": {"offer": offer, "live": {**evidence, "reason": "price"}}}
    expires_at = moment + timedelta(seconds=ttl or hold_seconds())
    hold_id = "h_" + secrets.token_hex(16)
    public = {"status": "available_to_order", "eta": offer.get("eta"), "ship_days": offer.get("ship_days"),
              "price": {"amount": str(amount), "currency": product["currency"]},
              "expires_at": expires_at.isoformat()}
    private = {"offer": offer, "source": {**{key: source.get(key) for key in
                                             ("id", "provider", "site", "vendor_name", "order_email")},
                                          "neutral_slip_required": source.get("neutral_slip_required") is True},
               "live": {**evidence, "price": str(live.price), "currency": live.currency,
                        "checked_at": live.checked_at.isoformat()},
               "landed": str(landed), "product_sku": product["sku"]}
    await Holds(store).save(tenant, hold_id, product["sku"], public, private, expires_at)
    token = hold_token(secret, tenant, hold_id, int(expires_at.timestamp()))
    return {"public": {**public, "hold": token}, "store": private, "hold_id": hold_id}
