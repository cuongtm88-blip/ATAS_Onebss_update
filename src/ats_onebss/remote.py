from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .telegram import (
    TelegramError,
    _telegram_updates,
    load_bot_token,
    send_message,
)


def _pid_running(value: object) -> bool:
    try:
        pid = int(value)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except (TypeError, ValueError, OSError):
        return False


def _resume_command(update: dict, allowed_chat_id: str) -> bool:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    if (
        str(chat.get("id", "")) != allowed_chat_id.strip()
        or chat.get("type") != "private"
    ):
        return False
    return bool(re.fullmatch(r"/resume(?:@\w+)?", str(message.get("text", "")).strip(), re.I))


def _settings(data_root: Path, region_key: str) -> tuple[bool, str]:
    try:
        values = json.loads((data_root / "gui-settings.json").read_text(encoding="utf-8"))
        region = values.get("regions", {}).get(region_key, {}).get("telegram", {})
        return bool(region.get("enabled")), str(region.get("chat_id", "")).strip()
    except (OSError, ValueError, TypeError):
        return False, ""


def _active_worker(path: Path) -> bool:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if state.get("starting"):
        return True
    if _pid_running(state.get("pid")):
        return True
    path.unlink(missing_ok=True)
    return False


def _gui_running(data_root: Path) -> bool:
    try:
        pid = (data_root / "ats-onebss-gui.lock").read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return False
    return _pid_running(pid)


def _write_resume_request(data_root: Path, region_key: str) -> None:
    path = data_root / f"remote-resume-{region_key}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"region": region_key}), encoding="utf-8")
    temporary.replace(path)


def _launch_app(region_key: str) -> None:
    if getattr(sys, "frozen", False):
        bundle = next(
            (parent for parent in Path(sys.executable).resolve().parents if parent.suffix == ".app"),
            None,
        )
        if bundle is None:
            raise RuntimeError("Không xác định được ứng dụng ATS OneBSS để mở lại.")
        command = ["/usr/bin/open", "-a", str(bundle), "--args", "--resume", region_key]
    else:
        command = [sys.executable, "-m", "ats_onebss.gui_main", "--resume", region_key]
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def run_supervisor(region_key: str) -> None:
    """Keep a private Telegram /resume listener alive after the GUI closes."""
    if sys.platform != "darwin":
        return
    import fcntl

    from .gui import user_data_root

    data_root = user_data_root()
    data_root.mkdir(parents=True, exist_ok=True)
    lock_path = data_root / f"telegram-supervisor-{region_key}.lock"
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        log_path = data_root / "logs" / f"{region_key}-supervisor.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        def log(message: str) -> None:
            with log_path.open("a", encoding="utf-8") as output:
                output.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")

        offset: int | None = None
        initialized = False
        state_path = data_root / f"{region_key}-worker-state.json"
        while True:
            try:
                enabled, chat_id = _settings(data_root, region_key)
                token = load_bot_token(region_key) if enabled and chat_id else ""
                if not token:
                    time.sleep(5)
                    continue
                if not initialized:
                    updates = _telegram_updates(token, None, 0)
                    offset = max(
                        (int(update.get("update_id", -1)) for update in updates),
                        default=-1,
                    ) + 1
                    initialized = True
                    time.sleep(2)
                    continue
                if _active_worker(state_path):
                    time.sleep(2)
                    continue
                updates = _telegram_updates(token, offset, 0)
                resume_requested = False
                for update in updates:
                    try:
                        offset = max(offset or 0, int(update.get("update_id", -1)) + 1)
                    except (TypeError, ValueError):
                        continue
                    resume_requested |= _resume_command(update, chat_id)
                if resume_requested:
                    if _active_worker(state_path):
                        send_message(token, chat_id, "ATS OneBSS đang chạy; bỏ qua lệnh /resume.")
                    else:
                        send_message(
                            token,
                            chat_id,
                            "Đã nhận /resume. ATS OneBSS đang khởi động lại chế độ giao phiếu.",
                        )
                        if _gui_running(data_root):
                            _write_resume_request(data_root, region_key)
                        else:
                            _launch_app(region_key)
                        log("Đã nhận lệnh /resume từ Chat ID được cấu hình.")
                        time.sleep(10)
                else:
                    time.sleep(2)
            except TelegramError as error:
                log(f"Không đọc được lệnh Telegram: {error}")
                time.sleep(10)
            except Exception as error:
                log(f"Lỗi giám sát Telegram: {type(error).__name__}: {error}")
                time.sleep(10)
