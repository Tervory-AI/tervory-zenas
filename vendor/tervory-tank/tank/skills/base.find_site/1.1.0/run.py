"""base.find_site 1.1.0 — Pass 2a. Find the website.

Order: a verified hint on the record → Tervory's own search (SearXNG, no key, no per-query cost)
→ Brave when a key is present → domain guesses. Every candidate must answer and, unless it came
from a verified hint, its page must mention the business name (the distinctive words, not just
"firearms"), so directories and parked domains never count."""

import re
from urllib.parse import urlparse

SKIP_HOSTS = ("facebook.com", "instagram.com", "yelp.com", "yellowpages.com", "bbb.org", "linkedin.com",
              "mapquest.com", "google.com", "youtube.com", "twitter.com", "x.com", "manta.com", "bizapedia.com",
              "opencorporates.com", "dnb.com", "zoominfo.com", "apollo.io", "gunbroker.com", "armslist.com")


def _host(url: str) -> str:
    try:
        return urlparse(url if "://" in url else f"https://{url}").netloc.lower().removeprefix("www.")
    except Exception:  # noqa: BLE001
        return ""


def _root(url: str) -> str:
    u = urlparse(url if "://" in url else f"https://{url}")
    return f"{u.scheme}://{u.netloc}"


def _final(url: str) -> str:
    """Canonical form: no trailing slash when the path is just '/'. Match keys depend on it."""
    url = str(url)
    parsed = urlparse(url)
    if parsed.path in ("", "/") and not parsed.query and not parsed.fragment:
        return url.rstrip("/")
    return url


async def _alive(ctx, url: str, must_mention: list[str] | None = None) -> str | None:
    """Return the final URL if the site answers 2xx/3xx. With must_mention, the page body
    has to contain one of those words, so a parked or unrelated domain does not count."""
    try:
        if must_mention is None:
            r = await ctx.http.head(url, follow_redirects=True, timeout=8)
            if r.status_code < 400:
                return _final(r.url)
        r = await ctx.http.get(url, follow_redirects=True, timeout=8)
        if r.status_code >= 400:
            return None
        if must_mention:
            body = r.text[:200_000].lower()
            if not _mentions_all(body, must_mention):
                return None
        return _final(r.url)
    except Exception:  # noqa: BLE001
        return None


GENERIC = {"the", "llc", "inc", "co", "corp", "corporation", "company", "ltd", "and", "of", "firearms", "firearm", "guns",
           "gun", "arms", "ammo", "ammunition", "sporting", "goods", "outdoors", "outdoor", "supply", "supplies", "tactical",
           "shooting", "sports", "shop", "store", "trading", "post", "pawn", "enterprises", "services", "group", "usa", "america"}


def _name_tokens(name: str) -> list[str]:
    base = re.sub(r"[^a-z0-9 ]", "", name.lower())
    return [t for t in base.split() if t not in {"the", "llc", "inc", "co", "corp", "company", "ltd"}]


def _mention_tokens(name: str) -> list[str]:
    """What a page must contain to count as this business: the distinctive words (≥4 chars, not generic).
    If the name is all generic words, fall back to the two longest words together."""
    toks = _name_tokens(name)
    distinct = [t for t in toks if len(t) >= 4 and t not in GENERIC]
    if distinct:
        return distinct[:3]
    return sorted((t for t in toks if len(t) >= 3), key=len, reverse=True)[:2]


def _mentions_all(body: str, words: list[str]) -> bool:
    return bool(words) and all(w in body for w in words)


def _guesses(name: str) -> list[str]:
    toks = _name_tokens(name)
    if not toks:
        return []
    return [f"https://{g}.com" for g in ("".join(toks), "-".join(toks))]


async def _searx(ctx, q: str) -> list[str]:
    """Tervory's own search. Returns candidate URLs in rank order, or [] when the engine is not configured."""
    base = ctx.env.get("SEARXNG_URL") if ctx is not None else None
    if not base:
        return []
    try:
        r = await ctx.http.get(f"{base.rstrip('/')}/search", params={"q": q, "format": "json", "language": "en-US", "safesearch": 0},
                               headers={"Accept": "application/json"}, timeout=12)
        if r.status_code != 200:
            return []
        return [x.get("url", "") for x in (r.json().get("results") or [])][:10]
    except Exception:  # noqa: BLE001
        return []


async def _brave(ctx, q: str) -> list[str]:
    key = ctx.env.get("BRAVE_API_KEY")
    if not key:
        return []
    try:
        r = await ctx.http.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": q, "count": 8, "country": "us"},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        return [x.get("url", "") for x in (r.json().get("web") or {}).get("results", [])]
    except Exception:  # noqa: BLE001
        return []


async def run(payload: dict, ctx) -> dict:
    name = str(payload.get("name") or "").strip()
    city = str(payload.get("city") or "").strip()
    state = str(payload.get("state") or "").strip()
    hint = str(payload.get("website") or "").strip()

    # 1. A website on the input record, if it actually answers.
    if hint and not any(h in _host(hint) for h in SKIP_HOSTS):
        final = await _alive(ctx, hint if "://" in hint else f"https://{hint}")
        if final:
            return {"website": {"value": final, "source": "input:verified", "confidence": 85}}

    mention = _mention_tokens(name)
    query = f"{name} {city} {state}".strip()

    # 2. Tervory's own search. A result counts only if it is live and its page names the business.
    seen: set[str] = set()
    for url in await _searx(ctx, query):
        h = _host(url)
        if not h or h in seen or any(s in h for s in SKIP_HOSTS):
            continue
        seen.add(h)
        final = await _alive(ctx, _root(url), must_mention=mention)
        if final:
            return {"website": {"value": final, "source": "search:searxng", "confidence": 75}}

    # 3. Brave, when a key is present.
    for url in await _brave(ctx, query):
        h = _host(url)
        if not h or h in seen or any(s in h for s in SKIP_HOSTS):
            continue
        seen.add(h)
        final = await _alive(ctx, url, must_mention=mention)
        if final:
            return {"website": {"value": final, "source": "search:brave", "confidence": 75}}

    # 4. Domain guesses from the name; the page must mention the name to count.
    for guess in _guesses(name):
        final = await _alive(ctx, guess, must_mention=mention)
        if final:
            return {"website": {"value": final, "source": "guess:domain", "confidence": 55}}

    return {"website": {"value": None, "source": "none", "confidence": 0}}
