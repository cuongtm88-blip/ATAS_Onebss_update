import asyncio

from ats_onebss.attendance import load_calendar, save_calendar
from ats_onebss.calendar_sync import HEADER, sync_calendar


def test_calendar_sync_keeps_leave_after_app_update_and_does_not_duplicate_events(tmp_path):
    local = tmp_path / "attendance_calendar.json"
    baseline = tmp_path / "attendance_calendar.synced.json"
    leave = {
        "member": "Ngô Thị Minh Phương",
        "start_date": "2026-10-29",
        "end_date": "2026-10-29",
        "session": "full",
    }
    save_calendar(local, {"overrides": [], "leave": [leave]})
    cloud_rows = []

    async def append(rows):
        cloud_rows.extend(rows)

    asyncio.run(sync_calendar(local, baseline, cloud_rows, append))
    assert load_calendar(local)["leave"] == [leave]
    assert load_calendar(baseline)["leave"] == [leave]
    assert len(cloud_rows) == 1

    asyncio.run(sync_calendar(local, baseline, cloud_rows, append))
    assert len(cloud_rows) == 1


def test_calendar_sync_reads_remote_leave_before_assignment(tmp_path):
    local = tmp_path / "attendance_calendar.json"
    baseline = tmp_path / "attendance_calendar.synced.json"
    row = dict.fromkeys(HEADER, "")
    row.update({
        "event_id": "remote-event",
        "operation": "put",
        "record_type": "leave",
        "record_key": "remote-key",
        "member": "Nguyễn Duy Thành",
        "start_date": "2026-10-29",
        "end_date": "2026-10-29",
        "session": "full",
    })

    async def append(_rows):
        raise AssertionError("Remote-only event must not be written again")

    asyncio.run(sync_calendar(local, baseline, [[row[key] for key in HEADER]], append))
    assert load_calendar(local)["leave"][0]["member"] == "Nguyễn Duy Thành"


def test_calendar_sync_preserves_daily_deduction_percent(tmp_path):
    local = tmp_path / "attendance_calendar.json"
    baseline = tmp_path / "attendance_calendar.synced.json"
    leave = {
        "member": "Ngô Thị Minh Phương",
        "start_date": "2026-10-29",
        "end_date": "2026-10-30",
        "session": "full",
        "deduction_percent": "3.75",
    }
    save_calendar(local, {"overrides": [], "leave": [leave]})
    cloud_rows = []

    async def append(rows):
        cloud_rows.extend(rows)

    asyncio.run(sync_calendar(local, baseline, cloud_rows, append))
    assert len(cloud_rows[0]) == len(HEADER) == 12
    assert cloud_rows[0][HEADER.index("deduction_percent")] == "3.75"
    assert load_calendar(local)["leave"] == [leave]

    asyncio.run(sync_calendar(local, baseline, cloud_rows, append))
    assert len(cloud_rows) == 1


def test_calendar_sync_reads_legacy_eleven_column_leave_rows(tmp_path):
    local = tmp_path / "attendance_calendar.json"
    baseline = tmp_path / "attendance_calendar.synced.json"
    legacy_header = HEADER[:-1]
    row = dict.fromkeys(legacy_header, "")
    row.update({
        "event_id": "legacy-event",
        "operation": "put",
        "record_type": "leave",
        "record_key": "legacy-key",
        "member": "Nguyễn Duy Thành",
        "start_date": "2026-10-29",
        "end_date": "2026-10-29",
        "session": "full",
    })

    async def append(_rows):
        raise AssertionError("Existing legacy event must not be written again")

    asyncio.run(sync_calendar(
        local, baseline,
        [[row[column] for column in legacy_header]], append,
    ))
    assert load_calendar(local)["leave"] == [{
        "member": "Nguyễn Duy Thành",
        "start_date": "2026-10-29",
        "end_date": "2026-10-29",
        "session": "full",
    }]
