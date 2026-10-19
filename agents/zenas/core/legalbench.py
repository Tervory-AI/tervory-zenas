"""Public LegalBench slices that fit the launch areas.

The files are the published train rows. CI has no model, so the score is a
code baseline: question words found in the clause. It is not a model score.
LegalBench is United States material and runs only on the US box.
"""

from __future__ import annotations

import csv
from pathlib import Path

_DIR = Path(__file__).resolve().parent / "benchmarks"
_STOP = frozenset(
    {
        "does",
        "the",
        "clause",
        "discuss",
        "describe",
        "about",
        "this",
        "that",
        "with",
        "from",
        "have",
        "what",
        "when",
        "your",
        "into",
        "than",
        "then",
        "them",
        "they",
        "will",
        "shall",
        "must",
    }
)


def _rows(name: str) -> list[dict[str, str]]:
    path = _DIR / name
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _yes(question: str, text: str) -> str:
    words = []
    for raw in question.lower().replace("?", " ").split():
        word = "".join(ch for ch in raw if ch.isalpha())
        if len(word) >= 4 and word not in _STOP:
            words.append(word)
    hay = text.lower()
    return "Yes" if words and any(word in hay for word in words) else "No"


def _score(rows: list[dict[str, str]], *, question_key: str) -> dict[str, float | int]:
    if not rows:
        return {"cases": 0, "correct": 0, "outcome_agreement": 0.0}
    correct = 0
    for row in rows:
        question = row.get(question_key) or row.get("text") or ""
        predicted = _yes(question, row.get("text") or "")
        if predicted.lower() == str(row.get("answer") or "").strip().lower():
            correct += 1
    return {
        "cases": len(rows),
        "correct": correct,
        "outcome_agreement": round(100.0 * correct / len(rows), 2),
    }


def legalbench_scores() -> list[dict]:
    """Three launch-area slices. Empty when the files are missing."""
    specs = (
        ("contract_qa.tsv", "question", "contracts"),
        ("contract_nli.tsv", "text", "contracts"),
        ("proa.tsv", "text", "regulatory"),
    )
    found = []
    for name, key, area in specs:
        scored = _score(_rows(name), question_key=key)
        if not scored["cases"]:
            continue
        found.append(
            {
                "kind": "legalbench",
                "area": area,
                "jurisdiction": "us",
                "cases": scored["cases"],
                "outcome_agreement": scored["outcome_agreement"],
                "element_agreement": 0.0,
                "citations_valid": 0.0,
                "cost_per_case": 0.0,
                "detail": {
                    "set": name,
                    "label": "LegalBench code baseline",
                    "baseline": "question words in the clause",
                    "correct": scored["correct"],
                    "model": False,
                },
            }
        )
    return found
