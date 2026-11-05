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


async def _discover(payload: dict, ctx) -> dict:
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


# Quality is deterministic. MX keeps the existing free DNS-over-HTTPS path.
import ipaddress
from pathlib import Path

import phonenumbers

_DISPOSABLE = frozenset(line.strip().lower() for line in
    (Path(__file__).parent / "data" / "disposable_email_blocklist.conf").read_text().splitlines()
    if line.strip() and not line.startswith("#"))
_LINE_TYPES = {
    phonenumbers.PhoneNumberType.MOBILE: "mobile",
    phonenumbers.PhoneNumberType.FIXED_LINE: "fixed line",
    phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed-or-mobile",
    phonenumbers.PhoneNumberType.VOIP: "VoIP",
    phonenumbers.PhoneNumberType.TOLL_FREE: "toll-free",
}
_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_MAILBOX = re.compile(r'(' + _ATOM + r'(?:\.' + _ATOM + r')*|"(?:[\x20-\x21\x23-\x5b\x5d-\x7e]|\\[\x20-\x7e])*")@(.+)')


def email_syntax(email: str) -> bool:
    """RFC 5321 ASCII mailbox grammar and octet limits; no SMTPUTF8 claim."""
    if not email.isascii() or len(email.encode()) > 254:
        return False
    match = _MAILBOX.fullmatch(email)
    if not match:
        return False
    local, domain = match.groups()
    if len(local.encode()) > 64 or len(domain.encode()) > 255:
        return False
    if domain.startswith("[") and domain.endswith("]"):
        literal = domain[1:-1]
        try:
            parsed = ipaddress.ip_address(literal.removeprefix("IPv6:"))
            return parsed.version == (6 if literal.startswith("IPv6:") else 4)
        except ValueError:
            return False
    return all(0 < len(label) <= 63 and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
               for label in domain.split("."))


def disposable(domain: str) -> bool:
    labels = domain.lower().split(".")
    return any(".".join(labels[i:]) in _DISPOSABLE for i in range(len(labels)))


def _evidence(value, item: dict, method: str) -> dict:
    return {"value": value, "source": item.get("source") or "site", "source_url": item.get("source_url"),
            "checked_at": item.get("checked_at"), "confidence": int(item.get("confidence", 80)), "method": method}


def phone_quality(item: dict, country: str) -> dict:
    raw = str(item.get("value") or "")
    region = country.upper().strip()
    region = region if region in phonenumbers.SUPPORTED_REGIONS else None
    e164, valid, line_type, matches = None, False, "unknown", None
    try:
        number = phonenumbers.parse(raw, region)
        e164 = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
        valid = phonenumbers.is_valid_number(number)
        line_type = _LINE_TYPES.get(phonenumbers.number_type(number), "unknown")
        if region:
            matches = phonenumbers.region_code_for_number(number) == region
    except phonenumbers.NumberParseException:
        pass
    return {"phone": raw, **{key: _evidence(value, item, "libphonenumber") for key, value in (
        ("phone_e164", e164), ("phone_valid", valid), ("phone_line_type", line_type),
        ("phone_country_matches", matches))}}


async def run(payload: dict, ctx) -> dict:
    result = await _discover(payload, ctx)
    country = str(payload.get("country") or "")
    phones, seen = [], set()
    for supplied in payload.get("phones") or []:
        item = supplied if isinstance(supplied, dict) else {"value": str(supplied), "source": "site"}
        key = (str(item.get("value") or ""), item.get("source_url"), item.get("source"))
        if key[0] and key not in seen:
            seen.add(key)
            phones.append(phone_quality(item, country))
    email_items = payload.get("email_evidence") or []
    observed = {str(e).strip().lower() for e in payload.get("emails") or [] if str(e).strip()}
    qualities = []
    mx = {}
    if result.get("domain"):
        mx[result["domain"]] = result["domain_accepts_mail"]
    for email in sorted(observed):
        item = next((e for e in email_items if str(e.get("value") or "").lower() == email),
                    {"value": email, "source": "site", "source_url": payload.get("website"), "confidence": 80})
        ok = email_syntax(email)
        local, _, domain = email.rpartition("@")
        if ok and domain not in mx:
            mx[domain] = await mx_ok(ctx, domain)
        qualities.append({"email": email, **{key: _evidence(value, item, method) for key, value, method in (
            ("email_syntax_ok", ok, "RFC 5321"),
            ("email_disposable", disposable(domain) if domain else False, "disposable-email-domains@aa73ac1b77f440ebdcca0872cf2ebbd73fadce70"),
            ("email_kind", "role" if local in _generic_names(payload) else "personal", "inbox local-part"),
            ("email_mx_ok", mx.get(domain) if ok else None, "DNS MX"))}})
    fields = {}
    primary_phone = str(payload.get("primary_phone") or (phones[0]["phone"] if phones else ""))
    primary_email = str(payload.get("primary_email") or (qualities[0]["email"] if qualities else "")).lower()
    for records, label, primary, keys in (
        (phones, "phone", primary_phone, ("phone_e164", "phone_valid", "phone_line_type", "phone_country_matches")),
        (qualities, "email", primary_email, ("email_syntax_ok", "email_disposable")),
    ):
        chosen = next((record for record in records if record[label] == primary), None)
        if chosen:
            fields.update({key: chosen[key] for key in keys})
    if phones:
        fields["phone_quality"] = {"value": phones, "source": "libphonenumber", "confidence": 100}
    if qualities:
        fields["email_quality"] = {"value": qualities, "source": "RFC 5321+DNS MX+disposable-email-domains", "confidence": 100}
    return {**result, "fields": fields, "phone_quality": phones, "email_quality": qualities}
