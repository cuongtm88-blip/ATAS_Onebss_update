import os
from pathlib import Path

import ats_onebss.gui as gui


def test_worker_command_uses_cli_and_exclusions(monkeypatch):
    monkeypatch.setattr(gui.sys, "frozen", False, raising=False)

    command = gui.worker_command(
        Path("/tmp/north/config.toml"),
        "run",
        ("Lê Đức Vinh", "Nguyễn Duy Thành"),
    )

    assert command[:4] == [
        gui.sys.executable, "-m", "ats_onebss.gui_main", "--worker",
    ]
    assert command[4:8] == [
        "--config", "/tmp/north/config.toml", "run", "--yes",
    ]
    assert command[-4:] == [
        "--exclude", "Lê Đức Vinh", "--exclude", "Nguyễn Duy Thành",
    ]


def test_worker_command_does_not_confirm_plan(monkeypatch):
    monkeypatch.setattr(gui.sys, "frozen", False, raising=False)

    command = gui.worker_command(Path("config.toml"), "plan", ())

    assert "--yes" not in command


def test_worker_command_passes_selected_poll_interval(monkeypatch):
    monkeypatch.setattr(gui.sys, "frozen", False, raising=False)

    command = gui.worker_command(Path("config.toml"), "run", (), 7)

    assert command[4:9] == [
        "--config", "config.toml", "--poll-interval-minutes", "7", "run",
    ]


def test_login_does_not_receive_exclusions(monkeypatch):
    monkeypatch.setattr(gui.sys, "frozen", False, raising=False)

    command = gui.worker_command(
        Path("config.toml"), "login", ("Nguyễn Duy Thành",)
    )

    assert command[-3:] == ["--config", "config.toml", "login"]
    assert "--exclude" not in command


def test_vinaphone_logo_is_available():
    assert (gui.resource_root() / "assets" / "vinaphone-logo.png").is_file()


def test_region_catalog_fills_missing_update_repo_without_overwriting_user_value(tmp_path):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    runtime_catalog = tmp_path / "runtime-regions.toml"
    runtime_catalog.write_text(
        '''default_region = "north"
[regions.north]
name = "Miền Bắc"
enabled = true
config = "config.toml"
[regions.central]
name = "Miền Trung"
enabled = false
config = "central.toml"
update_repository = "owner/custom-central"
''',
        encoding="utf-8",
    )
    packaged_catalog = tmp_path / "packaged-regions.toml"
    packaged_catalog.write_text(
        '''default_region = "north"
[regions.north]
name = "Miền Bắc"
enabled = true
config = "config.toml"
update_repository = "owner/north"
[regions.central]
name = "Miền Trung"
enabled = false
config = "central.toml"
update_repository = "owner/central"
''',
        encoding="utf-8",
    )

    catalog = gui.load_app_region_catalog(runtime_catalog, packaged_catalog)

    assert catalog.get("north").update_repository == "owner/north"
    assert catalog.get("central").update_repository == "owner/custom-central"


def test_north_only_package_ignores_stale_regions_and_default(tmp_path):
    (tmp_path / "north.toml").write_text("", encoding="utf-8")
    (tmp_path / "south.toml").write_text("", encoding="utf-8")
    runtime_catalog = tmp_path / "runtime-regions.toml"
    runtime_catalog.write_text(
        '''default_region = "south"
[regions.north]
name = "Miền Bắc"
enabled = true
config = "north.toml"
[regions.south]
name = "Miền Nam"
enabled = true
config = "south.toml"
''',
        encoding="utf-8",
    )
    packaged_catalog = tmp_path / "packaged-regions.toml"
    packaged_catalog.write_text(
        '''default_region = "north"
[regions.north]
name = "Miền Bắc"
enabled = true
config = "north.toml"
update_repository = "owner/north"
''',
        encoding="utf-8",
    )

    catalog = gui.load_app_region_catalog(runtime_catalog, packaged_catalog)

    assert [region.key for region in catalog.regions] == ["north"]
    assert catalog.default_region == "north"


def test_prepare_runtime_root_refreshes_newer_editable_rules(monkeypatch, tmp_path):
    project = tmp_path / "project"
    resources = tmp_path / "resources"
    runtime = tmp_path / "runtime"
    executable = project / "dist" / "ATS-OneBSS.app" / "Contents" / "MacOS" / "ATS-OneBSS"
    executable.parent.mkdir(parents=True)
    executable.touch()
    resources.mkdir()
    for relative in gui.RESOURCE_FILES:
        (resources / relative).write_text("bundled", encoding="utf-8")
    runtime.mkdir()
    runtime_rules = runtime / "Giao phiếu_demo2.xlsx"
    runtime_rules.write_text("old runtime", encoding="utf-8")
    editable_rules = project / "Giao phiếu_demo2.xlsx"
    editable_rules.parent.mkdir(parents=True, exist_ok=True)
    editable_rules.write_text("new editable", encoding="utf-8")
    os.utime(runtime_rules, (1, 1))
    os.utime(editable_rules, (2, 2))
    monkeypatch.setattr(gui.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gui.sys, "executable", str(executable))
    monkeypatch.setattr(gui.sys, "_MEIPASS", str(resources), raising=False)
    monkeypatch.setattr(gui, "user_data_root", lambda: runtime)

    assert gui.prepare_runtime_root() == runtime
    assert runtime_rules.read_text(encoding="utf-8") == "new editable"


def test_project_rule_sync_adds_missing_bundled_rules_without_replacing_user_rules(tmp_path):
    runtime = tmp_path / "runtime-projects.toml"
    packaged = tmp_path / "bundled-projects.toml"
    original = '''[[projects]]
name = "Dự án BCA"
priority = 1
match_fields = ["customer_name"]
contains = "BCA tùy chỉnh"
fixed_assignee = "Người dùng"
'''
    runtime.write_text(original, encoding="utf-8")
    packaged.write_text('''[[projects]]
name = "Dự án BCA"
priority = 90
match_fields = ["customer_name"]
contains = "BCA mặc định"
fixed_assignee = "Người mặc định"

[[projects]]
name = "Quy tắc Chứng Khoán"
priority = 90
is_project = false
match_fields = ["customer_name"]
contains = "Chứng Khoán"
fixed_assignee = "Đào Anh Vũ"

[[projects]]
name = "Quy tắc Truyền Hình Cáp"
priority = 90
is_project = false
match_fields = ["customer_name"]
contains = "Truyền Hình Cáp Việt Nam"
fixed_assignee = "Đào Anh Vũ"
''', encoding="utf-8")

    backup = gui.sync_missing_project_rules(runtime, packaged)

    assert backup is not None
    assert backup.read_text(encoding="utf-8") == original
    merged = gui.load_project_rules(runtime)
    assert next(rule for rule in merged if rule.name == "Dự án BCA").contains == "BCA tùy chỉnh"
    assert {rule.name for rule in merged} == {
        "Dự án BCA", "Quy tắc Chứng Khoán", "Quy tắc Truyền Hình Cáp",
    }


def test_install_rules_file_validates_replaces_and_keeps_backup(tmp_path):
    source = gui.resource_root() / "Giao phiếu.xlsx"
    target = tmp_path / "Giao phiếu.xlsx"
    target.write_text("previous rules", encoding="utf-8")

    backup = gui.install_rules_file(source, target)

    assert backup is not None
    assert backup.read_text(encoding="utf-8") == "previous rules"
    assert gui.load_rules(target)


def test_install_rules_file_accepts_group_percentage_workbook(tmp_path):
    source = gui.resource_root() / "Giao phiếu_demo2.xlsx"
    target = tmp_path / "Giao phiếu_demo2.xlsx"

    backup = gui.install_rules_file(source, target)

    assert backup is None
    assert len(gui.load_rules(target)) == 60


def test_install_rules_file_rejects_non_excel_file(tmp_path):
    source = tmp_path / "Giao phiếu.csv"
    source.write_text("not an Excel workbook", encoding="utf-8")

    try:
        gui.install_rules_file(source, tmp_path / "Giao phiếu.xlsx")
    except ValueError as error:
        assert ".xlsx" in str(error)
    else:
        raise AssertionError("Expected a non-Excel file to be rejected")


def test_install_rules_file_rejects_selecting_the_active_file():
    active_rules = gui.resource_root() / "Giao phiếu.xlsx"

    try:
        gui.install_rules_file(active_rules, active_rules)
    except ValueError as error:
        assert "chính file quy tắc đang chạy" in str(error)
    else:
        raise AssertionError("Expected selecting the active file to be rejected")


def test_read_skipped_csv_preserves_text_ids_and_utf8(tmp_path):
    path = tmp_path / "preview_skipped.csv"
    path.write_text(
        "\ufeffMã giao dịch,Mã thuê bao,Lý do\n"
        "00005275,00123,Thiếu quy tắc Tên miền\n",
        encoding="utf-8",
    )

    headers, rows = gui.read_skipped_csv(path)

    assert headers == ["Mã giao dịch", "Mã thuê bao", "Lý do"]
    assert rows == [["00005275", "00123", "Thiếu quy tắc Tên miền"]]


def test_read_skipped_csv_returns_empty_when_file_missing(tmp_path):
    assert gui.read_skipped_csv(tmp_path / "missing.csv") == ([], [])
