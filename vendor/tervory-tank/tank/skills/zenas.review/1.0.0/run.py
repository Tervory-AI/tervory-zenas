"""zenas.review — code first. The replacement sentence is the approved one."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _review() -> Any:
    import sys

    sys.dont_write_bytecode = True
    path = Path(__file__).resolve().parents[2] / "zenas.shared" / "review.py"
    spec = importlib.util.spec_from_file_location("zenas_shared_review", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("review module is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run(payload: dict, ctx: Any) -> dict:
    del ctx
    review = _review()
    body = payload or {}
    result = review.review_positions(text=str(body.get("text") or ""), positions=list(body.get("positions") or []))
    return {
        "deviations": result["deviations"],
        "risk": result["risk"],
        "inserts": result["inserts"],
        "deletes": result["deletes"],
    }
