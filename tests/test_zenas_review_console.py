"""The review console shows one locked deadline, and only a reviewer can open it."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "workspace" / "app" / "review" / "example.ts"
PAGE = ROOT / "workspace" / "app" / "review" / "page.tsx"
CONSOLE = ROOT / "workspace" / "app" / "review" / "console.tsx"
ACCESS = ROOT / "workspace" / "app" / "review" / "access.ts"
CASES = ROOT / "tank" / "skills" / "zenas.deadlines" / "1.0.0" / "evals" / "cases.jsonl"
COMPUTE = ROOT / "tank" / "skills" / "zenas.deadlines" / "1.0.0" / "compute.py"


def _first_case() -> dict:
    return json.loads(CASES.read_text(encoding="utf-8").splitlines()[0])


def test_the_console_shows_the_locked_worked_example() -> None:
    case = _first_case()
    example = EXAMPLE.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    console = CONSOLE.read_text(encoding="utf-8")
    assert case["name"] in example
    assert case["expected"]["deadline"] in example
    assert case["input"]["trigger"] in example
    assert str(case["input"]["days"]) in example
    for step in case["expected"]["steps"]:
        assert step["rule"] in example
        assert step["text"] in example
    assert "Worked example" in example
    assert "United States" in example
    assert "not a client matter" in example
    assert "SOUTH AFRICA" not in console
    assert "WORKED_EXAMPLE" in console
    assert "requireReviewer" in page
    assert "ReviewConsole" in page


def test_a_reviewer_opens_the_console_and_the_logos_stay_official() -> None:
    access = ACCESS.read_text(encoding="utf-8")
    console = CONSOLE.read_text(encoding="utf-8")
    assert 'role !== "reviewer"' in access
    assert "admission_no" in access
    assert "/brand/zenas-on-dark.png" in console
    assert "/brand/tervory-logo-reversed.png" in console
    assert 'account.trim() || "Your account"' in console
    assert "Account name" not in console
    assert "sign-up" not in console
    assert "Case name v Case name" not in console
    assert "Case name v Case name" not in EXAMPLE.read_text(encoding="utf-8")


def test_this_pass_does_not_rewrite_the_festive_pause() -> None:
    text = COMPUTE.read_text(encoding="utf-8")
    assert "16 December to 15 January, both inclusive." in text
