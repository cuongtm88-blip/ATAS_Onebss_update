from __future__ import annotations

import tomllib
import os
import socket
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class Config:
    root: Path
    onebss_url: str
    browser_profile: Path
    headless: bool
    browser_channel: str
    task: str
    batch_size: int
    timeout_ms: int
    rules_file: Path
    project_rules_file: Path | None
    use_backup_members: bool
    member_target_ratios: dict[str, Decimal]
    sheet_url: str
    sheet_name: str
    sheet_name_template: str
    sheet_data_start_row: int
    sheet_columns: tuple[str, ...]
    database: Path
    preview_csv: Path
    timezone: str
    poll_interval_minutes: int
    error_retry_seconds: int
    dashboard_enabled: bool
    dashboard_url: str
    dashboard_service_key: str
    dashboard_worker_id: str
    dashboard_command_poll_seconds: int
    excluded_members: tuple[str, ...]


def load_config(path: str | Path) -> Config:
    config_path = Path(path).resolve()
    root = config_path.parent
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    onebss = data["onebss"]
    rules = data["rules"]
    sheet = data["google_sheet"]
    runtime = data["runtime"]
    dashboard = data.get("dashboard", {})
    balance = data.get("balance", {})
    configured_sheet_columns = list(sheet.get("columns", [
        "transaction_id", "subscriber_id", "service", "assignee",
        "subscriber_name", "contract_type", "labor_address",
        "labor_province", "project_name", "reassignment",
    ]))
    # Older Application Support configs are user-owned and are not overwritten
    # by app updates. Add the K-column field at load time without resetting the
    # user's other settings.
    if "reassignment" not in configured_sheet_columns:
        insert_at = (
            configured_sheet_columns.index("project_name") + 1
            if "project_name" in configured_sheet_columns
            else len(configured_sheet_columns)
        )
        configured_sheet_columns.insert(insert_at, "reassignment")
    sheet_columns = tuple(configured_sheet_columns)
    required_sheet_columns = (
        "transaction_id", "subscriber_id", "service", "assignee",
    )
    if sheet_columns[:4] != required_sheet_columns:
        raise ValueError(
            "google_sheet.columns phải bắt đầu bằng transaction_id, "
            "subscriber_id, service, assignee"
        )
    member_target_ratios = {
        str(name).strip(): Decimal(str(value))
        for name, value in balance.get("member_target_ratios", {}).items()
    }
    if any(not name or ratio <= 0 for name, ratio in member_target_ratios.items()):
        raise ValueError("balance.member_target_ratios phải có tên và hệ số lớn hơn 0")
    def resolve(value: str) -> Path:
        candidate = root / value
        if candidate.exists():
            return candidate.resolve()
        # Windows filesystems may normalize decomposed Unicode filenames to
        # NFC when checking out the repository. Resolve configured resource
        # names by their canonical Unicode form as a portable fallback.
        wanted_name = unicodedata.normalize("NFC", candidate.name)
        try:
            match = next(
                item for item in candidate.parent.iterdir()
                if unicodedata.normalize("NFC", item.name) == wanted_name
            )
        except (FileNotFoundError, StopIteration):
            return candidate.resolve()
        return match.resolve()
    project_rules_value = rules.get("projects_file", "project_rules.toml")
    return Config(
        root=root,
        onebss_url=onebss["url"],
        browser_profile=resolve(onebss.get("browser_profile", ".browser-profile")),
        headless=bool(onebss.get("headless", False)),
        browser_channel=onebss.get("browser_channel", "chrome"),
        task=onebss.get("task", "Kiểm tra và xử lý"),
        batch_size=int(onebss.get("batch_size", 20)),
        timeout_ms=int(onebss.get("timeout_ms", 30_000)),
        rules_file=resolve(rules["file"]),
        project_rules_file=(
            resolve(project_rules_value) if project_rules_value else None
        ),
        use_backup_members=bool(rules.get("use_backup_members", False)),
        member_target_ratios=member_target_ratios,
        sheet_url=sheet["url"],
        sheet_name=sheet["sheet_name"],
        sheet_name_template=sheet.get("sheet_name_template", "Tháng {month}/{year}"),
        sheet_data_start_row=int(sheet.get("data_start_row", 9)),
        sheet_columns=sheet_columns,
        database=resolve(runtime.get("database", "ats_onebss.db")),
        preview_csv=resolve(runtime.get("preview_csv", "preview_assignments.csv")),
        timezone=runtime.get("timezone", "Asia/Ho_Chi_Minh"),
        poll_interval_minutes=int(runtime.get("poll_interval_minutes", 15)),
        error_retry_seconds=max(1, int(runtime.get("error_retry_seconds", 3))),
        dashboard_enabled=bool(dashboard.get("enabled", False)),
        dashboard_url=str(dashboard.get("supabase_url", "")).rstrip("/"),
        dashboard_service_key=os.environ.get(
            str(dashboard.get("service_key_env", "ATS_ONEBSS_SUPABASE_SERVICE_KEY")),
            "",
        ).strip(),
        dashboard_worker_id=str(
            dashboard.get("worker_id", f"{socket.gethostname()}-ats-onebss")
        ),
        dashboard_command_poll_seconds=max(
            2, int(dashboard.get("command_poll_seconds", 5))
        ),
        excluded_members=(),
    )
