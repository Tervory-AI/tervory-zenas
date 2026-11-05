"""A small five-field cron matcher. No external scheduler."""

from __future__ import annotations

from datetime import datetime, timezone


def _match_field(expr: str, value: int, low: int, high: int) -> bool:
    for part in expr.split(","):
        piece = part.strip()
        if not piece:
            return False
        step = 1
        if "/" in piece:
            base, step_text = piece.split("/", 1)
            step = int(step_text)
            if step <= 0:
                return False
        else:
            base = piece
        if base == "*":
            start, end = low, high
        elif "-" in base:
            left, right = base.split("-", 1)
            start, end = int(left), int(right)
        else:
            start = end = int(base)
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def _dow(when: datetime) -> int:
    """Cron day-of-week: 0 = Sunday … 6 = Saturday."""
    return (when.weekday() + 1) % 7


def cron_matches(expression: str, when: datetime) -> bool:
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("a cron expression needs 5 fields")
    minute, hour, dom, month, dow = fields
    if not (
        _match_field(minute, when.minute, 0, 59)
        and _match_field(hour, when.hour, 0, 23)
        and _match_field(month, when.month, 1, 12)
    ):
        return False
    dom_any = dom == "*"
    dow_any = dow == "*"
    dom_ok = _match_field(dom, when.day, 1, 31)
    dow_value = _dow(when)
    dow_ok = _match_field(dow, dow_value, 0, 7)
    if dow_value == 0:
        dow_ok = dow_ok or _match_field(dow, 7, 0, 7)
    if dom_any and dow_any:
        return True
    if dom_any:
        return dow_ok
    if dow_any:
        return dom_ok
    # Both day-of-month and day-of-week are set: standard cron matches either.
    return dom_ok or dow_ok


def is_due(expression: str, now: datetime, last_run: datetime | None) -> bool:
    """True when `now` falls in a cron minute that has not already run.

    One enqueue per matching minute. A missed minute is not replayed later.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    slot = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    if not cron_matches(expression, slot):
        return False
    if last_run is None:
        return True
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)
    return last_run.astimezone(timezone.utc) < slot
