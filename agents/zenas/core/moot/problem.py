"""Civil appeal counsel projection. Bench notes stay private."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_BENCH_PRIVATE = frozenset(
    {
        "bench_notes",
        "real_holding",
        "press_questions",
        "binding_on_issues",
    }
)

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
