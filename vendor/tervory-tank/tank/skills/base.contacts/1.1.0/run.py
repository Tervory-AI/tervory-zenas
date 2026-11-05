"""base.contacts 1.1.0 — Pass 3, contact discovery. Pure code.

From what pass 2 found (emails on the site, named people with roles, the website) produce:
  - generic inboxes (info@, sales@ …) kept separate from people
  - the email pattern the business uses, learned from its own addresses when a named person matches
  - a contact per named person, with an address observed on the site or derived from the pattern
  - the decision maker (owner / president / CEO / founder / GM) when one is named
  - whether the domain accepts mail at all (MX lookup over DNS-over-HTTPS; no key needed)

Extra generic inbox names come from the caller. This package ships with a neutral default
(info, sales, support, and the like). An address is personal unless its name is on that
default or the caller added it.

A derived address is labelled source="pattern" with lower confidence than one seen on the site.
Never invents a person.
"""

import re
from urllib.parse import urlparse

# Ordinary inboxes. A caller passes any further local-parts.
NEUTRAL_GENERIC = {"info", "sales", "contact", "hello", "support", "admin", "office", "service", "orders", "help",
                   "mail", "team", "shop", "store", "billing", "webmaster", "customerservice"}


def _extra_words(value: object) -> set[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return set()
    out: set[str] = set()
    for item in value:
        word = re.sub(r"[^a-z0-9]", "", str(item).lower())
        if word:
            out.add(word)
    return out


def _generic_names(payload: dict) -> set[str]:
    return set(NEUTRAL_GENERIC) | _extra_words(payload.get("generic_words"))


DECIDER = ("owner", "president", "ceo", "founder", "general manager", "gm", "managing", "principal", "partner", "director")
PATTERNS = {
    "first": lambda f, l: f,
    "first.last": lambda f, l: f"{f}.{l}",
    "firstlast": lambda f, l: f"{f}{l}",
    "flast": lambda f, l: f"{f[0]}{l}",
    "first_last": lambda f, l: f"{f}_{l}",
    "last": lambda f, l: l,
    "first.l": lambda f, l: f"{f}.{l[0]}",
    "firstl": lambda f, l: f"{f}{l[0]}",
}


def _domain(url: str) -> str:
    if not url:
        return ""
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    return host.removeprefix("www.")


def _split(name: str) -> tuple[str, str]:
    toks = [t for t in re.sub(r"[^a-z ]", "", name.lower()).split() if t not in {"jr", "sr", "ii", "iii", "mr", "mrs", "ms", "dr"}]
    if not toks:
        return "", ""
    return toks[0], (toks[-1] if len(toks) > 1 else "")


def learn_pattern(locals_: list[str], people: list[dict]) -> str | None:
    """Which pattern explains an observed personal address for a named person."""
    for p in people:
        f, l = _split(str(p.get("name") or ""))
        if not f:
            continue
        for pname, fn in PATTERNS.items():
            if not l and pname != "first":
                continue
            if fn(f, l) in locals_:
                return pname
    return None


async def mx_ok(ctx, domain: str) -> bool | None:
    """True/False if the lookup answered, None if it could not be made."""
    if not domain or ctx is None:
        return None
    try:
        r = await ctx.http.get("https://cloudflare-dns.com/dns-query", params={"name": domain, "type": "MX"},
                               headers={"accept": "application/dns-json"}, timeout=6)
        if r.status_code != 200:
            return None
        return bool(r.json().get("Answer"))
    except Exception:  # noqa: BLE001
        return None


async def run(payload: dict, ctx) -> dict:
    website = str(payload.get("website") or "")
    domain = _domain(website)
    emails = sorted({str(e).lower().strip() for e in (payload.get("emails") or []) if "@" in str(e)})
    people = [p for p in (payload.get("people") or []) if isinstance(p, dict) and p.get("name")]

    on_domain = [e for e in emails if domain and e.endswith("@" + domain)]
    pool = on_domain or emails
    names = _generic_names(payload)
    generic = [e for e in pool if e.split("@")[0] in names]
    personal = [e for e in pool if e not in generic]
    locals_ = [e.split("@")[0] for e in personal]

    pattern = learn_pattern(locals_, people)
    mail_domain = domain or (pool[0].split("@")[1] if pool else "")
    accepts = await mx_ok(ctx, mail_domain) if mail_domain else None

    contacts = []
    for p in people[:8]:
        f, l = _split(str(p.get("name") or ""))
        email, source, conf = None, None, 0
        # 1. an observed personal address that contains the first name or last name
        for e in personal:
            lp = e.split("@")[0]
            if f and (f in lp or (l and l in lp)):
                email, source, conf = e, "site", 85
                break
        # 2. derive from the learned pattern, only on a mail-accepting domain
        if not email and pattern and mail_domain and f and (l or pattern == "first") and accepts is not False:
            email, source, conf = f"{PATTERNS[pattern](f, l)}@{mail_domain}", "pattern", 55
        role = str(p.get("role") or "")
        contacts.append({"name": p["name"], "role": role, "email": email, "email_source": source, "confidence": conf,
                         "decision_maker": any(d in role.lower() for d in DECIDER)})

    decider = next((c for c in contacts if c["decision_maker"]), None)
    return {
        "domain": mail_domain or None,
        "domain_accepts_mail": accepts,
        "generic_emails": generic[:5],
        "personal_emails": personal[:5],
        "email_pattern": pattern,
        "contacts": contacts,
        "decision_maker": decider,
        "confidence": 85 if personal else (70 if generic else (40 if contacts else 0)),
    }
