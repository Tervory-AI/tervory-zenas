"""base.business_data 2.0.0 — pass 5 as plain code.

Same estimates as 1.0.0: size band, revenue band, business model, NAICS.
Confidence stays at or below 60, and the basis says these are estimates.
Size comes from a points score. The points and the band cutoffs are engine data,
as are licence weights, the revenue table, and category → NAICS.
This file holds no industry words and does not call a model.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from selectolax.parser import HTMLParser

_SOCIAL_HOSTS = ("facebook.com", "instagram.com", "youtube.com", "x.com", "twitter.com", "linkedin.com", "tiktok.com")
_LOCATION = re.compile(
    r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(locations?|stores?|facilities|shops?|branches)\b",
    re.I,
)
_HEADCOUNT = re.compile(
    r"\b(\d{1,6})\s+(?:employees|staff members|team members)\b|\b(?:staff|team|headcount)\s+of\s+(\d{1,6})\b",
    re.I,
)
_SINCE = re.compile(r"\bsince\s+((?:19|20)\d{2})\b", re.I)
_COPY = re.compile(
    r"(?:©|&copy;|\(c\))\s*((?:19|20)\d{2})(?:\s*[-–—]\s*((?:19|20)\d{2}))?",
    re.I,
)
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _load_html(payload: dict[str, Any]) -> str:
    html = payload.get("html")
    if isinstance(html, str) and html.strip():
        return html
    name = payload.get("fixture")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name):
        return ""
    path = Path(__file__).resolve().parent / "evals" / "fixtures" / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _engine_pack(payload: dict[str, Any]) -> dict[str, Any]:
    pack: dict[str, Any] = {}
    engine = payload.get("engine")
    if isinstance(engine, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,31}", engine):
        skills = Path(__file__).resolve().parents[2]
        for path in sorted(skills.glob(f"{engine}.profile/*/data/*.json")):
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                pack.update(loaded)
    elif isinstance(engine, dict):
        pack.update(engine)
    bands = payload.get("bands")
    if isinstance(bands, dict):
        pack.update(bands)
    return pack


def _visible(html: str) -> tuple[str, int]:
    if not html:
        return "", 0
    tree = HTMLParser(html)
    socials: set[str] = set()
    for link in tree.css("a[href]"):
        href = (link.attributes.get("href") or "").lower()
        for host in _SOCIAL_HOSTS:
            if host in href:
                socials.add(host)
    for node in tree.css("script, style, noscript, svg"):
        node.decompose()
    body = tree.body or tree.root
    text = " ".join(((body.text(separator=" ") if body is not None else "") or "").split())
    return text, len(socials)


def _weight(signals: dict[str, Any], key: str) -> int:
    try:
        return int(signals.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    for word in words:
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.I):
            return True
    return False


def _locations(text: str) -> int:
    found = 0
    for match in _LOCATION.finditer(text):
        raw = match.group(1).lower()
        count = _NUMBER_WORDS.get(raw, int(raw) if raw.isdigit() else 0)
        found = max(found, count)
    return found


def _headcount(text: str) -> int:
    found = 0
    for match in _HEADCOUNT.finditer(text):
        raw = match.group(1) or match.group(2) or "0"
        found = max(found, int(raw))
    return found


def _years(text: str, today: date) -> int:
    match = _SINCE.search(text)
    if match:
        year = int(match.group(1))
        if 1800 <= year <= today.year:
            return today.year - year
    match = _COPY.search(text)
    if match:
        start = int(match.group(1))
        end = int(match.group(2) or today.year)
        if 1800 <= start <= end <= today.year + 1:
            return end - start
    return 0


def _online(text: str, html: str) -> bool:
    if _has_word(text, ("online store", "ecommerce", "e-commerce")) or "add to cart" in text.lower():
        return True
    return bool(html and re.search(r"add to cart", html, re.I))


def _licence_points(payload: dict[str, Any], table: Any) -> int:
    if not isinstance(table, dict):
        return 0
    label = " ".join(str(payload.get("licence_type_label") or "").lower().split())
    code = " ".join(str(payload.get("licence_type") or "").lower().split())
    for key in (label, code, code.zfill(2) if code.isdigit() else ""):
        if key and key in table:
            try:
                return int(table[key])
            except (TypeError, ValueError):
                return 0
    return 0


def _band(points: int, thresholds: Any) -> str | None:
    if not isinstance(thresholds, list):
        return None
    rows: list[tuple[int, str]] = []
    for row in thresholds:
        if not isinstance(row, dict) or not row.get("band"):
            continue
        try:
            rows.append((int(row.get("min", 0)), str(row["band"])))
        except (TypeError, ValueError):
            continue
    for minimum, band in sorted(rows, key=lambda item: item[0], reverse=True):
        if points >= minimum:
            return band
    return None


def _naics(categories: list[Any], table: Any) -> tuple[str | None, str | None]:
    if not isinstance(table, dict):
        return None, None
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    order: list[str] = []
    for category in categories:
        key = " ".join(str(category).lower().split())
        hit = table.get(key)
        if not isinstance(hit, dict):
            for term, row in table.items():
                if re.search(rf"(?<!\w){re.escape(str(term).lower())}(?!\w)", key):
                    hit = row
                    break
        if not isinstance(hit, dict):
            continue
        code = re.sub(r"\D", "", str(hit.get("code") or ""))[:6]
        if len(code) != 6:
            continue
        if code not in counts:
            order.append(code)
            labels[code] = str(hit.get("label") or "")[:60]
            counts[code] = 0
        counts[code] += 1
    if not order:
        return None, None
    best = max(order, key=lambda code: (counts[code], -order.index(code)))
    return best, labels[best] or None


def _model(text: str, categories: list[Any], services: list[Any], online: bool) -> str | None:
    retailish = _has_word(text, ("retail", "shop", "shops", "store", "stores", "boutique"))
    wholesale = _has_word(text, ("wholesale", "distributor")) or "trade only" in text.lower()
    trade_only = "trade only" in text.lower()
    makes = "manufactur" in text.lower()
    serviceish = _has_word(text, ("repair", "repairs", "servicing", "consultation"))
    if makes and not retailish:
        return "manufacturer"
    if makes and retailish:
        return "mixed"
    if trade_only or (wholesale and not retailish):
        return "wholesale"
    if wholesale and retailish:
        return "mixed"
    if online:
        return "online retail"
    if serviceish and not categories and not retailish:
        return "service"
    if retailish or categories:
        return "retail"
    if serviceish or services:
        return "service"
    return None


def _today(payload: dict[str, Any]) -> date:
    raw = payload.get("_today")
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return date.today()
    return date.today()


def _blank(basis: str) -> dict[str, Any]:
    return {
        "size_band": None,
        "revenue_band": None,
        "business_model": None,
        "naics": None,
        "naics_label": None,
        "basis": basis[:200],
        "estimate": True,
        "confidence": 0,
        "fields": {
            "size_band": {"method": "none", "confidence": 0},
            "revenue_band": {"method": "none", "confidence": 0},
            "business_model": {"method": "none", "confidence": 0},
            "naics": {"method": "none", "confidence": 0},
        },
    }


async def run(payload: dict, ctx) -> dict:
    _ = ctx
    description = str(payload.get("description") or "")
    categories = list(payload.get("categories") or [])
    services = list(payload.get("services") or [])
    people = [person for person in (payload.get("people") or []) if isinstance(person, dict) and person.get("name")]
    html = _load_html(payload)
    visible, socials_on_page = _visible(html)
    if not description and not categories and not services and not visible:
        return _blank("Estimate: no signals from the site")

    blob = " ".join(
        part for part in (description, " ".join(map(str, categories)), " ".join(map(str, services)), visible) if part
    )
    pack = _engine_pack(payload)
    signals = pack.get("signal_points") if isinstance(pack.get("signal_points"), dict) else {}
    today = _today(payload)
    locations = _locations(blob)
    if isinstance(payload.get("locations"), int):
        locations = payload["locations"]
    headcount = _headcount(blob)
    years = _years(blob, today)
    online = bool(payload.get("online_store")) or _online(blob, html)
    if "socials_count" in payload and payload.get("socials_count") is not None:
        socials = int(payload.get("socials_count") or 0)
    else:
        socials = socials_on_page
    licence = _licence_points(payload, pack.get("licence_points"))
    service_n = min(len(services), _weight(signals, "max_services") or len(services))
    social_points = min(socials, _weight(signals, "max_social_points") or socials)

    points = 0
    points += locations * _weight(signals, "per_location")
    points += len(people) * _weight(signals, "per_person")
    points += service_n * _weight(signals, "per_service")
    points += (1 if online else 0) * _weight(signals, "online_store")
    points += social_points * _weight(signals, "per_social")
    points += (years // 5) * _weight(signals, "per_five_years")
    points += headcount * _weight(signals, "per_employee")
    points += licence

    size = _band(points, pack.get("size_thresholds"))
    revenue_table = pack.get("revenue_by_size") if isinstance(pack.get("revenue_by_size"), dict) else {}
    revenue = revenue_table.get(size) if size else None
    model = _model(blob, categories, services, online)
    naics, naics_label = _naics(categories, pack.get("naics_by_category"))

    used = sum([
        locations > 0,
        len(people) > 0,
        len(services) > 0,
        online,
        socials > 0,
        years >= 5,
        headcount > 0,
        licence > 0,
    ])
    confidence = 60 if used >= 2 else 40
    basis = (
        f"Estimate from a points score of {points}"
        f" (locations {locations}, people {len(people)}, services {len(services)},"
        f" online {int(online)}, socials {socials}, years {years}, headcount {headcount})."
    )
    return {
        "size_band": size,
        "revenue_band": str(revenue) if revenue else None,
        "business_model": model,
        "naics": naics,
        "naics_label": naics_label,
        "basis": basis[:200],
        "estimate": True,
        "points": points,
        "confidence": min(60, confidence),
        "fields": {
            "size_band": {"method": "points" if size else "none", "confidence": min(60, confidence) if size else 0},
            "revenue_band": {"method": "engine_table" if revenue else "none", "confidence": min(60, confidence) if revenue else 0},
            "business_model": {"method": "rules" if model else "none", "confidence": min(60, confidence) if model else 0},
            "naics": {"method": "engine_table" if naics else "none", "confidence": min(60, confidence) if naics else 0},
        },
    }
