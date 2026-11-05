"""Scope rules for Zenas. Areas live in areas.json, beside this file.

A new division is a new id on the allowed list (and off not_yet), plus evals.
Criminal law is never an allowed id. The cheap model runs only when no rule hits,
and it cannot overrule a rule that already decided.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path(__file__).with_name("areas.json")

REFUSED = "Refused. Zenas does not do criminal law. Ask a criminal lawyer in that jurisdiction."
FLAGGED = "In scope as compliance. A lawyer must review this before it is used."
NOT_YET = "Not offered yet."
IN_SCOPE = "In scope. A lawyer must review this before the client sees it."
OUT = "Not in scope."

_FIELDS = ("in_scope", "area", "jurisdiction", "criminal", "needs_lawyer", "reason")


def load_config(path: Path | None = None) -> dict[str, Any]:
    raw = json.loads((path or _CONFIG_PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("areas.json must be an object")
    return raw


def allowed_ids(config: dict[str, Any] | None = None) -> list[str]:
    return [str(item["id"]) for item in (config or load_config())["allowed"]]


def _hit(text: str, phrase: str) -> bool:
    needle = phrase.lower()
    if " " in needle or any(not char.isalnum() for char in needle):
        return needle in text.lower()
    return re.search(rf"\b{re.escape(needle)}\b", text, re.IGNORECASE) is not None


def _first(text: str, groups: list[dict[str, Any]]) -> str | None:
    for group in groups:
        phrases = group.get("phrases") or []
        if any(_hit(text, str(phrase)) for phrase in phrases):
            return str(group["id"])
    return None


def _any(text: str, phrases: list[str]) -> bool:
    return any(_hit(text, phrase) for phrase in phrases)


def detect_jurisdiction(text: str, config: dict[str, Any] | None = None) -> str:
    phrases = (config or load_config()).get("jurisdiction_phrases") or {}
    us = _any(text, list(phrases.get("us") or []))
    za = _any(text, list(phrases.get("za") or []))
    if us and za:
        return "both"
    if us:
        return "us"
    if za:
        return "za"
    return ""


def _jurisdiction(text: str, given: str | None, config: dict[str, Any]) -> str:
    if given in ("us", "za", "both"):
        return given
    return detect_jurisdiction(text, config)


def scan(text: str, config: dict[str, Any] | None = None) -> tuple[str | None, str | None]:
    """(kind, area id). kind is criminal, regulatory, not_yet, allowed, or None."""
    cfg = config or load_config()
    criminal = _any(text, list(cfg.get("criminal_phrases") or []))
    regulatory = _first(text, list(cfg.get("regulatory") or []))
    if regulatory and not criminal:
        return "regulatory", regulatory
    if criminal:
        return "criminal", None
    not_yet = _first(text, list(cfg.get("not_yet") or []))
    if not_yet:
        return "not_yet", not_yet
    allowed = _first(text, list(cfg.get("allowed") or []))
    if allowed:
        return "allowed", allowed
    return None, None


def ruled(text: str, config: dict[str, Any] | None = None) -> str | None:
    kind, _area = scan(text, config)
    return kind


def _result(
    *,
    in_scope: bool,
    area: str,
    jurisdiction: str,
    criminal: bool,
    needs_lawyer: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "in_scope": in_scope,
        "area": area,
        "jurisdiction": jurisdiction,
        "criminal": criminal,
        "needs_lawyer": needs_lawyer,
        "reason": reason,
    }


def classify(text: str, jurisdiction: str | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    body = text or ""
    jur = _jurisdiction(body, jurisdiction, cfg)
    kind, area = scan(body, cfg)
    if kind == "criminal":
        return _result(
            in_scope=False, area="", jurisdiction=jur, criminal=True, needs_lawyer=False, reason=REFUSED
        )
    if kind == "regulatory":
        return _result(
            in_scope=True,
            area="regulatory_compliance",
            jurisdiction=jur,
            criminal=False,
            needs_lawyer=True,
            reason=FLAGGED,
        )
    if kind == "not_yet":
        return _result(
            in_scope=False,
            area=area or "",
            jurisdiction=jur,
            criminal=False,
            needs_lawyer=False,
            reason=NOT_YET,
        )
    if kind == "allowed":
        return _result(
            in_scope=True,
            area=area or "",
            jurisdiction=jur,
            criminal=False,
            needs_lawyer=True,
            reason=IN_SCOPE,
        )
    return _result(in_scope=False, area="", jurisdiction=jur, criminal=False, needs_lawyer=False, reason=OUT)


def parse_hint(raw: str) -> dict[str, Any]:
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def classify_with_hint(
    text: str,
    raw: str,
    jurisdiction: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rules win. The model may only name an allowed area when no rule hit."""
    cfg = config or load_config()
    if ruled(text, cfg) is not None:
        return classify(text, jurisdiction, cfg)
    hint = parse_hint(raw or "")
    area = str(hint.get("area") or "").strip()
    hinted = str(hint.get("jurisdiction") or "").strip()
    jur = _jurisdiction(text, jurisdiction or (hinted if hinted in ("us", "za", "both") else None), cfg)
    not_yet = {str(item["id"]) for item in cfg.get("not_yet") or []}
    if area in not_yet:
        return _result(
            in_scope=False, area=area, jurisdiction=jur, criminal=False, needs_lawyer=False, reason=NOT_YET
        )
    if area in {"criminal", "criminal_law"}:
        return _result(
            in_scope=False, area="", jurisdiction=jur, criminal=True, needs_lawyer=False, reason=REFUSED
        )
    if area in set(allowed_ids(cfg)):
        return _result(
            in_scope=True, area=area, jurisdiction=jur, criminal=False, needs_lawyer=True, reason=IN_SCOPE
        )
    return _result(in_scope=False, area="", jurisdiction=jur, criminal=False, needs_lawyer=False, reason=OUT)


def public_result(result: dict[str, Any]) -> dict[str, Any]:
    return {key: result[key] for key in _FIELDS}
