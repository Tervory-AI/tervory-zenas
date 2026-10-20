"""Court deadlines from a trigger date. Pure code. No model and no network.

United States: Federal Rule of Civil Procedure 6.
South Africa: Uniform Rules of Court. Court days, public holidays, and the
16 December to 15 January exclusion only where that rule states it.
Every step names the rule it applied.

The Zenas box and zenas.deadlines 1.2.0 keep this file byte-identical:
agents/zenas/core/deadlines.py and tank/skills/zenas.deadlines/1.2.0/compute.py.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

_US = "us"
_ZA = "za"

# Named civil steps. The third value is whether that rule excludes
# 16 December to 15 January. Rule 19(2) and Rule 22(1) do not.
_ZA_STEPS: dict[str, tuple[int, str, bool]] = {
    "notice_of_intention_to_defend": (10, "Uniform Rule 19(1)", True),
    "state_notice": (20, "Uniform Rule 19(2)", False),
    "plea": (20, "Uniform Rule 22(1)", False),
    "bar": (5, "Uniform Rule 26", True),
}
_CRIMINAL = {"bail", "sentence", "sentencing", "charge", "prosecution", "criminal", "criminal_plea"}


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
    """5 U.S.C. § 6103(b): a Saturday holiday is observed Friday, a Sunday holiday Monday."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def us_holiday_names(year: int) -> dict[date, str]:
    """Day set aside by FRCP 6(a)(6)(A) and 5 U.S.C. § 6103, on the observed weekday."""
    fixed = (
        (date(year, 1, 1), "New Year's Day"),
        (date(year, 6, 19), "Juneteenth"),
        (date(year, 7, 4), "Independence Day"),
        (date(year, 11, 11), "Veterans Day"),
        (date(year, 12, 25), "Christmas Day"),
    )
    floating = (
        (_nth_weekday(year, 1, 0, 3), "Birthday of Martin Luther King, Jr."),
        (_nth_weekday(year, 2, 0, 3), "Washington's Birthday"),
        (_last_weekday(year, 5, 0), "Memorial Day"),
        (_nth_weekday(year, 9, 0, 1), "Labor Day"),
        (_nth_weekday(year, 10, 0, 2), "Columbus Day"),
        (_nth_weekday(year, 11, 3, 4), "Thanksgiving Day"),
    )
    named: dict[date, str] = {}
    for day, name in fixed:
        observed = _observed(day)
        named[observed] = name if observed == day else f"{name}, observed"
    for day, name in floating:
        named[day] = name
    return named


def us_legal_holidays(year: int) -> set[date]:
    return set(us_holiday_names(year))


def _us_name(day: date, extra: set[date]) -> str | None:
    for year in (day.year - 1, day.year, day.year + 1):
        name = us_holiday_names(year).get(day)
        if name:
            return name
    if day in extra:
        return "state holiday"
    return None


def _us_holiday(day: date, extra: set[date]) -> bool:
    return _us_name(day, extra) is not None


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


def sa_base_holidays(year: int) -> dict[date, str]:
    """Public holidays named in the Public Holidays Act 36 of 1994, before Sunday observance."""
    sunday = easter(year)
    return {
        date(year, 1, 1): "New Year's Day",
        date(year, 3, 21): "Human Rights Day",
        sunday - timedelta(days=2): "Good Friday",
        sunday + timedelta(days=1): "Family Day",
        date(year, 4, 27): "Freedom Day",
        date(year, 5, 1): "Workers' Day",
        date(year, 6, 16): "Youth Day",
        date(year, 8, 9): "National Women's Day",
        date(year, 9, 24): "Heritage Day",
        date(year, 12, 16): "Day of Reconciliation",
        date(year, 12, 25): "Christmas Day",
        date(year, 12, 26): "Day of Goodwill",
    }


def sa_holiday_names(year: int) -> dict[date, str]:
    """A Sunday holiday is also observed on the Monday."""
    base = sa_base_holidays(year)
    named = dict(base)
    for day, name in base.items():
        if day.weekday() == 6:
            monday = day + timedelta(days=1)
            named.setdefault(monday, f"{name}, observed")
    return named


def sa_public_holidays(year: int) -> set[date]:
    return set(sa_holiday_names(year))


def _sa_name(day: date, proclaimed: set[date] | None = None) -> str | None:
    if proclaimed:
        if day in proclaimed:
            return "proclaimed public holiday"
        if day.weekday() == 0 and day - timedelta(days=1) in proclaimed:
            return "proclaimed public holiday, observed"
    return sa_holiday_names(day.year).get(day)


def _dies_non(day: date) -> bool:
    """16 December to 15 January, both inclusive."""
    return (day.month == 12 and day.day >= 16) or (day.month == 1 and day.day <= 15)


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


def _join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _listed(days: list[date], limit: int = 6) -> str:
    shown = [day.isoformat() for day in days[:limit]]
    extra = len(days) - len(shown)
    if extra:
        return ", ".join(shown) + f", and {extra} more"
    return _join(shown)


def _criminal(body: dict[str, Any]) -> bool:
    if body.get("criminal") is True:
        return True
    kind = str(body.get("kind") or body.get("step") or "").strip().lower().replace(" ", "_")
    return kind in _CRIMINAL


def _za_step(body: dict[str, Any]) -> tuple[int, str, bool] | None:
    kind = str(body.get("kind") or body.get("step") or "").strip().lower().replace(" ", "_")
    service = str(body.get("service") or "").strip().lower()
    if kind in _ZA_STEPS:
        return _ZA_STEPS[kind]
    if service == "within":
        return _ZA_STEPS["notice_of_intention_to_defend"]
    if service == "state":
        return _ZA_STEPS["state_notice"]
    return None


def _us_days(trigger: date, days: int, extra: set[date]) -> tuple[date, list[dict[str, str]]]:
    start = trigger + timedelta(days=1)
    raw_end = start + timedelta(days=days - 1)
    steps = [
        _step(
            "FRCP 6(a)(1)(A)",
            f"The trigger day {trigger.isoformat()} is excluded. Counting starts {start.isoformat()}.",
        )
    ]
    between: list[str] = []
    saw_federal = False
    saw_state = False
    cursor = start
    while cursor < raw_end:
        federal = _us_name(cursor, set())
        if cursor.weekday() >= 5:
            label = "Saturday" if cursor.weekday() == 5 else "Sunday"
            between.append(f"{cursor.isoformat()} ({label})")
        elif federal:
            between.append(f"{cursor.isoformat()} ({federal})")
            saw_federal = True
        elif cursor in extra:
            between.append(f"{cursor.isoformat()} (state holiday)")
            saw_state = True
        cursor += timedelta(days=1)
    counted = (
        f"Counted {days} calendar {'day' if days == 1 else 'days'}, "
        "including intermediate Saturdays, Sundays, and legal holidays, "
        f"through {raw_end.isoformat()}."
    )
    if between:
        counted += " Those days include " + _join(between) + "."
    steps.append(_step("FRCP 6(a)(1)(B)", counted))

    end = raw_end
    reasons: list[str] = []
    while end.weekday() >= 5 or _us_holiday(end, extra):
        if end.weekday() == 5:
            reasons.append(f"{end.isoformat()}, a Saturday")
        elif end.weekday() == 6:
            reasons.append(f"{end.isoformat()}, a Sunday")
        else:
            name = _us_name(end, extra) or "legal holiday"
            reasons.append(f"{end.isoformat()}, a legal holiday ({name})")
            if _us_name(end, set()):
                saw_federal = True
            elif end in extra:
                saw_state = True
        end += timedelta(days=1)
    if reasons:
        steps.append(
            _step(
                "FRCP 6(a)(1)(C)",
                "The last day cannot be " + "; ".join(reasons) + f". The period continues to {end.isoformat()}.",
            )
        )
    else:
        steps.append(
            _step(
                "FRCP 6(a)(1)(C)",
                f"The last day {end.isoformat()} is a weekday and not a legal holiday, so it is included.",
            )
        )
    if saw_federal:
        steps.append(
            _step(
                "FRCP 6(a)(6)",
                "A legal holiday is a day set aside by FRCP 6(a)(6)(A) or 5 U.S.C. § 6103, including Juneteenth. When that day falls on a Saturday it is observed on Friday, and on a Sunday it is observed on Monday.",
            )
        )
    if saw_state:
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
                f"That moment falls on a Saturday, Sunday, or legal holiday, so the period continues to the same time on {end.isoformat(timespec='minutes')}.",
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


def _sa_days(
    trigger: date,
    days: int,
    *,
    dies_non: bool,
    length_rule: str,
    trigger_field: str,
    proclaimed: set[date],
) -> tuple[date, list[dict[str, str]]]:
    verb = "runs" if days == 1 else "run"
    if trigger_field == "delivery_date":
        start_text = f"The delivery date is not counted: {trigger.isoformat()}."
    elif trigger_field == "service_date":
        start_text = f"The sheriff's service date {trigger.isoformat()} is not counted."
    else:
        start_text = f"The trigger date {trigger.isoformat()} is not counted."
    steps = [
        _step(
            length_rule,
            f"{start_text} {days} court {'day' if days == 1 else 'days'} {verb} after it.",
        )
    ]
    cursor = trigger
    counted: list[date] = []
    weekends: list[date] = []
    holidays: list[date] = []
    skipped_dies = 0
    while len(counted) < days:
        cursor += timedelta(days=1)
        if dies_non and _dies_non(cursor):
            skipped_dies += 1
            continue
        if cursor.weekday() >= 5:
            weekends.append(cursor)
            continue
        if _sa_name(cursor, proclaimed):
            holidays.append(cursor)
            continue
        counted.append(cursor)
    end = counted[-1]
    court = "A court day is not a Saturday, Sunday, or public holiday."
    if weekends:
        court += f" Weekends skipped: {_listed(weekends)}."
    if holidays:
        named = [f"{day.isoformat()} ({_sa_name(day, proclaimed)})" for day in holidays]
        court += f" Public holidays skipped: {_join(named)}."
    court += f" The last court day is {end.isoformat()}."
    steps.append(_step("Uniform Rule 1", court))
    if holidays:
        steps.append(
            _step(
                "Public Holidays Act 36 of 1994",
                "A public holiday is not a court day. When a public holiday falls on a Sunday, the following Monday is also a public holiday.",
            )
        )
    if proclaimed:
        steps.append(_step(
            "Public Holidays Act 36 of 1994",
            "Proclaimed public holidays supplied: " + _join([day.isoformat() for day in sorted(proclaimed)]) + ". These are excluded as public holidays, including the following Monday when a proclaimed holiday falls on Sunday.",
        ))
    if dies_non:
        if skipped_dies:
            detail = f"{skipped_dies} days in that stretch were not counted."
        else:
            detail = "None of those days fell in this period."
        steps.append(
            _step(
                length_rule,
                "The days from 16 December to 15 January, both inclusive, are not counted in the time this rule allows. " + detail,
            )
        )
    return end, steps


def compute(payload: dict[str, Any] | None) -> dict[str, Any]:
    body = payload or {}
    if _criminal(body):
        return {"ok": False, "reason": "Criminal law is not offered. This counts civil deadlines only."}
    jurisdiction = body.get("jurisdiction")
    if jurisdiction not in (_US, _ZA):
        return {"ok": False, "reason": "jurisdiction must be us or za"}
    unit = str(body.get("unit") or "days")
    extra: set[date] = set()
    for item in body.get("state_holidays") or []:
        parsed = _parse_date(str(item))
        if parsed is not None:
            extra.add(parsed)

    if unit == "hours":
        if jurisdiction != _US:
            return {"ok": False, "reason": "South African periods are counted in court days"}
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

    named = _za_step(body) if jurisdiction == _ZA else None
    trigger_field = "trigger"
    proclaimed: set[date] = set()
    if jurisdiction == _ZA:
        kind = str(body.get("kind") or body.get("step") or "").strip().lower().replace(" ", "_")
        if named and named[1] in ("Uniform Rule 22(1)", "Uniform Rule 26"):
            trigger_field = "delivery_date"
        elif named or kind == "after_service":
            trigger_field = "service_date"
        supplied = body.get("proclaimed_holidays", [])
        if not isinstance(supplied, list):
            return {"ok": False, "reason": "proclaimed_holidays must be a list of dates"}
        for item in supplied:
            parsed = _parse_date(item) if isinstance(item, str) and len(item) == 10 else None
            if parsed is None:
                return {"ok": False, "reason": "proclaimed_holidays must contain valid YYYY-MM-DD dates"}
            proclaimed.add(parsed)
    trigger = _parse_date(str(body.get(trigger_field) or ""))
    if trigger is None:
        if trigger_field == "service_date":
            reason = "service_date from the sheriff's return of service is required"
        elif trigger_field == "delivery_date":
            reason = "delivery_date is required for a plea or notice of bar"
        else:
            reason = "trigger must be a date"
        return {"ok": False, "reason": reason}

    if named is not None:
        count, length_rule, dies_non = named
    else:
        try:
            count = int(body.get("days") or 0)
        except (TypeError, ValueError):
            count = 0
        if jurisdiction == _ZA:
            length_rule = "Uniform Rule 1"
            dies_non = False
        else:
            length_rule = "FRCP 6(a)(1)"
            dies_non = False
    if "dies_non" in body and jurisdiction == _ZA:
        dies_non = body.get("dies_non") is True
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
    end, steps = _sa_days(trigger, count, dies_non=dies_non, length_rule=length_rule, trigger_field=trigger_field, proclaimed=proclaimed)
    return {
        "ok": True,
        "jurisdiction": _ZA,
        trigger_field: trigger.isoformat(),
        "trigger": trigger.isoformat(),
        "deadline": end.isoformat(),
        "days": count,
        "unit": "court_days",
        "dies_non": dies_non,
        "steps": steps,
    }
