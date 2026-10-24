"""Moot problem built from a fixture civil appeal (Z8-shaped). Bench notes stay private."""

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

_FIXTURES = Path(__file__).with_name("fixtures")

_BENCH_PRIVATE = frozenset(
    {
        "bench_notes",
        "real_holding",
        "press_questions",
        "binding_on_issues",
    }
)

# In-memory founder edits for fixture problems (no DB in offline tests).
_EDITED: dict[str, dict[str, Any]] = {}


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
    if problem_id in _EDITED:
        data = deepcopy(_EDITED[problem_id])
    else:
        path = _index().get(problem_id)
        if path is None:
            raise KeyError(f"unknown moot fixture: {problem_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
    if str(data.get("kind") or "") != "civil_appeal":
        raise ValueError("moot fixtures must be civil appeals")
    if data.get("criminal"):
        raise ValueError("criminal fixtures are refused")
    data.setdefault("locked", False)
    return data


def edit_problem(problem_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Founder edits a fixture problem. Refused when locked. Never accepts criminal=true."""
    current = load_fixture_problem(problem_id)
    if current.get("locked"):
        raise ValueError("problem is locked")
    if changes.get("criminal") is True:
        raise ValueError("criminal problems are refused")
    # Bench-private keys may be edited by founder only through this path.
    allowed = {
        "title",
        "facts",
        "grounds_of_appeal",
        "bundles",
        "bench_notes",
        "real_holding",
        "source_url",
    }
    updated = deepcopy(current)
    for key, val in changes.items():
        if key not in allowed:
            raise ValueError(f"cannot edit field: {key}")
        updated[key] = deepcopy(val)
    _EDITED[problem_id] = updated
    return deepcopy(updated)


def lock_problem(problem_id: str) -> dict[str, Any]:
    current = load_fixture_problem(problem_id)
    current["locked"] = True
    _EDITED[problem_id] = current
    return deepcopy(current)


def reset_edits() -> None:
    """Test helper: clear in-memory founder edits."""
    _EDITED.clear()


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
    _strip_private(public)
    return public


def bench_view(problem: dict[str, Any]) -> dict[str, Any]:
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
