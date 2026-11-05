"""Court deadlines from a trigger date. Pure code. No model and no network.

United States: Federal Rule of Civil Procedure 6.
South Africa: Uniform Rules of Court, court days and dies non.
Every step names the rule it applied.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

_US = "us"
_ZA = "za"


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    shift = (weekday - first.weekday()) % 7
    return first + timedelta(days=shift + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    while cursor.weekday() != weekday:
        cursor -= timedelta(days=1)
    return cursor


def _observed(day: date) -> date:
    """5 U.S.C. § 6103(b): Saturday holidays are observed Friday; Sunday, Monday."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def us_legal_holidays(year: int) -> set[date]:
    """Days set aside by FRCP 6(a)(6)(A) and 5 U.S.C. § 6103, on the observed weekday."""
    fixed = (
        date(year, 1, 1),
        date(year, 6, 19),
        date(year, 7, 4),
        date(year, 11, 11),
        date(year, 12, 25),
    )
    floating = (
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _last_weekday(year, 5, 0),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 10, 0, 2),
        _nth_weekday(year, 11, 3, 4),
    )
    return {_observed(day) for day in fixed} | set(floating)


def _us_holiday(day: date, extra: set[date]) -> bool:
    years = {day.year - 1, day.year, day.year + 1}
    holidays: set[date] = set()
    for year in years:
        holidays |= us_legal_holidays(year)
    return day in holidays or day in extra


def easter(year: int) -> date:
    """Anonymous Gregorian computus. Easter Sunday."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def sa_public_holidays(year: int) -> set[date]:
    """Public Holidays Act 36 of 1994. A Sunday holiday is also observed on Monday."""
    sunday = easter(year)
    days = {
        date(year, 1, 1),
        date(year, 3, 21),
        sunday - timedelta(days=2),
        sunday + timedelta(days=1),
        date(year, 4, 27),
        date(year, 5, 1),
        date(year, 6, 16),
        date(year, 8, 9),
        date(year, 9, 24),
        date(year, 12, 16),
        date(year, 12, 25),
        date(year, 12, 26),
    }
    observed = {day + timedelta(days=1) for day in days if day.weekday() == 6}
    return days | observed


def _dies_non(day: date) -> bool:
    """16 December to 15 January, both inclusive."""
    return (day.month == 12 and day.day >= 16) or (day.month == 1 and day.day <= 15)


def _court_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    return day not in sa_public_holidays(day.year)


def _parse_date(value: str) -> date | None:
    raw = (value or "").strip()
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _parse_when(value: str) -> datetime | None:
    raw = (value or "").strip()
    if len(raw) < 10:
        return None
    try:
        if "T" in raw:
            return datetime.fromisoformat(raw.replace("Z", ""))
        return datetime.combine(date.fromisoformat(raw[:10]), time(0, 0))
    except ValueError:
        return None


def _step(rule: str, text: str) -> dict[str, str]:
    return {"rule": rule, "text": text}


def _us_days(trigger: date, days: int, extra: set[date]) -> tuple[date, list[dict[str, str]]]:
    start = trigger + timedelta(days=1)
    steps = [
        _step(
            "FRCP 6(a)(1)(A)",
            f"The trigger day {trigger.isoformat()} is excluded. Counting starts {start.isoformat()}.",
        )
    ]
    end = start + timedelta(days=days - 1)
    steps.append(
        _step(
            "FRCP 6(a)(1)(B)",
            f"Counted {days} calendar {'day' if days == 1 else 'days'}, including intermediate Saturdays, Sundays, and legal holidays, through {end.isoformat()}.",
        )
    )
    reasons: list[str] = []
    holiday_hit = False
    state_hit = False
    while end.weekday() >= 5 or _us_holiday(end, extra):
        if end.weekday() == 5:
            reasons.append(f"{end.isoformat()} is a Saturday")
        elif end.weekday() == 6:
            reasons.append(f"{end.isoformat()} is a Sunday")
        else:
            reasons.append(f"{end.isoformat()} is a legal holiday")
            holiday_hit = True
            if end in extra:
                state_hit = True
        end += timedelta(days=1)
    if reasons:
        steps.append(
            _step(
                "FRCP 6(a)(1)(C)",
                "The last day cannot be " + "; ".join(reasons) + f". The period runs on until {end.isoformat()}.",
            )
        )
    else:
        steps.append(
            _step(
                "FRCP 6(a)(1)(C)",
                f"The last day {end.isoformat()} is a weekday and not a legal holiday, so it is included.",
            )
        )
    if holiday_hit:
        steps.append(
            _step(
                "FRCP 6(a)(6)",
                "A legal holiday is a day set aside by FRCP 6(a)(6)(A) or 5 U.S.C. § 6103, including Juneteenth. When that day falls on a Saturday it is observed on Friday, and on a Sunday it is observed on Monday.",
            )
        )
    if state_hit:
        steps.append(
            _step(
                "FRCP 6(a)(6)(C)",
                "For a period measured after an event, a holiday of the state where the district court sits is also a legal holiday.",
            )
        )
    return end, steps


def _us_hours(trigger: datetime, hours: int, extra: set[date]) -> tuple[datetime, list[dict[str, str]]]:
    steps = [
        _step(
            "FRCP 6(a)(2)(A)",
            f"Counting starts at once on {trigger.isoformat(timespec='minutes')}.",
        )
    ]
    end = trigger + timedelta(hours=hours)
    steps.append(
        _step(
            "FRCP 6(a)(2)(B)",
            f"Counted {hours} hours, including hours on Saturdays, Sundays, and legal holidays, to {end.isoformat(timespec='minutes')}.",
        )
    )
    if end.weekday() >= 5 or _us_holiday(end.date(), extra):
        while end.weekday() >= 5 or _us_holiday(end.date(), extra):
            end += timedelta(days=1)
        steps.append(
            _step(
                "FRCP 6(a)(2)(C)",
                f"That moment falls on a Saturday, Sunday, or legal holiday, so the period runs until the same time on {end.isoformat(timespec='minutes')}.",
            )
        )
    else:
        steps.append(
            _step(
                "FRCP 6(a)(2)(C)",
                f"That moment is not on a Saturday, Sunday, or legal holiday, so the deadline is {end.isoformat(timespec='minutes')}.",
            )
        )
    return end, steps


def _sa_days(trigger: date, days: int, *, dies_non: bool, length_rule: str) -> tuple[date, list[dict[str, str]]]:
    steps = [
        _step(
            length_rule,
            f"The day of the trigger {trigger.isoformat()} is not counted. {days} court {'day' if days == 1 else 'days'} run after it.",
        )
    ]
    cursor = trigger
    counted: list[date] = []
    skipped_dies = 0
    while len(counted) < days:
        cursor += timedelta(days=1)
        if dies_non and _dies_non(cursor):
            skipped_dies += 1
            continue
        if not _court_day(cursor):
            continue
        counted.append(cursor)
    end = counted[-1]
    steps.append(
        _step(
            "Uniform Rule 1",
            f"A court day is not a Saturday, Sunday, or public holiday under the Public Holidays Act 36 of 1994. A public holiday on a Sunday is also observed on the Monday. The last court day is {end.isoformat()}.",
        )
    )
    if dies_non:
        if skipped_dies:
            detail = f"{skipped_dies} days in that stretch were skipped."
        else:
            detail = "None of those days fell in this period."
        steps.append(
            _step(
                "Uniform Rule 19(1)",
                "The days from 16 December to 15 January, both inclusive, are not counted. " + detail,
            )
        )
    return end, steps


def compute(payload: dict[str, Any] | None) -> dict[str, Any]:
    body = payload or {}
    jurisdiction = body.get("jurisdiction")
    if jurisdiction not in (_US, _ZA):
        return {"ok": False, "reason": "jurisdiction must be us or za"}
    unit = str(body.get("unit") or "days")
    extra: set[date] = set()
    for item in body.get("state_holidays") or []:
        parsed = _parse_date(str(item))
        if parsed is not None:
            extra.add(parsed)

    if jurisdiction == _US and unit == "hours":
        trigger_at = _parse_when(str(body.get("trigger") or ""))
        try:
            hours = int(body.get("hours") or 0)
        except (TypeError, ValueError):
            hours = 0
        if trigger_at is None or hours < 1:
            return {"ok": False, "reason": "an hour period needs a trigger and a positive number of hours"}
        end_at, steps = _us_hours(trigger_at, hours, extra)
        return {
            "ok": True,
            "jurisdiction": _US,
            "trigger": trigger_at.isoformat(timespec="minutes"),
            "deadline": end_at.isoformat(timespec="minutes"),
            "days": hours,
            "unit": "hours",
            "steps": steps,
        }

    trigger = _parse_date(str(body.get("trigger") or ""))
    if trigger is None:
        return {"ok": False, "reason": "trigger must be a date"}

    service = str(body.get("service") or "")
    kind = str(body.get("kind") or "")
    if jurisdiction == _ZA and (kind == "notice_of_intention_to_defend" or service == "within"):
        count, length_rule, dies_non = 10, "Uniform Rule 19(1)", True
    elif jurisdiction == _ZA and (kind == "state_notice" or service == "state"):
        count, length_rule, dies_non = 20, "Uniform Rule 19(2)", True
    else:
        try:
            count = int(body.get("days") or 0)
        except (TypeError, ValueError):
            count = 0
        if jurisdiction == _ZA:
            length_rule = "Uniform Rule 1"
            dies_non = body.get("dies_non", True) is not False
        else:
            length_rule = "FRCP 6(a)(1)"
            dies_non = False
    if count < 1:
        return {"ok": False, "reason": "days must be a positive number"}

    if jurisdiction == _US:
        end, steps = _us_days(trigger, count, extra)
        return {
            "ok": True,
            "jurisdiction": _US,
            "trigger": trigger.isoformat(),
            "deadline": end.isoformat(),
            "days": count,
            "unit": "days",
            "steps": steps,
        }
    end, steps = _sa_days(trigger, count, dies_non=dies_non, length_rule=length_rule)
    return {
        "ok": True,
        "jurisdiction": _ZA,
        "trigger": trigger.isoformat(),
        "deadline": end.isoformat(),
        "days": count,
        "unit": "court_days",
        "steps": steps,
    }
