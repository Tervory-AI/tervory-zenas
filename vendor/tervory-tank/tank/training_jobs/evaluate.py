# /// script
# requires-python = ">=3.11"
# dependencies = ["transformers==5.3.0", "torch==2.10.0", "huggingface-hub==1.7.2"]
# ///
"""Code-only outcome scoring for the Zenas court-text holdout."""
from __future__ import annotations

import json
import re
from pathlib import Path

OUTCOMES = frozenset({"upheld", "dismissed", "set aside", "granted", "refused", "settled"})


def outcome(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("outcome")
    text = " ".join(str(value or "").casefold().split())
    if text in OUTCOMES:
        return text
    match = re.search(r'"outcome"\s*:\s*"([^"]+)"', text)
    return match.group(1) if match and match.group(1) in OUTCOMES else None


def score_rows(rows: list[dict], answers: list[object]) -> dict[str, object]:
    if len(rows) != len(answers):
        raise ValueError("Every holdout row needs one answer")
    results = []
    for row, answer in zip(rows, answers, strict=True):
        expected = outcome(row.get("output"))
        if expected is None:
            raise ValueError("Holdout row has no code-extracted outcome")
        predicted = outcome(answer)
        results.append({"expected": expected, "predicted": predicted, "passed": predicted == expected})
    passed = sum(item["passed"] for item in results)
    return {"agreement": passed / len(results) if results else 0.0, "n": len(results),
            "passed": passed, "gold_holdout": True, "method": "exact_outcome", "rows": results}


def write_result(path: str | Path, rows: list[dict], answers: list[object]) -> dict[str, object]:
    result = score_rows(rows, answers)
    Path(path).write_text(json.dumps(result, separators=(",", ":")))
    return result
