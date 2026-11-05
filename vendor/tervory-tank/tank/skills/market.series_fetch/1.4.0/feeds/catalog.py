"""Load sources from the named engine. The engine is required."""

from __future__ import annotations

import importlib
import re

from feeds.models import SourceSpec

_ENGINE = re.compile(r"^[a-z][a-z0-9_]*$")


def sources(engine: str) -> list[SourceSpec]:
    if not isinstance(engine, str) or _ENGINE.fullmatch(engine) is None:
        raise SystemExit("engine is required")
    module = importlib.import_module(f"feeds.engines.{engine}")
    build = getattr(module, "sources", None)
    if not callable(build):
        raise SystemExit(f"unknown engine {engine}")
    return list(build())
