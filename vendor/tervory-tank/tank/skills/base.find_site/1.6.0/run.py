"""base.find_site 1.6.0 — Pass 2a. Find the website.

Order: a verified hint on the record → Tervory's own search (SearXNG, no key, no per-query cost)
→ Brave when a key is present → domain guesses. Every candidate must answer and, unless it came
from a verified hint, its page must mention the business name (the distinctive words, not the
generic ones). Generic words and blocked hosts come from the caller. This package ships with a
neutral default, so a directory or a parked domain never counts and a trade list is not applied
unless the caller passes it in.

A distinctive word counts only as a whole host label. One leftover word does not accept a longer
domain that merely starts with it. A guess whose host is exactly a one-word name can stand on
that name when the page names the business, even with no phone, ZIP, or street on the record."""

import re
from urllib.parse import urlparse

# Directories, social sites, and marketplaces. A caller passes any further hosts.
NEUTRAL_SKIP_HOSTS = ("facebook.com", "instagram.com", "yelp.com", "yellowpages.com", "bbb.org", "linkedin.com",
                      "mapquest.com", "google.com", "youtube.com", "twitter.com", "x.com", "manta.com", "bizapedia.com",
                      "opencorporates.com", "dnb.com", "zoominfo.com", "apollo.io",
                      "wikipedia.org", "wikimedia.org", "reddit.com", "amazon.com", "ebay.com", "tripadvisor.com",
                      "justia.com", "buzzfile.com", "chamberofcommerce.com", "nextdoor.com", "angi.com")
SKIP_SUFFIXES = (".gov", ".edu", ".mil", ".k12.us", ".state.us")   # public bodies and schools are never the business's own site


def _extra_hosts(value: object) -> tuple[str, ...]:
    """Host fragments the caller asked us to skip, on top of the neutral list."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return ()
    out: list[str] = []
    seen = set(NEUTRAL_SKIP_HOSTS)
    for item in value:
        host = _host(str(item))
        if host and host not in seen:
            seen.add(host)
            out.append(host)
    return tuple(out)


def _hosts(payload: dict) -> tuple[str, ...]:
    return NEUTRAL_SKIP_HOSTS + _extra_hosts(payload.get("blocked_hosts"))


def _skip(host: str, hosts: tuple[str, ...]) -> bool:
    return not host or any(s in host for s in hosts) or host.endswith(SKIP_SUFFIXES)


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


# Legal endings and ordinary business words. A caller passes any further words.
NEUTRAL_GENERIC = {"the", "llc", "inc", "co", "corp", "corporation", "company", "ltd", "and", "of",
                   "supply", "supplies", "sports", "shop", "store", "trading", "post", "enterprises", "services",
                   "group", "usa", "america", "general", "center", "centre", "sales", "international", "industries",
                   "associates", "son", "sons", "brothers", "custom", "security", "hardware", "exchange", "solutions",
                   "products", "manufacturing", "works"}


def _extra_words(value: object) -> set[str]:
    """Words the caller asked us to treat as generic, on top of the neutral list."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return set()
    out: set[str] = set()
    for item in value:
        out.update(re.sub(r"[^a-z0-9 ]", " ", str(item).lower()).split())
    return out


def _ignore(payload: dict) -> set[str]:
    return set(NEUTRAL_GENERIC) | _extra_words(payload.get("generic_words"))


STATE_NAMES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california", "co": "colorado", "ct": "connecticut",
    "de": "delaware", "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa",
    "ks": "kansas", "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland", "ma": "massachusetts", "mi": "michigan",
    "mn": "minnesota", "ms": "mississippi", "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada", "nh": "new hampshire",
    "nj": "new jersey", "nm": "new mexico", "ny": "new york", "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma",
    "or": "oregon", "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina", "sd": "south dakota", "tn": "tennessee",
    "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia", "wa": "washington", "wv": "west virginia", "wi": "wisconsin",
    "wy": "wyoming", "dc": "district of columbia", "pr": "puerto rico", "gu": "guam", "vi": "virgin islands",
}
DIRECTIONS = {"n", "s", "e", "w", "ne", "nw", "se", "sw", "north", "south", "east", "west"}
CONTACT_PATHS = ("/contact", "/contact-us")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?(\d{3})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})(?!\d)")


def _words(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()


def _name_tokens(name: str) -> list[str]:
    base = re.sub(r"[^a-z0-9 ]", "", name.lower())
    return [t for t in base.split() if t not in {"the", "llc", "inc", "co", "corp", "company", "ltd"}]


class Who:
    """The business, as a page has to show it. Name words that are only the town or the state never
    identify a business ("Kauai Tile Company" is not kauai.com). Location proof comes from the record:
    its phone, ZIP or street address appearing on the site."""

    def __init__(self, name: str, city: str = "", state: str = "", phone: str = "", zip_code: str = "", address: str = "",
                 ignore: set[str] | None = None):
        toks = _name_tokens(name)
        st = state.strip().lower()
        place = set(_words(city)) | {st} | set(_words(STATE_NAMES.get(st, st))) | {"county"}
        skipped = NEUTRAL_GENERIC if ignore is None else ignore
        self.distinct = [t for t in toks if len(t) >= 4 and t not in skipped and t not in place]
        self.words = self.distinct[:3] or [t for t in toks if len(t) >= 3][:3]
        self.joined = "".join(toks) if len("".join(toks)) >= 5 else ""
        digits = re.sub(r"\D", "", phone or "")
        self.phone = digits[-10:] if len(digits) >= 10 else ""
        z = re.sub(r"\D", "", zip_code or "")
        self.zip = z[:5] if len(z) >= 5 else ""
        a = _words(address or "")
        self.street_no = a[0] if a and a[0].isdigit() else ""
        rest = [w for w in a[1:] if w not in DIRECTIONS and not w.isdigit()]
        self.street_word = rest[0] if self.street_no and rest else ""
        self.has_location = bool(self.phone or self.zip or self.street_word)

    @property
    def strong(self) -> bool:
        """Two or more distinctive words: specific enough that a matching domain is very likely theirs."""
        return len(self.distinct) >= 2


class Page:
    def __init__(self, html: str = "", title: str | None = None, text: str | None = None):
        if title is None or text is None:
            title, text = _visible(html)
        self.title, self.text = title, text
        raw = (html[:400_000] if html else "") + " " + text
        self.phones = {"".join(m.groups()) for m in PHONE_RE.finditer(raw)}


def _visible(html: str) -> tuple[str, str]:
    """(title, visible text) from raw HTML, lower-case, scripts and styles removed."""
    low = html[:400_000].lower()
    m = re.search(r"<title[^>]*>(.*?)</title>", low, flags=re.S)
    title = " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split()) if m else ""
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>|<[^>]+>", " ", low, flags=re.S)
    return title, " ".join(text.split())


def _has_word(text: str, w: str) -> bool:
    return bool(w) and re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", text) is not None


def _mentions_all(text: str, words: list[str]) -> bool:
    return bool(words) and all(_has_word(text, w) for w in words)


def _name_labels(host: str) -> list[str]:
    """Host labels, minus a leading www and the public suffix. smith-wesson.com is smith and wesson."""
    parts = [part for part in re.split(r"[.\-]", host.lower()) if part]
    if parts and parts[0] == "www":
        parts = parts[1:]
    if len(parts) >= 2:
        parts = parts[:-1]
    return parts


def _host_has_name(host: str, who: Who) -> bool:
    """The host is this business, judging each label as a whole word.

    Two or more distinctive words may each be their own label, or the run-together name may be
    the whole label. One distinctive word counts only when that word is the entire name and the
    host is that word. A longer label that only starts with the word does not count, and a
    one-word host does not count when the name still has other words.
    """
    labels = _name_labels(host)
    if not labels or not who.distinct:
        return False
    bare = "".join(labels)
    if who.strong:
        folded = set(labels)
        if all(word in folded for word in who.distinct):
            return True
        return bare == "".join(who.distinct) or (bool(who.joined) and bare == who.joined)
    if len(who.distinct) != 1 or who.joined != who.distinct[0]:
        return False
    return labels == [who.distinct[0]]


def _named(host: str, page: Page, who: Who) -> bool:
    """The page names the business (every distinctive word, as whole visible words) and the site points
    back to that name. A title can stand in for the domain only when two or more distinctive words
    are in it. One leftover word in a title is not enough."""
    if not _mentions_all(page.text + " " + page.title, who.words):
        return False
    if _host_has_name(host, who):
        return True
    return who.strong and _mentions_all(page.title, who.words)


def _located(page: Page, who: Who) -> str | None:
    """Which piece of the record's own location the page shows, if any."""
    if who.phone and who.phone in page.phones:
        return "phone"
    if who.zip and _has_word(page.text, who.zip):
        return "zip"
    if who.street_word and _has_word(page.text, who.street_no) and _has_word(page.text, who.street_word):
        return "street"
    return None


async def _get(ctx, url: str) -> tuple[Exception | None, int | None, str | None, str]:
    try:
        r = await ctx.http.get(url, follow_redirects=True, timeout=8)
        return None, r.status_code, r.text, str(r.url)
    except Exception as e:  # noqa: BLE001
        return e, None, None, url


async def _contact_proof(ctx, root: str, who: Who) -> str | None:
    for path in CONTACT_PATHS:
        exc, status, body, _ = await _get(ctx, root.rstrip("/") + path)
        if exc is None and status is not None and status < 400 and body:
            ev = _located(Page(body), who)
            if ev:
                return ev
    return None


async def _check(ctx, url: str, who: Who, need_location: bool, budget: dict) -> tuple[str | None, str, str | None]:
    """(final URL or None, how, evidence). how: fetch | tiger | challenge | none."""
    exc, status, body, final = await _get(ctx, url)
    page, how = None, "fetch"
    if exc is None and status is not None and status < 400 and body is not None:
        p = Page(body)
        if _named(_host(final), p, who):
            page = p
    if page is None:
        if not _worth_tiger(exc, status, body) or budget.get("tiger", 0) <= 0:
            return None, "none", None
        budget["tiger"] -= 1
        t = await _tiger(ctx, url)
        if not t or t.get("challenge"):
            return None, ("challenge" if t and t.get("challenge") else "none"), None
        if not t.get("ok") or (t.get("status") or 200) >= 400:
            return None, "none", None
        final = t.get("final_url") or url
        p = Page(t.get("html") or "", " ".join((t.get("title") or "").lower().split()), " ".join((t.get("text") or "").lower().split()))
        if not _named(_host(final), p, who):
            return None, "none", None
        page, how = p, "tiger"
    ev = _located(page, who)
    if ev is None and who.has_location:
        ev = await _contact_proof(ctx, _root(final), who)
    if ev:
        return _final(final), how, ev
    if not need_location:
        return _final(final), how, "name"
    return None, "none", None


def _guesses(name: str) -> list[str]:
    toks = _name_tokens(name)
    if not toks:
        return []
    return [f"https://{g}.com" for g in dict.fromkeys(("".join(toks), "-".join(toks)))]


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
    hosts = _hosts(payload)
    who = Who(name, city, state, str(payload.get("phone") or ""), str(payload.get("zip") or ""), str(payload.get("address") or ""),
              ignore=_ignore(payload))
    hint_blocked = bool(hint) and _skip(_host(hint), hosts)

    def finish(body: dict) -> dict:
        body["name_words"] = list(who.distinct)
        body["hint_blocked"] = hint_blocked
        return body

    # 1. A website on the input record, if it actually answers. It came with the record, so it is not name-checked.
    if hint and not hint_blocked:
        url = hint if "://" in hint else f"https://{hint}"
        exc, status, body, final = await _get(ctx, url)
        if exc is None and status is not None and status < 400:
            return finish({"website": {"value": _final(final), "source": "input:verified", "confidence": 85}, "evidence": "input"})
        if _worth_tiger(exc, status, body):
            t = await _tiger(ctx, url)
            if t and t.get("ok") and not t.get("challenge") and (t.get("status") or 200) < 400:
                return finish({"website": {"value": _final(t.get("final_url") or url), "source": "input:verified+tiger", "confidence": 85}, "evidence": "input"})

    blocked: list[str] = []   # live sites that kept an interactive human check up; recorded, not forced
    query = f"{name} {city} {state}".strip()
    # A search result must show the record's phone, ZIP or street. When the record has none of those,
    # the host itself has to be the business's name: a whole label, not a longer word that starts with it.
    search_needs_location = who.has_location

    # 2. Tervory's own search, then Brave when a key is present.
    seen: set[str] = set()
    budget = {"tiger": TIGER_BUDGET}
    for engine, results in (("searxng", _searx), ("brave", _brave)):
        for url in (await results(ctx, query))[:SEARCH_CANDIDATES]:
            h = _host(url)
            if h in seen or _skip(h, hosts):
                continue
            seen.add(h)
            if not who.has_location and not _host_has_name(h, who):
                continue
            final, how, ev = await _check(ctx, _root(url), who, search_needs_location, budget)
            if final:
                conf = 80 if ev in ("phone", "zip", "street") else 65
                return finish({"website": {"value": final, "source": f"search:{engine}" + ("+tiger" if how == "tiger" else ""), "confidence": conf},
                               "evidence": ev})
            if how == "challenge":
                blocked.append(_root(url))

    # 3. Domain guesses from the name. A short, generic, or leftover name needs location proof.
    # Two or more distinctive words can stand on the name. So can one word when it is the entire
    # name and the guessed host is that word.
    for guess in _guesses(name):
        stand_on_name = who.strong or _host_has_name(_host(guess), who)
        final, how, ev = await _check(ctx, guess, who, need_location=not stand_on_name, budget={"tiger": 1})
        if final:
            conf = 75 if ev in ("phone", "zip", "street") else 55
            return finish({"website": {"value": final, "source": "guess:domain" + ("+tiger" if how == "tiger" else ""), "confidence": conf},
                           "evidence": ev})
        if how == "challenge":
            blocked.append(guess)

    out: dict = {"website": {"value": None, "source": "none", "confidence": 0}}
    if blocked:
        out["blocked_by_check"] = blocked
    return finish(out)
