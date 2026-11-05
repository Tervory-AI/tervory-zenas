"""Optional auto-order: send the confirmed PO to the approved source.

Per-store ``auto_order`` switch, off by default. Sandbox only: the supplier
"email" is a file in a local outbox (RIG_LOCATE_OUTBOX_DIR); nothing is sent.
The PO is Rig's own row (agent_rig.supplier_purchase_orders), addressed to the
store-entered vendor name and order inbox.
Idempotent. Supplier safety rules apply after the store confirms receiving payment.
"""
import email
import json
import smtplib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents" / "rig"))
sys.path.insert(0, str(ROOT / "tests"))
from rigbox import supplier_orders
from rigbox.locate import SupplyStore
from rigbox.quotes.privacy import shopper_body
from rigbox.fulfilment import NEUTRAL_SLIP, RigOrders, numbers
from rigbox.quotes.senders import DeliveryError
from rigbox.quotes.service import QuoteService
from rigbox.quotes.store import MemoryQuotes
from rigbox.retailer import Retailer
from rigbox.service import RigError, Service
from test_rig_locate_checkout import TENANT, Live, available, rate_stub, shop
from test_rig_optional_blind_ship import ADDRESS, LOCATED, PRICE, HandoffDouble

SOURCE = dict(id="other-store", approved=True, priority=1, max_offer_age_seconds=3600, provider="tiger",
              site="https://other.example", neutral_slip_required=True, vendor_name="Other Store Wholesale LLC",
              order_email="orders@other.example")
READY = {**LOCATED, "source": {**LOCATED["source"], "vendor_name": SOURCE["vendor_name"],
                               "order_email": SOURCE["order_email"]}}
SHIP = {"address": {**ADDRESS, "country": "ZA"}}
# A Rig supplier PO row, as rigbox.fulfilment writes it.
PURCHASE = {"quote_id": "q1", "number": "PO-00040", "state": "draft", "source_id": "other-store",
            "vendor_name": SOURCE["vendor_name"], "order_email": SOURCE["order_email"], "item_sku": "SRC|PART001",
            "item_url": "https://other.example/product/cam-pin/", "quantity": 1, "unit_cost": "45.00",
            "currency": "USD", "ship_to": SHIP["address"], "neutral_slip": True, "instructions": NEUTRAL_SLIP,
            "sent": None}
SANDBOX = {"RIG_LOCATE_ORDER_MODE": "sandbox"}


@pytest.fixture(autouse=True)
def no_network_mail(monkeypatch):
    """Auto-order must never open SMTP. Any attempt fails the test."""
    def refuse(*args, **kwargs):
        raise AssertionError("SMTP must never be used by auto-order")
    monkeypatch.setattr(smtplib, "SMTP", refuse)
    monkeypatch.setattr(smtplib, "SMTP_SSL", refuse)


def env(monkeypatch, outbox):
    monkeypatch.setenv("RIG_LOCATE_HOLD_SECRET", "s" * 40)
    monkeypatch.setenv("RIG_LOCATE_ORDER_MODE", "sandbox")
    monkeypatch.setenv("RIG_LOCATE_OUTBOX_DIR", str(outbox))


def broken_outbox(monkeypatch):
    def broken(*args, **kwargs):
        raise DeliveryError("outbox_unavailable")
    monkeypatch.setattr(supplier_orders, "write", broken)


async def auto_shop(monkeypatch, outbox, *, auto=True, blind=True, source=SOURCE, fail_supplier=False):
    env(monkeypatch, outbox)
    store = await rate_stub(await shop())
    await store.put_retailer(TENANT, "shop", "shop@example.com", None, "Shop", blind_ship=blind, auto_order=auto)
    await SupplyStore(store).source(TENANT, dict(source))
    if fail_supplier:
        broken_outbox(monkeypatch)
    orders = HandoffDouble()
    service = QuoteService(store, None, orders, MemoryQuotes(store))
    service.locate_checker = Live(available("45.00"))
    held = await service.locate_confirm(TENANT, "part-1")
    body = {"product_id": "part-1", "hold": held["hold"],
            "contact": {"name": "Thandi Mokoena", "email": "thandi@example.com"}, "delivery": dict(ADDRESS)}
    return store, service, orders, held, body


async def accept_and_confirm(service, body):
    accepted = await service.locate_accept(TENANT, body)
    assert accepted["state"] == "accepted" and service.store.locate_orders == {}
    return await service.confirm_store_payment(TENANT, accepted["request_id"])


# 1. Switch and source config.

@pytest.mark.asyncio
async def test_auto_order_is_off_by_default_and_needs_a_boolean():
    store = await rate_stub(await shop())
    assert (await store.retailer(TENANT))["auto_order"] is False
    retailer = Retailer(Service(store))
    base = {"name": "Shop", "email": "shop@example.com"}
    for bad in ("true", 1, None, "live"):
        with pytest.raises(ValueError):
            await retailer.set_settings(TENANT, {**base, "auto_order": bad})
    await retailer.set_settings(TENANT, {**base, "auto_order": True})
    assert (await retailer.panel(TENANT))["settings"]["auto_order"] is True
    await retailer.set_settings(TENANT, base)
    assert (await retailer.panel(TENANT))["settings"]["auto_order"] is True


@pytest.mark.asyncio
async def test_source_order_email_is_validated_and_supplier_ready_needs_both_fields():
    store = await rate_stub(await shop())
    supply = SupplyStore(store)
    for bad in ("orders", "a@b", "x@y.com, evil@z.com", "a@b.com\r\nBcc: z@z.com"):
        with pytest.raises(ValueError):
            await supply.source(TENANT, {**SOURCE, "order_email": bad})
    await supply.source(TENANT, SOURCE)
    assert supplier_orders.supplier_ready((await supply.snapshot(TENANT))[0][0])
    assert not supplier_orders.supplier_ready({**SOURCE, "vendor_name": ""})
    assert not supplier_orders.supplier_ready({**SOURCE, "order_email": ""})


# 2. Outbox: sandbox only, configured folder only, exactly once.

def test_outbox_refuses_outside_sandbox_or_without_folder(tmp_path):
    for env_values in ({}, {"RIG_LOCATE_ORDER_MODE": "live", "RIG_LOCATE_OUTBOX_DIR": str(tmp_path)},
                       {"RIG_LOCATE_ORDER_MODE": "sandbox"},
                       {"RIG_LOCATE_ORDER_MODE": "sandbox", "RIG_LOCATE_OUTBOX_DIR": str(tmp_path / "missing")}):
        with pytest.raises(DeliveryError):
            supplier_orders.outbox(env_values)
    assert supplier_orders.outbox({"RIG_LOCATE_ORDER_MODE": "sandbox", "RIG_LOCATE_OUTBOX_DIR": str(tmp_path)}) == tmp_path


def test_there_is_no_live_transport():
    text = (ROOT / "agents" / "rig" / "rigbox" / "supplier_orders.py").read_text()
    code = text.split('"""', 2)[2]  # code after the module docstring
    for word in ("smtplib", "SmtpMailer", "import socket", "httpx", "requests", "urllib", "send_message", "MODE_LIVE",
                 '== "live"', "== 'live'"):
        assert word not in code, word


def test_supplier_email_carries_po_address_and_blind_instruction_but_no_shop_pricing(tmp_path):
    message = supplier_orders.compose(tenant_id=TENANT, quote_id="q1", store_name="Harbor Gear",
                                      store_email="orders@customnation.example", purchase=PURCHASE)
    body = message.get_content()
    assert message["To"] == "orders@other.example" and message["Reply-To"] == "orders@customnation.example"
    assert message["Subject"] == "Purchase order PO-00040 from Harbor Gear"
    assert "Other Store Wholesale LLC" in body and "SRC|PART001" in body and "45.00 USD" in body
    assert "12 Long Street" in body and "Cape Town" in body and "SHIP BLIND" in body
    # The store's own shopper price, landed cost and margin never go to the supplier.
    for private in ("1125", "landed", "margin", "duty", "thandi@example.com"):
        assert private not in body
    unflagged = supplier_orders.compose(tenant_id=TENANT, quote_id="q1", store_name="Harbor Gear", store_email=None,
        purchase={**PURCHASE, "neutral_slip": False, "instructions": ""})
    assert "SHIP BLIND" not in unflagged.get_content() and unflagged["Reply-To"] is None
    to_store = supplier_orders.compose(tenant_id=TENANT, quote_id="q1", store_name="Shop", store_email=None,
        purchase={**PURCHASE, "ship_to": None})
    assert "Ship to Shop at the address on your account." in to_store.get_content()
    assert "Long Street" not in to_store.get_content() and "SHIP BLIND" not in to_store.get_content()
    with pytest.raises(DeliveryError, match="supplier_not_configured"):
        supplier_orders.compose(tenant_id=TENANT, quote_id="q1", store_name="x", store_email=None,
                                purchase={**PURCHASE, "order_email": ""})


def test_outbox_write_is_idempotent(tmp_path):
    values = {"RIG_LOCATE_ORDER_MODE": "sandbox", "RIG_LOCATE_OUTBOX_DIR": str(tmp_path)}
    message = supplier_orders.compose(tenant_id=TENANT, quote_id="q1", store_name="Shop", store_email=None,
                                      purchase=PURCHASE)
    first = supplier_orders.write(message, tenant_id=TENANT, quote_id="q1", env=values)
    second = supplier_orders.write(message, tenant_id=TENANT, quote_id="q1", env=values)
    assert first["duplicate"] is False and second["duplicate"] is True and first["file"] == second["file"]
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and oct(files[0].stat().st_mode & 0o777) == "0o600"
    parsed = email.message_from_bytes(files[0].read_bytes())
    assert parsed["To"] == "orders@other.example" and parsed["X-Rig-Reference"] == f"rig:{TENANT}:q1"


# 3. Accept -> pay -> order -> supplier email.

@pytest.mark.asyncio
async def test_accept_auto_orders_once_through_the_sandbox_outbox(monkeypatch, tmp_path):
    store, service, orders, held, body = await auto_shop(monkeypatch, tmp_path)
    result = await accept_and_confirm(service, body)
    assert result["state"] == "supplier_ordered"
    state = store.quotes[-1]["payload"]["locate_state"]
    assert state["state"] == "supplier_ordered" and state["purchase_order_state"] == "sent"
    assert len(list(tmp_path.iterdir())) == 1
    saved = await store.locate_order(TENANT, result["request_id"])
    po_number = numbers(TENANT, result["request_id"])[1]
    assert saved["order"]["state"] == "placed" and saved["purchase_order"]["number"] == po_number
    assert saved["purchase_order"]["state"] == "sent" and saved["purchase_order"]["sent"]["file"] == state[
        "supplier_order"]["file"]
    parsed = email.message_from_bytes(next(tmp_path.iterdir()).read_bytes())
    assert parsed["To"] == "orders@other.example" and po_number in parsed["Subject"]
    assert "Cape Town" in parsed.get_payload() and "SHIP BLIND" in parsed.get_payload()
    text = json.dumps(shopper_body(result))
    for marker in ("other-store", "other.example", "Other Store Wholesale", "orders@", "outbox", po_number):
        assert marker not in text
    with pytest.raises(RigError) as raised:  # the hold is single use: no second payment or order
        await accept_and_confirm(service, body)
    assert raised.value.status == 410
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.asyncio
async def test_supplier_order_failure_needs_store_review(monkeypatch, tmp_path):
    store, service, orders, _, body = await auto_shop(monkeypatch, tmp_path, fail_supplier=True)
    result = await accept_and_confirm(service, body)
    assert result["state"] == "supplier_order_failed"
    state = store.quotes[-1]["payload"]["locate_state"]
    assert state["needs_review"] is True and list(tmp_path.iterdir()) == []
    saved = await store.locate_order(TENANT, store.quotes[-1]["id"])
    assert saved["order"]["state"] == "cancelled" and saved["purchase_order"]["state"] == "cancelled"
    assert saved["order"]["cancel_reason"] == "supplier_order_failed:outbox_unavailable"


@pytest.mark.asyncio
async def test_source_without_vendor_or_inbox_refuses_before_payment(monkeypatch, tmp_path):
    store, service, orders, _, body = await auto_shop(monkeypatch, tmp_path,
                                                      source={**SOURCE, "vendor_name": "", "order_email": ""})
    accepted = await service.locate_accept(TENANT, body)
    with pytest.raises(RigError) as raised:
        await service.confirm_store_payment(TENANT, accepted["request_id"])
    assert raised.value.status == 503 and store.locate_orders == {}
    assert len(orders.sent) == 1


@pytest.mark.asyncio
async def test_missing_outbox_refuses_before_hold_or_payment(monkeypatch, tmp_path):
    store, service, orders, held, body = await auto_shop(monkeypatch, tmp_path)
    monkeypatch.setenv("RIG_LOCATE_OUTBOX_DIR", str(tmp_path / "missing"))
    accepted = await service.locate_accept(TENANT, body)
    with pytest.raises(RigError) as raised:
        await service.confirm_store_payment(TENANT, accepted["request_id"])
    assert raised.value.status == 503 and store.locate_orders == {}
    assert store.quotes[-1]["payload"]["locate_state"]["state"] == "accepted"


@pytest.mark.asyncio
async def test_switch_off_keeps_the_draft_po_and_writes_nothing(monkeypatch, tmp_path):
    store, service, orders, _, body = await auto_shop(monkeypatch, tmp_path, auto=False)
    assert (await accept_and_confirm(service, body))["state"] == "ordered"
    assert store.quotes[-1]["payload"]["locate_state"]["state"] == "ordered"
    assert list(tmp_path.iterdir()) == []
    [row] = store.locate_orders.values()
    assert row["purchase_order"]["state"] == "draft" and row["purchase_order"]["sent"] is None


# 4. RigOrders.send_supplier_order: Rig's PO, sent once, sandbox only.

async def placed(store, quote_id="q1"):
    await RigOrders(store).place_located_order(tenant_id=TENANT, quote_id=quote_id, located=READY, price=PRICE,
                                               ship=SHIP, env=SANDBOX)


@pytest.mark.asyncio
async def test_rig_supplier_order_sends_once_and_marks_the_po_sent(tmp_path):
    from rigbox.store import MemoryStore
    store = MemoryStore()
    await placed(store)
    values = {**SANDBOX, "RIG_LOCATE_OUTBOX_DIR": str(tmp_path)}
    first = await RigOrders(store).send_supplier_order(tenant_id=TENANT, quote_id="q1", store_name="Shop", env=values)
    second = await RigOrders(store).send_supplier_order(tenant_id=TENANT, quote_id="q1", store_name="Shop", env=values)
    assert first["purchase_order"] == {"number": numbers(TENANT, "q1")[1], "state": "sent", "recorded": True}
    assert first["supplier_order"]["duplicate"] is False and second["supplier_order"]["duplicate"] is True
    assert second["supplier_order"]["file"] == first["supplier_order"]["file"] and len(list(tmp_path.iterdir())) == 1


@pytest.mark.asyncio
async def test_rig_supplier_order_refuses_outside_sandbox_missing_or_cancelled_orders(tmp_path):
    from rigbox.store import MemoryStore
    store = MemoryStore()
    orders = RigOrders(store)
    for values in ({"RIG_LOCATE_ORDER_MODE": "live", "RIG_LOCATE_OUTBOX_DIR": str(tmp_path)}, SANDBOX):
        with pytest.raises(DeliveryError):
            await orders.send_supplier_order(tenant_id=TENANT, quote_id="q1", env=values)
    values = {**SANDBOX, "RIG_LOCATE_OUTBOX_DIR": str(tmp_path)}
    with pytest.raises(DeliveryError, match="order_missing"):
        await orders.send_supplier_order(tenant_id=TENANT, quote_id="q1", env=values)
    await placed(store)
    await orders.cancel(TENANT, "q1", "test", env=values)
    with pytest.raises(DeliveryError, match="order_state"):
        await orders.send_supplier_order(tenant_id=TENANT, quote_id="q1", env=values)
    assert list(tmp_path.iterdir()) == []


class LosesTheSentUpdate:
    """Store wrapper: the outbox write succeeds, recording it fails once."""

    def __init__(self, store):
        self.store, self.failed = store, False

    def __getattr__(self, name):
        return getattr(self.store, name)

    async def supplier_po_sent(self, *args):
        if not self.failed:
            self.failed = True
            raise RuntimeError("database went away")
        return await self.store.supplier_po_sent(*args)


@pytest.mark.asyncio
async def test_cancel_refuses_a_sent_po_and_reconcile_fixes_recorded_false(tmp_path):
    from rigbox.store import MemoryStore
    store = MemoryStore()
    values = {**SANDBOX, "RIG_LOCATE_OUTBOX_DIR": str(tmp_path)}
    await placed(store, "q1")
    await RigOrders(store).send_supplier_order(tenant_id=TENANT, quote_id="q1", store_name="Shop", env=values)
    assert await store.cancel_locate_order(TENANT, "q1", "late") == "sent"
    assert await RigOrders(store).cancel(TENANT, "q1", "late", env=values) == "sent"
    assert store.locate_orders[(TENANT, "q1")]["order"]["state"] == "placed"
    # recorded:false, then cancel: the outbox file exists, so cancel reconciles and refuses.
    flaky = LosesTheSentUpdate(store)
    await placed(store, "q2")
    lost = await RigOrders(flaky).send_supplier_order(tenant_id=TENANT, quote_id="q2", store_name="Shop", env=values)
    assert lost["purchase_order"]["recorded"] is False and lost["purchase_order"]["state"] == "draft"
    assert await RigOrders(store).cancel(TENANT, "q2", "x", env=values) == "sent"
    assert store.locate_orders[(TENANT, "q2")]["purchase_order"]["state"] == "sent"
    # recorded:false, then reconcile.
    flaky = LosesTheSentUpdate(store)
    await placed(store, "q3")
    await placed(store, "q4")  # never sent: reconcile leaves it draft
    await RigOrders(flaky).send_supplier_order(tenant_id=TENANT, quote_id="q3", store_name="Shop", env=values)
    result = await RigOrders(store).reconcile(tenant_id=TENANT, env=values)
    assert result == {"checked": 2, "reconciled": [{"quote_id": "q3", "purchase_order": numbers(TENANT, "q3")[1]}],
                      "stranded": [], "unknown": []}
    assert store.locate_orders[(TENANT, "q3")]["purchase_order"]["sent"]["file"] == supplier_orders.file_name(TENANT, "q3")
    assert store.locate_orders[(TENANT, "q4")]["purchase_order"]["state"] == "draft"
    assert len(list(tmp_path.iterdir())) == 3  # reconcile never writes the outbox
    with pytest.raises(DeliveryError):
        await RigOrders(store).reconcile(tenant_id=TENANT, env={"RIG_LOCATE_OUTBOX_DIR": str(tmp_path)})
    assert await RigOrders(store).cancel(TENANT, "q4", "x", env=values) == "cancelled"
    assert await RigOrders(store).cancel(TENANT, "nope", "x", env=values) == "missing"


@pytest.mark.asyncio
async def test_supplier_failure_after_the_outbox_write_keeps_the_order_and_does_not_refund(monkeypatch, tmp_path):
    store, service, orders, _, body = await auto_shop(monkeypatch, tmp_path)
    real = RigOrders.send_supplier_order

    async def written_then_lost(self, **kwargs):
        await real(self, **kwargs)
        store.locate_orders[(TENANT, kwargs["quote_id"])]["purchase_order"].update(state="draft", sent=None)
        raise DeliveryError("order_unavailable")
    monkeypatch.setattr(RigOrders, "send_supplier_order", written_then_lost)
    accepted = await accept_and_confirm(service, body)
    assert accepted["state"] in {"supplier_ordered", "supplier_order_unconfirmed"}
    state = store.quotes[-1]["payload"]["locate_state"]
    assert state["state"] == "supplier_ordered" and state["purchase_order_state"] == "sent"
    [row] = store.locate_orders.values()
    assert row["order"]["state"] == "placed" and row["purchase_order"]["state"] == "sent"
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.asyncio
async def test_written_po_and_reconcile_database_outage_never_refunds(monkeypatch, tmp_path):
    store, service, _, _, body = await auto_shop(monkeypatch, tmp_path)
    real_send = RigOrders.send_supplier_order

    async def lookup_failed(*args):
        raise RuntimeError("database unavailable during send reconciliation")

    async def written_then_database_lost(self, **kwargs):
        await real_send(self, **kwargs)
        # The supplier's outbox record exists, but its database receipt was lost.
        store.locate_orders[(TENANT, kwargs["quote_id"])]["purchase_order"].update(state="draft", sent=None)
        monkeypatch.setattr(store, "locate_order", lookup_failed)
        raise DeliveryError("order_unavailable")

    monkeypatch.setattr(RigOrders, "send_supplier_order", written_then_database_lost)
    accepted = await accept_and_confirm(service, body)
    assert accepted["state"] in {"supplier_ordered", "supplier_order_unconfirmed"}
    state = store.quotes[-1]["payload"]["locate_state"]
    assert state["state"] == "supplier_order_unconfirmed" and "refund" not in state
    [row] = store.locate_orders.values()
    assert row["order"]["state"] == "placed" and row["purchase_order"]["state"] == "draft"
    assert len(list(tmp_path.glob("*.eml"))) == 1


@pytest.mark.asyncio
async def test_known_unsent_po_cancel_record_failure_still_reports_unrecorded(monkeypatch, tmp_path):
    from rigbox.store import MemoryStore
    store = MemoryStore()
    await placed(store)
    values = {**SANDBOX, "RIG_LOCATE_OUTBOX_DIR": str(tmp_path)}

    async def cancel_failed(*args):
        raise RuntimeError("database unavailable while recording cancellation")

    monkeypatch.setattr(store, "cancel_locate_order", cancel_failed)
    assert await RigOrders(store).cancel(TENANT, "q1", "test", env=values) == "unrecorded"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_outbox_unavailable_on_cancel_never_refunds(monkeypatch, tmp_path):
    """The send fails and the outbox can't be read: Rig can't tell whether the
    supplier has the PO, so it neither cancels nor refunds."""
    store, service, orders, _, body = await auto_shop(monkeypatch, tmp_path)

    async def send_fails(self, **kwargs):
        raise DeliveryError("order_unavailable")

    def unreadable(**kwargs):
        raise DeliveryError("outbox_unavailable")
    monkeypatch.setattr(RigOrders, "send_supplier_order", send_fails)
    monkeypatch.setattr(supplier_orders, "sent_record", unreadable)
    accepted = await accept_and_confirm(service, body)
    assert accepted["state"] in {"supplier_ordered", "supplier_order_unconfirmed"}
    state = store.quotes[-1]["payload"]["locate_state"]
    assert state["state"] == "supplier_order_unconfirmed"
    assert "refund" not in state
    [row] = store.locate_orders.values()
    assert row["order"]["state"] == "placed" and row["purchase_order"]["state"] == "draft"
    quote_id = store.quotes[-1]["id"]
    assert await RigOrders(store).cancel(TENANT, quote_id, "x") == "unknown"
    assert store.locate_orders[(TENANT, quote_id)]["order"]["state"] == "placed"


@pytest.mark.asyncio
async def test_reconcile_lists_an_old_unsent_draft_po_as_stranded(tmp_path):
    """Rig stopped between recording the order and sending the PO: the draft PO
    has no outbox file. Once it is older than the cutoff, reconcile lists it as
    stranded (the payment reconciler cancels and refunds it) and changes nothing."""
    from datetime import datetime, timedelta, timezone
    from rigbox.store import MemoryStore
    store = MemoryStore()
    values = {**SANDBOX, "RIG_LOCATE_OUTBOX_DIR": str(tmp_path)}
    await placed(store, "q1")
    assert (await RigOrders(store).reconcile(tenant_id=TENANT, env=values))["stranded"] == []
    later = datetime.now(timezone.utc) + timedelta(minutes=30)
    result = await RigOrders(store).reconcile(tenant_id=TENANT, env=values, now=later)
    assert result == {"checked": 1, "reconciled": [], "stranded": ["q1"], "unknown": []}
    assert store.locate_orders[(TENANT, "q1")]["purchase_order"]["state"] == "draft"
    assert store.locate_orders[(TENANT, "q1")]["order"]["state"] == "placed" and list(tmp_path.iterdir()) == []

@pytest.mark.asyncio
async def test_an_unreadable_outbox_on_an_old_po_is_always_unknown_never_stranded(tmp_path, caplog):
    """A directory sits at the PO's outbox path, so reading it fails. Every
    reconcile lists the quote in "unknown" (and logs its id), never in
    "stranded", and nothing is cancelled."""
    from datetime import datetime, timedelta, timezone
    from rigbox.store import MemoryStore
    store = MemoryStore()
    values = {**SANDBOX, "RIG_LOCATE_OUTBOX_DIR": str(tmp_path)}
    await placed(store, "q1")
    (tmp_path / supplier_orders.file_name(TENANT, "q1")).mkdir()
    for days in (1, 30, 31):
        later = datetime.now(timezone.utc) + timedelta(days=days)
        with caplog.at_level("WARNING"):
            result = await RigOrders(store).reconcile(tenant_id=TENANT, env=values, now=later)
        assert result == {"checked": 1, "reconciled": [], "stranded": [], "unknown": ["q1"]}
    assert "q1" in caplog.text
    assert await RigOrders(store).cancel(TENANT, "q1", "x", env=values) == "unknown"
    row = store.locate_orders[(TENANT, "q1")]
    assert row["order"]["state"] == "placed" and row["purchase_order"]["state"] == "draft"


@pytest.mark.asyncio
async def test_cancel_is_strict_when_the_outbox_cannot_be_checked(tmp_path):
    """outbox_not_configured or order_mode at cancel time gives "unknown": the
    mode or folder may have changed after a PO was written. Only unsent=True
    (the send itself refused before writing) cancels without the check."""
    from rigbox.store import MemoryStore
    store = MemoryStore()
    values = {**SANDBOX, "RIG_LOCATE_OUTBOX_DIR": str(tmp_path)}
    await placed(store, "q1")
    await RigOrders(store).send_supplier_order(tenant_id=TENANT, quote_id="q1", store_name="Shop", env=values)
    store.locate_orders[(TENANT, "q1")]["purchase_order"].update(state="draft", sent=None)  # recorded:false
    assert len(list(tmp_path.iterdir())) == 1
    for env in ({}, {"RIG_LOCATE_OUTBOX_DIR": str(tmp_path)},
                {**SANDBOX, "RIG_LOCATE_OUTBOX_DIR": str(tmp_path / "moved")}):
        assert await RigOrders(store).cancel(TENANT, "q1", "x", env=env) == "unknown"
    assert store.locate_orders[(TENANT, "q1")]["order"]["state"] == "placed"
    await placed(store, "q2")
    assert await RigOrders(store).cancel(TENANT, "q2", "x", env={}, unsent=True) == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["outbox_not_configured", "order_mode"])
async def test_accept_refunds_when_the_send_itself_refused_before_writing(monkeypatch, tmp_path, code):
    store, service, orders, _, body = await auto_shop(monkeypatch, tmp_path)

    async def refused(self, **kwargs):
        raise DeliveryError(code)
    monkeypatch.setattr(RigOrders, "send_supplier_order", refused)
    result = await accept_and_confirm(service, body)
    assert result["state"] == "supplier_order_failed"
    assert store.quotes[-1]["payload"]["locate_state"]["needs_review"] is True
    [row] = store.locate_orders.values()
    assert row["order"]["state"] == "cancelled"


# 5. Real Postgres: defaults and persistence; PO sent once, cancel only an unsent PO.

@pytest.mark.asyncio
async def test_real_postgres_auto_order_and_vendor_fields(tmp_path):
    import asyncpg
    from uuid import uuid4
    from test_rig_postgres import _admin_url, _create_database, _database_url, _drop_database
    from rigbox.store import PgStore
    admin = _admin_url()
    name = "rig_auto_" + uuid4().hex[:12]
    await _create_database(admin, name)
    try:
        pool = await asyncpg.create_pool(_database_url(admin, name))
        try:
            store = PgStore(pool)
            await store.apply_schema()
            await store.apply_schema()
            await store.put_retailer(TENANT, "shop", None, None, "Shop")
            assert (await store.retailer(TENANT))["auto_order"] is False
            await store.put_retailer(TENANT, "shop", None, None, "Shop", auto_order=True)
            await store.put_retailer(TENANT, "shop", "a@example.com", None, "Shop", blind_ship=True)
            saved = await store.retailer(TENANT)
            assert saved["auto_order"] is True and saved["blind_ship"] is True
            supply_store = SupplyStore(store)
            await supply_store.source(TENANT, SOURCE)
            row = (await supply_store.snapshot(TENANT))[0][0]
            assert (row["vendor_name"], row["order_email"]) == ("Other Store Wholesale LLC", "orders@other.example")
            orders, values = RigOrders(store), {**SANDBOX, "RIG_LOCATE_OUTBOX_DIR": str(tmp_path)}
            await placed(store, "req_1")
            sent = [await orders.send_supplier_order(tenant_id=TENANT, quote_id="req_1", store_name="Shop", env=values)
                    for _ in range(2)]
            assert [item["supplier_order"]["duplicate"] for item in sent] == [False, True]
            row = await store.locate_order(TENANT, "req_1")
            assert row["purchase_order"]["state"] == "sent" and row["purchase_order"]["sent"]["transport"] == "sandbox_outbox"
            assert await pool.fetchval("SELECT sent_at IS NOT NULL FROM agent_rig.supplier_purchase_orders") is True
            assert await orders.cancel(TENANT, "req_1", "late", env=values) == "sent"  # a sent PO refuses cancel
            row = await store.locate_order(TENANT, "req_1")
            assert (row["order"]["state"], row["purchase_order"]["state"]) == ("placed", "sent")
            assert await store.cancel_locate_order(TENANT, "req_1", "late") == "sent"
            await placed(store, "req_2")
            assert await orders.cancel(TENANT, "req_2", "supplier_order_failed:outbox_unavailable",
                                        env=values) == "cancelled"
            row = await store.locate_order(TENANT, "req_2")
            assert (row["order"]["state"], row["purchase_order"]["state"]) == ("cancelled", "cancelled")
            assert len(list(tmp_path.iterdir())) == 1
            print("RIG_PO_PG", json.dumps(row, sort_keys=True))
            # recorded:false: the outbox file exists but the PO is still draft.
            await placed(store, "req_3")
            purchase = (await store.locate_order(TENANT, "req_3"))["purchase_order"]
            supplier_orders.write(supplier_orders.compose(tenant_id=TENANT, quote_id="req_3", store_name="Shop",
                                                          store_email=None, purchase=purchase),
                                  tenant_id=TENANT, quote_id="req_3", env=values)
            assert await store.draft_supplier_orders(TENANT) == ["req_3"]
            from datetime import datetime, timedelta, timezone
            soon = datetime.now(timezone.utc) + timedelta(minutes=30)
            assert await store.draft_supplier_orders(TENANT, before=soon) == ["req_3"]
            assert await store.draft_supplier_orders(TENANT, before=soon - timedelta(hours=1)) == []
            fixed = await orders.reconcile(tenant_id=TENANT, env=values)
            assert fixed == {"checked": 1, "reconciled": [{"quote_id": "req_3", "purchase_order": purchase["number"]}],
                             "stranded": [], "unknown": []}
            row = await store.locate_order(TENANT, "req_3")
            assert row["purchase_order"]["state"] == "sent" and row["purchase_order"]["sent"]["file"].endswith(".eml")
            assert await orders.reconcile(tenant_id=TENANT, env=values) == {
                "checked": 0, "reconciled": [], "stranded": [], "unknown": []}
            assert await store.draft_supplier_orders(TENANT) == []
            print("RIG_RECONCILE_PG", json.dumps(fixed, sort_keys=True))
            # Holds.peek reads without consuming; take still consumes once.
            from datetime import datetime, timedelta, timezone
            from rigbox.locate_live import Holds
            holds, now = Holds(store), datetime.now(timezone.utc)
            await holds.save(TENANT, "h1", "part-1", {"status": "available_to_order"}, {"source": SOURCE},
                             now + timedelta(minutes=5))
            assert (await holds.peek(TENANT, "h1", now))["private"]["source"]["id"] == "other-store"
            assert (await holds.peek(TENANT, "h1", now)) is not None and (await holds.take(TENANT, "h1", now))
            assert await holds.peek(TENANT, "h1", now) is None and await holds.take(TENANT, "h1", now) is None
            default = await pool.fetchval("""SELECT column_default FROM information_schema.columns
                WHERE table_schema='agent_rig' AND table_name='retailers' AND column_name='auto_order'""")
            assert default == "false"
        finally:
            await pool.close()
    finally:
        await _drop_database(admin, name)
