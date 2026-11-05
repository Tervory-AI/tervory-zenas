"""RIG-4 additions isolated from catalogue, feed and ranking work."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from rigbox.logic import fitting_parts
from rigbox.service import RigError, Service, _clean_text, _contact

from .senders import DeliveryError
from .notice import notice, receipt



_ADDRESS = {"name": 120, "street": 200, "street2": 200, "city": 120, "zip": 20, "state": 120,
            "country": 2, "phone": 40}
_ADDRESS_REQUIRED = ("name", "street", "city", "zip", "country")
log = logging.getLogger("agent")


def ship_address(value: Any) -> dict[str, str]:
    """Blind-ship delivery address from the shopper. Strict: known keys, sane lengths."""
    if not isinstance(value, dict) or set(value) - set(_ADDRESS):
        raise RigError("invalid", "A delivery address is required.", 422)
    address: dict[str, str] = {}
    for key, limit in _ADDRESS.items():
        item = value.get(key)
        if item is None or item == "":
            continue
        if not isinstance(item, str) or len(item.strip()) > limit or any(ch in item for ch in "\r\n\x00"):
            raise RigError("invalid", "A delivery address is required.", 422)
        address[key] = item.strip()
    if any(not address.get(key) for key in _ADDRESS_REQUIRED) or not re.fullmatch(r"[A-Za-z]{2}", address["country"]):
        raise RigError("invalid", "A delivery address is required.", 422)
    address["country"] = address["country"].upper()
    return address


class QuoteService(Service):
    def __init__(self, store: Any, models: Any, handoff: Any, quotes: Any) -> None:
        super().__init__(store, models, handoff)
        self.quote_store = quotes
        # Locate orders and supplier POs live in Rig's own database.
        from rigbox.fulfilment import RigOrders
        self.fulfilment: Any = RigOrders(store)

    async def validate_setup(self, tenant: str, setup: dict[str, Any]) -> None:
        parts = setup.get("parts")
        if not isinstance(parts, list) or not parts or len(parts) > 50:
            raise RigError("invalid", "Choose a setup from this catalogue.", 422)
        try:
            ids = [part["product"]["id"] for part in parts]
            roles = [part["role"] for part in parts]
            if not all(isinstance(value, str) and value for value in ids + roles):
                raise ValueError
            if len(set(ids)) != len(ids) or len(set(roles)) != len(roles):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise RigError("invalid", "Choose a setup from this catalogue.", 422) from None
        products = await self._active(tenant)
        centre = next((p for p in products if p["sku"] == ids[0]), None)
        if not centre:
            raise RigError("invalid", "That setup is not in this catalogue.", 422)
        fits = await self._approved_fits(tenant)
        allowed = {p["sku"] for p, _ in fitting_parts(centre, products, fits)}
        if any(sku not in allowed for sku in ids[1:]):
            raise RigError("fit", "A part no longer has an approved fit. Choose the setup again.", 422)

    async def swap(self, tenant_id: str, setup: dict[str, Any], role: str) -> dict[str, Any]:
        await self.validate_setup(tenant_id, setup)
        return await super().swap(tenant_id, setup, role)

    async def quote(self, tenant_id: str, body: dict[str, Any], *, hold: bool = False) -> dict[str, Any]:
        self._quote_fields(body.get("contact"))
        if isinstance(body.get("setup"), dict):
            await self.validate_setup(tenant_id, body["setup"])
        payload, contact = await self.quote_payload(tenant_id, body)
        evidence = await self._notice_receipt(tenant_id, body.get("notice_version"))
        if evidence:
            payload["notice_receipt"] = evidence
        return await self._store_quote(tenant_id, payload, contact, hold=hold)

    async def link_quote(self, tenant_id: str, url: str, note: str,
                         contact: dict[str, Any] | None = None, *, hold: bool = False,
                         notice_version: str | None = None) -> dict[str, Any]:
        self._quote_fields(contact)
        note = _clean_text(note, 4000)
        try:
            parsed = urlsplit(url.strip())
        except ValueError:
            raise RigError("invalid", "That link is not a web address.", 422) from None
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or len(url) > 2048 or len(note) > 4000):
            raise RigError("invalid", "That link is not a web address.", 422)
        payload = {"kind": "link", "url": parsed.geturl(), "note": note.strip()}
        evidence = await self._notice_receipt(tenant_id, notice_version)
        if evidence:
            payload["notice_receipt"] = evidence
        return await self._store_quote(tenant_id, payload,
            _contact(contact) if contact else {}, hold=hold)

    @staticmethod
    def _quote_fields(contact: Any) -> None:
        if contact is not None and (not isinstance(contact, dict)
                                   or set(contact) - {"name", "email", "phone"}):
            raise RigError("invalid", "The request is not valid.", 422)

    async def privacy_notice(self, tenant: str) -> dict[str, str] | None:
        retailer = await self.store.retailer(tenant) or {}
        return notice(retailer.get("quote_notice"))

    async def _notice_receipt(self, tenant: str, shown_version: str | None) -> dict[str, str] | None:
        return receipt(await self.privacy_notice(tenant), shown_version)

    async def _store_quote(self, tenant_id: str, payload: dict[str, Any],
                           contact: dict[str, Any], *, hold: bool = False) -> dict[str, Any]:
        quote_id = "req_" + uuid4().hex
        await self.quote_store.create(tenant_id, {"id": quote_id, "payload": payload, "contact": contact}, hold=hold)
        if hold:
            logging.getLogger("agent").warning("rig_quote_held tenant_id=%s request_id=%s reason=tenant_quote_limit",
                                               tenant_id, quote_id)
        else:
            await self.deliver(tenant_id, quote_id)
        state = await self.quote_store.status(tenant_id, quote_id)
        body: dict[str, Any] = {"request_id": quote_id, "delivery": state["status"]}
        if state["status"] != "sent":
            body["message"] = ("Your request is saved. The retailer needs to check its delivery."
                               if state["status"] == "needs_person" else
                               "Your request is saved. Delivery to the retailer is pending.")
        if hasattr(self.handoff, "portal"):
            try:
                portal = self.handoff.portal(tenant_id)
                if portal:
                    body["portal_url"] = portal
            except DeliveryError:
                pass  # Delivery state remains visible; never expose invalid URLs.
        body["items"] = ([part["product"] for part in payload["setup"]["parts"]]
                         if payload.get("kind") == "setup" else [payload["product"]]
                         if payload.get("kind") == "product" else [])
        return body

    async def locate_accept(self, tenant_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Accept the held price and deliver the quote, without ordering anything."""
        from datetime import datetime, timezone
        from rigbox.locate_live import Holds, LocateRefused, hold_secret, read_token

        row = await self._locate_row(tenant_id, str(body.get("product_id") or ""))
        self._quote_fields(body.get("contact"))
        retailer = await self.store.retailer(tenant_id) or {}
        ship_to = ship_address(body.get("delivery")) if retailer.get("blind_ship") is True else None
        now = datetime.now(timezone.utc)
        try:
            secret = hold_secret()
        except LocateRefused:
            raise RigError("unavailable", "Ordering from other stores is not available.", 503) from None
        try:
            hold_id = read_token(secret, tenant_id, str(body.get("hold") or ""), now)
        except LocateRefused as exc:
            raise RigError("hold", "That price has expired. Check availability again.",
                           410 if str(exc) == "hold_expired" else 422) from None
        held = await Holds(self.store).take(tenant_id, hold_id, now)
        if held is None or held["product_id"] != row["sku"]:
            raise RigError("hold", "That price has expired. Check availability again.", 410)
        price = held["public"]["price"]
        product = dict(await self.product(tenant_id, row["sku"]))
        product["price"] = dict(price)
        product["delivery"] = {"ship_days": held["public"].get("ship_days"),
                               "eta": held["public"].get("eta"), "back_order": False}
        state = {"state": "accepted", "hold_id": hold_id, "price": price,
                 "accepted_at": now.isoformat()}
        payload = {"kind": "product", "product": product, "locate_state": state,
                   "locate": [{"product_id": row["sku"], "public": held["public"], "store": held["private"]}]}
        if ship_to is not None:
            payload["ship_to"] = ship_to
            state["blind_ship"] = True
        evidence = await self._notice_receipt(tenant_id, body.get("notice_version"))
        if evidence:
            payload["notice_receipt"] = evidence
        stored = await self._store_quote(tenant_id, payload, _contact(body.get("contact") or {}))
        return {**stored, "state": "accepted", "price": price, "items": [product]}

    async def confirm_store_payment(self, tenant_id: str, quote_id: str) -> dict[str, Any]:
        """Record the retailer's confirmation; Rig never handles the money.

        The quote CAS gives one caller a bounded placement lease. A retry after
        a crash resumes the same order/PO numbers and exclusive supplier outbox
        record. Historical provider states are never migrated or acted on.
        """
        from datetime import datetime, timedelta, timezone
        from rigbox.fulfilment import sandbox_orders
        from rigbox import supplier_orders

        quote = await self.store.quote_for(tenant_id, quote_id)
        old = quote["payload"].get("locate_state") if quote else None
        if not isinstance(old, dict) or not old.get("accepted_at") or old.get("state") not in {
            "accepted", "store_confirmed", "ordered", "supplier_ordered", "supplier_order_unconfirmed",
            "supplier_order_failed", "needs_person"
        }:
            raise RigError("not_found", "The request is not valid.", 404)
        payload = quote["payload"]

        def response(state: dict[str, Any]) -> dict[str, Any]:
            return {"request_id": quote_id, "confirmed": True, "state": state["state"],
                    "items": [payload["product"]]}
        if old["state"] in {"ordered", "supplier_ordered", "supplier_order_unconfirmed", "supplier_order_failed", "needs_person"}:
            return response(old)
        now = datetime.now(timezone.utc)
        if old["state"] == "store_confirmed":
            # An active worker owns this quote. A lost worker may be resumed
            # only after the supplier operation's bounded timeout has elapsed.
            if old.get("attempted_at") and datetime.fromisoformat(old["attempted_at"]) > now - timedelta(minutes=15):
                return response(old)
        retailer = await self.store.retailer(tenant_id) or {}
        auto = old.get("auto_order", retailer.get("auto_order") is True)
        located = payload["locate"][0]["store"]
        try:
            sandbox_orders()
            if auto:
                supplier_orders.outbox()
                if not supplier_orders.supplier_ready(located["source"]):
                    raise DeliveryError("order_rejected")
        except DeliveryError:
            raise RigError("unavailable", "Ordering from other stores is not available.", 503) from None
        state = {**old, "state": "store_confirmed", "auto_order": auto,
                 "store_confirmed_at": old.get("store_confirmed_at", now.isoformat()),
                 "attempted_at": now.isoformat(), "attempt_id": uuid4().hex}
        if not await self.store.swap_locate_state(tenant_id, quote_id, old, state):
            current = await self.store.quote_for(tenant_id, quote_id)
            return response(current["payload"]["locate_state"])

        async def record(**changes: Any) -> None:
            nonlocal state
            new = {**state, **changes}
            if not await self.store.swap_locate_state(tenant_id, quote_id, state, new):
                raise DeliveryError("order_unavailable")
            state = new

        try:
            order = await self.fulfilment.place_located_order(
                tenant_id=tenant_id, quote_id=quote_id, located=located, price=state["price"],
                ship={"address": payload["ship_to"]} if payload.get("ship_to") else None)
        except Exception as exc:
            # Stable order/PO numbers make placement safe to resume after a
            # transient failure, including a response lost after the insert.
            # Release only our own lease; a takeover is handled by the corridor.
            if isinstance(exc, DeliveryError) and str(exc) in {"order_mismatch", "order_rejected"}:
                await record(state="needs_person", needs_review=True)
            else:
                await record(attempted_at=None, attempt_id=None)
            return response(state)
        await record(order=order["order"]["number"], purchase_order=order["purchase_order"]["number"])
        if auto:
            try:
                supplied = await self.fulfilment.send_supplier_order(
                    tenant_id=tenant_id, quote_id=quote_id, store_name=retailer.get("name") or "",
                    store_email=retailer.get("email"))
            except DeliveryError as exc:
                # Preserve #578's strict send/cancel boundary. An existing or
                # unreadable outbox is never treated as an unsent supplier PO.
                cancelled = await self.fulfilment.cancel(tenant_id, quote_id,
                    f"supplier_order_failed:{exc}", unsent=str(exc) in {"outbox_not_configured", "order_mode"})
                if cancelled == "sent":
                    await record(state="supplier_ordered", purchase_order_state="sent")
                elif cancelled in {"cancelled", "missing"}:
                    await record(state="supplier_order_failed", needs_review=True)
                else:
                    await record(state="supplier_order_unconfirmed", needs_review=True)
                return response(state)
            await record(state="supplier_ordered" if supplied["purchase_order"]["state"] == "sent"
                         else "supplier_order_unconfirmed",
                         purchase_order_state=supplied["purchase_order"]["state"],
                         supplier_order={key: supplied["supplier_order"].get(key)
                                         for key in ("channel", "transport", "file", "duplicate")},
                         needs_review=supplied["purchase_order"]["state"] != "sent")
        else:
            await record(state="ordered")
        return response(state)


    async def deliver(self, tenant: str, quote_id: str) -> None:
        quote = await self.quote_store.claim(tenant, quote_id)
        if quote is None:
            return
        attempt = quote.pop("_attempt")
        error = None
        try:
            # Shorter than the five-minute delivery claim lease.
            async with asyncio.timeout(60):
                retailer = await self.store.retailer(tenant) or {}
                await self.handoff.send(tenant_id=tenant, email=retailer.get("email"),
                                        webhook=retailer.get("webhook"), quote=quote)
        except DeliveryError as exc:
            error = str(exc)
        except Exception:
            error = "retailer_unavailable"
        await self.quote_store.finish(tenant, quote_id, error, attempt)

    async def retry_pending(self, tenant: str) -> int:
        ids = await self.quote_store.pending(tenant)
        for quote_id in ids:
            await self.deliver(tenant, quote_id)
        return len(ids)
