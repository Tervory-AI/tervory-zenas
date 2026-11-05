"""scan.secrets 1.0.0 — credentials in handed-over files. No network."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _engine():
    path = Path(__file__).with_name("engine.py")
    spec = importlib.util.spec_from_file_location("scan_secrets_engine", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("scan.secrets engine is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run(payload: dict, ctx: object) -> dict:
    del ctx
    if not isinstance(payload, dict):
        payload = {}
    return _engine().run_skill(payload)
