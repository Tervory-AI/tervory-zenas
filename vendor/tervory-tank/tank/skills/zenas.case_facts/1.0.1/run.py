"""zenas.case_facts — one cheap call per judgment, then the stored row."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _engine() -> Any:
    import sys

    sys.dont_write_bytecode = True
    path = Path(__file__).resolve().parents[2] / "zenas.shared" / "engine.py"
    spec = importlib.util.spec_from_file_location("zenas_shared_engine_facts", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("ladder engine is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run(payload: dict, ctx: Any) -> dict:
    engine = _engine()
    body = payload or {}
    cached = body.get("cached")
    if isinstance(cached, dict) and cached.get("pool_doc_id") == body.get("pool_doc_id"):
        return {
            "cached": True,
            "cost_usd": 0,
            "model_calls": 0,
            "outcome": cached.get("outcome") or "",
            "narrative": cached.get("narrative") or "",
        }
    judgment = str(body.get("judgment") or "")
    prepared = engine.prepare_prompt(
        judgment,
        home_region=body.get("home_region"),
        provider=str(body.get("provider") or "luna"),
    )
    if not prepared["ok"]:
        return {"cached": False, "cost_usd": 0, "model_calls": 0, "reason": prepared["reason"], "outcome": ""}
    raw = await ctx.model(
        [{"role": "user", "content": prepared["text"]}],
        task_type="zenas.case_facts",
        max_tokens=800,
    )
    parsed = engine.parse_case_facts(str(raw or ""))
    return {
        "cached": False,
        "cost_usd": engine.CHEAP_CALL_USD,
        "model_calls": 1,
        "outcome": parsed["outcome"],
        "narrative": judgment,
        "finding": (parsed["element_findings"] or [{"finding": ""}])[0]["finding"],
    }
