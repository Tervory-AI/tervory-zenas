"""Deterministic listing and matched-product alerts; no requests or models.

Product matches come from Tank's tenant-owned URL/SKU links, never from names
or a competitor's SKU. Missing/failed readings are unknown, not out of stock.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import UUID


@asynccontextmanager
async def room(conn, schema):
    # schema is the room's fixed setting (a disposable schema in offline tests).
    # It is never SQL text or a request parameter. All queries below are fixed.
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("invalid schema")
    async with conn.transaction():
        previous = await conn.fetchval("SELECT pg_catalog.current_setting('search_path')")
        await conn.execute("SELECT pg_catalog.set_config('search_path', $1, true)",
                           '"' + schema + '", pg_catalog, pg_temp')
        yield conn
        await conn.execute("SELECT pg_catalog.set_config('search_path', $1, true)", previous)


async def ensure_tables(conn, schema):
    async with room(conn, schema):
        await conn.execute(Path(__file__).with_name("schema.sql").read_text())


class StructuredListing(HTMLParser):
    """Read listing membership, not prices. No extra request or new extractor."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inside = False
        self.parts = []
        self.documents = []
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self.inside, self.parts = True, []

    def handle_data(self, data):
        if self.inside:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.inside:
            self.inside = False
            try:
                self.documents.append(json.loads("".join(self.parts)))
            except (ValueError, RecursionError):
                self.invalid = True


def _types(node):
    value = node.get("@type", [])
    return {str(t).rsplit("/", 1)[-1] for t in (value if isinstance(value, list) else [value])}


def _url(value, base):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(urljoin(base, value))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
            return None
        return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, parsed.query, ""))
    except ValueError:
        return None


def listing_products(html, url, extracted):
    """None means unconfirmed; {} is an explicitly empty structured listing.

    Product IDs are their own URLs, then GTIN/SKU, then name plus price. Titles
    alone are never identities. Incomplete lists retain the baseline. JSON-LD
    ItemList/ListItem and Product arrays, plus the existing microdata reader's
    items, cover confirmed structured listings without guessing from HTML text.
    """
    parser = StructuredListing()
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, RecursionError):
        return None
    if parser.invalid or parser.inside:
        return None
    products, lists = [], []

    def walk(node):
        if isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            types = _types(node)
            if "ItemList" in types:
                lists.append(node)
                return
            if "Product" in types:
                products.append(node)
                return  # Offers/recommendations aren't another product listing.
            for key in ("@graph", "mainEntity"):
                walk(node.get(key))
    try:
        walk(parser.documents)
    except RecursionError:
        return None
    if lists:
        products = []
        for listing in lists:
            entries = listing.get("itemListElement")
            if not isinstance(entries, list):
                return None
            # Partial/paginated structured lists must not create removal/re-add noise.
            total = listing.get("numberOfItems")
            if total is not None and str(total) != str(len(entries)):
                return None
            for entry in entries:
                if not isinstance(entry, dict):
                    return None
                product = entry.get("item", entry)
                if not isinstance(product, dict):
                    return None
                products.append(product)
    elif not products:
        products = list(extracted or [])
    page_url = _url(url, url)
    # A standalone Product or OpenGraph/price fallback is not evidence of a
    # category. Its canonical URL can differ from the watched URL. Explicit
    # ItemList pages can be empty/single; inferred listings need multiple items.
    if not lists and len(products) < 2:
        return None
    result = {}
    for product in products:
        name = " ".join(str(product.get("name") or "").split())
        address = _url(product.get("url") or product.get("@id"), url)
        # The existing microdata reader uses the category URL when it has no
        # individual offer URL. That fallback is never a product identity.
        identity = address if address != page_url else None
        if not identity:
            for key in ("gtin", "gtin13", "gtin14", "gtin12", "gtin8", "sku"):
                value = product.get(key)
                if isinstance(value, (str, int)) and str(value).strip():
                    identity = key + ":" + str(value).strip()
                    break
        if not identity:
            price = money(product.get("price"))
            if price is not None and name:
                identity = "name_price:" + json.dumps([name, str(price.normalize()), product.get("currency")])
        if not identity or not name or re.fullmatch(r"untitled\s+page", name, re.I):
            return None
        if identity in result and result[identity]["name"] != name:
            return None
        result[identity] = {"name": name}
    if not lists and not result:
        return None
    return result


def money(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (ValueError, InvalidOperation):
        return None


def evaluate(tenant, watches, states, observation, listing, context):
    """Pure transition calculation. Muted types advance state without replay."""
    from watch import _known_stock, _parse_ts, watch_is_due

    url = observation["url"]
    watch = next((w for w in watches if w["url"] == url and w["enabled"]), None)
    if watch is None:
        return {}, []
    key = "watch:" + str(watch["id"]) + ":" + url
    old = states.get(key, {})
    stamp = _parse_ts(observation["ts"])
    if old.get("ts") and _parse_ts(old["ts"]) >= stamp:
        return {}, []  # concurrent/late delivery of the same check
    current = {**old, "ts": stamp.isoformat(), "stock": None}
    named = bool(observation.get("product_name"))
    price_confirmed = (named and observation.get("method") not in {None, "unread", "error", "blocked", "robots", "none"}
                       and observation.get("issue") in {None, "missing_stock"})
    # Price/currency and stock are independent facts. An unavailable price does
    # not invalidate a stock phrase already confirmed by the existing reader.
    stock_confirmed = (named and observation.get("method") not in {None, "error", "blocked", "robots", "none"}
                       and observation.get("issue") in {None, "missing_price", "missing_currency", "missing_stock"})
    if stock_confirmed:
        current["stock"] = _known_stock(observation.get("availability"))
    updates, alerts = {key: current}, []

    def emit(kind, owner=watch, **fields):
        if owner.get("alert_" + kind, True):
            alerts.append({"tenant_id": tenant, "url": owner["url"], "ts": stamp,
                           "kind": kind, **fields})

    if listing is not None:
        if "products" in old:
            for identity, product in listing.items():
                if identity not in old["products"]:
                    emit("new_product", product_name=product["name"], after=product["name"])
        current["products"] = listing

    if not context or context.get("tenant_id") != str(tenant):
        return updates, alerts
    product = context.get("product")
    if not product or url not in context.get("urls", []) or url == product.get("url"):
        return updates, alerts
    sku = product["sku"]
    price, cost = money(observation.get("price")), money(product.get("cost"))
    below_key = "below:" + sku
    if (price_confirmed and price is not None and cost is not None
            and observation.get("currency") == product.get("cost_currency")):
        below = price < cost
        if below and not old.get(below_key, False):
            emit("below_cost", product_name=observation["product_name"],
                 before=str(cost), after=str(price), currency=product["cost_currency"])
        current[below_key] = below

    peers = [w for w in watches if w["enabled"] and w["url"] in context["urls"]
             and w["url"] != product.get("url")]
    readings = []
    for peer in peers:
        peer_key = "watch:" + str(peer["id"]) + ":" + peer["url"]
        reading = updates.get(peer_key, states.get(peer_key, {}))
        checked = _parse_ts(reading["ts"]) if reading.get("ts") else None
        readings.append(None if checked is None or watch_is_due(peer["every"], stamp, checked)
                        else reading.get("stock"))
    gap_key = "gap:" + sku
    gap = None
    if product.get("active") is False or any(s is not None and s != "OutOfStock" for s in readings):
        gap = False
    elif product.get("active") is True and readings and all(s == "OutOfStock" for s in readings):
        gap = True
    if gap is not None:
        if gap and not states.get(gap_key, {}).get("active", False):
            owners = sorted((w for w in peers if w.get("alert_market_gap", True)), key=lambda w: w["url"])
            if owners:
                emit("market_gap", owner=owners[0], product_name=product.get("name"),
                     before="InStock", after="OutOfStock")
        updates[gap_key] = {"active": gap}
    return updates, alerts


class PgAlerts:
    async def product_alerts(self, tenant, observation, listing, context):
        from watch import ensure_tables, SQL_LIST_WATCHES, _watch_out
        async with self.pool.acquire() as conn:
            await ensure_tables(conn, self.schema)
            async with room(conn, self.schema):
                await conn.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('tiger-alerts:' || $1,0))",
                                   str(tenant))
                watches = [_watch_out(w) for w in await conn.fetch(self._sql(SQL_LIST_WATCHES), UUID(str(tenant)))]
                rows = await conn.fetch("SELECT key, data FROM alert_state WHERE tenant_id=$1", UUID(str(tenant)))
                states = {r["key"]: json.loads(r["data"]) if isinstance(r["data"], str) else r["data"] for r in rows}
                updates, alerts = evaluate(str(tenant), watches, states, observation, listing, context)
                saved = [await self._save_alert(conn, alert) for alert in alerts]
                for key, data in updates.items():
                    await conn.execute("""INSERT INTO alert_state(tenant_id,key,data) VALUES($1,$2,$3::jsonb)
                        ON CONFLICT(tenant_id,key) DO UPDATE SET data=EXCLUDED.data""",
                        UUID(str(tenant)), key, json.dumps(data))
                return saved


class MemoryAlerts:
    async def product_alerts(self, tenant, observation, listing, context):
        if not hasattr(self, "alert_states"):
            self.alert_states, self.alert_locks = {}, {}
        tenant = str(tenant)
        async with self.alert_locks.setdefault(tenant, asyncio.Lock()):
            states = self.alert_states.setdefault(tenant, {})
            updates, alerts = evaluate(tenant, await self.list_watches(tenant), states, observation, listing, context)
            # Match the database transaction if an alert/mail write fails.
            old_alerts, old_mail = deepcopy(self.alerts), deepcopy(self.mail)
            try:
                saved = [await self.add_alert(alert) for alert in alerts]
            except Exception:
                self.alerts, self.mail = old_alerts, old_mail
                raise
            states.update(updates)
            return saved
