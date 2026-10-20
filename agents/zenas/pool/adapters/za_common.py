"""Shared guards for South African fetches. A key never goes in a URL."""

from __future__ import annotations

import asyncio
import os

from agents.zenas.pool.http import USER_AGENT as _USER_AGENT
from agents.zenas.pool.licence import FetchNotConfigured


def reject_credential_url(url: str, secret: str = "") -> None:
    """Refuse before the request if a credential would ride in the URL."""
    lowered = url.lower()
    if "api_key=" in lowered or "token=" in lowered or "?key=" in lowered or "&key=" in lowered:
        raise FetchNotConfigured("refusing a URL that contains a credential")
    if secret and len(secret) >= 8 and secret in url:
        raise FetchNotConfigured("refusing a URL that contains a credential")


def bearer_headers(token: str, accept: str) -> dict[str, str]:
    reject_credential_url("https://api.laws.africa/v3/", token)
    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "User-Agent": _USER_AGENT,
    }


def html_headers() -> dict[str, str]:
    return {"Accept": "text/html", "User-Agent": _USER_AGENT}


def json_headers() -> dict[str, str]:
    return {"Accept": "application/json", "User-Agent": _USER_AGENT}


def xml_headers() -> dict[str, str]:
    return {"Accept": "text/xml, application/xml", "User-Agent": _USER_AGENT}


def pdf_headers() -> dict[str, str]:
    return {"Accept": "application/pdf, */*;q=0.1", "User-Agent": _USER_AGENT}


def laws_africa_token() -> str:
    return os.environ.get("LAWS_AFRICA_API_TOKEN", "").strip()


async def pause(env_name: str, default: str = "1") -> None:
    raw = os.environ.get(env_name, default).strip()
    try:
        seconds = float(raw or "0")
    except ValueError:
        seconds = 0
    if seconds > 0:
        await asyncio.sleep(seconds)
