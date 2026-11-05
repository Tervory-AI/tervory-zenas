"""Read a price only from the layout Tiger already learned.

A later page that no longer has that layout answers "can't read". A currency
amount sitting somewhere else on the page is not used. Two different amounts
on the product, with no price the reader can point at, are "can't read".
"""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Any

CANT_READ = "can't read"
STRUCTURED = ("jsonld", "microdata", "opengraph")

_MONEY = re.compile(
    r"(?P<sym>Rs\.?|US\$|\$|€|£)\s?(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)"
    r"|(?P<code>USD|EUR|GBP|CAD|AUD|INR)\s?(?P<code_amount>\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)",
)
_SKIP_TAGS = {"script", "style", "noscript"}
_CONTAINER_HINTS = (
    "product_main",
    "product-information",
    "product-details",
    "product_title",
)


def learn_layout(html: str, extracted: dict[str, Any] | None) -> dict[str, Any]:
    """The marker a later visit must still have. Empty when nothing is trustworthy."""
    method = str((extracted or {}).get("method") or "")
    price = _first_price(extracted)
    if method in STRUCTURED and price:
        return {"primary": method}
    if not html or not html.strip():
        return {"primary": None}
    # The reader already refused two amounts. Do not pick one of them.
    if not price and len(_price_class_amounts(html)) > 1:
        return {"primary": None}
    found = _dom_marker(html, price)
    if found is None:
        return {"primary": None}
    return {"primary": "dom", "dom": found[0]}


def read_page(
    html: str,
    signature: dict[str, Any] | None,
    extracted: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return the known price, or "can't read" with no price."""
    layout = signature if signature else learn_layout(html, extracted)
    if not isinstance(html, str) or not html.strip():
        return _cant(layout, "empty")
    primary = str((layout or {}).get("primary") or "")
    if primary in STRUCTURED:
        if str((extracted or {}).get("method") or "") != primary:
            return _cant(layout, "layout")
        price = _first_price(extracted)
        if not price:
            return _cant(layout, "missing-price")
        return _read(layout, price, primary)
    if primary == "dom":
        price = _price_in_dom(html, str(layout.get("dom_hash") or layout.get("dom") or ""),
                              _first_price(extracted), hashed=bool(layout.get("dom_hash")))
        if not price:
            return _cant(layout, "layout")
        return _read(layout, price, "dom")
    return _cant(layout, "no-layout")


def layout_from_evidence(evidence: Any) -> dict[str, Any] | None:
    """Pull a saved layout marker out of an observation note."""
    if not isinstance(evidence, str) or not evidence.startswith("{"):
        return None
    import json

    try:
        data = json.loads(evidence)
    except json.JSONDecodeError:
        return None
    layout = data.get("layout") if isinstance(data, dict) else None
    if isinstance(layout, dict) and layout.get("primary"):
        return layout
    return None


def _read(layout: dict[str, Any], price: str, method: str) -> dict[str, Any]:
    return {"status": "read", "price": price, "method": method, "layout": layout, "reason": "read"}


def _cant(layout: dict[str, Any] | None, reason: str) -> dict[str, Any]:
    return {"status": CANT_READ, "price": None, "method": None, "layout": layout, "reason": reason}


def _first_price(extracted: dict[str, Any] | None) -> str | None:
    if not extracted:
        return None
    for item in extracted.get("items") or []:
        price = item.get("price")
        if isinstance(price, str) and price:
            return price
    return None


def _money_in(text: str) -> str | None:
    match = _MONEY.search(text or "")
    if match is None:
        return None
    amount = match.group("amount") or match.group("code_amount")
    return _canonical(amount)


def _canonical(raw: str) -> str | None:
    text = raw.strip().replace(" ", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", text):
            text = text.replace(",", "")
        elif re.fullmatch(r"\d+,\d{2}", text):
            text = text.replace(",", ".")
        else:
            return None
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return None
    whole, _, frac = text.partition(".")
    if frac:
        frac = (frac + "00")[:2]
        return f"{int(whole)}.{frac}"
    return f"{int(whole)}.00"


class _Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.stack: list[dict[str, Any]] = []
        self.nodes: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if self.skip or name in _SKIP_TAGS:
            if name in _SKIP_TAGS:
                self.skip += 1
            return
        attr = {key.lower(): value or "" for key, value in attrs}
        classes = [item for item in attr.get("class", "").split() if item]
        self.stack.append({
            "tag": name,
            "classes": classes,
            "text": "",
            "ancestors": [
                {"tag": item["tag"], "classes": list(item["classes"])}
                for item in self.stack
            ],
        })

    def handle_data(self, data: str) -> None:
        if self.skip or not self.stack or not data:
            return
        node = self.stack[-1]
        if len(node["text"]) < 240:
            node["text"] = (node["text"] + data)[:240]

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in _SKIP_TAGS and self.skip:
            self.skip -= 1
            return
        if self.skip or not self.stack:
            return
        index = None
        for cursor in range(len(self.stack) - 1, -1, -1):
            if self.stack[cursor]["tag"] == name:
                index = cursor
                break
        if index is None:
            return
        node = self.stack.pop(index)
        if self.stack and index == len(self.stack):
            parent = self.stack[-1]
            if len(parent["text"]) < 240:
                parent["text"] = (parent["text"] + node["text"])[:240]
        money = _money_in(node["text"]) if len(node["text"]) <= 180 else None
        if money:
            self.nodes.append({
                "tag": node["tag"],
                "classes": node["classes"],
                "money": money,
                "ancestors": node["ancestors"],
            })


def _dom_marker(html: str, hint: str | None) -> tuple[str, str] | None:
    scan = _Scan()
    try:
        scan.feed(html)
        scan.close()
    except Exception:  # noqa: BLE001 — a broken page is not a layout
        return None
    priced = [node for node in scan.nodes if _price_class(node["classes"])]
    pool = priced or scan.nodes
    if hint:
        matched = [node for node in pool if node["money"] == hint]
        if matched:
            pool = matched
    if not pool:
        return None
    chosen = _prefer(pool)
    selector = _selector(chosen)
    if not selector:
        return None
    return selector, chosen["money"]


def _price_class_amounts(html: str) -> set[str]:
    """Distinct amounts in price elements. One amount repeated is still one price."""
    scan = _Scan()
    try:
        scan.feed(html)
        scan.close()
    except Exception:  # noqa: BLE001 — broken markup is not two prices
        return set()
    found: set[str] = set()
    for node in scan.nodes:
        if _price_class(node["classes"]):
            found.add(node["money"])
    return found


def _price_class(classes: list[str]) -> str | None:
    for name in classes:
        if "price" in name.lower():
            return name
    return None


def _container(ancestors: list[dict[str, Any]]) -> tuple[str, str] | None:
    nearest: tuple[str, str] | None = None
    for ancestor in ancestors:
        for name in ancestor["classes"]:
            lowered = name.lower()
            if any(hint in lowered for hint in _CONTAINER_HINTS):
                return ancestor["tag"], name
            if nearest is None and name:
                nearest = ancestor["tag"], name
    if nearest is not None:
        return nearest
    for ancestor in reversed(ancestors):
        if ancestor["tag"] not in {"html", "body"}:
            return ancestor["tag"], ""
    return None


def _prefer(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    def rank(node: dict[str, Any]) -> tuple[int, int]:
        container = _container(node["ancestors"])
        specific = 0 if container and any(hint in container[1].lower() for hint in _CONTAINER_HINTS) else 1
        priced = 0 if _price_class(node["classes"]) else 1
        return specific, priced

    return min(nodes, key=rank)


def _selector(node: dict[str, Any]) -> str | None:
    price_class = _price_class(node["classes"])
    target = f"{node['tag']}.{price_class}" if price_class else node["tag"]
    container = _container(node["ancestors"])
    if container is None:
        return f"tag:{target}"
    tag, class_name = container
    scope = f"{tag}.{class_name}" if class_name else tag
    return f"scope:{scope}|{target}"


def _split_selector(selector: str) -> tuple[str, str, str, str]:
    """scope tag, scope class, target tag, target class."""
    scope, _, target = selector.partition("|")
    if selector.startswith("scope:"):
        scope = scope[len("scope:"):]
    elif selector.startswith("tag:"):
        target = selector[len("tag:"):]
        scope = ""
    scope_tag, _, scope_class = scope.partition(".")
    target_tag, _, target_class = target.partition(".")
    return scope_tag, scope_class, target_tag, target_class


def fingerprint_selector(selector: str) -> str:
    """Same selector grammar, with opaque tag/class tokens instead of raw values."""
    scope_tag, scope_class, target_tag, target_class = _split_selector(selector)
    def part(tag: str, css_class: str) -> str:
        result = _token_hash(tag) if tag else ''
        return result + ('.' + _token_hash(css_class) if css_class else '')
    target = part(target_tag, target_class)
    return f'scope:{part(scope_tag, scope_class)}|{target}' if scope_tag else f'tag:{target}'


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _price_in_dom(html: str, selector: str, hint: str | None = None, *, hashed: bool = False) -> str | None:
    if not selector:
        return None
    scope_tag, scope_class, target_tag, target_class = _split_selector(selector)
    found: list[str] = []

    class Finder(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.skip = 0
            self.stack: list[dict[str, Any]] = []
            self.scope_depth: int | None = None

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            name = tag.lower()
            if self.skip or name in _SKIP_TAGS:
                if name in _SKIP_TAGS:
                    self.skip += 1
                return
            attr = {key.lower(): value or "" for key, value in attrs}
            classes = attr.get("class", "").split()
            if hashed:
                classes = [_token_hash(value) for value in classes]
            self.stack.append({"tag": name, "classes": classes, "text": ""})
            compared_tag = _token_hash(name) if hashed else name
            if self.scope_depth is None and compared_tag == scope_tag and (not scope_class or scope_class in classes):
                self.scope_depth = len(self.stack)

        def handle_data(self, data: str) -> None:
            if self.skip or not self.stack or not data:
                return
            node = self.stack[-1]
            if len(node["text"]) < 240:
                node["text"] = (node["text"] + data)[:240]

        def handle_endtag(self, tag: str) -> None:
            name = tag.lower()
            if name in _SKIP_TAGS and self.skip:
                self.skip -= 1
                return
            if self.skip or not self.stack:
                return
            node = self.stack.pop()
            if self.stack and len(node["text"]) and len(self.stack[-1]["text"]) < 240:
                self.stack[-1]["text"] = (self.stack[-1]["text"] + node["text"])[:240]
            inside = self.scope_depth is not None and len(self.stack) + 1 >= self.scope_depth
            if (
                inside
                and (_token_hash(name) if hashed else name) == target_tag
                and (not target_class or target_class in node["classes"])
                and len(node["text"]) <= 180
            ):
                money = _money_in(node["text"])
                if money and money not in found:
                    found.append(money)
            if self.scope_depth is not None and len(self.stack) < self.scope_depth:
                self.scope_depth = None

    finder = Finder()
    try:
        finder.feed(html)
        finder.close()
    except Exception:  # noqa: BLE001 — broken markup is not a price
        return None
    if len(found) == 1:
        return found[0]
    # Two amounts in the learned spot. Use the price the reader pointed at.
    # Otherwise this is a guess, and the page cannot be read.
    if hint and hint in found:
        return hint
    return None
