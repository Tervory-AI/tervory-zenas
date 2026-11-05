"""Collect one licensed feed into an in-memory parser store.

The temporary store is never a serving database. The caller commits the checked
observations and an immutable source document to its tenant's Postgres ledger.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import urllib.request
import uuid
from typing import Any
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from feeds.adapters import ADAPTERS
from feeds.db import apply_schema, connect
from feeds.fetch import fetch_source, watch_page
from feeds.keys import bind_key, missing_key, redact
from feeds.live import BlockedSite, PoliteClient, login_boundary
from feeds.reader import FixtureReader
from feeds.store import Store


def public_url(url: str) -> str:
    """Never persist credentials, query strings or fragments from a request."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("a public HTTPS source is required")
    if parts.port not in {None, 443} or parts.hostname in {"localhost", "metadata.google.internal"}:
        raise ValueError("a public HTTPS source is required")
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("a public HTTPS source is required")
    return urlunsplit((parts.scheme, parts.hostname, parts.path, "", ""))


class EvidenceClient:
    """Read public pages, retain provenance, and stop at access boundaries."""

    def __init__(self, client: PoliteClient | None = None, *, allowed_domains: set[str] | None = None,
                 public_feeds: bool = False, tenant_id: str | None = None) -> None:
        from feeds.keyless import MAX_BYTES
        self.public_feeds = public_feeds
        self.tenant_id = tenant_id
        self.allowed_domains = allowed_domains
        self.client = client or PoliteClient(max_bytes=MAX_BYTES if public_feeds else None)
        if client is None:
            evidence = self
            class CheckedRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, request, fp, code, message, headers, newurl):
                    evidence._destination(newurl)
                    evidence._allowed(newurl)
                    if login_boundary(newurl):
                        raise BlockedSite(urlsplit(newurl).hostname or "")
                    evidence.client._wait(urlsplit(newurl).hostname or "")
                    return super().redirect_request(request, fp, code, message, headers, newurl)
            opener = urllib.request.build_opener(CheckedRedirect())
            self.client.opener = opener.open
        self.documents: list[dict] = []

    def _destination(self, url: str) -> str:
        from feeds.keyless import check_feed_url
        clean = check_feed_url(url) if self.public_feeds else public_url(url)
        host = urlsplit(clean).hostname or ""
        if self.allowed_domains is not None and not any(host == domain or host.endswith("." + domain) for domain in self.allowed_domains):
            raise ValueError("download is outside the configured publishers")
        return clean

    def _allowed(self, url: str) -> None:
        self._destination(url)
        self.client.ensure_open(url)

    def get_text(self, url: str, headers: dict | None = None) -> str:
        return self.get(url, headers).decode("utf-8-sig", errors="replace")

    def get(self, url: str, headers: dict | None = None) -> bytes:
        self._allowed(url)
        body = self.client.get(url, headers=headers)
        from feeds.keyless import public_link
        self.documents.append({"url": public_link(url) if self.public_feeds else public_url(url),
                               "sha256": hashlib.sha256(body).hexdigest()})
        return body

    def search(self, query: str) -> dict:
        """A fixed, room-authenticated Tank corridor; it cannot invoke a model."""
        import httpx
        import json
        from feeds.keyless import MAX_BYTES
        owner = str(uuid.UUID(str(self.tenant_id)))
        key = os.environ.get("TANK_KEY", "")
        if not key:
            raise ValueError("room connection unavailable")
        base = os.environ.get("TANK_URL", "http://tank:8000").rstrip("/")
        with httpx.Client(timeout=60, follow_redirects=False) as http:
            with http.stream("POST", base + "/v1/market-lab/internal/search",
                             headers={"X-Tank-Key": key}, json={"tenant_id": owner, "question": query,
                             "home_region": os.environ.get("TANK_REGION", "us-east")}) as response:
                response.raise_for_status()
                body = bytearray()
                for part in response.iter_bytes():
                    body.extend(part)
                    if len(body) > MAX_BYTES:
                        raise ValueError("response exceeds the feed limit")
        return json.loads(body)

    def post_json(self, url: str, payload: dict) -> bytes:
        """A publisher's read-only query API; never a caller-selected host."""
        import json
        self._allowed(url)
        data = json.dumps(payload, separators=(",", ":")).encode()
        if len(data) > 256000:
            raise ValueError("feed query exceeds the limit")
        body = self.client.request(url, headers={"Content-Type": "application/json"}, data=data)
        self.documents.append({"url": public_url(url), "sha256": hashlib.sha256(body).hexdigest()})
        return body

    def iter_lines(self, url: str, headers: dict | None = None):
        self._allowed(url)
        digest = hashlib.sha256()
        for line in self.client.iter_lines(url, headers=headers):
            digest.update(line.encode("utf-8"))
            yield line
        self.documents.append({"url": public_url(url), "sha256": digest.hexdigest(), "encoding": "decoded lines"})


def collect(source: Any, *, fixtures: str | None, backfill: bool, client: EvidenceClient) -> dict:
    spec = bind_key(source)
    from feeds.keyless import public_link
    keyless = spec.adapter in {"rss", "comtrade_preview", "searxng"}
    result = {"source_id": spec.id, "name": spec.name, "cadence": spec.cadence,
              "kind": "copy" if spec.allow_bulk else "linked", "licence": spec.licence,
              "terms_checked_at": spec.terms_checked_at, "url": public_link(spec.url) if keyless else public_url(spec.url),
              "presentation": spec.config.get("presentation", {}),
              "points": [], "events": [], "downloads": [], "note": "", "fixture": bool(fixtures)}
    if not spec.enabled:
        return {**result, "note": "Off, disabled"}
    if not fixtures and missing_key(spec):
        return {**result, "note": "Off, needs key"}
    if not spec.allow_bulk and (fixtures or not spec.config.get("watch")):
        return {**result, "note": "Linked only; storage is not permitted"}
    conn = connect(":memory:")
    try:
        apply_schema(conn)
        store = Store(conn)
        store.upsert_source(spec)
        conn.commit()
        # Defer the batch decision to the tenant ledger, which has prior values.
        store.begin_batch("collect")
        client.documents = []
        if fixtures:
            ADAPTERS[spec.adapter](spec, FixtureReader([Path(fixtures)]), store, backfill=backfill)
        elif spec.allow_bulk:
            fetch_source(spec, client, store, backfill=backfill)
        else:
            watch_page(spec, client, store)
        meta = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM series")}
        result["points"] = [{**meta[point["series_id"]], **point} for point in store._staged]
        if spec.adapter == "comtrade_preview" and result["points"]:
            result["url"] = result["points"][0]["url"]
        result["events"] = [dict(row) for row in conn.execute("SELECT * FROM events ORDER BY date, id")]
        result["downloads"] = list(client.documents)
    except BlockedSite:
        result.update(points=[], events=[], downloads=[], note="Off, blocked")
    except Exception:
        # Adapter exceptions can contain source data, URLs or credentials.
        result.update(points=[], events=[], downloads=[], note="Off, source could not be read")
    finally:
        conn.close()
    return _clean(result, public_links=keyless)


def _clean(value: Any, *, public_links: bool = False) -> Any:
    if isinstance(value, str):
        if public_links and value.startswith(("http://", "https://")):
            from feeds.keyless import public_link
            try:
                return public_link(value)
            except ValueError:
                return ""
        return redact(value)
    if isinstance(value, list):
        return [_clean(item, public_links=public_links) for item in value]
    if isinstance(value, dict):
        return {key: _clean(item, public_links=public_links) for key, item in value.items()}
    return value
