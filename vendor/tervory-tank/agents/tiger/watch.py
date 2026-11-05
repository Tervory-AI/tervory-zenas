"""Tiger's watchlist, price history, and alerts.

No product, brand, or industry words. A watch stores the caller's label and
engine string and does not interpret them. `threshold` is a fraction of the
previous price: 0 alerts on any change, 0.05 alerts above a 5% move.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import socket as socket  # Compatibility for callers supplying a test DNS resolver.
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import asyncpg
import httpx

from alert_email import letter_for
from change_alert import price_moved, record_stock, stock_reading, stock_statements
from chassis.cron import is_due
from chassis.db import schema_for
from chassis.public_urls import check_url as public_check_url, is_public_url as is_public_url
from domain_playbooks import MemoryPlaybooks, PgPlaybooks, public_marker, public_page
from g5_alerts import MemoryAlerts, PgAlerts, listing_products
from page_guard import layout_from_evidence, learn_layout, read_page

SCHEMA = schema_for("tiger")
ROBOTS_TOKEN = "tervorytiger"
_INTERVAL = re.compile(r"^(\d+)(s|m|h|d)$")
_SKIP_METHODS = {"blocked", "robots", "error", "none", "unread"}

SQL_LIST_WATCHES = """
SELECT id, tenant_id, url, label, engine, every, enabled, threshold, alert_price, alert_stock,
       alert_new_product, alert_below_cost, alert_market_gap
FROM {schema}.watchlist
WHERE tenant_id = $1
ORDER BY url
"""
SQL_GET_WATCH = """
SELECT id, tenant_id, url, label, engine, every, enabled, threshold, alert_price, alert_stock,
       alert_new_product, alert_below_cost, alert_market_gap
FROM {schema}.watchlist
WHERE tenant_id = $1 AND url = $2
"""
SQL_GET_WATCH_ID = """
SELECT id, tenant_id, url, label, engine, every, enabled, threshold, alert_price, alert_stock,
       alert_new_product, alert_below_cost, alert_market_gap
FROM {schema}.watchlist
WHERE tenant_id = $1 AND id = $2
"""
SQL_INSERT_WATCH = """
INSERT INTO {schema}.watchlist (tenant_id, url, label, engine, every, enabled, threshold, alert_price, alert_stock,
                               alert_new_product, alert_below_cost, alert_market_gap)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
RETURNING *
"""
SQL_UPDATE_WATCH = """
UPDATE {schema}.watchlist SET url=COALESCE($3,url), label=COALESCE($4,label), engine=COALESCE($5,engine),
    every=COALESCE($6,every), enabled=COALESCE($7,enabled), threshold=COALESCE($8,threshold),
    alert_price=COALESCE($9,alert_price), alert_stock=COALESCE($10,alert_stock),
    alert_new_product=COALESCE($11,alert_new_product), alert_below_cost=COALESCE($12,alert_below_cost),
    alert_market_gap=COALESCE($13,alert_market_gap)
WHERE tenant_id=$1 AND id=$2 RETURNING *
"""
SQL_DELETE_WATCH = """
DELETE FROM {schema}.watchlist
WHERE tenant_id = $1 AND id = $2
"""
SQL_INSERT_OBS = """
INSERT INTO {schema}.observations (tenant_id, url, ts, price, currency, availability, method, evidence)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
RETURNING id, tenant_id, url, ts, price, currency, availability, method, evidence
"""
SQL_LIST_OBS = """
SELECT id, tenant_id, url, ts, price, currency, availability, method, evidence
FROM {schema}.observations
WHERE tenant_id = $1 AND url = $2
ORDER BY ts DESC
LIMIT 200
"""
SQL_LATEST_OBS = """
SELECT id, tenant_id, url, ts, price, currency, availability, method, evidence
FROM {schema}.observations
WHERE tenant_id = $1 AND url = $2
ORDER BY ts DESC
LIMIT 1
"""
SQL_PREVIOUS_OBS = """
SELECT id, tenant_id, url, ts, price, currency, availability, method, evidence
FROM {schema}.observations
WHERE tenant_id = $1 AND url = $2 AND id <> $3
  AND method <> ALL($4::text[])
ORDER BY ts DESC
LIMIT 1
"""
SQL_INSERT_ALERT = """
INSERT INTO {schema}.alerts (tenant_id, url, ts, kind, before, after)
VALUES ($1, $2, $3, $4, $5, $6)
RETURNING id, tenant_id, url, ts, kind, before, after
"""
SQL_LIST_ALERTS = """
SELECT id, tenant_id, url, ts, kind, before, after
FROM {schema}.alerts
WHERE tenant_id = $1 AND ($2::text IS NULL OR url = $2)
  AND ($3::text IS NULL OR kind = $3)
ORDER BY ts DESC
LIMIT 200
"""
SQL_CHECK_OPEN = """
SELECT 1
FROM {schema}.jobs
WHERE tenant_id = $1 AND kind = 'check' AND status IN ('queued', 'running')
  AND input->>'url' = $2
LIMIT 1
"""
SQL_ENQUEUE_CHECK = """
INSERT INTO {schema}.jobs (tenant_id, kind, input, status, run_at)
VALUES ($1, 'check', $2::jsonb, 'queued', now())
"""
SQL_ENSURE_DISPATCH = """
INSERT INTO {schema}.schedules (tenant_id, cron, kind, input, enabled)
VALUES ($1, '* * * * *', 'dispatch', '{{}}'::jsonb, true)
ON CONFLICT (tenant_id, kind, cron) DO NOTHING
"""


class DuplicateWatch(Exception):
    """This tenant already watches that URL."""


class BadWatch(ValueError):
    """The watch fields are not usable."""


class FeedUnavailable(RuntimeError):
    """The official feed lookup failed; a policy-checked page read can follow."""


def session_for_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def robots_url_for(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/robots.txt"


def check_url(url: str) -> str:
    try:
        return public_check_url(url)
    except ValueError as exc:
        raise BadWatch(str(exc)) from None


def check_every(every: str) -> str:
    text = every.strip()
    if _INTERVAL.fullmatch(text) or text.isdigit():
        if every_seconds(text) <= 0:
            raise BadWatch("every must be a positive interval")
        return text
    try:
        is_due(text, datetime.now(timezone.utc), None)
    except (ValueError, TypeError) as exc:
        raise BadWatch("every must be a cron expression or an interval such as 1h") from exc
    return text


def check_threshold(value: Any) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BadWatch("threshold must be a number") from exc
    if number < 0:
        raise BadWatch("threshold must be zero or more")
    return _plain(number)


def check_text(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise BadWatch("a field is too long")
    return text


def every_seconds(every: str) -> int | None:
    text = every.strip()
    if text.isdigit():
        return int(text)
    match = _INTERVAL.fullmatch(text)
    if match is None:
        return None
    amount = int(match.group(1))
    unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    return amount * unit


def watch_is_due(every: str, now: datetime, last: datetime | None) -> bool:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    seconds = every_seconds(every)
    if seconds is None:
        return is_due(every.strip(), now, last)
    if seconds <= 0:
        return False
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last).total_seconds() >= seconds


def robots_allows(body: str, url: str, token: str = ROBOTS_TOKEN) -> bool:
    """True when robots.txt does not disallow this URL for Tiger."""
    if not body.strip():
        return True
    groups = _robot_groups(body)
    rules = _rules_for(groups, token)
    if rules is None:
        for agents, group_rules in groups:
            if any(agent.strip() == "*" for agent in agents):
                rules = group_rules
                break
    if not rules:
        return True
    path = urlparse(url).path or "/"
    allow_len = -1
    disallow_len = -1
    for kind, prefix in rules:
        if not prefix:
            continue
        if path.startswith(prefix):
            if kind == "allow" and len(prefix) > allow_len:
                allow_len = len(prefix)
            elif kind == "disallow" and len(prefix) > disallow_len:
                disallow_len = len(prefix)
    if disallow_len < 0:
        return True
    return allow_len >= disallow_len


def _robot_groups(body: str) -> list[tuple[list[str], list[tuple[str, str]]]]:
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []

    def flush() -> None:
        nonlocal agents, rules
        if agents or rules:
            groups.append((agents, rules))
        agents, rules = [], []

    for raw in body.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if rules:
                flush()
            agents.append(value)
        elif key in {"allow", "disallow"} and agents:
            rules.append((key, value))
    flush()
    return groups


def _rules_for(groups: list[tuple[list[str], list[tuple[str, str]]]], token: str) -> list[tuple[str, str]] | None:
    for agents, rules in groups:
        if any(token in agent.lower() for agent in agents):
            return rules
    return None


_UNSET = object()


def alerts_for(
    previous: dict | None,
    current: dict,
    threshold: Any,
    stock_before: Any = _UNSET,
    *,
    price_enabled: bool = True,
    stock_enabled: bool = True,
) -> list[dict[str, str]]:
    if not _comparable(previous) or not _comparable(current):
        return []
    assert previous is not None
    found: list[dict[str, str]] = []
    if price_enabled and price_moved(previous.get("price"), current.get("price"), threshold):
        found.append({
            "kind": "price",
            "before": str(previous.get("price")),
            "after": str(current.get("price")),
        })
    if stock_before is _UNSET:
        stock_before = _known_stock(previous.get("availability"))
    elif stock_before == "can't read":
        stock_before = None
    after_stock = stock_reading(current.get("availability"))
    if (
        stock_enabled
        and stock_before
        and after_stock
        and after_stock != "can't read"
        and stock_before != after_stock
    ):
        found.append({"kind": "stock", "before": str(stock_before), "after": str(after_stock)})
    return found


def _known_stock(value: Any) -> str | None:
    reading = stock_reading(value)
    if not reading or reading == "can't read":
        return None
    return reading


def _stock_for(method: str | None, raw: Any, html: str) -> str | None:
    """Record a known stock phrase, even with extra words. Other wording is "can't read"."""
    # A missing price/layout does not erase stock independently read by code.
    # Blocked/failed page responses return before this reader is called.
    recorded = record_stock(stock_statements(html))
    if recorded is not None:
        return recorded
    return stock_reading(raw)


async def _last_known_stock(store: Any, tenant_id: str, url: str, current_id: str) -> str | None:
    """Last stock phrase we could read. A "can't read" row is not a stock change."""
    rows = await store.list_observations(tenant_id, url)
    for row in rows:
        if str(row.get("id")) == str(current_id):
            continue
        if str(row.get("method") or "") in _SKIP_METHODS:
            continue
        known = _known_stock(row.get("availability"))
        if known:
            return known
    return None


def _comparable(row: dict | None) -> bool:
    if not row:
        return False
    return str(row.get("method") or "") not in _SKIP_METHODS


class DomainLimiter:
    """One request per domain per interval. Default 10 seconds."""

    def __init__(self, interval_s: float = 10, clock=None, sleep=None) -> None:
        self.interval_s = interval_s
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._next: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, host: str) -> float:
        async with self._lock:
            now = float(self._clock())
            ready = self._next.get(host, now)
            delay = max(0.0, ready - now)
            self._next[host] = max(ready, now) + self.interval_s
        if delay:
            await self._sleep(delay)
        return delay


def _plain(number: Decimal) -> str:
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _money(value: Any) -> str | None:
    if value is None:
        return None
    return f"{Decimal(str(value)).quantize(Decimal('0.01'))}"


def _stamp(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _obs(
    tenant_id: str,
    url: str,
    ts: datetime,
    price: str | None,
    currency: str | None,
    availability: str | None,
    method: str,
    evidence: str,
) -> dict[str, Any]:
    return {
        "tenant_id": str(tenant_id),
        "url": url,
        "ts": ts,
        "price": price,
        "currency": currency,
        "availability": availability,
        "method": method,
        "evidence": evidence[:8000],
    }


def _evidence(
    method: str | None,
    price: str | None,
    saved: str | None,
    layout: dict | None = None,
    stock: str | None = None,
) -> str:
    payload: dict[str, Any] = {"method": method, "price": price, "file": saved, "stock": stock}
    if layout and layout.get("primary"):
        payload["layout"] = {"primary": layout.get("primary")}
        if layout.get("dom"):
            payload["layout"]["dom"] = layout["dom"]
        if layout.get("dom_hash"):
            payload["layout"]["dom_hash"] = layout["dom_hash"]
    return json.dumps(payload, default=str)[:2000]


def _product_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    if not name or len(name) > 300 or re.fullmatch(r"untitled\s+page", name, re.I):
        return None
    return name


def _page_name(html: str) -> str | None:
    """Read a unique product heading; never use a document title or URL as a name."""
    class Names(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack = []
            self.names = set()

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            parent = self.stack[-1] if self.stack else {}
            skipped = parent.get("skip", False) or tag in {"script", "style", "nav", "header", "footer"}
            product = parent.get("product", False) or any(part in (values.get("class") or "").split()
                for part in ("product_main", "product-information", "product-details"))
            selected = not skipped and (tag == "h1" or product and tag == "h2"
                or product and values.get("itemprop") == "name")
            if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
                self.stack.append({"tag": tag, "skip": skipped, "product": product, "selected": selected, "text": ""})

        def handle_data(self, data):
            if self.stack and not self.stack[-1]["skip"]:
                for entry in self.stack:
                    if entry["selected"] and len(entry["text"]) <= 300:
                        entry["text"] += data

        def handle_endtag(self, tag):
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index]["tag"] == tag:
                    for entry in self.stack[index:]:
                        if entry["selected"] and (name := _product_name(entry["text"])):
                            self.names.add(name)
                    del self.stack[index:]
                    break

    scan = Names()
    scan.feed(html)
    scan.close()
    return next(iter(scan.names)) if len(scan.names) == 1 else None


def _reading_fields(evidence: Any) -> dict[str, Any]:
    try:
        value = json.loads(evidence or "{}")
    except (ValueError, TypeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    return {"product_name": _product_name(value.get("product_name")),
            "last_product_name": _product_name(value.get("last_product_name")),
            "issue": value.get("issue") if value.get("issue") in {
                "missing_name", "missing_price", "missing_currency", "missing_stock"} else None}


def _reading_evidence(evidence: str, name: str | None, last_name: str | None, issue: str | None = None) -> str:
    try:
        payload = json.loads(evidence)
    except ValueError:
        payload = {"detail": evidence}
    if not isinstance(payload, dict):
        payload = {}
    payload.update(product_name=name, last_product_name=name or last_name, issue=issue)
    # The original evidence is bounded at 2,000 chars and each name at 300.
    # Allow their JSON encoding too; truncating it would lose the saved layout
    # and let a later check learn a changed page as if it were the first read.
    return json.dumps(payload, default=str, ensure_ascii=False)


async def ensure_tables(conn: Any, schema: str = SCHEMA) -> None:
    statements = [
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.watchlist (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            url text NOT NULL,
            label text NOT NULL DEFAULT '',
            engine text NOT NULL DEFAULT '',
            every text NOT NULL,
            enabled boolean NOT NULL DEFAULT true,
            threshold numeric NOT NULL DEFAULT 0,
            alert_price boolean NOT NULL DEFAULT true,
            alert_stock boolean NOT NULL DEFAULT true,
            UNIQUE (tenant_id, url)
        )
        """,
        f"ALTER TABLE {schema}.watchlist ADD COLUMN IF NOT EXISTS alert_price boolean NOT NULL DEFAULT true",
        f"ALTER TABLE {schema}.watchlist ADD COLUMN IF NOT EXISTS alert_stock boolean NOT NULL DEFAULT true",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.observations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            url text NOT NULL,
            ts timestamptz NOT NULL DEFAULT now(),
            price numeric,
            currency text,
            availability text,
            method text,
            evidence text
        )
        """,
        f"CREATE INDEX IF NOT EXISTS observations_tenant_url_ts ON {schema}.observations (tenant_id, url, ts DESC)",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.alerts (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            url text NOT NULL,
            ts timestamptz NOT NULL DEFAULT now(),
            kind text NOT NULL,
            before text,
            after text
        )
        """,
        f"CREATE INDEX IF NOT EXISTS alerts_tenant_url_ts ON {schema}.alerts (tenant_id, url, ts DESC)",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.alert_mail (
            tenant_id uuid NOT NULL,
            alert_id uuid NOT NULL REFERENCES {schema}.alerts(id),
            url text NOT NULL,
            kind text NOT NULL,
            letter jsonb NOT NULL,
            status text NOT NULL DEFAULT 'pending',
            attempts integer NOT NULL DEFAULT 0,
            next_attempt_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            sent_at timestamptz,
            PRIMARY KEY (tenant_id, alert_id)
        )
        """,
        f"CREATE INDEX IF NOT EXISTS alert_mail_pending ON {schema}.alert_mail (tenant_id, next_attempt_at) WHERE status = 'pending'",
    ]
    for statement in statements:
        await conn.execute(statement)
    from rig_watch import ensure_rig_tables
    await ensure_rig_tables(conn, schema)
    from domain_playbooks import ensure_tables as ensure_playbooks
    await ensure_playbooks(conn, schema)
    from g5_alerts import ensure_tables as ensure_alerts
    await ensure_alerts(conn, schema)


def _watch_out(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "url": row["url"],
        "label": row["label"] or "",
        "engine": row["engine"] or "",
        "every": row["every"],
        "enabled": bool(row["enabled"]),
        "threshold": _plain(Decimal(str(row["threshold"]))),
        "alert_price": bool(row.get("alert_price", True)),
        "alert_stock": bool(row.get("alert_stock", True)),
        "alert_new_product": bool(row.get("alert_new_product", True)),
        "alert_below_cost": bool(row.get("alert_below_cost", True)),
        "alert_market_gap": bool(row.get("alert_market_gap", True)),
    }


def _obs_out(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "url": row["url"],
        "ts": _stamp(row["ts"]),
        "price": _money(row["price"]),
        "currency": row["currency"],
        "availability": row["availability"],
        "method": row["method"],
        "evidence": row["evidence"],
        **_reading_fields(row["evidence"]),
    }


def _alert_out(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "url": row["url"],
        "ts": _stamp(row["ts"]),
        "kind": row["kind"],
        "before": row["before"],
        "after": row["after"],
    }


class PgStore(PgPlaybooks, PgAlerts):
    def __init__(self, pool: Any, schema: str = SCHEMA) -> None:
        self.pool = pool
        self.schema = schema

    def _sql(self, statement: str) -> str:
        return statement.format(schema=self.schema)

    async def create_watch(self, row: dict[str, Any]) -> dict[str, Any]:
        async with self.pool.acquire() as conn:
            await ensure_tables(conn, self.schema)
            try:
                saved = await conn.fetchrow(
                    self._sql(SQL_INSERT_WATCH),
                    uuid.UUID(str(row["tenant_id"])),
                    row["url"],
                    row["label"],
                    row["engine"],
                    row["every"],
                    bool(row["enabled"]),
                    Decimal(str(row["threshold"])),
                    bool(row.get("alert_price", True)),
                    bool(row.get("alert_stock", True)),
                    bool(row.get("alert_new_product", True)),
                    bool(row.get("alert_below_cost", True)),
                    bool(row.get("alert_market_gap", True)),
                )
            except asyncpg.UniqueViolationError as exc:
                raise DuplicateWatch(row["url"]) from exc
            await conn.execute(self._sql(SQL_ENSURE_DISPATCH), uuid.UUID(str(row["tenant_id"])))
        return _watch_out(saved)

    async def list_watches(self, tenant_id: str) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            await ensure_tables(conn, self.schema)
            rows = await conn.fetch(self._sql(SQL_LIST_WATCHES), uuid.UUID(str(tenant_id)))
        return [_watch_out(row) for row in rows]

    async def get_watch(self, tenant_id: str, url: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            await ensure_tables(conn, self.schema)
            row = await conn.fetchrow(self._sql(SQL_GET_WATCH), uuid.UUID(str(tenant_id)), url)
        return _watch_out(row) if row else None

    async def get_watch_by_id(self, tenant_id: str, watch_id: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            await ensure_tables(conn, self.schema)
            row = await conn.fetchrow(
                self._sql(SQL_GET_WATCH_ID),
                uuid.UUID(str(tenant_id)),
                uuid.UUID(str(watch_id)),
            )
        return _watch_out(row) if row else None

    async def update_watch(self, tenant_id: str, watch_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        current = await self.get_watch_by_id(tenant_id, watch_id)
        if current is None:
            return None
        merged = dict(current)
        merged.update(fields)
        values: list[Any] = [uuid.UUID(str(tenant_id)), uuid.UUID(str(watch_id))]
        for column in ("url", "label", "engine", "every", "enabled", "threshold", "alert_price", "alert_stock",
                       "alert_new_product", "alert_below_cost", "alert_market_gap"):
            value = fields.get(column)
            values.append(Decimal(str(value)) if column == "threshold" and value is not None else value)
        async with self.pool.acquire() as conn:
            try:
                row = await conn.fetchrow(self._sql(SQL_UPDATE_WATCH), *values)
            except asyncpg.UniqueViolationError as exc:
                raise DuplicateWatch(str(merged.get("url"))) from exc
        return _watch_out(row) if row else None

    async def delete_watch(self, tenant_id: str, watch_id: str) -> bool:
        async with self.pool.acquire() as conn:
            await ensure_tables(conn, self.schema)
            result = await conn.execute(
                self._sql(SQL_DELETE_WATCH),
                uuid.UUID(str(tenant_id)),
                uuid.UUID(str(watch_id)),
            )
        return result.endswith(" 1")

    async def enabled_watches(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = await self.list_watches(tenant_id)
        return [row for row in rows if row["enabled"]]

    async def add_observation(self, row: dict[str, Any]) -> dict[str, Any]:
        async with self.pool.acquire() as conn:
            await ensure_tables(conn, self.schema)
            async with conn.transaction():
                saved = await conn.fetchrow(
                    self._sql(SQL_INSERT_OBS),
                    uuid.UUID(str(row["tenant_id"])),
                    row["url"],
                    row["ts"],
                    Decimal(str(row["price"])) if row.get("price") is not None else None,
                    row.get("currency"),
                    row.get("availability"),
                    row.get("method"),
                    row.get("evidence"),
                )
                from rig_watch import record_observation
                await record_observation(conn, self.schema, _obs_out(saved))
        return _obs_out(saved)

    async def list_observations(self, tenant_id: str, url: str) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            await ensure_tables(conn, self.schema)
            rows = await conn.fetch(self._sql(SQL_LIST_OBS), uuid.UUID(str(tenant_id)), url)
        return [_obs_out(row) for row in rows]

    async def latest_observation(self, tenant_id: str, url: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            await ensure_tables(conn, self.schema)
            row = await conn.fetchrow(self._sql(SQL_LATEST_OBS), uuid.UUID(str(tenant_id)), url)
        return _obs_out(row) if row else None

    async def previous_observation(self, tenant_id: str, url: str, current_id: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            await ensure_tables(conn, self.schema)
            row = await conn.fetchrow(
                self._sql(SQL_PREVIOUS_OBS),
                uuid.UUID(str(tenant_id)),
                url,
                uuid.UUID(str(current_id)),
                list(_SKIP_METHODS),
            )
        return _obs_out(row) if row else None

    async def rig_skus(self, tenant_id: str, watch_id: str) -> list[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(f"SELECT sku FROM {self.schema}.rig_targets WHERE tenant_id=$1 AND watch_id=$2 ORDER BY sku",
                                    uuid.UUID(str(tenant_id)), uuid.UUID(str(watch_id)))
        return [row['sku'] for row in rows]

    async def add_alert(self, row: dict[str, Any]) -> dict[str, Any]:
        async with self.pool.acquire() as conn:
            await ensure_tables(conn, self.schema)
            # The alert and its delivery receipt commit together.
            async with conn.transaction():
                return await self._save_alert(conn, row)

    async def _save_alert(self, conn: Any, row: dict[str, Any]) -> dict[str, Any]:
        saved = await conn.fetchrow(
            self._sql(SQL_INSERT_ALERT),
            uuid.UUID(str(row["tenant_id"])), row["url"], row["ts"],
            row["kind"], row.get("before"), row.get("after"),
        )
        letter = letter_for(row)
        if letter:
            await conn.execute(self._sql("""
                INSERT INTO {schema}.alert_mail (tenant_id, alert_id, url, kind, letter)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                ON CONFLICT (tenant_id, alert_id) DO NOTHING
            """), uuid.UUID(str(row["tenant_id"])), saved["id"], row["url"], row["kind"], json.dumps(letter))
        return _alert_out(saved)

    async def pending_mail(self, tenant_id: str, now: datetime) -> list[str]:
        async with self.pool.acquire() as conn:
            await ensure_tables(conn, self.schema)
            rows = await conn.fetch(self._sql("""
                SELECT alert_id FROM {schema}.alert_mail
                WHERE tenant_id = $1 AND status = 'pending' AND next_attempt_at <= $2
                ORDER BY next_attempt_at, alert_id LIMIT 3
            """), uuid.UUID(str(tenant_id)), now)
        return [str(row["alert_id"]) for row in rows]

    async def claim_mail(self, tenant_id: str, alert_id: str, now: datetime) -> dict | None:
        async with self.pool.acquire() as conn:
            # A committed sending state prevents concurrent workers and restarts
            # from sending this alert again, including after an uncertain send.
            row = await conn.fetchrow(self._sql("""
                UPDATE {schema}.alert_mail AS m SET status = 'sending', updated_at = $3,
                    attempts = attempts + 1
                WHERE tenant_id = $1 AND alert_id = $2 AND status = 'pending' AND next_attempt_at <= $3
                RETURNING url, kind, letter, EXISTS (
                    SELECT 1 FROM {schema}.watchlist w WHERE w.tenant_id = m.tenant_id
                    AND w.url = m.url AND w.enabled AND CASE m.kind
                        WHEN 'price' THEN w.alert_price WHEN 'stock' THEN w.alert_stock
                        WHEN 'new_product' THEN w.alert_new_product WHEN 'below_cost' THEN w.alert_below_cost
                        WHEN 'market_gap' THEN w.alert_market_gap ELSE false END
                ) AS allowed
            """), uuid.UUID(str(tenant_id)), uuid.UUID(str(alert_id)), now)
        if row is None:
            return None
        return {"url": row["url"], "kind": row["kind"], "allowed": row["allowed"],
                "letter": json.loads(row["letter"]) if isinstance(row["letter"], str) else dict(row["letter"])}

    async def finish_mail(self, tenant_id: str, alert_id: str, state: str, now: datetime) -> None:
        if state not in {"pending", "sent", "uncertain", "suppressed"}:
            raise ValueError("invalid mail state")
        async with self.pool.acquire() as conn:
            await conn.execute(self._sql("""
                UPDATE {schema}.alert_mail SET status = $3, updated_at = $4,
                    sent_at = CASE WHEN $3 = 'sent' THEN $4 ELSE sent_at END,
                    next_attempt_at = $4 + least(3600, 60 * power(2, least(attempts - 1, 6))) * interval '1 second'
                WHERE tenant_id = $1 AND alert_id = $2 AND status = 'sending'
            """), uuid.UUID(str(tenant_id)), uuid.UUID(str(alert_id)), state, now)

    async def list_alerts(self, tenant_id: str, url: str | None, kind: str | None = None) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            await ensure_tables(conn, self.schema)
            rows = await conn.fetch(self._sql(SQL_LIST_ALERTS), uuid.UUID(str(tenant_id)), url, kind)
        return [_alert_out(row) for row in rows]

    async def check_open(self, tenant_id: str, url: str) -> bool:
        async with self.pool.acquire() as conn:
            found = await conn.fetchval(self._sql(SQL_CHECK_OPEN), uuid.UUID(str(tenant_id)), url)
        return found is not None

    async def enqueue_check(self, tenant_id: str, url: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                self._sql(SQL_ENQUEUE_CHECK),
                uuid.UUID(str(tenant_id)),
                json.dumps({"url": url}),
            )


class MemoryStore(MemoryPlaybooks, MemoryAlerts):
    """In-memory stand-in used by tests. Same tenant filter as the SQL."""

    def __init__(self) -> None:
        self.watches: list[dict[str, Any]] = []
        self.observations: list[dict[str, Any]] = []
        self.alerts: list[dict[str, Any]] = []
        self.mail: dict[tuple[str, str], dict] = {}
        self.jobs: list[dict[str, Any]] = []
        self.schedules: list[dict[str, Any]] = []

    async def create_watch(self, row: dict[str, Any]) -> dict[str, Any]:
        if any(item["tenant_id"] == str(row["tenant_id"]) and item["url"] == row["url"] for item in self.watches):
            raise DuplicateWatch(row["url"])
        saved = {
            "id": str(uuid.uuid4()),
            "tenant_id": str(row["tenant_id"]),
            "url": row["url"],
            "label": row["label"],
            "engine": row["engine"],
            "every": row["every"],
            "enabled": bool(row["enabled"]),
            "threshold": _plain(Decimal(str(row["threshold"]))),
            "alert_price": bool(row.get("alert_price", True)),
            "alert_stock": bool(row.get("alert_stock", True)),
            "alert_new_product": bool(row.get("alert_new_product", True)),
            "alert_below_cost": bool(row.get("alert_below_cost", True)),
            "alert_market_gap": bool(row.get("alert_market_gap", True)),
        }
        self.watches.append(saved)
        await self._ensure_dispatch(saved["tenant_id"])
        return dict(saved)

    async def _ensure_dispatch(self, tenant_id: str) -> None:
        key = (tenant_id, "dispatch", "* * * * *")
        if key not in {(item["tenant_id"], item["kind"], item["cron"]) for item in self.schedules}:
            self.schedules.append({"tenant_id": tenant_id, "kind": "dispatch", "cron": "* * * * *"})

    async def list_watches(self, tenant_id: str) -> list[dict[str, Any]]:
        return [dict(item) for item in self.watches if item["tenant_id"] == str(tenant_id)]

    async def get_watch(self, tenant_id: str, url: str) -> dict[str, Any] | None:
        for item in self.watches:
            if item["tenant_id"] == str(tenant_id) and item["url"] == url:
                return dict(item)
        return None

    async def get_watch_by_id(self, tenant_id: str, watch_id: str) -> dict[str, Any] | None:
        for item in self.watches:
            if item["tenant_id"] == str(tenant_id) and item["id"] == str(watch_id):
                return dict(item)
        return None

    async def update_watch(self, tenant_id: str, watch_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        for item in self.watches:
            if item["tenant_id"] == str(tenant_id) and item["id"] == str(watch_id):
                item.update(fields)
                if "threshold" in fields:
                    item["threshold"] = _plain(Decimal(str(fields["threshold"])))
                return dict(item)
        return None

    async def delete_watch(self, tenant_id: str, watch_id: str) -> bool:
        before = len(self.watches)
        self.watches = [
            item for item in self.watches
            if not (item["tenant_id"] == str(tenant_id) and item["id"] == str(watch_id))
        ]
        return len(self.watches) != before

    async def enabled_watches(self, tenant_id: str) -> list[dict[str, Any]]:
        return [item for item in await self.list_watches(tenant_id) if item["enabled"]]

    async def add_observation(self, row: dict[str, Any]) -> dict[str, Any]:
        saved = {
            "id": str(uuid.uuid4()),
            "tenant_id": str(row["tenant_id"]),
            "url": row["url"],
            "ts": _stamp(row["ts"]),
            "price": _money(row.get("price")),
            "currency": row.get("currency"),
            "availability": row.get("availability"),
            "method": row.get("method"),
            "evidence": row.get("evidence"),
            **_reading_fields(row.get("evidence")),
        }
        self.observations.append(saved)
        return dict(saved)

    def _obs_for(self, tenant_id: str, url: str) -> list[dict[str, Any]]:
        rows = [
            item for item in self.observations
            if item["tenant_id"] == str(tenant_id) and item["url"] == url
        ]
        return sorted(rows, key=lambda item: item["ts"], reverse=True)

    async def list_observations(self, tenant_id: str, url: str) -> list[dict[str, Any]]:
        return [dict(item) for item in self._obs_for(tenant_id, url)]

    async def latest_observation(self, tenant_id: str, url: str) -> dict[str, Any] | None:
        rows = self._obs_for(tenant_id, url)
        return dict(rows[0]) if rows else None

    async def previous_observation(self, tenant_id: str, url: str, current_id: str) -> dict[str, Any] | None:
        for item in self._obs_for(tenant_id, url):
            if item["id"] == str(current_id):
                continue
            if item.get("method") in _SKIP_METHODS:
                continue
            return dict(item)
        return None

    async def add_alert(self, row: dict[str, Any]) -> dict[str, Any]:
        saved = {
            "id": str(uuid.uuid4()),
            "tenant_id": str(row["tenant_id"]),
            "url": row["url"],
            "ts": _stamp(row["ts"]),
            "kind": row["kind"],
            "before": row.get("before"),
            "after": row.get("after"),
        }
        self.alerts.append(saved)
        letter = letter_for(row)
        if letter:
            self.mail[(saved["tenant_id"], saved["id"])] = {
                "url": saved["url"], "kind": saved["kind"], "letter": letter,
                "status": "pending", "attempts": 0, "next_attempt_at": _parse_ts(saved["ts"]),
            }
        return dict(saved)

    async def pending_mail(self, tenant_id: str, now: datetime) -> list[str]:
        return [alert_id for (tenant, alert_id), row in self.mail.items()
                if tenant == str(tenant_id) and row["status"] == "pending" and row["next_attempt_at"] <= now][:3]

    async def claim_mail(self, tenant_id: str, alert_id: str, now: datetime) -> dict | None:
        row = self.mail.get((str(tenant_id), str(alert_id)))
        if not row or row["status"] != "pending" or row["next_attempt_at"] > now:
            return None
        row.update(status="sending", attempts=row["attempts"] + 1)
        watch = next((w for w in self.watches if w["tenant_id"] == str(tenant_id) and w["url"] == row["url"]), None)
        allowed = bool(watch and watch.get("enabled") and watch.get("alert_" + row["kind"], True))
        return {**{key: row[key] for key in ("url", "kind", "letter")}, "allowed": allowed}

    async def finish_mail(self, tenant_id: str, alert_id: str, state: str, now: datetime) -> None:
        row = self.mail.get((str(tenant_id), str(alert_id)))
        if row and row["status"] == "sending":
            row.update(status=state, next_attempt_at=now + timedelta(seconds=min(3600, 60 * 2 ** min(row["attempts"] - 1, 6))))
            if state == "sent":
                row["sent_at"] = now

    async def list_alerts(self, tenant_id: str, url: str | None, kind: str | None = None) -> list[dict[str, Any]]:
        rows = [item for item in self.alerts if item["tenant_id"] == str(tenant_id)]
        if url is not None:
            rows = [item for item in rows if item["url"] == url]
        if kind is not None:
            rows = [item for item in rows if item["kind"] == kind]
        return [dict(item) for item in rows]

    async def check_open(self, tenant_id: str, url: str) -> bool:
        return any(
            item["tenant_id"] == str(tenant_id)
            and item["url"] == url
            and item["status"] in {"queued", "running"}
            for item in self.jobs
        )

    async def enqueue_check(self, tenant_id: str, url: str) -> None:
        self.jobs.append({
            "tenant_id": str(tenant_id),
            "url": url,
            "kind": "check",
            "status": "queued",
        })


def _save_png(files: Any, tenant_id: str, png_base64: str | None, stamp: datetime) -> str | None:
    if not png_base64 or files is None:
        return None
    import base64
    try:
        raw = base64.b64decode(png_base64)
    except Exception:  # noqa: BLE001
        return None
    if not raw.startswith(b"\x89PNG") or len(raw) > 1_500_000:
        return None
    name = f"price-{int(stamp.timestamp())}.png"
    return str(files.save_file(tenant_id, name, raw))


async def external_observation(store, tenant_id, watch, item, method, receipt, clock):
    url = watch["url"]
    latest = await store.latest_observation(tenant_id, url)
    fields = _reading_fields(latest.get("evidence") if latest else None)
    name = _product_name(item.get("name"))
    issue = "missing_name" if not name else None
    if not issue and not re.fullmatch(r"[A-Za-z]{3}", str(item.get("currency") or "")):
        issue = "missing_currency"
    quantity = item.get("quantity")
    availability = ("in_stock" if Decimal(str(quantity)) > 0 else "out_of_stock") if quantity is not None else None
    price = None if issue else item.get("price")
    saved_method = "unread" if issue else method
    evidence = _reading_evidence(json.dumps({"method": method, "receipt": receipt}, default=str), name,
                                 fields["product_name"] or fields["last_product_name"], issue)
    current = await store.add_observation(_obs(tenant_id, url, clock(), price, item.get("currency"),
        availability, saved_method, evidence))
    previous = await store.previous_observation(tenant_id, url, current["id"])
    stock_before = await _last_known_stock(store, tenant_id, url, current["id"])
    found = alerts_for(previous, current, watch.get("threshold") or 0, stock_before,
                       price_enabled=watch.get("alert_price", True), stock_enabled=watch.get("alert_stock", True))
    saved = [await store.add_alert({"tenant_id": tenant_id, "url": url, "ts": clock(), **alert,
        "product_name": current.get("product_name"), "currency": current.get("currency")}) for alert in found]
    from rig_watch import publish_price_changed
    await publish_price_changed(store, tenant_id, watch, previous, current)
    return {"status": "can't read" if issue else "observed", "url": url, "method": saved_method, "price": price,
            "stock": availability, "alerts": saved, "observation": current, "receipt": receipt}


async def run_check(
    store, tenant_id, watch, agent, render, extract, ocr, fetch_text, limiter,
    files=None, now=None, feed=None, product_context=None, skills=None,
):
    captured = {}

    async def rendered(*args):
        page = await render(*args)
        if (page.get("ok") and not page.get("challenge") and not page.get("blocked_by_check")
                and page.get("status") not in {401, 403, 429} and not page.get("portal_item")):
            captured["html"] = page.get("html") or ""
        return page

    async def extracted(html, url):
        value = await extract(html, url)
        captured["items"] = value.get("items") or []
        captured["listing"] = listing_products(html, url, captured["items"])
        return value

    async def read_image(*args):
        # Listing membership is already confirmed by code. Its lack of a single
        # product price must not invoke the existing paid screenshot fallback.
        stock_only = not watch.get("alert_price", True) and not watch.get("alert_below_cost", True)
        stock = _known_stock(_stock_for("unread", None, captured.get("html", "")))
        if captured.get("listing") is not None or (stock_only and stock is not None):
            return {"items": []}
        return await ocr(*args)

    result = await _run_check(store, tenant_id, watch, agent, rendered, extracted, read_image,
                              fetch_text, limiter, files=files, now=now, feed=feed, skills=skills)
    observation = result.get("observation")
    if observation is not None:
        listing = captured.get("listing") if captured.get("html") else None
        context = None
        if product_context is not None:
            try:
                context = await product_context(str(tenant_id), watch["url"])
            except (httpx.HTTPError, ValueError):
                logging.getLogger(__name__).warning("Rig alert context unavailable.")
        result["alerts"].extend(await store.product_alerts(tenant_id, observation, listing, context))
    return result


async def _run_check(
    store: Any,
    tenant_id: str,
    watch: dict[str, Any],
    agent: str,
    render: Any,
    extract: Any,
    ocr: Any,
    fetch_text: Any,
    limiter: DomainLimiter,
    files: Any = None,
    now: Any = None,
    feed: Any = None,
    skills: Any = None,
) -> dict[str, Any]:
    async def playbook_call(operation, **payload):
        output = await skills({"operation": operation, **payload}) if skills else None
        if output is not None:
            return output["value"]
        return {"public_page": public_page, "public_marker": public_marker,
                "learn_layout": learn_layout, "read_page": read_page}[operation](**payload)

    clock = now or (lambda: datetime.now(timezone.utc))
    url = watch["url"]
    if str(watch.get("tenant_id")) != str(tenant_id):
        return {"status": "skipped", "url": url, "alerts": []}
    previous_row = await store.latest_observation(tenant_id, url)
    previous_fields = _reading_fields(previous_row.get("evidence") if previous_row else None)
    last_name = previous_fields["product_name"] or previous_fields["last_product_name"]
    if feed is not None:
        try:
            official = await feed(str(tenant_id), url)
        except FeedUnavailable:
            official = {"found": False}
        if official.get("found") is True:
            return await external_observation(store, tenant_id, watch, official["item"], "official_feed", official["receipt"], clock)
        if official.get("found") is not False:
            raise ValueError("Official feed did not confirm whether the product exists.")
    host = host_of(url)
    # Public reads do not fetch or enforce robots.txt. The browser keeps the
    # access boundary and stops blocked hosts for the room/tenant/task run.
    await limiter.wait(host)
    page = await render(url, session_for_url(url), agent, str(tenant_id))
    if page.get("challenge") or page.get("blocked_by_check") or page.get("status") in {401, 403, 429}:
        detail = _reading_evidence("blocked", None, last_name) if last_name else "blocked"
        row = await store.add_observation(_obs(tenant_id, url, clock(), None, None, None, "blocked", detail))
        return {"status": "blocked", "url": url, "alerts": [], "observation": row}
    if page.get("portal_item"):
        return await external_observation(store, tenant_id, watch, page["portal_item"], "private_portal",
            {"source": "client_connection", "fetched_at": clock().isoformat(), "private": True}, clock)
    html = page.get("html") or ""
    if not page.get("ok") or not html:
        detail = str(page.get("error") or "unread")[:300]
        if last_name:
            detail = _reading_evidence(detail, None, last_name)
        row = await store.add_observation(_obs(tenant_id, url, clock(), None, None, None, "error", detail))
        return {"status": "error", "url": url, "alerts": [], "observation": row}

    extracted = await extract(html, url)
    items = list(extracted.get("items") or [])
    stored = layout_from_evidence(previous_row.get("evidence") if previous_row else None)
    shared_page = await playbook_call("public_page", url=url, page=page)
    playbook, attempted, decision = None, None, None
    if shared_page:
        try:
            playbook = await store.get_playbook(shared_page[0])
        except (asyncpg.PostgresError, OSError, ValueError):
            logging.getLogger(__name__).warning("Domain playbook unavailable; using the URL layout.")
    if stored is None and playbook and playbook['marker']:
        trial = await playbook_call('read_page', html=html, signature=playbook['marker'], extracted=extracted)
        attempted = trial['status'] == 'read'
        if attempted:
            decision = trial
    if decision is None:
        if stored is None or stored.get("primary") == "ocr":
            signature = await playbook_call("learn_layout", html=html, extracted=extracted)
            if not signature.get("primary"):
                signature = stored
        else:
            signature = stored
        decision = await playbook_call("read_page", html=html, signature=signature, extracted=extracted)
        if playbook and await playbook_call('public_marker', layout=stored) == playbook['marker'] and stored is not None:
            attempted = decision['status'] == 'read'
    png = page.get("png_base64")
    layout = decision.get("layout")
    chosen = None
    if decision["status"] == "read":
        method = decision.get("method") or extracted.get("method")
        price = decision.get("price")
        chosen = next((item for item in items if item.get("price") == price), None)
    else:
        method = None
        price = None
        # A screenshot is only a fallback before a layout is known. It is not a guess
        # after the known layout has gone.
        if stored is None and png:
            read = await ocr(png, url)
            priced = [item for item in (read.get("items") or []) if item.get("price")]
            if priced:
                chosen = priced[0]
                method = "ocr"
                price = chosen.get("price")
                layout = {"primary": "ocr"}
    if method is None:
        method = "unread"
        price = None
        chosen = None
    currency = chosen.get("currency") if chosen else None
    availability = _stock_for(method, chosen.get("availability") if chosen else None, html)
    name = _product_name(chosen.get("name")) if chosen else None
    if chosen is None:
        names = {_product_name(item.get("name")) for item in items}
        names.discard(None)
        name = next(iter(names)) if len(names) == 1 else None
    name = name or _page_name(html)
    issue = None
    if not name:
        issue = "missing_name"
    elif price is None:
        issue = "missing_price"
    elif not currency or not re.fullmatch(r"[A-Za-z]{3}", str(currency)):
        issue = "missing_currency"
    elif _known_stock(availability) is None:
        issue = "missing_stock"
    if issue in {"missing_name", "missing_currency"}:
        method, price = "unread", None
    saved_file = _save_png(files, str(tenant_id), png if isinstance(png, str) else None, clock())
    current = await store.add_observation(_obs(
        tenant_id, url, clock(), price, currency, availability, method,
        _reading_evidence(_evidence(method, price, saved_file, layout, availability), name, last_name, issue),
    ))
    if shared_page and playbook:
        confirmed = await playbook_call('public_marker', layout=layout) if not issue and decision['status'] == 'read' and method != 'ocr' else None
        try:
            await store.record_playbook(*shared_page, playbook['generation'], attempted, confirmed)
        except (asyncpg.PostgresError, OSError, ValueError):
            logging.getLogger(__name__).warning("Domain layout confirmation was not saved.")
    previous = await store.previous_observation(tenant_id, url, current["id"])
    stock_before = await _last_known_stock(store, tenant_id, url, current["id"])
    found = alerts_for(previous, current, watch.get("threshold") or 0, stock_before,
                       price_enabled=watch.get("alert_price", True), stock_enabled=watch.get("alert_stock", True))
    # An incomplete public-page check must not send a price or stock alert.
    if _known_stock(availability) is None:
        found = []
    saved_alerts = []
    for alert in found:
        saved_alerts.append(await store.add_alert({
            "tenant_id": tenant_id,
            "url": url,
            "ts": clock(),
            "kind": alert["kind"],
            "before": alert["before"],
            "after": alert["after"],
            "product_name": current.get("product_name"),
            "currency": current.get("currency"),
        }))
    from rig_watch import publish_price_changed
    await publish_price_changed(store, tenant_id, watch, previous, current)
    return {
        "status": "can't read" if method == "unread" else "observed",
        "url": url,
        "method": method,
        "price": price,
        "stock": availability,
        "alerts": saved_alerts,
        "observation": current,
    }


async def dispatch_tenant(store: Any, tenant_id: str, now: datetime) -> dict[str, Any]:
    queued: list[str] = []
    for watch in await store.enabled_watches(tenant_id):
        last = await store.latest_observation(tenant_id, watch["url"])
        last_ts = _parse_ts(last.get("ts")) if last else None
        if not watch_is_due(str(watch["every"]), now, last_ts):
            continue
        if await store.check_open(tenant_id, watch["url"]):
            continue
        await store.enqueue_check(tenant_id, watch["url"])
        queued.append(watch["url"])
    return {"queued": queued}
