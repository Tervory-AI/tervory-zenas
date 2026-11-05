"""zenas.draft — approved clauses only. Gaps are marked, not invented."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _drafting() -> Any:
    import sys

    sys.dont_write_bytecode = True
    path = Path(__file__).resolve().parents[2] / "zenas.shared" / "drafting.py"
    spec = importlib.util.spec_from_file_location("zenas_shared_drafting", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("drafting module is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run(payload: dict, ctx: Any) -> dict:
    del ctx
    drafting = _drafting()
    body = payload or {}
    assembled = drafting.assemble_draft(
        template=str(body.get("template") or ""),
        clauses=list(body.get("clauses") or []),
        terms=dict(body.get("terms") or {}),
        prior=body.get("prior") if isinstance(body.get("prior"), dict) else None,
    )
    if not assembled.get("ok"):
        return {"text": "", "library": [], "review": [], "reused": False, "model_calls": 0, "reason": assembled.get("reason")}
    return {
        "text": assembled["text"],
        "library": assembled["library"],
        "review": assembled["review"],
        "reused": assembled["reused"],
        "model_calls": 0,
    }
