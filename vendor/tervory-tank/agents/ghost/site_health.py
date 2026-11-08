"""One-way signed health files. The observer accepts no Enforce input."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import sys
import time
from urllib.parse import urlsplit
from pathlib import Path

import httpx

HEALTH_URLS = {"tervory.com": "https://tervory.com/"}
HEALTH_DIR = Path("/health")
OUTPUT_DIR = Path("/data")
SECRET_ENV = "AUTUMBRA_HEALTH_SECRET"


def canonical(result: dict) -> bytes:
    return json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def health_url(site: str) -> str:
    # Existing health egress approves this exact host; no caller-selected host.
    if site not in HEALTH_URLS:
        raise ValueError("Health host is not approved.")
    url = os.environ.get("AUTUMBRA_HEALTH_URL", "").strip() or HEALTH_URLS[site]
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != site
            or parsed.port not in {None, 443} or parsed.username or parsed.password
            or parsed.query or parsed.fragment or len(url) > 1000
            or any(ord(c) < 33 for c in url)):
        raise ValueError("Health URL must use the client's approved HTTPS host.")
    return url


def observer_site() -> str | None:
    site = os.environ.get("SITE_NAME", "").strip()
    try:
        health_url(site)
    except ValueError:
        return None
    return site


def health_configured(site: str | None = None) -> bool:
    configured_site = observer_site()
    return (bool(os.environ.get(SECRET_ENV, "").strip()) and HEALTH_DIR.is_dir()
            and configured_site is not None and (site is None or site == configured_site))


def _read_result(site: str) -> dict | None:
    """Read only authenticated, bounded, recent results, including failures."""
    try:
        secret = os.environ.get(SECRET_ENV, "")
        if not secret or site not in HEALTH_URLS:
            return None
        path = HEALTH_DIR / (site + ".json")
        if path.stat().st_size > 4096:
            return None
        envelope = json.loads(path.read_text())
        result = envelope["result"]
        expected = hmac.new(secret.encode(), canonical(result), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, envelope["signature"]):
            return None
        age = time.time() - result["checked_at"]
        if (set(result) != {"site", "url", "ok", "status", "ms", "checked_at"}
                or result["site"] != site or result["url"] != health_url(site)
                or type(result["ok"]) is not bool
                or not math.isfinite(result["ms"]) or result["ms"] < 0
                or not 0 <= age <= 30):
            return None
        return result
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None


def read_health(site: str, *, after: float | None = None, wait_seconds: float = 0,
                result_out: dict | None = None) -> bool:
    """Require a signed result strictly after apply, waiting at most 30 seconds.

    Pre-apply success or failure cannot judge the new rule. An authenticated
    post-apply failure is immediately false; missing/invalid results wait only
    until the fixed deadline, then fail closed for automatic Undo.
    """
    if result_out is not None:
        result_out.clear()
    deadline = time.monotonic() + min(30.0, max(0.0, wait_seconds))
    while True:
        result = _read_result(site)
        if result is not None and (after is None or result["checked_at"] > after):
            if result_out is not None:
                result_out.update(result)
            return (result["ok"] is True and type(result["status"]) is int
                    and 200 <= result["status"] < 300)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(1.0, remaining))


def observe_once(client: httpx.Client, site: str) -> dict:
    url = health_url(site)
    started = time.monotonic()
    checked_at = time.time()
    status = None
    try:
        # Streaming discards the body; redirects and cookie persistence are disabled.
        client.cookies.clear()
        with client.stream("GET", url) as response:
            status = response.status_code
    except httpx.HTTPError:
        pass
    finally:
        client.cookies.clear()
    return {"site": site, "url": url, "ok": status is not None and 200 <= status < 300,
            "status": status, "ms": round((time.monotonic() - started) * 1000, 3),
            "checked_at": checked_at}


def main() -> None:
    secret = os.environ.get(SECRET_ENV, "")
    site = observer_site()
    if not secret.strip() or site is None:
        print("Health observer is not configured.", flush=True)
        from .configuration import missing_settings
        names = missing_settings("health_observer")
        if site is None and "SITE_NAME" not in names:
            names.append("SITE_NAME")
        print("Missing or invalid settings: " + ", ".join(names), file=sys.stderr, flush=True)
        while True:
            time.sleep(15)
    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    transport = httpx.HTTPTransport(proxy="http://health-egress-proxy:8080")
    with httpx.Client(transport=transport, trust_env=False, timeout=5,
                      follow_redirects=False) as client:
        while True:
            result = observe_once(client, site)
            signature = hmac.new(secret.encode(), canonical(result), hashlib.sha256).hexdigest()
            path = output_dir / (site + ".json")
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"result": result, "signature": signature}))
            temporary.replace(path)
            time.sleep(1)


if __name__ == "__main__":
    main()
