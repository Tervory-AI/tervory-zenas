"""zenas.scope — rules first, cheap model only when no rule hits."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{name} is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rules() -> Any:
    return _load(Path(__file__).with_name("rules.py"), "zenas_scope_rules")


def _masked(text: str) -> str:
    """Placeholders only. The model never sees the identifier."""
    detect = _load(
        Path(__file__).resolve().parents[2] / "zenas.pii" / "1.0.0" / "detect.py",
        "zenas_pii_detect_for_scope",
    )
    return str(detect.detect(text).get("text") or "")


_PROMPT = """Classify this request for Zenas. Reply with JSON only:
{"area":"<id>","jurisdiction":"us|za|both"}
Allowed area ids: __ALLOWED__
If it is not one of those, use "none". Do not give legal advice. Do not help with criminal law.

Request:
__TEXT__
"""


async def run(payload: dict, ctx: Any) -> dict:
    rules = _rules()
    text = str((payload or {}).get("text") or "")
    given = (payload or {}).get("jurisdiction")
    jurisdiction = given if given in ("us", "za", "both") else None
    if rules.ruled(text) is not None or not text.strip():
        return rules.public_result(rules.classify(text, jurisdiction))
    allowed = ", ".join(rules.allowed_ids())
    prompt = _PROMPT.replace("__ALLOWED__", allowed).replace("__TEXT__", _masked(text))
    raw = await ctx.model(
        [{"role": "user", "content": prompt}],
        task_type="zenas.scope",
        max_tokens=200,
    )
    return rules.public_result(rules.classify_with_hint(text, str(raw or ""), jurisdiction))
