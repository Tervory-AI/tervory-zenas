"""zenas.deadlines is pure code. Dates below were counted by hand from the rules."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPUTE = ROOT / "tank" / "skills" / "zenas.deadlines" / "1.0.0" / "compute.py"
RUN = ROOT / "tank" / "skills" / "zenas.deadlines" / "1.0.0" / "run.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


compute = _load(COMPUTE, "zenas_deadlines_compute_test")
run_mod = _load(RUN, "zenas_deadlines_run_test")


def _rules(result: dict) -> list[str]:
    return [step["rule"] for step in result["steps"]]


def test_frcp_fourteen_days_include_the_last_weekday() -> None:
    result = compute.compute({"jurisdiction": "us", "trigger": "2026-01-02", "days": 14})
    assert result["deadline"] == "2026-01-16"
    assert _rules(result)[:3] == ["FRCP 6(a)(1)(A)", "FRCP 6(a)(1)(B)", "FRCP 6(a)(1)(C)"]


def test_frcp_rolls_a_weekend_and_an_observed_holiday() -> None:
    weekend = compute.compute({"jurisdiction": "us", "trigger": "2026-06-19", "days": 1})
    assert weekend["deadline"] == "2026-06-22"
    holiday = compute.compute({"jurisdiction": "us", "trigger": "2026-07-02", "days": 1})
    assert holiday["deadline"] == "2026-07-06"
    assert "FRCP 6(a)(6)" in _rules(holiday)
    christmas = compute.compute({"jurisdiction": "us", "trigger": "2026-12-24", "days": 1})
    assert christmas["deadline"] == "2026-12-28"


def test_frcp_hours_roll_to_the_same_time_on_a_weekday() -> None:
    result = compute.compute(
        {"jurisdiction": "us", "trigger": "2026-01-02T10:00:00", "unit": "hours", "hours": 30}
    )
    assert result["deadline"] == "2026-01-05T16:00"
    assert "FRCP 6(a)(2)(C)" in _rules(result)


def test_frcp_state_holiday_on_the_last_day_is_skipped() -> None:
    result = compute.compute(
        {"jurisdiction": "us", "trigger": "2026-03-19", "days": 1, "state_holidays": ["2026-03-20"]}
    )
    assert result["deadline"] == "2026-03-23"
    assert "FRCP 6(a)(6)(C)" in _rules(result)


def test_uniform_rule_notice_and_dies_non() -> None:
    notice = compute.compute({"jurisdiction": "za", "trigger": "2026-03-02", "service": "within"})
    assert notice["deadline"] == "2026-03-16"
    assert notice["days"] == 10
    assert "Uniform Rule 19(1)" in _rules(notice)
    assert "Uniform Rule 1" in _rules(notice)
    across = compute.compute(
        {"jurisdiction": "za", "trigger": "2026-12-11", "kind": "notice_of_intention_to_defend"}
    )
    assert across["deadline"] == "2027-01-27"
    freedom = compute.compute({"jurisdiction": "za", "trigger": "2026-04-24", "days": 1})
    assert freedom["deadline"] == "2026-04-28"
    state = compute.compute({"jurisdiction": "za", "trigger": "2026-03-02", "service": "state"})
    assert state["deadline"] == "2026-03-30"
    assert state["days"] == 20
    assert "Uniform Rule 19(2)" in _rules(state)


def test_each_step_cites_a_rule() -> None:
    result = compute.compute({"jurisdiction": "za", "trigger": "2026-03-02", "service": "within"})
    assert result["steps"]
    for step in result["steps"]:
        assert step["rule"]
        assert step["text"]


@pytest.mark.asyncio
async def test_the_skill_does_not_call_a_model() -> None:
    class _Ctx:
        async def model(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("a deadline must not call a model")

        http = None

    result = await run_mod.run({"jurisdiction": "us", "trigger": "2026-01-02", "days": 14}, _Ctx())
    assert result["deadline"] == "2026-01-16"
    assert result["ok"] is True
