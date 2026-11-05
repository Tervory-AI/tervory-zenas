"""ffl.profile — Pass 6, FFL vertical module. From the licence fields kept by ffl.ingest plus what
passes 1-5 found, produce the FFL-specific layer: licence type, expiry and renewal window, segment,
inventory signal. Pure code; nothing here needs a model.

What the public ATF list does NOT carry, and is therefore left null with a reason rather than
guessed: SOT (Class III) status, violations/revocations. Those come from other sources later.
"""

from datetime import date

LIC_TYPES = {
    "01": "Dealer in firearms other than destructive devices",
    "02": "Pawnbroker in firearms other than destructive devices",
    "03": "Collector of curios and relics",
    "06": "Manufacturer of ammunition for firearms",
    "07": "Manufacturer of firearms other than destructive devices",
    "08": "Importer of firearms other than destructive devices",
    "09": "Dealer in destructive devices",
    "10": "Manufacturer of destructive devices",
    "11": "Importer of destructive devices",
}
# ATF expiry code: one digit (last digit of the year) + one letter for the month, I is skipped.
MONTHS = {c: i + 1 for i, c in enumerate("ABCDEFGHJKLM")}

INVENTORY = {
    "handguns": ("handgun", "pistol", "revolver"),
    "rifles": ("rifle", "ar-15", "ar15", "carbine", "bolt action", "long gun"),
    "shotguns": ("shotgun",),
    "ammunition": ("ammo", "ammunition", "cartridge"),
    "optics": ("optic", "scope", "red dot", "sight"),
    "nfa": ("suppressor", "silencer", "nfa", "class 3", "class iii", "sbr", "machine gun", "full auto"),
    "accessories": ("accessor", "holster", "magazine", "gear", "apparel"),
    "reloading": ("reload",),
    "archery": ("archery", "bow", "crossbow"),
    "range": ("range", "lane", "shooting range"),
    "gunsmithing": ("gunsmith", "repair", "cerakote", "custom build"),
    "training": ("training", "class", "course", "ccw", "concealed carry", "instruction"),
    "transfers": ("transfer", "ffl transfer"),
}


def expiry(code: str, today: date | None = None) -> tuple[str | None, int | None]:
    """'3K' → ('2033-10-31' or nearest future year ending in 3, days until). Expiry is the last day of the month."""
    today = today or date.today()
    code = (code or "").strip().upper()
    if len(code) != 2 or not code[0].isdigit() or code[1] not in MONTHS:
        return None, None
    digit, month = int(code[0]), MONTHS[code[1]]
    # Licences run 3 years; the year digit is the last digit of the expiry year. Pick the year
    # ending in that digit inside the window [today - 2y, today + 8y) so lapsed ones still resolve.
    year = None
    for y in range(today.year - 2, today.year + 8):
        if y % 10 == digit:
            year = y
            break
    if year is None:
        return None, None
    # last day of month
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    last = date.fromordinal(nxt.toordinal() - 1)
    return last.isoformat(), (last - today).days


def segment(lic_type: str, categories: list[str], services: list[str], people: list[dict]) -> str:
    words = " ".join(map(str, categories + services)).lower()
    if lic_type == "02":
        return "pawn"
    if lic_type in ("07", "10"):
        return "manufacturer"
    if lic_type in ("08", "11"):
        return "importer"
    if lic_type == "03":
        return "collector"
    if lic_type == "06":
        return "ammo manufacturer"
    if "range" in words or "lane" in words:
        return "range"
    if "gunsmith" in words or "repair" in words or "cerakote" in words:
        return "gunsmith"
    if "online" in words or "ecommerce" in words or "e-commerce" in words:
        return "online dealer"
    if "sporting goods" in words or "outdoor" in words or "fishing" in words or "hunting" in words:
        return "sporting goods"
    return "independent dealer"


async def run(payload: dict, ctx) -> dict:
    eng = payload.get("engine") or {}
    profile = payload.get("profile") or {}
    lic_type = str(eng.get("lic_type") or "").zfill(2) if eng.get("lic_type") else ""
    exp_iso, days = expiry(str(eng.get("lic_xprdte") or ""), payload.get("_today") and date.fromisoformat(payload["_today"]))

    def val(k):
        v = profile.get(k)
        return v.get("value") if isinstance(v, dict) else v

    cats = list(val("categories") or [])
    svcs = list(val("services") or [])
    people = list(val("people") or [])
    words = " ".join(map(str, cats + svcs)).lower()
    inventory = sorted(k for k, needles in INVENTORY.items() if any(n in words for n in needles))

    out = {
        "licence_number": eng.get("licence_number") or None,
        "licence_type": lic_type or None,
        "licence_type_label": LIC_TYPES.get(lic_type),
        "licence_expires": exp_iso,
        "days_to_expiry": days,
        "renewal_window": (days is not None and 0 <= days <= 120),
        "lapsed": (days is not None and days < 0),
        "trade_name_differs": bool(eng.get("trade_name_differs")),
        "segment": segment(lic_type, cats, svcs, people),
        "inventory_signal": inventory,
        "sot_status": None,              # not in the public list
        "compliance_flags": None,        # not in the public list
        "notes": "SOT status and violations are not in the ATF public list; left null, not guessed.",
    }
    # Confidence of this layer: the licence facts are registry-grade; the segment/inventory are inferred.
    out["confidence"] = 95 if lic_type else 0
    return out
