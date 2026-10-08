from __future__ import annotations

import argparse
import asyncio
import csv
import os
import shutil
from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .browser import BrowserSession, GoogleSheetClient, OneBSSClient
from .config import Config, load_config
from .credentials import load_credentials
from .dashboard import DashboardClient
from .ledger import Ledger
from .models import Assignment, Ticket
from .planner import plan_assignments
from .regions import RegionError, load_regions
from .rules import (
    RuleError,
    canonical_member_name,
    eligible_members,
    load_project_rules,
    load_rules,
    load_score_member_names,
    match_project_rule,
    match_rule,
    member_group,
    voice_brandname_mode,
)
from .text import normalize, ticket_identity, unaccent
from .telegram import TelegramNotifier, wait_for_otp
from .ingest import IngestApiClient


async def notify_telegram_error(
    notifier: TelegramNotifier, context: str, error: BaseException | str
) -> None:
    """Send a best-effort alert without interrupting ticket recovery."""
    if not notifier.enabled:
        return
    try:
        sent = await asyncio.to_thread(notifier.notify_error, context, error)
        if sent:
            print("Đã gửi cảnh báo lỗi qua Telegram.")
    except Exception as telegram_error:
        print(f"Không gửi được cảnh báo Telegram: {telegram_error}")


async def monitor_onebss_session(
    client: OneBSSClient,
    notifier: TelegramNotifier,
    state: dict[str, object],
) -> None:
    """Publish token expiry for the UI and send one alert per expiry event."""
    try:
        expiry = await client.session_expiry_epoch()
    except Exception:
        expiry = None
    previous = state.get("expiry", object())
    if expiry != previous:
        print(
            "ATS_ONEBSS_SESSION_EXPIRY="
            + (str(expiry) if expiry is not None else "unknown"),
            flush=True,
        )
        state["expiry"] = expiry

    if expiry is not None:
        state["logged_out_checks"] = 0
        now = int(datetime.now().timestamp())
        if 0 < expiry - now <= 15 * 60 and state.get("warned_expiry") != expiry:
            state["warned_expiry"] = expiry
            if notifier.enabled:
                try:
                    await asyncio.to_thread(notifier.notify_session_expiring, expiry)
                    print("Đã gửi cảnh báo phiên OneBSS sắp hết hạn qua Telegram.")
                except Exception as error:
                    print(f"Không gửi được cảnh báo sắp hết phiên: {error}")
        if expiry <= now and state.get("expired_expiry") != expiry:
            state["expired_expiry"] = expiry
            print("ATS_ONEBSS_SESSION_STATE=expired", flush=True)
            if notifier.enabled:
                try:
                    await asyncio.to_thread(notifier.notify_session_expired, expiry)
                    print("Đã gửi cảnh báo phiên OneBSS hết hạn qua Telegram.")
                except Exception as error:
                    print(f"Không gửi được cảnh báo hết phiên: {error}")
        return

    logged_in = await client.session_is_logged_in()
    if logged_in:
        state["logged_out_checks"] = 0
        return
    state["logged_out_checks"] = int(state.get("logged_out_checks", 0)) + 1
    if state["logged_out_checks"] < 2 or state.get("expired_without_token"):
        return
    state["expired_without_token"] = True
    print("ATS_ONEBSS_SESSION_STATE=expired", flush=True)
    if notifier.enabled:
        try:
            await asyncio.to_thread(notifier.notify_session_expired, None)
            print("Đã gửi cảnh báo phiên OneBSS bị ngắt qua Telegram.")
        except Exception as error:
            print(f"Không gửi được cảnh báo hết phiên: {error}")


def _unique_tickets(tickets: list[Ticket]) -> list[Ticket]:
    """Keep one visible row per transaction/subscriber pair in each batch."""
    result: list[Ticket] = []
    seen: set[str] = set()
    for ticket in tickets:
        if ticket.key in seen:
            continue
        seen.add(ticket.key)
        result.append(ticket)
    return result


def _ticket_counts(tickets: list[Ticket]) -> dict[str, int]:
    """Count visible occurrences, including identical OneBSS rows."""
    return dict(Counter(ticket.key for ticket in tickets))


def _release_completed_occurrences(
    processed: set[str], before: dict[str, int], after: dict[str, int]
) -> None:
    """Allow the next duplicate only after OneBSS removed the saved row."""
    for key, previous_count in before.items():
        if after.get(key, 0) < previous_count:
            processed.discard(key)


def write_preview(path: Path, assignments: list[Assignment]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "Mã giao dịch", "Mã thuê bao", "Dịch vụ OneBSS", "Dịch vụ Google Sheet", "Người nhận",
            "Điểm phiếu", "Điểm/người", "Dòng quy tắc",
            "Trạng thái Google Sheet", "Dự án", "Tên thuê bao", "Tên KH",
            "Địa chỉ LĐ", "Địa chỉ KN", "Tỉnh LĐ",
        ])
        for item in assignments:
            writer.writerow([
                item.ticket.transaction_id,
                item.ticket.subscriber_id,
                item.ticket.service_type or item.ticket.service,
                item.sheet_service or item.ticket.service_type or item.ticket.service,
                ", ".join(item.assignees),
                item.points,
                item.points_per_person,
                item.rule_row,
                (
                    "Theo quy tắc - không ghi Google Sheet"
                    if not item.write_to_sheet
                    else "Đã có - ghi dòng mới, đánh dấu Giao lại"
                    if item.sheet_reassignment
                    else "Đã có - ghi dòng mới, không đánh dấu Giao lại"
                    if item.sheet_existing
                    else "Phiếu mới - sẽ ghi"
                ),
                item.project_name,
                item.ticket.subscriber_name,
                item.ticket.customer_name,
                item.ticket.labor_address,
                item.ticket.connection_address,
                item.ticket.labor_province,
            ])


def write_skipped(path: Path, skipped: list[tuple[object, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "Mã giao dịch", "Mã thuê bao", "Dịch vụ", "Loại HĐ",
            "Tên thuê bao", "Tên KH", "Địa chỉ LĐ", "Địa chỉ KN", "Tỉnh LĐ",
            "Lý do",
        ])
        for ticket, reason in skipped:
            writer.writerow([
                ticket.transaction_id,
                ticket.subscriber_id,
                ticket.service_type or ticket.service,
                ticket.contract_type,
                ticket.subscriber_name,
                ticket.customer_name,
                ticket.labor_address,
                ticket.connection_address,
                ticket.labor_province,
                reason,
            ])


async def collect_plan(
    config: Config, client: OneBSSClient, ledger: Ledger, refresh: bool = True,
    completed_keys: set[str] | None = None,
    sheet_assignees: dict[str, tuple[str, ...]] | None = None,
    balance_scores: dict | None = None,
) -> tuple[list[Assignment], list[tuple[object, str]], dict[str, int]]:
    rules = load_rules(config.rules_file)
    project_rules = load_project_rules(config.project_rules_file)
    excluded_members = tuple(
        canonical_member_name(rules, name)
        for name in getattr(config, "excluded_members", ())
    )
    excluded = {normalize(name) for name in excluded_members}
    tickets = await client.visible_tickets(refresh=refresh)
    visible_counts = _ticket_counts(tickets)
    # OneBSS can show multiple identical rows. Selecting them by the same
    # transaction/subscriber locator makes Syncfusion select a different count
    # than the planned batch. Process one occurrence per refresh; if OneBSS
    # removes it successfully, the next identical occurrence is handled in
    # this same automatic cycle.
    tickets = _unique_tickets(tickets)
    pending_projects = ledger.pending_project_routes()
    tickets = [
        replace(
            ticket,
            customer_name=(
                ticket.customer_name
                or pending_projects.get(ticket.key, {}).get("customer_name", "")
            ),
            subscriber_name=(
                ticket.subscriber_name
                or pending_projects.get(ticket.key, {}).get("subscriber_name", "")
            ),
            labor_address=(
                ticket.labor_address
                or pending_projects.get(ticket.key, {}).get("labor_address", "")
            ),
            labor_province=(
                ticket.labor_province
                or pending_projects.get(ticket.key, {}).get("labor_province", "")
            ),
        )
        for ticket in tickets
    ]
    tickets = await client.enrich_ticket_details(tickets, project_rules)
    valid = []
    skipped: list[tuple[object, str]] = []
    manual_assignees: dict[str, str] = {}
    sheet_assignees = sheet_assignees or {}
    configured_names = {
        normalize(member.name): member.name
        for rule in rules for member in rule.members
    }
    for project in project_rules:
        for name in (project.fixed_assignee, *(route.assignee for route in project.routes)):
            if name:
                configured_names.setdefault(normalize(name), name)
    configured_unaccented: dict[str, list[str]] = {}
    for name in configured_names.values():
        configured_unaccented.setdefault(unaccent(name), []).append(name)
    pending_overrides = ledger.manual_overrides()
    customer_rules = any(
        "customer_name" in project.match_fields for project in project_rules
    )
    for ticket in tickets:
        try:
            requested_assignee = pending_overrides.get(ticket.key)
            if requested_assignee:
                canonical_assignee = configured_names.get(normalize(requested_assignee))
                if canonical_assignee is None:
                    matches = configured_unaccented.get(unaccent(requested_assignee), [])
                    canonical_assignee = matches[0] if len(matches) == 1 else None
                if canonical_assignee is None:
                    raise RuleError(
                        f"Người được chỉ định ({requested_assignee}) không còn trong cấu hình nhân sự"
                    )
                manual_assignees[ticket.key] = canonical_assignee
                valid.append(ticket)
                continue
            if customer_rules and not ticket.customer_name:
                raise RuleError(
                    "Không đọc được Tên KH để kiểm tra phiếu dự án; "
                    "giữ phiếu chưa giao và sẽ thử đọc lại ở chu kỳ sau."
                )
            prior_assignees = sheet_assignees.get(
                ticket_identity(ticket.transaction_id, ticket.subscriber_id)
            )
            try:
                current_rule = match_rule(rules, ticket)
            except RuleError:
                current_rule = None
            if current_rule is not None and not current_rule.write_to_sheet:
                # Ignore historical rows written before this policy was
                # implemented; this service follows Excel, not Sheet history.
                prior_assignees = None
            if prior_assignees:
                try:
                    rule = match_rule(rules, ticket)
                except RuleError:
                    # Historical services may no longer exist in the current
                    # Excel file, but their Sheet assignee remains authoritative.
                    for assignee in prior_assignees:
                        canonical = canonical_member_name(rules, assignee)
                        if normalize(canonical) in excluded:
                            raise RuleError(
                                f"Người nhận theo Google Sheet ({canonical}) "
                                "đang nghỉ phép"
                            )
                        member_group(rules, canonical)
                else:
                    if voice_brandname_mode(rule, ticket) == "giam_sat":
                        # Validate the forced current-state route, not the stale
                        # assignee recorded when the ticket was first handled.
                        eligible_members(
                            rule, ticket, config.use_backup_members,
                            excluded_members,
                        )
                    else:
                        for assignee in prior_assignees:
                            canonical = canonical_member_name(rules, assignee)
                            if normalize(canonical) in excluded:
                                raise RuleError(
                                    f"Người nhận theo Google Sheet ({canonical}) "
                                    "đang nghỉ phép"
                                )
                            member_group(rules, canonical, rule)
            else:
                rule = match_rule(rules, ticket)
                voice_mode = voice_brandname_mode(rule, ticket)
                if voice_mode or not rule.write_to_sheet:
                    eligible_members(
                        rule, ticket, config.use_backup_members,
                        excluded_members,
                    )
                else:
                    project = match_project_rule(project_rules, ticket)
                    if project:
                        if normalize(project.assignee) in excluded:
                            ledger.remember_pending_project(
                                ticket, project.project_name, project.assignee
                            )
                            raise RuleError(
                                f"{project.project_name}: người phụ trách "
                                f"{project.assignee} đang nghỉ phép"
                            )
                        member_group(rules, project.assignee, rule)
                    else:
                        eligible_members(
                            rule, ticket, config.use_backup_members,
                            excluded_members,
                        )
            valid.append(ticket)
        except RuleError as error:
            skipped.append((ticket, str(error)))
    now = datetime.now(ZoneInfo(config.timezone))
    cohort_conflicts: dict[str, str] = {}
    assignments = plan_assignments(
        valid,
        rules,
        balance_scores if balance_scores is not None else ledger.scores(now.year, now.month),
        completed_keys or set(),
        config.use_backup_members,
        project_rules,
        sheet_assignees,
        getattr(sheet_assignees, "current_keys", None),
        config.member_target_ratios,
        excluded_members,
        ledger.cohort_assignees(),
        cohort_conflicts,
        manual_assignees,
    )
    for ticket in valid:
        pinned = cohort_conflicts.get(ticket.key)
        if pinned:
            skipped.append((
                ticket,
                f"Phiếu cùng Tên KH, Địa chỉ LĐ và dịch vụ đang được giao cho "
                f"{pinned}, nhưng nhân sự này không thuộc nhóm đủ điều kiện "
                "của quy tắc phiếu hiện tại.",
            ))
    return assignments, skipped, visible_counts


async def command_login(config: Config) -> None:
    async with BrowserSession(config) as session:
        onebss = OneBSSClient(session)
        sheet = GoogleSheetClient(session)
        await sheet.open()
        await onebss.open()
        if not await onebss.session_is_logged_in():
            region_key = os.environ.get("ATS_ONEBSS_REGION_KEY", "north")
            username, password = load_credentials(region_key)
            telegram = TelegramNotifier.from_environment()
            if username and password and telegram.enabled:
                await authenticate_with_retries(
                    onebss, username, password, telegram
                )
            else:
                await onebss.wait_until_logged_in()
        expiry = await onebss.session_expiry_epoch()
        print(
            "ATS_ONEBSS_SESSION_EXPIRY="
            + (str(expiry) if expiry is not None else "unknown"),
            flush=True,
        )
        print("ATS_ONEBSS_LOGIN_READY=1", flush=True)
        await asyncio.to_thread(
            input,
            "Đã nhận diện OneBSS và Google Sheets. Nhấn Enter để lưu phiên và đóng Chromium: ",
        )


async def command_plan(config: Config) -> None:
    ledger = Ledger(config.database)
    async with BrowserSession(config) as session:
        onebss = OneBSSClient(session)
        sheet = GoogleSheetClient(session)
        await sheet.open()
        await onebss.open()
        await onebss.wait_until_logged_in()
        sheet_assignees, balance_scores = await load_sheet_source(
            config, sheet, ledger
        )
        assignments, skipped, _visible_keys = await collect_plan(
            config, onebss, ledger,
            sheet_assignees=sheet_assignees,
            balance_scores=balance_scores,
        )
        write_preview(config.preview_csv, assignments)
        write_skipped(config.preview_csv.with_name("preview_skipped.csv"), skipped)
        print(f"Đã lập kế hoạch {len(assignments)} phiếu: {config.preview_csv}")
        if skipped:
            print(f"Bỏ qua {len(skipped)} phiếu thiếu quy tắc: {config.preview_csv.with_name('preview_skipped.csv')}")


async def sync_pending(
    config: Config, sheet: GoogleSheetClient, ledger: Ledger,
    dashboard: DashboardClient,
) -> None:
    ingest = IngestApiClient.from_environment()
    pending_api_rows = ledger.pending_ingest_rows()
    # Rule row numbers are not stable identifiers: in Group DV workbooks they
    # point to the parent group row and can change when the workbook is edited.
    # A queued row stores the Google-Sheet/API service name, so use that stable
    # value to revalidate the API opt-in against the currently active workbook.
    api_flags_by_service: dict[str, list[bool]] = {}
    for rule in load_rules(config.rules_file):
        service_name = normalize(rule.sheet_service or rule.service)
        api_flags_by_service.setdefault(service_name, []).append(rule.send_to_api)
    api_enabled_services = {
        service for service, flags in api_flags_by_service.items()
        if flags and all(flags)
    }
    ingest_pending = [
        row for row in pending_api_rows
        if normalize(row["service"]) in api_enabled_services
    ]
    disabled_api_count = len(pending_api_rows) - len(ingest_pending)
    if disabled_api_count:
        print(
            f"Bỏ qua {disabled_api_count} phiếu chờ API vì cột Gửi API trong Excel "
            "đang để trống/Không."
        )
    if ingest.enabled and ingest_pending:
        try:
            result = await ingest.push_pending(ingest_pending)
            invalid_indices = {
                item.get("index") for item in result.get("invalid", [])
                if isinstance(item, dict)
            }
            valid_keys = {
                row["ticket_key"] for index, row in enumerate(ingest_pending)
                if index not in invalid_indices
            }
            ledger.mark_ingest_keys(valid_keys)
            if valid_keys:
                print(f"Đã đồng bộ {len(valid_keys)} phiếu lên API nhận phiếu.")
            for item in result.get("invalid", []):
                if isinstance(item, dict):
                    print(
                        "API chưa nhận dòng "
                        f"{item.get('index', '?')}: {item.get('reason', 'không hợp lệ')}"
                    )
        except Exception as error:
            # API is an independent sink; keep its durable outbox for retry and
            # do not turn a successful OneBSS/Sheet assignment into a failure.
            print(f"Chưa đồng bộ được API nhận phiếu; sẽ thử lại: {error}")

    pending = ledger.pending_sheet_rows()
    if pending:
        await sheet.append_records(pending)
        ledger.mark_sheet_keys({row["ticket_key"] for row in pending})
        print(f"Đã đồng bộ {len(pending)} dòng còn thiếu lên Google Sheet.")
    dashboard_pending = ledger.pending_dashboard_rows()
    if dashboard.enabled and dashboard_pending:
        try:
            await dashboard.upsert_assignments(dashboard_pending)
            ledger.mark_dashboard_keys({row["ledger_key"] for row in dashboard_pending})
            print(f"Đã đồng bộ {len(dashboard_pending)} dòng lên Dashboard Giao phiếu.")
        except Exception as error:
            print(
                "Chưa đồng bộ được Dashboard; dữ liệu đã được giữ trong hàng "
                f"đợi cục bộ: {error}"
            )


async def load_sheet_source(
    config: Config, sheet: GoogleSheetClient, ledger: Ledger,
) -> tuple[dict[str, tuple[str, ...]], dict]:
    """Load authoritative routing and monthly scores from Google Sheet."""
    assignees, scores = await sheet.assignment_source(
        load_score_member_names(config.rules_file)
    )
    if scores:
        return assignees, scores
    now = datetime.now(ZoneInfo(config.timezone))
    print(
        "Không đọc được hàng Điểm quy đổi trên Google Sheet; "
        "tạm dùng điểm SQLite cho chu kỳ này."
    )
    return assignees, ledger.scores(now.year, now.month)


async def process_available(
    config: Config,
    onebss: OneBSSClient,
    sheet: GoogleSheetClient,
    ledger: Ledger,
    dashboard: DashboardClient,
    processed: set[str],
    sheet_assignees: dict[str, tuple[str, ...]],
    balance_scores: dict,
) -> tuple[int, int]:
    """Process the current queue until no eligible, unprocessed ticket remains."""
    completed = 0
    while True:
        assignments, skipped, visible_counts = await collect_plan(
            config, onebss, ledger, refresh=False, completed_keys=processed,
            sheet_assignees=sheet_assignees,
            balance_scores=balance_scores,
        )
        # Permit a new occurrence only after the ticket disappeared from a
        # refreshed snapshot. A still-visible row is not assigned repeatedly.
        processed.intersection_update(visible_counts)
        write_preview(config.preview_csv, assignments)
        write_skipped(config.preview_csv.with_name("preview_skipped.csv"), skipped)
        if not assignments:
            blocked = processed.intersection(visible_counts)
            if blocked:
                blocked_rows = sum(visible_counts[key] for key in blocked)
                print(
                    f"OneBSS vẫn còn hiển thị {blocked_rows} dòng vừa xử lý; "
                    f"tải lại và thử ngay sau {config.error_retry_seconds} giây..."
                )
                await asyncio.sleep(config.error_retry_seconds)
                await onebss.refresh_tickets()
                # Business rule: every row that is still returned as Chưa giao
                # is a new actionable occurrence, even if its identifiers were
                # assigned earlier (for example, a returned ticket).
                processed.difference_update(blocked)
                continue
            return completed, len(skipped)
        assignees = assignments[0].assignees
        batch = [
            item for item in assignments if item.assignees == assignees
        ][:config.batch_size]
        current_overrides = ledger.manual_overrides()
        if any(
            (item.manual_override and item.ticket.key not in current_overrides)
            or (
                item.ticket.key in current_overrides
                and (
                    not item.manual_override
                    or len(item.assignees) != 1
                    or normalize(item.assignees[0])
                    != normalize(current_overrides[item.ticket.key])
                )
            )
            for item in batch
        ):
            print("Danh sách chỉ định riêng vừa thay đổi; đang lập lại kế hoạch trước khi giao.")
            continue
        before_counts = {
            item.ticket.key: visible_counts.get(item.ticket.key, 0)
            for item in batch
        }
        batch = [ledger.stage(assignment) for assignment in batch]
        repeated = sum(item.sheet_existing for item in batch)
        marked_repeated = sum(
            item.sheet_reassignment for item in batch if item.sheet_existing
        )
        omitted = sum(not item.write_to_sheet for item in batch)
        notices = []
        if repeated:
            notices.append(
                f"{repeated} phiếu đã có trên Google Sheet, ghi dòng mới "
                f"({marked_repeated} dòng đánh dấu Giao lại ở cột K)"
            )
        if omitted:
            notices.append(f"{omitted} phiếu theo quy tắc không ghi Google Sheet")
        suffix = f" ({'; '.join(notices)})" if notices else ""
        print(f"Giao {len(batch)} phiếu cho {', '.join(assignees)}{suffix}...")
        if dashboard.enabled:
            await dashboard.heartbeat("busy")
        await onebss.assign(batch)
        for assignment in batch:
            ledger.mark(assignment, "onebss_saved")
            ledger.mark(assignment, "sms_clicked")
        completed += len(batch)
        processed.update(item.ticket.key for item in batch)
        print("Cập nhật lại danh sách phiếu chưa giao...")
        await onebss.refresh_tickets()
        remaining = await onebss.visible_tickets(refresh=False)
        _release_completed_occurrences(
            processed, before_counts, _ticket_counts(remaining)
        )
        await sync_pending(config, sheet, ledger, dashboard)
        for assignment in batch:
            if not assignment.write_to_sheet:
                continue
            identity = ticket_identity(
                assignment.ticket.transaction_id,
                assignment.ticket.subscriber_id,
            )
            sheet_assignees[identity] = assignment.assignees
            current_keys = getattr(sheet_assignees, "current_keys", None)
            if current_keys is not None:
                current_keys.add(identity)
            if not assignment.sheet_existing:
                share = assignment.points_per_person
                for assignee in assignment.assignees:
                    balance_scores[assignee] = balance_scores.get(assignee, 0) + share
        if dashboard.enabled:
            await dashboard.heartbeat()
            await process_reassignment_command(
                config, onebss, sheet, ledger, dashboard
            )


async def open_clients(
    config: Config, session: BrowserSession, ledger: Ledger,
    dashboard: DashboardClient,
    telegram: TelegramNotifier | None = None,
    region_key: str = "north",
) -> tuple[OneBSSClient, GoogleSheetClient]:
    onebss = OneBSSClient(session)
    sheet = GoogleSheetClient(session)
    await sheet.open()
    await onebss.open()
    if not await onebss.session_is_logged_in():
        username, password = load_credentials(region_key)
        if username and password and telegram and telegram.enabled:
            await authenticate_with_retries(onebss, username, password, telegram)
        else:
            await onebss.wait_until_logged_in()
    await sync_pending(config, sheet, ledger, dashboard)
    await onebss.ensure_unassigned_filters()
    await onebss.refresh_tickets()
    return onebss, sheet


async def authenticate_with_retries(
    onebss: OneBSSClient,
    username: str,
    password: str,
    telegram: TelegramNotifier,
) -> None:
    """Run one bounded login recovery sequence; never retry indefinitely."""
    delays = (3, 10)
    region_key = os.environ.get("ATS_ONEBSS_REGION_KEY", "north")
    for attempt in range(1, 4):
        try:
            print(f"Đang đăng nhập lại OneBSS (lần {attempt}/3)...")

            async def obtain_otp() -> str:
                return await asyncio.to_thread(
                    wait_for_otp,
                    telegram.token,
                    telegram.chat_id,
                    telegram.region_name,
                    180,
                )

            await onebss.login_with_otp(username, password, obtain_otp)
            print("Đăng nhập lại OneBSS thành công.")
            return
        except Exception as error:
            print(f"Đăng nhập OneBSS lần {attempt}/3 chưa thành công: {error}")
            if attempt == 3:
                await notify_telegram_error(
                    telegram,
                    f"Tự đăng nhập OneBSS thất bại sau 3 lần — {region_key}",
                    error,
                )
                raise RuntimeError(
                    "Tự đăng nhập OneBSS thất bại sau 3 lần; đã dừng để tránh khóa tài khoản."
                ) from None
            await asyncio.sleep(delays[attempt - 1])
            try:
                page = onebss.page
                if page and not page.is_closed():
                    await page.goto(
                        onebss.session.config.onebss_url,
                        wait_until="domcontentloaded",
                    )
            except Exception:
                await onebss.open()


async def recover_browser(
    config: Config,
    session: BrowserSession,
    onebss: OneBSSClient,
    sheet: GoogleSheetClient,
    ledger: Ledger,
    dashboard: DashboardClient,
) -> None:
    """Reload OneBSS first, restarting the shared browser context if needed."""
    try:
        await onebss.reload_for_recovery()
        print("OneBSS đã tải lại; tiếp tục quy trình tự động.")
        return
    except Exception as reload_error:
        print(
            f"Tải lại tab OneBSS không phục hồi được ({reload_error}); "
            "đang khởi động lại Chromium..."
        )

    await session.restart_context()
    onebss.page = None
    sheet.page = None
    timeout_ms = max(15_000, config.timeout_ms)
    try:
        await sheet.open()
        await onebss.open()
        await onebss.page.wait_for_selector("#frmGiaoViecVIP", timeout=timeout_ms)
        await sync_pending(config, sheet, ledger, dashboard)
        await onebss.ensure_unassigned_filters()
        await onebss.refresh_tickets()
    except Exception as restart_error:
        raise RuntimeError(
            f"Không phục hồi được sau khi khởi động lại Chromium: {restart_error}"
        ) from restart_error
    print("Đã khởi động lại Chromium, khôi phục các tab và tải lại hàng đợi.")


async def command_run(config: Config) -> None:
    ledger = Ledger(config.database)
    dashboard = DashboardClient(config)
    async with BrowserSession(config) as session:
        onebss, sheet = await open_clients(config, session, ledger, dashboard)
        sheet_assignees, balance_scores = await load_sheet_source(
            config, sheet, ledger
        )
        completed, skipped = await process_available(
            config, onebss, sheet, ledger, dashboard, processed=set(),
            sheet_assignees=sheet_assignees,
            balance_scores=balance_scores,
        )
        print(f"Đã giao {completed} phiếu.")
        if skipped:
            print(
                f"Còn {skipped} phiếu chưa giao do thiếu hoặc không khớp quy tắc. "
                f"Xem {config.preview_csv.with_name('preview_skipped.csv')}."
            )


async def command_sync(config: Config) -> None:
    """Flush completed OneBSS work without opening or changing OneBSS."""
    ledger = Ledger(config.database)
    dashboard = DashboardClient(config)
    async with BrowserSession(config) as session:
        sheet = GoogleSheetClient(session)
        await sheet.open()
        await sync_pending(config, sheet, ledger, dashboard)
        if dashboard.enabled:
            await dashboard.heartbeat()
        print("Đã hoàn tất đồng bộ dữ liệu tồn đọng.")


async def process_reassignment_command(
    config: Config, onebss: OneBSSClient, sheet: GoogleSheetClient,
    ledger: Ledger, dashboard: DashboardClient,
) -> bool:
    command = await dashboard.claim_command()
    if not command:
        return False
    command_id = command["id"]
    requested_assignee = command["requested_assignee"]
    try:
        await dashboard.heartbeat("busy", command_id)
        rules = load_rules(config.rules_file)
        project_rules = load_project_rules(config.project_rules_file)
        allowed_names = {
            normalize(member.name)
            for rule in rules
            for member in rule.members
        }
        allowed_names.update(
            normalize(route.assignee)
            for project in project_rules
            for route in project.routes
        )
        allowed_names.update(
            normalize(project.fixed_assignee)
            for project in project_rules if project.fixed_assignee
        )
        if normalize(requested_assignee) not in allowed_names:
            raise RuntimeError(
                f"{requested_assignee} không có trong danh sách nhân viên cấu hình"
            )
        assignment = await dashboard.assignment(command["assignment_id"])
        if assignment["current_assignee"] == requested_assignee:
            await dashboard.finish_command(
                command_id, "completed", result={"already_completed": True}
            )
            await dashboard.heartbeat()
            return True
        if assignment["current_assignee"] != command["previous_assignee"]:
            raise RuntimeError(
                "Người nhận hiện tại đã thay đổi sau khi lệnh được tạo"
            )
        record = ledger.assignment_record(
            assignment["ledger_key"], assignment["original_assignee"]
        )
        if record is None:
            raise RuntimeError(
                "Máy agent không có bản ghi gốc của phiếu; không thể cập nhật Sheet an toàn"
            )
        ticket = Ticket(
            transaction_id=assignment["transaction_id"],
            subscriber_id=assignment.get("subscriber_id", ""),
            service=assignment.get("service", ""),
        )
        print(
            f"Giao lại {ticket.transaction_id} từ {command['previous_assignee']} "
            f"cho {requested_assignee}..."
        )
        await onebss.reassign(
            ticket, command["previous_assignee"], requested_assignee
        )
        await sheet.replace_assignee(record, requested_assignee)
        ledger.reassign(
            assignment["ledger_key"], assignment["original_assignee"],
            requested_assignee,
        )
        await dashboard.complete_reassignment(
            assignment["id"], requested_assignee
        )
        await dashboard.finish_command(
            command_id, "completed",
            result={
                "previous_assignee": command["previous_assignee"],
                "current_assignee": requested_assignee,
                "google_sheet_updated": True,
            },
        )
        await dashboard.heartbeat()
        await onebss.ensure_unassigned_filters()
        await onebss.refresh_tickets()
        print("Đã hoàn tất lệnh giao lại và cập nhật Google Sheet/Dashboard.")
    except Exception as error:
        retry = int(command.get("attempt_count") or 1) < 3
        await dashboard.finish_command(
            command_id, "queued" if retry else "failed", error=str(error)
        )
        await dashboard.heartbeat("error", error=str(error))
        print(
            f"Lệnh giao lại gặp lỗi: {error}. "
            + ("Sẽ thử lại ngay." if retry else "Đã dừng sau 3 lần thử.")
        )
        try:
            await onebss.ensure_unassigned_filters()
            await onebss.refresh_tickets()
        except Exception:
            await onebss.open()
    return True


async def command_watch(config: Config) -> None:
    ledger = Ledger(config.database)
    dashboard = DashboardClient(config)
    telegram = TelegramNotifier.from_environment()
    interval_seconds = config.poll_interval_minutes * 60
    command_poll_seconds = getattr(config, "dashboard_command_poll_seconds", 5)
    processed: set[str] = set()
    session_state: dict[str, object] = {}
    region_key = os.environ.get("ATS_ONEBSS_REGION_KEY", "north")
    async with BrowserSession(config) as session:
        onebss, sheet = await open_clients(
            config, session, ledger, dashboard, telegram, region_key
        )
        await monitor_onebss_session(onebss, telegram, session_state)
        if dashboard.enabled:
            await dashboard.heartbeat()
            print(
                "Đã kết nối Dashboard Giao phiếu; đang nhận lệnh giao lại "
                f"mỗi {command_poll_seconds} giây."
            )
        else:
            print(
                "Dashboard Giao phiếu chưa kết nối: "
                + (
                    dashboard.disabled_reason
                    or "hãy đặt biến môi trường ATS_ONEBSS_SUPABASE_SERVICE_KEY"
                )
                + "."
            )
        print(
            f"Đang chạy liên tục mỗi {config.poll_interval_minutes} phút. "
            "Nhấn Ctrl+C để dừng."
        )
        next_automatic_cycle = 0.0
        while True:
            await monitor_onebss_session(onebss, telegram, session_state)
            if not await onebss.session_is_logged_in():
                # Avoid reacting to a transient navigation before starting an
                # authentication attempt or consuming any Telegram OTP.
                if onebss.page and not onebss.page.is_closed():
                    await onebss.page.wait_for_timeout(1500)
                if not await onebss.session_is_logged_in():
                    username, password = load_credentials(region_key)
                    if not username or not password:
                        raise RuntimeError(
                            "Phiên OneBSS đã hết nhưng chưa lưu tài khoản/mật khẩu. "
                            "Hãy đăng nhập thủ công và lưu thông tin trong ứng dụng."
                        )
                    if not telegram.enabled:
                        raise RuntimeError(
                            "Cần bật Telegram và cấu hình Chat ID để nhận OTP đăng nhập."
                        )
                    await authenticate_with_retries(
                        onebss, username, password, telegram
                    )
                    await sheet.open()
                    await sync_pending(config, sheet, ledger, dashboard)
                    await onebss.ensure_unassigned_filters()
                    await onebss.refresh_tickets()
                    processed.clear()
            now = asyncio.get_running_loop().time()
            if now >= next_automatic_cycle:
              try:
                sheet_assignees, balance_scores = await load_sheet_source(
                    config, sheet, ledger
                )
                completed, skipped = await process_available(
                    config, onebss, sheet, ledger, dashboard, processed,
                    sheet_assignees, balance_scores,
                )
                # A returned/re-entered ticket visible in the next scheduled
                # cycle is a new occurrence and must be eligible again.
                processed.clear()
                print(f"Chu kỳ đã giao {completed} phiếu.")
                if skipped:
                    print(
                        f"Còn {skipped} phiếu chưa giao do thiếu hoặc không khớp "
                        f"quy tắc. Xem "
                        f"{config.preview_csv.with_name('preview_skipped.csv')}."
                    )
                next_automatic_cycle = (
                    asyncio.get_running_loop().time() + interval_seconds
                )
              except Exception as error:
                print(
                    f"Chu kỳ gặp lỗi: {error}. "
                    f"Sẽ phục hồi và thử lại sau {config.error_retry_seconds} giây."
                )
                await notify_telegram_error(
                    telegram, "Chu kỳ tự động gặp lỗi và đang phục hồi", error
                )
                # Do not apply the normal 15-minute interval to a failed
                # cycle. First finish any Sheet write left pending by a batch
                # that OneBSS already saved, then rebuild the unassigned list.
                await asyncio.sleep(config.error_retry_seconds)
                try:
                    await sync_pending(config, sheet, ledger, dashboard)
                except Exception as sheet_error:
                    print(f"Chưa phục hồi được Google Sheet, sẽ tiếp tục thử: {sheet_error}")
                try:
                    await recover_browser(
                        config, session, onebss, sheet, ledger, dashboard
                    )
                except Exception as recovery_error:
                    print(f"Không phục hồi được OneBSS: {recovery_error}")
                    await notify_telegram_error(
                        telegram, "Không phục hồi được phiên OneBSS", recovery_error
                    )
                next_automatic_cycle = asyncio.get_running_loop().time()
                continue
              next_run = datetime.now(ZoneInfo(config.timezone)).timestamp() + interval_seconds
              print(
                    "Lần kiểm tra tiếp theo: "
                    + datetime.fromtimestamp(next_run, ZoneInfo(config.timezone)).strftime(
                        "%d/%m/%Y %H:%M:%S"
                    )
                )
            if dashboard.enabled:
                try:
                    handled = await process_reassignment_command(
                        config, onebss, sheet, ledger, dashboard
                    )
                    if handled:
                        continue
                    await dashboard.heartbeat()
                except Exception as error:
                    print(
                        f"Chưa đọc được hàng đợi Dashboard: {error}. "
                        f"Sẽ thử lại sau {config.error_retry_seconds} giây."
                    )
                    await notify_telegram_error(
                        telegram, "Không đọc được hàng đợi Dashboard", error
                    )
                    await asyncio.sleep(config.error_retry_seconds)
                    continue
            remaining = max(
                0.1, next_automatic_cycle - asyncio.get_running_loop().time()
            )
            await asyncio.sleep(min(command_poll_seconds, remaining))
            if asyncio.get_running_loop().time() >= next_automatic_cycle:
                try:
                    await onebss.refresh_tickets()
                except Exception as error:
                    print(f"Không tải lại được OneBSS, sẽ phục hồi ngay: {error}")
                    await notify_telegram_error(
                        telegram, "Không tải lại được phiên OneBSS", error
                    )
                    try:
                        await recover_browser(
                            config, session, onebss, sheet, ledger, dashboard
                        )
                    except Exception as recovery_error:
                        print(f"Không phục hồi được OneBSS: {recovery_error}")
                        await notify_telegram_error(
                            telegram,
                            "Không phục hồi được phiên OneBSS",
                            recovery_error,
                        )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Tự động giao phiếu OneBSS")
    result.add_argument("--config", default="config.toml")
    result.add_argument(
        "--regions", default="regions.toml",
        help="Danh mục cấu hình theo miền (mặc định: regions.toml)",
    )
    result.add_argument(
        "--region", metavar="MÃ_MIỀN",
        help="Chọn cấu hình miền, ví dụ north, central hoặc south",
    )
    result.add_argument(
        "--poll-interval-minutes", type=int, metavar="PHÚT",
        help="Ghi đè chu kỳ tự động của config.toml cho lần chạy này",
    )
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Tạo config.toml từ file mẫu")
    sub.add_parser("login", help="Mở trình duyệt để đăng nhập lần đầu")
    def add_exclude_option(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument(
            "--exclude", action="append", default=[], metavar="TÊN_NHÂN_SỰ",
            help="Tạm không giao phiếu cho nhân sự này; có thể dùng nhiều lần",
        )

    plan = sub.add_parser("plan", help="Tạo CSV xem trước, không thay đổi dữ liệu")
    add_exclude_option(plan)
    run = sub.add_parser("run", help="Chạy liên tục, tự kiểm tra theo chu kỳ")
    run.add_argument("--yes", action="store_true", help="Xác nhận chạy thật")
    add_exclude_option(run)
    watch = sub.add_parser("watch", help="Chạy liên tục và kiểm tra lại theo chu kỳ")
    watch.add_argument("--yes", action="store_true", help="Xác nhận chạy thật")
    add_exclude_option(watch)
    once = sub.add_parser("once", help="Chỉ xử lý hàng đợi hiện tại rồi thoát")
    once.add_argument("--yes", action="store_true", help="Xác nhận chạy thật")
    add_exclude_option(once)
    sync = sub.add_parser(
        "sync", help="Chỉ đồng bộ dữ liệu đã giao sang Google Sheet/Dashboard"
    )
    sync.add_argument("--yes", action="store_true", help="Xác nhận ghi dữ liệu")
    return result


def main() -> None:
    args = parser().parse_args()
    config_path = Path(args.config)
    if args.command == "init":
        if config_path.exists():
            raise SystemExit(f"Đã tồn tại {config_path}")
        shutil.copyfile(Path(__file__).parents[2] / "config.example.toml", config_path)
        print(f"Đã tạo {config_path}")
        return
    if args.region:
        try:
            region = load_regions(args.regions).get(args.region)
        except RegionError as error:
            raise SystemExit(str(error)) from error
        if not region.enabled:
            raise SystemExit(
                f"{region.name} chưa có nhân sự và quy tắc nên chưa thể chạy."
            )
        config_path = region.config_path
        print(f"Đang sử dụng cấu hình {region.name}: {config_path}")
    config = load_config(config_path)
    if args.poll_interval_minutes is not None:
        if args.poll_interval_minutes < 1:
            parser().error("--poll-interval-minutes phải lớn hơn hoặc bằng 1")
        config = replace(config, poll_interval_minutes=args.poll_interval_minutes)
        print(f"Chu kỳ tự động: {config.poll_interval_minutes} phút.")
    excluded = tuple(getattr(args, "exclude", ()) or ())
    if excluded:
        try:
            rules = load_rules(config.rules_file)
            excluded = tuple(
                dict.fromkeys(canonical_member_name(rules, name) for name in excluded)
            )
        except RuleError as error:
            raise SystemExit(str(error)) from error
        config = replace(config, excluded_members=excluded)
        print("Tạm không giao phiếu cho: " + ", ".join(excluded))
    if args.command == "login":
        asyncio.run(command_login(config))
    elif args.command == "plan":
        asyncio.run(command_plan(config))
    elif args.command == "run":
        if not args.yes:
            raise SystemExit("Chạy thật yêu cầu thêm --yes. Hãy chạy lệnh plan trước.")
        try:
            asyncio.run(command_watch(config))
        except KeyboardInterrupt:
            print("Đã dừng chế độ chạy liên tục theo yêu cầu.")
    elif args.command == "watch":
        if not args.yes:
            raise SystemExit("Chạy thật yêu cầu thêm --yes. Hãy chạy lệnh plan trước.")
        try:
            asyncio.run(command_watch(config))
        except KeyboardInterrupt:
            print("Đã dừng chế độ chạy liên tục theo yêu cầu.")
    elif args.command == "once":
        if not args.yes:
            raise SystemExit("Chạy thật yêu cầu thêm --yes. Hãy chạy lệnh plan trước.")
        asyncio.run(command_run(config))
    elif args.command == "sync":
        if not args.yes:
            raise SystemExit("Đồng bộ dữ liệu yêu cầu thêm --yes.")
        asyncio.run(command_sync(config))


if __name__ == "__main__":
    main()
