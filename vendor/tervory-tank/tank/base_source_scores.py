"""Shared source ranking for Tank audits and the Base room; arithmetic only."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

SOURCES = ("reference_file", "own_site", "engine_registry", "social_profile", "archive",
           "google_places", "opencorporates", "other_web")
SOCIAL_HOSTS = ("facebook.com", "fb.com", "fb.me", "instagram.com", "x.com", "twitter.com",
                "linkedin.com", "youtube.com", "youtu.be", "tiktok.com")


def host(value: Any) -> str:
    try:
        return (urlsplit(value).hostname or "").lower().removeprefix("www.") if isinstance(value, str) else ""
    except ValueError:
        return ""


def classify(source_url: Any, website_url: Any, source: str | None = None) -> str | None:
    """Explicit provenance covers non-web sources; URLs are classified by host, never substrings."""
    if source in SOURCES:
        return source
    if isinstance(source, str) and source.startswith(("registry:", "engine:")):
        return "engine_registry"
    address = host(source_url)
    if not address:
        return None
    def belongs(domains):
        return any(address == domain or address.endswith("." + domain) for domain in domains)
    if belongs(("atf.gov",)):
        return "reference_file"
    if belongs(("web.archive.org", "archive.org", "archive.ph", "archive.today")):
        return "archive"
    path = urlsplit(source_url).path.lower()
    if (belongs(("maps.google.com", "maps.app.goo.gl")) or
            address in {"google.com", "goo.gl"} and (path == "/maps" or path.startswith("/maps/"))):
        return "google_places"
    if belongs(("opencorporates.com",)):
        return "opencorporates"
    if belongs(SOCIAL_HOSTS):
        return "social_profile"
    site = host(website_url)
    if site and (address == site or address.endswith("." + site) or site.endswith("." + address)):
        return "own_site"
    return "other_web"


def prefer(candidates: list[dict[str, Any]], field: str,
           table: dict[tuple[str, str], float]) -> dict[str, Any] | None:
    """Highest measured Wilson bound wins; stable first-candidate ties, unscored last."""
    return max(candidates, key=lambda item: table.get(
        (classify(item.get("source_url"), item.get("website"), item.get("source_kind") or item.get("source")), field), -1.0),
        default=None)


def select_observations(profile: dict[str, Any], observations: list[dict[str, Any]],
                        table: dict[tuple[str, str], float]) -> dict[str, Any]:
    """Rank independently observed contacts; retain every candidate in record-local evidence.

    This chooses primary values, not verification. Existing final checks and blocked/archive
    rules still decide whether each field can be exported as verified.
    """
    website = (profile.get("website") or {}).get("value")
    decisions = {}
    for field, collection, target in (("phone", "phones", "phone"), ("email", "emails", "emails"),
                                      ("socials", "socials", "socials")):
        candidates = []
        for page in observations:
            if not page.get("source_url") or not page.get("checked_at"):
                continue
            found = page.get(collection) or []
            if isinstance(found, dict):
                found = list(found.values())
            if not isinstance(found, list):
                continue
            for value in found:
                if isinstance(value, str) and value:
                    candidates.append({"value": value, "source_url": page["source_url"],
                                       "checked_at": page["checked_at"], "website": website,
                                       "source_kind": classify(page["source_url"], website, page.get("source_kind"))})
        if len({item["value"] for item in candidates}) < 2:
            continue
        winner = prefer(candidates, field, table)
        if not any((item["source_kind"], field) in table for item in candidates):
            continue  # No measurements yet: preserve existing primary behavior.
        ordered = [winner, *(item for item in candidates if item is not winner)]
        values = list(dict.fromkeys(item["value"] for item in ordered))
        profile[target] = {"value": winner["value"] if field == "phone" else values,
                           "source": "site", "source_url": winner["source_url"],
                           "checked_at": winner["checked_at"], "source_kind": winner["source_kind"],
                           "confidence": (profile.get(target) or {}).get("confidence", 80)}
        decisions[field] = {"selected": winner, "candidates": candidates,
                            "wilson_lower": table.get((winner["source_kind"], field))}
    return decisions
