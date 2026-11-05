"""Postal codes used when a feed is national. Not an industry list."""

from __future__ import annotations

US_POSTAL = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL",
    "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
    "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
    "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI",
    "WY",
)

US_POSTAL_SET = frozenset(US_POSTAL)

# Two-digit FIPS for each postal code. Used when a climate feed asks by state.
STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08",
    "CT": "09", "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15",
    "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21",
    "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27",
    "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
    "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
    "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53",
    "WV": "54", "WI": "55", "WY": "56",
}

_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming",
}


def normalize_label(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalpha())


STATE_BY_NAME = {normalize_label(name): code for code, name in _STATE_NAMES.items()}
STATE_BY_NAME["districtofcolumbia"] = "DC"
STATE_BY_NAME["washingtondc"] = "DC"


def mentioned_states(question: str) -> set[str]:
    """Recognize complete state names and explicit uppercase postal codes."""
    import re

    codes = set(re.findall(r"\b[A-Z]{2}\b", question)) & US_POSTAL_SET
    remaining = question.lower()
    for code, name in sorted(_STATE_NAMES.items(), key=lambda item: len(item[1]), reverse=True):
        pattern = r"\b" + re.escape(name.lower()) + r"\b"
        if re.search(pattern, remaining):
            codes.add(code)
            remaining = re.sub(pattern, " ", remaining)
    return codes


def state_for_area(area: str) -> str | None:
    if area in US_POSTAL_SET:
        return area
    if area.isdigit() and len(area) in {2, 5}:
        return next((code for code, fips in STATE_FIPS.items() if area[:2] == fips), None)
    return STATE_BY_NAME.get(normalize_label(area))


def area_label(area: str, level: str, title: str) -> str:
    """Readable location without changing the stored geographic identifier."""
    if level == "county":
        if area == "unmatched":
            return "County could not be matched"
        # Both county-producing adapters retain the publisher's place name.
        for prefix in ("Population, ", "Active listings in "):
            if title.startswith(prefix):
                name = title.removeprefix(prefix).strip()
                if prefix == "Active listings in " and not any(word in name.lower() for word in ("county", "parish", "borough", "census area", "municipality")):
                    name += " County"
                code = state_for_area(area)
                state = _STATE_NAMES.get(code, "")
                return f"{name}, {state}" if state and state not in name else name
        return "County not named in source"
    if level == "state":
        code = state_for_area(area)
        return _STATE_NAMES[code] if code else "State not named in source"
    if area.upper() in {"US", "USA"}:
        return "United States"
    if area.upper() in {"WORLD", "GLOBAL", "WLD"}:
        return "Worldwide"
    if level == "port" and ", " in area:
        name, code = area.rsplit(", ", 1)
        return f"{name}, {_STATE_NAMES.get(code, code)}"
    return area if area and not area.isdigit() else "Place not named in source"
