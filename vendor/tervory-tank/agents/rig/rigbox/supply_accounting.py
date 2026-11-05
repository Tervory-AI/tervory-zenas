"""Store-reported sale receipts, never inferred revenue from searches or quotes.

A receipt is an admin-provided accounting reference and signed net amount (a
negative receipt can record a refund). No payment, invoice or order is issued.
Amounts are grouped by currency; this module never estimates or converts them.
"""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from rigbox.locate import stamp
from rigbox.pricing import currency


async def record_receipt(service: Any, tenant: str, data: dict) -> bool:
    try:
        amount = Decimal(str(data["amount"]))
        if not amount.is_finite() or abs(amount) > Decimal("1000000000000") or amount.as_tuple().exponent < -2:
            raise ValueError()
        code = currency(data["currency"])
        when = stamp(data["received_at"])
        if when > datetime.now(timezone.utc):
            raise ValueError()
        if any(not isinstance(data.get(k), str) or not data[k].strip() or len(data[k]) > 200
               for k in ("receipt_id", "quote_id", "product_id", "source_ref")):
            raise ValueError()
    except (InvalidOperation, KeyError, TypeError):
        raise ValueError("Invalid sale receipt.") from None
    # A retailer receipt must refer to a supplier-backed product actually saved
    # on this tenant's quote. User-submitted product IDs are not sufficient.
    quotes = await service.store.quotes_for(tenant)
    quote = next((q for q in quotes if q["id"] == data["quote_id"]), None)
    if not quote or not any(line.get("product_id") == data["product_id"] and line.get("store")
                            for line in quote["payload"].get("locate", [])):
        raise ValueError("Receipt does not refer to a located request.")
    row = {**data, "amount": str(amount), "currency": code, "received_at": when.isoformat()}
    pool = getattr(service.store, "pool", None)
    if pool is None:
        if not hasattr(service.store, "supply_receipts"):
            service.store.supply_receipts = {}
        key = str(tenant), data["receipt_id"]
        previous = service.store.supply_receipts.get(key)
        if previous is not None:
            if previous != row:
                raise ValueError("Receipt identity already has different evidence.")
            return False
        service.store.supply_receipts[key] = row
        return True
    async with pool.acquire() as conn, conn.transaction():
        inserted = await conn.fetchval("""INSERT INTO agent_rig.supply_sale_receipts
            (tenant_id,receipt_id,quote_id,product_id,amount,currency,source_ref,received_at)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT(tenant_id,receipt_id) DO NOTHING RETURNING receipt_id""",
            UUID(str(tenant)), data["receipt_id"], data["quote_id"], data["product_id"], amount, code, data["source_ref"], when)
        if inserted is None:
            previous = await conn.fetchrow("""SELECT quote_id,product_id,amount,currency,source_ref,received_at
                FROM agent_rig.supply_sale_receipts WHERE tenant_id=$1 AND receipt_id=$2""", UUID(str(tenant)), data["receipt_id"])
            if (previous["quote_id"], previous["product_id"], previous["amount"], previous["currency"], previous["source_ref"], previous["received_at"]) != (
                    data["quote_id"], data["product_id"], amount, code, data["source_ref"], when):
                raise ValueError("Receipt identity already has different evidence.")
            return False
        await conn.execute("""INSERT INTO agent_rig.widget_counts(tenant_id,day,event,count,kept_revenue)
            VALUES($1,$2,'quotes',0,jsonb_build_object($3::text,$4::numeric::text))
            ON CONFLICT(tenant_id,day,event) DO UPDATE SET kept_revenue=
                agent_rig.widget_counts.kept_revenue || jsonb_build_object($3::text,
                (coalesce((agent_rig.widget_counts.kept_revenue ->> $3::text)::numeric,0)+$4::numeric)::text)""",
            UUID(str(tenant)), when.date(), code, amount)
    return True


async def analytics(service: Any, tenant: str, since: date) -> dict:
    pool = getattr(service.store, "pool", None)
    if pool is None:
        located = sum(n for (t, day), n in service.quote_store.located_counts.items() if t == tenant and day >= since)
        receipts = [row for (t, _), row in getattr(service.store, "supply_receipts", {}).items()
                    if t == tenant and stamp(row["received_at"]).date() >= since]
    else:
        async with pool.acquire() as conn:
            located = await conn.fetchval("""SELECT coalesce(sum(located_count),0) FROM agent_rig.widget_counts
                WHERE tenant_id=$1 AND day >= $2""", UUID(str(tenant)), since)
            receipts = await conn.fetch("""SELECT receipt_id,quote_id,product_id,amount,currency,source_ref,received_at
                FROM agent_rig.supply_sale_receipts WHERE tenant_id=$1 AND received_at >= $2""",
                UUID(str(tenant)), datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc))
    totals = {}
    for row in receipts:
        totals[row["currency"]] = totals.get(row["currency"], Decimal(0)) + Decimal(str(row["amount"]))
    return {"located_count": int(located), "located_count_provenance": "persisted_supplier_locate_quote_requests",
            "kept_revenue": {code: str(value) for code, value in sorted(totals.items())} if receipts else None,
            "kept_revenue_provenance": "store_reported_sale_receipts" if receipts else "no_sale_receipts",
            "receipt_count": len(receipts)}
