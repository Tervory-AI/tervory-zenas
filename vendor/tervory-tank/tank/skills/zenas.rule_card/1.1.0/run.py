"""zenas.rule_card 1.1.0 — top tier once per law version, then the approved card is free.

Quotes are cite-checked UNMASKED, word for word, then masked for output
(card_from_proposal(..., verify_unmasked=True)). The prompt hint (QUOTE_RULE) only
asks for quotes without dates or identifiers; it is not the fix.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

# The room's rule-card prompt carries the same line (agents/zenas/core/service.py).
QUOTE_RULE = (
    "Prefer whole sentences that contain no dates, amounts, patent or case numbers, ID or phone "
    "numbers, email or street addresses, and keep those out of every other field too. They are "
    "masked in the card, so a quote without them reads clearly and is checked most simply."
)


def _engine() -> Any:
    import sys

    sys.dont_write_bytecode = True
    path = Path(__file__).resolve().parents[2] / "zenas.shared" / "engine.py"
    spec = importlib.util.spec_from_file_location("zenas_shared_engine_card", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("ladder engine is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run(payload: dict, ctx: Any) -> dict:
    engine = _engine()
    body = payload or {}
    question = str(body.get("question") or "")
    secrets = [str(item) for item in (body.get("secrets") or []) if item]
    approved = body.get("approved_card")
    if (
        isinstance(approved, dict)
        and approved.get("status") == "lawyer_approved"
        and engine.question_key(str(approved.get("question") or "")) == engine.question_key(question)
    ):
        return {
            "status": "lawyer_approved",
            "reused": True,
            "approvable": True,
            "cost_usd": 0,
            "model_calls": 0,
            "question": approved.get("question"),
            "client_facts_in_card": False,
        }
    prepared = engine.prepare_prompt(
        question,
        home_region=body.get("home_region"),
        provider=str(body.get("provider") or "luna"),
    )
    if not prepared["ok"]:
        return {
            "status": "draft",
            "reused": False,
            "approvable": False,
            "cost_usd": 0,
            "model_calls": 0,
            "reason": prepared["reason"],
            "client_facts_in_card": False,
        }
    raw = await ctx.model(
        [{"role": "user", "content": f"{QUOTE_RULE}\n\n{prepared['text']}"}],
        task_type="zenas.rule_card",
        max_tokens=800,
    )
    proposal = engine.parse_json_object(str(raw or ""))
    card = engine.card_from_proposal(
        proposal,
        list(body.get("passages") or []),
        question=question,
        secrets=secrets,
        verify_unmasked=True,
    )
    problem = engine.law_only_problem(card, secrets)
    return {
        "status": "draft",
        "reused": False,
        "approvable": bool(card["cite"]["passed"] and problem is None),
        "cost_usd": engine.TOP_CALL_USD,
        "model_calls": 1,
        "question": card["question"],
        "element_count": len(card["elements"]),
        "client_facts_in_card": engine.client_text_present(card, secrets),
    }
