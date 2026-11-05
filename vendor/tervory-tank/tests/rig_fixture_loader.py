"""Disk catalogue loaders for isolated tests only; never packaged in Rig."""
from pathlib import Path
from typing import Any
from uuid import UUID
import json

def load_folder(folder: Path) -> dict[str, Any]:
    from rigbox.catalogue import (tenant_id_for, parse_products_csv, parse_fits_csv, parse_market_csv)
    publish_path = folder / "publish.json"
    publish: dict[str, Any] = {}
    publish_error = ""
    if publish_path.is_file():
        try:
            publish = read_publish(publish_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            publish_error = str(exc)
    try:
        tenant_id = tenant_id_for(folder.name, str(publish.get("tenant_id") or "") or None)
    except ValueError:
        tenant_id = ""
        publish_error = publish_error or "tenant_id is not a uuid"
    products_path = folder / "products.csv"
    fits_path = folder / "fits.csv"
    products = parse_products_csv(products_path.read_text(encoding="utf-8")) if products_path.is_file() else {
        "rows": [],
        "rejected": [{"row": 0, "reason": "products.csv is missing"}],
    }
    fits = parse_fits_csv(fits_path.read_text(encoding="utf-8")) if fits_path.is_file() else {
        "rows": [],
        "rejected": [],
    }
    market_path = folder / "market.csv"
    market = parse_market_csv(market_path.read_text(encoding="utf-8")) if market_path.is_file() else None
    return {
        "slug": folder.name,
        "tenant_id": tenant_id,
        "publish": publish,
        "publish_error": publish_error,
        "products": products,
        "fits": fits,
        "market": market,
    }


async def load_catalogue(self, folder: Path) -> dict[str, Any]:
    loaded = load_folder(folder)
    report = await import_catalogue(self, loaded)
    if loaded["tenant_id"] and not loaded["publish_error"]:
        shop = loaded["publish"].get("shop", {})
        if hasattr(self.store, "retailers"):
            self.store.retailers[loaded["tenant_id"]]["shop"] = shop
        else:
            async with self.store.pool.acquire() as conn:
                await conn.execute("UPDATE agent_rig.retailers SET shop=$2::jsonb WHERE tenant_id=$1", UUID(loaded["tenant_id"]), json.dumps(shop))
    return report

async def import_catalogue(self, loaded: dict[str, Any]) -> dict[str, Any]:
    from rigbox.catalogue import check_fit_refs, families_from
    from rigbox.store import _fit_key
    tenant = loaded["tenant_id"]
    report: dict[str, Any] = {"slug": loaded["slug"], "tenant_id": tenant, "products": {}, "fits": {}}
    if not tenant:
        reason = loaded["publish_error"] or "no tenant"
        report["products"] = {"added": 0, "updated": 0, "rejected": [{"row": 0, "reason": reason}]}
        report["fits"] = {"added": 0, "updated": 0, "rejected": []}
        return report
    products = loaded["products"]
    if products["rejected"]:
        report["products"] = {"added": 0, "updated": 0, "rejected": products["rejected"]}
    else:
        added = updated = 0
        for row in products["rows"]:
            action = await self.store.upsert_product(tenant, row)
            if action == "added":
                added += 1
            elif action == "updated":
                updated += 1
        report["products"] = {"added": added, "updated": updated, "rejected": []}
        for family, members in families_from(await self.store.products(tenant)).items():
            await self.store.set_family(tenant, family, members)
        report["market"] = await _import_market(self, tenant, loaded.get("market"))
    if not loaded["publish_error"]:
        await _apply_publish(self, tenant, loaded["slug"], loaded["publish"], report)
    fits = loaded["fits"]
    if fits["rejected"]:
        report["fits"] = {"added": 0, "updated": 0, "rejected": fits["rejected"]}
        return report
    known = {str(row["sku"]) for row in await self.store.products(tenant)}
    family_names = set(families_from(await self.store.products(tenant)))
    ref_errors = check_fit_refs(fits["rows"], known, family_names)
    if ref_errors:
        report["fits"] = {"added": 0, "updated": 0, "rejected": ref_errors}
        return report
    existing = {_fit_key(row) for row in await self.store.fits(tenant)}
    added = updated = 0
    for row in fits["rows"]:
        if _fit_key(row) in existing:
            updated += 1
        else:
            added += 1
        await self.store.upsert_fit(tenant, row)
    report["fits"] = {"added": added, "updated": updated, "rejected": []}
    return report


async def _apply_publish(self, tenant_id: str, slug: str, publish: dict[str, Any], report: dict[str, Any]) -> None:
    from rigbox.service import _KEY, hash_key
    keys = publish.get("keys") if isinstance(publish.get("keys"), list) else []
    parsed: list[tuple[str, list[str]]] = []
    rejected: list[dict[str, Any]] = []
    for index, item in enumerate(keys, start=1):
        if not isinstance(item, dict):
            rejected.append({"row": index, "reason": "key must start with rk_pub_ and list origins"})
            continue
        raw = str(item.get("key") or "")
        origins = item.get("origins")
        if _KEY.fullmatch(raw) is None or not isinstance(origins, list) or not origins:
            rejected.append({"row": index, "reason": "key must start with rk_pub_ and list origins"})
            continue
        parsed.append((raw, [str(origin) for origin in origins]))
    if rejected:
        report["keys_rejected"] = rejected
    else:
        for raw, origins in parsed:
            await self.store.put_key(tenant_id, hash_key(raw), "publishable", origins)
        report["keys"] = len(parsed)
    email = publish.get("email")
    webhook = publish.get("webhook")
    await self.store.put_retailer(
        tenant_id,
        slug,
        str(email) if email else None,
        str(webhook) if webhook else None,
        str(publish.get("name") or ""),
    )
    templates = publish.get("setup_templates")
    if isinstance(templates, dict):
        for category, roles in templates.items():
            await self.store.set_template(tenant_id, str(category), roles)
    feed = publish.get("feed")
    if isinstance(feed, dict) and feed.get("url") and feed.get("format"):
        await self.store.put_feed(
            tenant_id,
            str(feed["format"]),
            str(feed["url"]),
            str(feed.get("currency") or ""),
        )


async def _import_market(self, tenant_id: str, market: dict[str, Any] | None) -> dict[str, Any]:
    from rigbox.locate import catalogue as supply_catalogue
    if market is None:
        return {"rows": 0, "rejected": []}
    if market.get("rejected"):
        return {"rows": 0, "rejected": list(market["rejected"])}
    known = {str(row.get("sku") or "") for row in await supply_catalogue(self.store, tenant_id)}
    unknown = [row for row in market.get("rows") or [] if str(row.get("sku") or "") not in known]
    if unknown:
        return {
            "rows": 0,
            "rejected": [{"row": row.get("row") or 0, "reason": "sku is not in this catalogue"} for row in unknown],
        }
    await self.store.replace_market(tenant_id, list(market.get("rows") or []))
    return {"rows": len(market.get("rows") or []), "rejected": []}



def read_publish(text: str) -> dict[str, Any]:
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("publish file is not JSON") from exc
    if not isinstance(loaded, dict):
        raise ValueError("publish file must be an object")
    return loaded
