"""Bounded, non-fetching catalogue parsers and field-map suggestions.

Only field names leave the mapping preview. Raw prices remain server-side.
XLSX is read as data: no formulas, macros, external links or relationships run.
"""

import base64
import csv
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

from rigbox.catalogue import _product_row

LIMIT = 8_000_000
ROW_LIMIT = 50_000
ALIASES = {
    "sku": ("sku", "itemnumber", "itemno", "rsrstocknumber", "rsrstocknbr", "id", "code", "productcode"),
    "name": ("name", "title", "description", "productname"),
    "brand": ("brand", "manufacturer", "vendor"),
    "category": ("category", "producttype", "googleproductcategory", "department"),
    "price": ("price", "dealerprice", "prc", "rsrregularprice", "cost", "unitcost", "sellingprice", "amount"),
    "quantity": ("quantity", "qty", "qtyavailable", "inventoryquantity", "stock"),
    "active": ("active", "available", "availability", "stockstatus"),
    "image_url": ("imageurl", "imagelink", "image", "picture"),
    "url": ("url", "link", "permalink"),
    "mpn": ("mpn", "manufacturerpartnumber", "partnumber"),
    "eta": ("eta", "estimatedarrival", "leadtime"),
    "ship_days": ("shipdays", "leadtimedays"),
    "region": ("region", "shipfrom", "country"),
    "gtin": ("gtin", "upc", "upccode", "barcode"),
    "family": ("family",),
    "fits_sku": ("fitssku",), "fits_family": ("fitsfamily",), "fit_evidence": ("fitevidence",),
}
REQUIRED = ("sku", "name", "brand", "category", "price", "image_url")


def safe_xml(raw: bytes | str) -> ET.Element:
    # Reject UTF-16/32 and declarations before invoking an XML parser.
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw.lstrip("\ufeff")
    if "\x00" in text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML declarations are not allowed.")
    return ET.fromstring(text.strip())


def flatten(value: dict, prefix: str = "", depth: int = 0) -> dict:
    if depth > 12:
        raise ValueError("Fields are nested too deeply.")
    result = {}
    for key, item in value.items():
        name = prefix + str(key)
        if isinstance(item, dict):
            result.update(flatten(item, name + ".", depth + 1))
        elif not isinstance(item, list):
            result[name] = item
    if len(result) > 200 or any(len(key) > 160 for key in result):
        raise ValueError("Too many or oversized fields.")
    return result


def xml_fields(node: ET.Element, depth: int = 0) -> dict:
    if depth > 12:
        raise ValueError("XML fields are nested too deeply.")
    result = {"@" + key.rsplit("}", 1)[-1]: value for key, value in node.attrib.items()}
    for child in node:
        name = child.tag.rsplit("}", 1)[-1]
        value = xml_fields(child, depth + 1) if len(child) else (child.text or "").strip()
        if name in result:
            raise ValueError("Repeated fields need a product record path.")
        result[name] = value
    return result


def xlsx_rows(raw: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = archive.infolist()
        if len(files) > 1000 or sum(f.file_size for f in files) > 32_000_000:
            raise ValueError("Workbook expands beyond the size limit.")
        if len({f.filename for f in files}) != len(files):
            raise ValueError("Duplicate workbook entries.")
        def read(name):
            with archive.open(name) as source:
                data = source.read(LIMIT + 1)
            if len(data) > LIMIT:
                raise ValueError("Workbook part too large.")
            return safe_xml(data)
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(n.text or "" for n in si.iter() if n.tag.endswith("}t")) for si in read("xl/sharedStrings.xml")]
        # Resolve the first worksheet through workbook relationships, never fetch them.
        workbook = read("xl/workbook.xml")
        sheets = [n for n in workbook.iter() if n.tag.endswith("}sheet")]
        if not sheets:
            raise ValueError("Workbook has no worksheet.")
        relationship = sheets[0].get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        targets = [n for n in read("xl/_rels/workbook.xml.rels") if n.get("Id") == relationship and n.get("TargetMode") != "External"]
        target = targets[0].get("Target", "") if len(targets) == 1 else ""
        name = target.lstrip("/") if target.startswith("/xl/") else "xl/" + target
        if not re.fullmatch(r"xl/worksheets/[A-Za-z0-9_-]+\.xml", name):
            raise ValueError("Invalid worksheet relationship.")
        table = []
        for row in read(name).iter():
            if not row.tag.endswith("}row"):
                continue
            values = {}
            for cell in row:
                ref = re.fullmatch(r"([A-Z]{1,3})[1-9][0-9]*", cell.get("r", ""))
                if ref is None:
                    raise ValueError("Invalid cell reference.")
                index = 0
                for char in ref[1]:
                    index = index * 26 + ord(char) - 64
                if index > 200 or index in values or any(n.tag.endswith("}f") for n in cell):
                    raise ValueError("Use a value-only workbook with at most 200 columns.")
                kind = cell.get("t")
                # Keep numeric XML text exact; catalogue money validation uses Decimal.
                value = next((n.text or "" for n in cell if n.tag.endswith("}v")), "")
                if kind == "s":
                    if not value.isdigit() or int(value) >= len(strings):
                        raise ValueError("Invalid shared string.")
                    value = strings[int(value)]
                elif kind == "inlineStr":
                    value = "".join(n.text or "" for n in cell.iter() if n.tag.endswith("}t"))
                elif kind == "e":
                    raise ValueError("Workbook contains an error cell.")
                values[index] = value
            if values:
                table.append(values)
            if len(table) > ROW_LIMIT + 1:
                raise ValueError("Too many products.")
        if len(table) < 2:
            raise ValueError("Workbook needs headings and products.")
        headings = table[0]
        if len(set(headings.values())) != len(headings) or any(not h for h in headings.values()):
            raise ValueError("Workbook headings must be unique.")
        return [{headings[i]: row.get(i, "") for i in headings} for row in table[1:]]


def source_rows(record: dict, contents: str | bytes) -> tuple[list[dict], dict]:
    kind = record["format"]
    if record.get("encoding") == "base64":
        if len(contents) > 10_666_668:
            raise ValueError("File too large.")
        raw = base64.b64decode(contents, validate=True)
    else:
        raw = contents.encode() if isinstance(contents, str) else contents
    if len(raw) > LIMIT:
        raise ValueError("File too large.")
    path = record.get("records_path", "")
    metadata = {}
    if kind == "xlsx":
        rows = xlsx_rows(raw)
    elif kind in {"csv", "tsv"}:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), delimiter="\t" if kind == "tsv" else ",", strict=True)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("Headings must be unique.")
        rows = []
        for row in reader:
            if None in row or len(rows) >= ROW_LIMIT:
                raise ValueError("Invalid or oversized table.")
            rows.append(row)
    elif kind in {"json", "shopify", "woocommerce"}:
        loaded = json.loads(raw)
        metadata = loaded.get("rig", {}) if isinstance(loaded, dict) else {}
        rows = loaded
        if path:
            for part in path.split("."):
                rows = rows[part]
        elif kind in {"shopify", "woocommerce"} and isinstance(rows, dict):
            rows = rows.get("products")
        elif isinstance(rows, dict):
            candidates = [value for key, value in rows.items() if key != "rig" and isinstance(value, list)]
            if len(candidates) != 1:
                raise ValueError("Choose the product list path.")
            rows = candidates[0]
        if not isinstance(rows, list) or len(rows) > ROW_LIMIT or any(not isinstance(row, dict) for row in rows):
            raise ValueError("A bounded list of products is required.")
        if kind == "shopify":
            from .mapper import _first_image, _variant_image, _flag
            if any(not isinstance(item.get("variants"), list) for item in rows) or sum(len(item["variants"]) for item in rows) > ROW_LIMIT:
                raise ValueError("A bounded list of variants is required.")
            rows = [{**item, "brand": item.get("vendor", ""), "name": item.get("title", ""),
                     "category": item.get("product_type", ""), "image_url": _variant_image(variant) or _first_image(item.get("images")),
                     "url": item.get("handle", ""), **variant,
                     "active": _flag(item.get("status"), default="active", yes={"active", "publish", "yes"}) and variant.get("available") is not False}
                    for item in rows for variant in item.get("variants", [])]
        elif kind == "woocommerce":
            from .mapper import _first_image, _woo_brand, _woo_category, _woo_price, _flag
            rows = [{**item, "brand": _woo_brand(item), "category": _woo_category(item),
                     "image_url": _first_image(item.get("images")), "url": item.get("permalink", ""), "price": _woo_price(item),
                     "gtin": item.get("global_unique_id") or item.get("gtin", ""),
                     "active": _flag(item.get("status"), default="publish", yes={"publish", "active", "yes"}) and item.get("stock_status") != "outofstock"}
                    for item in rows]
    elif kind == "xml":
        root = safe_xml(raw)
        if path:
            nodes = [root]
            parts = path.split(".")
            if parts[0] == root.tag.rsplit("}", 1)[-1]:
                parts = parts[1:]
            for part in parts:
                nodes = [child for node in nodes for child in node if child.tag.rsplit("}", 1)[-1] == part]
        else:
            nodes = [n for n in root.iter() if n.tag.rsplit("}", 1)[-1].lower() in {"item", "product", "record", "article"}]
            if not nodes:
                nodes = list(root)
        if len(nodes) > ROW_LIMIT:
            raise ValueError("Too many products.")
        rows = [xml_fields(node) for node in nodes]
    else:
        raise ValueError("Unknown catalogue format.")
    if not isinstance(rows, list) or not rows or len(rows) > ROW_LIMIT or any(not isinstance(row, dict) for row in rows):
        raise ValueError("A bounded list of products is required.")
    return [flatten(row) for row in rows], metadata


def suggest_mapping(record: dict, contents: str | bytes) -> dict:
    rows, _ = source_rows(record, contents)
    return mapping_for_rows(rows)


def mapping_for_rows(rows: list[dict]) -> dict:
    fields = sorted({key for row in rows for key in row})
    if len(fields) > 200:
        raise ValueError("Too many fields.")
    normalize = lambda value: re.sub(r"[^a-z0-9]", "", value.lower())
    columns = {}
    for target, aliases in ALIASES.items():
        if target in fields:
            columns[target] = target
            continue
        matches = [field for field in fields if normalize(field.split(".")[-1]) in aliases]
        if len(matches) == 1:
            columns[target] = matches[0]
    return {"fields": fields, "columns": columns, "required": list(REQUIRED)}


def map_catalogue(record: dict, contents: str | bytes) -> dict[str, Any]:
    rows, metadata = source_rows(record, contents)
    suggested = mapping_for_rows(rows)["columns"]
    explicit = record.get("columns")
    # Keep existing ACCESS maps, which named price/quantity/upc while labels used
    # standard headings. New UI maps include all required fields explicitly.
    columns = {**{key: value for key, value in suggested.items() if key in REQUIRED},
               **{("gtin" if key == "upc" else key): value for key, value in explicit.items()}} if explicit else suggested
    if any(key not in ALIASES or not isinstance(value, str) for key, value in columns.items()):
        raise ValueError("Unknown product field.")
    products, rejected, fits, seen = [], [], [], set()
    for index, source in enumerate(rows, 1):
        try:
            values = {key: str(source.get(field) if source.get(field) is not None else "").strip() for key, field in columns.items()}
            if any(not values.get(key) for key in REQUIRED):
                raise ValueError("Missing product field.")
            values["currency"] = record["currency"]
            # Merchant-style amounts may include the declared currency; no silent currency override.
            amount = values["price"].split()
            if len(amount) == 2 and amount[1].upper() == record["currency"]:
                values["price"] = amount[0]
            active = values.get("active", "true").lower() in {"true", "yes", "1", "active", "in_stock", "in stock", "instock", "available"}
            catalogue_active = active
            if values.get("quantity"):
                from rigbox.pricing import number
                quantity = number(values["quantity"])
                in_stock = quantity > 0
                active = active and in_stock
            values["active"] = "true" if active else "false"
            product = _product_row(values, index)
            product["supply_eligible"] = bool(catalogue_active and values.get("quantity") and quantity == 0)
            product["mpn"] = values.get("mpn") or None
            product["quantity"] = str(quantity) if values.get("quantity") else None
            product["eta"] = values.get("eta") or None
            product["ship_days"] = int(values["ship_days"]) if values.get("ship_days") else None
            if product["ship_days"] is not None and product["ship_days"] < 0:
                raise ValueError("Invalid ship days.")
            product["region"] = values.get("region") or ""
            if product["sku"] in seen:
                raise ValueError("Repeated product identifier.")
            seen.add(product["sku"])
            products.append(product)
            if values.get("fits_sku") or values.get("fits_family"):
                fits.append({"part_sku": product["sku"], "fits_sku": values.get("fits_sku"), "fits_family": values.get("fits_family"), "note": values.get("fit_evidence", "")})
        except (ValueError, TypeError):
            rejected.append({"row": index, "reason": "Check the confirmed product fields."})
    if not isinstance(metadata, dict):
        raise ValueError("Invalid catalogue metadata.")
    return {"rows": [] if rejected else products, "rejected": rejected, "metadata": {**metadata, "fits": [*metadata.get("fits", []), *fits]}}
