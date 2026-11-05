"""zenas.apply — reuse, then code, then one cheap call per element that differs."""

from __future__ import annotations

import json
import importlib.util
from pathlib import Path
from typing import Any


def _engine() -> Any:
    import sys

    sys.dont_write_bytecode = True
    path = Path(__file__).resolve().parents[2] / "zenas.shared" / "engine.py"
    spec = importlib.util.spec_from_file_location("zenas_shared_engine_apply", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("ladder engine is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run(payload: dict, ctx: Any) -> dict:
    engine = _engine()
    body = payload or {}
    card = body.get("card") or {}
    features = dict(body.get("features") or {})
    tenant_id = str(body.get("tenant_id") or "")
    ranked = engine.rank_worked(
        list(body.get("worked") or []),
        rule_card_ids=[str(card.get("id") or "")],
        features=features,
        narrative=str(body.get("narrative") or ""),
    )
    elements = []
    cost = 0.0
    calls = 0
    for element in card.get("elements") or []:
        decided = engine.decide_without_model(
            element,
            features,
            list(card.get("decision_table") or []),
            ranked,
            tenant_id=tenant_id,
        )
        if decided is not None:
            elements.append(decided)
            continue
        prepared = engine.prepare_prompt(
            json.dumps({"test": element.get("test"), "features": features}),
            home_region=body.get("home_region"),
            provider=str(body.get("provider") or "luna"),
        )
        if not prepared["ok"]:
            return {"rungs": [], "findings": [], "cost_usd": 0, "model_calls": 0, "reason": prepared["reason"]}
        raw = await ctx.model(
            [{"role": "user", "content": prepared["text"]}],
            task_type="zenas.apply",
            max_tokens=200,
        )
        parsed = engine.parse_json_object(str(raw or ""))
        finding = str(parsed.get("finding") or "unclear")
        if finding not in ("met", "not_met", "unclear"):
            finding = "unclear"
        calls += 1
        cost += engine.CHEAP_CALL_USD
        elements.append(
            {
                "element_id": element.get("id"),
                "finding": finding,
                "rung": "model",
                "cost_usd": engine.CHEAP_CALL_USD,
            }
        )
    return {
        "rungs": [item["rung"] for item in elements],
        "findings": [item["finding"] for item in elements],
        "cost_usd": cost,
        "model_calls": calls,
    }
