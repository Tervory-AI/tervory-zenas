"""Radix's application-level HTTP guard.

An empty RADIX_ALLOWED_HOSTS refuses every host. A real request dials only the
room proxy, and only for https://lokd.co.za/. There is no direct socket to the
target and no environment proxy. Tank and Hermes use the chassis, not this client.
"""

from __future__ import annotations

import ipaddress
import os
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_PORTS = {"http": 80, "https": 443}
# Must match egress.ROOM_PROXY. Kept here so this module does not import the proxy.
ROOM_PROXY = "http://egress-proxy:8080"
DIAL_HOST = "lokd.co.za"


class HostNotAllowed(PermissionError):
    """The URL is invalid or its exact host is not allowed."""

    def __init__(self, host: str) -> None:
        self.host = host
        super().__init__(f"host not allowed: {host or '(none)'}")


def _hostname(value: str) -> str:
    # Only explicit ASCII DNS names. International names must use their exact
    # punycode form in both the approved list and the request.
    if not value.isascii():
        return ""
    name = value.lower().removesuffix(".")
    labels = name.split(".")
    if (
        len(name) > 253
        or len(labels) < 2
        or any(_LABEL.fullmatch(label) is None for label in labels)
        or re.fullmatch(r"(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})", labels[-1]) is None
    ):
        return ""
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return name
    return ""


def allowed_hosts(raw: str | None = None) -> frozenset[str]:
    """Parse exact DNS names; invalid configuration fails closed."""
    if raw is None:
        raw = os.environ.get("RADIX_ALLOWED_HOSTS", "")
    names: set[str] = set()
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        name = _hostname(value)
        if not name:
            # Do not echo arbitrary config, credentials, or control characters.
            raise ValueError("RADIX_ALLOWED_HOSTS must contain exact DNS names")
        names.add(name)
    return frozenset(names)


def host_from_url(url: str) -> str:
    """Reject ambiguous authority syntax before HTTPX normalizes it."""
    if not isinstance(url, str) or not url or not url.isascii():
        return ""
    if any(ord(char) <= 32 or ord(char) == 127 for char in url) or "\\" in url:
        return ""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in _PORTS
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or "%" in parsed.netloc
            or "#" in url
        ):
            return ""
        host = _hostname(parsed.hostname or "")
        if not host:
            return ""
        # Explicit ports must be the scheme's default. Reject empty, padded,
        # signed, out-of-range and nonnumeric ports rather than guessing.
        if ":" in parsed.netloc:
            if parsed.netloc.rsplit(":", 1)[1] != str(_PORTS[parsed.scheme]):
                return ""
        if parsed.port not in (None, _PORTS[parsed.scheme]):
            return ""
    except ValueError:
        return ""
    return host


def require_room_proxy() -> str:
    """Fail closed unless the process is pointed at the room proxy and nothing else."""
    raw = os.environ.get("RADIX_EGRESS_PROXY", "").strip()
    if raw != ROOM_PROXY:
        raise HostNotAllowed("")
    return ROOM_PROXY


def require_allowed(url: str, allow: frozenset[str] | None = None) -> str:
    """Return the exact host, or refuse before any DNS/network activity."""
    host = host_from_url(url)
    listed = allowed_hosts() if allow is None else allow
    if not host or host not in listed:
        raise HostNotAllowed(host)
    return host


class Client:
    """Single-hop HTTP requests. The injected HTTP client is trusted test code.

    Redirects are returned to the caller, never followed automatically. A
    subsequent request must pass this guard again. No environment proxy is used.
    Without an injected test client, the only dial is to the room proxy, and
    only for https://lokd.co.za/.
    """

    def __init__(
        self,
        allow: frozenset[str] | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self._allow = allowed_hosts() if allow is None else allowed_hosts(",".join(allow))
        self._http = http

    def _request(self, client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
        request = client.build_request(method, url, **kwargs)
        require_allowed(str(request.url), self._allow)
        # A different Host header can select a different virtual host at the
        # same address. Also check defaults supplied on an injected test client.
        if request.headers.get("host", "").lower() != request.url.netloc.decode("ascii").lower():
            raise HostNotAllowed("")
        return client.send(request, auth=None, follow_redirects=False)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        host = require_allowed(url, self._allow)
        if kwargs.pop("follow_redirects", False) is not False:
            raise ValueError("Radix does not follow redirects automatically")
        # Auth handlers may issue extra requests; extensions may override SNI.
        if "auth" in kwargs or "extensions" in kwargs:
            raise ValueError("Radix does not accept auth handlers or transport extensions")
        if self._http is None:
            if host != DIAL_HOST or not url.lower().startswith("https://"):
                raise HostNotAllowed(host)
            proxy = require_room_proxy()
            with httpx.Client(
                proxy=proxy, timeout=30, trust_env=False, follow_redirects=False
            ) as client:
                return self._request(client, method, url, **kwargs)
        return self._request(self._http, method, url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)
