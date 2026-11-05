import asyncio
import json
import os
from decimal import Decimal
from types import SimpleNamespace

from ats_onebss.attendance import (
    default_leave_deduction_percent,
    monthly_availability_ratios,
    validate_calendar,
    working_dates,
)
from ats_onebss.calendar_sync import HEADER, _records, sync_calendar


def test_leave_deduction_uses_month_workdays_and_custom_value_per_entry():
    calendar = validate_calendar({
        "overrides": [
            {"date": "2026-02-02", "status": "holiday"},
            {"date": "2026-02-07", "status": "workday"},
        ],
        "leave": [
            {
                "member": "Default",
                "start_date": "2026-02-03",
                "end_date": "2026-02-03",
                "session": "full",
            },
            {
                "member": "Custom full",
                "start_date": "2026-02-03",
                "end_date": "2026-02-03",
                "session": "full",
                "deduction_percent": "3",
            },
            {
                "member": "Custom half",
                "start_date": "2026-02-03",
                "end_date": "2026-02-03",
                "session": "afternoon",
                "deduction_percent": "3",
            },
        ],
    })

    assert len(working_dates(2026, 2, calendar)) == 20
    assert default_leave_deduction_percent(2026, 2, calendar) == Decimal(5)
    ratios = monthly_availability_ratios(
        calendar, ("Default", "Custom full", "Custom half", "No leave"),
        2026, 2,
    )
    assert ratios["Default"] == Decimal("0.95")
    assert ratios["Custom full"] == Decimal("0.97")
    assert ratios["Custom half"] == Decimal("0.985")
    assert ratios["No leave"] == Decimal(1)


def test_legacy_leave_without_percent_keeps_default_behavior():
    calendar = validate_calendar({
        "overrides": [],
        "leave": [{
            "member": "Legacy",
            "start_date": "2026-02-03",
            "end_date": "2026-02-03",
            "session": "morning",
        }],
    })

    assert "deduction_percent" not in calendar["leave"][0]
    assert monthly_availability_ratios(calendar, ("Legacy",), 2026, 2)[
        "Legacy"
    ] == Decimal("0.975")


def test_calendar_sync_persists_custom_percent_and_reads_legacy_rows(tmp_path):
    from ats_onebss.attendance import save_calendar

    calendar = {
        "version": 1,
        "overrides": [{"date": "2026-02-02", "status": "holiday"}],
        "leave": [{
            "member": "Nhân sự",
            "start_date": "2026-02-03",
            "end_date": "2026-02-03",
            "session": "afternoon",
            "deduction_percent": "3",
        }],
    }
    local_path = tmp_path / "attendance.json"
    baseline_path = tmp_path / "attendance.synced.json"
    save_calendar(local_path, calendar)
    save_calendar(baseline_path, {"version": 1, "overrides": [], "leave": []})
    written = []

    async def append_rows(rows):
        written.extend(rows)

    synced = asyncio.run(
        sync_calendar(local_path, baseline_path, [], append_rows)
    )

    assert {row[2] for row in written} == {"overrides", "leave"}
    assert all(len(row) == 12 for row in written)
    leave_event = next(row for row in written if row[2] == "leave")
    assert leave_event[11] == "3"
    assert synced["leave"][0]["deduction_percent"] == "3"

    repeated_writes = []

    async def capture_repeat(rows):
        repeated_writes.extend(rows)

    round_trip = asyncio.run(
        sync_calendar(
            local_path, baseline_path, written, capture_repeat,
        )
    )
    assert not repeated_writes
    assert round_trip["leave"][0]["deduction_percent"] == "3"

    # An existing 11-column Sheet row has no deduction value. It remains valid
    # and preserves the old default behavior.
    legacy = dict(_records({
        "version": 1,
        "overrides": [],
        "leave": [{
            "member": "Old",
            "start_date": "2026-02-03",
            "end_date": "2026-02-03",
            "session": "full",
        }],
    }))
    (record_key, (record_type, payload)), = legacy.items()
    legacy_row = [
        "legacy-id", "put", record_type, record_key, "", "",
        payload["member"], payload["start_date"], payload["end_date"],
        payload["session"], "2026-01-01T00:00:00+07:00",
    ]
    save_calendar(local_path, {"version": 1, "overrides": [], "leave": [payload]})
    save_calendar(baseline_path, {"version": 1, "overrides": [], "leave": [payload]})
    called = []

    async def should_not_append(rows):
        called.extend(rows)

    merged = asyncio.run(
        sync_calendar(local_path, baseline_path, [legacy_row], should_not_append)
    )
    assert not called
    assert merged["leave"] == [payload]


def test_calendar_append_migrates_legacy_sheet_header(monkeypatch):
    import csv
    import io

    import ats_onebss.browser as browser_module
    from ats_onebss.browser import GoogleSheetClient

    sheet_rows = [HEADER[:-1].copy()]
    page_holder = {}

    class NameBox:
        value = ""

        async def fill(self, value):
            self.value = value

        async def press(self, _key):
            return None

        async def input_value(self):
            return self.value

    class MenuItem:
        async def hover(self):
            return None

        async def click(self):
            page_holder["page"].columns_inserted += 1

    class MenuItems:
        def nth(self, _index):
            return MenuItem()

        @property
        def last(self):
            return MenuItem()

    class Menus:
        def __init__(self):
            self.items = MenuItems()

        @property
        def last(self):
            return self

        def locator(self, _selector):
            return self.items

    class InsertMenu:
        async def click(self):
            return None

    class Page:
        def __init__(self):
            self.name_box = NameBox()
            self.clipboard = ""
            self.columns_inserted = 0
            self.keyboard = SimpleNamespace(press=self.press)
            page_holder["page"] = self

        def locator(self, selector):
            if selector == "#t-name-box":
                return self.name_box
            if selector == "#docs-insert-menu":
                return InsertMenu()
            return Menus() if selector == '[role="menu"]:visible' else MenuItems()

        async def evaluate(self, _script, text):
            self.clipboard = text

        async def wait_for_timeout(self, _milliseconds):
            return None

        async def press(self, _key):
            pasted = list(csv.reader(io.StringIO(self.clipboard), delimiter="\t"))
            if self.name_box.value == "L1":
                sheet_rows[0].append(pasted[0][0])
            else:
                sheet_rows.extend(pasted)

    async def visible(locator):
        return locator

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(browser_module, "_visible", visible)
    monkeypatch.setattr(browser_module.asyncio, "sleep", no_wait)
    client = object.__new__(GoogleSheetClient)
    client.page = Page()
    client.session = SimpleNamespace(
        config=SimpleNamespace(timeout_ms=1000),
    )

    async def activate_sheet(_name):
        return None

    async def export_rows(_name):
        return [row[:] for row in sheet_rows]

    client.activate_sheet = activate_sheet
    client.export_rows = export_rows
    event = [
        "event-id", "put", "leave", "record-key", "", "", "Nhân sự",
        "2026-02-03", "2026-02-03", "full", "2026-01-01T00:00:00+07:00", "3",
    ]

    asyncio.run(client.append_calendar_events([event], "Lịch ATS"))

    assert sheet_rows[0] == HEADER
    assert sheet_rows[1] == event
    assert client.page.columns_inserted == 1


def test_calendar_sync_accepts_legacy_header(tmp_path):
    from ats_onebss.cli import sync_attendance_calendar

    class Sheet:
        async def export_rows(self, _name):
            return [HEADER[:-1].copy()]

        async def append_calendar_events(self, _rows, _name):
            raise AssertionError("No events should be written for an empty calendar")

    calendar_path = tmp_path / "attendance.json"
    calendar_path.write_text(
        '{"version":1,"overrides":[],"leave":[]}', encoding="utf-8"
    )
    config = SimpleNamespace(
        attendance_calendar_file=calendar_path,
        attendance_sheet_name="Lịch ATS",
    )

    asyncio.run(sync_attendance_calendar(config, Sheet()))

    assert calendar_path.with_name("attendance.synced.json").is_file()


def test_leave_table_filters_month_and_deletes_the_selected_record(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ats_onebss.attendance_ui import AttendanceCalendarDialog

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "attendance.json"
    path.write_text(json.dumps({
        "version": 1,
        "overrides": [],
        "leave": [
            {
                "member": "January",
                "start_date": "2026-01-30",
                "end_date": "2026-02-02",
                "session": "full",
            },
            {
                "member": "February",
                "start_date": "2026-02-05",
                "end_date": "2026-02-05",
                "session": "full",
                "deduction_percent": "3",
            },
            {
                "member": "March",
                "start_date": "2026-03-01",
                "end_date": "2026-03-01",
                "session": "morning",
            },
        ],
    }), encoding="utf-8")
    dialog = AttendanceCalendarDialog(path, ("January", "February", "March"))
    dialog.leave_month.setCurrentIndex(1)
    dialog.leave_year.setValue(2026)

    assert dialog.leave_table.rowCount() == 2
    assert dialog.leave_table.item(0, 0).text() == "January"
    assert dialog.leave_table.item(1, 0).text() == "February"
    assert dialog.leave_table.item(1, 4).text() == "3.0000%"
    dialog.leave_table.selectRow(0)
    dialog._remove_selected(dialog.leave_table, "leave")

    assert [entry["member"] for entry in dialog.data["leave"]] == ["February", "March"]
    assert dialog.leave_table.rowCount() == 1
    assert dialog.leave_table.item(0, 0).text() == "February"
    dialog.close()
    app.processEvents()
