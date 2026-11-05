"""Per-feed API keys for Market Lab.

The values live in the environment, the same place as every other secret in
this repo. Coolify holds them. The names are listed in ``.env.example`` and
passed into the Market Lab box by ``agents/market_lab/docker-compose.yml``.
This module is the list of those names. It never returns a value to a log,
a row, or an error string.

A feed named here stays off until its variable is non-empty. Nothing in the
app asks for a key. FRED is read live and is not stored.
"""

from __future__ import annotations

import os
import re
from dataclasses import replace

from feeds.models import SourceSpec

# Adapter -> env var. These feeds copy rows and stay off until the key is set.
ADAPTER_KEYS: dict[str, str] = {
    "census_population": "CENSUS_API_KEY",
    "census_trade": "CENSUS_API_KEY",
    "legiscan": "LEGISCAN_API_KEY",
    "eia": "EIA_API_KEY",
    "noaa_climate": "NOAA_TOKEN",
}

# Live read only. FRED's terms forbid a database copy, so the feeds runner
# never stores the series. The daily refresh reads them when this is set.
FRED_KEY = "FRED_API_KEY"

# Every name the box reads. Compose and .env.example must list each one.
KEY_NAMES: tuple[str, ...] = (
    "CENSUS_API_KEY",
    "LEGISCAN_API_KEY",
    "EIA_API_KEY",
    "NOAA_TOKEN",
    FRED_KEY,
)

_QUERY = re.compile(r"\?[^\s'\"<>]*")
_ASSIGNED = re.compile(r"(?i)\b(api_key|apikey|token|key)=([^&\s]+)")


def key_name_for(spec: SourceSpec) -> str:
    """Env var this feed needs, or '' when it runs without one."""
    if spec.key_name:
        return spec.key_name
    if spec.id.startswith("fred_"):
        return FRED_KEY
    return ADAPTER_KEYS.get(spec.adapter, "")


def bind_key(spec: SourceSpec) -> SourceSpec:
    """Copy the spec with the key name filled in from this registry."""
    name = key_name_for(spec)
    required = bool(name)
    if spec.key_name == name and spec.key_required is required:
        return spec
    return replace(spec, key_name=name, key_required=required)


def key_is_set(name: str) -> bool:
    """True when no key is required, or the named variable is non-empty.

    The value is never returned.
    """
    if not name:
        return True
    return bool(os.environ.get(name, "").strip())


def missing_key(spec: SourceSpec) -> str:
    """The env name when this feed must stay off, else ''."""
    name = key_name_for(spec)
    if not name or key_is_set(name):
        return ""
    return name


def redact(text: str) -> str:
    """Drop query strings and any configured key value from a stored error."""
    cleaned = _ASSIGNED.sub(r"\1=REDACTED", text)
    cleaned = _QUERY.sub("", cleaned)
    for name in KEY_NAMES:
        value = os.environ.get(name, "").strip()
        if len(value) >= 8 and value in cleaned:
            cleaned = cleaned.replace(value, "REDACTED")
    return cleaned
