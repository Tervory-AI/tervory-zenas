"""Locate switch, live confirm, holds and sandbox-only order guards.

Offline. Checkers and the order step are test doubles here only.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents" / "rig"))
from rigbox.locate import SupplyStore, catalogue, enabled
from rigbox.locate_live import (Checkers, Holds, LiveResult, LocateRefused, SourceApiChecker, TigerChecker,
                                confirm, hold_token, read_token)
from rigbox.fulfilment import sandbox_orders
from rigbox.quotes.senders import DeliveryError
from rigbox.quotes.privacy import shopper_body
from rigbox.quotes.service import QuoteService
from rigbox.quotes.store import MemoryQuotes
from rigbox.retailer import Retailer
from rigbox.service import RigError, Service
from rigbox.store import MemoryStore

NOW = datetime.now(timezone.utc)
TENANT = "00000000-0000-0000-0000-000000000201"
OTHER = "00000000-0000-0000-0000-000000000202"
SECRET = b"t" * 40


class Live:
    """Test double checker."""

    def __init__(self, result):
        self.result, self.calls = result, []

    async def check(self, tenant, source, offer):
        self.calls.append((tenant, source["id"], offer["sku"]))
        return self.result


def available(price="42.00", currency="USD", checked=None, code=None):
    return LiveResult("available", Decimal(price), currency, checked or datetime.now(timezone.utc), code, "double")


async def shop(enabled_flag=True, tenant=TENANT):
    store = MemoryStore()
    product = dict(sku="part-1", id="part-1", gtin="2000000903071", mpn="90307", name="Roller cam pin",
                   brand="POF-USA", category="Parts", price="2345", currency="ZAR",
                   image_url="https://example.com/part.png", active=True,
                   attributes={"stock": {"status": "out_of_stock"}})
    await store.upsert_product(tenant, product)
    await store.save_rule(tenant, {"freight": "5", "duty_percent": "0", "margin_percent": "20"})
    await store.put_retailer(tenant, "shop", "shop@example.com", None, "Shop", locate_enabled=enabled_flag)
    supply = SupplyStore(store)
    await supply.source(tenant, dict(id="other-store", approved=True, priority=1, max_offer_age_seconds=3600,
                                     provider="tiger", site="https://other.example"))
    store.supply_state[tenant]["offers"] = [dict(
        tenant_id=tenant, source_id="other-store", sku="RSR|POF90307", gtin="2000000903071", brand="POF-USA",
        mpn="90307", quantity="1", status="available", eta="2026-11-12", ship_days=10, region="US",
        cost="40", currency="USD", seen_at=NOW, url="https://other.example/product/cam-pin/")]
    return store


async def rate_stub(store):
    async def rate(tenant, source, target):
        if (source, target) == ("USD", "ZAR"):
            return Decimal("18")
        if source == target:
            return Decimal("1")
        raise ValueError("no rate")
    store.rate = rate
    return store


# 1. Switch: off by default, refusal and skipped supply lookup.

@pytest.mark.asyncio
async def test_switch_is_off_by_default_and_persists_explicit_values():
    store = MemoryStore()
    await store.put_retailer(TENANT, "shop", None, None, "Shop")
    assert await enabled(store, TENANT) is False
    assert (await store.retailer(TENANT))["locate_enabled"] is False
    await store.put_retailer(TENANT, "shop", None, None, "Shop", locate_enabled=True)
    # Saving other settings without the field keeps the saved value.
    await store.put_retailer(TENANT, "shop", "a@example.com", None, "Shop")
    assert await enabled(store, TENANT) is True
    assert await enabled(MemoryStore(), OTHER) is False


@pytest.mark.asyncio
async def test_settings_toggle_requires_a_boolean_and_panel_reports_it():
    store = await rate_stub(await shop(False))
    retailer = Retailer(Service(store))
    base = {"name": "Shop", "email": "shop@example.com"}
    for bad in ("true", 1, None):
        with pytest.raises(ValueError):
            await retailer.set_settings(TENANT, {**base, "locate_enabled": bad})
    await retailer.set_settings(TENANT, {**base, "locate_enabled": True})
    assert (await retailer.panel(TENANT))["settings"]["locate_enabled"] is True
    await retailer.set_settings(TENANT, base)
    assert (await retailer.panel(TENANT))["settings"]["locate_enabled"] is True
    await retailer.set_settings(TENANT, {**base, "locate_enabled": False})
    assert (await retailer.panel(TENANT))["settings"]["locate_enabled"] is False


@pytest.mark.asyncio
async def test_off_refuses_locate_confirm_accept_and_skips_search_and_quote_lookup():
    store = await rate_stub(await shop(False))
    service = QuoteService(store, None, None, MemoryQuotes(store))
    service.locate_checker = Live(available())
    for call in (service.locate(TENANT, "part-1"), service.locate_confirm(TENANT, "part-1"),
                 service.locate_accept(TENANT, {"product_id": "part-1"})):
        with pytest.raises(RigError) as raised:
            await call
        assert raised.value.status == 403 and raised.value.code == "locate_disabled"
    rows = await catalogue(store, TENANT)
    assert "availability" not in rows[0]
    payload, _ = await service.quote_payload(TENANT, {"product_id": "part-1", "contact": {
        "name": "Shopper", "email": "shopper@example.com"}})
    assert "locate" not in payload
    assert service.locate_checker.calls == []


# 2. Confirm: live re-check, freshness, unknown never available.

@pytest.mark.asyncio
async def test_confirm_issues_hold_from_live_price_without_supplier_fields():
    store = await rate_stub(await shop())
    checker = Live(available("45.00"))
    product = (await store.products(TENANT))[0]
    result = await confirm(store, TENANT, product, checker, secret=SECRET)
    public = result["public"]
    assert public["status"] == "available_to_order"
    # (45 + 5) * 18 / 0.8 from the live price, not the imported 40.
    assert public["price"] == {"amount": "1125.00", "currency": "ZAR"}
    assert checker.calls == [(TENANT, "other-store", "RSR|POF90307")]
    assert set(shopper_body(public)) == {"status", "eta", "ship_days", "price", "expires_at", "hold"}
    assert "other-store" not in json.dumps(shopper_body(public)) and "RSR" not in json.dumps(public)
    assert read_token(SECRET, TENANT, public["hold"], datetime.now(timezone.utc)) == result["hold_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("live,expected", [
    (LiveResult.unknown("double", "blocked"), "check_with_store"),
    (LiveResult("unavailable", method="double"), "unavailable"),
    (LiveResult("available", None, "USD", NOW, None, "double"), "check_with_store"),
    ("stale-live", "check_with_store"),
    ("other-gtin", "check_with_store"),
])
async def test_confirm_never_shows_unknown_or_stale_stock_as_available(live, expected):
    store = await rate_stub(await shop())
    if live == "stale-live":
        live = available(checked=datetime.now(timezone.utc) - timedelta(hours=2))
    elif live == "other-gtin":
        live = available(code="00000000000000")
    result = await confirm(store, TENANT, (await store.products(TENANT))[0], Live(live), secret=SECRET)
    assert result["public"] == {"status": expected}
    assert not store.supply_state[TENANT].get("holds")


@pytest.mark.asyncio
async def test_confirm_rechecks_offer_freshness_before_any_live_call():
    store = await rate_stub(await shop())
    store.supply_state[TENANT]["offers"][0]["seen_at"] = NOW - timedelta(hours=3)
    checker = Live(available())
    result = await confirm(store, TENANT, (await store.products(TENANT))[0], checker, secret=SECRET)
    assert result["public"] == {"status": "check_with_store"} and checker.calls == []


@pytest.mark.asyncio
async def test_provider_without_live_checker_is_unknown():
    store = await rate_stub(await shop())
    checkers = Checkers({"api": Live(available())})
    result = await confirm(store, TENANT, (await store.products(TENANT))[0], checkers, secret=SECRET)
    assert result["public"] == {"status": "check_with_store"}


@pytest.mark.asyncio
async def test_service_confirm_needs_checker_and_hold_secret(monkeypatch):
    store = await rate_stub(await shop())
    service = Service(store)
    monkeypatch.delenv("RIG_LOCATE_HOLD_SECRET", raising=False)
    with pytest.raises(RigError) as raised:
        await service.locate_confirm(TENANT, "part-1")
    assert raised.value.status == 503
    service.locate_checker = Live(available())
    with pytest.raises(RigError) as raised:
        await service.locate_confirm(TENANT, "part-1")
    assert raised.value.status == 503
    monkeypatch.setenv("RIG_LOCATE_HOLD_SECRET", "s" * 40)
    assert (await service.locate_confirm(TENANT, "part-1"))["status"] == "available_to_order"


# 3. Holds: signed, short-lived, single-use and tenant scoped.

@pytest.mark.asyncio
async def test_hold_token_rejects_tamper_other_tenant_expiry_and_reuse():
    store = await rate_stub(await shop())
    result = await confirm(store, TENANT, (await store.products(TENANT))[0], Live(available()), secret=SECRET)
    token, now = result["public"]["hold"], datetime.now(timezone.utc)
    hold_id, expires, signature = token.split(".")
    for bad in (f"{hold_id}.{int(expires) + 600}.{signature}", f"{hold_id}.{expires}.{'0' * 64}", "x"):
        with pytest.raises(LocateRefused):
            read_token(SECRET, TENANT, bad, now)
    with pytest.raises(LocateRefused):
        read_token(SECRET, OTHER, token, now)
    with pytest.raises(LocateRefused, match="hold_expired"):
        read_token(SECRET, TENANT, token, now + timedelta(hours=1))
    assert await Holds(store).take(OTHER, hold_id, now) is None
    assert (await Holds(store).take(TENANT, hold_id, now))["product_id"] == "part-1"
    assert await Holds(store).take(TENANT, hold_id, now) is None
    stale = hold_token(SECRET, TENANT, hold_id, int(now.timestamp()) - 1)
    with pytest.raises(LocateRefused):
        read_token(SECRET, TENANT, stale, now)


# 4. Payments: test mode only, no live key.





# 5. Orders: sandbox only.

def test_order_placement_refuses_outside_sandbox():
    for value in ("", "live", "production", "SANDBOX"):
        with pytest.raises(DeliveryError, match="order_mode"):
            sandbox_orders({"RIG_LOCATE_ORDER_MODE": value})
    sandbox_orders({"RIG_LOCATE_ORDER_MODE": "sandbox"})


class SandboxOrders:
    """Test double for the retailer handoff and the order step (rigbox.fulfilment)."""

    def __init__(self, fail=False):
        self.sent, self.placed, self.fail = [], [], fail

    async def send(self, *, tenant_id, email, webhook, quote):
        self.sent.append((tenant_id, quote["id"]))

    async def place_located_order(self, *, tenant_id, quote_id, located, price=None, ship=None):
        sandbox_orders()
        if self.fail:
            raise DeliveryError("order_rejected")
        self.placed.append((tenant_id, quote_id, located["offer"]["sku"], located["live"]["price"]))
        return {"order": {"number": "S00001", "state": "placed"},
                "purchase_order": {"number": "P00001", "state": "draft"}}














# 6. Product checkers: unknown unless explicitly configured and answered.

@pytest.mark.asyncio
async def test_product_checkers_fail_closed():
    source = {"id": "other-store", "provider": "tiger"}
    offer = {"sku": "x", "url": "https://other.example/p"}
    seen = []

    def answer(request):
        seen.append((str(request.url), dict(request.headers)))
        if request.url.path == "/rig/check-now":
            return httpx.Response(200, json={"status": "available", "price": "45.00", "currency": "USD",
                                             "checked_at": NOW.isoformat(), "gtin": "2000000903071"})
        return httpx.Response(200, json={"status": "available"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        assert (await TigerChecker(http, {}).check(TENANT, source, offer)).status == "unknown"
        assert (await TigerChecker(http, {"RIG_TIGER_URL": "http://tiger", "RIG_TIGER_CHECK_KEY": "k"}).check(
            TENANT, source, {"sku": "x", "url": "http://other.example/p"})).status == "unknown"
        live = await TigerChecker(http, {"RIG_TIGER_URL": "http://tiger", "RIG_TIGER_CHECK_KEY": "k"}).check(
            TENANT, source, offer)
        assert live.status == "available" and live.price == Decimal("45.00") and live.gtin == "02000000903071"
        assert seen[-1][1]["x-rig-check-key"] == "k"
        api = {"id": "other-store", "provider": "api"}
        assert (await SourceApiChecker(http, {}).check(TENANT, api, offer)).status == "unknown"
        env = {"RIG_SUPPLY_API_URL_OTHER_STORE": "https://supplier.example/v1", "RIG_SUPPLY_API_KEY_OTHER_STORE": "k"}
        # Available without a stated price is unknown.
        assert (await SourceApiChecker(http, env).check(TENANT, api, offer)).status == "unknown"
        env["RIG_SUPPLY_API_URL_OTHER_STORE"] = "http://supplier.example/v1"
        assert (await SourceApiChecker(http, env).check(TENANT, api, offer)).status == "unknown"


# 7. Tiger check-now: a watched page, robots, explicit stock and price only.

class Watches:
    def __init__(self, url):
        self.url = url

    async def get_watch(self, tenant, url):
        return {"enabled": True} if url == self.url else None


@pytest.mark.asyncio
async def test_tiger_check_now_reads_only_explicit_stock_on_watched_pages():
    import asyncio
    sys.path.insert(0, str(ROOT / "agents" / "tiger"))
    from rig_watch import check_now
    url = "https://other.example/product/cam-pin/"

    async def robots(_):
        return "User-agent: *\nDisallow: /cart/"

    def page(availability, price="234.00"):
        async def render(_):
            return {"ok": True, "html": "<html></html>"}
        async def extract(html, _):
            return {"method": "jsonld", "items": [{"name": "Cam pin", "price": price, "currency": "USD",
                                                    "availability": availability, "gtin": "2000000903071",
                                                    "method": "jsonld"}]}
        return render, extract

    render, extract = page("InStock")
    result = await check_now(Watches(url), TENANT, url, render, extract, robots)
    assert result["status"] == "available" and result["price"] == "234.00" and result["currency"] == "USD"
    render, extract = page("OutOfStock")
    assert (await check_now(Watches(url), TENANT, url, render, extract, robots))["status"] == "unavailable"
    render, extract = page("SomethingElse")
    assert (await check_now(Watches(url), TENANT, url, render, extract, robots))["status"] == "unknown"
    render, extract = page("InStock")
    assert (await check_now(Watches("https://elsewhere.example/"), TENANT, url, render, extract, robots)) == {
        "status": "unknown", "reason": "not_watched", "url": url}

    async def disallow(_):
        return "User-agent: *\nDisallow: /product/"
    assert (await check_now(Watches(url), TENANT, url, render, extract, disallow))["reason"] == "robots"

    async def challenge(_):
        return {"ok": True, "challenge": True, "html": "<html>Just a moment</html>"}
    assert (await check_now(Watches(url), TENANT, url, challenge, extract, robots))["reason"] == "blocked"

    async def slow(_):
        await asyncio.sleep(1)
    assert (await check_now(Watches(url), TENANT, url, slow, extract, robots, budget=0.05))["reason"] == "timeout"


def test_tiger_check_now_uses_the_45_second_budget():
    sys.path.insert(0, str(ROOT / "agents" / "tiger"))
    import rig_watch
    from rigbox import locate_live
    assert rig_watch.CHECK_NOW_BUDGET_S == locate_live.TIGER_BUDGET_SECONDS == 45
    source = (ROOT / "agents" / "tiger" / "app.py").read_text()
    assert '"request_timeout_ms": 45000' in source and '@app.post("/rig/check-now")' in source


# 8. Real Postgres: default false, holds tenant scoped and single use.

@pytest.mark.asyncio
async def test_real_postgres_switch_default_and_hold_isolation():
    import asyncpg
    from uuid import uuid4
    from test_rig_postgres import _admin_url, _database_url, _create_database, _drop_database
    from rigbox.store import PgStore
    admin = _admin_url()
    name = "rig_locate_" + uuid4().hex[:12]
    await _create_database(admin, name)
    try:
        pool = await asyncpg.create_pool(_database_url(admin, name))
        try:
            store = PgStore(pool)
            await store.apply_schema()
            await store.apply_schema()
            await store.put_retailer(TENANT, "shop", None, None, "Shop")
            assert (await store.retailer(TENANT))["locate_enabled"] is False
            assert await enabled(store, TENANT) is False
            await store.put_retailer(TENANT, "shop", None, None, "Shop", locate_enabled=True)
            await store.put_retailer(TENANT, "shop", "a@example.com", None, "Shop")
            assert await enabled(store, TENANT) is True
            default = await pool.fetchval("""SELECT column_default FROM information_schema.columns
                WHERE table_schema='agent_rig' AND table_name='retailers' AND column_name='locate_enabled'""")
            assert default == "false"
            now = datetime.now(timezone.utc)
            holds = Holds(store)
            await holds.save(TENANT, "h_" + "a" * 32, "part-1", {"status": "available_to_order"},
                             {"offer": {"sku": "x"}}, now + timedelta(minutes=5))
            assert await holds.take(OTHER, "h_" + "a" * 32, now) is None
            assert (await holds.take(TENANT, "h_" + "a" * 32, now))["private"] == {"offer": {"sku": "x"}}
            assert await holds.take(TENANT, "h_" + "a" * 32, now) is None
            await holds.save(TENANT, "h_" + "b" * 32, "part-1", {}, {}, now - timedelta(seconds=1))
            assert await holds.take(TENANT, "h_" + "b" * 32, now) is None
            quote_id = "req_locate_state"
            await store.add_quote(TENANT, quote_id, {"kind": "product"}, {})
            await store.locate_state(OTHER, quote_id, {"state": "paid"})
            await store.locate_state(TENANT, quote_id, {"state": "ordered"})
            payload = json.loads(await pool.fetchval("SELECT payload FROM agent_rig.quotes WHERE id=$1", quote_id))
            assert payload["locate_state"] == {"state": "ordered"}
        finally:
            await pool.close()
    finally:
        await _drop_database(admin, name)


# Tiger page observations feed the same offer table, definite answers only.

@pytest.mark.asyncio
async def test_page_observation_records_only_definite_priced_reads():
    store = await rate_stub(await shop())
    store.supply_state[TENANT]["offers"] = []
    supply = SupplyStore(store)
    page = dict(url="https://other.example/product/cam-pin/", sku="RSR|POF90307", brand="POF-USA", mpn=None, region="us")
    for status in ("unknown", "blocked"):
        result = LiveResult(status, None, None, NOW, None, "double")
        assert (await supply.observe_page(TENANT, "other-store", live=result, **page))["imported"] == 0
    assert store.supply_state[TENANT]["offers"] == []
    product = (await store.products(TENANT))[0]
    assert (await catalogue_status(store, product)) == "unavailable"
    await supply.observe_page(TENANT, "other-store", live=available("234.00", code="2000000903071"), **page)
    [offer] = store.supply_state[TENANT]["offers"]
    assert offer["quantity"] == 1 and offer["status"] == "available" and offer["region"] == "US"
    assert offer["eta"] is None and offer["cost"] == Decimal("234.00")
    assert (await catalogue_status(store, product)) == "available_to_order"
    await supply.observe_page(TENANT, "other-store", live=LiveResult(
        "unavailable", Decimal("234.00"), "USD", datetime.now(timezone.utc), "2000000903071", "double"), **page)
    assert (await catalogue_status(store, product)) == "unavailable"
    with pytest.raises(ValueError):
        await supply.observe_page(TENANT, "other-store", live=available(), **{**page, "url": "http://other.example/x"})
    await supply.source(TENANT, dict(id="feed", approved=True, max_offer_age_seconds=60, provider="api"))
    with pytest.raises(ValueError):
        await supply.observe_page(TENANT, "feed", live=available(), **page)
    with pytest.raises(ValueError):
        await supply.observe_page(OTHER, "other-store", live=available(), **page)


async def catalogue_status(store, product):
    from rigbox.locate import locate
    return (await locate(store, TENANT, product))["public"]["status"]
