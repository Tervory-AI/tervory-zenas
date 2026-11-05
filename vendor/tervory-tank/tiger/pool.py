"""Session keys for Tiger's browser pool.

One context per agent, tenant, and session. The file path is the isolation
boundary: two tenants never share a file, even when the session string matches.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

IDLE_S = 600
HEAVY_RESOURCES = frozenset({"image", "media", "font"})
_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,128}$")


class SessionKeyError(ValueError):
    """A session key would leave that agent and tenant's directory."""


def is_public_url(url: str) -> bool:
    """True only for an http(s) URL whose every address is on the public internet.

    A name with no dot is a Docker service (`tank`, `litellm`, `postgres`), not a
    site. `localhost` is refused by name. Everything else is resolved, and one
    private, loopback, link-local, multicast, reserved, or unspecified address
    — IPv4, IPv6, or an IPv4 address mapped into IPv6 — refuses the URL.
    """
    if not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host == "localhost":
        return False
    if not _is_ip(host) and "." not in host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            address = info[4][0]
        except (IndexError, TypeError):
            return False
        if _blocked_address(address):
            return False
    return True


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _blocked_address(address: str) -> bool:
    text = address.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def render_block(url: str) -> dict[str, str] | None:
    """What `/render` returns before it opens a page. None when the URL may load."""
    if is_public_url(url):
        return None
    return {"error": "url not allowed"}


def route_continues(url: str, resource_type: str, screenshot: bool) -> bool:
    """A navigation, redirect, or sub-resource may proceed only when its URL is public."""
    if not is_public_url(url):
        return False
    return not blocks_resource(resource_type, screenshot)


def blocks_resource(resource_type: str, screenshot: bool) -> bool:
    """Images and fonts stay blocked unless the caller asked for a picture."""
    if screenshot:
        return False
    return resource_type in HEAVY_RESOURCES


def idle_expired(last_used: float, now: float, idle_s: float = IDLE_S) -> bool:
    return (now - last_used) >= idle_s


def session_file(root: str | Path, agent: str, tenant: str, session: str) -> Path:
    """`/data/sessions/<agent>/<tenant>/<session>.json`, or raise."""
    base = Path(root)
    parts = (_part(agent), _part(tenant), _part(session))
    path = base / "sessions" / parts[0] / parts[1] / f"{parts[2]}.json"
    root_dir = base.resolve()
    resolved = path.resolve()
    if resolved != root_dir and root_dir not in resolved.parents:
        raise SessionKeyError("bad session key")
    return path


def _part(value: str) -> str:
    if not isinstance(value, str) or _PART.fullmatch(value) is None:
        raise SessionKeyError("bad session key")
    return value
