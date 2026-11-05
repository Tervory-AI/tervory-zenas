"""Readable labels for stored measurements; original source fields stay intact."""
from decimal import Decimal
import re

from feeds.geo import area_label


def period_label(iso: str, frequency: str = "") -> str:
    from datetime import date
    try:
        day = date.fromisoformat(iso[:10])
    except ValueError:
        return "Date not supplied"
    if frequency == "annual":
        return str(day.year)
    if frequency == "monthly":
        return day.strftime("%b %Y")
    readable = f"{day.day} {day.strftime('%b %Y')}"
    return f"Week of {readable}" if frequency == "weekly" else readable


def observation_label(point: dict, presentation: dict | None = None) -> str:
    info = presentation or {}
    title = point["title"]
    place = area_label(point["geo"], point["geo_level"], title)
    if title == "Active listings in unmatched":
        return "Active listings with no matched county"
    for prefix, label in (("PRCP in ", "Precipitation in "), ("TAVG in ", "Average temperature in ")):
        if title.startswith(prefix):
            return label + place
    if point["geo_level"] in {"state", "county"} and " in " in title:
        if point["geo_level"] == "county":
            title = title.split(" in ", 1)[0] + " in " + place
        else:
            title = title.replace(f' in {point["geo"]}', f" in {place}")
    category = re.fullmatch(r"([0-9]+) checks in (.+)", title)
    if category:
        return f"Background checks in {category[2]} (type not described by publisher)"
    check = re.fullmatch(r"(.+) checks in (.+)", title)
    if check and check[1] in info.get("check_types", {}):
        return f'{info["check_types"][check[1]]} in {check[2]}'
    category = re.fullmatch(r"Active listings of type ([0-9]+)", title)
    if category:
        return info.get("listing_types", {}).get(category[1], "Active listings (licence type not described by publisher)")
    trade = re.fullmatch(r"(import|export) ([0-9]+) (from|to) (.+) via (.+)", title)
    if trade:
        product = info.get("trade_products", {}).get(trade[2], "Product not described by publisher")
        flow = "Imports" if trade[1] == "import" else "Exports"
        return f"{flow} of {product[0].lower() + product[1:]} {trade[3]} {trade[4].title()} through {place}"
    return title.replace("New listings in ", "Listings added to the register in ").replace("Dropped listings in ", "Listings removed from the register in ").replace("Freight transportation services index", "Freight volumes compared with 2000")


def event_label(event: dict, presentation: dict | None = None) -> str:
    labels = (presentation or {}).get("event_titles", {})
    if event["title"] in labels:
        return labels[event["title"]]
    if event["kind"] in {"alert", "severe_weather"}:
        return f'{area_label(event["geo"], "state", "")}: {event["title"]}'
    if event["kind"] == "bill_status":
        title = event["title"]
        title = re.sub(r"^AB (?=\d)", "Assembly Bill ", title)
        title = re.sub(r"^SB (?=\d)", "Senate Bill ", title)
        title = re.sub(r"\bEngrossed\b", "— amended text prepared for the next legislative step", title, flags=re.I)
        return f'{area_label(event["geo"], "state", "")}: {title}'
    return event["title"]


def unit_label(value: str, unit: str) -> str:
    if unit == "index 2000=100":
        return "points (average in 2000 = 100)"
    if unit == "index":
        return "points (publisher did not supply a comparison baseline)"
    forms = {
        "listings": ("listing", "listings"),
        "checks": ("check", "checks"),
        "holders": ("holder", "holders"),
        "persons": ("person", "people"),
        "people": ("person", "people"),
        "dollars": ("US dollar", "US dollars"),
        "dollars per gallon": ("US dollar per gallon", "US dollars per gallon"),
        "inches": ("inch", "inches"),
        "degrees F": ("degree Fahrenheit", "degrees Fahrenheit"),
        "TEU": ("20-foot container unit", "20-foot container units"),
        "USD/metric ton": ("US dollar per metric ton", "US dollars per metric ton"),
        "USD/kg": ("US dollar per kilogram", "US dollars per kilogram"),
    }
    singular, plural = forms.get(unit, (unit, unit))
    return singular if abs(Decimal(str(value))) == 1 else plural


def notice_link(event: dict, presentation: dict | None = None) -> dict:
    """Keep transport URLs in evidence, but send people to readable pages."""
    from urllib.parse import urlsplit
    target = urlsplit(event["url"])
    machine = (target.hostname or "").startswith("api.") or any(part in target.path for part in ("/api/", "/webservices/", ".json"))
    if machine:
        return {"display_url": (presentation or {}).get("publisher_url", ""), "link_label": "View current notices on the publisher’s website"}
    return {"display_url": event["url"] if target.scheme in {"http", "https"} else "", "link_label": "Original notice"}
