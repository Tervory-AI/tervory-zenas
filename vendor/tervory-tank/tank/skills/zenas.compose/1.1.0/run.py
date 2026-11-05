"""zenas.compose — template first. The model cannot add law."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
import re
from collections import Counter

_NEW_CITE = re.compile(r"§|\b(?:section|article|rule)\s+\d|\[\d{4}\]|\b\w+\s+v\.?\s+\w+|\d+\s+(?:F\.|U\.S\.|Del\.)", re.I)
_MARKERS = re.compile(
    r"\b(?:not|never|must|can|enforceable|unenforceable|met|unclear|liable|unless|except|only|before|after|within)\b|\d+"
)


def _fold(value: str) -> str:
    return " ".join(str(value).lower().split())


def _effect(value: str) -> tuple:
    """The legal-effect words of a memo: negations, duties, findings, numbers."""
    value = re.sub(r"(?m)^Issue:.*$", "", value)
    folded = _fold(value)
    folded = re.sub(r"\b(?:can be enforced|is enforceable)\b", "enforceable", folded)
    folded = re.sub(r"\b(?:shall|is required to|has to)\b", "must", folded)
    folded = re.sub(r"\b(?:may|is permitted to)\b", "can", folded)
    markers = Counter(_MARKERS.findall(folded))
    if "met" in markers:
        markers["met"] = 1
    return tuple(sorted(markers.items()))


def _keeps_effect(template: str, proposal: str, citations: list[str]) -> bool:
    """The smoothed memo keeps every citation, adds no law and keeps the same legal effect."""
    folded = _fold(proposal)
    if any(_fold(cite) not in folded for cite in citations):
        return False
    stripped = proposal
    for cite in sorted(citations, key=len, reverse=True):
        stripped = re.sub(re.escape(cite), "", stripped, flags=re.I)
    if _NEW_CITE.search(stripped):
        return False
    return _effect(template) == _effect(proposal)


def _engine() -> Any:
    import sys

    sys.dont_write_bytecode = True
    path = Path(__file__).resolve().parents[2] / "zenas.shared" / "engine.py"
    spec = importlib.util.spec_from_file_location("zenas_shared_engine_compose", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("ladder engine is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run(payload: dict, ctx: Any) -> dict:
    engine = _engine()
    body = payload or {}
    card = body.get("card") or {}
    memo = engine.compose_memo(card, list(body.get("elements") or []))
    smoothed = False
    text = memo["text"]
    if body.get("smooth") and ctx is not None:
        prepared = engine.prepare_prompt(
            text,
            home_region=body.get("home_region"),
            provider=str(body.get("provider") or "luna"),
        )
        if not prepared["ok"]:
            return {"conclusion": memo["conclusion"], "smoothed": False, "kept_template": True, "reason": prepared["reason"]}
        raw = await ctx.model(
            [{"role": "user", "content": prepared["text"]}],
            task_type="zenas.compose",
            max_tokens=800,
        )
        proposal = str(raw or "")
        cites = list(memo["citations"])
        if engine.wording_keeps_law(text, proposal, cites) and _keeps_effect(text, proposal, cites):
            text = proposal.strip()
            smoothed = True
    return {
        "conclusion": memo["conclusion"],
        "citations": memo["citations"],
        "smoothed": smoothed,
        "kept_template": not smoothed,
        "text": text,
    }
