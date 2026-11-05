"""zenas.retrieve — code only. The corpus is the pool, passed in or searched by the box."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _engine() -> Any:
    import sys

    sys.dont_write_bytecode = True
    path = Path(__file__).resolve().parents[2] / "zenas.shared" / "engine.py"
    spec = importlib.util.spec_from_file_location("zenas_shared_engine_retrieve", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("ladder engine is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run(payload: dict, ctx: Any) -> dict:
    del ctx
    engine = _engine()
    body = payload or {}
    passages = engine.retrieve_passages(
        list(body.get("corpus") or []),
        query=str(body.get("query") or ""),
        citation=str(body.get("citation") or ""),
        jurisdiction=str(body.get("jurisdiction") or ""),
        as_at=body.get("as_at"),
        level=body.get("level"),
        kind=body.get("kind"),
    )
    return {"count": len(passages), "citations": [item["citation"] for item in passages], "passages": passages}
