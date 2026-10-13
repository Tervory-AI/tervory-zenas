"""Region is configuration. The package is not forked for South Africa."""

from __future__ import annotations

import os

US_REGION = "us-east"
ZA_REGION = "za"

_REGION_JURISDICTION = {
    "us-east": "us",
    "us": "us",
    "za": "za",
}


def current_region() -> str:
    """TANK_REGION selects the deployment. Unset is an error, not a US default."""
    raw = os.environ.get("TANK_REGION", "").strip().lower()
    if not raw:
        raise ValueError(f"TANK_REGION must be {US_REGION} or {ZA_REGION}")
    if raw == "us":
        return US_REGION
    if raw not in (US_REGION, ZA_REGION):
        raise ValueError(f"TANK_REGION must be {US_REGION} or {ZA_REGION}")
    return raw


def jurisdiction_for(region: str | None = None) -> str:
    key = region or current_region()
    if key == "us":
        key = US_REGION
    try:
        return _REGION_JURISDICTION[key]
    except KeyError as exc:
        raise ValueError(f"unknown region {key}") from exc
