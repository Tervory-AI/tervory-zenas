"""Deterministic publication scrubber. No inference or network calls.

Names cannot be exhaustively inferred by regular expressions. Publication needs
the founder's reviewed identity inventory; absent/incomplete inventory refuses.
Metadata is never published. Unknown identity forms require inventory updates.
"""

import ipaddress
import importlib.util
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

_path = Path(__file__).with_name("pii.py")
if not _path.exists():
    _path = (
        Path(__file__).resolve().parents[1] / "tank/skills/zenas.pii/1.0.0/detect.py"
    )
_spec = importlib.util.spec_from_file_location("memory_pii", _path)
_pii = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pii)

EMAIL = re.compile(r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+\.[\w-]+", re.I)
UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I
)
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)")
URL = re.compile(r'(?:(?:https?://|www\.)[^\s<>"\[\]]+)', re.I)
NAME_LABEL = re.compile(r"\b(?:name|contact|attention|attn)\s*:\s*[^\n,;]+", re.I)
PROPER_NAMES = re.compile(
    r"\b[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)*(?:\s+[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)*)+\b"
)


def normalized(text: str) -> str:
    for _ in range(3):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    return unicodedata.normalize("NFKC", text)


def identity_inventory(identifiers: dict) -> tuple[set[str], list[str]]:
    """A review flag alone cannot authorize publication of client text."""
    if identifiers.get("reviewed") is not True:
        raise ValueError("Reviewed identity inventory is required")
    values = set()
    for field in ("names", "emails", "phones", "addresses"):
        items = identifiers.get(field)
        if not isinstance(items, list) or any(
            not isinstance(x, str) or not x.strip() for x in items
        ):
            raise ValueError("Incomplete identity inventory")
        values.update(items)
        if field == "names":
            values.update(
                part
                for name in items
                for part in re.split(r"\s+", name)
                if len(part) > 1
            )
    domains = identifiers.get("client_domains")
    if not isinstance(domains, list) or any(
        not isinstance(d, str) or not d.strip() for d in domains
    ):
        raise ValueError("Incomplete client domains")
    domains = [normalized(d).lower().strip().rstrip(".") for d in domains]
    if any(not d or "/" in d or "@" in d or ":" in d or "*" in d for d in domains):
        raise ValueError("Client domains must be host names")
    if not values and not domains:
        raise ValueError("Incomplete identity inventory")
    return values, domains


def deidentify(
    claim: str, evidence: list[str], tenant: str, identifiers: dict, *, public_addresses: list[str] | None = None
) -> tuple[str, list[str]]:
    values, domains = identity_inventory(identifiers)
    values.add(tenant)
    public = []
    for candidate in public_addresses or []:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.is_global:
            public.append(str(address))

    def client_url(value: str) -> bool:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        host = (parsed.hostname or "").lower().rstrip(".")
        host = host.encode("idna").decode("ascii")
        return any(
            host == d.encode("idna").decode("ascii")
            or host.endswith("." + d.encode("idna").decode("ascii"))
            for d in domains
        )

    def scrub(value: str) -> str:
        value = normalized(value)
        # Detect complete addresses before inventory substitutions can fragment
        # an email/domain and leave an identifying local part behind.
        value = EMAIL.sub("[EMAIL]", value)
        value = URL.sub(
            lambda m: "[URL]" if client_url(m[0].rstrip(".,;)")) else m[0], value
        )
        # Bare domains (including subdomains) are also identifying, even without a scheme.
        for domain in domains:
            value = re.sub(
                r"(?i)(?:[\w-]+\.)*" + re.escape(domain) + r"(?:/[^\s]*)?",
                "[URL]",
                value,
            )
        for item in sorted(values, key=len, reverse=True):
            value = re.sub(re.escape(normalized(item)), "[IDENTITY]", value, flags=re.I)
        protected = {}
        for offset, address in enumerate(public):
            token = "PUBLICADDRESSPLACEHOLDER" + chr(65 + offset)
            # Identity-inventory replacement above wins over public-fact hints.
            value, count = re.subn(r"(?<![0-9a-fA-F:.])" + re.escape(address) + r"(?![0-9a-fA-F:.])", token, value)
            if count:
                protected[token] = address
        value = EMAIL.sub("[EMAIL]", value)
        value = UUID.sub("[TENANT]", value)
        value = PHONE.sub("[PHONE]", value)
        value = _pii.detect(value)["text"]
        value = NAME_LABEL.sub("[NAME]", value)
        value = PROPER_NAMES.sub("[NAME]", value)
        for token, address in protected.items():
            value = value.replace(token, address)
        return value

    clean_claim = scrub(claim)
    # Only unchanged public HTTP(S) evidence survives. Internal URNs and opaque
    # source strings could encode a client name even without a recognised URL.
    clean_evidence = []
    for source in evidence:
        decoded = normalized(source)
        parsed = urlsplit(decoded)
        if (
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and not client_url(decoded)
            and scrub(decoded) == decoded
        ):
            clean_evidence.append(decoded)
    if not re.sub(r"\[[^\]]*\]|\W", "", clean_claim):
        raise ValueError("No publishable claim remains")
    return clean_claim, list(dict.fromkeys(clean_evidence))
