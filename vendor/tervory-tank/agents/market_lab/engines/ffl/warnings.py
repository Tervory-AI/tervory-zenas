"""First shipping chains and the market words; statistics live in core."""

from typing import Any
import hashlib


def plan() -> dict[str, Any]:
    from engines.ffl.engine import load_map
    return load_map()["warnings"]


def product_series(watch: dict, observations: list[dict]) -> list[dict]:
    """Tiger's recorded product name is the subject; never invent a category."""
    if watch.get("engine") not in {"", "ffl"}:
        return []
    named = [row for row in observations if str(row.get("product_name") or "").strip()]
    if not named:
        return []
    name = str(max(named, key=lambda row: str(row.get("ts")))["product_name"])
    common = {
        "ref": str(watch["id"]) + ":" + hashlib.sha256(name.encode()).hexdigest()[:16],
        "name": name,
        "url": watch["url"],
        "geo": "US",
        "geo_level": "national",
    }
    stock, prices = [], {}
    for row in sorted(observations, key=lambda row: str(row.get("ts"))):
        if row.get("product_name") != name:
            continue  # A changed product page must not mix two products into one history.
        availability = (
            str(row.get("availability") or "")
            .rsplit("/", 1)[-1]
            .lower()
            .replace("-", "")
            .replace(" ", "")
            .replace("_", "")
        )
        if availability in {"instock", "outofstock"}:
            stock.append(
                {
                    "date": str(row["ts"])[:10],
                    "value": "2" if availability == "instock" else "1",
                    "display_value": "Available"
                    if availability == "instock"
                    else "Unavailable",
                    "observation_id": str(row["id"]),
                }
            )
        if row.get("price") is not None and row.get("currency"):
            prices.setdefault(str(row["currency"]), []).append(
                {
                    "date": str(row["ts"])[:10],
                    "value": str(row["price"]),
                    "observation_id": str(row["id"]),
                }
            )
    result = []
    if stock:
        result.append(
            {
                **common,
                "source": "tiger_stock",
                "unit": "availability",
                "currency": "none",
                "title": f"Availability of {name}",
                "explanation": "Saved product-page availability. Calculations encode available as 2 and unavailable as 1; these are status labels, not stock quantities.",
                "points": stock,
            }
        )
    for currency, points in prices.items():
        result.append(
            {
                **common,
                "ref": common["ref"] + ":" + currency,
                "source": "tiger_price",
                "unit": currency,
                "currency": currency,
                "title": f"Price of {name}",
                "points": points,
            }
        )
    return result
