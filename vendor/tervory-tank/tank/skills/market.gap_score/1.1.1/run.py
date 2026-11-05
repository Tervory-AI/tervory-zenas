"""market.gap_score 1.1.0 — validated comparable area/segment snapshots."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def score(payload: dict) -> dict:
    spec = importlib.util.spec_from_file_location("market_gap_110_scoring", Path(__file__).with_name("scoring.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.gap_score(payload)


async def run(payload: dict, ctx) -> dict:
    return score(payload or {})
