from __future__ import annotations

import codecs
import csv
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QObject,
    QProcess,
    QProcessEnvironment,
    Qt,
    QTimer,
    QUrl,
    QLockFile,
    Signal,
)
from PySide6.QtGui import (
    QCloseEvent,
    QDesktopServices,
    QFont,
    QIcon,
    QPixmap,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .config import load_config
from .regions import Region, RegionCatalog, RegionError, load_regions
from .rules import load_project_rules, load_rules
from .telegram import (
    TELEGRAM_CHAT_ID_ENV,
    TELEGRAM_REGION_ENV,
    TELEGRAM_TOKEN_ENV,
    TelegramError,
    load_bot_token,
    save_bot_token,
    send_message,
)
from .updater import (
    UpdateError,
    UpdateRelease,
    detect_install_target,
    download_asset,
    latest_update,
    launch_installer,
    stage_update,
)


APP_NAME = "ATS OneBSS"
RESOURCE_FILES = (
    "regions.toml",
    "config.toml",
    "project_rules.toml",
    "Giao phiếu.xlsx",
)


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return Path(__file__).resolve().parents[2]


def user_data_root() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ATS-OneBSS"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        return base / "ATS-OneBSS"
    return Path.home() / ".config" / "ATS-OneBSS"


def editable_rules_file() -> Path | None:
    """Return a newer Excel rule file kept beside a development .app bundle."""
    if not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    app_bundle = next((parent for parent in executable.parents if parent.suffix == ".app"), None)
    if app_bundle is None:
        return None
    candidate = app_bundle.parent.parent / "Giao phiếu.xlsx"
    return candidate if candidate.is_file() else None


def prepare_runtime_root() -> Path:
    source = resource_root()
    if not getattr(sys, "frozen", False):
        return source

    target = user_data_root()
    target.mkdir(parents=True, exist_ok=True)
    for relative in RESOURCE_FILES:
        source_file = source / relative
        if not source_file.exists():
            wanted_name = unicodedata.normalize("NFC", source_file.name)
            try:
                source_file = next(
                    item for item in source.iterdir()
                    if unicodedata.normalize("NFC", item.name) == wanted_name
                )
            except (FileNotFoundError, StopIteration):
                pass
        target_file = target / relative
        if source_file.exists() and not target_file.exists():
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file)
    # In the development layout the editable file is next to ``dist`` while
    # the running app reads its private Application Support copy.  Refresh
    # only when that editable source is newer, so a manual runtime adjustment
    # is never overwritten by an older bundled default.
    editable_rules = editable_rules_file()
    runtime_rules = target / "Giao phiếu.xlsx"
    if editable_rules and (
        not runtime_rules.exists()
        or editable_rules.stat().st_mtime_ns > runtime_rules.stat().st_mtime_ns
    ):
        shutil.copy2(editable_rules, runtime_rules)
    return target


def install_rules_file(source: str | Path, target: str | Path) -> Path | None:
    """Validate and atomically replace the active Excel rule file.

    The worker is stopped by the UI before this function is available, so the
    next run sees either the complete previous workbook or the complete new
    workbook.  A timestamped backup keeps the prior active rules recoverable.
    """
    source_path = Path(source).expanduser().resolve()
    target_path = Path(target).expanduser().resolve()
    if source_path.suffix.casefold() != ".xlsx":
        raise ValueError("Chỉ hỗ trợ file Excel có đuôi .xlsx.")
    if not source_path.is_file():
        raise ValueError(f"Không tìm thấy file Giao phiếu: {source_path}")

    rules = load_rules(source_path)
    if not rules:
        raise ValueError("File Giao phiếu không có quy tắc dịch vụ nào.")
    if source_path == target_path:
        raise ValueError(
            "Bạn đang chọn chính file quy tắc đang chạy. "
            "Hãy chọn bản Excel đã chỉnh sửa ở vị trí khác để nhập."
        )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path = target_path.with_name(
        f".{target_path.stem}.importing{target_path.suffix}"
    )
    try:
        shutil.copy2(source_path, staging_path)
        # Validate the copied bytes too, before the active file is replaced.
        if not load_rules(staging_path):
            raise ValueError("File Giao phiếu không có quy tắc dịch vụ nào.")
        backup_path = None
        if target_path.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = target_path.with_name(
                f"{target_path.name}.backup-{stamp}"
            )
            shutil.copy2(target_path, backup_path)
        staging_path.replace(target_path)
        return backup_path
    finally:
        if staging_path.exists():
            staging_path.unlink()


def worker_command(
    config_path: Path,
    command: str,
    excluded: tuple[str, ...],
    poll_interval_minutes: int | None = None,
) -> list[str]:
    if getattr(sys, "frozen", False):
        result = [sys.executable, "--worker"]
    else:
        result = [sys.executable, "-m", "ats_onebss.gui_main", "--worker"]
    result.extend(["--config", str(config_path)])
    if poll_interval_minutes is not None:
        result.extend(["--poll-interval-minutes", str(poll_interval_minutes)])
    result.append(command)
    if command in {"run", "watch", "once", "sync"}:
        result.append("--yes")
    if command in {"plan", "run", "watch", "once"}:
        for name in excluded:
            result.extend(["--exclude", name])
    return result


def region_member_names(region: Region) -> tuple[str, ...]:
    if not region.enabled:
        return ()
    config = load_config(region.config_path)
    rules = load_rules(config.rules_file)
    result: list[str] = []
    seen: set[str] = set()

    def add(name: str | None) -> None:
        if not name:
            return
        cleaned = name.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)

    for rule in rules:
        for member in rule.members:
            add(member.name)
    for project in load_project_rules(config.project_rules_file):
        add(project.fixed_assignee)
        for route in project.routes:
            add(route.assignee)
    return tuple(result)


def read_skipped_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    """Read skipped rows as text, preserving IDs and Vietnamese characters."""
    if not path.is_file():
        return [], []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        rows = list(reader)
    if not rows:
        return [], []
    width = len(rows[0])
    return rows[0], [row + [""] * (width - len(row)) for row in rows[1:]]


class TelegramSignals(QObject):
    completed = Signal(bool, str)


class UpdateSignals(QObject):
    checked = Signal(object, str)
    staged = Signal(object, str)


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")


class ATSOneBSSWindow(QMainWindow):
    def __init__(self, runtime_root: Path):
        super().__init__()
        self.runtime_root = runtime_root
        self.catalog_path = runtime_root / "regions.toml"
        self.catalog: RegionCatalog = load_regions(self.catalog_path)
        self.region_by_index: list[Region] = []
        self.member_names: tuple[str, ...] = ()
        self.process = QProcess(self)
        self.process_mode = ""
        self._output_decoder = codecs.getincrementaldecoder("utf-8")(
            errors="replace"
        )
        self._session_output_buffer = ""
        self._session_expiry_epoch = None
        self._session_expiry_unknown = False
        self._session_expired = False
        self.caffeinate_process: subprocess.Popen[bytes] | None = None
        self.settings_path = user_data_root() / "gui-settings.json"
        self.settings = self._load_settings()
        self.log_path = user_data_root() / "logs" / "ats-onebss.log"
        self.skipped_csv_path: Path | None = None
        self.skipped_csv_stamp: tuple[int, int] | None = None
        self.telegram_signals = TelegramSignals(self)
        self.update_signals = UpdateSignals(self)
        self._update_busy = False
        self.closing_after_stop = False

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        logo_path = resource_root() / "assets" / "vinaphone-logo.png"
        if logo_path.exists():
            self.setWindowIcon(QIcon(str(logo_path)))
        self.resize(1100, 740)
        self.setMinimumSize(900, 620)
        self._apply_styles()
        self._build_ui()
        self._connect_process()
        self.telegram_signals.completed.connect(self._telegram_test_completed)
        self.update_signals.checked.connect(self._version_check_completed)
        self.update_signals.staged.connect(self._update_download_completed)
        self.skipped_timer = QTimer(self)
        self.skipped_timer.setInterval(2000)
        self.skipped_timer.timeout.connect(self._refresh_skipped_csv)
        self.skipped_timer.start()
        self.session_timer = QTimer(self)
        self.session_timer.setInterval(1000)
        self.session_timer.timeout.connect(self._update_session_countdown)
        self.session_timer.start()
        self._select_initial_region()

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow { background: #f2f5f8; }
            QLabel#title { color: #12395b; font-size: 25px; font-weight: 700; }
            QLabel#subtitle { color: #607080; font-size: 13px; }
            QLabel#status { color: #1261a0; font-size: 14px; font-weight: 700; }
            QFrame#card {
                background: white;
                border: 1px solid #d9e2ea;
                border-radius: 10px;
            }
            QPushButton {
                min-height: 32px;
                padding: 2px 13px;
                border-radius: 6px;
            }
            QPushButton#primary {
                color: white;
                background: #0866c6;
                font-weight: 700;
            }
            QPushButton#primary:disabled { background: #9ab7d3; }
            QPlainTextEdit, QListWidget, QTableWidget, QLineEdit {
                background: #f8fafc;
                border: 1px solid #dbe3ea;
                border-radius: 6px;
            }
            QTabWidget::pane {
                border: 1px solid #dbe3ea;
                border-radius: 6px;
                background: white;
            }
            QTabBar::tab {
                min-height: 28px;
                padding: 3px 13px;
            }
            QComboBox { min-height: 30px; padding-left: 8px; }
            """
        )

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        page = QVBoxLayout(central)
        page.setContentsMargins(24, 20, 24, 20)
        page.setSpacing(14)

        header = QHBoxLayout()
        logo = QLabel()
        logo_path = resource_root() / "assets" / "vinaphone-logo.png"
        if logo_path.exists():
            logo_pixmap = QPixmap(str(logo_path))
            logo.setPixmap(
                logo_pixmap.scaled(
                    225, 55, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
            logo.setAccessibleName("VinaPhone")
            header.addWidget(logo)
        title = QLabel("ATS OneBSS")
        title.setObjectName("title")
        header.addWidget(title, 0, Qt.AlignVCenter)
        header.addStretch(1)
        version = QLabel(f"Phiên bản {__version__}")
        version.setObjectName("subtitle")
        header.addWidget(version, 0, Qt.AlignBottom)
        page.addLayout(header)
        subtitle = QLabel(
            "Tự động giao phiếu theo quy tắc và cân bằng điểm riêng cho từng miền"
        )
        subtitle.setObjectName("subtitle")
        page.addWidget(subtitle)

        control_card = Card()
        control_layout = QVBoxLayout(control_card)
        control_layout.setContentsMargins(16, 14, 16, 14)
        control_layout.setSpacing(11)

        region_row = QHBoxLayout()
        region_row.addWidget(QLabel("Miền hoạt động:"))
        self.region_combo = QComboBox()
        self.region_combo.setMinimumWidth(270)
        for region in self.catalog.regions:
            suffix = "" if region.enabled else " — chưa cấu hình"
            self.region_combo.addItem(f"{region.name}{suffix}")
            self.region_by_index.append(region)
        region_row.addWidget(self.region_combo)
        region_row.addSpacing(14)
        region_row.addWidget(QLabel("Chu kỳ:"))
        self.cycle_minutes = QSpinBox()
        self.cycle_minutes.setRange(1, 1440)
        self.cycle_minutes.setSuffix(" phút")
        self.cycle_minutes.setValue(15)
        self.cycle_minutes.setToolTip(
            "Khoảng thời gian chờ giữa hai chu kỳ tự động."
        )
        region_row.addWidget(self.cycle_minutes)
        self.update_rules_button = QPushButton("Cập nhật Giao phiếu")
        self.update_rules_button.setToolTip(
            "Chọn file Giao phiếu.xlsx mới cho miền đang hoạt động. "
            "File sẽ được kiểm tra trước khi thay thế."
        )
        region_row.addWidget(self.update_rules_button)
        self.update_version_button = QPushButton("Kiểm tra phiên bản")
        self.update_version_button.setToolTip(
            "Kiểm tra GitHub Release của miền đang chọn và cài bản mới nếu có."
        )
        region_row.addWidget(self.update_version_button)
        region_row.addStretch(1)
        self.status_label = QLabel("Đã dừng")
        self.status_label.setObjectName("status")
        region_row.addWidget(self.status_label)
        control_layout.addLayout(region_row)

        self.region_description = QLabel()
        self.region_description.setObjectName("subtitle")
        description_row = QHBoxLayout()
        description_row.addWidget(self.region_description, 1)
        self.session_countdown_label = QLabel("Phiên OneBSS: chưa xác định")
        self.session_countdown_label.setObjectName("subtitle")
        self.session_countdown_label.setMinimumWidth(230)
        self.session_countdown_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        description_row.addWidget(self.session_countdown_label)
        control_layout.addLayout(description_row)

        buttons = QHBoxLayout()
        self.login_button = QPushButton("Đăng nhập")
        self.finish_login_button = QPushButton("Hoàn tất đăng nhập")
        self.plan_button = QPushButton("Chạy thử")
        self.start_button = QPushButton("Bắt đầu tự động")
        self.start_button.setObjectName("primary")
        self.stop_button = QPushButton("Dừng")
        buttons.addWidget(self.login_button)
        buttons.addWidget(self.finish_login_button)
        buttons.addWidget(self.plan_button)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)
        self.keep_awake = QCheckBox("Giữ máy không Sleep khi đang chạy")
        self.keep_awake.setChecked(True)
        buttons.addWidget(self.keep_awake)
        control_layout.addLayout(buttons)
        page.addWidget(control_card)

        splitter = QSplitter(Qt.Horizontal)
        staff_card = Card()
        staff_layout = QVBoxLayout(staff_card)
        staff_layout.setContentsMargins(14, 13, 14, 14)
        staff_title = QLabel("Nhân sự tạm nghỉ")
        staff_title.setStyleSheet("font-weight: 700; font-size: 14px;")
        staff_layout.addWidget(staff_title)
        staff_help = QLabel(
            "Chọn nhân sự không nhận phiếu, kể cả phiếu dự án. "
            "Danh sách được lưu riêng theo miền."
        )
        staff_help.setWordWrap(True)
        staff_help.setObjectName("subtitle")
        staff_layout.addWidget(staff_help)
        self.member_list = QListWidget()
        self.member_list.setSelectionMode(QListWidget.MultiSelection)
        staff_layout.addWidget(self.member_list, 1)
        splitter.addWidget(staff_card)

        detail_card = Card()
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(14, 10, 14, 14)
        self.detail_tabs = QTabWidget()

        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        log_layout.setContentsMargins(8, 8, 8, 8)
        log_tools = QHBoxLayout()
        self.copy_log_button = QPushButton("Sao chép nhật ký")
        self.open_log_button = QPushButton("Mở file nhật ký")
        log_tools.addWidget(self.copy_log_button)
        log_tools.addWidget(self.open_log_button)
        log_tools.addStretch(1)
        log_layout.addLayout(log_tools)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(10)
        self.log_text.setFont(mono)
        self.log_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        log_layout.addWidget(self.log_text, 1)
        self.detail_tabs.addTab(log_page, "Nhật ký hoạt động")

        skipped_page = QWidget()
        skipped_layout = QVBoxLayout(skipped_page)
        skipped_layout.setContentsMargins(8, 8, 8, 8)
        skipped_tools = QHBoxLayout()
        self.skipped_status = QLabel("Chưa có dữ liệu phiếu chưa giao")
        self.skipped_status.setObjectName("subtitle")
        skipped_tools.addWidget(self.skipped_status)
        skipped_tools.addStretch(1)
        self.refresh_skipped_button = QPushButton("Làm mới")
        self.open_skipped_button = QPushButton("Mở file CSV")
        skipped_tools.addWidget(self.refresh_skipped_button)
        skipped_tools.addWidget(self.open_skipped_button)
        skipped_layout.addLayout(skipped_tools)
        self.skipped_table = QTableWidget()
        self.skipped_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.skipped_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.skipped_table.setAlternatingRowColors(True)
        self.skipped_table.setWordWrap(False)
        self.skipped_table.verticalHeader().setVisible(False)
        skipped_splitter = QSplitter(Qt.Vertical)
        skipped_splitter.addWidget(self.skipped_table)
        self.skipped_detail_text = QPlainTextEdit()
        self.skipped_detail_text.setReadOnly(True)
        self.skipped_detail_text.setPlaceholderText(
            "Chọn một phiếu để xem đầy đủ nội dung và lý do chưa giao."
        )
        skipped_splitter.addWidget(self.skipped_detail_text)
        skipped_splitter.setStretchFactor(0, 3)
        skipped_splitter.setStretchFactor(1, 2)
        skipped_layout.addWidget(skipped_splitter, 1)
        self.detail_tabs.addTab(skipped_page, "Phiếu chưa giao")

        telegram_page = QWidget()
        telegram_layout = QVBoxLayout(telegram_page)
        telegram_layout.setContentsMargins(16, 14, 16, 14)
        telegram_help = QLabel(
            "Gửi cảnh báo khi phiên OneBSS sắp hết hạn hoặc đã hết hạn, "
            "chu kỳ gặp lỗi, hay tiến trình dừng bất thường. "
            "Bot Token được lưu trong Keychain macOS, không ghi vào file cấu hình."
        )
        telegram_help.setWordWrap(True)
        telegram_help.setObjectName("subtitle")
        telegram_layout.addWidget(telegram_help)
        telegram_form = QFormLayout()
        self.telegram_enabled = QCheckBox("Bật thông báo lỗi cho miền này")
        telegram_form.addRow("Trạng thái:", self.telegram_enabled)
        self.telegram_token = QLineEdit()
        self.telegram_token.setEchoMode(QLineEdit.Password)
        self.telegram_token.setPlaceholderText("123456789:AA...")
        telegram_form.addRow("Bot Token:", self.telegram_token)
        self.telegram_chat_id = QLineEdit()
        self.telegram_chat_id.setPlaceholderText("Ví dụ: 123456789 hoặc -100...")
        telegram_form.addRow("Chat ID:", self.telegram_chat_id)
        telegram_layout.addLayout(telegram_form)
        telegram_buttons = QHBoxLayout()
        self.save_telegram_button = QPushButton("Lưu cấu hình")
        self.test_telegram_button = QPushButton("Gửi tin thử")
        self.telegram_result = QLabel("")
        self.telegram_result.setWordWrap(True)
        telegram_buttons.addWidget(self.save_telegram_button)
        telegram_buttons.addWidget(self.test_telegram_button)
        telegram_buttons.addWidget(self.telegram_result, 1)
        telegram_layout.addLayout(telegram_buttons)
        telegram_layout.addStretch(1)
        self.detail_tabs.addTab(telegram_page, "Telegram")

        detail_layout.addWidget(self.detail_tabs, 1)
        splitter.addWidget(detail_card)
        splitter.setSizes([310, 740])
        splitter.setStretchFactor(1, 1)
        page.addWidget(splitter, 1)

        self.region_combo.currentIndexChanged.connect(self._on_region_changed)
        self.login_button.clicked.connect(lambda: self._start("login"))
        self.finish_login_button.clicked.connect(self._finish_login)
        self.plan_button.clicked.connect(lambda: self._start("plan"))
        self.start_button.clicked.connect(lambda: self._start("run"))
        self.stop_button.clicked.connect(self._stop)
        self.member_list.itemSelectionChanged.connect(self._save_settings)
        self.cycle_minutes.valueChanged.connect(lambda _value: self._save_settings())
        self.update_rules_button.clicked.connect(self._update_rules_file)
        self.update_version_button.clicked.connect(self._check_version_update)
        self.copy_log_button.clicked.connect(self._copy_log)
        self.open_log_button.clicked.connect(self._open_log_file)
        self.refresh_skipped_button.clicked.connect(
            lambda: self._refresh_skipped_csv(force=True)
        )
        self.open_skipped_button.clicked.connect(self._open_skipped_csv)
        self.skipped_table.itemSelectionChanged.connect(self._show_skipped_detail)
        self.save_telegram_button.clicked.connect(self._save_telegram_settings)
        self.test_telegram_button.clicked.connect(self._test_telegram)
        self._set_process_controls(False)

    def _connect_process(self) -> None:
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_process_output)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)

    def _load_settings(self) -> dict:
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"regions": {}}

    def _save_settings(self) -> None:
        region = self._selected_region()
        if region:
            self.settings.setdefault("regions", {}).setdefault(region.key, {})[
                "excluded_members"
            ] = list(self._selected_exclusions())
            self.settings["regions"][region.key]["poll_interval_minutes"] = (
                self.cycle_minutes.value()
            )
            self.settings["last_region"] = region.key
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.settings_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp_path.replace(self.settings_path)

    def _select_initial_region(self) -> None:
        wanted = str(self.settings.get("last_region", self.catalog.default_region))
        index = next(
            (
                item_index
                for item_index, region in enumerate(self.region_by_index)
                if region.key == wanted
            ),
            next(
                item_index
                for item_index, region in enumerate(self.region_by_index)
                if region.key == self.catalog.default_region
            ),
        )
        self.region_combo.setCurrentIndex(index)
        self._load_region(self.region_by_index[index])

    def _selected_region(self) -> Region | None:
        index = self.region_combo.currentIndex()
        if 0 <= index < len(self.region_by_index):
            return self.region_by_index[index]
        return None

    def _on_region_changed(self, index: int) -> None:
        if not (0 <= index < len(self.region_by_index)):
            return
        if self.process.state() != QProcess.NotRunning:
            QMessageBox.warning(
                self,
                "Đang chạy",
                "Hãy dừng miền hiện tại trước khi chuyển sang miền khác.",
            )
            current_key = str(
                self.settings.get("last_region", self.catalog.default_region)
            )
            previous = next(
                i
                for i, region in enumerate(self.region_by_index)
                if region.key == current_key
            )
            self.region_combo.blockSignals(True)
            self.region_combo.setCurrentIndex(previous)
            self.region_combo.blockSignals(False)
            return
        self._load_region(self.region_by_index[index])

    def _load_region(self, region: Region) -> None:
        self.region_description.setText(region.description)
        self.member_list.clear()
        self.member_names = ()
        self.skipped_csv_path = None
        self.skipped_csv_stamp = None
        default_interval = 15
        if region.enabled:
            try:
                self.member_names = region_member_names(region)
                config = load_config(region.config_path)
                default_interval = config.poll_interval_minutes
                self.skipped_csv_path = config.preview_csv.with_name(
                    "preview_skipped.csv"
                )
                self.log_path = user_data_root() / "logs" / f"{region.key}.log"
            except Exception as error:
                self._append_log(f"Không đọc được nhân sự {region.name}: {error}")
            excluded = set(
                self.settings.get("regions", {})
                .get(region.key, {})
                .get("excluded_members", [])
            )
            for name in self.member_names:
                item = QListWidgetItem(name)
                self.member_list.addItem(item)
                item.setSelected(name in excluded)
        saved_interval = (
            self.settings.get("regions", {})
            .get(region.key, {})
            .get("poll_interval_minutes", default_interval)
        )
        try:
            self.cycle_minutes.setValue(int(saved_interval))
        except (TypeError, ValueError):
            self.cycle_minutes.setValue(default_interval)
        self._load_telegram_settings(region)
        self._refresh_skipped_csv(force=True)
        self.settings["last_region"] = region.key
        self._set_process_controls(False)

    def _selected_exclusions(self) -> tuple[str, ...]:
        return tuple(item.text() for item in self.member_list.selectedItems())

    def _update_rules_file(self) -> None:
        """Import a validated workbook for the selected region while stopped."""
        region = self._selected_region()
        if not region or not region.enabled:
            return
        if self.process.state() != QProcess.NotRunning:
            QMessageBox.warning(
                self,
                "Đang chạy",
                "Hãy dừng tác vụ trước khi cập nhật file Giao phiếu.",
            )
            return
        try:
            config = load_config(region.config_path)
        except Exception as error:
            QMessageBox.critical(self, "Không đọc được cấu hình", str(error))
            return
        editable_rules = editable_rules_file()
        preferred_source = (
            editable_rules
            if editable_rules and editable_rules.resolve() != config.rules_file.resolve()
            else config.rules_file.parent
        )
        source, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Chọn file Giao phiếu.xlsx",
            str(preferred_source),
            "Excel Workbook (*.xlsx)",
        )
        if not source:
            return
        try:
            backup = install_rules_file(source, config.rules_file)
            rule_count = len(load_rules(config.rules_file))
        except Exception as error:
            QMessageBox.critical(
                self,
                "Không cập nhật được Giao phiếu",
                f"File không được thay thế. Lý do: {error}",
            )
            return
        backup_message = (
            f"\nBản trước: {backup.name}" if backup else ""
        )
        source_path = Path(source).expanduser().resolve()
        self._append_log(
            f"Đã cập nhật file Giao phiếu cho {region.name}: "
            f"{rule_count} quy tắc dịch vụ.\n"
            f"Nguồn: {source_path}\n"
            f"Đang dùng: {config.rules_file}{backup_message}"
        )
        QMessageBox.information(
            self,
            "Đã cập nhật Giao phiếu",
            f"Đã kiểm tra và cập nhật {rule_count} quy tắc cho {region.name}."
            f"\nQuy tắc mới sẽ được áp dụng khi bắt đầu tác vụ tiếp theo."
            f"\nNguồn: {source_path}"
            f"\nĐang dùng: {config.rules_file}"
            f"{backup_message}",
        )

    def _check_version_update(self) -> None:
        region = self._selected_region()
        if not region or not region.enabled:
            return
        if self.process.state() != QProcess.NotRunning:
            QMessageBox.warning(
                self,
                "Đang chạy",
                "Hãy dừng tác vụ trước khi cập nhật ứng dụng.",
            )
            return
        if not region.update_repository:
            QMessageBox.information(
                self, "Chưa cấu hình", f"Chưa cấu hình kho cập nhật cho {region.name}."
            )
            return
        if self._update_busy:
            return
        self._update_busy = True
        self.update_version_button.setText("Đang kiểm tra…")
        self._set_process_controls(False)
        repository = region.update_repository
        current_version = __version__
        self._append_log(
            f"Đang kiểm tra phiên bản {current_version} trên GitHub ({repository})…"
        )

        def check() -> None:
            try:
                release = latest_update(repository, current_version)
                self.update_signals.checked.emit(release, "")
            except Exception as error:
                self.update_signals.checked.emit(None, str(error))

        threading.Thread(target=check, name="ats-version-check", daemon=True).start()

    def _version_check_completed(self, release: object, error: str) -> None:
        if error:
            self._append_log(f"Kiểm tra phiên bản thất bại: {error}")
            QMessageBox.warning(self, "Không kiểm tra được phiên bản", error)
            self._update_busy = False
            self.update_version_button.setText("Kiểm tra phiên bản")
            self._set_process_controls(self.process.state() != QProcess.NotRunning)
            return
        if release is None:
            self._append_log(
                f"Không tìm thấy bản phát hành mới trên GitHub; "
                f"phiên bản hiện tại là {__version__}."
            )
            QMessageBox.information(
                self,
                "Không có bản mới",
                f"Không có bản phát hành mới hơn trên GitHub. Phiên bản hiện tại: {__version__}.",
            )
            self._update_busy = False
            self.update_version_button.setText("Kiểm tra phiên bản")
            self._set_process_controls(self.process.state() != QProcess.NotRunning)
            return

        assert isinstance(release, UpdateRelease)
        notes = release.notes or "Không có ghi chú phát hành."
        prompt = QMessageBox(self)
        prompt.setWindowTitle("Có phiên bản mới")
        prompt.setIcon(QMessageBox.Information)
        prompt.setText(
            f"Có phiên bản {release.version} (hiện tại {__version__}).\n"
            "Tải và cài đặt ngay?"
        )
        prompt.setInformativeText(notes[:4000])
        prompt.setTextFormat(Qt.PlainText)
        prompt.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        prompt.setDefaultButton(QMessageBox.No)
        if prompt.exec() != QMessageBox.Yes:
            self._append_log(f"Đã hoãn cập nhật lên {release.version}.")
            self._update_busy = False
            self.update_version_button.setText("Kiểm tra phiên bản")
            self._set_process_controls(self.process.state() != QProcess.NotRunning)
            return
        try:
            target = detect_install_target()
        except UpdateError as target_error:
            self._append_log(f"Không thể tự cài bản mới: {target_error}")
            QMessageBox.warning(self, "Không thể tự cài đặt", str(target_error))
            self._update_busy = False
            self.update_version_button.setText("Kiểm tra phiên bản")
            self._set_process_controls(self.process.state() != QProcess.NotRunning)
            return

        self.update_version_button.setText("Đang tải…")
        self._append_log(
            f"Đang tải ATS OneBSS {release.version}; xác minh SHA-256 trước khi cài."
        )

        def download_and_stage() -> None:
            try:
                archive = download_asset(release, user_data_root() / "updates")
                result = stage_update(archive, target, release.version)
                self.update_signals.staged.emit(result, "")
            except Exception as stage_error:
                self.update_signals.staged.emit(None, str(stage_error))

        threading.Thread(
            target=download_and_stage,
            name=f"ats-update-{release.version}",
            daemon=True,
        ).start()

    def _update_download_completed(self, result: object, error: str) -> None:
        if error:
            self._append_log(f"Tải/cài đặt bản mới thất bại: {error}")
            QMessageBox.critical(self, "Không cập nhật được", error)
            self._update_busy = False
            self.update_version_button.setText("Kiểm tra phiên bản")
            self._set_process_controls(self.process.state() != QProcess.NotRunning)
            return
        if not isinstance(result, tuple) or len(result) != 2:
            self._update_busy = False
            self.update_version_button.setText("Kiểm tra phiên bản")
            self._set_process_controls(self.process.state() != QProcess.NotRunning)
            QMessageBox.critical(self, "Lỗi cập nhật", "Không nhận được gói cài đặt hợp lệ.")
            return
        staged_app, backup_path = result
        try:
            target = detect_install_target()
            launch_installer(
                staged_app,
                backup_path,
                target,
                script_directory=user_data_root() / "updates",
            )
        except Exception as install_error:
            self._append_log(f"Không thể khởi chạy trình cài bản mới: {install_error}")
            QMessageBox.critical(self, "Không cập nhật được", str(install_error))
            self._update_busy = False
            self.update_version_button.setText("Kiểm tra phiên bản")
            self._set_process_controls(self.process.state() != QProcess.NotRunning)
            return
        self._append_log(
            f"Đã chuẩn bị bản cập nhật. Trình cài sẽ thay thế ứng dụng sau khi thoát; "
            f"bản sao lưu: {backup_path}."
        )
        QMessageBox.information(
            self,
            "Đang cập nhật",
            "Gói đã tải và xác minh. Ứng dụng sẽ đóng, được cập nhật rồi mở lại.",
        )
        QTimer.singleShot(300, QApplication.instance().quit)

    def _start(self, mode: str) -> None:
        region = self._selected_region()
        if not region or not region.enabled:
            QMessageBox.information(
                self,
                "Chưa cấu hình",
                "Miền này chưa có nhân sự và quy tắc nên chưa thể chạy.",
            )
            return
        if self.process.state() != QProcess.NotRunning:
            QMessageBox.warning(
                self, "Đang chạy", "Một tác vụ ATS OneBSS đang hoạt động."
            )
            return
        try:
            load_config(region.config_path)
            self._save_settings()
        except Exception as error:
            QMessageBox.critical(self, "Cấu hình không hợp lệ", str(error))
            return
        try:
            self._persist_telegram_settings(show_result=False)
        except TelegramError as error:
            QMessageBox.warning(self, "Telegram chưa hợp lệ", str(error))
            self.detail_tabs.setCurrentIndex(2)
            return

        command = worker_command(
            region.config_path,
            mode,
            self._selected_exclusions(),
            self.cycle_minutes.value() if mode in {"run", "watch"} else None,
        )
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUNBUFFERED", "1")
        environment.insert("PYTHONIOENCODING", "utf-8")
        if self.telegram_enabled.isChecked():
            token = self.telegram_token.text().strip()
            chat_id = self.telegram_chat_id.text().strip()
            environment.insert(TELEGRAM_TOKEN_ENV, token)
            environment.insert(TELEGRAM_CHAT_ID_ENV, chat_id)
            environment.insert(TELEGRAM_REGION_ENV, region.name)
        self.process.setProcessEnvironment(environment)
        self.process_mode = mode
        self.closing_after_stop = False
        self._output_decoder = codecs.getincrementaldecoder("utf-8")(
            errors="replace"
        )
        self._session_output_buffer = ""
        self._session_expiry_epoch = None
        self._session_expiry_unknown = False
        self._session_expired = False
        self.process.start(command[0], command[1:])
        if not self.process.waitForStarted(5000):
            self.process_mode = ""
            QMessageBox.critical(
                self,
                "Không khởi động được",
                self.process.errorString() or "Không tạo được tiến trình nền.",
            )
            return

        label = {
            "login": "Đang chờ đăng nhập",
            "plan": "Đang chạy thử",
            "run": "Đang tự động giao phiếu",
        }.get(mode, "Đang hoạt động")
        self.status_label.setText(f"{region.name}: {label}")
        self._append_log("\n" + "=" * 68)
        self._append_log(f"Khởi động {label.lower()} cho {region.name}...")
        if self._selected_exclusions():
            self._append_log(
                "Tạm không giao cho: " + ", ".join(self._selected_exclusions())
            )
        self._set_process_controls(True)
        self._start_caffeinate_if_needed()

    def _start_caffeinate_if_needed(self) -> None:
        if (
            sys.platform != "darwin"
            or not self.keep_awake.isChecked()
            or self.process.state() == QProcess.NotRunning
        ):
            return
        try:
            self.caffeinate_process = subprocess.Popen(
                [
                    "/usr/bin/caffeinate",
                    "-ims",
                    "-w",
                    str(int(self.process.processId())),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._append_log("Đã bật chế độ giữ máy Mac không Sleep.")
        except OSError as error:
            self._append_log(f"Không bật được caffeinate: {error}")

    def _read_process_output(self) -> None:
        raw = bytes(self.process.readAllStandardOutput())
        if not raw:
            return
        output = self._output_decoder.decode(raw, final=False)
        self._consume_worker_output(output)

    def _consume_worker_output(self, output: str, final: bool = False) -> None:
        self._session_output_buffer += output
        lines = self._session_output_buffer.splitlines(keepends=True)
        self._session_output_buffer = ""
        if lines and not lines[-1].endswith(("\n", "\r")) and not final:
            self._session_output_buffer = lines.pop()
        visible: list[str] = []
        for line in lines:
            clean = line.strip()
            expiry = re.fullmatch(
                r"ATS_ONEBSS_SESSION_EXPIRY=(\d+|unknown)", clean
            )
            if expiry:
                raw_expiry = expiry.group(1)
                self._session_expiry_epoch = (
                    int(raw_expiry) if raw_expiry.isdigit() else None
                )
                self._session_expiry_unknown = raw_expiry == "unknown"
                self._session_expired = False
                continue
            if clean == "ATS_ONEBSS_SESSION_STATE=expired":
                self._session_expired = True
                continue
            visible.append(line)
        if visible:
            self._write_log_chunk("".join(visible))
        self._update_session_countdown()

    def _update_session_countdown(self) -> None:
        if self.process.state() == QProcess.NotRunning:
            self.session_countdown_label.setText("Phiên OneBSS: —")
        elif self._session_expired:
            self.session_countdown_label.setText("Phiên OneBSS: đã hết hạn")
        elif self._session_expiry_epoch is not None:
            remaining = max(
                0, self._session_expiry_epoch - int(datetime.now().timestamp())
            )
            hours, remainder = divmod(remaining, 3600)
            minutes, seconds = divmod(remainder, 60)
            self.session_countdown_label.setText(
                f"Phiên OneBSS còn: {hours:02d}:{minutes:02d}:{seconds:02d}"
            )
        elif self._session_expiry_unknown:
            self.session_countdown_label.setText(
                "Phiên OneBSS: máy chủ không công bố hạn token"
            )
        else:
            self.session_countdown_label.setText("Phiên OneBSS: đang kiểm tra")

    def _append_log(self, message: str) -> None:
        self._write_log_chunk(message + "\n")

    def _write_log_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(chunk)
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(chunk)
        except OSError:
            pass

    def _copy_log(self) -> None:
        QApplication.clipboard().setText(self.log_text.toPlainText())

    def _open_log_file(self) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.touch(exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_path)))
        except OSError as error:
            QMessageBox.warning(self, "Không mở được nhật ký", str(error))

    def _refresh_skipped_csv(self, force: bool = False) -> None:
        path = self.skipped_csv_path
        if path is None or not path.is_file():
            if force or self.skipped_table.rowCount():
                self.skipped_table.clear()
                self.skipped_table.setRowCount(0)
                self.skipped_table.setColumnCount(0)
                self.skipped_detail_text.clear()
            self.skipped_status.setText("Chưa có file preview_skipped.csv")
            self.open_skipped_button.setEnabled(False)
            self.skipped_csv_stamp = None
            return
        try:
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            if not force and stamp == self.skipped_csv_stamp:
                return
            headers, rows = read_skipped_csv(path)
        except (OSError, csv.Error) as error:
            self.skipped_status.setText(f"Không đọc được CSV: {error}")
            return
        self.skipped_table.setSortingEnabled(False)
        self.skipped_table.clear()
        self.skipped_table.setColumnCount(len(headers))
        self.skipped_table.setRowCount(len(rows))
        if headers:
            self.skipped_table.setHorizontalHeaderLabels(headers)
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row[: len(headers)]):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.skipped_table.setItem(row_index, column_index, item)
        header = self.skipped_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        for column in range(min(4, len(headers))):
            header.resizeSection(column, 145)
        if headers:
            header.setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)
        self.skipped_table.setSortingEnabled(True)
        self.skipped_status.setText(
            f"{len(rows)} phiếu chưa giao — {path.name}"
        )
        self.open_skipped_button.setEnabled(True)
        self.skipped_csv_stamp = stamp
        if rows:
            self.skipped_table.selectRow(0)
        else:
            self.skipped_detail_text.clear()

    def _show_skipped_detail(self) -> None:
        """Show every field of the selected skipped ticket without truncation."""
        row = self.skipped_table.currentRow()
        if row < 0:
            self.skipped_detail_text.clear()
            return
        details: list[str] = []
        for column in range(self.skipped_table.columnCount()):
            header = self.skipped_table.horizontalHeaderItem(column)
            item = self.skipped_table.item(row, column)
            label = header.text() if header else f"Cột {column + 1}"
            value = item.text() if item else ""
            details.append(f"{label}: {value or '—'}")
        self.skipped_detail_text.setPlainText("\n\n".join(details))

    def _open_skipped_csv(self) -> None:
        if self.skipped_csv_path and self.skipped_csv_path.is_file():
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.skipped_csv_path))
            )

    def _load_telegram_settings(self, region: Region) -> None:
        values = (
            self.settings.get("regions", {})
            .get(region.key, {})
            .get("telegram", {})
        )
        self.telegram_enabled.setChecked(bool(values.get("enabled", False)))
        self.telegram_chat_id.setText(str(values.get("chat_id", "")))
        self.telegram_result.clear()
        token = ""
        if region.enabled:
            try:
                token = load_bot_token(region.key)
            except TelegramError as error:
                self._append_log(f"Không đọc được Telegram token: {error}")
        self.telegram_token.setText(token)
        for widget in (
            self.telegram_enabled,
            self.telegram_token,
            self.telegram_chat_id,
            self.save_telegram_button,
            self.test_telegram_button,
        ):
            widget.setEnabled(region.enabled)

    def _persist_telegram_settings(self, show_result: bool) -> None:
        region = self._selected_region()
        if not region or not region.enabled:
            return
        token = self.telegram_token.text().strip()
        chat_id = self.telegram_chat_id.text().strip()
        enabled = self.telegram_enabled.isChecked()
        if enabled and not token:
            raise TelegramError("Hãy nhập Telegram Bot Token.")
        if enabled and not chat_id:
            raise TelegramError("Hãy nhập Telegram Chat ID.")
        if token:
            save_bot_token(region.key, token)
        region_settings = self.settings.setdefault("regions", {}).setdefault(
            region.key, {}
        )
        region_settings["telegram"] = {
            "enabled": enabled,
            "chat_id": chat_id,
        }
        self._save_settings()
        if show_result:
            self.telegram_result.setStyleSheet("color: #16794b;")
            self.telegram_result.setText("Đã lưu cấu hình Telegram.")

    def _save_telegram_settings(self) -> None:
        try:
            self._persist_telegram_settings(show_result=True)
        except TelegramError as error:
            self.telegram_result.setStyleSheet("color: #b42318;")
            self.telegram_result.setText(str(error))

    def _test_telegram(self) -> None:
        try:
            self._persist_telegram_settings(show_result=False)
        except TelegramError as error:
            self.telegram_result.setStyleSheet("color: #b42318;")
            self.telegram_result.setText(str(error))
            return
        region = self._selected_region()
        if not region:
            return
        token = self.telegram_token.text().strip()
        chat_id = self.telegram_chat_id.text().strip()
        self.test_telegram_button.setEnabled(False)
        self.telegram_result.setStyleSheet("color: #607080;")
        self.telegram_result.setText("Đang gửi tin thử...")

        def send_test() -> None:
            try:
                send_message(
                    token,
                    chat_id,
                    f"✅ ATS OneBSS — {region.name}\nTelegram đã được kết nối thành công.",
                )
            except Exception as error:
                self.telegram_signals.completed.emit(False, str(error))
            else:
                self.telegram_signals.completed.emit(True, "Đã gửi tin thử thành công.")

        threading.Thread(target=send_test, daemon=True).start()

    def _telegram_test_completed(self, success: bool, message: str) -> None:
        region = self._selected_region()
        self.test_telegram_button.setEnabled(
            bool(
                region
                and region.enabled
                and self.process.state() == QProcess.NotRunning
            )
        )
        self.telegram_result.setStyleSheet(
            "color: #16794b;" if success else "color: #b42318;"
        )
        self.telegram_result.setText(message)

    def _finish_login(self) -> None:
        if (
            self.process.state() == QProcess.NotRunning
            or self.process_mode != "login"
        ):
            return
        self.process.write(b"\n")
        self.finish_login_button.setEnabled(False)
        self._append_log("Đang lưu phiên đăng nhập và đóng Chromium...")

    def _stop(self) -> None:
        if self.process.state() == QProcess.NotRunning:
            return
        self.status_label.setText("Đang dừng an toàn...")
        self._append_log("Đang yêu cầu dừng tác vụ...")
        pid = int(self.process.processId())
        try:
            if os.name == "nt":
                self.process.terminate()
            else:
                os.kill(pid, signal.SIGINT)
        except (OSError, ProcessLookupError):
            self.process.terminate()
        QTimer.singleShot(5000, self._force_stop_if_needed)

    def _force_stop_if_needed(self) -> None:
        if self.process.state() != QProcess.NotRunning:
            self._append_log(
                "Tác vụ chưa dừng sau 5 giây; đang kết thúc tiến trình."
            )
            self.process.kill()

    def _process_finished(
        self, return_code: int, _exit_status: QProcess.ExitStatus
    ) -> None:
        mode = self.process_mode
        self._read_process_output()
        self._consume_worker_output(
            self._output_decoder.decode(b"", final=True), final=True
        )
        self._stop_caffeinate()
        if return_code == 0 or (mode == "run" and return_code in {-2, 130}):
            self.status_label.setText("Đã dừng")
            self._append_log("Tác vụ đã kết thúc.")
        else:
            self.status_label.setText("Đã dừng do lỗi")
            self._append_log(f"Tác vụ kết thúc với mã lỗi {return_code}.")
        self.process_mode = ""
        self._set_process_controls(False)
        if self.closing_after_stop:
            QApplication.instance().quit()

    def _process_error(self, _error: QProcess.ProcessError) -> None:
        if self.process.state() == QProcess.NotRunning and self.process_mode:
            self._append_log(f"Lỗi tiến trình: {self.process.errorString()}")

    def _stop_caffeinate(self) -> None:
        if self.caffeinate_process and self.caffeinate_process.poll() is None:
            self.caffeinate_process.terminate()
        self.caffeinate_process = None

    def _set_process_controls(self, running: bool) -> None:
        region = self._selected_region()
        enabled_region = bool(region and region.enabled)
        idle = not running and not self._update_busy
        self.login_button.setEnabled(enabled_region and idle)
        self.plan_button.setEnabled(enabled_region and idle)
        self.start_button.setEnabled(enabled_region and idle)
        self.stop_button.setEnabled(running)
        self.finish_login_button.setEnabled(
            running and self.process_mode == "login"
        )
        self.region_combo.setEnabled(idle)
        self.member_list.setEnabled(idle)
        self.cycle_minutes.setEnabled(enabled_region and idle)
        self.update_rules_button.setEnabled(enabled_region and idle)
        self.update_version_button.setEnabled(
            enabled_region
            and bool(region.update_repository)
            and idle
        )
        for widget in (
            self.telegram_enabled,
            self.telegram_token,
            self.telegram_chat_id,
            self.save_telegram_button,
            self.test_telegram_button,
        ):
            widget.setEnabled(enabled_region and idle)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.process.state() != QProcess.NotRunning:
            answer = QMessageBox.question(
                self,
                "Dừng ATS OneBSS?",
                "Chương trình đang giao phiếu. Dừng an toàn và đóng ứng dụng?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.closing_after_stop = True
            self._stop()
            event.ignore()
            return
        self._save_settings()
        self._stop_caffeinate()
        event.accept()


def run_gui() -> None:
    application = QApplication(sys.argv)
    application.setApplicationName(APP_NAME)
    application.setOrganizationName("CNTTDVS")
    logo_path = resource_root() / "assets" / "vinaphone-logo.png"
    if logo_path.exists():
        application.setWindowIcon(QIcon(str(logo_path)))
    # Prevent two GUI instances from competing for the same Chromium profile.
    lock_path = user_data_root() / "ats-onebss-gui.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    gui_lock = QLockFile(str(lock_path))
    gui_lock.setStaleLockTime(0)
    if not gui_lock.tryLock(100):
        QMessageBox.warning(
            None,
            "ATS OneBSS đang chạy",
            "Ứng dụng ATS OneBSS đã mở. Hãy sử dụng cửa sổ hiện tại để đăng nhập.",
        )
        raise SystemExit(1)
    application._ats_onebss_gui_lock = gui_lock
    try:
        window = ATSOneBSSWindow(prepare_runtime_root())
    except RegionError as error:
        QMessageBox.critical(None, "Không đọc được cấu hình miền", str(error))
        raise SystemExit(2) from error
    window.show()
    raise SystemExit(application.exec())
