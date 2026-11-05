"""Keep independent scrape evidence separate from the merged serving profile."""

from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from typing import Any

from base_gold.gold_sources import _host


class Addresses(HTMLParser):
    def __init__(self):
        super().__init__()
        self.script = False
        self.parts = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        self.script = tag == "script" and dict(attrs).get("type") == "application/ld+json"
        if self.script:
            self.parts = []

    def handle_data(self, data):
        if self.script:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.script:
            self.script = False
            try:
                self.visit(json.loads("".join(self.parts)))
            except (ValueError, RecursionError):
                pass

    def visit(self, value):
        if isinstance(value, list):
            for item in value:
                self.visit(item)
        elif isinstance(value, dict):
            if isinstance(value.get("streetAddress"), str):
                self.rows.append({
                    "address": value["streetAddress"], "city": value.get("addressLocality", ""),
                    "state": value.get("addressRegion", ""), "zip": value.get("postalCode", ""),
                })
            for item in value.values():
                if isinstance(item, (dict, list)):
                    self.visit(item)


def observation(website: str, scraped: dict[str, Any]) -> dict[str, Any] | None:
    try:
        host = _host(website)
        pages = scraped.get("pages") or []
        text = scraped.get("text") or ""
        if (
            not host or scraped.get("error") or not pages or not text
            or any(_host(page) != host for page in pages)
            or any(word in text.casefold() for word in ("captcha", "verify you are human", "access denied"))
        ):
            return None
        addresses = Addresses()
        addresses.feed(scraped.get("html") or "")
        return {
            "website": website, "source_url": pages[0],
            "observed_on": datetime.now(timezone.utc).date().isoformat(),
            "status": 200, "blocked": False, "text": text[:6000],
            "phones": [str(phone) for phone in scraped.get("phones", [])][:30],
            "addresses": addresses.rows[:30],
        }
    except (ValueError, TypeError, AttributeError):
        return None
