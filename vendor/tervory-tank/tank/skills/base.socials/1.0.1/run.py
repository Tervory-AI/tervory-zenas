"""base.socials — Pass 4, social media discovery. Pure code.

Takes the social links pass 2 saw on the site (and any on the input record), canonicalises each
to platform + profile URL + handle, drops share/login/generic links, and reports how many real
profiles the business has. Activity scoring (posts per week) needs platform APIs and is a later
version; this one establishes presence.
"""

from urllib.parse import urlparse

PLATFORMS = {
    "facebook": ("facebook.com", "fb.com", "fb.me"),
    "instagram": ("instagram.com",),
    "youtube": ("youtube.com", "youtu.be"),
    "x": ("x.com", "twitter.com"),
    "linkedin": ("linkedin.com",),
    "tiktok": ("tiktok.com",),
    "pinterest": ("pinterest.com",),
    "yelp": ("yelp.com",),
}
JUNK = ("/sharer", "/share", "/intent/", "/login", "/signup", "/dialog/", "/plugins/", "/hashtag/", "/search", "/tr?",
        "/embed", "/watch?", "/channel/UC_placeholder")
GENERIC_HANDLES = {"", "home", "pages", "profile.php", "p", "reel", "share", "sharer.php", "login", "company", "in",
                   "watch", "channel", "user", "c", "explore", "hashtag", "intent", "search"}


def _platform(host: str) -> str | None:
    host = host.lower().removeprefix("www.").removeprefix("m.")
    for p, doms in PLATFORMS.items():
        if any(host == d or host.endswith("." + d) for d in doms):
            return p
    return None


def _handle(platform: str, path: str) -> str | None:
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    if platform == "linkedin" and len(parts) >= 2 and parts[0] in ("company", "in", "school"):
        return parts[1]
    if platform == "youtube":
        if parts[0].startswith("@"):
            return parts[0]
        if parts[0] in ("channel", "c", "user") and len(parts) >= 2:
            return parts[1]
        return None
    if platform == "facebook" and parts[0] == "profile.php":
        return None
    h = parts[0]
    return None if h.lower() in GENERIC_HANDLES else h.lstrip("@")


def canon(url: str) -> dict | None:
    url = str(url or "").strip()
    if not url:
        return None
    if any(j in url for j in JUNK):
        return None
    u = urlparse(url if "://" in url else f"https://{url}")
    platform = _platform(u.netloc)
    if not platform:
        return None
    handle = _handle(platform, u.path)
    if not handle:
        return None
    clean = f"https://{u.netloc.lower().removeprefix('www.').removeprefix('m.')}{u.path.rstrip('/')}"
    return {"platform": platform, "url": clean, "handle": handle}


async def run(payload: dict, ctx) -> dict:
    links: list[str] = []
    s = payload.get("socials") or {}
    if isinstance(s, dict):
        links.extend(str(v) for v in s.values())
    elif isinstance(s, list):
        links.extend(str(v) for v in s)
    for k in ("facebook", "instagram", "youtube", "x", "twitter", "linkedin", "tiktok"):
        if payload.get(k):
            links.append(str(payload[k]))

    profiles: dict[str, dict] = {}
    for l in links:
        c = canon(l)
        if c and c["platform"] not in profiles:
            profiles[c["platform"]] = {"url": c["url"], "handle": c["handle"], "source": "site", "confidence": 85}
    n = len(profiles)
    return {
        "profiles": profiles,
        "platforms": sorted(profiles),
        "count": n,
        "presence": "none" if n == 0 else ("light" if n == 1 else ("active" if n <= 3 else "broad")),
        "confidence": 85 if n else 0,
    }
