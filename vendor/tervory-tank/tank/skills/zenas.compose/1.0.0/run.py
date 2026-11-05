"""zenas.compose — template first. The model cannot add law."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


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
        if engine.wording_keeps_law(text, proposal, list(memo["citations"])):
            text = proposal.strip()
            smoothed = True
    return {
        "conclusion": memo["conclusion"],
        "citations": memo["citations"],
        "smoothed": smoothed,
        "kept_template": not smoothed,
        "text": text,
    }
