"""zenas.deadlines 1.2.0 — civil court days from a trigger date. No model."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _compute() -> Any:
    path = Path(__file__).with_name("compute.py")
    spec = importlib.util.spec_from_file_location("zenas_deadlines_1_2_compute", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("zenas.deadlines compute is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run(payload: dict, ctx: Any) -> dict:
    del ctx
    return _compute().compute(payload or {})
