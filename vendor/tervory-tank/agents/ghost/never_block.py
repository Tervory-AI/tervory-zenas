"""Never-block list and IP gate for Autumbra shields.

Own IPs, payment providers and Cloudflare ranges are never blocked. An address
must appear in the raw log (or event) before any block action. Plain rules decide
first; a model opinion is never required for this gate. Actions per hour are capped.
"""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import re
import time
from collections import defaultdict
from typing import Iterable

# Cloudflare public published ranges (IPv4 subset used for the never-block gate).
# Refresh from https://www.cloudflare.com/ips/ — Founder may replace with a feed.
CLOUDFLARE_CIDRS = (
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
)

# Well-known payment / processor egress ranges and docs hosts (names for receipts).
# These are defensive denylists for *blocking*, not allowlists for traffic.
PAYMENT_CIDRS = (
    # Stripe documented webhooks / API egress (representative; refresh with Stripe docs).
    "54.187.174.169/32",
    "54.187.205.235/32",
    "54.187.216.72/32",
    "54.241.31.99/32",
    "54.241.31.102/32",
    "54.241.34.107/32",
    # PayPal / Braintree representative
    "64.4.240.0/21",
    "66.211.168.0/22",
    "173.0.80.0/20",
)

IP_IN_TEXT = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"
)

DEFAULT_ACTIONS_PER_HOUR = 60


class NeverBlockError(ValueError):
    """Raised when a block would hit the never-block list or fail the IP-in-log rule."""


def _networks(cidrs: Iterable[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    out: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in cidrs:
        out.append(ipaddress.ip_network(item, strict=False))
    return out


_CF_NETS = _networks(CLOUDFLARE_CIDRS)
_PAY_NETS = _networks(PAYMENT_CIDRS)


def parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def extract_ips(raw_log: str | bytes) -> list[str]:
    """IPs that appear in the raw event/log before any shield acts."""
    if isinstance(raw_log, (bytes, bytearray)):
        try:
            text = raw_log.decode("utf-8", errors="replace")
        except Exception:
            return []
    else:
        text = str(raw_log)
    # Deduplicate, preserve order.
    seen: set[str] = set()
    ordered: list[str] = []
    for match in IP_IN_TEXT.findall(text):
        if match not in seen and parse_ip(match) is not None:
            seen.add(match)
            ordered.append(match)
    return ordered


def classify_never_block(
    address: str,
    *,
    client_own_cidrs: Iterable[str] = (),
) -> str | None:
    """Return a plain reason if this address must never be blocked, else None."""
    ip = parse_ip(address)
    if ip is None:
        return "not a valid address"
    for net in _networks(tuple(client_own_cidrs) + ()):
        if ip in net:
            return "client own address"
    for net in _CF_NETS:
        if ip in net:
            return "Cloudflare"
    for net in _PAY_NETS:
        if ip in net:
            return "payment provider"
    return None


def may_block_ip(
    address: str,
    *,
    raw_log: str | bytes,
    client_own_cidrs: Iterable[str] = (),
) -> tuple[bool, str]:
    """Rules-first gate. IP must appear in the raw log; never-block list wins."""
    ips_in_log = extract_ips(raw_log)
    if address not in ips_in_log:
        return False, "address not present in the raw log"
    reason = classify_never_block(address, client_own_cidrs=client_own_cidrs)
    if reason:
        return False, f"never-block: {reason}"
    return True, "ok"


class ActionCap:
    """Per-tenant sliding hour; production uses the box's locked durable state."""

    def __init__(self, limit: int = DEFAULT_ACTIONS_PER_HOUR, *, clock=time.time, data_dir=None):
        if limit < 1:
            raise ValueError("Action cap must be at least 1.")
        root = data_dir or os.environ.get("DATA_DIR")
        self._path = Path(root) / "action-cap.json" if root else None
        self.limit = limit
        self._clock = clock
        self._hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, tenant_id: str) -> tuple[bool, str]:
        if self._path is not None:
            from .durable_state import locked_state
            try:
                with locked_state(self._path) as state:
                    return self._allow(tenant_id, state)
            except (OSError, ValueError, TypeError):
                # Corrupt/unwritable state never resets a safety budget.
                return False, f"actions-per-hour cap ({self.limit}) reached"
        return self._allow(tenant_id, self._hits)

    def _allow(self, tenant_id: str, state: dict) -> tuple[bool, str]:
        now = self._clock()
        hits = [t for t in state.get(tenant_id, []) if t >= now - 3600]
        state[tenant_id] = hits
        if len(hits) >= self.limit:
            return False, f"actions-per-hour cap ({self.limit}) reached"
        hits.append(now)
        return True, "ok"
