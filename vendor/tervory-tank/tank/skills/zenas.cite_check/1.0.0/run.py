"""zenas.cite_check — code only. A fake or mismatched citation blocks delivery."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _engine() -> Any:
    import sys

    sys.dont_write_bytecode = True
    path = Path(__file__).resolve().parents[2] / "zenas.shared" / "engine.py"
    spec = importlib.util.spec_from_file_location("zenas_shared_engine_cite", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("ladder engine is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run(payload: dict, ctx: Any) -> dict:
    del ctx
    engine = _engine()
    body = payload or {}
    result = engine.cite_check(list(body.get("statements") or []), list(body.get("passages") or []))
    return {"passed": result["passed"], "blocked": result["blocked"], "results": result["results"]}
