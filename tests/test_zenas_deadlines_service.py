import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "tank/skills/zenas.deadlines/1.2.0"
spec = importlib.util.spec_from_file_location("service_deadlines", SKILL / "run.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.asyncio
async def test_service_date_controls_sa_even_when_order_date_is_earlier():
    result = await module.run({
        "jurisdiction": "za", "service_date": "2026-04-24",
        "trigger": "2026-04-01", "order_date": "2026-04-01",
        "kind": "notice_of_intention_to_defend",
    }, None)
    assert result["deadline"] == "2026-05-12"
    assert result["service_date"] == result["trigger"] == "2026-04-24"
    assert "sheriff's service date" in result["steps"][0]["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("service_date", [None, "", "not a date", "2026-02-30"])
async def test_missing_or_invalid_service_never_uses_order(service_date):
    result = await module.run({
        "jurisdiction": "za", "service_date": service_date, "trigger": "2026-04-24", "days": 10,
        "kind": "after_service",
    }, None)
    assert not result["ok"]
    assert "service_date" in result["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["plea", "bar"])
@pytest.mark.parametrize("delivery_date", [None, "", "not a date", "2026-02-30"])
async def test_delivery_required_without_falling_back(kind, delivery_date):
    result = await module.run({
        "jurisdiction": "za", "kind": kind, "delivery_date": delivery_date,
        "service_date": "2026-03-02", "trigger": "2026-03-02", "order_date": "2026-03-02",
    }, None)
    assert result == {"ok": False, "reason": "delivery_date is required for a plea or notice of bar"}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,deadline", [("plea", "2026-03-30"), ("bar", "2026-03-09")])
async def test_delivery_period_needs_no_service_date(kind, deadline):
    result = await module.run({
        "jurisdiction": "za", "kind": kind, "delivery_date": "2026-03-02",
        "trigger": "2026-01-02", "order_date": "2026-01-02",
    }, None)
    assert result["deadline"] == deadline
    assert result["delivery_date"] == result["trigger"] == "2026-03-02"
    assert "service_date" not in result
    assert "the delivery date is not counted" in result["steps"][0]["text"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", [
    {"kind": "notice_of_intention_to_defend"}, {"kind": "state_notice"},
    {"kind": "after_service"}, {"step": "after service"},
    {"service": "within"}, {"service": "state"},
])
async def test_service_periods_require_service_not_delivery(selector):
    result = await module.run({
        "jurisdiction": "za", "days": 1, "delivery_date": "2026-03-02",
        "trigger": "2026-03-02", **selector,
    }, None)
    assert not result["ok"]
    assert "service_date" in result["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["kind", "step"])
@pytest.mark.parametrize("extra", [{}, {"days": 10}])
async def test_spaced_notice_requires_service_date_with_only_trigger(selector, extra):
    result = await module.run({
        "jurisdiction": "za", selector: "notice of intention to defend",
        "trigger": "2026-03-02", **extra,
    }, None)
    assert result == {
        "ok": False,
        "reason": "service_date from the sheriff's return of service is required",
    }


@pytest.mark.asyncio
async def test_proclaimed_sunday_holiday_observed_on_monday():
    result = await module.run({
        "jurisdiction": "za", "kind": "after_service", "service_date": "2026-03-06",
        "days": 1, "proclaimed_holidays": ["2026-03-08", "2026-03-08"],
    }, None)
    assert result["deadline"] == "2026-03-10"
    assert "2026-03-09 (proclaimed public holiday, observed)" in result["steps"][1]["text"]
    assert "2026-03-08" in result["steps"][-1]["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("holidays", ["2024-05-29", ["bad"], ["2026-02-30"], [None]])
async def test_invalid_proclaimed_holidays_are_not_silently_ignored(holidays):
    result = await module.run({
        "jurisdiction": "za", "trigger": "2024-05-28", "days": 1,
        "proclaimed_holidays": holidays,
    }, None)
    assert not result["ok"]
    assert "proclaimed_holidays" in result["reason"]


@pytest.mark.asyncio
async def test_version_evals():
    for line in (SKILL / "evals/cases.jsonl").read_text().splitlines():
        case = json.loads(line)
        result = await module.run(case["input"], None)
        for key, value in case["expected"].items():
            assert result[key] == value, case["name"]
