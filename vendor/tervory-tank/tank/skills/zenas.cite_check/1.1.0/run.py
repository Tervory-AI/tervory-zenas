"""zenas.cite_check 1.1.0 — code only. A fake or mismatched citation blocks delivery.

A quote shown masked ("[PHONE_1]", "[Date]") also passes when it carries the
fingerprint and length of the exact court words it was built from (quote_sha,
quote_len), those words are in the cited passage, and they mask to the quote
shown (cite_check(..., allow_masked=True)). 1.0.x ignore quote_sha.
"""

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
    result = engine.cite_check(
        list(body.get("statements") or []),
        list(body.get("passages") or []),
        allow_masked=True,
    )
    return {"passed": result["passed"], "blocked": result["blocked"], "results": result["results"]}
