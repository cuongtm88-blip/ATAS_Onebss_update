from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
    QVBoxLayout,
)

from .attendance import load_calendar, save_calendar, validate_calendar


class AttendanceCalendarDialog(QDialog):
    def __init__(
        self, path: str | Path, member_names: tuple[str, ...],
        region_name: str = "", parent=None,
    ) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self.data = deepcopy(load_calendar(self.path))
        title = "Lịch làm việc và nghỉ nhân sự"
        self.setWindowTitle(f"{title} — {region_name}" if region_name else title)
        self.resize(850, 610)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        calendar_page = QVBoxLayout()
        calendar_help = QLabel(
            "Mặc định làm việc thứ Hai–thứ Sáu. Thêm ngày nghỉ lễ hoặc ngày "
            "làm bù để ghi đè lịch mặc định; ngày làm bù có thể là thứ Bảy "
            "hoặc Chủ nhật. Lịch ngày lễ khởi đầu trống, hãy nhập các ngày "
            "nghỉ lễ cần áp dụng cho năm tương ứng."
        )
        calendar_help.setWordWrap(True)
        calendar_page.addWidget(calendar_help)
        calendar_form = QHBoxLayout()
        self.override_date = self._date_edit()
        self.override_status = QComboBox()
        self.override_status.addItem("Ngày nghỉ lễ", "holiday")
        self.override_status.addItem("Ngày làm bù", "workday")
        self.add_override_button = QPushButton("Thêm / cập nhật ngày")
        self.add_override_button.clicked.connect(self._add_override)
        calendar_form.addWidget(QLabel("Ngày:"))
        calendar_form.addWidget(self.override_date)
        calendar_form.addWidget(self.override_status)
        calendar_form.addWidget(self.add_override_button)
        calendar_form.addStretch(1)
        calendar_page.addLayout(calendar_form)
        self.override_table = QTableWidget(0, 2)
        self.override_table.setHorizontalHeaderLabels(["Ngày", "Loại ngày"])
        self._prepare_table(self.override_table)
        calendar_page.addWidget(self.override_table, 1)
        remove_override = QPushButton("Xóa ngày đang chọn")
        remove_override.clicked.connect(
            lambda: self._remove_selected(self.override_table, "overrides")
        )
        calendar_page.addWidget(remove_override, 0, Qt.AlignRight)
        override_widget = QWidget()
        override_widget.setLayout(calendar_page)
        tabs.addTab(override_widget, "Ngày lễ / ngày làm bù")

        leave_page = QVBoxLayout()
        leave_help = QLabel(
            "Nghỉ cả ngày trừ 1 ngày công; nghỉ nửa ngày trừ 0,5 ngày công. "
            "Ngày cuối tuần/ngày lễ không bị trừ thêm. Buổi nghỉ áp dụng cho "
            "mọi ngày làm việc trong khoảng đã chọn."
        )
        leave_help.setWordWrap(True)
        leave_page.addWidget(leave_help)
        self.member_combo = QComboBox()
        self.member_combo.addItems(member_names)
        self.leave_start = self._date_edit()
        self.leave_end = self._date_edit()
        self.leave_end.setDate(self.leave_start.date())
        self.leave_session = QComboBox()
        self.leave_session.addItem("Cả ngày", "full")
        self.leave_session.addItem("Nửa ngày sáng (08:00–12:00)", "morning")
        self.leave_session.addItem("Nửa ngày chiều (13:00–17:00)", "afternoon")
        leave_form = QFormLayout()
        leave_form.addRow("Nhân sự:", self.member_combo)
        date_row = QHBoxLayout()
        date_row.addWidget(QLabel("Từ ngày:"))
        date_row.addWidget(self.leave_start)
        date_row.addSpacing(12)
        date_row.addWidget(QLabel("Đến ngày:"))
        date_row.addWidget(self.leave_end)
        date_row.addStretch(1)
        leave_form.addRow("Khoảng nghỉ:", date_row)
        leave_form.addRow("Thời lượng:", self.leave_session)
        leave_page.addLayout(leave_form)
        add_leave = QPushButton("Thêm lịch nghỉ")
        add_leave.clicked.connect(self._add_leave)
        leave_page.addWidget(add_leave, 0, Qt.AlignLeft)
        self.leave_table = QTableWidget(0, 4)
        self.leave_table.setHorizontalHeaderLabels(
            ["Nhân sự", "Từ ngày", "Đến ngày", "Buổi nghỉ"]
        )
        self._prepare_table(self.leave_table)
        leave_page.addWidget(self.leave_table, 1)
        remove_leave = QPushButton("Xóa lịch nghỉ đang chọn")
        remove_leave.clicked.connect(
            lambda: self._remove_selected(self.leave_table, "leave")
        )
        leave_page.addWidget(remove_leave, 0, Qt.AlignRight)
        leave_widget = QWidget()
        leave_widget.setLayout(leave_page)
        tabs.addTab(leave_widget, "Nghỉ nhân sự")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Lưu lịch")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_tables()

    @staticmethod
    def _date_edit() -> QDateEdit:
        widget = QDateEdit(QDate.currentDate())
        widget.setCalendarPopup(True)
        widget.setDisplayFormat("dd/MM/yyyy")
        return widget

    @staticmethod
    def _prepare_table(table: QTableWidget) -> None:
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.ExtendedSelection)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)

    @staticmethod
    def _date_value(widget: QDateEdit) -> str:
        return widget.date().toString("yyyy-MM-dd")

    @staticmethod
    def _display_date(value: str) -> str:
        return date.fromisoformat(value).strftime("%d/%m/%Y")

    def _refresh_tables(self) -> None:
        labels = {"holiday": "Ngày nghỉ lễ", "workday": "Ngày làm bù"}
        self.override_table.setRowCount(0)
        for entry in self.data["overrides"]:
            row = self.override_table.rowCount()
            self.override_table.insertRow(row)
            self.override_table.setItem(row, 0, QTableWidgetItem(self._display_date(entry["date"])))
            self.override_table.setItem(row, 1, QTableWidgetItem(labels[entry["status"]]))
        sessions = {
            "full": "Cả ngày",
            "morning": "Nửa ngày sáng",
            "afternoon": "Nửa ngày chiều",
        }
        self.leave_table.setRowCount(0)
        for entry in self.data["leave"]:
            row = self.leave_table.rowCount()
            self.leave_table.insertRow(row)
            values = (
                entry["member"],
                self._display_date(entry["start_date"]),
                self._display_date(entry["end_date"]),
                sessions[entry["session"]],
            )
            for column, value in enumerate(values):
                self.leave_table.setItem(row, column, QTableWidgetItem(value))

    def _add_override(self) -> None:
        day = self._date_value(self.override_date)
        status = str(self.override_status.currentData())
        existing = next(
            (item for item in self.data["overrides"] if item["date"] == day), None
        )
        if existing:
            existing["status"] = status
        else:
            self.data["overrides"].append({"date": day, "status": status})
        self.data = validate_calendar(self.data)
        self._refresh_tables()

    def _add_leave(self) -> None:
        start = self._date_value(self.leave_start)
        end = self._date_value(self.leave_end)
        if end < start:
            QMessageBox.warning(self, "Khoảng ngày không hợp lệ", "Ngày kết thúc phải từ ngày bắt đầu trở đi.")
            return
        self.data["leave"].append({
            "member": self.member_combo.currentText(),
            "start_date": start,
            "end_date": end,
            "session": str(self.leave_session.currentData()),
        })
        try:
            self.data = validate_calendar(self.data)
        except ValueError as error:
            self.data["leave"].pop()
            QMessageBox.warning(self, "Lịch nghỉ bị trùng", str(error))
            return
        self._refresh_tables()

    def _remove_selected(self, table: QTableWidget, key: str) -> None:
        rows = sorted({item.row() for item in table.selectedItems()}, reverse=True)
        for row in rows:
            del self.data[key][row]
        self._refresh_tables()

    def _save(self) -> None:
        try:
            self.data = save_calendar(self.path, self.data)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Không lưu được lịch", str(error))
            return
        self.accept()
