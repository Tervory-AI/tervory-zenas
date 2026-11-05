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


TIGER_STATUSES = {403, 429, 503, 520, 521, 522, 523, 524, 525, 526}
JS_SHELL_HINTS = ("enable javascript", "<noscript", "__next", "id=\"root\"", "id=\"app\"", "wix.com", "squarespace")


def _worth_tiger(exc: Exception | None, status: int | None, body: str | None) -> bool:
    """Only send a site to the browser when a plain fetch saw a live site it could not read:
    a timeout, a protective status, or a JavaScript shell. A domain that does not exist never goes."""
    if exc is not None:
        name, msg = type(exc).__name__, str(exc).lower()
        if "name or service not known" in msg or "nodename" in msg or "no address" in msg or "getaddrinfo" in msg:
            return False
        return "Timeout" in name or "RemoteProtocol" in name or "ReadError" in name
    if status is not None and status in TIGER_STATUSES:
        return True
    if body is not None:
        # A full page that simply does not name the business is an answer, not a reason to look harder.
        # Only a thin page (or a JavaScript shell with little visible text) goes to the browser.
        low = body[:200_000].lower()
        visible = len(" ".join(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", low, flags=re.S).split()))
        return visible < 1500 or (visible < 4000 and any(h in low for h in JS_SHELL_HINTS))
    return False


async def _tiger(ctx, url: str) -> dict | None:
    base = ctx.env.get("TIGER_URL") if ctx is not None else None
    if not base:
        return None
    try:
        r = await ctx.http.post(f"{base.rstrip('/')}/render", json={"url": url, "settle_ms": 10_000, "max_ms": 25_000}, timeout=50)
        return r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


TIGER_BUDGET = 1       # browser loads across all search results for one record; each can take 10-25 seconds
SEARCH_CANDIDATES = 6  # search results checked per engine


async def _alive(ctx, url: str, must_mention: list[str] | None = None, budget: dict | None = None) -> tuple[str | None, str]:
    """(final URL or None, how). A plain fetch first; when it saw a live site it could not read,
    Tiger loads the page in a real browser and waits for it to settle. With must_mention, the
    page text has to name the business, so parked or unrelated domains never count."""
    exc, status, body = None, None, None
    try:
        if must_mention is None:
            r = await ctx.http.head(url, follow_redirects=True, timeout=8)
            if r.status_code < 400:
                return _final(r.url), "fetch"
        r = await ctx.http.get(url, follow_redirects=True, timeout=8)
        status, body = r.status_code, r.text
        if r.status_code < 400 and (not must_mention or _mentions_all(r.text[:200_000].lower(), must_mention)):
            return _final(r.url), "fetch"
    except Exception as e:  # noqa: BLE001
        exc = e
    if not _worth_tiger(exc, status, body):
        return None, "none"
    if budget is not None:
        if budget.get("tiger", 0) <= 0:
            return None, "none"
        budget["tiger"] -= 1
    t = await _tiger(ctx, url)
    if not t or t.get("challenge"):
        return None, "challenge" if t and t.get("challenge") else "none"
    if not t.get("ok") or (t.get("status") or 200) >= 400:
        return None, "none"
    if must_mention and not _mentions_all(((t.get("text") or "") + " " + (t.get("title") or "")).lower(), must_mention):
        return None, "none"
    return _final(t.get("final_url") or url), "tiger"


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
        final, how = await _alive(ctx, hint if "://" in hint else f"https://{hint}", budget={"tiger": 1})
        if final:
            return {"website": {"value": final, "source": "input:verified" + ("+tiger" if how == "tiger" else ""), "confidence": 85}}

    mention = _mention_tokens(name)
    budget = {"tiger": TIGER_BUDGET}
    blocked: list[str] = []   # live sites that kept an interactive human check up; recorded, not forced
    query = f"{name} {city} {state}".strip()

    # 2. Tervory's own search. A result counts only if it is live and its page names the business.
    seen: set[str] = set()
    for url in (await _searx(ctx, query))[:SEARCH_CANDIDATES]:
        h = _host(url)
        if not h or h in seen or any(s in h for s in SKIP_HOSTS):
            continue
        seen.add(h)
        final, how = await _alive(ctx, _root(url), must_mention=mention, budget=budget)
        if final:
            return {"website": {"value": final, "source": "search:searxng" + ("+tiger" if how == "tiger" else ""), "confidence": 75}}

    # 3. Brave, when a key is present.
    for url in (await _brave(ctx, query))[:SEARCH_CANDIDATES]:
        h = _host(url)
        if not h or h in seen or any(s in h for s in SKIP_HOSTS):
            continue
        seen.add(h)
        final, how = await _alive(ctx, url, must_mention=mention, budget=budget)
        if final:
            return {"website": {"value": final, "source": "search:brave" + ("+tiger" if how == "tiger" else ""), "confidence": 75}}

    # 4. Domain guesses from the name; the page must mention the name to count.
    for guess in _guesses(name):
        final, how = await _alive(ctx, guess, must_mention=mention, budget={"tiger": 1})
        if final:
            return {"website": {"value": final, "source": "guess:domain" + ("+tiger" if how == "tiger" else ""), "confidence": 55}}
        if how == "challenge":
            blocked.append(guess)

    out = {"website": {"value": None, "source": "none", "confidence": 0}}
    if blocked:
        out["blocked_by_check"] = blocked
    return out
