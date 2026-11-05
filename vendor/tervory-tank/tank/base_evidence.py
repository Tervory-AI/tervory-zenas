"""Public field evidence and Base training outputs; no industry rules or models."""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from selectolax.parser import HTMLParser

observations: ContextVar[list[dict[str, Any]] | None] = ContextVar("base_field_observations", default=None)
FIELDS = ("website", "phone", "email", "socials")
SITE_SIGNAL_FIELDS = ("technologies", "sells_online", "site_newest_year")
EMAIL = re.compile('(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\\.[A-Za-z]{2,63}(?![A-Za-z0-9.-])')
PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
SOCIAL_NETWORKS = {
    "facebook": ("facebook.com", "fb.com", "fb.me"), "instagram": ("instagram.com",),
    "x": ("x.com", "twitter.com"), "linkedin": ("linkedin.com",),
    "youtube": ("youtube.com", "youtu.be"), "tiktok": ("tiktok.com",),
}


def social_network(url: str) -> str | None:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return None
    networks = {**SOCIAL_NETWORKS, "pinterest": ("pinterest.com",), "yelp": ("yelp.com",)}
    return next((name for name, hosts in networks.items()
                 if any(host == domain or host.endswith("." + domain) for domain in hosts)), None)


def social_profile(url: str) -> bool:
    if not public_url(url) or not social_network(url):
        return False
    path = urlsplit(url).path.lower().strip("/")
    first = path.split("/")[0]
    return bool(path) and first not in {
        "share", "sharer", "sharer.php", "intent", "login", "signup", "dialog", "plugins", "hashtag", "search",
        "home", "pages", "profile.php", "p", "reel", "watch", "explore",
    } and path not in {"company", "in", "channel", "user", "c"}


def public_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        p = urlsplit(value)
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            return None
        return urlunsplit((p.scheme, p.netloc, p.path or "/", "", ""))
    except ValueError:
        return None


def phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) == 10 or len(digits) == 11 and digits.startswith("1") else ""


def observe(url: str, html: str) -> None:
    target = observations.get()
    address = public_url(url)
    if target is None or not address:
        return
    tree = HTMLParser(html[:600_000])
    links, websites = [], []
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href", "")
        if value := public_url(urljoin(url, href)):
            links.append(value)
            # rel=me is the site's explicit identity assertion, not a navigation link.
            if "me" in anchor.attributes.get("rel", "").lower().split() and not social_network(value):
                websites.append(value)
    for node in tree.css("script,style,noscript,template"):
        node.decompose()
    text = tree.text(separator=" ")
    emails = {match.lower() for match in EMAIL.findall(text)}
    for anchor in tree.css('a[href^="mailto:"]'):
        emails.update(match.lower() for match in EMAIL.findall(anchor.attributes.get("href", "")))
    phones = {phone(match) for match in PHONE.findall(text)} - {""}
    for anchor in tree.css('a[href^="tel:"]'):
        if value := phone(anchor.attributes.get("href", "")[4:]):
            phones.add(value)
    target.append({"source_url": address, "checked_at": datetime.now(timezone.utc).isoformat(),
                   "emails": sorted(emails), "phones": sorted(phones), "links": list(dict.fromkeys(links)),
                   **({"websites": list(dict.fromkeys(websites))} if websites else {}),
                   **({"socials": list(dict.fromkeys(link for link in links if social_profile(link)))}
                      if any(social_profile(link) for link in links) else {})})


def field(value: Any = None, source: str | None = None, checked: str | None = None, reason: str | None = None) -> dict[str, Any]:
    return {"value": value, "source_url": source, "checked_at": checked, "verified": bool(value and source and checked),
            "reason": reason if reason else None if value and source and checked else "No source and check date were recorded."}


def values(value: Any) -> list[str]:
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return [text for item in value for text in values(item)]
    return [value] if isinstance(value, str) and value else []


def add_values(result: dict[str, Any], name: str, found: list[str], source: str, checked: str) -> None:
    items = result[name].get("items", [])
    if not items and result[name]["verified"]:
        items = [result[name]]
    key = phone if name == "phone" else public_url if name in {"website", "socials"} else str.lower
    seen = {key(item["value"]) for item in items}
    for value in found:
        identity = key(value)
        if identity and identity not in seen:
            items.append(field(value, source, checked))
            seen.add(identity)
    if items:
        scalar = name in {"website", "phone"}
        result[name] = {**field(items[0]["value"] if scalar else [item["value"] for item in items],
                               items[0]["source_url"], items[0]["checked_at"]),
                        **({"items": items} if len(items) > 1 or not scalar else {})}


def row_fields(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if row.get("status") not in {None, "done", "failed"}:
        return {name: field(reason="Checks are still running.") for name in FIELDS}
    profile, passes = row.get("profile") or {}, row.get("passes") or {}
    proof = passes.get("2b", {}).get("field_observations") or []
    checks = passes.get("7", {}).get("checks") or {}
    blocked = passes.get("2b", {}).get("access_error")
    blocked_url = blocked.get("url") if isinstance(blocked, dict) and blocked.get("status") == "blocked" else None
    blocked_sites = passes.get("2a", {}).get("blocked_by_check") or []
    if not blocked_url and isinstance(blocked_sites, list) and blocked_sites:
        blocked_url = blocked_sites[0]
    if blocked_url:
        return {name: field(source=public_url(blocked_url), reason="blocked") for name in FIELDS}
    if passes.get("2b", {}).get("archive_date"):
        return {name: field(reason="Only an archived page was available; current details were not verified.") for name in FIELDS}
    result = {name: field(reason="Not found in the public pages checked.") for name in FIELDS}
    website = (profile.get("website") or {}).get("value")
    wanted_phone = (profile.get("phone") or {}).get("value")
    websites = values(website) + values((profile.get("websites") or {}).get("value"))
    phones = values(wanted_phone) + values((profile.get("phones") or {}).get("value"))
    emails = values((profile.get("emails") or {}).get("value"))
    wanted_links = values((profile.get("socials") or {}).get("value")) + values((profile.get("social_links") or {}).get("value"))
    for observed in proof:
        url, checked = public_url(observed.get("source_url")), observed.get("checked_at")
        if not url or not checked:
            continue
        same_site = any(public_url(site) and (urlsplit(url).hostname or "").removeprefix("www.") ==
                        (urlsplit(site).hostname or "").removeprefix("www.") for site in websites)
        if same_site:
            matches = [site for site in websites if public_url(site) and (public_url(site) == url or
                       site == website and (urlsplit(site).hostname or "").removeprefix("www.") ==
                       (urlsplit(url).hostname or "").removeprefix("www."))]
            add_values(result, "website", matches + values(observed.get("websites")), url, checked)
        observed_phones = {phone(item) for item in observed.get("phones", [])} - {""}
        if checks.get("phone_matches_site") is not False:
            matches = [item for item in phones if phone(item) in observed_phones]
            matches += ["+1" + item for item in sorted(observed_phones)] if same_site else []
            add_values(result, "phone", matches, url, checked)
        observed_emails = [item.lower() for item in values(observed.get("emails"))]
        matches = [email for email in emails if email.lower() in observed_emails]
        if same_site:
            matches += observed_emails
        if matches and checks.get("email_domain_matches_site") is not False:
            add_values(result, "email", matches, url, checked)
        matches = [link for link in wanted_links if social_profile(link) and public_url(link) in observed.get("links", [])]
        if same_site:
            matches += [link for link in observed.get("links", []) if social_profile(link)]
        if matches:
            add_values(result, "socials", matches, url, checked)
    for name, check in (("phone", "phone_matches_site"), ("email", "email_domain_matches_site")):
        if checks.get(check) is False:
            result[name] = field(reason="The final cross-check disagreed with the source.")
    contacts = (profile.get("contacts") or {}).get("value") or {}
    if isinstance(contacts, dict) and contacts.get("domain_accepts_mail") is False:
        result["email"] = field(reason="The mail domain does not accept email.")
    social_block = passes.get("4", {}).get("access_error") or {}
    if isinstance(social_block, dict) and social_block.get("status") == "blocked":
        result["socials"] = field(source=public_url(social_block.get("url")), reason="blocked")
    if not proof:
        # Legacy rows retain their actual observation, never the current time.
        old = passes.get("2b", {}).get("gold_observation") or {}
        if old.get("status") == 200 and old.get("blocked") is False and not passes.get("2b", {}).get("archive_date"):
            url, checked = public_url(old.get("source_url")), old.get("observed_on")
            if website and url and checked:
                result["website"] = field(website, url, checked)
            if url and checked and checks.get("phone_matches_site") is not False:
                old_phones = {phone(v) for v in old.get("phones", [])} - {""}
                add_values(result, "phone", [item for item in phones if phone(item) in old_phones] +
                           ["+1" + item for item in sorted(old_phones)], url, checked)
    if row.get("status") == "failed":
        for name in FIELDS:
            if not result[name]["verified"]:
                result[name] = field(reason="A pipeline step failed before this field could be checked.")
    # Ranking never upgrades verification. Reorder only candidates that survived
    # the existing source/date, archive, blocking and final cross-check guards.
    for name, decision in (passes.get("source_reliability") or {}).items():
        if name not in result or not result[name]["verified"]:
            continue
        winner = decision.get("selected") or {}
        items = result[name].get("items") or [result[name]]
        match = next((item for item in items if item["value"] == winner.get("value")
                      and item["source_url"] == winner.get("source_url")), None)
        if match:
            ordered = [match, *(item for item in items if item is not match)]
            result[name] = {**field(match["value"] if name in {"website", "phone"}
                                    else [item["value"] for item in ordered],
                                    match["source_url"], match["checked_at"]), "items": ordered,
                            "source_kind": winner.get("source_kind")}
    # Add quality only for the exact contact and observation that survived the
    # existing verification guards. Do not change existing evidence or order.
    for name, collection, identity, keys in (
        ("phone", "phone_quality", "phone", ("phone_e164", "phone_valid", "phone_line_type", "phone_region")),
        ("email", "email_quality", "email", ("email_syntax_ok", "email_disposable")),
    ):
        if not result[name].get("verified"):
            continue
        quality = (profile.get(collection) or {}).get("value") or []
        if not isinstance(quality, list):
            continue
        first = (result[name].get("items") or [result[name]])[0]
        primary = first.get("value")
        primary = primary[0] if isinstance(primary, list) and primary else primary
        for candidate in quality:
            observed = candidate.get(identity)
            same = phone(observed) == phone(primary) if name == "phone" else str(observed).lower() == str(primary).lower()
            if not same:
                continue
            evidence = candidate.get(keys[0]) or {}
            if evidence.get("source_url") != first.get("source_url") or evidence.get("checked_at") != first.get("checked_at"):
                continue
            for key in keys:
                item = candidate.get(key)
                if (isinstance(item, dict) and item.get("source_url") == first.get("source_url")
                        and item.get("checked_at") == first.get("checked_at")):
                    result[key] = {**item, "verified": True, "reason": None, "contact_value": primary}
            break
    # Optional passive signals retain the observation date and exact source evidence.
    # Keep the legacy field set unchanged until an approved signals pass has run.
    if passes.get("2b_signals"):
        for name in SITE_SIGNAL_FIELDS:
            candidate = profile.get(name)
            if not isinstance(candidate, dict):
                continue
            evidence = candidate.get("evidence") or []
            source, checked = public_url(candidate.get("source_url")), candidate.get("checked_at")
            valid = bool(source and checked and evidence and candidate.get("value") is not None
                         and all(isinstance(item, dict) and public_url(item.get("source_url"))
                                 and item.get("text") and item.get("checked_at") for item in evidence))
            result[name] = {**field(candidate.get("value"), source, checked),
                            "verified": valid, "reason": None if valid else field()["reason"],
                            "confidence": candidate.get("confidence", 0), "source": candidate.get("source"),
                            "evidence": evidence}
    return result
