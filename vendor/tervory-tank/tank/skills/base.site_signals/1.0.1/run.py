"""Passive site signals from already fetched HTML and selected response headers. No I/O."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

FINGERPRINTS = json.loads((Path(__file__).parent / "data/fingerprints.json").read_text())
DATE = re.compile(r"\b(20\d{2}|19\d{2})-(\d{2})-(\d{2})(?:T[^\s<\"']*)?")
COPYRIGHT = re.compile(r"(?:©|\bcopyright\b|\(c\))[^\n]{0,100}", re.I)
BUY = re.compile(r"\b(?:add to (?:cart|bag|basket)|buy now|checkout)\b", re.I)
BOOK = re.compile(r"\b(?:book (?:now|online|an? appointment)|schedule (?:an? appointment|a visit)|request (?:a )?quote|get (?:a )?quote)\b", re.I)


def _match(pattern, text):
    expression = pattern.get("re", "") if isinstance(pattern, dict) else pattern
    if not isinstance(expression, str) or not expression:
        return None
    try:
        return re.search(expression, text, re.I)
    except re.error:
        return None  # Unsupported upstream JS regexes never become detections.


def _date(value, today):
    try:
        found = DATE.search(str(value))
        result = date(*map(int, found.groups())) if found else parsedate_to_datetime(str(value)).date()
        return result.isoformat() if date(1990, 1, 1) <= result <= today else None
    except (ValueError, TypeError, OverflowError):
        return None


def _patterns(value):
    return value if isinstance(value, list) else [value]


def _detect(tree, html, headers):
    scripts = [n.attributes.get("src", "") for n in tree.css("script[src]")]
    meta = {n.attributes.get("name", "").lower(): n.attributes.get("content", "") for n in tree.css("meta[name][content]")}
    detected = []
    for fp in FINGERPRINTS:
        evidence = []
        for pattern in _patterns(fp.get("html", [])):
            match = _match(pattern, html)
            if match:
                evidence.append({"kind": "html", "text": match.group(0)[:300]})
        for pattern in _patterns(fp.get("scriptSrc", [])):
            for src in scripts:
                if _match(pattern, src):
                    evidence.append({"kind": "scriptSrc", "text": src[:300]})
        for key, pattern in fp.get("meta", {}).items():
            value = meta.get(key.lower())
            if value is not None and (pattern == "" or _match(pattern, value)):
                evidence.append({"kind": "meta", "text": f'{key}: {value}'[:300]})
        for key, pattern in fp.get("headers", {}).items():
            value = headers.get(key.lower())
            if value is not None and (pattern == "" or _match(pattern, value)):
                evidence.append({"kind": "header", "text": f'{key}: {value}'[:300]})
        if evidence:
            detected.append({"name": fp["name"], "categories": fp["categories"], "evidence": evidence,
                             "confidence": 90 if any(e["kind"] in {"meta", "header", "scriptSrc"} for e in evidence) else 75})
    return detected


async def run(payload: dict, ctx) -> dict:
    if payload.get("archived"):
        return {"fields": {}}
    today = date.fromisoformat(str(payload["as_of"])[:10]) if payload.get("as_of") else datetime.now(timezone.utc).date()
    website = str(payload.get("website") or "")
    pages = payload.get("page_responses") or [{"source_url": website, "html": payload.get("html") or "", "headers": payload.get("headers") or {}}]
    technologies = {}
    dates = {"copyright_year": [], "last_modified": [], "newest_post": []}
    selling = []
    negative = []
    for page in pages:
        html = str(page.get("html") or "")[:400 * 1024]
        source = str(page.get("source_url") or website)
        headers = {str(k).lower(): str(v) for k, v in (page.get("headers") or {}).items()}
        tree = HTMLParser(html)
        for tech in _detect(tree, html, headers):
            for item in tech["evidence"]:
                item["source_url"] = source
            if tech["name"] in technologies:
                technologies[tech["name"]]["evidence"].extend(tech["evidence"])
            else:
                technologies[tech["name"]] = tech
        for node in tree.css("script[type='application/ld+json']"):
            try:
                data = json.loads(node.text())
            except (ValueError, TypeError):
                continue
            def visit(value):
                if isinstance(value, list):
                    for item in value:
                        visit(item)
                elif isinstance(value, dict):
                    kinds = value.get("@type", [])
                    kinds = [kinds] if isinstance(kinds, str) else kinds
                    if any(k in {"Article", "NewsArticle", "BlogPosting"} for k in kinds):
                        for key in ("datePublished", "dateModified"):
                            parsed = _date(value.get(key), today)
                            if parsed:
                                dates["newest_post"].append((parsed, f'{key}: {value[key]}', source))
                    for item in value.values():
                        if isinstance(item, (dict, list)):
                            visit(item)
            visit(data)
        for node in tree.css("article time[datetime], [itemtype*='BlogPosting'] time[datetime], [itemtype*='NewsArticle'] time[datetime]"):
            raw = node.attributes["datetime"]
            parsed = _date(raw, today)
            if parsed:
                dates["newest_post"].append((parsed, node.html[:500], source))
        raw = headers.get("last-modified")
        parsed = _date(raw, today)
        if parsed:
            dates["last_modified"].append((parsed, f'Last-Modified: {raw}', source))
        for node in tree.css("a[href], button, input[type='submit']"):
            label = node.text() or node.attributes.get("value", "")
            if BUY.search(label):
                selling.append({"text": node.html[:500], "source_url": source})
            if BOOK.search(label) and node.attributes.get("href"):
                target = urljoin(source, node.attributes["href"])
                host = urlparse(target).hostname
                # Preserve a directly observed tool URL, without guessing its vendor.
                if host and host != urlparse(source).hostname:
                    technologies.setdefault(target, {"name": target, "categories": ["booking_quote"], "confidence": 80,
                                                     "evidence": [{"kind": "link", "text": node.html[:500], "source_url": source}]})
        for node in tree.css("script, style, noscript"):
            node.decompose()
        text = tree.text(separator="\n")
        for match in COPYRIGHT.finditer(text):
            years = [int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", match.group()) if int(y) <= today.year]
            if years:
                dates["copyright_year"].append((max(years), match.group(), source))
        for match in re.finditer(r"\b(?:we do not sell online|no online (?:sales|ordering)|in[- ]store (?:purchases|sales) only)\b", text, re.I):
            negative.append({"text": match.group(), "source_url": source})
    observed_dates = {str(page.get("source_url") or website): page.get("checked_at") for page in pages}
    for observed in payload.get("_base_observations") or []:
        observed_dates.setdefault(str(observed.get("source_url")), observed.get("checked_at"))
    for tech in technologies.values():
        for item in tech["evidence"]:
            item["checked_at"] = observed_dates.get(item["source_url"])
    fields = {}
    def field(value, evidence, confidence=90):
        evidence = [{**item, "checked_at": observed_dates.get(item["source_url"])} for item in evidence]
        return {"value": value, "checked_at": evidence[0].get("checked_at"), "source": "site:code", "source_kind": "website", "source_url": evidence[0]["source_url"],
                "confidence": confidence, "evidence": evidence}
    if technologies:
        items = sorted(technologies.values(), key=lambda t: t["name"])
        fields["technologies"] = field(items, [e for item in items for e in item["evidence"]], min(t["confidence"] for t in items))
    if selling or negative:
        # Conflicting recorded statements stay unknown instead of inventing a boolean.
        fields["sells_online"] = field(None if selling and negative else bool(selling), selling + negative, 50 if selling and negative else 90)
    freshness = {}
    evidence = []
    for key, candidates in dates.items():
        if candidates:
            value, text, source = max(candidates, key=lambda item: item[0])
            item = {"text": text, "source_url": source}
            freshness[key] = {"value": value, "evidence": item}
            evidence.append(item)
    if freshness:
        fields["site_freshness"] = field(freshness, evidence, 80)
        newest = max(int(str(item["value"])[:4]) for item in freshness.values())
        fields["site_newest_year"] = field(newest, [item["evidence"] for item in freshness.values() if int(str(item["value"])[:4]) == newest], 80)
    return {"fields": fields}
