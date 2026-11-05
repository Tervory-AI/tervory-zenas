"""Public access fallbacks for Base. Never receive portal credentials.

Retries and archives are free. An exhausted fallback returns to the existing
pipeline, which already owns the paid continuation and its receipts.
"""
from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}: self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}: self.hidden = max(0, self.hidden-1)

    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


async def scrape_with_archives(run, http, base, headers, tenant, payload):
    result = await run()
    spent = float(result.get("_cost_usd") or 0)
    # Human checks take the queue. Archives are for unreachable/dead sites only.
    def failed(value):
        return not value.get("text") and value.get("error") and not value.get("blocked_by_check") and not value.get("challenge") and not any(word in str(value.get("error", "")).lower() for word in ("human", "captcha", "blocked", "login"))
    if not failed(result): return result
    for delay in (1, 3):
        await asyncio.sleep(delay)
        result = await run()
        spent += float(result.get("_cost_usd") or 0)
        result["_cost_usd"] = spent
        if not failed(result): return result
    try:
        response = await http.post(base + "/v1/access/archives", json={"tenant_id": tenant, "url": payload.get("website", "")}, headers=headers, timeout=90)
    except Exception:
        return {**result, "access_error": "Archived sources are unavailable.", "next": "existing_paid_rung"}
    if response.status_code != 200:
        return {**result, "access_error": "Archived sources are unavailable.", "next": "existing_paid_rung"}
    archive = response.json()
    if not archive.get("found"):
        return {**result, "access_sources": archive}
    parser = Text()
    parser.feed(archive["html"])
    text = re.sub(r"\s+", " ", " ".join(parser.parts))[:6000]
    # Historical contact details are deliberately not promoted to live fields.
    return {**result, "text": text, "pages": [], "error": None, "archived": True,
            "via": "archived, dated", "archive_date": archive["date"], "archive_source": archive["source"]}


async def public_business_pages(run, http, base, headers, tenant, payload):
    """Enrich search evidence only with the approved public-business API."""
    from urllib.parse import urlsplit
    result = await run()
    official = {}
    for platform in ("facebook", "instagram"):
        value = (payload.get("socials") or {}).get(platform)
        if not isinstance(value, str): continue
        parsed = urlsplit(value)
        if parsed.hostname not in {platform + ".com", "www." + platform + ".com"}: continue
        name = parsed.path.strip("/").split("/")[0]
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", name): continue
        try:
            response = await http.post(base + "/v1/access/social", headers=headers, timeout=40,
                json={"tenant_id": tenant, "platform": platform, "business_id": name,
                      "business_username": name if platform == "instagram" else ""})
        except Exception:
            result["access_error"] = "The public business API is unavailable; search evidence was kept."
            continue
        if response.status_code != 200:
            result["access_error"] = "The public business API is unavailable; search evidence was kept."
            continue
        data = response.json()
        if data.get("enabled"): official[platform] = data["page"]
    if official: result["official_business_pages"] = official
    return result
