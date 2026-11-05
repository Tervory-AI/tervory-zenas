"""Bounded public RSS/Atom, Comtrade preview and SearXNG response adapters.

The parser receives saved bytes/text, never a URL to open. Live requests use
the existing polite client; SearXNG is reached through Tank, never a model.
"""
from __future__ import annotations

import calendar
import ipaddress
import json
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import feedparser

from feeds.models import SourceSpec
from feeds.store import Store, utc_now

MAX_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 100


def public_link(value: str) -> str:
    """Keep useful public query parameters, but never credential-bearing URLs."""
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError("url not allowed")
    parts = urlsplit(value.strip())
    host = parts.hostname or ""
    if parts.scheme not in {"http", "https"} or "." not in host or parts.username or parts.password:
        raise ValueError("url not allowed")
    if host.rstrip(".").lower() in {"localhost", "metadata.google.internal"}:
        raise ValueError("url not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("url not allowed")
    if parts.port not in {None, 80, 443}:
        raise ValueError("url not allowed")
    for name, _ in parse_qsl(parts.query, keep_blank_values=True):
        if any(word in name.lower() for word in ("key", "token", "secret", "password", "signature", "auth", "credential")):
            raise ValueError("url not allowed")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def check_feed_url(value: str) -> str:
    from chassis.public_urls import check_url
    return check_url(public_link(value))


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _text(value: str, limit: int) -> str:
    parser = _Text()
    parser.feed(str(value)[:limit * 4])
    return " ".join(" ".join(parser.parts).split())[:limit]


def _event(store, source, *, title, body, url, date, kind):
    title = _text(title, 500)
    if not title:
        return 0
    try:
        url = public_link(url)
    except ValueError:
        return 0
    return int(store.put_event(source.id, kind, "", date, title, _text(body, 4000), url))


def run_rss(source: SourceSpec, reader, store: Store, *, backfill: bool) -> int:
    total = 0
    seen = set()
    for filename in source.config["files"]:
        raw = reader.read_bytes(filename)
        if len(raw) > MAX_BYTES or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise ValueError("invalid feed")
        parsed = feedparser.parse(raw, response_headers={"content-location": source.url, "content-type": "application/xml"})
        if not parsed.version or parsed.bozo:
            raise ValueError("invalid feed")
        for entry in parsed.entries[:MAX_ITEMS]:
            link = urljoin(source.url, entry.get("link", "")) if entry.get("link") else ""
            if link in seen:
                continue
            seen.add(link)
            stamp = entry.get("published_parsed") or entry.get("updated_parsed")
            date = datetime.fromtimestamp(calendar.timegm(stamp), timezone.utc).date().isoformat() if stamp else utc_now()[:10]
            total += _event(store, source, title=entry.get("title", ""), body=entry.get("summary", ""),
                            url=link, date=date, kind="feed_item")
    return total


def run_searxng(source: SourceSpec, reader, store: Store, *, backfill: bool) -> int:
    total = 0
    seen = set()
    for filename in source.config["files"]:
        payload = json.loads(reader.read_text(filename))
        for item in payload["results"][:MAX_ITEMS]:
            url = item.get("url", "")
            if url in seen:
                continue
            seen.add(url)
            total += _event(store, source, title=item.get("title", ""), body=item.get("content", ""),
                            url=url, date=utc_now()[:10], kind="search_result")
    return total


def comtrade_url(source: SourceSpec, *, period: str | None = None) -> str:
    # One bounded preview request, not a reconstruction of the full dataset.
    query = {"period": period or str(datetime.now(timezone.utc).year - 1), "reporterCode": source.config["reporter"],
             "cmdCode": ",".join(source.config["codes"]), "flowCode": "M,X", "partnerCode": "0",
             "partner2Code": "0", "customsCode": "C00", "motCode": "0", "maxRecords": "500",
             "breakdownMode": "classic", "includeDesc": "true"}
    return source.url + "?" + urlencode(query)


def run_comtrade(source: SourceSpec, reader, store: Store, *, backfill: bool) -> int:
    total = 0
    for filename in source.config["files"]:
        payload = json.loads(reader.read_text(filename))
        rows = payload["data"]
        if not isinstance(rows, list) or len(rows) > 500:
            raise ValueError("invalid preview")
        for row in rows:
            if str(row["reporterCode"]) != str(source.config["reporter"]) or str(row["cmdCode"]) not in source.config["codes"]:
                raise ValueError("unexpected trade series")
            if str(row["partnerCode"]) != "0" or row["flowCode"] not in {"M", "X"}:
                raise ValueError("unexpected trade series")
            value = Decimal(str(row["primaryValue"]))
            if not value.is_finite() or value < 0:
                raise ValueError("invalid trade value")
            year = int(row["period"])
            date = datetime(year, 1, 1).date().isoformat()
            ref = f"{row['reporterCode']}:{row['cmdCode']}:{row['flowCode']}"
            title = f"{row['cmdDesc']} — {row['flowDesc']}"
            series = store.ensure_series(source.id, ref, title, "USD", "national", row["reporterDesc"], "annual")
            total += int(store.put_point(series, date, value, str(row.get("lastReleased") or year)))
            if store._staged:
                store._staged[-1]["url"] = comtrade_url(source, period=str(year))
    return total


def fetch_rss(source, client, store, *, backfill):
    from feeds.fetch import MemoryReader
    check_feed_url(source.url)
    return run_rss(source, MemoryReader(client.get(source.url)), store, backfill=backfill)


def fetch_comtrade(source, client, store, *, backfill):
    from feeds.fetch import MemoryReader
    return run_comtrade(source, MemoryReader(client.get_text(comtrade_url(source))), store, backfill=backfill)


def fetch_searxng(source, client, store, *, backfill):
    from feeds.fetch import MemoryReader
    results = []
    for query in source.config["queries"]:
        results.extend(client.search(query)["results"][:MAX_ITEMS])
    return run_searxng(source, MemoryReader(json.dumps({"results": results})), store, backfill=backfill)
