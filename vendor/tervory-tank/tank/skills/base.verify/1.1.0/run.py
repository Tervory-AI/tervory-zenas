"""base.verify — Pass 7, verification and the confidence score. Pure code.

Cross-checks what the passes found against each other and against the input, flags what looks
wrong or stale, and assigns the record's 0-100 confidence per the Build Pack table:
  80-100 ready to use · 60-79 use with awareness · 40-59 flag for review · <40 re-queue

The score is built from weighted layers, each capped, so a record cannot look "ready" on one
strong field alone. Agreement between independent sources earns a bonus; disagreement costs.
"""

import re
from datetime import date, datetime, timezone
from urllib.parse import urlparse


def _digits(p) -> str:
    d = re.sub(r"\D", "", str(p or ""))
    return d[-10:] if len(d) >= 10 else d


def _domain(url: str) -> str:
    if not url:
        return ""
    return urlparse(url if "://" in url else f"https://{url}").netloc.lower().removeprefix("www.")


def _v(profile: dict, k: str, default=None):
    f = profile.get(k)
    if isinstance(f, dict) and "value" in f:
        return f["value"] if f["value"] not in (None, "", [], {}) else default
    return f if f not in (None, "", [], {}) else default


def _c(profile: dict, k: str) -> int:
    f = profile.get(k)
    return int(f.get("confidence") or 0) if isinstance(f, dict) else 0


async def run(payload: dict, ctx) -> dict:
    profile: dict = payload.get("profile") or {}
    raw: dict = payload.get("raw") or {}
    engine: dict = payload.get("engine_profile") or {}
    checks: dict[str, bool | None] = {}
    flags: list[str] = []

    # --- identity layer (max 20): name + city + state cleaned from the input
    ident = min(20, round((_c(profile, "name") + _c(profile, "city") + _c(profile, "state")) / 300 * 20))

    # --- registry layer (max 15): matched in the engine's list
    reg = _v(profile, "registry_match")
    registry = 15 if reg else 0
    if not reg:
        flags.append("no_registry_match")

    # --- web layer (max 25): a live website, more if it agrees with the input
    site = _v(profile, "website")
    web = 0
    if site:
        web = 15 if (profile.get("website") or {}).get("source", "").startswith(("input:verified", "search")) else 10
        site_phones = [_digits(p) for p in (_v(profile, "phones", []) or [])]
        in_phone = _digits(raw.get("phone") or raw.get("voice_phone"))
        if in_phone and site_phones:
            checks["phone_matches_site"] = in_phone in site_phones
            web += 10 if checks["phone_matches_site"] else -5
            if not checks["phone_matches_site"]:
                flags.append("phone_mismatch")
        elif site_phones:
            web += 3
        web = max(0, min(25, web))
    else:
        flags.append("no_website")

    # --- contacts layer (max 15): mail domain agrees with the site, people with addresses
    contacts = _v(profile, "contacts", {}) or {}
    ctc = 0
    if contacts:
        dom = contacts.get("domain")
        if site and dom:
            checks["email_domain_matches_site"] = (dom == _domain(site))
            if not checks["email_domain_matches_site"]:
                flags.append("email_domain_differs")
        if contacts.get("domain_accepts_mail") is False:
            flags.append("domain_rejects_mail")
        ctc += 6 if contacts.get("generic_emails") else 0
        ctc += 6 if contacts.get("personal_emails") else 0
        ctc += 3 if contacts.get("decision_maker") else 0
        if checks.get("email_domain_matches_site") is False:
            ctc -= 4
        ctc = max(0, min(15, ctc))

    # --- facts layer (max 10): extraction + socials + business data
    facts = 0
    facts += 4 if _v(profile, "description") else 0
    facts += 3 if _v(profile, "categories") else 0
    socials = _v(profile, "socials_profiles", {}) or {}
    facts += 3 if socials.get("count") else 0
    facts = min(10, facts)

    # --- vertical layer (max 15): engine facts; lapsed licences are a hard flag
    vert = 0
    if engine and engine.get("licence_type"):
        vert = 12
        if engine.get("lapsed"):
            flags.append("licence_lapsed")
            vert = 4
        elif engine.get("renewal_window"):
            flags.append("renewal_window")
        if engine.get("inventory_signal"):
            vert = min(15, vert + 3)

    # --- staleness: explicit recorded dates supplement the existing thin-site check.
    freshness = _v(profile, "site_freshness", {}) or {}
    today = date.fromisoformat(str(payload["as_of"])[:10]) if payload.get("as_of") else datetime.now(timezone.utc).date()
    dated = []
    if isinstance(freshness, dict):
        for key in ("last_modified", "newest_post"):
            item = freshness.get(key) or {}
            try:
                observed = date.fromisoformat(str(item.get("value"))[:10])
            except (ValueError, TypeError):
                continue
            if observed <= today and item.get("evidence"):
                dated.append(observed)
        # Copyright is a weak template signal, never proof of fresh editorial content.
        if dated:
            checks["site_dated_content_stale"] = (today - max(dated)).days > 730
            if site and checks["site_dated_content_stale"]:
                flags.append("site_thin_or_stale")

    # --- existing thin-site heuristic
    if site and "site_thin_or_stale" not in flags and not (_v(profile, "emails") or socials.get("count") or _v(profile, "description")):
        flags.append("site_thin_or_stale")

    score = max(0, min(100, ident + registry + web + ctc + facts + vert))
    tier = "ready" if score >= 80 else "use_with_awareness" if score >= 60 else "review" if score >= 40 else "requeue"
    return {
        "confidence": score,
        "tier": tier,
        "layers": {"identity": ident, "registry": registry, "web": web, "contacts": ctc, "facts": facts, "vertical": vert},
        "checks": checks,
        "flags": flags,
    }
