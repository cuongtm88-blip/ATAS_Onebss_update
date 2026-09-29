import ats_onebss.telegram as telegram
import ats_onebss.remote as remote


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return b'{"ok": true, "result": {"message_id": 1}}'


def test_send_message_posts_encoded_chat_and_text(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data.decode("utf-8")
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(telegram, "urlopen", fake_urlopen)

    telegram.send_message("TOKEN", "-100123", "Lỗi đăng nhập")

    assert captured["url"].endswith("/botTOKEN/sendMessage")
    assert "chat_id=-100123" in captured["body"]
    assert "L%E1%BB%97i+%C4%91%C4%83ng+nh%E1%BA%ADp" in captured["body"]
    assert captured["timeout"] == 12


def test_resume_command_requires_configured_private_chat():
    update = {
        "message": {
            "chat": {"id": 12345, "type": "private"},
            "text": "/resume@atsonebss_bot",
        }
    }
    assert remote._resume_command(update, "12345")
    assert not remote._resume_command(update, "999")
    update["message"]["chat"]["type"] = "group"
    assert not remote._resume_command(update, "12345")


def test_notifier_suppresses_immediate_duplicate(monkeypatch):
    messages = []
    monkeypatch.setattr(
        telegram, "send_message", lambda token, chat_id, text: messages.append(text)
    )
    notifier = telegram.TelegramNotifier(
        token="TOKEN", chat_id="123", region_name="Miền Bắc"
    )

    assert notifier.notify_error("Chu kỳ gặp lỗi", RuntimeError("checkbox"))
    assert not notifier.notify_error("Chu kỳ gặp lỗi", RuntimeError("checkbox"))

    assert len(messages) == 1
    assert "Miền Bắc" in messages[0]
    assert "checkbox" in messages[0]


def test_safe_error_redacts_token():
    assert "SECRET" not in telegram._safe_error(
        RuntimeError("URL contains SECRET"), "SECRET"
    )


def test_otp_accepts_only_numeric_code_from_configured_chat(monkeypatch):
    messages = []
    batches = iter([
        [{"update_id": 10, "message": {"chat": {"id": 123}, "text": "123456"}}],
        [
            {"update_id": 11, "message": {"chat": {"id": 456}, "text": "654321"}},
            {"update_id": 12, "message": {"chat": {"id": 123}, "text": "/otp 908172"}},
        ],
    ])
    monkeypatch.setattr(telegram, "_telegram_updates", lambda *_args: next(batches))
    monkeypatch.setattr(
        telegram, "send_message", lambda _token, _chat, message: messages.append(message)
    )

    assert telegram.wait_for_otp("TOKEN", "123", "Miền Bắc", 2) == "908172"
    assert "OTP" in messages[0]


def test_otp_parser_rejects_text_and_untrusted_chat():
    update = {"message": {"chat": {"id": 456}, "text": "111111"}}
    assert telegram._otp_from_update(update, "123") is None
    update["message"]["chat"]["id"] = 123
    update["message"]["text"] = "Mã là 111111"
    assert telegram._otp_from_update(update, "123") is None
    update["message"]["text"] = "111111"
    update["message"]["chat"]["type"] = "group"
    assert telegram._otp_from_update(update, "123") is None
