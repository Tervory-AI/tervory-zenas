"""RIG-3 persistence. Private pricing never joins a public product query."""

import copy
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID
from typing import Any
from collections.abc import AsyncIterator


class ConnectionPool:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        yield self.conn


class RetailerMemoryStore:
    async def commerce_config(self, tenant_id: str) -> dict:
        return copy.deepcopy(getattr(self, 'commerce_settings', {}).get(str(tenant_id), {'mode': 'quote'}))

    async def save_commerce_config(self, tenant_id: str, config: dict) -> None:
        if not hasattr(self, 'commerce_settings'):
            self.commerce_settings = {}
        self.commerce_settings[str(tenant_id)] = copy.deepcopy(config)

    async def watch_context(self, tenant_id: str, sku: str) -> dict | None:
        if sku in await self.deleted_skus(tenant_id):
            return None
        product = next((p for p in await self.products(tenant_id) if p["sku"] == sku), None)
        if product is None:
            return None
        cost = next((c for c in await self.costs(tenant_id) if c["sku"] == sku), {})
        return {**{k: product.get(k) for k in ("sku", "name", "url", "active")},
                "cost": str(cost["cost"]) if cost.get("cost") is not None else None,
                "cost_currency": cost.get("currency")}

    async def watch_products(self, tenant_id: str, *, catalogue: bool = False) -> list[dict[str, Any]]:
        deleted = await self.deleted_skus(tenant_id)
        return [{"sku": row["sku"], "url": (row.get("url") or "").strip(), "name": row["name"],
                 "gtin": row.get("gtin"), "mpn": row.get("mpn"), "brand": row.get("brand"),
                 "price": str(row["price"]) if row.get("price") is not None else None,
                 "currency": row.get("currency")}
                for row in await self.products(tenant_id)
                if row["sku"] not in deleted and (catalogue or (
                    (row.get("url") or "").strip() and not await self.is_feed_product(tenant_id, row["sku"])))]

    async def watch_applied_at(self, tenant_id: str, sku: str, url: str):
        return getattr(self, "watch_receipts", {}).get((str(tenant_id), sku, url))

    async def mark_watch_applied(self, tenant_id: str, sku: str, url: str, stamp) -> None:
        if not hasattr(self, "watch_receipts"):
            self.watch_receipts = {}
        self.watch_receipts[(str(tenant_id), sku, url)] = stamp

    def _retailer_state(self) -> None:
        if not hasattr(self, "private_rules"):
            self.private_rules = {}
            self.private_rates = {}
            self.private_costs = {}
            self.feed_receipts = []

    @asynccontextmanager
    async def atomic(self, tenant_id: str) -> AsyncIterator[Any]:
        self._retailer_state()
        saved = copy.deepcopy(self.__dict__)
        try:
            yield self
        except BaseException:
            self.__dict__.clear()
            self.__dict__.update(saved)
            raise

    async def rule(self, tenant_id: str) -> dict[str, Any] | None:
        self._retailer_state()
        return self.private_rules.get(str(tenant_id))

    async def save_rule(self, tenant_id: str, rule: dict[str, Any]) -> None:
        self._retailer_state()
        self.private_rules[str(tenant_id)] = dict(rule)

    async def rate(self, tenant_id: str, currency: str, target: str = "ZAR") -> Any:
        self._retailer_state()
        return "1" if currency == target else self.private_rates.get((str(tenant_id), currency if target == "ZAR" else currency + ":" + target))

    async def save_rate(self, tenant_id: str, currency: str, rate: Any, target: str = "ZAR") -> None:
        self._retailer_state()
        self.private_rates[(str(tenant_id), currency if target == "ZAR" else currency + ":" + target)] = str(rate)

    async def costs(self, tenant_id: str) -> list[dict[str, Any]]:
        self._retailer_state()
        return [dict(row) for (tenant, _), row in self.private_costs.items() if tenant == str(tenant_id)]

    async def save_cost(self, tenant_id: str, row: dict[str, Any]) -> None:
        self._retailer_state()
        self.private_costs[(str(tenant_id), row["sku"])] = dict(row)

    async def is_feed_product(self, tenant_id: str, sku: str) -> bool:
        self._retailer_state()
        tenant = str(tenant_id)
        indexed = getattr(self, "indexed_feeds", set())
        unknown = any(owner == tenant and (owner, url) not in indexed for owner, url in self.feeds)
        return (unknown or (tenant, sku) in self.private_costs
                or any(owner == tenant and item == sku for owner, item, _ in getattr(self, "feed_products", set())))

    async def index_feed_products(self, tenant_id: str, url: str, skus: list[str], *, complete: bool = True) -> None:
        if not hasattr(self, "indexed_feeds"):
            self.indexed_feeds, self.feed_products = set(), set()
        if complete:
            self.indexed_feeds.add((str(tenant_id), url))
        else:
            self.indexed_feeds.discard((str(tenant_id), url))
        self.feed_products.update((str(tenant_id), sku, url) for sku in skus if (str(tenant_id), sku) in self._products)

    async def save_feed_receipt(self, tenant_id: str, receipt: dict[str, Any]) -> None:
        self._retailer_state()
        self.feed_receipts.append({**receipt, "tenant_id": str(tenant_id), "created_at": datetime.now(timezone.utc).isoformat()})

    async def receipts(self, tenant_id: str) -> list[dict[str, Any]]:
        self._retailer_state()
        return [{k: v for k, v in row.items() if k != "tenant_id"} for row in reversed(self.feed_receipts) if row["tenant_id"] == str(tenant_id)][:30]

    async def missed_searches(self, tenant_id: str) -> list[dict[str, Any]]:
        counts = {}
        for row in self.searches:
            if row["tenant_id"] == str(tenant_id) and (not row.get("ids") or not row.get("exact_match", True)):
                counts[row["query"]] = counts.get(row["query"], 0) + 1
        return [{"query": q, "count": n} for q, n in sorted(counts.items(), key=lambda item: -item[1])[:50]]


class RetailerPgStore:
    async def commerce_config(self, tenant_id: str) -> dict:
        async with self.pool.acquire() as conn:
            raw = await conn.fetchval('SELECT commerce FROM agent_rig.retailers WHERE tenant_id=$1', UUID(str(tenant_id)))
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data or {'mode': 'quote'}

    async def save_commerce_config(self, tenant_id: str, config: dict) -> None:
        async with self.pool.acquire() as conn:
            result = await conn.execute('UPDATE agent_rig.retailers SET commerce=$2::jsonb WHERE tenant_id=$1',
                                        UUID(str(tenant_id)), json.dumps(config))
        if result != 'UPDATE 1':
            raise ValueError('retailer_missing')

    async def watch_context(self, tenant_id: str, sku: str) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""SELECT p.sku,p.name,p.url,p.active,
                c.cost::text AS cost,c.currency AS cost_currency
                FROM agent_rig.products p LEFT JOIN agent_rig.supplier_costs c
                    ON c.tenant_id=p.tenant_id AND c.sku=p.sku
                WHERE p.tenant_id=$1 AND p.sku=$2 AND NOT EXISTS (
                    SELECT 1 FROM agent_rig.deleted_products d WHERE d.tenant_id=p.tenant_id AND d.sku=p.sku)
                """, UUID(str(tenant_id)), sku)
        return dict(row) if row else None

    async def watch_products(self, tenant_id: str, *, catalogue: bool = False) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""SELECT p.sku,btrim(p.url) AS url,p.name,p.gtin,p.mpn,p.brand,
                p.price::text AS price,p.currency FROM agent_rig.products p
                WHERE p.tenant_id=$1 AND ($2 OR (btrim(coalesce(p.url,''))<>''
                AND NOT EXISTS (SELECT 1 FROM agent_rig.supplier_costs s WHERE s.tenant_id=p.tenant_id AND s.sku=p.sku)
                AND NOT EXISTS (SELECT 1 FROM agent_rig.feed_products f WHERE f.tenant_id=p.tenant_id AND f.sku=p.sku)
                AND NOT EXISTS (SELECT 1 FROM agent_rig.feeds f WHERE f.tenant_id=p.tenant_id AND NOT f.watch_indexed)
                )) AND NOT EXISTS (SELECT 1 FROM agent_rig.deleted_products d WHERE d.tenant_id=p.tenant_id AND d.sku=p.sku)
                ORDER BY p.sku""", UUID(str(tenant_id)), catalogue)
        return [dict(row) for row in rows]

    async def watch_applied_at(self, tenant_id: str, sku: str, url: str):
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""SELECT observed_at FROM agent_rig.tiger_receipts
                WHERE tenant_id=$1 AND sku=$2 AND url=$3""", UUID(str(tenant_id)), sku, url)

    async def mark_watch_applied(self, tenant_id: str, sku: str, url: str, stamp) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""INSERT INTO agent_rig.tiger_receipts(tenant_id,sku,url,observed_at)
                VALUES($1,$2,$3,$4) ON CONFLICT(tenant_id,sku,url)
                DO UPDATE SET observed_at=EXCLUDED.observed_at""", UUID(str(tenant_id)), sku, url, stamp)

    @asynccontextmanager
    async def atomic(self, tenant_id: str) -> AsyncIterator[Any]:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Serializes imports, rule changes and reviews for one tenant only.
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", "rig-retailer:" + str(tenant_id))
                yield type(self)(ConnectionPool(conn))

    async def rule(self, tenant_id: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            raw = await conn.fetchval("SELECT rule FROM agent_rig.price_rules WHERE tenant_id=$1", UUID(str(tenant_id)))
        return json.loads(raw) if isinstance(raw, str) else raw

    async def save_rule(self, tenant_id: str, rule: dict[str, Any]) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""INSERT INTO agent_rig.price_rules(tenant_id,rule) VALUES($1,$2::jsonb)
                ON CONFLICT(tenant_id) DO UPDATE SET rule=EXCLUDED.rule,updated_at=now()""", UUID(str(tenant_id)), json.dumps(rule))

    async def rate(self, tenant_id: str, currency: str, target: str = "ZAR") -> Any:
        if currency == target:
            return "1"
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT rate FROM agent_rig.exchange_rates WHERE tenant_id=$1 AND currency=$2", UUID(str(tenant_id)), currency if target == "ZAR" else currency + ":" + target)

    async def save_rate(self, tenant_id: str, currency: str, rate: Any, target: str = "ZAR") -> None:
        from decimal import Decimal
        async with self.pool.acquire() as conn:
            await conn.execute("""INSERT INTO agent_rig.exchange_rates(tenant_id,currency,rate) VALUES($1,$2,$3)
                ON CONFLICT(tenant_id,currency) DO UPDATE SET rate=EXCLUDED.rate,updated_at=now()""", UUID(str(tenant_id)), currency if target == "ZAR" else currency + ":" + target, Decimal(str(rate)))

    async def costs(self, tenant_id: str) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT sku,cost,currency,provider,site,feed_metadata FROM agent_rig.supplier_costs WHERE tenant_id=$1", UUID(str(tenant_id)))
        result = []
        for row in rows:
            values = dict(row)
            metadata = values.pop("feed_metadata")
            result.append({**values, **(json.loads(metadata) if isinstance(metadata, str) else metadata)})
        return result

    async def save_cost(self, tenant_id: str, row: dict[str, Any]) -> None:
        from decimal import Decimal
        async with self.pool.acquire() as conn:
            await conn.execute("""INSERT INTO agent_rig.supplier_costs(tenant_id,sku,cost,currency,provider,site,feed_metadata)
                VALUES($1,$2,$3,$4,$5,$6,$7::jsonb) ON CONFLICT(tenant_id,sku) DO UPDATE SET
                cost=EXCLUDED.cost,currency=EXCLUDED.currency,provider=EXCLUDED.provider,site=EXCLUDED.site,
                feed_metadata=EXCLUDED.feed_metadata,updated_at=now()""",
                UUID(str(tenant_id)), row["sku"], Decimal(str(row["cost"])), row["currency"], row["provider"], row["site"],
                json.dumps({key: row[key] for key in ("feed_id", "country", "price_basis") if key in row}))

    async def is_feed_product(self, tenant_id: str, sku: str) -> bool:
        async with self.pool.acquire() as conn:
            return bool(await conn.fetchval("""SELECT
                EXISTS(SELECT 1 FROM agent_rig.supplier_costs WHERE tenant_id=$1 AND sku=$2)
                OR EXISTS(SELECT 1 FROM agent_rig.feed_products WHERE tenant_id=$1 AND sku=$2)
                OR EXISTS(SELECT 1 FROM agent_rig.feeds WHERE tenant_id=$1 AND NOT watch_indexed)""", UUID(str(tenant_id)), sku))

    async def index_feed_products(self, tenant_id: str, url: str, skus: list[str], *, complete: bool = True) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""INSERT INTO agent_rig.feed_products(tenant_id,sku,feed_url)
                SELECT tenant_id,sku,$2 FROM agent_rig.products WHERE tenant_id=$1 AND sku=ANY($3::text[])
                ON CONFLICT DO NOTHING""", UUID(str(tenant_id)), url, skus)
            await conn.execute("UPDATE agent_rig.feeds SET watch_indexed=$3 WHERE tenant_id=$1 AND url=$2", UUID(str(tenant_id)), url, complete)

    async def save_feed_receipt(self, tenant_id: str, receipt: dict[str, Any]) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""INSERT INTO agent_rig.feed_receipts(tenant_id,id,provider,site,added,updated,rejected,feed_id)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8)""", UUID(str(tenant_id)), UUID(receipt["id"]), receipt["provider"], receipt["site"],
                receipt["added"], receipt["updated"], receipt["rejected"], receipt.get("feed_id", ""))

    async def receipts(self, tenant_id: str) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""SELECT id,provider,site,feed_id,added,updated,rejected,created_at FROM agent_rig.feed_receipts
                WHERE tenant_id=$1 ORDER BY created_at DESC LIMIT 30""", UUID(str(tenant_id)))
        return [{**dict(row), "id": str(row["id"]), "created_at": row["created_at"].isoformat()} for row in rows]

    async def missed_searches(self, tenant_id: str) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""SELECT query,count(*)::int AS count FROM agent_rig.searches
                WHERE tenant_id=$1 AND (NOT exact_match OR ids='[]'::jsonb)
                AND created_at >= now()-interval '90 days' GROUP BY query ORDER BY count(*) DESC,query LIMIT 50""", UUID(str(tenant_id)))
        return [dict(row) for row in rows]
