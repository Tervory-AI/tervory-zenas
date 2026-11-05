"""base.find_site — Pass 2a. Find the website. Code first; a search API when a key is present."""

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
            if not any(w in body for w in must_mention):
                return None
        return _final(r.url)
    except Exception:  # noqa: BLE001
        return None


def _name_tokens(name: str) -> list[str]:
    base = re.sub(r"[^a-z0-9 ]", "", name.lower())
    return [t for t in base.split() if t not in {"the", "llc", "inc", "co", "corp", "company", "ltd"}]


def _guesses(name: str) -> list[str]:
    toks = _name_tokens(name)
    if not toks:
        return []
    return [f"https://{g}.com" for g in ("".join(toks), "-".join(toks))]


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

    # 2. Search, when we have a key.
    for url in await _brave(ctx, f"{name} {city} {state}".strip()):
        h = _host(url)
        if not h or any(s in h for s in SKIP_HOSTS):
            continue
        final = await _alive(ctx, url)
        if final:
            return {"website": {"value": final, "source": "search:brave", "confidence": 75}}

    # 3. Domain guesses from the name; the page must mention the name to count.
    mention = [t for t in _name_tokens(name) if len(t) >= 4][:3] or _name_tokens(name)[:1]
    for guess in _guesses(name):
        final = await _alive(ctx, guess, must_mention=mention)
        if final:
            return {"website": {"value": final, "source": "guess:domain", "confidence": 55}}

    return {"website": {"value": None, "source": "none", "confidence": 0}}
