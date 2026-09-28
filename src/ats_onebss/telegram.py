from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TELEGRAM_TOKEN_ENV = "ATS_ONEBSS_TELEGRAM_TOKEN"
TELEGRAM_CHAT_ID_ENV = "ATS_ONEBSS_TELEGRAM_CHAT_ID"
TELEGRAM_REGION_ENV = "ATS_ONEBSS_TELEGRAM_REGION"
KEYCHAIN_ACCOUNT = "bot-token"


class TelegramError(RuntimeError):
    pass


def keychain_service(region_key: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in "-_." else "-"
        for character in region_key.strip().lower()
    )
    return f"vn.cnttdvs.ats-onebss.telegram.{cleaned or 'default'}"


def save_bot_token(region_key: str, token: str) -> None:
    """Store a Telegram bot token outside the JSON/config files."""
    if sys.platform != "darwin":
        raise TelegramError(
            "Bản hiện tại chỉ hỗ trợ lưu Telegram token trong Keychain macOS."
        )
    value = token.strip()
    if not value:
        raise TelegramError("Telegram Bot Token không được để trống.")
    result = subprocess.run(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-U",
            "-s",
            keychain_service(region_key),
            "-a",
            KEYCHAIN_ACCOUNT,
            "-w",
            value,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Không rõ nguyên nhân").strip()
        raise TelegramError(f"Không lưu được token vào Keychain: {detail}")


def load_bot_token(region_key: str) -> str:
    if sys.platform != "darwin":
        return ""
    result = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-s",
            keychain_service(region_key),
            "-a",
            KEYCHAIN_ACCOUNT,
            "-w",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _safe_error(error: BaseException, token: str = "") -> str:
    message = str(error).strip() or error.__class__.__name__
    if token:
        message = message.replace(token, "[TOKEN ĐÃ ẨN]")
    return message[:1200]


def send_message(token: str, chat_id: str, message: str, timeout: int = 12) -> None:
    clean_token = token.strip()
    clean_chat_id = chat_id.strip()
    if not clean_token:
        raise TelegramError("Chưa nhập Telegram Bot Token.")
    if not clean_chat_id:
        raise TelegramError("Chưa nhập Telegram Chat ID.")
    body = urlencode(
        {
            "chat_id": clean_chat_id,
            "text": message[:4000],
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{clean_token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            payload = json.loads(error.read().decode("utf-8"))
            description = str(payload.get("description", "")).strip()
        except (ValueError, OSError):
            description = ""
        detail = description or f"HTTP {error.code}"
        raise TelegramError(f"Telegram từ chối yêu cầu: {detail}") from None
    except (URLError, TimeoutError, OSError) as error:
        raise TelegramError(
            "Không kết nối được Telegram: " + _safe_error(error, clean_token)
        ) from None
    except ValueError:
        raise TelegramError("Telegram trả về dữ liệu không hợp lệ.") from None
    if not payload.get("ok"):
        raise TelegramError(
            "Telegram không xác nhận gửi tin: "
            + str(payload.get("description", "Không rõ nguyên nhân"))
        )


@dataclass
class TelegramNotifier:
    token: str = ""
    chat_id: str = ""
    region_name: str = "ATS OneBSS"
    minimum_repeat_seconds: int = 300
    _last_signature: str = field(default="", init=False, repr=False)
    _last_sent_at: float = field(default=0.0, init=False, repr=False)

    @classmethod
    def from_environment(cls) -> "TelegramNotifier":
        return cls(
            token=os.environ.get(TELEGRAM_TOKEN_ENV, "").strip(),
            chat_id=os.environ.get(TELEGRAM_CHAT_ID_ENV, "").strip(),
            region_name=os.environ.get(
                TELEGRAM_REGION_ENV, "ATS OneBSS"
            ).strip()
            or "ATS OneBSS",
        )

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def notify_error(self, context: str, error: BaseException | str) -> bool:
        if not self.enabled:
            return False
        detail = _safe_error(
            error if isinstance(error, BaseException) else RuntimeError(error),
            self.token,
        )
        signature = f"{context}\n{detail}"
        now = time.monotonic()
        if (
            signature == self._last_signature
            and now - self._last_sent_at < self.minimum_repeat_seconds
        ):
            return False
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        send_message(
            self.token,
            self.chat_id,
            "\n".join(
                [
                    f"🚨 ATS OneBSS — {self.region_name}",
                    context,
                    f"Lỗi: {detail}",
                    f"Thời gian: {timestamp}",
                ]
            ),
        )
        self._last_signature = signature
        self._last_sent_at = now
        return True

    def notify_session_expiring(self, expires_at: int) -> None:
        if not self.enabled:
            return
        expiry = datetime.fromtimestamp(expires_at).astimezone().strftime(
            "%d/%m/%Y %H:%M:%S"
        )
        send_message(
            self.token,
            self.chat_id,
            "\n".join(
                [
                    f"⚠️ ATS OneBSS — {self.region_name}",
                    "Phiên đăng nhập OneBSS sẽ hết hạn trong 15 phút hoặc ít hơn.",
                    f"Thời điểm hết hạn dự kiến: {expiry}",
                ]
            ),
        )

    def notify_session_expired(self, expires_at: int | None = None) -> None:
        if not self.enabled:
            return
        expiry_line = (
            "Thời điểm hết hạn: "
            + datetime.fromtimestamp(expires_at).astimezone().strftime(
                "%d/%m/%Y %H:%M:%S"
            )
            if expires_at is not None else "OneBSS không còn hiển thị trang làm việc đã đăng nhập."
        )
        send_message(
            self.token,
            self.chat_id,
            "\n".join(
                [
                    f"🚨 ATS OneBSS — {self.region_name}",
                    "Phiên đăng nhập OneBSS đã hết hạn hoặc bị ngắt.",
                    expiry_line,
                    "Hãy đăng nhập lại trong ứng dụng.",
                ]
            ),
        )
