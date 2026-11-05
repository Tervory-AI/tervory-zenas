"""zenas.pii — placeholders only. The raw identifier is not returned."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _detect() -> Any:
    path = Path(__file__).with_name("detect.py")
    spec = importlib.util.spec_from_file_location("zenas_pii_detect", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("zenas.pii detect is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run(payload: dict, ctx: Any) -> dict:
    del ctx
    module = _detect()
    text = str((payload or {}).get("text") or "")
    return module.public_view(module.detect(text))
