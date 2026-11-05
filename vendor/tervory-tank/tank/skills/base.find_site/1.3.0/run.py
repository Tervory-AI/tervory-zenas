"""base.find_site 1.3.0 — Pass 2a. Find the website.

Order: a verified hint on the record → Tervory's own search (SearXNG, no key, no per-query cost)
→ Brave when a key is present → domain guesses. Every candidate must answer and, unless it came
from a verified hint, its page must mention the business name (the distinctive words, not just
"firearms"), so directories and parked domains never count."""

import re
from urllib.parse import urlparse

SKIP_HOSTS = ("facebook.com", "instagram.com", "yelp.com", "yellowpages.com", "bbb.org", "linkedin.com",
              "mapquest.com", "google.com", "youtube.com", "twitter.com", "x.com", "manta.com", "bizapedia.com",
              "opencorporates.com", "dnb.com", "zoominfo.com", "apollo.io", "gunbroker.com", "armslist.com",
              "wikipedia.org", "wikimedia.org", "reddit.com", "amazon.com", "ebay.com", "tripadvisor.com",
              "atf.gov", "justia.com", "buzzfile.com", "chamberofcommerce.com", "nextdoor.com", "angi.com")
SKIP_SUFFIXES = (".gov", ".edu", ".mil", ".k12.us", ".state.us")   # public bodies and schools are never a dealer's own site


def _skip(host: str) -> bool:
    return not host or any(s in host for s in SKIP_HOSTS) or host.endswith(SKIP_SUFFIXES)


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


async def _alive(ctx, url: str, must_mention: "Mention | None" = None, budget: dict | None = None) -> tuple[str | None, str]:
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
        if r.status_code < 400:
            if must_mention is None:
                return _final(r.url), "fetch"
            title, text = _visible(r.text)
            if _belongs(_host(str(r.url)), title, text, must_mention):
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
    final = t.get("final_url") or url
    if must_mention is not None:
        text = " ".join((t.get("text") or "").lower().split())
        title = " ".join((t.get("title") or "").lower().split())
        if not _belongs(_host(final), title, text, must_mention):
            return None, "none"
    return _final(final), "tiger"


GENERIC = {"the", "llc", "inc", "co", "corp", "corporation", "company", "ltd", "and", "of", "firearms", "firearm", "guns",
           "gun", "arms", "ammo", "ammunition", "sporting", "goods", "outdoors", "outdoor", "supply", "supplies", "tactical",
           "shooting", "sports", "shop", "store", "trading", "post", "pawn", "enterprises", "services", "group", "usa", "america"}


def _name_tokens(name: str) -> list[str]:
    base = re.sub(r"[^a-z0-9 ]", "", name.lower())
    return [t for t in base.split() if t not in {"the", "llc", "inc", "co", "corp", "company", "ltd"}]


class Mention:
    """What a page must show to count as this business."""

    def __init__(self, name: str, city: str = ""):
        toks = _name_tokens(name)
        distinct = [t for t in toks if len(t) >= 4 and t not in GENERIC]
        self.weak = not distinct
        # A weak name ("PIA Firearms", "WGS") falls back to all its words of 3+ letters, generic or not.
        self.words = distinct[:3] if distinct else [t for t in toks if len(t) >= 3][:3]
        self.joined = "".join(toks) if len("".join(toks)) >= 5 else ""
        self.city = " ".join(re.sub(r"[^a-z0-9 ]", "", city.lower()).split())


def _mention_tokens(name: str) -> list[str]:
    return Mention(name).words


def _visible(html: str) -> tuple[str, str]:
    """(title, visible text) from raw HTML, lower-case, scripts and styles removed."""
    low = html[:400_000].lower()
    m = re.search(r"<title[^>]*>(.*?)</title>", low, flags=re.S)
    title = " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split()) if m else ""
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>|<[^>]+>", " ", low, flags=re.S)
    return title, " ".join(text.split())


def _has_word(text: str, w: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", text) is not None


def _mentions_all(text: str, words: list[str]) -> bool:
    return bool(words) and all(_has_word(text, w) for w in words)


def _belongs(host: str, title: str, text: str, m: "Mention") -> bool:
    """Does this page belong to the business? Every distinctive word must be on the page as a word,
    and the site itself must point back to the name: a distinctive word (or the run-together name)
    in the domain, or the whole name in the page title. A weak name (no distinctive word) also needs
    the town on the page."""
    if not _mentions_all(text + " " + title, m.words):
        return False
    h = host.replace("-", "")
    linked = any(w in h for w in m.words if len(w) >= 4) or (m.joined and m.joined in h) \
        or _mentions_all(title, m.words)
    if not linked:
        return False
    if m.weak:
        return bool(m.city) and _has_word(text + " " + title, m.city)
    return True


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
    if hint and not _skip(_host(hint)):
        final, how = await _alive(ctx, hint if "://" in hint else f"https://{hint}", budget={"tiger": 1})
        if final:
            return {"website": {"value": final, "source": "input:verified" + ("+tiger" if how == "tiger" else ""), "confidence": 85}}

    mention = Mention(name, city)
    budget = {"tiger": TIGER_BUDGET}
    blocked: list[str] = []   # live sites that kept an interactive human check up; recorded, not forced
    query = f"{name} {city} {state}".strip()

    # 2. Tervory's own search. A result counts only if it is live and its page names the business.
    seen: set[str] = set()
    for url in (await _searx(ctx, query))[:SEARCH_CANDIDATES]:
        h = _host(url)
        if h in seen or _skip(h):
            continue
        seen.add(h)
        final, how = await _alive(ctx, _root(url), must_mention=mention, budget=budget)
        if final:
            return {"website": {"value": final, "source": "search:searxng" + ("+tiger" if how == "tiger" else ""), "confidence": 75}}

    # 3. Brave, when a key is present.
    for url in (await _brave(ctx, query))[:SEARCH_CANDIDATES]:
        h = _host(url)
        if h in seen or _skip(h):
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
