"""base.normalize — Pass 1a. Clean a raw business record into canonical fields and a match key.

Pure code. No network, no model. Every field it outputs carries source="input"
and a confidence that reflects how much cleaning was needed.
"""

import re

SUFFIXES = {
    "inc", "inc.", "incorporated", "llc", "l.l.c.", "ltd", "ltd.", "limited", "co", "co.", "corp", "corp.",
    "corporation", "company", "lp", "llp", "pllc", "pc", "dba", "d/b/a",
}
STREET_WORDS = {
    "street": "st", "st.": "st", "avenue": "ave", "ave.": "ave", "road": "rd", "rd.": "rd", "drive": "dr", "dr.": "dr",
    "boulevard": "blvd", "blvd.": "blvd", "lane": "ln", "ln.": "ln", "highway": "hwy", "hwy.": "hwy", "suite": "ste",
    "ste.": "ste", "north": "n", "south": "s", "east": "e", "west": "w", "n.": "n", "s.": "s", "e.": "e", "w.": "w",
    "court": "ct", "ct.": "ct", "place": "pl", "pl.": "pl", "parkway": "pkwy", "circle": "cir", "route": "rte",
}
US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
    "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}


def _tidy(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _title(s: str) -> str:
    # Title-case but keep short all-caps tokens like "LLC", "USA", "ATF"
    out = []
    for tok in s.split(" "):
        if len(tok) <= 4 and tok.isupper() and tok.isalpha():
            out.append(tok)
        elif tok.lower().rstrip(".,") in SUFFIXES:
            out.append(tok.upper().rstrip(".,"))
        else:
            out.append(tok[:1].upper() + tok[1:].lower())
    return " ".join(out)


def normalize_name(raw: str) -> tuple[str, str]:
    """(display name, match key). Match key drops punctuation, suffixes and 'the'."""
    name = _tidy(raw)
    display = _title(name) if name.isupper() or name.islower() else name
    toks = re.sub(r"[^a-z0-9& ]+", " ", name.lower()).split()
    toks = [t for t in toks if t not in SUFFIXES and t != "the"]
    key = " ".join(toks).replace("&", "and")
    return display, key


def normalize_address(raw: str) -> str:
    toks = [STREET_WORDS.get(t, t) for t in _tidy(raw).lower().replace(",", " ").split()]
    return " ".join(toks)


def normalize_state(raw: str) -> str:
    s = _tidy(raw)
    if len(s) == 2:
        return s.upper()
    return US_STATES.get(s.lower(), s.upper()[:2])


def normalize_zip(raw: str) -> str:
    m = re.search(r"\b(\d{5})(?:-?\d{4})?\b", str(raw or ""))
    return m.group(1) if m else ""


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return f"+1{digits}" if len(digits) == 10 else ""


async def run(payload: dict, ctx) -> dict:
    name_raw = payload.get("name") or payload.get("business_name") or payload.get("license_name") or ""
    display, key = normalize_name(name_raw)
    street = normalize_address(payload.get("address") or payload.get("street") or "")
    city = _tidy(payload.get("city") or "").title()
    state = normalize_state(payload.get("state") or "")
    zip5 = normalize_zip(payload.get("zip") or payload.get("postal_code") or "")
    phone = normalize_phone(payload.get("phone") or "")

    def f(value, conf, source="input"):
        return {"value": value, "source": source, "confidence": conf}

    fields = {
        "name": f(display, 95 if display else 0),
        "name_key": f(key, 90 if key else 0),
        "street": f(street, 85 if street else 0),
        "city": f(city, 90 if city else 0),
        "state": f(state, 95 if len(state) == 2 else 0),
        "zip": f(zip5, 95 if zip5 else 0),
        "phone": f(phone, 90 if phone else 0),
    }
    match_key = f"{key}|{city.lower()}|{state.lower()}" if key else ""
    return {"fields": fields, "match_key": match_key}
