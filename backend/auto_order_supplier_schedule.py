"""Supplier calendar for weekly Friday tobacco deliveries (Europe/Rome).

Orders placed Monday, Tuesday, or Wednesday strictly BEFORE 11:00 local time
qualify for delivery that same Friday. Any later order targets the next Friday.
Monday is the preferred planning/ordering day, not an exclusive order date.

These are commercial rules supplied by the operator, not guaranteed actual
delivery times or a substitute for checking supplier holidays/closures.
"""
from __future__ import annotations

from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")


def friday_delivery_schedule(now: datetime) -> dict:
    """Deterministic supplier timetable for timezone-aware or Rome-local datetimes."""
    if now.tzinfo is None:
        raise ValueError("Ora senza fuso: usare Europe/Rome")
    local = now.astimezone(ROME)
    day = local.date()
    # Days until the Friday of the current week.
    days_to_friday = 4 - day.weekday()
    same_week = (
        (day.weekday() in (0, 1))
        or (day.weekday() == 2 and local.time() < time(11, 0))
    )
    delivery = day + timedelta(days=days_to_friday + (0 if same_week else 7))
    # The valid booking cutoff is the Wednesday immediately preceding delivery.
    cutoff_date = delivery - timedelta(days=2)
    cutoff = datetime.combine(cutoff_date, time(11, 0), tzinfo=ROME)
    preferred = delivery - timedelta(days=4)
    return {
        "mode": "FRIDAY_WEEKLY",
        "timezone": "Europe/Rome",
        "preferred_order_day": "MONDAY",
        "preferred_order_date": preferred.isoformat(),
        "cutoff_weekday": "WEDNESDAY",
        "cutoff_local_time": "11:00",
        "cutoff_datetime": cutoff.isoformat(),
        "delivery_weekday": "FRIDAY",
        "delivery_date": delivery.isoformat(),
        "lead_days": (delivery - day).days,
        "review_days": 7,
        "same_week_available": same_week,
        "late_for_this_friday": not same_week,
        "delivery_time_known": False,
    }
