"""rig.explain — why lines from a model reply. Parts in that reply are ignored."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _ground() -> Any:
    path = Path(__file__).with_name("ground.py")
    spec = importlib.util.spec_from_file_location("rig_explain_ground", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("rig.explain logic is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run(payload: dict, ctx: Any) -> dict:
    data = dict(payload or {})
    text = data.get("model_text")
    if not isinstance(text, str):
        text = await ctx.model(
            [{"role": "user", "content": _ground().explain_prompt(data)}],
            task_type="rig.explain",
        )
    return _ground().explain(data, text)
