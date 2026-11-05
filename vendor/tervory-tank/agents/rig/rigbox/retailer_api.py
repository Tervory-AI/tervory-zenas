"""Private retailer operations. Only Tank holds this control credential."""

import hmac
import json
import os
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from chassis.tenant_db import activate_tenant, tenant_scope

from rigbox.retailer import Retailer
from rigbox.service import RigError
from rigbox.quotes.senders import DeliveryError

router = APIRouter()
OPERATIONS = {"room-archive", "commerce-connect", "commerce-orders", "store-delivery", "confirm-store-payment", "panel", "settings", "embed-keys", "import", "delete-products", "mapping", "price-rule", "exchange-rate", "templates", "approve", "reject", "suggest", "tiger", "watch-tenants", "watch-products", "watch-context"}


@router.post("/internal/retailer/{operation}")
async def retailer_operation(operation: str, request: Request) -> JSONResponse:
    expected = os.environ.get("RIG_RETAILER_KEY", "")
    given = request.headers.get("x-rig-retailer-key", "")
    if not expected or not given or not hmac.compare_digest(expected.encode(), given.encode()):
        raise RigError("unauthorized", "Retailer control authentication required.", 401)
    if operation not in OPERATIONS:
        raise RigError("not_found", "Unknown retailer operation.", 404)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 12_000_000:
            raise RigError("invalid", "The file is too large.", 413)
    try:
        body = json.loads(raw)
        tenant = str(UUID(body["tenant_id"]))
        data = body.get("data", {})
        if not isinstance(data, dict):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise RigError("invalid", "A tenant and valid operation fields are required.", 422) from None
    from rigbox.rooms import require_owner, quiesced
    require_owner(tenant)
    if quiesced() and operation != "room-archive":
        raise RigError("unavailable", "This store room is archived.", 503)
    activate_tenant(tenant)
    service = request.app.state.rig
    retailer = Retailer(service)
    try:
        if operation == "room-archive":
            from rigbox.rooms import archive
            result = await archive(service, tenant)
        elif operation in {"commerce-connect", "commerce-orders", "store-delivery"}:
            from rigbox.cart_api import cart, commerce_operation
            from rigbox.commerce import CommerceError
            try:
                result = await commerce_operation(cart(request), tenant,
                    {"commerce-connect": "connect", "commerce-orders": "orders"}.get(operation, operation), data)
            except CommerceError:
                raise ValueError() from None
        elif operation == "watch-tenants":
            if tenant != "00000000-0000-0000-0000-000000000001":
                raise RigError("unauthorized", "Retailer control authentication required.", 403)
            with tenant_scope(None, administrative=True):
                result = {"tenants": await service.store.tenant_ids()}
        elif operation == "watch-products":
            result = {"tenant_id": tenant, "products": await service.store.watch_products(tenant, **({"catalogue": True} if data.get("catalogue") is True else {}))}
        elif operation == "watch-context":
            result = {"tenant_id": tenant, "product": await service.store.watch_context(tenant, str(data["sku"]))}
        elif operation == "confirm-store-payment":
            if (set(data) != {"quote_id"} or not isinstance(data["quote_id"], str)
                    or not 1 <= len(data["quote_id"]) <= 200):
                raise ValueError()
            from rigbox.quotes.privacy import shopper_body
            try:
                confirmation = await service.confirm_store_payment(tenant, data["quote_id"])
            except DeliveryError:
                # A worker may have lost its lease to a newer confirmation.
                # Return only this tenant's durable state, never the stale one.
                current = await service.store.quote_for(tenant, data["quote_id"])
                if not current:
                    raise RigError("not_found", "The request is not valid.", 404) from None
                payload = current["payload"]
                confirmation = {"request_id": data["quote_id"], "confirmed": True,
                                "state": payload["locate_state"]["state"], "items": [payload["product"]]}
            result = shopper_body(confirmation)
        elif operation == "panel":
            result = await retailer.panel(tenant)
        elif operation == "settings":
            result = await retailer.set_settings(tenant, data)
        elif operation == "embed-keys":
            result = await retailer.embed_key(tenant, data)
        elif operation == "mapping":
            import csv
            import zipfile
            import xml.etree.ElementTree as ET
            from rigbox.feeds.catalogue import suggest_mapping
            kind = data.get("provider")
            if kind == "google":
                kind = "xml"
            elif kind not in {"shopify", "woocommerce"}:
                kind = data.get("format")
            try:
                result = suggest_mapping({**data, "format": kind}, data["contents"])
            except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError, UnicodeError, csv.Error, ET.ParseError, zipfile.BadZipFile, RuntimeError):
                raise ValueError("Check the file format and product list path.") from None
        elif operation == "import":
            result = await retailer.import_file(tenant, data)
        elif operation == "delete-products":
            result = await retailer.delete_products(tenant, data)
        elif operation == "price-rule":
            result = await retailer.set_rule(tenant, data)
        elif operation == "exchange-rate":
            result = await retailer.set_rate(tenant, data, body.get("founder") is True)
        elif operation == "templates":
            result = await retailer.set_templates(tenant, data)
        elif operation in {"approve", "reject"}:
            part = str(data["part_sku"])
            sku, family = data.get("fits_sku"), data.get("fits_family")
            if not part or bool(sku) == bool(family):
                raise ValueError()
            async with service.store.atomic(tenant) as store:
                changed = await getattr(store, operation + "_fit")(tenant, part, sku, family)
            if not changed:
                raise RigError("not_found", "That fit is not in this catalogue.", 404)
            result = {"saved": True}
        elif operation == "suggest":
            if service.models is None:
                raise RigError("unavailable", "Fit suggestions are unavailable. Nothing was approved.", 503)
            await service.suggest(tenant)
            result = await service.suggested_fits(tenant)
        else:
            result = {"updated": await service.apply_watch(tenant, str(data["sku"]), data)}
    except (ValueError, TypeError, KeyError):
        raise RigError("invalid", "Check the fields. Nothing was changed.", 422) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
