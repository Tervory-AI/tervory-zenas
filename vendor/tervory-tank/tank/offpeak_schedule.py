"""Choose among published prices for the selected model, without provider calls."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo


def select(info: dict, at: datetime, deadline: datetime, *, batch_discount=None) -> dict:
    """Reserve 120 seconds for foreground work or 24 hours for provider batches.

    Only configured prices participate. Equal-price windows prefer earlier
    completion. A deadline bounds waiting and dispatch time; it cannot guarantee
    an upstream provider completes successfully.
    """
    if at.utcoffset() is None or deadline.utcoffset() is None:
        raise ValueError("Scheduling requires timezone-aware timestamps")
    at, deadline = at.astimezone(timezone.utc), deadline.astimezone(timezone.utc)
    duration = timedelta(seconds=120)
    if at + duration > deadline:
        raise ValueError("Deadline leaves no foreground execution window")
    candidates = [(Decimal(1), at + duration, at, "foreground")]
    if batch_discount is not None and at + timedelta(hours=24) <= deadline:
        discount = Decimal(str(batch_discount))
        if not discount.is_finite() or not 0 < discount <= 1:
            raise ValueError("Invalid batch price")
        candidates.append((discount, at + timedelta(hours=24), at, "batch"))
    settings = info.get("offpeak")
    if settings:
        discount = Decimal(str(settings["charge_multiplier"]))
        if not discount.is_finite() or not 0 < discount <= 1:
            raise ValueError("Invalid off-peak price")
        zone = ZoneInfo(settings["timezone"])
        first = at.astimezone(zone).date()
        # Recurring prices repeat weekly; later identical windows cannot win.
        days = {first + timedelta(days=i) for i in range(8)}
        days.update(datetime.fromisoformat(d).date() for d in settings.get("dates", []))
        for day in sorted(days):
            windows = ([{"start": "00:00", "end": "24:00"}]
                       if day.isoformat() in settings.get("dates", []) else
                       [w for w in settings["windows"] if day.weekday() in w["days"]])
            for window in windows:
                def boundary(value, fold):
                    hour, minute = map(int, value.split(":"))
                    if hour == 24 and minute == 0:
                        return datetime.combine(day + timedelta(days=1), time(), zone).replace(fold=fold)
                    return datetime.combine(day, time(hour, minute), zone).replace(fold=fold)
                if window["start"] >= window["end"]:
                    raise ValueError("Invalid off-peak window")
                for fold in (0, 1):
                    wall = boundary(window["start"], fold)
                    # A spring-forward boundary may not exist. Move to the
                    # first real local minute, rather than inventing a rate.
                    for _ in range(1441):
                        restored = wall.astimezone(timezone.utc).astimezone(zone)
                        if restored.replace(tzinfo=None) == wall.replace(tzinfo=None):
                            break
                        wall = (wall + timedelta(minutes=1)).replace(fold=fold)
                    else:
                        continue
                    start = wall.astimezone(timezone.utc)
                    end = boundary(window["end"], fold).astimezone(timezone.utc)
                    start = max(at, start)
                    local = start.astimezone(zone)
                    if local.date() != day or not window["start"] <= local.strftime("%H:%M") < window["end"]:
                        continue
                    if start < end and start + duration <= deadline:
                        candidates.append((discount, start + duration, start, "foreground"))
    charge, _, start, mode = min(candidates)
    return {"dispatch_at": start.isoformat(), "deadline": deadline.isoformat(),
            "mode": mode, "charge_multiplier": float(charge)}
