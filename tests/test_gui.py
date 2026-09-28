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
    runtime_rules = runtime / "Giao phiếu.xlsx"
    runtime_rules.write_text("old runtime", encoding="utf-8")
    editable_rules = project / "Giao phiếu.xlsx"
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


def test_install_rules_file_validates_replaces_and_keeps_backup(tmp_path):
    source = gui.resource_root() / "Giao phiếu.xlsx"
    target = tmp_path / "Giao phiếu.xlsx"
    target.write_text("previous rules", encoding="utf-8")

    backup = gui.install_rules_file(source, target)

    assert backup is not None
    assert backup.read_text(encoding="utf-8") == "previous rules"
    assert gui.load_rules(target)


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
