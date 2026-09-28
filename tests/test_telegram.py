import ats_onebss.telegram as telegram


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
