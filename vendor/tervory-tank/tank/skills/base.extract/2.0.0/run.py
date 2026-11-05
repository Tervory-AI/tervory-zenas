"""base.extract 2.0.0 — pass 2c as plain code.

Same facts as 1.0.0: categories, services, a one-line description, named people.
No model call. For each field the first source that has a value wins, in this order:
structured data on the page, then page structure, then the engine vocabulary.
The engine passes {term → category} and {term → service}. This file holds no industry words.
Nothing is invented: an empty field stays empty.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from selectolax.parser import HTMLParser

_METHOD_CONFIDENCE = {
    "jsonld": 90,
    "microdata": 80,
    "opengraph": 75,
    "meta": 70,
    "page": 65,
    "vocabulary": 55,
    "none": 0,
}
_PRODUCT_HEADS = {"shop", "products", "product", "catalog", "catalogue", "store"}
_SERVICE_HEADS = {"services", "service", "our services", "what we do", "what we offer"}
_ABOUT_HEADS = {"about", "about us", "our story", "who we are"}
_TEAM_HEADS = {"team", "our team", "staff", "our staff", "our people", "people", "meet the team"}
_ROLE_PREFIX = re.compile(
    r"(?:^|[.!?]\s+)(Owner|Founder|Co-founder|President|CEO|Manager|Director|Proprietor|Partner)\s+"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b"
)
_NAME_ROLE = re.compile(
    r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)\s*[–—-]\s*([A-Z][A-Za-z]{1,40})\b"
)
_STOCK = re.compile(r"\b(?:we\s+)?(?:stock|sell|carry)\s+([^.]{3,240})", re.I)
_OFFER = re.compile(r"\b(?:we\s+)?(?:offer|provide|speciali[sz]e in)\s+([^.]{3,240})", re.I)
_AND_OFFER = re.compile(r",?\s+and\s+(?:offer|provide|we)\b", re.I)
_JSONLD = re.compile(r"application/ld\+json", re.I)


def _clean(value: Any, width: int = 80) -> str:
    if isinstance(value, list) and value:
        value = value[0]
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:width].strip(" -–—,;:")


def _one_line(value: str) -> str:
    text = " ".join(value.split())
    sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    return sentence[:300]


def _cap(items: list[str], limit: int, width: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _clean(item, width)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _cap_people(items: list[dict[str, str]], limit: int = 5) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        name = _clean(item.get("name"), 80)
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "role": _clean(item.get("role"), 60)})
        if len(out) >= limit:
            break
    return out


def _split_list(blob: str) -> list[str]:
    blob = _AND_OFFER.split(blob, maxsplit=1)[0]
    parts = re.split(r",|\band\b|&|/", blob)
    out: list[str] = []
    for part in parts:
        text = _clean(part, 60)
        text = re.sub(r"^(?:a|an|our|the)\s+", "", text, flags=re.I)
        if not text or text.lower() in {"more", "other", "etc", "products", "services"}:
            continue
        out.append(text)
    return out


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
    vocab = payload.get("vocabulary")
    if isinstance(vocab, dict):
        pack.update(vocab)
    return pack


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _leaves(node: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in _as_list(node.get("@type")):
        out.append(str(item).rstrip("/").split("/")[-1].lower())
    return out


def _kind(leaves: list[str]) -> str:
    for leaf in leaves:
        if leaf == "person":
            return "person"
        if "service" in leaf:
            return "service"
        if leaf == "product" or leaf.endswith("product"):
            return "product"
    return ""


def _walk(node: Any, found: list[dict[str, Any]]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, found)
        return
    if not isinstance(node, dict):
        return
    found.append(node)
    for value in node.values():
        if isinstance(value, (dict, list)):
            _walk(value, found)


def _offers_from(node: dict[str, Any]) -> list[Any]:
    offers: list[Any] = []
    offers.extend(_as_list(node.get("makesOffer")))
    for catalog in _as_list(node.get("hasOfferCatalog")):
        if isinstance(catalog, dict):
            offers.extend(_as_list(catalog.get("itemListElement")))
    return offers


def _from_offer(offer: Any) -> tuple[str, str]:
    if not isinstance(offer, dict):
        return "", ""
    target = offer.get("itemOffered") if isinstance(offer.get("itemOffered"), dict) else offer
    if not isinstance(target, dict):
        return "", ""
    leaves = _leaves(target) + _leaves(offer)
    kind = _kind(leaves)
    if kind == "service" or "service" in leaves:
        return "", _clean(target.get("name") or offer.get("name"), 60)
    category = _clean(target.get("category"), 40)
    if category:
        return category, ""
    if kind == "product":
        return _clean(target.get("name"), 40), ""
    return "", ""


def _people_node(node: dict[str, Any]) -> list[dict[str, str]]:
    people: list[dict[str, str]] = []
    leaves = _leaves(node)
    if _kind(leaves) == "person" or node.get("jobTitle"):
        name = _clean(node.get("name"), 80)
        if name and (_kind(leaves) == "person" or node.get("jobTitle")):
            people.append({"name": name, "role": _clean(node.get("jobTitle"), 60)})
    for key in ("employee", "employees", "founder", "founders"):
        for person in _as_list(node.get(key)):
            if isinstance(person, dict):
                people.extend(_people_node(person))
            elif isinstance(person, str):
                name = _clean(person, 80)
                if name:
                    people.append({"name": name, "role": ""})
    return people


def _hours_node(node: dict[str, Any]) -> list[str]:
    hours: list[str] = []
    for item in _as_list(node.get("openingHours")):
        text = _clean(item, 80) if not isinstance(item, dict) else ""
        if isinstance(item, str) and text:
            hours.append(text)
    for item in _as_list(node.get("openingHoursSpecification")):
        if isinstance(item, str):
            text = _clean(item, 80)
            if text:
                hours.append(text)
        elif isinstance(item, dict):
            bits = [item.get("dayOfWeek"), item.get("opens"), item.get("closes")]
            text = " ".join(str(bit) for bit in bits if bit)
            text = _clean(text, 80)
            if text:
                hours.append(text)
    return hours


def _jsonld(tree: HTMLParser) -> dict[str, Any]:
    categories: list[str] = []
    services: list[str] = []
    people: list[dict[str, str]] = []
    hours: list[str] = []
    for script in tree.css("script"):
        kind = script.attributes.get("type") or ""
        if not _JSONLD.search(kind):
            continue
        try:
            data = json.loads(script.text() or "")
        except json.JSONDecodeError:
            continue
        nodes: list[dict[str, Any]] = []
        _walk(data, nodes)
        for node in nodes:
            for offer in _offers_from(node):
                category, service = _from_offer(offer)
                if category:
                    categories.append(category)
                if service:
                    services.append(service)
            kind_name = _kind(_leaves(node))
            if kind_name == "product":
                category, service = _from_offer(node)
                if category:
                    categories.append(category)
                if service:
                    services.append(service)
            elif kind_name == "service":
                service = _clean(node.get("name"), 60)
                if service:
                    services.append(service)
            people.extend(_people_node(node))
            hours.extend(_hours_node(node))
    return {
        "categories": _cap(categories, 6, 40),
        "services": _cap(services, 6, 60),
        "people": _cap_people(people),
        "opening_hours": _cap(hours, 8, 80),
    }


def _direct_props(el: Any) -> dict[str, list[str]]:
    props: dict[str, list[str]] = {}
    for child in el.css("[itemprop]"):
        if "itemscope" in child.attributes:
            continue
        parent = child.parent
        nested = False
        while parent is not None and parent != el:
            if "itemscope" in (parent.attributes or {}):
                nested = True
                break
            parent = parent.parent
        if nested:
            continue
        key = child.attributes.get("itemprop") or ""
        if not key:
            continue
        value = child.attributes.get("content") or " ".join((child.text() or "").split())
        value = _clean(value, 80)
        if value:
            props.setdefault(key, []).append(value)
    return props


def _microdata(tree: HTMLParser) -> dict[str, Any]:
    categories: list[str] = []
    services: list[str] = []
    people: list[dict[str, str]] = []
    hours: list[str] = []
    for el in tree.css("[itemscope]"):
        kind = _kind([str(el.attributes.get("itemtype") or "").rstrip("/").split("/")[-1].lower()])
        props = _direct_props(el)
        if kind == "person":
            name = (props.get("name") or [""])[0]
            role = (props.get("jobTitle") or props.get("role") or [""])[0]
            if name:
                people.append({"name": name, "role": role})
        elif kind == "service":
            name = (props.get("name") or [""])[0]
            if name:
                services.append(name)
        elif kind == "product":
            name = (props.get("category") or props.get("name") or [""])[0]
            if name:
                categories.append(name)
        else:
            hours.extend(props.get("openingHours") or [])
    return {
        "categories": _cap(categories, 6, 40),
        "services": _cap(services, 6, 60),
        "people": _cap_people(people),
        "opening_hours": _cap(hours, 8, 80),
    }


def _meta_description(tree: HTMLParser) -> tuple[str, str]:
    og = tree.css_first('meta[property="og:description"]')
    if og is not None:
        text = _one_line(og.attributes.get("content") or "")
        if text:
            return text, "opengraph"
    meta = tree.css_first('meta[name="description"]')
    if meta is not None:
        text = _one_line(meta.attributes.get("content") or "")
        if text:
            return text, "meta"
    return "", ""


def _heading_title(node: Any) -> str:
    return " ".join((node.text() or "").split()).lower()


def _after_heading(heading: Any) -> list[Any]:
    nodes: list[Any] = []
    sib = heading.next
    while sib is not None:
        tag = (sib.tag or "").lower()
        if tag in {"h1", "h2", "h3", "h4"}:
            break
        nodes.append(sib)
        sib = sib.next
    return nodes


def _group_label(item: Any) -> str:
    sub = item.css_first("ul")
    for link in item.css("a"):
        parent = link.parent
        inside = False
        while parent is not None and parent != item:
            if parent == sub:
                inside = True
                break
            parent = parent.parent
        if not inside:
            return " ".join((link.text() or "").split()).lower()
    return ""


def _nav(tree: HTMLParser) -> tuple[list[str], list[str]]:
    categories: list[str] = []
    services: list[str] = []
    for item in tree.css("nav li"):
        sub = item.css_first("ul")
        if sub is None:
            continue
        label = _group_label(item)
        labels = [" ".join((link.text() or "").split()) for link in sub.css("a")]
        labels = [label_text for label_text in labels if label_text]
        if label in _PRODUCT_HEADS:
            categories.extend(labels)
        elif label in _SERVICE_HEADS:
            services.extend(labels)
    return _cap(categories, 6, 40), _cap(services, 6, 60)


def _section_items(tree: HTMLParser, titles: set[str]) -> list[str]:
    items: list[str] = []
    for heading in tree.css("h1, h2, h3, h4"):
        title = _heading_title(heading)
        if title not in titles and not any(title.startswith(name + " ") for name in titles):
            continue
        for node in _after_heading(heading):
            tag = (node.tag or "").lower()
            if tag in {"ul", "ol"}:
                for li in node.css("li"):
                    text = " ".join((li.text() or "").split())
                    if text:
                        items.append(text)
            elif tag in {"p", "div"}:
                text = " ".join((node.text() or "").split())
                if text:
                    items.extend(_split_list(text))
    return items


def _about_sentence(tree: HTMLParser) -> str:
    for heading in tree.css("h1, h2, h3, h4"):
        title = _heading_title(heading)
        if title not in _ABOUT_HEADS and not title.startswith("about"):
            continue
        for node in _after_heading(heading):
            text = " ".join((node.text() or "").split())
            if text:
                return _one_line(text)
    return ""


def _team_people(tree: HTMLParser) -> list[dict[str, str]]:
    people: list[dict[str, str]] = []
    for heading in tree.css("h1, h2, h3"):
        title = _heading_title(heading)
        if title not in _TEAM_HEADS and not title.startswith("meet "):
            continue
        parent = heading.parent
        if parent is None:
            continue
        for card in parent.css("h3, h4"):
            if card == heading:
                continue
            name = " ".join((card.text() or "").split())
            role = ""
            sib = card.next
            while sib is not None and (sib.tag or "").lower() in {"", "-text"}:
                sib = sib.next
            if sib is not None and (sib.tag or "").lower() in {"p", "span", "div"}:
                role = " ".join((sib.text() or "").split())
            if name:
                people.append({"name": name, "role": role})
    return people


def _people_from_text(text: str) -> list[dict[str, str]]:
    people: list[dict[str, str]] = []
    for match in _ROLE_PREFIX.finditer(text):
        people.append({"name": match.group(2), "role": match.group(1)})
    for match in _NAME_ROLE.finditer(text):
        people.append({"name": match.group(1), "role": match.group(2).strip(" .")})
    return people


def _match_terms(text: str, table: Any) -> list[str]:
    if not isinstance(table, dict) or not table:
        return []
    lowered = text.lower()
    occupied = [False] * len(lowered)
    found: list[str] = []
    seen: set[str] = set()
    for term in sorted(table, key=lambda item: len(str(item)), reverse=True):
        needle = " ".join(str(term).lower().split())
        if not needle:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)")
        for match in pattern.finditer(lowered):
            if any(occupied[match.start():match.end()]):
                continue
            for index in range(match.start(), match.end()):
                occupied[index] = True
            label = _clean(table.get(term), 60)
            key = label.lower()
            if label and key not in seen:
                seen.add(key)
                found.append(label)
    return found


def _visible(tree: HTMLParser) -> str:
    for node in tree.css("script, style, noscript, svg"):
        node.decompose()
    body = tree.body or tree.root
    if body is None:
        return ""
    return " ".join((body.text(separator=" ") or "").split())


def _empty() -> dict[str, Any]:
    return {
        "categories": [],
        "services": [],
        "description": "",
        "people": [],
        "opening_hours": [],
        "confidence": 0,
        "fields": {
            "categories": {"method": "none", "confidence": 0},
            "services": {"method": "none", "confidence": 0},
            "description": {"method": "none", "confidence": 0},
            "people": {"method": "none", "confidence": 0},
        },
    }


def _finish(slots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    scores: list[int] = []
    for name in ("categories", "services", "description", "people"):
        method = str(slots[name]["method"])
        score = _METHOD_CONFIDENCE.get(method, 0)
        if method == "none":
            score = 0
        fields[name] = {"method": method, "confidence": score}
        if method != "none":
            scores.append(score)
    confidence = int(round(sum(scores) / len(scores))) if scores else 0
    return {
        "categories": slots["categories"]["value"],
        "services": slots["services"]["value"],
        "description": slots["description"]["value"],
        "people": slots["people"]["value"],
        "opening_hours": slots["opening_hours"]["value"],
        "confidence": confidence,
        "fields": fields,
    }


async def run(payload: dict, ctx) -> dict:
    _ = ctx
    html = _load_html(payload)
    text = str(payload.get("text") or "")
    tree = HTMLParser(html) if html else None
    structured = _jsonld(tree) if tree is not None else {"categories": [], "services": [], "people": [], "opening_hours": []}
    micro = _microdata(tree) if tree is not None else {"categories": [], "services": [], "people": [], "opening_hours": []}
    description, description_method = _meta_description(tree) if tree is not None else ("", "")
    nav_categories, nav_services = _nav(tree) if tree is not None else ([], [])
    about = _about_sentence(tree) if tree is not None else ""
    section_services = _section_items(tree, _SERVICE_HEADS) if tree is not None else []
    team = _team_people(tree) if tree is not None else []
    visible = _visible(tree) if tree is not None else ""
    blob = " ".join(part for part in (text, visible) if part)
    if len(blob) < 40 and not html:
        return _empty()

    slots: dict[str, dict[str, Any]] = {
        "categories": {"value": [], "method": "none"},
        "services": {"value": [], "method": "none"},
        "description": {"value": "", "method": "none"},
        "people": {"value": [], "method": "none"},
        "opening_hours": {"value": [], "method": "none"},
    }

    def put(field: str, value: Any, method: str) -> None:
        if slots[field]["method"] != "none":
            return
        if field == "description":
            text_value = _one_line(str(value or ""))
            if text_value:
                slots[field] = {"value": text_value, "method": method}
            return
        if field == "people":
            people = _cap_people(list(value or []))
            if people:
                slots[field] = {"value": people, "method": method}
            return
        items = _cap(list(value or []), 8 if field == "opening_hours" else 6, 80 if field == "opening_hours" else (40 if field == "categories" else 60))
        if items:
            slots[field] = {"value": items, "method": method}

    put("categories", structured["categories"], "jsonld")
    put("services", structured["services"], "jsonld")
    put("people", structured["people"], "jsonld")
    put("opening_hours", structured["opening_hours"], "jsonld")
    put("categories", micro["categories"], "microdata")
    put("services", micro["services"], "microdata")
    put("people", micro["people"], "microdata")
    put("opening_hours", micro["opening_hours"], "microdata")
    if description:
        put("description", description, description_method)

    put("categories", nav_categories, "page")
    put("services", nav_services or section_services, "page")
    put("description", about, "page")
    for match in _STOCK.finditer(blob):
        put("categories", _split_list(match.group(1)), "page")
    for match in _OFFER.finditer(blob):
        put("services", _split_list(match.group(1)), "page")
    # Name–role lines and team cards are one page-structure source.
    put("people", [*team, *_people_from_text(blob)], "page")

    pack = _engine_pack(payload)
    put("categories", _match_terms(blob, pack.get("categories")), "vocabulary")
    put("services", _match_terms(blob, pack.get("services")), "vocabulary")
    return _finish(slots)
