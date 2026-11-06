import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import ats_onebss.cli as cli
from ats_onebss.models import Member, ServiceRule, Ticket


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


def test_parser_accepts_outside_working_hours_override():
    args = cli.parser().parse_args([
        "--allow-outside-working-hours", "run", "--yes",
    ])
    assert args.allow_outside_working_hours is True


def test_outside_working_hours_override_bypasses_weekends_and_sessions():
    calendar = {"overrides": []}
    config = SimpleNamespace(allow_outside_working_hours=False)
    after_hours = cli.datetime(2026, 11, 2, 19, 0)
    weekend = cli.datetime(2026, 11, 7, 10, 0)

    assert cli._working_time_block_reason(config, calendar, after_hours)
    assert cli._working_time_block_reason(config, calendar, weekend)

    config.allow_outside_working_hours = True
    assert cli._working_time_block_reason(config, calendar, after_hours) is None
    assert cli._working_time_block_reason(config, calendar, weekend) is None


def test_open_clients_recovers_from_calendar_target_crash_before_opening_onebss(
    monkeypatch,
):
    calls = []

    class FakeSession:
        async def restart_context(self):
            calls.append("restart")

    class FakeOneBSS:
        page = None

        def __init__(self, _session):
            pass

        async def open(self):
            calls.append("onebss.open")

        async def session_is_logged_in(self):
            return True

        async def ensure_unassigned_filters(self):
            calls.append("onebss.filters")

        async def refresh_tickets(self):
            calls.append("onebss.refresh")

    class FakeSheet:
        page = None

        def __init__(self, _session):
            pass

        async def open(self):
            calls.append("sheet.open")

    async def fail_calendar_sync(*_args):
        raise RuntimeError("Target crashed")

    async def no_pending(*_args):
        calls.append("sync_pending")

    monkeypatch.setattr(cli, "OneBSSClient", FakeOneBSS)
    monkeypatch.setattr(cli, "GoogleSheetClient", FakeSheet)
    monkeypatch.setattr(cli, "sync_attendance_calendar", fail_calendar_sync)
    monkeypatch.setattr(cli, "sync_pending", no_pending)

    config = SimpleNamespace(attendance_calendar_file="attendance.json")
    onebss, _sheet = asyncio.run(
        cli.open_clients(
            config, FakeSession(), object(), object(),
        )
    )

    assert calls == [
        "sheet.open", "restart", "onebss.open", "sync_pending",
        "onebss.filters", "onebss.refresh",
    ]
    assert isinstance(onebss, FakeOneBSS)


def test_collect_plan_routes_voice_xu_ly_away_from_old_supervision_owner(
    monkeypatch,
):
    voice_xu_ly = ServiceRule(
        11, "Voice Brandname", Decimal("20"),
        'Cột VIP xử lý = "Xu ly"',
        (
            Member("Đỗ Thị Thu Trang", "Nhóm 2", "Chính", Decimal("0.34")),
            Member("Ngô Thùy Trang", "Nhóm 2", "Chính", Decimal("0.33")),
            Member("Nguyễn Thị Thu Trang", "Nhóm 2", "Chính", Decimal("0.33")),
        ),
        "Voice Brandname",
    )
    voice_giam_sat = ServiceRule(
        10, "Voice Brandname", Decimal("20"),
        'Cột VIP xử lý = "Giam sat"',
        (Member("Lê Đức Tuấn", "Nhóm 2", "Chính"),), "Voice Brandname",
    )
    other_group = ServiceRule(
        5, "Fiber", Decimal("17"), "",
        (Member("Lê Đức Tuấn", "Nhóm 1", "Chính"),),
    )
    monkeypatch.setattr(
        cli, "load_rules", lambda _path: [voice_xu_ly, voice_giam_sat, other_group]
    )
    monkeypatch.setattr(cli, "load_project_rules", lambda _path: [])

    ticket = Ticket(
        "TT7/GH/261016/05543", "001541392CNTT", "Voice Brandname",
        vip_status="Xu ly", customer_name="Khách hàng A",
        labor_address="Bãi đỗ xe Liên Ninh",
    )

    class Client:
        async def visible_tickets(self, *, refresh):
            assert refresh is True
            return [ticket]

        async def enrich_ticket_details(self, tickets, _projects, _rules):
            return tickets

    class FakeLedger:
        def pending_project_routes(self):
            return {}

        def manual_overrides(self):
            return {}

        def service_assignments(self, _year, _month):
            return {}

        def cohort_assignees(self):
            return {}

    config = SimpleNamespace(
        rules_file="rules.xlsx", project_rules_file="projects.toml",
        excluded_members=(), use_backup_members=False,
        member_target_ratios={}, timezone="Asia/Ho_Chi_Minh",
    )

    assignments, skipped, _ = asyncio.run(cli.collect_plan(
        config, Client(), FakeLedger(),
        sheet_assignees={
            "tt7/gh/261016/05543|001541392cntt": ("Lê Đức Tuấn",),
        },
        balance_scores={},
    ))

    assert skipped == []
    assert len(assignments) == 1
    assert assignments[0].assignees[0] in {
        "Đỗ Thị Thu Trang", "Ngô Thùy Trang", "Nguyễn Thị Thu Trang",
    }
    assert assignments[0].sheet_existing is True
    assert assignments[0].sheet_reassignment is False


def test_collect_plan_skips_voice_ticket_with_unknown_vip_even_with_sheet_history(
    monkeypatch,
):
    rules = [
        ServiceRule(
            10, "Voice Brandname", Decimal("20"),
            'Cột VIP xử lý = "Giam sat"',
            (Member("Lê Đức Tuấn", "Nhóm 2", "Chính"),),
            "Voice Brandname",
        ),
        ServiceRule(
            11, "Voice Brandname", Decimal("20"),
            'Cột VIP xử lý = "Xu ly"',
            (Member("Đỗ Thị Thu Trang", "Nhóm 2", "Chính"),),
            "Voice Brandname",
        ),
    ]
    monkeypatch.setattr(cli, "load_rules", lambda _path: rules)
    monkeypatch.setattr(cli, "load_project_rules", lambda _path: [])

    ticket = Ticket(
        "GD-VOICE-1", "TB-VOICE-1", "Voice Brandname", vip_status="",
    )

    class Client:
        async def visible_tickets(self, *, refresh):
            return [ticket]

        async def enrich_ticket_details(self, tickets, _projects, _rules):
            return tickets

    class FakeLedger:
        def pending_project_routes(self):
            return {}

        def manual_overrides(self):
            return {}

        def service_assignments(self, _year, _month):
            return {}

        def cohort_assignees(self):
            return {}

    config = SimpleNamespace(
        rules_file="rules.xlsx", project_rules_file="projects.toml",
        excluded_members=(), use_backup_members=False,
        member_target_ratios={}, timezone="Asia/Ho_Chi_Minh",
    )

    assignments, skipped, _ = asyncio.run(cli.collect_plan(
        config, Client(), FakeLedger(),
        sheet_assignees={"gd-voice-1|tb-voice-1": ("Đỗ Thị Thu Trang",)},
        balance_scores={},
    ))

    assert assignments == []
    assert len(skipped) == 1
    assert skipped[0][0] == ticket
    assert "không xác định được VIP xử lý" in skipped[0][1]


def test_collect_plan_manual_override_routes_voice_ticket_with_unknown_vip(
    monkeypatch,
):
    rules = [
        ServiceRule(
            10, "Voice Brandname", Decimal("20"),
            'Cột VIP xử lý = "Giam sat"',
            (Member("Lê Đức Tuấn", "Nhóm 2", "Chính"),),
            "Voice Brandname",
        ),
        ServiceRule(
            11, "Voice Brandname", Decimal("20"),
            'Cột VIP xử lý = "Xu ly"',
            (Member("Đỗ Thị Thu Trang", "Nhóm 2", "Chính"),),
            "Voice Brandname",
        ),
    ]
    monkeypatch.setattr(cli, "load_rules", lambda _path: rules)
    monkeypatch.setattr(cli, "load_project_rules", lambda _path: [])
    ticket = Ticket("GD-MANUAL", "TB-MANUAL", "Voice Brandname")

    class Client:
        async def visible_tickets(self, *, refresh):
            return [ticket]

        async def enrich_ticket_details(self, tickets, _projects, _rules):
            return tickets

    class FakeLedger:
        def pending_project_routes(self):
            return {}

        def manual_overrides(self):
            return {ticket.key: "Lê Đức Tuấn"}

        def service_assignments(self, _year, _month):
            return {}

        def cohort_assignees(self):
            return {}

    config = SimpleNamespace(
        rules_file="rules.xlsx", project_rules_file="projects.toml",
        excluded_members=(), use_backup_members=False,
        member_target_ratios={}, timezone="Asia/Ho_Chi_Minh",
    )

    assignments, skipped, _ = asyncio.run(cli.collect_plan(
        config, Client(), FakeLedger(), balance_scores={},
    ))

    assert skipped == []
    assert len(assignments) == 1
    assert assignments[0].assignees == ("Lê Đức Tuấn",)
    assert assignments[0].manual_override is True


def test_unique_tickets_keeps_only_one_visible_duplicate_per_cycle():
    tickets = [
        Ticket("GD1", "TB1", "Leasedline GE"),
        Ticket("GD1", "TB1", "Leasedline GE"),
        Ticket("GD2", "TB2", "Fiber"),
    ]
    assert [ticket.key for ticket in cli._unique_tickets(tickets)] == [
        "GD1|TB1", "GD2|TB2",
    ]


def test_large_queue_read_window_starts_only_above_threshold():
    config = SimpleNamespace(read_batch_size=20, read_batch_threshold=50)
    tickets = [Ticket(f"GD{i}", f"TB{i}", "Fiber") for i in range(51)]

    assert cli._queue_read_window_size(config, tickets[:50]) is None
    assert cli._queue_read_window_size(config, tickets) == 20


def test_read_windows_without_threshold_remain_always_enabled():
    config = SimpleNamespace(read_batch_size=20, read_batch_threshold=None)

    assert cli._queue_read_window_size(config, []) == 20


@pytest.mark.parametrize(
    ("queue_size", "expected_plan_sizes", "expected_assign_sizes"),
    [
        (50, [50, 30, 10, 0], [20, 20, 10]),
        (51, [20, 20, 11], [20, 20, 11]),
    ],
)
def test_process_available_uses_twenty_ticket_read_windows_only_above_fifty(
    monkeypatch, tmp_path, queue_size, expected_plan_sizes, expected_assign_sizes,
):
    tickets = [Ticket(f"GD{i}", f"TB{i}", "Fiber") for i in range(queue_size)]
    plan_sizes = []
    assign_sizes = []

    class OneBSS:
        def __init__(self):
            self.tickets = list(tickets)

        async def visible_tickets(self, *, refresh):
            assert refresh is False
            return list(self.tickets)

        async def refresh_tickets(self):
            pass

        async def assign(self, assignments):
            assign_sizes.append(len(assignments))
            keys = {item.ticket.key for item in assignments}
            self.tickets = [ticket for ticket in self.tickets if ticket.key not in keys]

    class Ledger:
        def stage(self, assignment):
            return assignment

        def mark(self, *_args):
            pass

        def manual_overrides(self):
            return {}

    async def fake_collect_plan(
        _config, client, _ledger, *, completed_keys, ticket_snapshot=None,
        visible_snapshot=None, **_kwargs,
    ):
        selected = (
            ticket_snapshot if ticket_snapshot is not None
            else await client.visible_tickets(refresh=False)
        )
        visible = visible_snapshot if visible_snapshot is not None else selected
        plan_sizes.append(len(selected))
        assignments = [
            cli.Assignment(ticket, ("Người A",), Decimal(1), 1)
            for ticket in selected if ticket.key not in completed_keys
        ]
        return assignments, [], cli._ticket_counts(visible)

    monkeypatch.setattr(cli, "collect_plan", fake_collect_plan)
    monkeypatch.setattr(cli, "attendance_adjusted_config", lambda value: value)
    monkeypatch.setattr(cli, "sync_pending", AsyncMock())
    config = SimpleNamespace(
        excluded_members=(), attendance_calendar_file=None,
        read_batch_size=20, read_batch_threshold=50, batch_size=20,
        preview_csv=tmp_path / "preview_assignments.csv", error_retry_seconds=1,
    )
    onebss = OneBSS()
    dashboard = SimpleNamespace(enabled=False)

    completed, skipped = asyncio.run(cli.process_available(
        config, onebss, object(), Ledger(), dashboard, set(), {}, {},
    ))

    assert completed == queue_size
    assert skipped == 0
    assert plan_sizes == expected_plan_sizes
    assert assign_sizes == expected_assign_sizes


def test_api_outbox_uses_mapped_service_instead_of_unstable_rule_row(monkeypatch):
    sent = []
    marked = []

    class FakeLedger:
        def pending_ingest_rows(self):
            return [
                {"ticket_key": "old-voice", "service": "Voice Brandname", "rule_row": 15},
                {"ticket_key": "fiber", "service": "Fiber", "rule_row": 7},
            ]

        def mark_ingest_keys(self, keys):
            marked.extend(keys)

        def pending_sheet_rows(self):
            return []

        def pending_dashboard_rows(self):
            return []

    class FakeIngest:
        enabled = True

        async def push_pending(self, rows):
            sent.extend(rows)
            return {"invalid": []}

    rules = [
        # Voice Brandname had an old queue row with this number; the number
        # now belongs to an API-enabled rule, but this service is opted out.
        type("Rule", (), {
            "row_number": 10, "service": "Voice Brandname",
            "sheet_service": "Voice Brandname", "send_to_api": False,
        })(),
        type("Rule", (), {
            "row_number": 7, "service": "Fiber", "sheet_service": "Fiber",
            "send_to_api": True,
        })(),
    ]
    monkeypatch.setattr(cli, "load_rules", lambda _path: rules)
    monkeypatch.setattr(cli.IngestApiClient, "from_environment", lambda: FakeIngest())
    config = SimpleNamespace(rules_file="rules.xlsx")

    asyncio.run(cli.sync_pending(config, object(), FakeLedger(), SimpleNamespace(enabled=False)))

    assert [row["ticket_key"] for row in sent] == ["fiber"]
    assert marked == ["fiber"]


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
    async def open_sheet():
        calls.append("open-sheet")

    sheet = SimpleNamespace(page=object(), open=AsyncMock(side_effect=open_sheet))
    monkeypatch.setattr(cli, "sync_pending", AsyncMock())
    config = SimpleNamespace(timeout_ms=30000)

    asyncio.run(cli.recover_browser(
        config, FakeSession(), onebss, sheet, object(), object()
    ))

    assert calls == ["restart", "open-sheet", "open-onebss"]
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
        attendance_calendar_file=None,
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

        async def session_is_logged_in(self):
            return True

        async def refresh_tickets(self):
            self.refreshes += 1

        async def reload_for_recovery(self):
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


def test_watch_recovers_calendar_target_crash_and_continues_cycle(tmp_path, monkeypatch):
    config = SimpleNamespace(
        database=tmp_path / "ledger.db",
        poll_interval_minutes=15,
        error_retry_seconds=3,
        timezone="Asia/Ho_Chi_Minh",
        attendance_calendar_file=tmp_path / "attendance.json",
    )
    recovered = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeOneBSS:
        async def session_is_logged_in(self):
            return True

        async def refresh_tickets(self):
            return None

    async def fake_open_clients(*_args):
        return FakeOneBSS(), object()

    async def fail_calendar_sync(*_args):
        raise RuntimeError("Target crashed")

    async def fake_recover(*_args):
        recovered.append(True)

    async def fake_load_sheet_source(*_args):
        return {}, {}

    async def cancel_after_cycle(*_args):
        raise asyncio.CancelledError

    monkeypatch.setattr(cli, "BrowserSession", lambda _config: FakeSession())
    monkeypatch.setattr(cli, "open_clients", fake_open_clients)
    monkeypatch.setattr(cli, "sync_attendance_calendar", fail_calendar_sync)
    monkeypatch.setattr(cli, "recover_browser", fake_recover)
    monkeypatch.setattr(cli, "load_sheet_source", fake_load_sheet_source)
    monkeypatch.setattr(cli, "process_available", cancel_after_cycle)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cli.command_watch(config))

    assert recovered == [True]


def test_automatic_login_stops_after_three_attempts(monkeypatch):
    attempts = []
    delays = []

    class FakeOneBSS:
        page = None

        async def login_with_otp(self, username, password, _otp_provider):
            assert (username, password) == ("operator", "secret")
            attempts.append(1)
            raise RuntimeError("login failed")

        async def open(self):
            return None

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(cli.asyncio, "sleep", fake_sleep)
    with pytest.raises(RuntimeError, match="sau 3 lần"):
        asyncio.run(
            cli.authenticate_with_retries(
                FakeOneBSS(), "operator", "secret", cli.TelegramNotifier()
            )
        )

    assert len(attempts) == 3
    assert delays == [3, 10]


def test_login_command_uses_saved_credentials_and_telegram_otp(monkeypatch, capsys):
    events = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeOneBSS:
        async def open(self):
            events.append("onebss-open")
            return None

        async def session_is_logged_in(self):
            return False

        async def session_expiry_epoch(self):
            return 1_800_000_000

        async def wait_until_logged_in(self):
            pytest.fail("should use saved credentials when Telegram is enabled")

    class FakeSheet:
        async def open(self):
            events.append("sheet-open")
            return None

    notifier = cli.TelegramNotifier(token="TOKEN", chat_id="123")
    monkeypatch.setattr(cli, "BrowserSession", lambda _config: FakeSession())
    monkeypatch.setattr(cli, "OneBSSClient", lambda _session: FakeOneBSS())
    monkeypatch.setattr(cli, "GoogleSheetClient", lambda _session: FakeSheet())
    monkeypatch.setattr(cli, "load_credentials", lambda _region: ("user", "pass"))
    monkeypatch.setattr(
        cli.TelegramNotifier, "from_environment", classmethod(lambda _cls: notifier)
    )

    async def fake_authenticate(_onebss, username, password, _telegram):
        events.append((username, password))

    monkeypatch.setattr(cli, "authenticate_with_retries", fake_authenticate)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    asyncio.run(cli.command_login(SimpleNamespace()))

    assert events == ["sheet-open", "onebss-open", ("user", "pass")]
    assert "ATS_ONEBSS_LOGIN_READY=1" in capsys.readouterr().out
