from datetime import date, datetime
from decimal import Decimal

import pytest

from ats_onebss.attendance import (
    is_working_day,
    is_working_session,
    members_on_leave,
    monthly_availability_ratios,
    validate_calendar,
    working_dates,
)


def test_calendar_overrides_weekdays_and_weekends():
    calendar = validate_calendar({
        "overrides": [
            {"date": "2026-10-05", "status": "holiday"},
            {"date": "2026-10-10", "status": "workday"},
        ],
        "leave": [],
    })

    assert not is_working_day(date(2026, 10, 5), calendar)
    assert is_working_day(date(2026, 10, 10), calendar)
    assert date(2026, 10, 5) not in working_dates(2026, 10, calendar)
    assert date(2026, 10, 10) in working_dates(2026, 10, calendar)


def test_leave_reduces_monthly_target_and_excludes_active_session():
    calendar = validate_calendar({
        "overrides": [],
        "leave": [{
            "member": "Nhân viên A",
            "start_date": "2026-10-05",
            "end_date": "2026-10-05",
            "session": "morning",
        }],
    })
    ratios = monthly_availability_ratios(calendar, ["Nhân viên A"], 2026, 10)

    assert ratios["Nhân viên A"] == Decimal(21.5) / Decimal(22)
    assert "Nhân viên A" in members_on_leave(
        calendar, datetime.fromisoformat("2026-10-05T09:00:00")
    )
    assert not members_on_leave(
        calendar, datetime.fromisoformat("2026-10-05T14:00:00")
    )


def test_working_sessions_are_morning_and_afternoon_only():
    assert is_working_session(datetime.fromisoformat("2026-10-05T08:00:00"))
    assert not is_working_session(datetime.fromisoformat("2026-10-05T12:00:00"))
    assert is_working_session(datetime.fromisoformat("2026-10-05T13:00:00"))
    assert not is_working_session(datetime.fromisoformat("2026-10-05T17:00:00"))


def test_overlapping_leave_is_rejected():
    with pytest.raises(ValueError, match="bị trùng ngày"):
        validate_calendar({
            "overrides": [],
            "leave": [
                {"member": "A", "start_date": "2026-10-05", "end_date": "2026-10-06", "session": "full"},
                {"member": "a", "start_date": "2026-10-06", "end_date": "2026-10-06", "session": "afternoon"},
            ],
        })
