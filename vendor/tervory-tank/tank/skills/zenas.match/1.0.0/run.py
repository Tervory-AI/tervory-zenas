"""zenas.match — code only. Another tenant's narrative is not returned."""

from __future__ import annotations

import json
import importlib.util
from pathlib import Path
from typing import Any


def _engine() -> Any:
    import sys

    sys.dont_write_bytecode = True
    path = Path(__file__).resolve().parents[2] / "zenas.shared" / "engine.py"
    spec = importlib.util.spec_from_file_location("zenas_shared_engine_match", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("ladder engine is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run(payload: dict, ctx: Any) -> dict:
    del ctx
    engine = _engine()
    body = payload or {}
    tenant_id = str(body.get("tenant_id") or "")
    ranked = engine.rank_worked(
        list(body.get("worked") or []),
        rule_card_ids=list(body.get("rule_card_ids") or []),
        features=dict(body.get("features") or {}),
        narrative=str(body.get("narrative") or ""),
    )
    matches = [engine.sanitize_match(item, tenant_id=tenant_id) for item in ranked]
    if not matches:
        return {"top_id": "", "same": [], "differs": [], "leaked": False}
    top = matches[0]
    same = [item["element_id"] for item in top["element_diff"] if item["same"]]
    differs = [item["element_id"] for item in top["element_diff"] if not item["same"]]
    secret = str(body.get("secret") or "")
    leaked = bool(secret) and secret in json.dumps(matches)
    return {"top_id": top["worked_matter_id"], "same": same, "differs": differs, "leaked": leaked}
