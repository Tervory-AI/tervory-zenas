"""Optional blind ship: Locate orders ship from the other store straight to the shopper.

Per-store ``blind_ship`` switch, off by default. Per-source
``neutral_slip_required``. The order and the supplier PO are Rig's own rows
(rigbox.fulfilment): the PO carries the shopper's
delivery address and, when the source requires it, the neutral-slip
instruction. No supplier identity reaches the shopper. Offline here, except the
real Postgres test (RIG_TEST_DATABASE_URL).
"""
import asyncio
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents" / "rig"))
sys.path.insert(0, str(ROOT / "tests"))
from rigbox.api import mount
from rigbox.locate import SupplyStore
from rigbox.locate_live import confirm
from rigbox.quotes.api import mount as mount_quotes
from fixtures.rig_quote_sample import KEY, TENANT as SAMPLE, seed
from rigbox.fulfilment import NEUTRAL_SLIP, RigOrders, numbers
from rigbox.quotes.senders import DeliveryError
from rigbox.quotes.service import QuoteService, ship_address
from rigbox.quotes.store import MemoryQuotes
from rigbox.retailer import Retailer
from rigbox.service import RigError, Service
from rigbox.store import MemoryStore
from test_rig_locate_checkout import TENANT, Live, available, rate_stub, shop

ADDRESS = {"name": "Thandi Mokoena", "street": "12 Long Street", "city": "Cape Town", "zip": "8001",
           "state": "Western Cape", "country": "za", "phone": "+27 21 000 0000"}
SUPPLIER = ("other-store", "other.example", "RSR|POF90307", "RSR", "/product/cam-pin")


class HandoffDouble:
    """Test double for the retailer handoff (the quote email or webhook)."""

    def __init__(self):
        self.sent = []

    async def send(self, *, tenant_id, email, webhook, quote):
        self.sent.append(quote)


class FailingOrders(RigOrders):
    async def place_located_order(self, **kwargs):
        raise DeliveryError("order_unavailable")


def only_order(store, tenant=TENANT):
    [(key, row)] = [(key, row) for key, row in store.locate_orders.items() if key[0] == tenant]
    return row["order"], row["purchase_order"]


def env(monkeypatch):
    monkeypatch.setenv("RIG_LOCATE_HOLD_SECRET", "s" * 40)
    monkeypatch.setenv("RIG_LOCATE_ORDER_MODE", "sandbox")


async def blind_shop(monkeypatch, *, blind=True, neutral=True, fail=False):
    env(monkeypatch)
    store = await rate_stub(await shop())
    await store.put_retailer(TENANT, "shop", "shop@example.com", None, "Shop", blind_ship=blind)
    await SupplyStore(store).source(TENANT, dict(id="other-store", approved=True, priority=1,
        max_offer_age_seconds=3600, provider="tiger", site="https://other.example", neutral_slip_required=neutral))
    orders = HandoffDouble()
    service = QuoteService(store, None, orders, MemoryQuotes(store))
    if fail:
        service.fulfilment = FailingOrders(store)
    service.locate_checker = Live(available("45.00"))
    held = await service.locate_confirm(TENANT, "part-1")
    body = {"product_id": "part-1", "hold": held["hold"],
            "contact": {"name": "Thandi Mokoena", "email": "thandi@example.com"}, "delivery": dict(ADDRESS)}
    return store, service, orders, held, body


# 1. Switches: off by default, explicit booleans, persisted.

@pytest.mark.asyncio
async def test_blind_ship_is_off_by_default_and_settings_need_a_boolean():
    store = await rate_stub(await shop())
    assert (await store.retailer(TENANT))["blind_ship"] is False
    retailer = Retailer(Service(store))
    base = {"name": "Shop", "email": "shop@example.com"}
    for bad in ("true", 1, None):
        with pytest.raises(ValueError):
            await retailer.set_settings(TENANT, {**base, "blind_ship": bad})
    await retailer.set_settings(TENANT, {**base, "blind_ship": True})
    assert (await retailer.panel(TENANT))["settings"]["blind_ship"] is True
    await retailer.set_settings(TENANT, base)  # absent keeps the saved value
    assert (await retailer.panel(TENANT))["settings"]["blind_ship"] is True
    await store.put_retailer(TENANT, "shop", "a@example.com", None, "Shop", locate_enabled=False)
    assert (await store.retailer(TENANT))["blind_ship"] is True
    await retailer.set_settings(TENANT, {**base, "blind_ship": False})
    assert (await retailer.panel(TENANT))["settings"]["blind_ship"] is False


@pytest.mark.asyncio
async def test_neutral_slip_flag_is_per_source_boolean_and_reaches_the_private_hold():
    store = await rate_stub(await shop())
    supply = SupplyStore(store)
    base = dict(id="other-store", approved=True, priority=1, max_offer_age_seconds=3600, provider="tiger",
                site="https://other.example")
    with pytest.raises(ValueError):
        await supply.source(TENANT, {**base, "neutral_slip_required": "yes"})
    sources, _, _ = await supply.snapshot(TENANT)
    assert sources[0]["neutral_slip_required"] is False  # default
    await supply.source(TENANT, {**base, "neutral_slip_required": True})
    result = await confirm(store, TENANT, (await store.products(TENANT))[0], Live(available()), secret=b"t" * 40)
    assert result["store"]["source"]["neutral_slip_required"] is True
    assert "neutral_slip_required" not in json.dumps(result["public"])


def test_ship_address_is_strict():
    assert ship_address(ADDRESS)["country"] == "ZA"
    for bad in (None, "12 Long Street", {**ADDRESS, "country": "ZAF"}, {**ADDRESS, "street": ""},
                {**ADDRESS, "zip": None}, {**ADDRESS, "city": "x" * 121}, {**ADDRESS, "name": "a\r\nBcc: x"},
                {**ADDRESS, "company": "x"}, {**ADDRESS, "street": 12}):
        with pytest.raises(RigError) as raised:
            ship_address(bad)
        assert raised.value.status == 422


# 2. Accept with blind ship.

@pytest.mark.asyncio
async def test_blind_accept_records_a_rig_order_and_a_neutral_slip_po_to_the_shopper(monkeypatch):
    store, service, orders, held, body = await blind_shop(monkeypatch)
    accepted = await service.locate_accept(TENANT, body)
    assert accepted["state"] == "accepted" and store.locate_orders == {}
    result = await service.confirm_store_payment(TENANT, accepted["request_id"])
    assert result["state"] == "ordered"
    order, po = only_order(store)
    number, po_number = numbers(TENANT, result["request_id"])
    assert order == {"quote_id": result["request_id"], "number": number, "state": "placed", "product_id": "part-1",
                     "price": held["price"]["amount"], "currency": "ZAR", "ship_to": {**ADDRESS, "country": "ZA"}}
    assert po["number"] == po_number and po["state"] == "draft" and po["source_id"] == "other-store"
    assert po["ship_to"] == {**ADDRESS, "country": "ZA"}
    assert po["neutral_slip"] is True and po["instructions"] == NEUTRAL_SLIP
    assert (po["item_sku"], po["item_url"], po["unit_cost"], po["currency"]) == (
        "RSR|POF90307", "https://other.example/product/cam-pin/", "45.00", "USD")
    quote = next(q for q in store.quotes if q["id"] == result["request_id"])
    assert quote["payload"]["ship_to"]["city"] == "Cape Town"
    state = quote["payload"]["locate_state"]
    assert state["blind_ship"] is True and (state["order"], state["purchase_order"]) == (number, po_number)
    # Shopper text never carries the supplier, its cost or the slip instruction.
    text = json.dumps(result)
    for marker in ("other-store", "other.example", "RSR", "45.00", "SHIP BLIND", po_number):
        assert marker not in text


@pytest.mark.asyncio
async def test_blind_accept_without_address_refuses_before_hold_or_payment(monkeypatch):
    store, service, orders, held, body = await blind_shop(monkeypatch)
    for delivery in (None, {**ADDRESS, "street": ""}):
        with pytest.raises(RigError) as raised:
            await service.locate_accept(TENANT, {**body, "delivery": delivery})
        assert raised.value.status == 422
    assert orders.sent == [] and store.locate_orders == {}
    hold_id = held["hold"].split(".")[0]
    assert store.supply_state[TENANT]["holds"][hold_id]["used_at"] is None
    assert (await service.locate_accept(TENANT, body))["state"] == "accepted"


@pytest.mark.asyncio
async def test_switch_off_keeps_the_existing_ship_to_store_order(monkeypatch):
    store, service, orders, held, body = await blind_shop(monkeypatch, blind=False)
    accepted = await service.locate_accept(TENANT, body)
    assert accepted["state"] == "accepted" and store.locate_orders == {}
    result = await service.confirm_store_payment(TENANT, accepted["request_id"])
    assert result["state"] == "ordered"
    order, po = only_order(store)
    assert order["ship_to"] is None and po["ship_to"] is None
    assert po["neutral_slip"] is False and po["instructions"] == ""  # ships to the store: no blind slip
    assert "ship_to" not in store.quotes[-1]["payload"]


@pytest.mark.asyncio
async def test_blind_transient_order_failure_releases_lease(monkeypatch):
    store, service, orders, _, body = await blind_shop(monkeypatch, fail=True)
    accepted = await service.locate_accept(TENANT, body)
    result = await service.confirm_store_payment(TENANT, accepted["request_id"])
    assert result["state"] == "store_confirmed" and store.locate_orders == {}
    state = (await store.quote_for(TENANT, accepted["request_id"]))["payload"]["locate_state"]
    assert state["attempted_at"] is None
    service.fulfilment = RigOrders(store)
    assert (await service.confirm_store_payment(TENANT, accepted["request_id"]))["state"] == "ordered"
    assert len(store.locate_orders) == 1


# 3. Shopper HTTP responses: no supplier identity or supplier cost (an invented POF-style row, 92445).
# The catalogue attribute export_usd is #505's cost guard; this row carries only shopper-safe attributes.

POF_92445 = dict(sku="92445", id="92445", gtin="2000000924458",
                 name="Test Suppressor, invented for this test, BLACK",
                 brand="POF-USA", category="Suppressors", price="12345", currency="ZAR",
                 image_url="https://assets.local/pof-92445.jpg", active=True)


def pof_row(sku):
    assert sku == "92445"
    return {**POF_92445, "attributes": {"color": "BLACK", "stock": {"status": "out_of_stock"}}}


def test_locate_confirm_accept_responses_have_no_supplier_identity_or_cost(monkeypatch):
    env(monkeypatch)

    async def ready():
        store = MemoryStore()
        await seed(store)
        await store.upsert_product(SAMPLE, pof_row("92445"))
        await store.save_rule(SAMPLE, {"freight": "5", "duty_percent": "0", "margin_percent": "20"})
        retailer = await store.retailer(SAMPLE)
        await store.put_retailer(SAMPLE, retailer["slug"], retailer["email"], retailer["webhook"], retailer["name"],
                                 locate_enabled=True, blind_ship=True)
        await SupplyStore(store).source(SAMPLE, dict(id="other-store", approved=True, priority=1,
            max_offer_age_seconds=3600, provider="tiger", site="https://other.example", neutral_slip_required=True))
        from datetime import datetime, timezone
        store.supply_state[SAMPLE]["offers"] = [dict(
            tenant_id=SAMPLE, source_id="other-store", sku="RSR|POF92445", gtin="2000000924458", brand="POF-USA",
            mpn=None, quantity="1", status="available", eta="2026-11-20", ship_days=8, region="US",
            cost="987", currency="USD", seen_at=datetime.now(timezone.utc),
            url="https://other.example/product/cam-pin/")]
        await rate_stub(store)
        service = QuoteService(store, None, HandoffDouble(), MemoryQuotes(store))
        service.locate_checker = Live(available("987.00"))
        return service

    app = FastAPI()
    app.state.rig = asyncio.run(ready())
    mount(app)
    mount_quotes(app)
    headers = {"X-Rig-Key": KEY, "Origin": "http://127.0.0.1:8001"}
    with TestClient(app) as client:
        located = client.post("/v1/rig/locate", json={"product_id": "92445"}, headers=headers)
        held = client.post("/v1/rig/locate/confirm", json={"product_id": "92445"}, headers=headers)
        product = client.get("/v1/rig/products/92445", headers=headers)
        assert located.status_code == held.status_code == product.status_code == 200, (located.text, held.text)
        assert held.json()["status"] == "available_to_order"
        accepted = client.post("/v1/rig/locate/accept", headers=headers, json={
            "product_id": "92445", "hold": held.json()["hold"],
            "contact": {"name": "Thandi Mokoena", "email": "thandi@example.com"}, "delivery": ADDRESS})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["state"] == "accepted"
        assert accepted.json()["items"][0]["delivery"]["ship_days"] == 8
    for response in (located, held, product, accepted):
        text = response.text
        # The supplier's live cost (a made-up 987 USD, not a real supplier price). Random ids (hold token, request id,
        # payment reference) are dropped first: their hex can contain "987".
        assert re.search(r"(?<![0-9A-Za-z])987(?:\.0+)?(?![0-9A-Za-z])", re.sub(r'"(hold|request_id|reference)":"[^"]*"', "", text)) is None, text
        assert "neutral_slip" not in text and "landed" not in text
        for marker in ("other-store", "other.example", "RSR|POF92445", "RSR", "tiger"):
            assert marker not in text, (marker, text)
    assert app.state.rig.store.locate_orders == {}


# 4. RigOrders: Rig's own order rows, no ERP.

LOCATED = {"product_sku": "part-1",
           "source": {"id": "other-store", "site": "https://other.example", "neutral_slip_required": True},
           "offer": {"sku": "RSR|POF90307", "url": "https://other.example/product/cam-pin/"},
           "live": {"price": "45.00", "currency": "USD"}}
PRICE = {"amount": "1125.00", "currency": "ZAR"}
SHIP = {"address": {**ADDRESS, "country": "ZA"}}


@pytest.mark.asyncio
async def test_rig_orders_refuse_outside_sandbox_before_any_write(monkeypatch):
    store = MemoryStore()
    for mode in ("", "live"):
        with pytest.raises(DeliveryError) as raised:
            await RigOrders(store).place_located_order(tenant_id=TENANT, quote_id="q1", located=LOCATED,
                                                       price=PRICE, ship=SHIP, env={"RIG_LOCATE_ORDER_MODE": mode})
        assert str(raised.value) == "order_mode"
    assert store.locate_orders == {}


@pytest.mark.asyncio
async def test_rig_orders_are_idempotent_per_quote_and_tenant_scoped():
    store, sandbox = MemoryStore(), {"RIG_LOCATE_ORDER_MODE": "sandbox"}
    orders = RigOrders(store)
    first = await orders.place_located_order(tenant_id=TENANT, quote_id="q1", located=LOCATED, price=PRICE,
                                             ship=SHIP, env=sandbox)
    again = await orders.place_located_order(tenant_id=TENANT, quote_id="q1", located=LOCATED, price=PRICE,
                                             ship=SHIP, env=sandbox)
    assert first == again and len(store.locate_orders) == 1
    assert first["purchase_order"] == {"number": numbers(TENANT, "q1")[1], "state": "draft",
                                       "ship_direct": True, "neutral_slip": True}
    other = "00000000-0000-4000-8000-000000000999"
    elsewhere = await orders.place_located_order(tenant_id=other, quote_id="q1", located=LOCATED, price=PRICE,
                                                 env=sandbox)
    assert elsewhere["order"]["number"] != first["order"]["number"] and len(store.locate_orders) == 2
    assert await store.locate_order(other, "q1") != await store.locate_order(TENANT, "q1")
    assert elsewhere["purchase_order"]["neutral_slip"] is False  # not blind: no slip instruction


@pytest.mark.asyncio
@pytest.mark.parametrize("located,price", [
    ({**LOCATED, "live": {"price": "0", "currency": "USD"}}, PRICE),
    ({**LOCATED, "live": {"price": "45.00", "currency": "usd$"}}, PRICE),
    ({k: v for k, v in LOCATED.items() if k != "source"}, PRICE),
    (LOCATED, {"amount": "NaN", "currency": "ZAR"}),
])
async def test_rig_orders_reject_a_bad_snapshot_without_writing(located, price):
    store = MemoryStore()
    with pytest.raises(DeliveryError) as raised:
        await RigOrders(store).place_located_order(tenant_id=TENANT, quote_id="q1", located=located, price=price,
                                                   env={"RIG_LOCATE_ORDER_MODE": "sandbox"})
    assert str(raised.value) == "order_rejected" and store.locate_orders == {}


# 5. Real Postgres: defaults false, values persist; orders and POs are Rig's own rows.

@pytest.mark.asyncio
async def test_real_postgres_blind_ship_and_neutral_slip_defaults_and_persistence():
    import asyncpg
    from uuid import uuid4
    from test_rig_postgres import _admin_url, _create_database, _database_url, _drop_database
    from rigbox.store import PgStore
    admin = _admin_url()
    name = "rig_blind_" + uuid4().hex[:12]
    await _create_database(admin, name)
    try:
        pool = await asyncpg.create_pool(_database_url(admin, name))
        try:
            store = PgStore(pool)
            await store.apply_schema()
            await store.apply_schema()
            await store.put_retailer(TENANT, "shop", None, None, "Shop")
            assert (await store.retailer(TENANT))["blind_ship"] is False
            await store.put_retailer(TENANT, "shop", None, None, "Shop", blind_ship=True)
            await store.put_retailer(TENANT, "shop", "a@example.com", None, "Shop", locate_enabled=True)
            assert (await store.retailer(TENANT))["blind_ship"] is True
            supply = SupplyStore(store)
            base = dict(id="other-store", approved=True, priority=1, max_offer_age_seconds=3600, provider="tiger",
                        site="https://other.example")
            await supply.source(TENANT, base)
            assert (await supply.snapshot(TENANT))[0][0]["neutral_slip_required"] is False
            await supply.source(TENANT, {**base, "neutral_slip_required": True})
            assert (await supply.snapshot(TENANT))[0][0]["neutral_slip_required"] is True
            # Rig-owned order + PO: one row each per quote, even under concurrent Accepts.
            orders, sandbox = RigOrders(store), {"RIG_LOCATE_ORDER_MODE": "sandbox"}
            placed = await asyncio.gather(*[orders.place_located_order(
                tenant_id=TENANT, quote_id="req_1", located=LOCATED, price=PRICE, ship=SHIP, env=sandbox)
                for _ in range(4)])
            assert all(item == placed[0] for item in placed)
            saved = await store.locate_order(TENANT, "req_1")
            assert saved["order"]["price"] == "1125.00" and saved["order"]["ship_to"] == SHIP["address"]
            assert saved["purchase_order"]["unit_cost"] == "45.00" and saved["purchase_order"]["neutral_slip"] is True
            assert saved["purchase_order"]["instructions"] == NEUTRAL_SLIP
            assert await pool.fetchval("SELECT count(*) FROM agent_rig.locate_orders") == 1
            assert await pool.fetchval("SELECT count(*) FROM agent_rig.supplier_purchase_orders") == 1
            assert await store.locate_order("00000000-0000-4000-8000-000000000999", "req_1") is None
            unblind = await orders.place_located_order(tenant_id=TENANT, quote_id="req_2", located=LOCATED,
                                                       price=PRICE, env=sandbox)
            row = await store.locate_order(TENANT, "req_2")
            assert unblind["purchase_order"]["ship_direct"] is False and row["purchase_order"]["ship_to"] is None
            print("RIG_ORDERS_PG", json.dumps(saved, sort_keys=True))
            defaults = await pool.fetch("""SELECT table_name, column_default, is_nullable FROM information_schema.columns
                WHERE table_schema='agent_rig' AND column_name IN ('blind_ship', 'neutral_slip_required')""")
            assert {(r["table_name"], r["column_default"], r["is_nullable"]) for r in defaults} == {
                ("retailers", "false", "NO"), ("supply_sources", "false", "NO")}
        finally:
            await pool.close()
    finally:
        await _drop_database(admin, name)


@pytest.mark.asyncio
async def test_real_postgres_concurrent_orders_for_one_quote_never_raise():
    """Four concurrent place_located_order calls per quote, 1,000 rounds: every
    call reads back the one order (0 errors). ON CONFLICT (tenant, quote) alone
    let the (tenant, number) unique index raise, wrapped as order_unavailable."""
    import asyncpg
    from uuid import uuid4
    from test_rig_postgres import _admin_url, _create_database, _database_url, _drop_database
    from rigbox.store import PgStore
    admin = _admin_url()
    name = "rig_race_" + uuid4().hex[:12]
    await _create_database(admin, name)
    try:
        pool = await asyncpg.create_pool(_database_url(admin, name), min_size=4, max_size=8)
        try:
            store = PgStore(pool)
            await store.apply_schema()
            orders, sandbox = RigOrders(store), {"RIG_LOCATE_ORDER_MODE": "sandbox"}
            errors, rounds = [], 1000
            for index in range(rounds):
                results = await asyncio.gather(*[orders.place_located_order(
                    tenant_id=TENANT, quote_id=f"req_race_{index}", located=LOCATED, price=PRICE, ship=SHIP,
                    env=sandbox) for _ in range(4)], return_exceptions=True)
                errors += [result for result in results if isinstance(result, Exception)]
                assert all(result == results[0] for result in results if not isinstance(result, Exception))
            assert errors == []
            assert await pool.fetchval("SELECT count(*) FROM agent_rig.locate_orders") == rounds
            assert await pool.fetchval("SELECT count(*) FROM agent_rig.supplier_purchase_orders") == rounds
        finally:
            await pool.close()
    finally:
        await _drop_database(admin, name)


@pytest.mark.asyncio
async def test_real_postgres_an_order_number_taken_by_another_quote_is_a_mismatch(monkeypatch):
    import asyncpg
    from uuid import uuid4
    from test_rig_postgres import _admin_url, _create_database, _database_url, _drop_database
    from rigbox import fulfilment
    from rigbox.store import PgStore
    admin = _admin_url()
    name = "rig_race_" + uuid4().hex[:12]
    await _create_database(admin, name)
    try:
        pool = await asyncpg.create_pool(_database_url(admin, name))
        try:
            store = PgStore(pool)
            await store.apply_schema()
            orders, sandbox = RigOrders(store), {"RIG_LOCATE_ORDER_MODE": "sandbox"}
            await orders.place_located_order(tenant_id=TENANT, quote_id="req_a", located=LOCATED, price=PRICE,
                                             env=sandbox)
            taken = fulfilment.numbers(TENANT, "req_a")
            monkeypatch.setattr(fulfilment, "numbers", lambda tenant, quote: taken)  # forced collision
            with pytest.raises(DeliveryError, match="order_mismatch"):
                await orders.place_located_order(tenant_id=TENANT, quote_id="req_b", located=LOCATED,
                                                 price=PRICE, env=sandbox)
            assert await store.locate_order(TENANT, "req_b") is None
            assert await pool.fetchval("SELECT count(*) FROM agent_rig.locate_orders") == 1
        finally:
            await pool.close()
    finally:
        await _drop_database(admin, name)
