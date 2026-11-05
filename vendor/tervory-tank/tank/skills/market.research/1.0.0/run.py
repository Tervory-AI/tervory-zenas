"""market.research — a short answer, each claim with a source URL and a date."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _load() -> Any:
    here = Path(__file__).resolve()
    names = (
        "agents/market_lab/core/pure.py",
        "skills/market.series_fetch/1.0.0/pure.py",
        "market.series_fetch/1.0.0/pure.py",
    )
    for parent in here.parents:
        for name in names:
            path = parent / name
            if not path.is_file():
                continue
            spec = importlib.util.spec_from_file_location("market_lab_pure", path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise RuntimeError("market lab logic is missing")


def _has_source_passages(value: object) -> bool:
    return isinstance(value, list) and len(value) > 0


async def run(payload: dict, ctx) -> dict:
    data = dict(payload or {})
    if not _has_source_passages(data.get("passages")):
        return {
            "question": str(data.get("question") or "").strip(),
            "answer": "no sources yet",
            "citations": [],
            "cost_usd": 0,
        }
    pure = _load()
    return pure.research_answer(data)
