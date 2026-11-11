from ats_onebss.attendance_ui import AttendanceCalendarDialog


class _SelectedCell:
    def __init__(self, row: int) -> None:
        self._row = row

    def row(self) -> int:
        return self._row


class _SourceIndexCell:
    def __init__(self, source_index: int) -> None:
        self.source_index = source_index

    def data(self, _role: object) -> int:
        return self.source_index


class _FilteredTable:
    def __init__(self, source_indices: list[int], selected_rows: list[int]) -> None:
        self.cells = [_SourceIndexCell(index) for index in source_indices]
        self.selected_rows = selected_rows

    def selectedItems(self) -> list[_SelectedCell]:
        return [_SelectedCell(row) for row in self.selected_rows]

    def item(self, row: int, _column: int) -> _SourceIndexCell:
        return self.cells[row]


def test_remove_leave_uses_original_index_after_month_filter():
    dialog = type("DialogState", (), {})()
    dialog.data = {
        "leave": [
            {"member": "Đặng Văn Minh", "start_date": "2026-10-30"},
            {"member": "Nguyễn Duy Thành", "start_date": "2026-11-11"},
            {"member": "Tạ Lê Hoa", "start_date": "2026-11-13"},
        ]
    }
    dialog._refresh_tables = lambda: None
    november_table = _FilteredTable(source_indices=[1, 2], selected_rows=[0])

    AttendanceCalendarDialog._remove_selected(dialog, november_table, "leave")

    assert [item["member"] for item in dialog.data["leave"]] == [
        "Đặng Văn Minh", "Tạ Lê Hoa",
    ]
