import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import ats_onebss.cli as cli
from ats_onebss.models import Ticket


def test_parser_accepts_multiple_excluded_members():
    args = cli.parser().parse_args([
        "run", "--yes", "--exclude", "Lê Đức Vinh",
        "--exclude", "Nguyễn Duy Thành",
    ])
    assert args.exclude == [
        "Lê Đức Vinh", "Nguyễn Duy Thành",
    ]


def test_parser_accepts_poll_interval_override():
    args = cli.parser().parse_args([
        "--poll-interval-minutes", "7", "run", "--yes",
    ])
    assert args.poll_interval_minutes == 7


def test_unique_tickets_keeps_only_one_visible_duplicate_per_cycle():
    tickets = [
        Ticket("GD1", "TB1", "Leasedline GE"),
        Ticket("GD1", "TB1", "Leasedline GE"),
        Ticket("GD2", "TB2", "Fiber"),
    ]
    assert [ticket.key for ticket in cli._unique_tickets(tickets)] == [
        "GD1|TB1", "GD2|TB2",
    ]


def test_recover_browser_restarts_context_when_page_reload_fails(monkeypatch):
    calls = []

    class FakeSession:
        async def restart_context(self):
            calls.append("restart")

    page = SimpleNamespace(wait_for_selector=AsyncMock())
    onebss = SimpleNamespace(
        page=page,
        reload_for_recovery=AsyncMock(side_effect=RuntimeError("overlay stuck")),
        open=AsyncMock(),
        ensure_unassigned_filters=AsyncMock(),
        refresh_tickets=AsyncMock(),
    )
    async def open_onebss():
        calls.append("open-onebss")
        onebss.page = page

    onebss.open.side_effect = open_onebss
    sheet = SimpleNamespace(page=object(), open=AsyncMock())
    monkeypatch.setattr(cli, "sync_pending", AsyncMock())
    config = SimpleNamespace(timeout_ms=30000)

    asyncio.run(cli.recover_browser(
        config, FakeSession(), onebss, sheet, object(), object()
    ))

    assert calls == ["restart", "open-onebss"]
    assert sheet.page is None
    sheet.open.assert_awaited_once()
    cli.sync_pending.assert_awaited_once()
    onebss.ensure_unassigned_filters.assert_awaited_once()
    onebss.refresh_tickets.assert_awaited_once()
def test_completed_duplicate_is_released_when_visible_count_decreases():
    processed = {"GD1|TB1", "GD2|TB2"}
    cli._release_completed_occurrences(
        processed,
        {"GD1|TB1": 3, "GD2|TB2": 1},
        {"GD1|TB1": 2, "GD2|TB2": 1},
    )
    assert processed == {"GD2|TB2"}


def test_stale_duplicate_stays_blocked_when_count_does_not_decrease():
    processed = {"GD1|TB1"}
    cli._release_completed_occurrences(
        processed, {"GD1|TB1": 2}, {"GD1|TB1": 2}
    )
    assert processed == {"GD1|TB1"}


def test_watch_retries_failed_cycle_without_normal_interval(tmp_path, monkeypatch):
    config = SimpleNamespace(
        database=tmp_path / "ledger.db",
        poll_interval_minutes=15,
        error_retry_seconds=3,
        timezone="Asia/Ho_Chi_Minh",
    )
    delays = []
    attempts = 0

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeOneBSS:
        def __init__(self):
            self.refreshes = 0

        async def refresh_tickets(self):
            self.refreshes += 1

        async def open(self):
            return None

    onebss = FakeOneBSS()

    async def fake_open_clients(*_args):
        return onebss, object()

    async def fake_process_available(*_args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("lỗi checkbox")
        raise asyncio.CancelledError

    async def fake_load_sheet_source(*_args):
        return {}, {}

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(cli, "BrowserSession", lambda _config: FakeSession())
    monkeypatch.setattr(cli, "open_clients", fake_open_clients)
    monkeypatch.setattr(cli, "load_sheet_source", fake_load_sheet_source)
    monkeypatch.setattr(cli, "process_available", fake_process_available)
    monkeypatch.setattr(cli.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cli.command_watch(config))

    assert attempts == 2
    assert delays == [3]
    assert onebss.refreshes == 1
