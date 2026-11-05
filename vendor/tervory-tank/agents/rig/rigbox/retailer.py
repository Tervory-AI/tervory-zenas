"""Retailer operations behind Tank's authenticated, tenant-scoped corridor."""

import json
import re
from uuid import uuid4
from typing import Any

from rigbox.catalogue import _product_row, check_fit_refs, families_from
from rigbox.feeds import pull
from rigbox.pricing import currency, indicative_price, number, price_rule, resolved_rule, selling_price
from rigbox.service import RigError, Service


def templates(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict) or len(value) > 30:
        raise ValueError("Choose at most 30 category templates.")
    result = {}
    for category, roles in value.items():
        if not isinstance(category, str) or not category.strip() or len(category) > 80 or not isinstance(roles, dict) or len(roles) > 12:
            raise ValueError("Check the category and its roles.")
        if any(not isinstance(k, str) or not isinstance(v, str) or not k.strip() or not v.strip() or max(len(k), len(v)) > 80 for k, v in roles.items()):
            raise ValueError("Every role needs a product category.")
        result[category.strip()] = {k.strip(): v.strip() for k, v in roles.items()}
    return result


def map_import(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if data.get("parse_error"):
        raise ValueError("The source file could not be read.")
    provider = data["provider"]
    metadata = {}
    if data.get("contents") and data.get("format") in {"csv", "tsv", "json", "xml", "xlsx"} and (provider not in {"shopify", "woocommerce", "google"} or data.get("columns")):
        kind = provider if provider in {"shopify", "woocommerce", "google"} else data["format"]
        mapped = pull({**data, "format": kind}, data["contents"])
        metadata = mapped.get("metadata", {})
    elif provider in {"shopify", "woocommerce", "google"}:
        raw = data["contents"]
        if isinstance(raw, (str, bytes)) and len(raw.encode() if isinstance(raw, str) else raw) > 8_000_000:
            raise ValueError("File too large.")
        mapped = pull({"format": provider, "currency": data.get("currency", "")}, raw)
        if any(row["currency"] != data.get("currency", row["currency"]) for row in mapped["rows"]):
            raise ValueError("The feed currency differs from its declared currency.")
        if provider != "google":
            loaded = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(loaded, dict):
                metadata = loaded.get("rig") or {}
    else:
        rows = []
        rejected = []
        for index, row in enumerate(data.get("rows", []), 1):
            try:
                # ACCESS-1 already validated supplier amounts and identifiers.
                values = {key: str(row.get(key) or "") for key in ("sku", "name", "brand", "category", "image_url", "url", "family")}
                values.update(price=str(row.get("price", "")), currency=data.get("currency", "USD"),
                              gtin=str(row.get("upc") or ""), active="true" if number(row.get("quantity") or 0) > 0 else "false")
                rows.append(_product_row(values, index))
            except ValueError:
                rejected.append({"row": index, "reason": "Check the product columns and supplier amount."})
        mapped = {"rows": rows if not rejected else [], "rejected": rejected}
        metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("Feed metadata must be an object.")
    if len(mapped["rows"]) > 50000:
        raise ValueError("Too many products.")
    if not mapped["rows"] and not mapped["rejected"]:
        raise ValueError("The file has no products.")
    return mapped, metadata


class Retailer:
    def __init__(self, service: Service) -> None:
        self.service = service
        self.store = service.store

    async def panel(self, tenant: str) -> dict[str, Any]:
        settings = await self.store.rule(tenant)
        retailer = await self.store.retailer(tenant) or {}
        products = await self.store.products(tenant)
        return {
            "settings": {"name": retailer.get("name", ""), "email": retailer.get("email") or "",
                         "theme": retailer.get("theme", "neutral"),
                         "locate_enabled": retailer.get("locate_enabled") is True,
                         "blind_ship": retailer.get("blind_ship") is True,
                         "auto_order": retailer.get("auto_order") is True},
            "price_configured": bool(settings),
            "currency": (settings or {}).get("shop_currency", "ZAR"),
            "templates": await self.store.templates_for(tenant),
            "fits": (await self.service.suggested_fits(tenant))["fits"],
            "missed_searches": await self.store.missed_searches(tenant),
            "receipts": await self.store.receipts(tenant),
            "products": len(products),
            "product_rows": [{"sku": row["sku"], "name": row["name"], "price": str(row["price"]), "currency": row["currency"]} for row in products],
        }

    async def delete_products(self, tenant: str, data: dict[str, Any]) -> dict[str, Any]:
        skus = data.get("skus")
        if (not isinstance(skus, list) or not 1 <= len(skus) <= 5000
                or any(not isinstance(sku, str) or not sku.strip() for sku in skus)):
            raise ValueError()
        async with self.store.atomic(tenant) as store:
            deleted = await store.delete_products(tenant, list(dict.fromkeys(skus)))
            # Existing receipt shape: updated counts removals for this operation.
            receipt = {"id": str(uuid4()), "provider": "delete-products", "site": "", "feed_id": "",
                       "added": 0, "updated": deleted, "rejected": 0}
            await store.save_feed_receipt(tenant, receipt)
        return {"deleted": deleted, "receipt": receipt}

    async def set_settings(self, tenant: str, data: dict[str, Any]) -> dict[str, bool]:
        name = data.get("name", "")
        email = data.get("email", "")
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 120
                or not isinstance(email, str) or len(email) > 254
                or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email)):
            raise ValueError("Enter a store name and delivery email address.")
        previous = await self.store.retailer(tenant) or {}
        theme = data.get("theme", previous.get("theme", "neutral"))
        if theme not in ("neutral", "custom-nation"):
            raise ValueError()
        from rigbox.quotes.notice import notice

        if "quote_notice" in data and data["quote_notice"] is None:
            raise ValueError("The request is not valid.")
        configured_notice = data.get("quote_notice", previous.get("quote_notice"))
        validated_notice = notice(configured_notice)
        # Locate is an explicit per-store opt-in. Absent keeps the saved value.
        locate_enabled = data.get("locate_enabled", previous.get("locate_enabled", False))
        if type(locate_enabled) is not bool:
            raise ValueError("The request is not valid.")
        # Blind ship is a separate explicit opt-in, off by default.
        blind_ship = data.get("blind_ship", previous.get("blind_ship", False))
        if type(blind_ship) is not bool:
            raise ValueError("The request is not valid.")
        # Auto-order is its own explicit opt-in, off by default, sandbox only.
        auto_order = data.get("auto_order", previous.get("auto_order", False))
        if type(auto_order) is not bool:
            raise ValueError("The request is not valid.")
        delivery = data.get("delivery", previous.get("delivery", {}))
        if (not isinstance(delivery, dict) or delivery and (
                set(delivery) != {"min_days", "max_days"}
                or any(type(delivery[key]) is not int for key in ("min_days", "max_days"))
                or not 0 <= delivery["min_days"] <= delivery["max_days"] <= 365)):
            raise ValueError("The request is not valid.")
        await self.store.put_retailer(tenant, previous.get("slug") or tenant, email,
                                      previous.get("webhook"), name.strip(), theme=theme,
                                      quote_notice={key: validated_notice[key] for key in ("text", "url")}
                                      if validated_notice else None,
                                      locate_enabled=locate_enabled, blind_ship=blind_ship,
                                      auto_order=auto_order, delivery=delivery)
        return {"saved": True}

    async def embed_key(self, tenant: str, data: dict[str, Any]) -> dict:
        from rigbox.quotes.keys import embed_snippet, issue_embed_key

        retailer = await self.store.retailer(tenant) or {}
        if not retailer.get("email") or not await self.store.products(tenant):
            raise RigError("invalid", "Save a delivery email and publish your catalogue first.", 422)
        # Verify the shipped asset before issuing a key that cannot be installed.
        theme = retailer.get("theme", "neutral")
        embed_snippet("", theme=theme)
        result = await issue_embed_key(self.store, tenant, data.get("origins"))
        return {**result, "snippet": embed_snippet(result["publishable_key"], theme=theme)}

    async def reprice(self, store: Any, tenant: str) -> None:
        settings = await store.rule(tenant)
        target = (settings or {}).get("shop_currency", "ZAR")
        for row in await store.costs(tenant):
            amount = await self.amount(store, tenant, row, settings)
            await store.update_price(tenant, row["sku"], {"price": amount, "currency": target})

    async def amount(self, store: Any, tenant: str, row: dict, settings: dict | None):
        target = (settings or {}).get("shop_currency", "ZAR")
        rate = await store.rate(tenant, row["currency"], target)
        if row.get("price_basis", "cost") == "selling":
            return selling_price(row["cost"], rate)
        return indicative_price(row["cost"], resolved_rule(settings, row), rate)

    async def set_rule(self, tenant: str, data: dict[str, Any]) -> dict[str, bool]:
        rule = price_rule(data)
        scope = data.get("scope", "default")
        identifier = str(data.get("scope_id", "")).strip()
        if scope not in {"default", "region", "supplier"} or (scope != "default" and (not identifier or len(identifier) > 500)):
            raise ValueError("Choose a default, region or supplier rule.")
        if scope == "region" and not re.fullmatch("[A-Z]{2}", identifier):
            raise ValueError("Choose a two-letter region or country.")
        async with self.store.atomic(tenant) as store:
            settings = dict(await store.rule(tenant) or {})
            if scope == "default":
                settings.update(rule)
            else:
                # A scoped rule cannot silently establish a zero-cost default.
                if not all(key in settings for key in rule):
                    raise ValueError("Set the default price rule first.")
                settings["scopes"] = {**settings.get("scopes", {}), scope + ":" + identifier: rule}
            if data.get("shop_currency"):
                settings["shop_currency"] = currency(data["shop_currency"])
            await store.save_rule(tenant, settings)
            await self.reprice(store, tenant)
        return {"saved": True, "price_configured": True}

    async def set_rate(self, tenant: str, data: dict[str, Any], founder: bool = False) -> dict[str, bool]:
        if not founder:
            raise RigError("forbidden", "Only the founder can set an exchange rate.", 403)
        code = currency(data.get("currency", ""))
        target = currency(data.get("target_currency", "ZAR"))
        rate = number(data.get("rate"), maximum="1000000")
        if not rate or (code == target and rate != 1):
            raise ValueError("Use a currency code and a positive exchange rate.")
        async with self.store.atomic(tenant) as store:
            await store.save_rate(tenant, code, rate, target)
            await self.reprice(store, tenant)
        return {"saved": True}

    async def set_templates(self, tenant: str, data: dict[str, Any]) -> dict[str, bool]:
        values = templates(data)
        async with self.store.atomic(tenant) as store:
            for category, roles in values.items():
                await store.set_template(tenant, category, roles)
        return {"saved": True}

    async def import_file(self, tenant: str, data: dict[str, Any]) -> dict[str, Any]:
        receipt = {"id": str(uuid4()), "provider": data["provider"], "site": data["site"], "feed_id": data.get("feed_id", ""), "added": 0, "updated": 0, "rejected": 0}
        problems = []
        try:
            country = str(data.get("country", "US")).upper()
            basis = data.get("price_basis", "cost")
            if not re.fullmatch("[A-Z]{2}", country) or basis not in {"cost", "selling"}:
                raise ValueError("Declare the feed country and price type.")
            currency(data.get("currency", "USD"))
            mapped, metadata = map_import(data)
            problems = mapped["rejected"]
            if problems:
                raise ValueError("The file contains invalid products.")
            defaults = templates(metadata.get("setup_templates", {}))
            async with self.store.atomic(tenant) as store:
                settings = await store.rule(tenant)
                target = (settings or {}).get("shop_currency", "ZAR")
                existing = {row["sku"]: row for row in await store.products(tenant)}
                suppressed = await store.deleted_skus(tenant) if data.get("refresh") is True else set()
                removed_families = {row.get("family") for row in mapped["rows"] if row["sku"] in suppressed and row.get("family")}
                mapped["rows"] = [row for row in mapped["rows"] if row["sku"] not in suppressed]
                combined = {**existing, **{row["sku"]: row for row in mapped["rows"]}}
                families = metadata.get("families", {})
                if not isinstance(families, dict) or len(families) > 5000:
                    raise ValueError("Check the feed's families.")
                for family, members in families.items():
                    if isinstance(members, list):
                        if any(isinstance(sku, str) and sku in suppressed for sku in members):
                            removed_families.add(family)
                        members = [sku for sku in members if not isinstance(sku, str) or sku not in suppressed]
                    if not isinstance(family, str) or not family or len(family) > 120 or not isinstance(members, list) or any(sku not in combined for sku in members):
                        raise ValueError("Every family member must be in this catalogue.")
                    for sku in members:
                        combined[sku] = {**combined[sku], "family": family}
                fits = metadata.get("fits", [])
                if not isinstance(fits, list) or len(fits) > 50000:
                    raise ValueError("Check the supplied fits.")
                checked = []
                for row in fits:
                    if not isinstance(row, dict) or not row.get("part_sku") or bool(row.get("fits_sku")) == bool(row.get("fits_family")):
                        raise ValueError("Each fit needs a part and exactly one target.")
                    if row["part_sku"] in suppressed or row.get("fits_sku") in suppressed:
                        continue
                    if row.get("fits_family") in removed_families and not any(r.get("family") == row["fits_family"] for r in combined.values()):
                        continue
                    note = str(row.get("note", "")).strip()
                    if not note or len(note) > 2000:
                        raise ValueError("Each supplied fit needs evidence for review.")
                    checked.append({"part_sku": row["part_sku"], "fits_sku": row.get("fits_sku"), "fits_family": row.get("fits_family"),
                                    "note": note, "source": "suggested", "approved": False, "confidence": None, "rule": {}, "row": 0})
                if check_fit_refs(checked, set(combined), set(families_from(list(combined.values())))):
                    raise ValueError("A supplied fit refers to an unknown product or family.")
                if data.get("refresh") is not True:
                    await store.restore_products(tenant, [row["sku"] for row in mapped["rows"]])
                for row in mapped["rows"]:
                    sku = row["sku"]
                    # Source amounts exist only in the private cost table.
                    cost = {"sku": sku, "cost": row["price"], "currency": row["currency"], "provider": data["provider"], "site": data["site"],
                            "country": country, "price_basis": basis, "feed_id": data.get("feed_id", "")}
                    amount = await self.amount(store, tenant, cost, settings)
                    public = {**combined[sku], "price": amount, "currency": target, "attributes": {}}
                    action = await store.upsert_product(tenant, public)
                    receipt[action] += 1
                    await store.save_cost(tenant, cost)
                imported_skus = {row["sku"] for row in mapped["rows"]}
                for sku, row in combined.items():
                    if sku not in imported_skus and sku in existing and existing[sku].get("family") != row.get("family"):
                        await store.upsert_product(tenant, row)
                next_families = families_from(list(combined.values()))
                for family in set(families_from(list(existing.values()))) | set(next_families):
                    await store.set_family(tenant, family, next_families.get(family, []))
                current_templates = await store.templates_for(tenant)
                for category, roles in defaults.items():
                    if category not in current_templates:
                        await store.set_template(tenant, category, roles)
                # A refresh must not overwrite a human approval or rejection.
                reviewed = {(r["part_sku"], r.get("fits_sku") or "", r.get("fits_family") or "") for r in await store.fits(tenant)
                            if r.get("approved") or r.get("source") == "rejected"}
                for row in checked:
                    if (row["part_sku"], row.get("fits_sku") or "", row.get("fits_family") or "") not in reviewed:
                        await store.upsert_fit(tenant, row)
                await store.save_feed_receipt(tenant, receipt)
        except (ValueError, TypeError, KeyError):
            receipt.update(added=0, updated=0, rejected=max(1, len(problems)))
            await self.store.save_feed_receipt(tenant, receipt)
            return {"receipt": receipt, "ok": False, "message": "Nothing was imported. Check the file, private price rule and founder-set exchange rate."}
        result = {"receipt": receipt, "ok": True}
        if not metadata.get("fits"):
            if self.service.models is None:
                result["suggestions"] = "unavailable"
            else:
                try:
                    await self.service.suggest(tenant)
                    result["suggestions"] = "awaiting_review"
                except Exception:
                    # The import is committed and receipted. A model outage is
                    # reported separately and never changes approval status.
                    result["suggestions"] = "unavailable"
        return result
