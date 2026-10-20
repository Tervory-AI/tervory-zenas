"""zenas.deadlines 1.1.0 is pure code. The dates below were counted by hand.

United States, 14 days from Thursday 2 July 2026 under FRCP 6(a)(1):
the trigger day is out, so the 14 calendar days are 3 July through 16 July.
3 July is Independence Day observed (4 July is a Saturday), 4-5 July and
11-12 July are the weekends, and Thursday 16 July is a weekday that is not
a holiday. The deadline is 16 July 2026.

South Africa, notice of intention to defend served Friday 24 April 2026.
Uniform Rule 19(1) allows 10 court days, and Uniform Rule 1 drops Saturdays,
Sundays, and public holidays. After 24 April the court days are 28, 29 and
30 April, then 4, 5, 6, 7, 8, 11 and 12 May. 25-26 April are a weekend,
27 April is Freedom Day, and 1 May is Workers' Day. The deadline is
12 May 2026.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "agents" / "zenas" / "core" / "deadlines.py"
SKILL = ROOT / "tank" / "skills" / "zenas.deadlines" / "1.1.0"
COMPUTE = SKILL / "compute.py"
RUN = SKILL / "run.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


compute = _load(COMPUTE, "zenas_deadlines_11_core")
run_mod = _load(RUN, "zenas_deadlines_11_run")


def _rules(result: dict) -> list[str]:
    return [step["rule"] for step in result["steps"]]


def test_the_box_and_the_current_skill_are_the_same_calculator() -> None:
    assert CORE.read_bytes() == (SKILL.parent / "1.2.0" / "compute.py").read_bytes()


def test_us_fourteen_days_include_a_weekend_and_independence_day() -> None:
    result = compute.compute({"jurisdiction": "us", "trigger": "2026-07-02", "days": 14})
    assert result["ok"] is True
    assert result["deadline"] == "2026-07-16"
    assert _rules(result) == [
        "FRCP 6(a)(1)(A)",
        "FRCP 6(a)(1)(B)",
        "FRCP 6(a)(1)(C)",
        "FRCP 6(a)(6)",
    ]
    counted = result["steps"][1]["text"]
    assert "2026-07-04 (Saturday)" in counted
    assert "2026-07-03 (Independence Day, observed)" in counted


def test_us_last_day_rolls_off_an_observed_holiday_and_the_weekend() -> None:
    result = compute.compute({"jurisdiction": "us", "trigger": "2026-07-02", "days": 1})
    assert result["deadline"] == "2026-07-06"
    assert "FRCP 6(a)(1)(C)" in _rules(result)
    assert "FRCP 6(a)(6)" in _rules(result)


def test_us_hours_and_a_state_holiday_still_roll_forward() -> None:
    hours = compute.compute(
        {"jurisdiction": "us", "trigger": "2026-01-02T10:00:00", "unit": "hours", "hours": 30}
    )
    assert hours["deadline"] == "2026-01-05T16:00"
    state = compute.compute(
        {"jurisdiction": "us", "trigger": "2026-03-19", "days": 1, "state_holidays": ["2026-03-20"]}
    )
    assert state["deadline"] == "2026-03-23"
    assert "FRCP 6(a)(6)(C)" in _rules(state)


def test_sa_notice_crosses_a_weekend_and_two_public_holidays() -> None:
    result = compute.compute(
        {"jurisdiction": "za", "trigger": "2026-04-24", "kind": "notice_of_intention_to_defend"}
    )
    assert result["deadline"] == "2026-05-12"
    assert result["days"] == 10
    assert result["dies_non"] is True
    assert "Uniform Rule 19(1)" in _rules(result)
    assert "Uniform Rule 1" in _rules(result)
    assert "Public Holidays Act 36 of 1994" in _rules(result)
    court = next(step["text"] for step in result["steps"] if step["rule"] == "Uniform Rule 1")
    assert "2026-04-25" in court
    assert "Freedom Day" in court
    assert "Workers' Day" in court


def test_festive_days_are_skipped_for_a_notice_and_counted_for_a_plea() -> None:
    notice = compute.compute(
        {"jurisdiction": "za", "trigger": "2026-12-11", "kind": "notice_of_intention_to_defend"}
    )
    plea = compute.compute({"jurisdiction": "za", "trigger": "2026-12-11", "kind": "plea"})
    assert notice["deadline"] == "2027-01-27"
    assert notice["dies_non"] is True
    assert plea["deadline"] == "2027-01-13"
    assert plea["dies_non"] is False
    assert "Uniform Rule 22(1)" in _rules(plea)
    assert "Uniform Rule 19(1)" not in _rules(plea)
    assert "Christmas Day" in plea["steps"][1]["text"]


def test_a_sunday_public_holiday_is_also_observed_on_monday() -> None:
    result = compute.compute({"jurisdiction": "za", "trigger": "2023-09-22", "days": 1})
    assert result["deadline"] == "2023-09-26"
    assert "Heritage Day, observed" in result["steps"][1]["text"]
    assert result["dies_non"] is False


def test_the_state_period_does_not_use_the_rule_19_1_exclusion() -> None:
    result = compute.compute({"jurisdiction": "za", "trigger": "2026-03-02", "service": "state"})
    assert result["deadline"] == "2026-03-30"
    assert result["days"] == 20
    assert result["dies_non"] is False
    assert "Uniform Rule 19(2)" in _rules(result)
    assert "Uniform Rule 19(1)" not in _rules(result)


def test_criminal_law_is_refused() -> None:
    result = compute.compute({"jurisdiction": "za", "trigger": "2026-04-24", "days": 5, "criminal": True})
    assert result["ok"] is False
    assert "Criminal" in result["reason"]
    assert "deadline" not in result
    bail = compute.compute({"jurisdiction": "us", "trigger": "2026-07-02", "days": 1, "kind": "bail"})
    assert bail["ok"] is False


def test_each_step_cites_a_rule() -> None:
    result = compute.compute(
        {"jurisdiction": "za", "trigger": "2026-04-24", "kind": "notice_of_intention_to_defend"}
    )
    assert result["steps"]
    for step in result["steps"]:
        assert step["rule"]
        assert step["text"]


def test_easter_anchors_the_movable_holidays() -> None:
    assert compute.easter(2024).isoformat() == "2024-03-31"
    assert compute.easter(2025).isoformat() == "2025-04-20"
    assert compute.easter(2026).isoformat() == "2026-04-05"


@pytest.mark.asyncio
async def test_the_skill_does_not_call_a_model() -> None:
    class _Ctx:
        async def model(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("a deadline must not call a model")

        http = None

    result = await run_mod.run({"jurisdiction": "us", "trigger": "2026-07-02", "days": 14}, _Ctx())
    assert result["deadline"] == "2026-07-16"
    assert result["ok"] is True
