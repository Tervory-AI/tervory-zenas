"""Tenant-scoped catalogue store. Every read and write takes a tenant_id."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import UUID

from chassis.tenant_db import TenantPool

from rigbox.logic import public_product
from rigbox.recall import SEARCH_RECALL_CAP
from rigbox.retailer_store import RetailerMemoryStore, RetailerPgStore
from rigbox.shopper_attributes import PRIVATE_KEY_PATTERN, SHOPPER_ATTRIBUTES

_log = logging.getLogger("rigbox.store")

_SEARCH_KEYS = sorted(SHOPPER_ATTRIBUTES)

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"

_PRODUCTS = """
SELECT sku, gtin, mpn, supply_eligible, name, brand, category, price, currency, image_url, url, family,
       attributes, active
FROM agent_rig.products
WHERE tenant_id = $1
"""

_SEARCH_TEMPLATE = """
SELECT sku, gtin, mpn, supply_eligible, name, brand, category, price, currency, image_url, url, family,
       attributes, active
FROM agent_rig.products
WHERE tenant_id = $1
  AND active = true
  AND (
    to_tsvector('simple', coalesce(name, '') || ' ' || coalesce(brand, '') || ' ' || coalesce(sku, '') || ' ' || {shopper_text})
      @@ plainto_tsquery('simple', $2)
    OR similarity(coalesce(name, ''), $2) > 0.2
    OR similarity(coalesce(brand, ''), $2) > 0.2
    OR similarity(coalesce(sku, ''), $2) > 0.2
  )
ORDER BY ts_rank(
    to_tsvector('simple', coalesce(name, '') || ' ' || coalesce(brand, '') || ' ' || coalesce(sku, '') || ' ' || {shopper_text}),
    plainto_tsquery('simple', $2)
  ) DESC,
  similarity(coalesce(name, ''), $2) DESC,
  sku ASC
LIMIT $3
"""

_SEARCH_FTS_TEMPLATE = """
SELECT sku, gtin, mpn, supply_eligible, name, brand, category, price, currency, image_url, url, family,
       attributes, active
FROM agent_rig.products
WHERE tenant_id = $1
  AND active = true
  AND to_tsvector('simple', coalesce(name, '') || ' ' || coalesce(brand, '') || ' ' || coalesce(sku, '') || ' ' || {shopper_text})
      @@ plainto_tsquery('simple', $2)
ORDER BY ts_rank(
    to_tsvector('simple', coalesce(name, '') || ' ' || coalesce(brand, '') || ' ' || coalesce(sku, '') || ' ' || {shopper_text}),
    plainto_tsquery('simple', $2)
  ) DESC,
  sku ASC
LIMIT $3
"""

# Search text: name, brand, sku and only allowlisted attributes, never
# attributes::text. Scalars and lists of scalars are indexed; ``stock`` gives
# its shopper fields only; other structured attributes (options, defaults,
# price_history, verified) are not search text. $4 is SHOPPER_ATTRIBUTES.
_SHOPPER_TEXT = """coalesce((
    SELECT string_agg(e.key || ' ' || CASE
        WHEN jsonb_typeof(e.value) IN ('string', 'number', 'boolean') THEN e.value #>> '{}'
        WHEN e.key = 'stock' AND jsonb_typeof(e.value) = 'object'
            THEN concat_ws(' ', e.value ->> 'status', e.value ->> 'location', e.value ->> 'lead', e.value ->> 'label')
        WHEN e.key <> 'verified' AND jsonb_typeof(e.value) = 'array' THEN coalesce((
            SELECT string_agg(x #>> '{}', ' ') FROM jsonb_array_elements(e.value) AS x(value)
            WHERE jsonb_typeof(x.value) IN ('string', 'number')), '')
        ELSE '' END, ' ')
    FROM jsonb_each(CASE WHEN jsonb_typeof(attributes) = 'object' THEN attributes ELSE '{}'::jsonb END) AS e(key, value)
    WHERE e.key = ANY($4::text[])), '')"""
_SEARCH = _SEARCH_TEMPLATE.replace("{shopper_text}", _SHOPPER_TEXT)
_SEARCH_FTS = _SEARCH_FTS_TEMPLATE.replace("{shopper_text}", _SHOPPER_TEXT)


def schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


log = logging.getLogger("agent")

# apply_schema runs at every boot, and ALTER TABLE ... ADD COLUMN IF NOT EXISTS
# takes an ACCESS EXCLUSIVE lock even when the column is already there. Each
# statement gets a lock timeout (SET LOCAL: that statement's transaction only)
# and a few retries with backoff, so a long transaction holding agent_rig.quotes
# fails the boot cleanly instead of queueing every Rig query behind it.
SCHEMA_LOCK_TIMEOUT = "5s"
SCHEMA_LOCK_ATTEMPTS = 4
SCHEMA_LOCK_BACKOFF = 0.5  # seconds, doubled after each timeout


def _lock_not_available(exc: BaseException) -> bool:
    return getattr(exc, "sqlstate", None) == "55P03"


def schema_statements() -> list[str]:
    return [part.strip() for part in schema_sql().split(";") if part.strip()]


def _classified_sql(statement: str) -> str:
    """SQL used to classify a statement.

    ``schema_statements`` splits on semicolons, so leading ``--`` comments and
    the blank lines under them stay attached to the next statement. Postgres
    still runs that statement; classification has to look past the comment.
    """
    kept: list[str] = []
    started = False
    for line in statement.splitlines():
        stripped = line.strip()
        if not started and (not stripped or stripped.startswith("--")):
            continue
        started = True
        kept.append(line)
    return "\n".join(kept).strip()


def _created_schema_name(sql: str) -> str | None:
    """Name from ``CREATE SCHEMA [IF NOT EXISTS] name``, or None."""
    tokens = sql.split()
    if len(tokens) < 3 or tokens[0].upper() != "CREATE" or tokens[1].upper() != "SCHEMA":
        return None
    index = 2
    if (
        index + 2 < len(tokens)
        and tokens[index].upper() == "IF"
        and tokens[index + 1].upper() == "NOT"
        and tokens[index + 2].upper() == "EXISTS"
    ):
        index += 3
    if index >= len(tokens):
        return None
    token = tokens[index].rstrip(";")
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    name: list[str] = []
    for char in token:
        if char.isalnum() or char == "_":
            name.append(char)
        else:
            break
    if not name:
        return None
    return "".join(name).lower()


def _optional_schema_statement(sql: str) -> bool:
    """Extension and trigram-index failures are optional on this role."""
    tokens = sql.split()
    if len(tokens) >= 2 and tokens[0].upper() == "CREATE" and tokens[1].upper() == "EXTENSION":
        return True
    return "GIN_TRGM_OPS" in sql.upper()


def _role_cannot_create_schema(exc: BaseException) -> bool:
    """True when Postgres refused because this role may not create schemas.

    ``CREATE SCHEMA IF NOT EXISTS`` still checks that privilege when the schema
    is already there. The provisioner creates ``agent_rig``; the agent role
    does not have it. The live error is
    ``InsufficientPrivilegeError: permission denied for database tervory``.
    """
    if any(cls.__name__ == "InsufficientPrivilegeError" for cls in type(exc).mro()):
        return True
    if getattr(exc, "sqlstate", None) == "42501":
        return True
    return "permission denied" in str(exc).lower()


def _search_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return datetime.now(timezone.utc)


def _uuid(value: str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _json(value: Any) -> str:
    """JSON text for a jsonb argument.

    asyncpg's jsonb codec sends a string. A dict is rejected before Postgres
    sees it. The string is the JSON object, encoded once.
    """
    return json.dumps(value if value is not None else {})


def _numeric(value: Any) -> Decimal | None:
    """A numeric column. None stays NULL. Floats go through text so 10.10 stays 10.10."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("numeric value is not a number")
    if isinstance(value, Decimal):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("numeric value is not a number") from exc


def _money(value: Any) -> Decimal:
    number = _numeric(value)
    if number is None:
        raise ValueError("numeric value is missing")
    return number


def _timestamptz(value: Any) -> datetime:
    """A timestamptz value. A date-only sheet cell is midnight UTC.

    asyncpg will not accept ``'2026-03-01'`` for a timestamp argument. The
    cast in SQL does not change that: the driver encodes the value first.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _fit_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("part_sku") or ""),
        str(row.get("fits_sku") or ""),
        str(row.get("fits_family") or ""),
    )




class MemoryStore(RetailerMemoryStore):
    """In-memory stand-in. Tests never open a database."""

    def __init__(self) -> None:
        self._products: dict[tuple[str, str], dict[str, Any]] = {}
        self._fits: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.families: dict[tuple[str, str], list[str]] = {}
        self.templates: dict[tuple[str, str], Any] = {}
        self.keys: dict[str, dict[str, Any]] = {}
        self.quotes: list[dict[str, Any]] = []
        self.retailers: dict[str, dict[str, Any]] = {}
        self.feeds: dict[tuple[str, str], dict[str, Any]] = {}
        self.answers: dict[tuple[str, str, str], tuple[str, dict[str, Any]]] = {}
        self.fit_why: dict[tuple[str, str, str], list[str]] = {}
        self.market: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.searches: list[dict[str, Any]] = []
        self.training_events: list[dict[str, Any]] = []
        self.deleted_products: set[tuple[str, str]] = set()
        # Rig-owned Locate orders: (tenant, quote) -> {"order", "purchase_order"}.
        self.locate_orders: dict[tuple[str, str], dict[str, Any]] = {}
        self.locate_order_created: dict[tuple[str, str], datetime] = {}
        # Pricing inputs moved out of attributes at import. Store-only.
        self.private_attributes: dict[tuple[str, str], dict[str, Any]] = {}

    async def deleted_skus(self, tenant_id: str) -> set[str]:
        return {sku for tenant, sku in self.deleted_products if tenant == str(tenant_id)}

    async def restore_products(self, tenant_id: str, skus: list[str]) -> None:
        self.deleted_products.difference_update((str(tenant_id), sku) for sku in skus)

    async def delete_products(self, tenant_id: str, skus: list[str]) -> int:
        """Called inside atomic: catalogue, private rows and recall change together."""
        self._retailer_state()
        tenant, wanted = str(tenant_id), set(skus)
        found = {sku for owner, sku in self._products if owner == tenant and sku in wanted}
        for sku in found:
            del self._products[(tenant, sku)]
            self.deleted_products.add((tenant, sku))
        self.private_costs = {key: row for key, row in self.private_costs.items()
                              if key[0] != tenant or key[1] not in found}
        self.private_attributes = {key: row for key, row in self.private_attributes.items()
                                   if key[0] != tenant or key[1] not in found}
        if hasattr(self, "feed_products"):
            self.feed_products = {key for key in self.feed_products if key[0] != tenant or key[1] not in found}
        if hasattr(self, "watch_receipts"):
            self.watch_receipts = {key: value for key, value in self.watch_receipts.items() if key[0] != tenant or key[1] not in found}
        self._fits = {key: row for key, row in self._fits.items()
                      if key[0] != tenant or (key[1] not in found and key[2] not in found)}
        for (owner, family), members in list(self.families.items()):
            if owner == tenant:
                remaining = [sku for sku in members if sku not in found]
                await self.set_family(tenant, family, remaining)
                if members and not remaining:
                    self._fits = {key: row for key, row in self._fits.items() if key[0] != tenant or key[3] != family}
        if found:
            self._drop_answers(tenant)
            self.searches = [row for row in self.searches
                             if row["tenant_id"] != tenant or not found.intersection(row["ids"])]
        return len(found)

    def _drop_answers(self, tenant_id: str) -> None:
        tenant = str(tenant_id)
        self.answers = {key: value for key, value in self.answers.items() if key[0] != tenant}

    async def product_exists(self, tenant_id: str, sku: str) -> bool:
        return (str(tenant_id), sku) in self._products

    async def upsert_product(self, tenant_id: str, row: dict[str, Any]) -> str:
        key = (str(tenant_id), str(row["sku"]))
        if key in self.deleted_products:
            return "skipped"
        action = "updated" if key in self._products else "added"
        public = {name: value for name, value in row.items() if name != "private_attributes"}
        self._products[key] = {**public, "tenant_id": str(tenant_id), "sku": str(row["sku"])}
        if "private_attributes" in row:
            if row["private_attributes"]:
                self.private_attributes[key] = dict(row["private_attributes"])
            else:
                self.private_attributes.pop(key, None)
        self._drop_answers(tenant_id)
        return action

    async def product_private_attributes(self, tenant_id: str, sku: str) -> dict[str, Any]:
        """Store-only. Never on a shopper path."""
        return dict(self.private_attributes.get((str(tenant_id), sku), {}))

    async def products(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = [row for (tenant, _sku), row in self._products.items() if tenant == str(tenant_id)]
        return sorted(rows, key=lambda row: str(row.get("sku")))

    async def upsert_fit(self, tenant_id: str, row: dict[str, Any]) -> None:
        part, fits_sku, fits_family = _fit_key(row)
        self._fits[(str(tenant_id), part, fits_sku, fits_family)] = {
            **row,
            "tenant_id": str(tenant_id),
            "fits_sku": fits_sku or None,
            "fits_family": fits_family or None,
        }
        self._drop_answers(tenant_id)

    async def fits(self, tenant_id: str) -> list[dict[str, Any]]:
        return [row for key, row in self._fits.items() if key[0] == str(tenant_id)]

    async def set_family(self, tenant_id: str, family: str, members: list[str]) -> None:
        self.families[(str(tenant_id), family)] = list(members)

    async def set_template(self, tenant_id: str, category: str, roles: Any) -> None:
        self.templates[(str(tenant_id), category)] = roles
        self._drop_answers(tenant_id)

    async def templates_for(self, tenant_id: str) -> dict[str, Any]:
        return {category: roles for (tenant, category), roles in self.templates.items() if tenant == str(tenant_id)}

    async def put_key(self, tenant_id: str, key_hash: str, kind: str, origins: list[str]) -> None:
        self.keys[key_hash] = {
            "tenant_id": str(tenant_id),
            "kind": kind,
            "origins": list(origins),
        }

    async def key(self, key_hash: str) -> dict[str, Any] | None:
        row = self.keys.get(key_hash)
        return dict(row) if row else None

    async def put_retailer(
        self,
        tenant_id: str,
        slug: str,
        email: str | None,
        webhook: str | None,
        name: str = "",
        theme: str | None = None,
        quote_notice: dict[str, str] | None = None,
        locate_enabled: bool | None = None,
        blind_ship: bool | None = None,
        auto_order: bool | None = None,
        delivery: dict[str, int] | None = None,
    ) -> None:
        previous = self.retailers.get(str(tenant_id), {})
        theme = theme if theme is not None else self.retailers.get(str(tenant_id), {}).get("theme", "neutral")
        if theme not in {"neutral", "custom-nation"}:
            raise ValueError()
        self.retailers[str(tenant_id)] = {"slug": slug, "email": email, "webhook": webhook, "name": name, "theme": theme,
            "quote_notice": quote_notice if quote_notice is not None else self.retailers.get(str(tenant_id), {}).get("quote_notice"),
            # Optional cross-store fulfilment. Off until the store switches it on.
            "locate_enabled": bool(locate_enabled) if locate_enabled is not None
            else bool(previous.get("locate_enabled", False)),
            # Optional blind ship (Locate orders ship straight to the shopper). Off by default.
            "blind_ship": bool(blind_ship) if blind_ship is not None else bool(previous.get("blind_ship", False)),
            # Optional auto-order (sandbox outbox only). Off by default.
            "shop": copy.deepcopy(previous.get("shop", {})),
            "delivery": copy.deepcopy(delivery if delivery is not None else previous.get("delivery", {})),
            "auto_order": bool(auto_order) if auto_order is not None else bool(previous.get("auto_order", False))}








    async def retailer(self, tenant_id: str) -> dict[str, Any] | None:
        row = self.retailers.get(str(tenant_id))
        return dict(row) if row else None

    async def replace_market(self, tenant_id: str, rows: list[dict[str, Any]]) -> None:
        tenant = str(tenant_id)
        self.market = {key: value for key, value in self.market.items() if key[0] != tenant}
        for row in rows:
            sku = str(row.get("sku") or "")
            source = str(row.get("source") or "")
            self.market[(tenant, sku, source)] = {
                "sku": sku,
                "source": source,
                "price": row.get("price"),
                "currency": str(row.get("currency") or ""),
                "seen_at": str(row.get("seen_at") or ""),
            }
        self._drop_answers(tenant)

    async def market_for(self, tenant_id: str, skus: list[str]) -> list[dict[str, Any]]:
        wanted = {str(sku) for sku in skus}
        tenant = str(tenant_id)
        return [
            dict(row)
            for (owner, sku, _source), row in self.market.items()
            if owner == tenant and sku in wanted
        ]

    async def put_feed(self, tenant_id: str, fmt: str, url: str, currency: str) -> None:
        self.feeds[(str(tenant_id), url)] = {"format": fmt, "url": url, "currency": currency}
        getattr(self, "indexed_feeds", set()).discard((str(tenant_id), url))

    async def feeds_for(self, tenant_id: str) -> list[dict[str, Any]]:
        return [dict(row) for (tenant, _url), row in self.feeds.items() if tenant == str(tenant_id)]

    async def tenant_ids(self) -> list[str]:
        found = {tenant for tenant, _sku in self._products}
        found.update(self.retailers)
        return sorted(found)

    async def add_quote(self, tenant_id: str, quote_id: str, payload: dict[str, Any], contact: dict[str, Any]) -> None:
        self.quotes.append(
            {"tenant_id": str(tenant_id), "id": quote_id, "payload": payload, "contact": contact, "sent_at": None,
             "created_at": datetime.now(timezone.utc)}
        )
        await self.training_event(tenant_id, "rig_quote", quote_id, payload, {"request_id": quote_id})

    async def locate_state(self, tenant_id: str, quote_id: str, state: dict[str, Any]) -> None:
        """Locate checkout state in the quote payload. Tenant scoped."""
        for row in self.quotes:
            if row["tenant_id"] == str(tenant_id) and row["id"] == quote_id:
                row["payload"] = {**row["payload"], "locate_state": state}

    async def swap_locate_state(self, tenant_id: str, quote_id: str, old: dict[str, Any],
                                new: dict[str, Any]) -> bool:
        """Compare-and-swap the quote's locate_state. Tenant scoped."""
        for row in self.quotes:
            if row["tenant_id"] == str(tenant_id) and row["id"] == quote_id:
                if row["payload"].get("locate_state") != old:
                    return False
                row["payload"] = {**row["payload"], "locate_state": copy.deepcopy(new)}
                return True
        return False


    @staticmethod
    def _attempt_order(row: dict[str, Any]) -> datetime:
        return row.get("locate_attempt_at") or row.get("created_at") or datetime.min.replace(tzinfo=timezone.utc)

    async def locate_attempt(self, tenant_id: str, quote_id: str, at: datetime) -> None:
        """Record when the reconciler last tried this quote. Tenant scoped."""
        for row in self.quotes:
            if row["tenant_id"] == str(tenant_id) and row["id"] == quote_id:
                row["locate_attempt_at"] = at

    async def locate_states(self, tenant_id: str, quote_ids: list[str]) -> list[tuple[str, dict[str, Any]]]:
        """(quote id, locate_state) for these quotes, where one is recorded. Tenant scoped."""
        wanted = set(quote_ids)
        return [(row["id"], copy.deepcopy(row["payload"]["locate_state"])) for row in self.quotes
                if row["tenant_id"] == str(tenant_id) and row["id"] in wanted
                and row["payload"].get("locate_state")]


    async def place_locate_order(self, tenant_id: str, order: dict[str, Any],
                                 purchase: dict[str, Any]) -> dict[str, Any]:
        """Rig-owned Locate order and draft supplier PO. First write wins per (tenant, quote)."""
        key = (str(tenant_id), order["quote_id"])
        created = key not in self.locate_orders
        if created and any(tenant == key[0] and (row["order"]["number"] == order["number"]
                                                  or row["purchase_order"]["number"] == purchase["number"])
                           for (tenant, _quote), row in self.locate_orders.items()):
            # Same as Postgres' UNIQUE (tenant_id, number): a number taken by
            # another quote saves nothing.
            from rigbox.quotes.senders import DeliveryError
            raise DeliveryError("order_mismatch")
        if created:
            self.locate_orders[key] = {"order": copy.deepcopy(order), "purchase_order": copy.deepcopy(purchase)}
            self.locate_order_created[key] = datetime.now(timezone.utc)
        return {**copy.deepcopy(self.locate_orders[key]), "created": created}

    async def locate_order(self, tenant_id: str, quote_id: str) -> dict[str, Any] | None:
        row = self.locate_orders.get((str(tenant_id), quote_id))
        return copy.deepcopy(row) if row else None

    async def supplier_po_sent(self, tenant_id: str, quote_id: str, sent: dict[str, Any]) -> dict[str, Any] | None:
        """draft -> sent, once. None when the PO is missing or not a draft."""
        row = self.locate_orders.get((str(tenant_id), quote_id))
        if row is None or row["purchase_order"]["state"] != "draft":
            return None
        row["purchase_order"].update(state="sent", sent=copy.deepcopy(sent))
        return copy.deepcopy(row["purchase_order"])

    async def cancel_locate_order(self, tenant_id: str, quote_id: str, reason: str) -> str:
        """Cancel a placed order and its unsent PO. Refuses ("sent") once the PO was sent."""
        row = self.locate_orders.get((str(tenant_id), quote_id))
        if row is None:
            return "missing"
        if row["purchase_order"]["state"] == "sent":
            return "sent"
        if row["order"]["state"] == "placed":
            row["order"].update(state="cancelled", cancel_reason=reason)
        if row["purchase_order"]["state"] == "draft":
            row["purchase_order"]["state"] = "cancelled"
        return "cancelled"

    async def draft_supplier_orders(self, tenant_id: str, limit: int = 200,
                                    before: datetime | None = None, *, auto_only: bool = False) -> list[str]:
        """Quote ids whose PO is still draft and whose order is still placed.

        With before, only POs created before that time. With auto_only, only
        quotes whose locate_state says they were auto-ordered, least recently
        tried first (locate_attempt, else the PO's creation): orders placed
        without auto-order stay draft for a person and never fill the list.
        """
        quotes = {row["id"]: row for row in self.quotes if row["tenant_id"] == str(tenant_id)}
        keys = [(tenant, quote) for (tenant, quote), row in self.locate_orders.items()
                if tenant == str(tenant_id) and row["purchase_order"]["state"] == "draft"
                and row["order"]["state"] == "placed"
                and (before is None or self.locate_order_created[(tenant, quote)] < before)]
        if auto_only:
            def auto(quote: str) -> bool:
                state = (quotes.get(quote) or {}).get("payload", {}).get("locate_state") or {}
                return state.get("auto_order") is True
            keys = [key for key in keys if auto(key[1])]
            keys.sort(key=lambda key: (quotes[key[1]].get("locate_attempt_at") or self.locate_order_created[key]))
        return [quote for _tenant, quote in keys][:limit]

    async def mark_sent(self, tenant_id: str, quote_id: str) -> None:
        for row in self.quotes:
            if row["tenant_id"] == str(tenant_id) and row["id"] == quote_id:
                row["sent_at"] = "sent"

    async def quote_for(self, tenant_id: str, quote_id: str) -> dict[str, Any] | None:
        return next((copy.deepcopy(row) for row in self.quotes
                     if row["tenant_id"] == str(tenant_id) and row["id"] == quote_id), None)

    async def quotes_for(self, tenant_id: str) -> list[dict[str, Any]]:
        return [row for row in self.quotes if row["tenant_id"] == str(tenant_id)]

    async def approve_fit(self, tenant_id: str, part_sku: str, fits_sku: str | None, fits_family: str | None) -> bool:
        key = (str(tenant_id), part_sku, fits_sku or "", fits_family or "")
        row = self._fits.get(key)
        if row is None:
            return False
        if row.get("source") == "rejected":
            row["source"] = "suggested"
        row["approved"] = True
        self._drop_answers(tenant_id)
        return True

    async def reject_fit(self, tenant_id: str, part_sku: str, fits_sku: str | None, fits_family: str | None) -> bool:
        key = (str(tenant_id), part_sku, fits_sku or "", fits_family or "")
        row = self._fits.get(key)
        if row is None:
            return False
        row["approved"] = False
        row["source"] = "rejected"
        self._drop_answers(tenant_id)
        return True

    async def update_price(self, tenant_id: str, sku: str, patch: dict[str, Any]) -> bool:
        row = self._products.get((str(tenant_id), sku))
        if row is None:
            return False
        if "price" in patch:
            row["price"] = patch["price"]
        if "currency" in patch:
            row["currency"] = patch["currency"]
        if "active" in patch:
            row["active"] = patch["active"]
        self._drop_answers(tenant_id)
        return True

    async def get_answer(self, tenant_id: str, kind: str, product_id: str, version: str) -> dict[str, Any] | None:
        found = self.answers.get((str(tenant_id), kind, product_id))
        if found is None or found[0] != version:
            return None
        return copy.deepcopy(found[1])

    async def put_answer(self, tenant_id: str, kind: str, product_id: str, version: str, body: dict[str, Any]) -> None:
        self.answers[(str(tenant_id), kind, product_id)] = (version, copy.deepcopy(body))

    async def get_fit_why(self, tenant_id: str, fit_id: str, rule_hash: str) -> list[str] | None:
        found = self.fit_why.get((str(tenant_id), fit_id, rule_hash))
        if found is None:
            return None
        return list(found)

    async def put_fit_why(self, tenant_id: str, fit_id: str, rule_hash: str, why: list[str]) -> None:
        self.fit_why[(str(tenant_id), fit_id, rule_hash)] = [str(line) for line in why]

    async def training_event(self, tenant: str, source: str, ref: str, value: dict, output: dict) -> None:
        self.training_events.append({"tenant_id": tenant, "source": source, "source_ref": ref,
                                     "input": copy.deepcopy(value), "output": copy.deepcopy(output)})

    async def add_search(self, tenant_id: str, version: str, row: dict[str, Any]) -> None:
        self.searches.append(
            {
                "tenant_id": str(tenant_id),
                "id": str(row.get("id") or ""),
                "version": version,
                "query": str(row.get("query") or ""),
                "host": str(row.get("host") or ""),
                "signature": str(row.get("signature") or ""),
                "limit": int(row.get("limit") or 0),
                "ids": [str(item) for item in row.get("ids") or []],
                "exact_match": bool(row.get("exact_match", True)),
                "message": row.get("message"),
                "parts": copy.deepcopy(list(row.get("parts") or [])),
                "created_at": _search_time(row.get("created_at")),
            }
        )

    async def searches_for(self, tenant_id: str, version: str, days: int = 90) -> list[dict[str, Any]]:
        """Newest first, inside the window, at most 500. One retailer only.

        The live store does this in SQL. This stand-in keeps the same rule
        for tests that never open a database.
        """
        tenant = str(tenant_id)
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
        rows = [
            row
            for row in self.searches
            if row["tenant_id"] == tenant and row["version"] == version and row["created_at"] >= cutoff
        ]
        rows.sort(key=lambda row: (row["created_at"], row["id"]), reverse=True)
        return [copy.deepcopy(row) for row in rows[:SEARCH_RECALL_CAP]]

    async def drop_searches(self, tenant_id: str) -> None:
        tenant = str(tenant_id)
        self.searches = [row for row in self.searches if row["tenant_id"] != tenant]

    async def expire_searches(self, days: int) -> int:
        """Delete saved searches older than the window, for every retailer."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
        kept = [row for row in self.searches if row["created_at"] >= cutoff]
        deleted = len(self.searches) - len(kept)
        self.searches = kept
        return deleted


def _record_product(row: Any) -> dict[str, Any]:
    attributes = row["attributes"]
    if isinstance(attributes, str):
        attributes = json.loads(attributes)
    return {
        "sku": row["sku"],
        "id": row["sku"],
        "gtin": row["gtin"],
        "mpn": row.get("mpn"),
        "supply_eligible": bool(row.get("supply_eligible", False)),
        "name": row["name"],
        "brand": row["brand"],
        "category": row["category"],
        "price": row["price"],
        "currency": row["currency"],
        "image_url": row["image_url"],
        "url": row["url"],
        "family": row["family"] or "",
        "attributes": attributes or {},
        "active": row["active"],
    }


class PgStore(RetailerPgStore):
    """Postgres for the live room. Search is full text plus trigram, always by tenant."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def deleted_skus(self, tenant_id: str) -> set[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT sku FROM agent_rig.deleted_products WHERE tenant_id=$1", _uuid(tenant_id))
        return {row["sku"] for row in rows}

    async def restore_products(self, tenant_id: str, skus: list[str]) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM agent_rig.deleted_products WHERE tenant_id=$1 AND sku=ANY($2::text[])", _uuid(tenant_id), skus)

    async def delete_products(self, tenant_id: str, skus: list[str]) -> int:
        """Use the caller's atomic connection, including its tenant lock."""
        tenant = _uuid(tenant_id)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT sku FROM agent_rig.products WHERE tenant_id=$1 AND sku=ANY($2::text[]) FOR UPDATE", tenant, skus)
            found = [row["sku"] for row in rows]
            if not found:
                return 0
            await conn.execute("""INSERT INTO agent_rig.deleted_products(tenant_id,sku)
                SELECT $1,unnest($2::text[]) ON CONFLICT(tenant_id,sku) DO NOTHING""", tenant, found)
            await conn.execute("DELETE FROM agent_rig.supplier_costs WHERE tenant_id=$1 AND sku=ANY($2::text[])", tenant, found)
            await conn.execute("DELETE FROM agent_rig.fits WHERE tenant_id=$1 AND (part_sku=ANY($2::text[]) OR fits_sku=ANY($2::text[]))", tenant, found)
            families = await conn.fetch("SELECT family,member_skus FROM agent_rig.families WHERE tenant_id=$1", tenant)
            # These derived jobs reference the product. Quote history has no such FK.
            await conn.execute("DELETE FROM agent_rig.rig_3d_jobs WHERE tenant_id=$1 AND product_id=ANY($2::text[])", tenant, found)
            await conn.execute("DELETE FROM agent_rig.products WHERE tenant_id=$1 AND sku=ANY($2::text[])", tenant, found)
        removed = set(found)
        for row in families:
            remaining = [sku for sku in row["member_skus"] if sku not in removed]
            await self.set_family(tenant_id, row["family"], remaining)
            if row["member_skus"] and not remaining:
                async with self.pool.acquire() as conn:
                    await conn.execute("DELETE FROM agent_rig.fits WHERE tenant_id=$1 AND fits_family=$2", tenant, row["family"])
        await self._drop_answers(tenant_id)
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM agent_rig.searches WHERE tenant_id=$1 AND ids ?| $2::text[]", tenant, found)
        return len(found)

    async def apply_schema(self) -> None:
        """Apply ``schema.sql``. The agent role is not allowed to create schemas.

        A ``CREATE SCHEMA`` for ``agent_rig`` that fails for that reason is
        ignored only when the schema is already there. Any other failure,
        including the same privilege error when the schema is missing, stops
        startup. ``CREATE EXTENSION`` and trigram indexes stay optional.
        Statements are classified after leading blank lines and ``--`` comments
        are removed, because those comments ride along on the first statement.
        """
        # Optional DDL errors must roll back before the existing-schema fallback
        # or next statement. Keep SET LOCAL per statement on scoped pools.
        # Each statement runs in its own transaction that first sets
        # lock_timeout with set_config(..., true) (SET LOCAL), so the timeout
        # ends with that statement and never reaches the session. A lock
        # timeout (55P03) is retried with backoff (statements are repeat-safe);
        # once the attempts run out it is logged and raised like any failure.
        checkout = self.pool.acquire(autocommit=True) if isinstance(self.pool, TenantPool) else self.pool.acquire()
        async with checkout as conn:
            for statement in schema_statements():
                sql = _classified_sql(statement)
                if not sql:
                    continue
                try:
                    for attempt in range(1, SCHEMA_LOCK_ATTEMPTS + 1):
                        try:
                            async with conn.transaction():
                                await conn.execute("SELECT set_config('lock_timeout', $1, true)",
                                                   SCHEMA_LOCK_TIMEOUT)
                                await conn.execute(statement)
                            break
                        except Exception as exc:
                            if not _lock_not_available(exc):
                                raise
                            if attempt == SCHEMA_LOCK_ATTEMPTS:
                                log.error("rig schema: lock not acquired after %d attempts (lock_timeout %s): %s",
                                          attempt, SCHEMA_LOCK_TIMEOUT, " ".join(sql.split())[:80])
                                raise
                            log.warning("rig schema: lock timeout, retry %d of %d", attempt,
                                        SCHEMA_LOCK_ATTEMPTS - 1)
                            await asyncio.sleep(SCHEMA_LOCK_BACKOFF * 2 ** (attempt - 1))
                except Exception as exc:
                    if _optional_schema_statement(sql):
                        continue
                    if _created_schema_name(sql) == "agent_rig" and _role_cannot_create_schema(exc):
                        present = await conn.fetchval(
                            "SELECT 1 FROM pg_namespace WHERE nspname = 'agent_rig'"
                        )
                        if present == 1:
                            continue
                    raise
            await self._log_cost_guard(conn)

    async def _log_cost_guard(self, conn: Any) -> int:
        """Products whose attributes still carry a private key after the migration.

        Logged on every startup. Zero visible products, or a non-zero count,
        means the migration did not run as intended (for example under forced
        RLS with no tenant set). Shoppers stay safe either way: every shopper
        path allowlists attributes.
        """
        try:
            visible, left = await conn.fetchrow(
                "SELECT count(*), count(*) FILTER (WHERE jsonb_typeof(attributes) = 'object' AND EXISTS ("
                "SELECT 1 FROM jsonb_object_keys(attributes) AS k(key) WHERE k.key ~* $1)) "
                "FROM agent_rig.products",
                PRIVATE_KEY_PATTERN,
            )
        except Exception:  # noqa: BLE001 - a log line must never stop startup
            _log.warning("cost-guard: could not count products after the migration", exc_info=True)
            return -1
        rls = await self._rls_hides_rows(conn) if not visible else False
        level = logging.WARNING if left or rls else logging.INFO
        _log.log(level, "cost-guard: %d products visible, %d still carry private keys in attributes", visible, left)
        if rls:
            _log.warning(
                "cost-guard: agent_rig.products has forced row-level security and this role does not bypass it, "
                "so the migration saw no rows and moved nothing. Run schema.sql as the table owner with RLS "
                "bypassed (or per tenant) to move stored private keys."
            )
        return int(left)

    @staticmethod
    async def _rls_hides_rows(conn: Any) -> bool:
        """True when row-level security applies to this role on agent_rig.products.

        Then a startup migration with no tenant set sees nothing, and zero
        visible rows does not mean an empty table.
        """
        try:
            row = await conn.fetchrow(
                "SELECT c.relrowsecurity, c.relforcerowsecurity, r.rolbypassrls, r.rolsuper, "
                "pg_get_userbyid(c.relowner) = current_user AS owner "
                "FROM pg_class c JOIN pg_roles r ON r.rolname = current_user "
                "WHERE c.oid = 'agent_rig.products'::regclass"
            )
        except Exception:  # noqa: BLE001 - a log line must never stop startup
            return False
        if not row or not row["relrowsecurity"] or row["rolbypassrls"] or row["rolsuper"]:
            return False
        return bool(row["relforcerowsecurity"] or not row["owner"])

    async def product_exists(self, tenant_id: str, sku: str) -> bool:
        async with self.pool.acquire() as conn:
            found = await conn.fetchval(
                "SELECT 1 FROM agent_rig.products WHERE tenant_id = $1 AND sku = $2",
                _uuid(tenant_id),
                sku,
            )
        return found == 1

    async def upsert_product(self, tenant_id: str, row: dict[str, Any]) -> str:
        # Legacy scheduled feeds also share the delete/import tenant lock.
        async with self.atomic(tenant_id) as store:
            return await store._upsert_product(tenant_id, row)

    async def _upsert_product(self, tenant_id: str, row: dict[str, Any]) -> str:
        async with self.pool.acquire() as conn:
            deleted = await conn.fetchval("SELECT 1 FROM agent_rig.deleted_products WHERE tenant_id=$1 AND sku=$2", _uuid(tenant_id), str(row["sku"]))
        if deleted:
            return "skipped"
        existed = await self.product_exists(tenant_id, str(row["sku"]))
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO agent_rig.products
                    (tenant_id, id, sku, gtin, name, brand, category, price, currency,
                     image_url, url, family, attributes, active, updated_at, mpn, supply_eligible)
                VALUES ($1, $2, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13, now(), $14, $15)
                ON CONFLICT (tenant_id, sku) DO UPDATE SET
                    gtin = EXCLUDED.gtin,
                    mpn = EXCLUDED.mpn,
                    supply_eligible = EXCLUDED.supply_eligible,
                    name = EXCLUDED.name,
                    brand = EXCLUDED.brand,
                    category = EXCLUDED.category,
                    price = EXCLUDED.price,
                    currency = EXCLUDED.currency,
                    image_url = EXCLUDED.image_url,
                    url = EXCLUDED.url,
                    family = EXCLUDED.family,
                    attributes = EXCLUDED.attributes,
                    active = EXCLUDED.active,
                    updated_at = now()
                """,
                _uuid(tenant_id),
                str(row["sku"]),
                _optional_text(row.get("gtin")),
                str(row["name"]),
                str(row["brand"]),
                str(row["category"]),
                _money(row.get("price")),
                str(row["currency"]),
                _optional_text(row.get("image_url")),
                str(row.get("url") or ""),
                str(row.get("family") or ""),
                _json(row.get("attributes") or {}),
                bool(row.get("active", True)),
                _optional_text(row.get("mpn")),
                bool(row.get("supply_eligible", False)),
            )
            if "private_attributes" in row:
                private = row.get("private_attributes") or {}
                if private:
                    await conn.execute(
                        """
                        INSERT INTO agent_rig.product_private_attributes (tenant_id, sku, attributes, updated_at)
                        VALUES ($1, $2, $3::jsonb, now())
                        ON CONFLICT (tenant_id, sku) DO UPDATE SET attributes = EXCLUDED.attributes, updated_at = now()
                        """,
                        _uuid(tenant_id), str(row["sku"]), _json(private),
                    )
                else:
                    await conn.execute(
                        "DELETE FROM agent_rig.product_private_attributes WHERE tenant_id = $1 AND sku = $2",
                        _uuid(tenant_id), str(row["sku"]),
                    )
        await self._drop_answers(tenant_id)
        return "updated" if existed else "added"

    async def product_private_attributes(self, tenant_id: str, sku: str) -> dict[str, Any]:
        """Store-only. Never on a shopper path."""
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT attributes FROM agent_rig.product_private_attributes WHERE tenant_id = $1 AND sku = $2",
                _uuid(tenant_id), sku,
            )
        if isinstance(value, str):
            value = json.loads(value)
        return dict(value or {})

    async def products(self, tenant_id: str) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(_PRODUCTS + " ORDER BY sku", _uuid(tenant_id))
        return [_record_product(row) | {"tenant_id": str(tenant_id)} for row in rows]

    async def search_rows(self, tenant_id: str, query: str, limit: int) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            try:
                rows = await conn.fetch(_SEARCH, _uuid(tenant_id), query, limit, _SEARCH_KEYS)
            except Exception as exc:
                if "similarity" not in str(exc).lower() and "pg_trgm" not in str(exc).lower():
                    raise
                rows = await conn.fetch(_SEARCH_FTS, _uuid(tenant_id), query, limit, _SEARCH_KEYS)
        return [_record_product(row) | {"tenant_id": str(tenant_id)} for row in rows]

    async def upsert_fit(self, tenant_id: str, row: dict[str, Any]) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO agent_rig.fits
                    (tenant_id, part_sku, fits_sku, fits_family, rule, source, confidence, approved, note)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
                ON CONFLICT (tenant_id, part_sku, fits_sku, fits_family) DO UPDATE SET
                    rule = EXCLUDED.rule,
                    source = EXCLUDED.source,
                    confidence = EXCLUDED.confidence,
                    approved = EXCLUDED.approved,
                    note = EXCLUDED.note
                """,
                _uuid(tenant_id),
                str(row["part_sku"]),
                str(row.get("fits_sku") or ""),
                str(row.get("fits_family") or ""),
                _json(row.get("rule") or {}),
                str(row.get("source") or "import"),
                _numeric(row.get("confidence")),
                bool(row.get("approved")),
                str(row.get("note") or ""),
            )
        await self._drop_answers(tenant_id)

    async def fits(self, tenant_id: str) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT part_sku, fits_sku, fits_family, rule, source, confidence, approved, note
                FROM agent_rig.fits WHERE tenant_id = $1
                """,
                _uuid(tenant_id),
            )
        out: list[dict[str, Any]] = []
        for row in rows:
            rule = row["rule"]
            if isinstance(rule, str):
                rule = json.loads(rule)
            out.append(
                {
                    "tenant_id": str(tenant_id),
                    "part_sku": row["part_sku"],
                    "fits_sku": row["fits_sku"] or None,
                    "fits_family": row["fits_family"] or None,
                    "rule": rule or {},
                    "source": row["source"],
                    "confidence": row["confidence"],
                    "approved": row["approved"],
                    "note": row["note"],
                }
            )
        return out

    async def set_family(self, tenant_id: str, family: str, members: list[str]) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO agent_rig.families (tenant_id, family, member_skus)
                VALUES ($1, $2, $3)
                ON CONFLICT (tenant_id, family) DO UPDATE SET member_skus = EXCLUDED.member_skus
                """,
                _uuid(tenant_id),
                str(family),
                [str(item) for item in members],
            )

    async def set_template(self, tenant_id: str, category: str, roles: Any) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO agent_rig.setup_templates (tenant_id, category, roles)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (tenant_id, category) DO UPDATE SET roles = EXCLUDED.roles
                """,
                _uuid(tenant_id),
                str(category),
                _json(roles),
            )
        await self._drop_answers(tenant_id)

    async def templates_for(self, tenant_id: str) -> dict[str, Any]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT category, roles FROM agent_rig.setup_templates WHERE tenant_id = $1",
                _uuid(tenant_id),
            )
        found: dict[str, Any] = {}
        for row in rows:
            roles = row["roles"]
            if isinstance(roles, str):
                roles = json.loads(roles)
            found[row["category"]] = roles
        return found

    async def put_key(self, tenant_id: str, key_hash: str, kind: str, origins: list[str]) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO agent_rig.keys (tenant_id, key_hash, kind, origins)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (key_hash) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    kind = EXCLUDED.kind,
                    origins = EXCLUDED.origins
                """,
                _uuid(tenant_id),
                str(key_hash),
                str(kind),
                [str(item) for item in origins],
            )

    async def key(self, key_hash: str) -> dict[str, Any] | None:
        # Authentication must identify the tenant before tenant-scoped checkout.
        bootstrap_pool = getattr(self.pool, "admin_pool", None) or self.pool
        async with bootstrap_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT tenant_id, kind, origins FROM agent_rig.keys WHERE key_hash = $1",
                key_hash,
            )
        if row is None:
            return None
        return {"tenant_id": str(row["tenant_id"]), "kind": row["kind"], "origins": list(row["origins"] or [])}

    async def put_retailer(
        self,
        tenant_id: str,
        slug: str,
        email: str | None,
        webhook: str | None,
        name: str = "",
        theme: str | None = None,
        quote_notice: dict[str, str] | None = None,
        locate_enabled: bool | None = None,
        blind_ship: bool | None = None,
        auto_order: bool | None = None,
        delivery: dict[str, int] | None = None,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO agent_rig.retailers (tenant_id, slug, email, webhook, name, theme, quote_notice, locate_enabled, blind_ship,
                                             auto_order, delivery)
                VALUES ($1, $2, $3, $4, $5, coalesce($6, 'neutral'), $7::jsonb, coalesce($8, false), coalesce($9, false),
                        coalesce($10, false), coalesce($11::jsonb, '{}'::jsonb))
                ON CONFLICT (tenant_id) DO UPDATE SET
                    slug = EXCLUDED.slug,
                    email = EXCLUDED.email,
                    webhook = EXCLUDED.webhook,
                    name = EXCLUDED.name,
                    theme = coalesce($6, agent_rig.retailers.theme),
                    quote_notice = coalesce($7::jsonb, agent_rig.retailers.quote_notice),
                    locate_enabled = coalesce($8, agent_rig.retailers.locate_enabled),
                    blind_ship = coalesce($9, agent_rig.retailers.blind_ship),
                    auto_order = coalesce($10, agent_rig.retailers.auto_order),
                    delivery = coalesce($11::jsonb, agent_rig.retailers.delivery)
                """,
                _uuid(tenant_id),
                str(slug),
                _optional_text(email),
                _optional_text(webhook),
                str(name or ""),
                theme,
                json.dumps(quote_notice) if quote_notice is not None else None,
                None if locate_enabled is None else bool(locate_enabled),
                None if blind_ship is None else bool(blind_ship),
                None if auto_order is None else bool(auto_order),
                None if delivery is None else _json(delivery),
            )








    async def retailer(self, tenant_id: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT slug, email, webhook, name, theme, quote_notice, locate_enabled, blind_ship, auto_order, shop, delivery FROM agent_rig.retailers WHERE tenant_id = $1",
                _uuid(tenant_id),
            )
        if row is None:
            return None
        return {"slug": row["slug"], "email": row["email"], "webhook": row["webhook"], "name": row["name"] or "", "theme": row["theme"],
                "quote_notice": json.loads(row["quote_notice"]) if isinstance(row["quote_notice"], str) else row["quote_notice"],
                "locate_enabled": bool(row["locate_enabled"]), "blind_ship": bool(row["blind_ship"]),
                "auto_order": bool(row["auto_order"]),
                "delivery": json.loads(row["delivery"]) if isinstance(row["delivery"], str) else row["delivery"],
                "shop": json.loads(row["shop"]) if isinstance(row["shop"], str) else row["shop"]}


    async def replace_market(self, tenant_id: str, rows: list[dict[str, Any]]) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM agent_rig.market_prices WHERE tenant_id = $1",
                    _uuid(tenant_id),
                )
                for row in rows:
                    seen = row.get("seen_at")
                    price = _money(row.get("price"))
                    if seen is not None and str(seen).strip():
                        await conn.execute(
                            """
                            INSERT INTO agent_rig.market_prices
                                (tenant_id, sku, source, price, currency, seen_at)
                            VALUES ($1, $2, $3, $4, $5, $6)
                            """,
                            _uuid(tenant_id),
                            str(row.get("sku") or ""),
                            str(row.get("source") or ""),
                            price,
                            str(row.get("currency") or ""),
                            _timestamptz(seen),
                        )
                    else:
                        await conn.execute(
                            """
                            INSERT INTO agent_rig.market_prices
                                (tenant_id, sku, source, price, currency)
                            VALUES ($1, $2, $3, $4, $5)
                            """,
                            _uuid(tenant_id),
                            str(row.get("sku") or ""),
                            str(row.get("source") or ""),
                            price,
                            str(row.get("currency") or ""),
                        )
        await self._drop_answers(tenant_id)

    async def market_for(self, tenant_id: str, skus: list[str]) -> list[dict[str, Any]]:
        if not skus:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT sku, source, price, currency, seen_at
                FROM agent_rig.market_prices
                WHERE tenant_id = $1 AND sku = ANY($2::text[])
                """,
                _uuid(tenant_id),
                [str(sku) for sku in skus],
            )
        return [
            {
                "sku": row["sku"],
                "source": row["source"],
                "price": row["price"],
                "currency": row["currency"],
                "seen_at": row["seen_at"].isoformat() if row["seen_at"] is not None else "",
            }
            for row in rows
        ]

    async def put_feed(self, tenant_id: str, fmt: str, url: str, currency: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO agent_rig.feeds (tenant_id, format, url, currency)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (tenant_id, url) DO UPDATE SET format = EXCLUDED.format, currency = EXCLUDED.currency, watch_indexed=false
                """,
                _uuid(tenant_id),
                str(fmt),
                str(url),
                str(currency or ""),
            )

    async def feeds_for(self, tenant_id: str) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT format, url, currency FROM agent_rig.feeds WHERE tenant_id = $1",
                _uuid(tenant_id),
            )
        return [{"format": row["format"], "url": row["url"], "currency": row["currency"]} for row in rows]

    async def tenant_ids(self) -> list[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT tenant_id FROM agent_rig.retailers UNION SELECT tenant_id FROM agent_rig.products")
        return [str(row["tenant_id"]) for row in rows]

    async def add_quote(self, tenant_id: str, quote_id: str, payload: dict[str, Any], contact: dict[str, Any]) -> None:
        from rigbox.training import enqueue

        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO agent_rig.quotes (tenant_id, id, payload, contact)
                VALUES ($1, $2, $3::jsonb, $4::jsonb)
                """,
                _uuid(tenant_id),
                str(quote_id),
                _json(payload),
                _json(contact),
            )
            await enqueue(conn, tenant_id, "rig_quote", quote_id, payload, {"request_id": quote_id})

    async def locate_state(self, tenant_id: str, quote_id: str, state: dict[str, Any]) -> None:
        """Locate checkout state in the existing quote jsonb. Tenant scoped."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE agent_rig.quotes SET payload = jsonb_set(payload, '{locate_state}', $3::jsonb)"
                " WHERE tenant_id = $1 AND id = $2",
                _uuid(tenant_id), quote_id, _json(state),
            )

    async def swap_locate_state(self, tenant_id: str, quote_id: str, old: dict[str, Any],
                                new: dict[str, Any]) -> bool:
        """Compare-and-swap the quote's locate_state in one statement (jsonb equality)."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE agent_rig.quotes SET payload = jsonb_set(payload, '{locate_state}', $4::jsonb)"
                " WHERE tenant_id = $1 AND id = $2 AND payload->'locate_state' = $3::jsonb",
                _uuid(tenant_id), quote_id, _json(old), _json(new),
            )
        return result.endswith(" 1")


    async def locate_states(self, tenant_id: str, quote_ids: list[str]) -> list[tuple[str, dict[str, Any]]]:
        """(quote id, locate_state) for these quotes, where one is recorded. Tenant scoped."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, payload->'locate_state' AS state FROM agent_rig.quotes"
                " WHERE tenant_id = $1 AND id = ANY($2::text[]) AND payload ? 'locate_state'"
                " ORDER BY created_at",
                _uuid(tenant_id), [str(item) for item in quote_ids][:1000],
            )
        return [(row["id"], json.loads(row["state"]) if isinstance(row["state"], str) else row["state"]) for row in rows]


    _ORDER_COLUMNS = "quote_id, number, state, product_id, price, currency, ship_to, cancel_reason"
    _PO_COLUMNS = ("quote_id, number, state, source_id, vendor_name, order_email, item_sku, item_url, quantity,"
                   " unit_cost, currency, ship_to, neutral_slip, instructions, sent")

    async def place_locate_order(self, tenant_id: str, order: dict[str, Any],
                                 purchase: dict[str, Any]) -> dict[str, Any]:
        """Rig-owned Locate order and draft supplier PO, in one transaction.

        ON CONFLICT DO NOTHING with no conflict target: a retry or a concurrent
        second Accept for the same quote reads back the first order instead of
        making another. With no target, a clash on either unique index
        ((tenant, quote) or (tenant, number), and the number is derived from
        the quote) waits for the first insert and then does nothing; a target
        on (tenant, quote) alone let concurrent inserts raise on the number
        index. If nothing is saved for this quote afterwards (its number is
        taken by another quote), DeliveryError("order_mismatch").
        The PO insert targets UNIQUE (tenant_id, number) only: a PO number
        taken by another quote raises the same DeliveryError("order_mismatch")
        inside the transaction, so the order rolls back too. The PO's
        (tenant, quote) key can't clash there (the order row was just created
        and the PO's foreign key needs it), so any other conflict raises
        instead of being swallowed.
        """
        tenant = _uuid(tenant_id)
        async with self.pool.acquire() as conn, conn.transaction():
            created = await conn.fetchval(
                "INSERT INTO agent_rig.locate_orders (tenant_id, quote_id, number, state, product_id, price, currency, ship_to)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb) ON CONFLICT DO NOTHING RETURNING true",
                tenant, order["quote_id"], order["number"], order["state"], order["product_id"],
                Decimal(order["price"]), order["currency"],
                None if order["ship_to"] is None else _json(order["ship_to"]))
            if created:
                po = await conn.fetchval(
                    "INSERT INTO agent_rig.supplier_purchase_orders (tenant_id, quote_id, number, state, source_id,"
                    " vendor_name, order_email, item_sku, item_url, quantity, unit_cost, currency, ship_to,"
                    " neutral_slip, instructions)"
                    " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14, $15)"
                    " ON CONFLICT (tenant_id, number) DO NOTHING RETURNING true",
                    tenant, purchase["quote_id"], purchase["number"], purchase["state"], purchase["source_id"],
                    purchase["vendor_name"], purchase["order_email"], purchase["item_sku"], purchase["item_url"],
                    int(purchase["quantity"]), Decimal(purchase["unit_cost"]), purchase["currency"],
                    None if purchase["ship_to"] is None else _json(purchase["ship_to"]),
                    bool(purchase["neutral_slip"]), purchase["instructions"])
                if not po:
                    # The PO number belongs to another quote: raising here rolls
                    # the order back too, so nothing is saved (same as memory).
                    from rigbox.quotes.senders import DeliveryError
                    raise DeliveryError("order_mismatch")
            saved = await self._locate_order(conn, tenant, order["quote_id"])
        if saved is None:
            from rigbox.quotes.senders import DeliveryError
            raise DeliveryError("order_mismatch")
        return {**saved, "created": bool(created)}

    async def locate_order(self, tenant_id: str, quote_id: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            return await self._locate_order(conn, _uuid(tenant_id), quote_id)

    async def supplier_po_sent(self, tenant_id: str, quote_id: str, sent: dict[str, Any]) -> dict[str, Any] | None:
        """draft -> sent, once (a single conditional UPDATE). None when not a draft."""
        async with self.pool.acquire() as conn:
            updated = await conn.fetchval(
                "UPDATE agent_rig.supplier_purchase_orders SET state = 'sent', sent = $3::jsonb, sent_at = now(),"
                " updated_at = now() WHERE tenant_id = $1 AND quote_id = $2 AND state = 'draft' RETURNING true",
                _uuid(tenant_id), quote_id, _json(sent))
            if not updated:
                return None
            saved = await self._locate_order(conn, _uuid(tenant_id), quote_id)
        return saved["purchase_order"] if saved else None

    async def cancel_locate_order(self, tenant_id: str, quote_id: str, reason: str) -> str:
        """Cancel a placed order and its unsent PO, in one transaction.

        The PO row is locked first; a PO already sent refuses the whole cancel
        ("sent"), so the supplier never holds an order Rig marked cancelled.
        """
        tenant = _uuid(tenant_id)
        async with self.pool.acquire() as conn, conn.transaction():
            po_state = await conn.fetchval(
                "SELECT state FROM agent_rig.supplier_purchase_orders WHERE tenant_id = $1 AND quote_id = $2"
                " FOR UPDATE", tenant, quote_id)
            if po_state is None:
                return "missing"
            if po_state == "sent":
                return "sent"
            await conn.execute(
                "UPDATE agent_rig.locate_orders SET state = 'cancelled', cancel_reason = $3, updated_at = now()"
                " WHERE tenant_id = $1 AND quote_id = $2 AND state = 'placed'", tenant, quote_id, reason)
            await conn.execute(
                "UPDATE agent_rig.supplier_purchase_orders SET state = 'cancelled', updated_at = now()"
                " WHERE tenant_id = $1 AND quote_id = $2 AND state = 'draft'", tenant, quote_id)
        return "cancelled"

    async def draft_supplier_orders(self, tenant_id: str, limit: int = 200,
                                    before: datetime | None = None, *, auto_only: bool = False) -> list[str]:
        """Quote ids whose PO is still draft and whose order is still placed.

        Oldest PO first; with auto_only, least recently tried first.

        With before, only POs created before that time. With auto_only, only
        quotes whose locate_state says they were auto-ordered, least recently
        tried first (locate_attempt_at, else the PO's creation): orders placed
        without auto-order stay draft for a person and never fill the list.
        """
        async with self.pool.acquire() as conn:
            if not auto_only:
                rows = await conn.fetch(
                    "SELECT p.quote_id FROM agent_rig.supplier_purchase_orders p JOIN agent_rig.locate_orders o"
                    " USING (tenant_id, quote_id) WHERE p.tenant_id = $1 AND p.state = 'draft' AND o.state = 'placed'"
                    " AND ($3::timestamptz IS NULL OR p.created_at < $3)"
                    " ORDER BY p.created_at LIMIT $2", _uuid(tenant_id), max(1, min(int(limit), 1000)), before)
            else:
                rows = await conn.fetch(
                    "SELECT p.quote_id FROM agent_rig.supplier_purchase_orders p JOIN agent_rig.locate_orders o"
                    " USING (tenant_id, quote_id) JOIN agent_rig.quotes q ON q.tenant_id = p.tenant_id"
                    " AND q.id = p.quote_id"
                    " WHERE p.tenant_id = $1 AND p.state = 'draft' AND o.state = 'placed'"
                    " AND ($3::timestamptz IS NULL OR p.created_at < $3)"
                    " AND q.payload->'locate_state'->'auto_order' = 'true'::jsonb"
                    " ORDER BY coalesce(q.locate_attempt_at, p.created_at), p.created_at LIMIT $2",
                    _uuid(tenant_id), max(1, min(int(limit), 1000)), before)
        return [row["quote_id"] for row in rows]

    async def locate_attempt(self, tenant_id: str, quote_id: str, at: datetime) -> None:
        """Record when the reconciler last tried this quote. Tenant scoped."""
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE agent_rig.quotes SET locate_attempt_at = $3 WHERE tenant_id = $1 AND id = $2",
                               _uuid(tenant_id), quote_id, at)

    async def _locate_order(self, conn: Any, tenant: UUID, quote_id: str) -> dict[str, Any] | None:
        order = await conn.fetchrow(
            f"SELECT {self._ORDER_COLUMNS} FROM agent_rig.locate_orders WHERE tenant_id = $1 AND quote_id = $2",
            tenant, quote_id)
        purchase = await conn.fetchrow(
            f"SELECT {self._PO_COLUMNS} FROM agent_rig.supplier_purchase_orders WHERE tenant_id = $1 AND quote_id = $2",
            tenant, quote_id)
        if order is None or purchase is None:
            return None

        def plain(row: Any) -> dict[str, Any]:
            out = dict(row)
            for key, value in out.items():
                if isinstance(value, Decimal):
                    out[key] = str(value.quantize(Decimal("0.01")))
            for key in ("ship_to", "sent"):
                if isinstance(out.get(key), str):
                    out[key] = json.loads(out[key])
            return out

        return {"order": plain(order), "purchase_order": plain(purchase)}


    async def mark_sent(self, tenant_id: str, quote_id: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE agent_rig.quotes SET sent_at = now() WHERE tenant_id = $1 AND id = $2",
                _uuid(tenant_id),
                quote_id,
            )

    async def quote_for(self, tenant_id: str, quote_id: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, payload, contact FROM agent_rig.quotes WHERE tenant_id=$1 AND id=$2",
                _uuid(tenant_id), quote_id)
        if row is None:
            return None
        return {"id": row["id"], "tenant_id": str(tenant_id),
                **{key: json.loads(row[key]) if isinstance(row[key], str) else row[key]
                   for key in ("payload", "contact")}}

    async def quotes_for(self, tenant_id: str) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, payload, contact FROM agent_rig.quotes WHERE tenant_id = $1",
                _uuid(tenant_id),
            )
        out = []
        for row in rows:
            payload = row["payload"]
            contact = row["contact"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(contact, str):
                contact = json.loads(contact)
            out.append({"id": row["id"], "tenant_id": str(tenant_id), "payload": payload, "contact": contact})
        return out

    async def approve_fit(self, tenant_id: str, part_sku: str, fits_sku: str | None, fits_family: str | None) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE agent_rig.fits SET
                    approved = true,
                    source = CASE WHEN source = 'rejected' THEN 'suggested' ELSE source END
                WHERE tenant_id = $1 AND part_sku = $2 AND fits_sku = $3 AND fits_family = $4
                """,
                _uuid(tenant_id),
                str(part_sku),
                str(fits_sku or ""),
                str(fits_family or ""),
            )
        changed = str(result).endswith("1")
        if changed:
            await self._drop_answers(tenant_id)
        return changed

    async def reject_fit(self, tenant_id: str, part_sku: str, fits_sku: str | None, fits_family: str | None) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE agent_rig.fits SET approved = false, source = 'rejected'
                WHERE tenant_id = $1 AND part_sku = $2 AND fits_sku = $3 AND fits_family = $4
                """,
                _uuid(tenant_id),
                str(part_sku),
                str(fits_sku or ""),
                str(fits_family or ""),
            )
        changed = str(result).endswith("1")
        if changed:
            await self._drop_answers(tenant_id)
        return changed

    async def update_price(self, tenant_id: str, sku: str, patch: dict[str, Any]) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE agent_rig.products SET
                    price = COALESCE($3, price),
                    currency = COALESCE($4, currency),
                    active = COALESCE($5, active),
                    updated_at = now()
                WHERE tenant_id = $1 AND sku = $2
                """,
                _uuid(tenant_id),
                str(sku),
                _numeric(patch.get("price")),
                _optional_text(patch.get("currency")),
                None if patch.get("active") is None else bool(patch.get("active")),
            )
        changed = str(result).endswith("1")
        if changed:
            await self._drop_answers(tenant_id)
        return changed

    async def _drop_answers(self, tenant_id: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM agent_rig.answers WHERE tenant_id = $1", _uuid(tenant_id))

    async def get_answer(self, tenant_id: str, kind: str, product_id: str, version: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT version, body FROM agent_rig.answers
                WHERE tenant_id = $1 AND kind = $2 AND product_id = $3
                """,
                _uuid(tenant_id),
                kind,
                product_id,
            )
        if row is None or row["version"] != version:
            return None
        body = row["body"]
        if isinstance(body, str):
            body = json.loads(body)
        return copy.deepcopy(body)

    async def put_answer(self, tenant_id: str, kind: str, product_id: str, version: str, body: dict[str, Any]) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO agent_rig.answers (tenant_id, kind, product_id, version, body)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                ON CONFLICT (tenant_id, kind, product_id) DO UPDATE
                SET version = EXCLUDED.version, body = EXCLUDED.body
                """,
                _uuid(tenant_id),
                kind,
                product_id,
                version,
                _json(body),
            )

    async def get_fit_why(self, tenant_id: str, fit_id: str, rule_hash: str) -> list[str] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT why FROM agent_rig.fit_why
                WHERE tenant_id = $1 AND fit_id = $2 AND rule_hash = $3
                """,
                _uuid(tenant_id),
                fit_id,
                rule_hash,
            )
        if row is None:
            return None
        why = row["why"]
        if isinstance(why, str):
            why = json.loads(why)
        return [str(line) for line in why]

    async def put_fit_why(self, tenant_id: str, fit_id: str, rule_hash: str, why: list[str]) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO agent_rig.fit_why (tenant_id, fit_id, rule_hash, why)
                VALUES ($1, $2, $3, $4::jsonb)
                ON CONFLICT (tenant_id, fit_id, rule_hash) DO UPDATE SET why = EXCLUDED.why
                """,
                _uuid(tenant_id),
                fit_id,
                rule_hash,
                _json(list(why)),
            )

    async def training_event(self, tenant: str, source: str, ref: str, value: dict, output: dict) -> None:
        from rigbox.training import enqueue

        async with self.pool.acquire() as conn:
            await enqueue(conn, tenant, source, ref, value, output)

    async def add_search(self, tenant_id: str, version: str, row: dict[str, Any]) -> None:
        created = row.get("created_at")
        when = created if isinstance(created, datetime) else None
        if when is not None and when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO agent_rig.searches (
                    tenant_id, id, version, query, host, signature, result_limit,
                    ids, exact_match, message, parts, created_at
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11::jsonb,
                    COALESCE($12::timestamptz, now())
                )
                """,
                _uuid(tenant_id),
                str(row["id"]),
                version,
                str(row.get("query") or ""),
                str(row.get("host") or ""),
                str(row.get("signature") or ""),
                int(row.get("limit") or 0),
                _json([str(item) for item in row.get("ids") or []]),
                bool(row.get("exact_match", True)),
                row.get("message"),
                _json(list(row.get("parts") or [])),
                when,
            )

    async def searches_for(self, tenant_id: str, version: str, days: int = 90) -> list[dict[str, Any]]:
        """One retailer's recent searches. The window, order, and cap are in the query."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT query, host, signature, result_limit, ids, exact_match, message, parts
                FROM agent_rig.searches
                WHERE tenant_id = $1
                  AND version = $2
                  AND created_at >= now() - ($3::int * interval '1 day')
                ORDER BY created_at DESC
                LIMIT 500
                """,
                _uuid(tenant_id),
                version,
                int(days),
            )
        found: list[dict[str, Any]] = []
        for row in rows:
            ids = row["ids"]
            parts = row["parts"]
            if isinstance(ids, str):
                ids = json.loads(ids)
            if isinstance(parts, str):
                parts = json.loads(parts)
            found.append(
                {
                    "query": row["query"],
                    "host": row["host"],
                    "signature": row["signature"],
                    "limit": row["result_limit"],
                    "ids": list(ids),
                    "exact_match": row["exact_match"],
                    "message": row["message"],
                    "parts": parts,
                }
            )
        return found

    async def drop_searches(self, tenant_id: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM agent_rig.searches WHERE tenant_id = $1", _uuid(tenant_id))

    async def expire_searches(self, days: int) -> int:
        """Delete saved searches older than the window. Every retailer. This table only.

        Running it again deletes nothing that the first run already removed.
        """
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM agent_rig.searches
                WHERE created_at < now() - ($1::int * interval '1 day')
                """,
                int(days),
            )
        parts = str(result).split()
        return int(parts[-1]) if parts and parts[-1].isdigit() else 0


def as_public_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [public_product(row) for row in rows]
