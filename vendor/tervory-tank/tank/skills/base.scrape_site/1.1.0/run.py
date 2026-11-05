"""base.scrape_site 1.1.0 — Pass 2b. Fetch home + contact/about pages; extract contacts and text.
When the home page cannot be read by a plain fetch (slow, JavaScript-built, a self-clearing check),
Tiger renders it in a real browser. Interactive human checks are recorded, never solved."""

import re
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
SOCIAL = {"facebook": "facebook.com", "instagram": "instagram.com", "youtube": "youtube.com", "x": "x.com",
          "twitter": "twitter.com", "linkedin": "linkedin.com", "tiktok": "tiktok.com"}
CONTACT_WORDS = ("contact", "about", "location", "hours", "visit")
MAX_TEXT = 6000


async def _tiger_html(ctx, url: str) -> tuple[str | None, bool]:
    base = ctx.env.get("TIGER_URL") if ctx is not None else None
    if not base:
        return None, False
    try:
        r = await ctx.http.post(f"{base.rstrip('/')}/render", json={"url": url, "settle_ms": 10_000, "max_ms": 25_000}, timeout=50)
        if r.status_code != 200:
            return None, False
        j = r.json()
        if j.get("challenge"):
            return None, True
        return (j.get("html") if j.get("ok") else None), False
    except Exception:  # noqa: BLE001
        return None, False


def _visible_len(html: str) -> int:
    t = HTMLParser(html)
    for node in t.css("script, style, noscript, svg"):
        node.decompose()
    return len(" ".join((t.body.text(separator=" ") if t.body else "").split()))


async def _get(ctx, url: str) -> str | None:
    try:
        r = await ctx.http.get(url, follow_redirects=True, timeout=12)
        if r.status_code < 400 and "text/html" in r.headers.get("content-type", ""):
            return r.text[:600_000]
    except Exception:  # noqa: BLE001
        return None
    return None


def _clean_phone(p: str) -> str:
    d = re.sub(r"\D", "", p)
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return f"+1{d}" if len(d) == 10 else ""


async def run(payload: dict, ctx) -> dict:
    site = str(payload.get("website") or "").strip()
    if not site:
        return {"emails": [], "phones": [], "socials": {}, "text": "", "pages": []}
    if "://" not in site:
        site = f"https://{site}"

    home = await _get(ctx, site)
    via = "fetch"
    if home is None or _visible_len(home) < 200:
        rendered, blocked = await _tiger_html(ctx, site)
        if rendered:
            home, via = rendered, "tiger"
        elif blocked and home is None:
            return {"emails": [], "phones": [], "socials": {}, "text": "", "pages": [], "error": "blocked by human check", "via": "tiger"}
    if home is None:
        return {"emails": [], "phones": [], "socials": {}, "text": "", "pages": [], "error": "home page unreachable"}

    pages = [site]
    htmls = [home]
    tree = HTMLParser(home)
    base_host = urlparse(site).netloc
    # Follow up to 3 same-site links that look like contact/about pages.
    seen = set()
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        text = (a.text() or "").lower()
        if any(w in href.lower() or w in text for w in CONTACT_WORDS):
            full = urljoin(site, href)
            if urlparse(full).netloc == base_host and full not in seen and full != site:
                seen.add(full)
        if len(seen) >= 3:
            break
    for url in list(seen):
        h = await _get(ctx, url)
        if h:
            pages.append(url)
            htmls.append(h)

    emails: set[str] = set()
    phones: set[str] = set()
    socials: dict[str, str] = {}
    texts: list[str] = []
    for html in htmls:
        t = HTMLParser(html)
        for node in t.css("script, style, noscript, svg"):
            node.decompose()
        for a in t.css("a[href]"):
            href = a.attributes.get("href") or ""
            if href.lower().startswith("mailto:"):
                emails.add(href[7:].split("?")[0].strip().lower())
            for k, dom in SOCIAL.items():
                if dom in href and k not in socials:
                    socials[k] = href
        body_text = re.sub(r"\s+", " ", t.body.text(separator=" ") if t.body else "")
        emails.update(e.lower() for e in EMAIL.findall(body_text) if not e.lower().endswith((".png", ".jpg", ".svg")))
        phones.update(p for p in (_clean_phone(x) for x in PHONE.findall(body_text)) if p)
        texts.append(body_text)

    text = " ".join(texts)[:MAX_TEXT]
    return {"emails": sorted(emails), "phones": sorted(phones), "socials": socials, "text": text, "pages": pages, "via": via}
