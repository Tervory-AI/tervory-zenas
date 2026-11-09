"""Company-only Context.dev trial adapter. A durable Tank ledger is required."""

from __future__ import annotations

try:
    from network import BoundedHTTP, paid_post
except ModuleNotFoundError:  # package imports in offline and shared callers
    from agents.base.network import BoundedHTTP, paid_post

import asyncio
import ipaddress
import hashlib
import json
import math
import os
import re
import uuid
from datetime import date
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

ENDPOINT = "https://api.context.dev/v1/brand/retrieve"
BRAND_CREDITS = 10


class Ledger(Protocol):
    async def reserve(self, event: dict[str, Any], max_credits: int) -> str: ...

    async def settle(self, reservation: str, event: dict[str, Any]) -> None: ...


class CreditLimit(Exception):
    pass


def public_domain(value: str) -> str:
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        raise ValueError("a public business domain is required")
    if parsed.query or parsed.fragment or parsed.port not in (None, 80, 443):
        raise ValueError("URL credentials, queries and fragments are not accepted")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP addresses are not business domains")
    if (
        not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", host)
        or host.endswith((".local", ".internal", ".localhost", ".test", ".invalid"))
    ):
        raise ValueError("a public business domain is required")
    return host


def lookup_body(*, name: str = "", domain: str = "", url: str = "") -> dict[str, str]:
    if domain or url:
        return {"type": "by_domain", "domain": public_domain(domain or url)}
    name = name.strip()
    if not 3 <= len(name) <= 30 or not re.fullmatch(r"[\w &'’().,-]+", name):
        raise ValueError("a public business name of 3–30 characters is required")
    return {"type": "by_name", "name": name}


def candidate_fields(body: dict[str, Any]) -> dict[str, Any]:
    brand = body.get("brand")
    if not isinstance(brand, dict):
        return {}
    fields = {key: brand[key] for key in ("title", "description", "colors", "logos", "industries") if key in brand}
    if "title" in fields:
        fields["name"] = fields.pop("title")
    if isinstance(brand.get("domain"), str):
        try:
            fields["website"] = f"https://{public_domain(brand['domain'])}"
        except ValueError:
            pass
    socials = brand.get("socials")
    if isinstance(socials, list):
        fields["socials"] = [
            {"url": item["url"]}
            for item in socials
            if isinstance(item, dict) and isinstance(item.get("url"), str)
            and item["url"].startswith("https://")
            and not urlsplit(item["url"]).query and not urlsplit(item["url"]).username
        ]
    return fields


def charged_credits(response: httpx.Response, body: dict[str, Any]) -> float | None:
    metadata = body.get("key_metadata")
    raw = response.headers.get("X-Credits-Used")
    if raw is None and isinstance(metadata, dict):
        raw = metadata.get("credits_consumed")
    try:
        credits = float(raw)
        return credits if math.isfinite(credits) and credits >= 0 else None
    except (TypeError, ValueError):
        return None


def _api_key():
    return os.environ.get("CONTEXT_DEV_API_KEY")


def configured():
    return bool(_api_key())


class ContextDev:
    def __init__(self, http: httpx.AsyncClient, ledger: Ledger | None, *, tenant_id: str, run_id: str) -> None:
        self.http = BoundedHTTP(http)
        self.ledger = ledger
        self.tenant_id = str(uuid.UUID(tenant_id))
        self.run_id = str(uuid.UUID(run_id))
        self.max_credits = int(os.environ.get("BASE_CTX_MAX_CREDITS") or os.environ.get("CONTEXT_DEV_MAX_CREDITS") or "500")
        if self.max_credits < 0:
            raise ValueError("credit limit must not be negative")
        self.reserved = 0
        self.stopped = False
        self.lock = asyncio.Lock()

    async def _paid_admission(self, payload, record_id):
        key = os.environ.get("TANK_KEY", "")
        if not key:
            raise RuntimeError("Paid-step admission is unavailable")
        digest = hashlib.sha256(json.dumps([record_id,payload],sort_keys=True,separators=(",", ":")).encode()).hexdigest()
        run_id = uuid.uuid5(uuid.UUID(str(self.run_id)), digest)
        response = await paid_post(self.http, os.environ.get("TANK_URL", "http://tank:8000").rstrip("/"),
            "/v1/paid-steps/admit", {"tenant_id": str(self.tenant_id), "run_id": str(run_id),
            "step_id": "base.context_dev.credit", "request_hash": digest}, {"X-Tank-Key": key}, timeout=30)
        response.raise_for_status()
        return response.json()

    async def lookup(self, *, name: str = "", domain: str = "", url: str = "", record_id: str | None = None) -> dict[str, Any]:
        payload = lookup_body(name=name, domain=domain, url=url)
        if self.ledger is None:
            return {"status": "ledger_unavailable", "fields": {}}
        key = _api_key()
        if not key:
            return {"status": "not_configured", "fields": {},
                    "missing_settings": ["CONTEXT_DEV_API_KEY"],
                    "message": "Missing setting: CONTEXT_DEV_API_KEY"}
        if self.stopped or self.reserved + BRAND_CREDITS > self.max_credits:
            return {"status": "credit_limit", "message": "credit cap reached", "fields": {}}
        approval = await self._paid_admission(payload, record_id)
        async with self.lock:
            if self.stopped or self.reserved + BRAND_CREDITS > self.max_credits:
                return {"status": "credit_limit", "message": "credit cap reached", "fields": {}}
            event = {
                "tenant_id": self.tenant_id, "run_id": self.run_id,
                "call_id": str(uuid.uuid4()), "agent": "base",
                "task_type": "base.context_dev", "reserved_credits": BRAND_CREDITS,
                "record_id": record_id,
                "paid_step_approval_id": approval["approval_id"],
            }
            try:
                reservation = await self.ledger.reserve(event, self.max_credits)
            except CreditLimit:
                self.stopped = True
                return {"status": "credit_limit", "message": "credit cap reached", "fields": {}}
            self.reserved += BRAND_CREDITS
            result: dict[str, Any] = {"status": "unknown", "fields": {}, "credits": None,
                                      "observed_on": date.today().isoformat()}
            try:
                response = await self.http.post(
                    ENDPOINT, json=payload, headers={"Authorization": f"Bearer {key}"},
                    timeout=70, follow_redirects=False,
                )
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                if not isinstance(body, dict):
                    body = {}
                result["credits"] = charged_credits(response, body)
                result["status"] = "candidate" if response.status_code == 200 and body.get("status") == "ok" else "failed"
                if result["status"] == "candidate":
                    result["fields"] = candidate_fields(body)
                result["partial"] = body.get("partial") is True
            except httpx.HTTPError:
                result["status"] = "transport_error"
            finally:
                credits = result["credits"]
                if credits is None or credits <= 0 or credits > BRAND_CREDITS:
                    self.stopped = True
                try:
                    await self.ledger.settle(reservation, {**event, **result})
                except BaseException:
                    self.stopped = True
                    raise
            return result
