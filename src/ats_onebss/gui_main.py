from __future__ import annotations

import sys

from ats_onebss.cli import main as cli_main
from ats_onebss.browser import BrowserProfileInUseError
from ats_onebss.gui import run_gui
from ats_onebss.telegram import TelegramNotifier


def _configure_worker_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", line_buffering=True, write_through=True)


def main() -> None:
    # The packaged .app also acts as its own background worker. This keeps the
    # macOS distribution to one application while preserving stdout for the UI.
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        del sys.argv[1]
        _configure_worker_streams()
        try:
            cli_main()
        except BrowserProfileInUseError as error:
            # A second login/worker must not produce a Playwright traceback in
            # the GUI log; report the actionable profile ownership reason.
            print(f"Không thể mở phiên Chromium: {error}")
            return
        except KeyboardInterrupt:
            raise
        except SystemExit as error:
            if error.code not in (None, 0):
                notifier = TelegramNotifier.from_environment()
                try:
                    notifier.notify_error(
                        "Tiến trình ATS OneBSS đã dừng do lỗi", error
                    )
                except Exception as telegram_error:
                    print(f"Không gửi được cảnh báo Telegram: {telegram_error}")
            raise
        except BaseException as error:
            notifier = TelegramNotifier.from_environment()
            try:
                notifier.notify_error("Tiến trình ATS OneBSS đã dừng do lỗi", error)
            except Exception as telegram_error:
                print(f"Không gửi được cảnh báo Telegram: {telegram_error}")
            raise
        return

    run_gui()


if __name__ == "__main__":
    main()
