"""Decide public record from the source, and mask client work before it is shared.

The material type is never inferred from the words of the document. A court
source id is public record. A tenant matter is client work. Anything else is
unknown and is not shared.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.zenas.core.pii_detect import mask_client_text, regex_party_names

log = logging.getLogger("zenas.material")

# One list. The convert job and POST /cases/facts both use it.
# A source that is not in this set is unknown. Unknown material is not shared.
COURT_SOURCES = frozenset(
    {
        "courtlistener",
        "cap",
        "saflii",
        "govza-gazette",
        "concourt",
        "sca",
        "cac",
        "comptrib",
        "sars-taxcourt",
        "landcourt",
        "ocj-highcourt",
        "labour-court",
        "labour-appeal",
        "special-tribunal",
    }
)

UNKNOWN = "material type is unknown"
MASK_FAILED = "masking failed"

# Fields that already name a person or a company. The body is not one of them.
_NAME_KEYS = frozenset(
    {
        "parties",
        "party",
        "signatories",
        "signatory",
        "contacts",
        "contact",
        "counterparties",
        "counterparty",
        "name",
        "legal_name",
        "company",
        "entity",
        "person",
        "organisation",
        "organization",
    }
)


def material_type_for_pool(source_id: str | None) -> str | None:
    """Public record only when the pool source is a court source."""
    if (source_id or "").strip().lower() in COURT_SOURCES:
        return "public_record"
    return None


def log_withheld(reason: str) -> None:
    """Log a fixed reason. The document text is never written here."""
    log.warning("not shared: %s", reason)


class _Names:
    """One letter per distinct name, shared by every field in the release."""

    def __init__(self) -> None:
        self.labels: dict[str, str] = {}
        self.forms: list[str] = []

    def add(self, name: str) -> None:
        text = (name or "").strip()
        key = text.casefold()
        if len(text) < 3 or key in self.labels:
            return
        number = len(self.labels) + 1
        letter = chr(ord("A") + number - 1) if number <= 26 else str(number)
        self.labels[key] = f"[Party {letter}]"
        self.forms.append(text)

    def extend(self, names: list[str]) -> None:
        for name in names:
            self.add(name)


def _from_value(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if len(text) >= 3 else []
    if isinstance(value, list):
        found: list[str] = []
        for item in value:
            found.extend(_names_in(item) if isinstance(item, dict) else _from_value(item))
        return found
    if isinstance(value, dict):
        return _names_in(value)
    return []


def _names_in(record: Any) -> list[str]:
    if not isinstance(record, dict):
        return []
    found: list[str] = []
    for key, value in record.items():
        if key in _NAME_KEYS:
            found.extend(_from_value(value))
        elif isinstance(value, dict):
            found.extend(_names_in(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    found.extend(_names_in(item))
    return found


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        found: list[str] = []
        for item in value:
            found.extend(_strings(item))
        return found
    if isinstance(value, dict):
        found = []
        for item in value.values():
            found.extend(_strings(item))
        return found
    return []


def _walk(value: Any, names: _Names) -> Any:
    if isinstance(value, str):
        masked = mask_client_text(value, forms=names.forms, placeholders=names.labels)
        if not masked["ok"]:
            raise ValueError(MASK_FAILED)
        return masked["text"]
    if isinstance(value, list):
        return [_walk(item, names) for item in value]
    if isinstance(value, dict):
        return {key: _walk(item, names) for key, item in value.items()}
    return value


def mask_share(
    *,
    parties: Any = None,
    matter: Any = None,
    document: Any = None,
    body: str = "",
    narrative: str = "",
    features: dict[str, Any] | None = None,
    findings: list[dict[str, Any]] | None = None,
    lawyer_edits: str = "",
    outcome: str = "",
    memo: str = "",
) -> dict[str, Any]:
    """Mask the three things a shared client worked matter may keep.

    A matter with no recorded party names is not shared.
    """
    recorded = _from_value(parties if parties is not None else (matter or {}).get("parties"))
    if not recorded:
        return {"ok": False, "reason": MASK_FAILED}
    names = _Names()
    names.extend(recorded)
    names.extend(_names_in(matter))
    names.extend(_names_in(document))
    payload = {
        "body": body,
        "narrative": narrative,
        "features": features or {},
        "findings": findings or [],
        "lawyer_edits": lawyer_edits,
        "outcome": outcome,
        "memo": memo,
    }
    for text in _strings(payload):
        names.extend(regex_party_names(text))
    try:
        masked = {key: _walk(value, names) for key, value in payload.items()}
    except Exception:
        return {"ok": False, "reason": MASK_FAILED}
    return {"ok": True, **masked}
