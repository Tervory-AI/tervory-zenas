"""Rig's own Locate fulfilment: orders and supplier purchase orders in Rig's Postgres.

A store-confirmed accepted quote is recorded here. One accepted quote makes
exactly one row in agent_rig.locate_orders
(what the shopper bought, at the held price) and one draft row in
agent_rig.supplier_purchase_orders (what the store buys from the approved
source, at the source's live price). Both are keyed by (tenant, quote), so a
retry or a concurrent second Accept returns the same order and PO numbers and
never makes a second row.

Blind ship (per store, off by default): the PO carries the shopper's delivery
address, and when the source is flagged neutral_slip_required it carries the
neutral-slip instruction. The order and PO are private store records: nothing
here is returned to the shopper except through QuoteService.locate_accept's
own response, which names neither the source nor its price.

Sandbox only, like the rest of Locate ordering (RIG_LOCATE_ORDER_MODE=sandbox).
Nothing here opens a socket or sends mail.

The interface (Fulfilment) is what QuoteService calls. RigOrders is the only
implementation. There is no external ERP order path.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from rigbox.quotes.senders import DeliveryError

ORDER_MODE_ENV = "RIG_LOCATE_ORDER_MODE"


def sandbox_orders(env: dict[str, str] | None = None) -> None:
    """Locate order placement is sandbox-only. Anything else refuses."""
    if (os.environ if env is None else env).get(ORDER_MODE_ENV, "") != "sandbox":
        raise DeliveryError("order_mode")

# Printed on the supplier PO when the order ships blind and the approved source
# is flagged neutral_slip_required.
NEUTRAL_SLIP = ("SHIP BLIND: this parcel goes straight to our customer. No supplier branding, prices, "
                "invoice or branded packing slip in the parcel. Use a plain packing slip or none.")

_CURRENCY = re.compile(r"[A-Z]{3}")


class Fulfilment(Protocol):
    """Order placement after the store confirms receiving its own payment."""

    async def place_located_order(self, *, tenant_id: str, quote_id: str, located: dict[str, Any],
                                  price: dict[str, Any], ship: dict[str, Any] | None = None,
                                  quantity: int = 1) -> dict[str, Any]:
        """Record the order and its draft supplier PO. Idempotent per (tenant, quote)."""
        ...


def numbers(tenant_id: str, quote_id: str) -> tuple[str, str]:
    """Stable order and PO numbers for a quote: the same on every retry."""
    digest = hashlib.sha256(f"rig:{tenant_id}:{quote_id}".encode()).hexdigest()[:10].upper()
    return f"RIG-{digest}", f"PO-{digest}"


def _amount(value: Any) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise DeliveryError("order_rejected") from None
    if not amount.is_finite() or amount <= 0:
        raise DeliveryError("order_rejected")
    return str(amount.quantize(Decimal("0.01")))


def _currency(value: Any) -> str:
    currency = str(value or "").upper()
    if not _CURRENCY.fullmatch(currency):
        raise DeliveryError("order_rejected")
    return currency


def _public(order: dict[str, Any], purchase: dict[str, Any]) -> dict[str, Any]:
    """The caller's view: numbers and states only (kept in locate_state)."""
    return {"order": {"number": order["number"], "state": order["state"]},
            "purchase_order": {"number": purchase["number"], "state": purchase["state"],
                               "ship_direct": purchase["ship_to"] is not None,
                               "neutral_slip": purchase["neutral_slip"]}}


class RigOrders:
    """Fulfilment in Rig's own database (MemoryStore or PgStore)."""

    def __init__(self, store: Any) -> None:
        self.store = store

    async def place_located_order(self, *, tenant_id: str, quote_id: str, located: dict[str, Any],
                                  price: dict[str, Any], ship: dict[str, Any] | None = None,
                                  env: dict[str, str] | None = None, quantity: int = 1) -> dict[str, Any]:
        sandbox_orders(env)
        if type(quantity) is not int or not 1 <= quantity <= 100:
            raise DeliveryError("order_rejected")
        try:
            source, offer, live = located["source"], located["offer"], located["live"]
            product_id, source_id = str(located["product_sku"]), str(source["id"])
        except (KeyError, TypeError):
            raise DeliveryError("order_rejected") from None
        if not product_id or not source_id:
            raise DeliveryError("order_rejected")
        address = dict(ship["address"]) if ship is not None else None
        neutral = address is not None and source.get("neutral_slip_required") is True
        order_number, po_number = numbers(tenant_id, quote_id)
        order = {"quote_id": quote_id, "number": order_number, "state": "placed", "product_id": product_id,
                 "price": _amount(price.get("amount")), "currency": _currency(price.get("currency")),
                 "ship_to": address}
        purchase = {"quote_id": quote_id, "number": po_number, "state": "draft", "source_id": source_id,
                    "vendor_name": str(source.get("vendor_name") or "").strip(),
                    "order_email": str(source.get("order_email") or "").strip(),
                    "item_sku": str(offer.get("sku") or ""), "item_url": str(offer.get("url") or ""),
                    "quantity": quantity, "unit_cost": _amount(live.get("price")),
                    "currency": _currency(live.get("currency")), "ship_to": address,
                    "neutral_slip": neutral, "instructions": NEUTRAL_SLIP if neutral else "", "sent": None}
        try:
            saved = await self.store.place_locate_order(tenant_id, order, purchase)
        except DeliveryError:
            raise
        except Exception:
            raise DeliveryError("order_unavailable") from None
        if saved["purchase_order"]["source_id"] != source_id:
            raise DeliveryError("order_mismatch")
        return _public(saved["order"], saved["purchase_order"])

    async def send_supplier_order(self, *, tenant_id: str, quote_id: str, store_name: str = "",
                                  store_email: str | None = None,
                                  env: dict[str, str] | None = None) -> dict[str, Any]:
        """Auto-order, sandbox only: email Rig's draft PO to the source, then mark it sent.

        "Email" is the local sandbox outbox (rigbox.supplier_orders); nothing is
        sent over a network. Idempotent: a sent PO is never written again, and
        the outbox file is created once (O_EXCL). On DeliveryError the caller
        uses the strict cancel boundary below; unknown sends stay for review.
        """
        from rigbox import supplier_orders

        sandbox_orders(env)
        supplier_orders.outbox(env)
        try:
            saved = await self.store.locate_order(tenant_id, quote_id)
        except Exception:
            raise DeliveryError("order_unavailable") from None
        if saved is None:
            raise DeliveryError("order_missing")
        order, purchase = saved["order"], saved["purchase_order"]
        if purchase["state"] == "sent" and purchase.get("sent"):
            return {"purchase_order": {"number": purchase["number"], "state": "sent"},
                    "supplier_order": {**purchase["sent"], "duplicate": True}}
        if purchase["state"] != "draft" or order["state"] != "placed":
            raise DeliveryError("order_state")
        message = supplier_orders.compose(tenant_id=tenant_id, quote_id=quote_id, store_name=store_name,
                                          store_email=store_email, purchase=purchase)
        sent = supplier_orders.write(message, tenant_id=tenant_id, quote_id=quote_id, env=env)
        record = {key: sent.get(key) for key in ("channel", "transport", "file", "message_id")}
        try:
            updated = await self.store.supplier_po_sent(tenant_id, quote_id, record)
        except Exception:
            updated = None
        # The outbox file is the send. If recording it failed, report the send
        # (a retry reads the same file as a duplicate) rather than refund an
        # order the supplier already has.
        return {"purchase_order": {"number": purchase["number"], "state": "sent" if updated else "draft",
                                   "recorded": updated is not None},
                "supplier_order": sent}

    async def cancel(self, tenant_id: str, quote_id: str, reason: str,
                     env: dict[str, str] | None = None, *, unsent: bool = False) -> str:
        """Cancel the order and its unsent PO. Never raises.

        Returns "cancelled", "missing", "sent", "unknown" or "unrecorded".
        "sent" means the supplier already has the PO (it is marked sent, or its
        outbox file exists and is reconciled now): the order is not cancelled,
        and the caller must not cancel it. "unknown" means Rig could not tell
        whether the PO was sent (for example the outbox file exists but can't be
        read): nothing is cancelled; a person or
        a later reconcile settles it. "unrecorded" means the store failed after
        the outbox was checked; the order may still be placed, so the caller
        must keep the order for review.

        Strict: when the outbox can't be checked for any reason (including
        outbox_not_configured or order_mode, since the mode or folder may have
        changed after a PO was written) the answer is "unknown". unsent=True
        skips the outbox check; pass it only when send_supplier_order itself
        refused with outbox_not_configured or order_mode, which it checks
        before writing anything. The store still refuses a PO marked sent.
        """
        log = logging.getLogger(__name__)
        try:
            if not unsent and await self._reconcile_one(tenant_id, quote_id, env) is not None:
                return "sent"
        except DeliveryError as exc:
            log.warning("locate order send state unknown before cancel: %s (%s)", quote_id, exc)
            return "unknown"
        except Exception:  # noqa: BLE001
            log.warning("locate order reconcile before cancel failed")
            # Reconciliation may fail after finding the supplier's outbox PO.
            # Until its send state is known, do not cancel it.
            return "unknown"
        try:
            return await self.store.cancel_locate_order(tenant_id, quote_id, reason[:120])
        except Exception:  # noqa: BLE001 — the store sees the open order
            log.warning("locate order cancel not recorded")
            return "unrecorded"

    async def _reconcile_one(self, tenant_id: str, quote_id: str,
                             env: dict[str, str] | None) -> dict[str, Any] | None:
        """Mark a draft PO sent when its outbox file exists. Returns the sent PO, else None."""
        from rigbox import supplier_orders

        sandbox_orders(env)
        record = supplier_orders.sent_record(tenant_id=tenant_id, quote_id=quote_id, env=env)
        if record is None:
            return None
        saved = await self.store.locate_order(tenant_id, quote_id)
        if saved is None:
            return None
        if saved["purchase_order"]["state"] == "sent":
            return saved["purchase_order"]
        if saved["purchase_order"]["state"] != "draft":
            return None
        return await self.store.supplier_po_sent(tenant_id, quote_id, record)

    async def reconcile(self, *, tenant_id: str, env: dict[str, str] | None = None,
                        limit: int = 200, older_than_minutes: int = 15,
                        now: datetime | None = None, auto_only: bool = False) -> dict[str, Any]:
        """Sandbox reconcile for send_supplier_order's recorded:false case.

        For each draft PO whose order is still placed, if the outbox file was
        written (the supplier has the order) mark the PO sent with that file.

        A draft PO older than the cutoff with no outbox file is listed in
        "stranded": the send never happened (for example Rig stopped between
        recording the order and sending the PO). This method never writes the
        outbox or cancels an order; stranded auto-order POs need store review. A PO whose outbox file can't be read is
        listed in "unknown" (never in "stranded"). Safe to run repeatedly.
        auto_only=True (supplier-order recovery) lists only auto-ordered quotes,
        least recently tried first: drafts placed without auto-order never
        leave draft, so they would otherwise fill the list.
        """
        from rigbox import supplier_orders

        sandbox_orders(env)
        supplier_orders.outbox(env)
        listed = {"auto_only": True} if auto_only else {}
        drafts = await self.store.draft_supplier_orders(tenant_id, limit, **listed)
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(minutes=max(1, int(older_than_minutes)))
        old = set(await self.store.draft_supplier_orders(tenant_id, limit, before=cutoff, **listed))
        fixed, stranded, unknown = [], [], []
        for quote_id in drafts:
            try:
                purchase = await self._reconcile_one(tenant_id, quote_id, env)
            except DeliveryError:
                # The outbox file can't be read: neither sent nor stranded. It
                # is listed so a person sees the paid order waiting on them.
                logging.getLogger(__name__).warning("locate order send state unknown in reconcile: %s", quote_id)
                unknown.append(quote_id)
                continue
            if purchase is not None:
                fixed.append({"quote_id": quote_id, "purchase_order": purchase["number"]})
            elif quote_id in old:
                stranded.append(quote_id)
        return {"checked": len(drafts), "reconciled": fixed, "stranded": stranded, "unknown": unknown}
