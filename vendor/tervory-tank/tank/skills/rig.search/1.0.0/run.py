"""rig.search — rank one tenant's products. No network."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _pure() -> Any:
    path = Path(__file__).with_name("pure.py")
    spec = importlib.util.spec_from_file_location("rig_search_pure", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("rig.search logic is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run(payload: dict, ctx: Any) -> dict:
    del ctx
    return _pure().search(payload or {})
