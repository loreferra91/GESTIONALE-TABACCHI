"""Supplier rules for the actual tobacco cadence supplied by the operator.

Ordinary week only: no assumption about public holidays or exceptional closures.
"""
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pytest

from backend.auto_order_supplier_schedule import friday_delivery_schedule, ROME
from backend.auto_order_3_operational import readiness, policy_snapshot
from backend.auto_order_3_service import compare_policies


@pytest.mark.parametrize("local,delivery,lead,same_week", [
    ("2026-11-09T08:30:00+01:00", "2026-11-13", 4, True),  # Monday
    ("2026-11-10T13:48:00+01:00", "2026-11-13", 3, True),  # Tuesday
    ("2026-11-11T10:59:59+01:00", "2026-11-13", 2, True),  # Last valid second
    ("2026-11-11T11:00:00+01:00", "2026-11-20", 9, False),  # Exact cutoff
    ("2026-11-11T11:00:01+01:00", "2026-11-20", 9, False),
    ("2026-11-12T09:00:00+01:00", "2026-11-20", 8, False),
    ("2026-11-13T09:00:00+01:00", "2026-11-20", 7, False),
    ("2026-11-14T09:00:00+01:00", "2026-11-20", 6, False),
    ("2026-11-15T09:00:00+01:00", "2026-11-20", 5, False),
    ("2027-01-04T08:00:00+01:00", "2027-01-08", 4, True),
    ("2027-03-29T08:00:00+02:00", "2027-04-02", 4, True),  # Rome DST
    ("2027-10-27T10:59:59+02:00", "2027-10-29", 2, True),
    ("2027-10-27T11:00:00+02:00", "2027-11-05", 9, False),
])
def test_friday_calendar(local, delivery, lead, same_week):
    result = friday_delivery_schedule(datetime.fromisoformat(local))
    assert result["delivery_date"] == delivery
    assert result["lead_days"] == lead
    assert result["review_days"] == 7
    assert result["same_week_available"] is same_week
    assert result["cutoff_datetime"].endswith("+01:00") or result["cutoff_datetime"].endswith("+02:00")
    assert date.fromisoformat(result["preferred_order_date"]).weekday() == 0
    assert date.fromisoformat(result["delivery_date"]).weekday() == 4
    assert datetime.fromisoformat(result["cutoff_datetime"]).weekday() == 2


def test_cutoff_uses_rome_timezone_even_when_supplied_utc():
    utc_now = datetime(2026, 11, 11, 9, 59, 59, tzinfo=ZoneInfo("UTC"))
    assert friday_delivery_schedule(utc_now)["delivery_date"] == "2026-11-13"
    utc_late = datetime(2026, 11, 11, 10, 0, 0, tzinfo=ZoneInfo("UTC"))
    assert friday_delivery_schedule(utc_late)["delivery_date"] == "2026-11-20"


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        friday_delivery_schedule(datetime(2026, 11, 11, 10, 59))


def test_friday_calendar_replaces_constant_lead_and_review_in_policy():
    active = {
        "planning_day": "2026-11-10",
        "snapshot_key": "snapshot-1",
        "parametri": {"AUTO_ORDER_WEEKLY_FRIDAY": 1,
                      "AUTO_ORDER_3_1_ENABLED": 1,
                      "AUTO_ORDER_LEAD_TIME_GG": 0,
                      "AUTO_ORDER_REVIEW_PERIOD_GG": 0},
        "data_quality": "DATI_AFFIDABILI",
        "righe": [], "esclusi": [],
    }
    tuesday = datetime.fromisoformat("2026-11-10T13:48:00+01:00")
    wednesday_after_cutoff = datetime.fromisoformat("2026-11-11T11:01:00+01:00")
    tuesday_result = compare_policies(active, [], now=tuesday)
    late_result = compare_policies(active, [], now=wednesday_after_cutoff)
    assert tuesday_result["delivery_schedule"]["lead_days"] == 3
    assert late_result["delivery_schedule"]["lead_days"] == 9
    assert tuesday_result["delivery_schedule"]["review_days"] == 7
    assert readiness(active, {**tuesday_result, "native_history_days": 90})["operational_ready"] is True
    assert policy_snapshot(active, tuesday_result) != policy_snapshot(active, late_result)


def test_without_explicit_calendar_flag_keeps_legacy_fixed_lead_behavior():
    active = {
        "planning_day": "2026-11-10", "snapshot_key": "snapshot-1",
        "parametri": {"AUTO_ORDER_LEAD_TIME_GG": 4, "AUTO_ORDER_REVIEW_PERIOD_GG": 7},
        "righe": [], "esclusi": [],
    }
    output = compare_policies(active, [], now=datetime.fromisoformat("2026-11-11T12:00:00+01:00"))
    assert output["delivery_schedule"] is None
