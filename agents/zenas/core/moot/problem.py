"""Moot problem built from a fixture civil appeal (Z8-shaped). Bench notes stay private."""

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

_FIXTURES = Path(__file__).with_name("fixtures")

# Keys never shown on counsel pages or in counsel API responses.
_BENCH_PRIVATE = frozenset(
    {
        "bench_notes",
        "real_holding",
        "press_questions",
        "binding_on_issues",
    }
)


@lru_cache(maxsize=1)
def _index() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for path in sorted(_FIXTURES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        pid = str(data.get("id") or path.stem)
        out[pid] = path
    return out


def list_fixture_ids() -> list[str]:
    return sorted(_index())


def load_fixture_problem(problem_id: str) -> dict[str, Any]:
    path = _index().get(problem_id)
    if path is None:
        raise KeyError(f"unknown moot fixture: {problem_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if str(data.get("kind") or "") != "civil_appeal":
        raise ValueError("moot fixtures must be civil appeals")
    if data.get("criminal"):
        raise ValueError("criminal fixtures are refused")
    return data


def counsel_bundle(problem: dict[str, Any], side: str) -> dict[str, Any]:
    """Bundle for one side. Bench-private fields are stripped."""
    if side not in ("appellant", "respondent"):
        raise ValueError("side must be appellant or respondent")
    bundles = problem.get("bundles") or {}
    if side not in bundles:
        raise KeyError(f"no bundle for {side}")
    public = {k: deepcopy(v) for k, v in problem.items() if k not in _BENCH_PRIVATE and k != "bundles"}
    public["side"] = side
    public["bundle"] = deepcopy(bundles[side])
    # Defence in depth: never leak nested private keys.
    _strip_private(public)
    return public


def bench_view(problem: dict[str, Any]) -> dict[str, Any]:
    """Operator / judge view including private notes."""
    return deepcopy(problem)


def _strip_private(node: Any) -> None:
    if isinstance(node, dict):
        for key in list(node):
            if key in _BENCH_PRIVATE:
                del node[key]
            else:
                _strip_private(node[key])
    elif isinstance(node, list):
        for item in node:
            _strip_private(item)


def counsel_payload_leaks_bench(payload: dict[str, Any]) -> list[str]:
    """Return private key paths found in a counsel payload (empty = clean)."""
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                here = f"{path}.{key}" if path else key
                if key in _BENCH_PRIVATE:
                    found.append(here)
                walk(val, here)
        elif isinstance(node, list):
            for i, val in enumerate(node):
                walk(val, f"{path}[{i}]")

    walk(payload, "")
    return found
