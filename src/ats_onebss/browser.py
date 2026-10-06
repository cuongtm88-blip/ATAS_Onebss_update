from __future__ import annotations

import asyncio
import base64
import csv
import io
import json
import os
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from playwright.async_api import BrowserContext, Locator, Page, async_playwright

from .config import Config
from .models import Assignment, ProjectRule, Ticket
from .text import normalize, ticket_identity, unaccent


class BrowserProfileInUseError(RuntimeError):
    """The configured Chromium profile is already owned by another session."""


class OneBSSLoginError(RuntimeError):
    """OneBSS rejected or did not complete an automated login attempt."""


class BrowserSession:
    def __init__(self, config: Config):
        self.config = config
        self.playwright = None
        self.context: BrowserContext | None = None
        self._profile_lock = None

    def _acquire_profile_lock(self) -> None:
        """Hold an OS-level lock so one profile cannot be opened twice."""
        lock_path = self.config.browser_profile.parent / ".ats-onebss-profile.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                handle.write("0")
                handle.flush()
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as error:
            handle.close()
            raise BrowserProfileInUseError(
                "Phiên tự động đang sử dụng cửa sổ Chromium cho hồ sơ này. "
                "Hãy thao tác trên phiên hiện tại hoặc dừng phiên trước khi đăng nhập lại."
            ) from error
        self._profile_lock = handle

    def _release_profile_lock(self) -> None:
        handle = self._profile_lock
        self._profile_lock = None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()

    async def __aenter__(self) -> "BrowserSession":
        self.config.browser_profile.mkdir(parents=True, exist_ok=True)
        self._acquire_profile_lock()
        try:
            self.playwright = await async_playwright().start()
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=self.config.browser_profile,
                channel=self.config.browser_channel,
                headless=self.config.headless,
                viewport={"width": 1920, "height": 1000},
                ignore_default_args=["--enable-automation"],
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            )
        except Exception as error:
            message = str(error)
            self._release_profile_lock()
            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception:
                    pass
                self.playwright = None
            if any(marker in message for marker in (
                "Target page, context or browser has been closed",
                "Mở trong phiên trình duyệt hiện tại",
                "ProcessSingleton",
                "profile",
            )):
                raise BrowserProfileInUseError(
                    "Không thể mở Chromium vì hồ sơ đang được một phiên khác sử dụng. "
                    "Hãy đóng phiên Chromium ATS OneBSS đang mở rồi thử lại."
                ) from error
            raise
        self.context.set_default_timeout(self.config.timeout_ms)
        await self.context.grant_permissions(
            ["clipboard-read", "clipboard-write"], origin="https://docs.google.com"
        )
        return self

    async def __aexit__(self, *_args) -> None:
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass
        finally:
            self.context = None
            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception:
                    pass
                self.playwright = None
            self._release_profile_lock()

    async def restart_context(self) -> None:
        """Restart Chromium while retaining this session's profile lock."""
        if not self.playwright:
            raise RuntimeError("Không thể khởi động lại Chromium khi Playwright chưa chạy")
        context = self.context
        self.context = None
        if context:
            try:
                await context.close()
            except Exception:
                pass
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=self.config.browser_profile,
                channel=self.config.browser_channel,
                headless=self.config.headless,
                viewport={"width": 1920, "height": 1000},
                ignore_default_args=["--enable-automation"],
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            )
            self.context.set_default_timeout(self.config.timeout_ms)
            await self.context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin="https://docs.google.com",
            )
        except Exception:
            self.context = None
            raise

    async def page_for(self, url_fragment: str) -> Page:
        assert self.context
        for page in self.context.pages:
            if url_fragment in page.url:
                return page
        return await self.context.new_page()


def _jwt_expiries(value: str) -> list[int]:
    parts = str(value or "").split(".")
    if len(parts) < 2:
        return []
    try:
        payload = parts[1].replace("-", "+").replace("_", "/")
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        expiry = json.loads(decoded.decode("utf-8")).get("exp")
        if isinstance(expiry, (int, float)) and expiry > 0:
            return [int(expiry)]
    except (ValueError, TypeError, UnicodeDecodeError):
        pass
    return []


class AssignmentPopupError(RuntimeError):
    """A pre-save assignment-dialog failure that is safe to retry once."""


def _sheet_subscriber_key(value: str) -> str:
    """Normalize Sheet text markers, including a lone apostrophe for blank ids."""
    cleaned = value.strip()
    return cleaned[1:] if cleaned.startswith("'") else cleaned


def _sheet_text_input(value: str) -> str:
    """Force non-empty identifiers to text so Sheets preserves leading zeroes."""
    return f"'{value}" if value else ""


def _sheet_assignment_key(
    timestamp: str, row: list[str], columns: tuple[str, ...],
) -> tuple[str, str, str, str, str]:
    """Identify one assignment independently of mutable or Sheets-formatted fields."""
    values = dict(zip(columns, row, strict=True))
    return (
        timestamp.strip(),
        values["transaction_id"].strip(),
        _sheet_subscriber_key(values["subscriber_id"]),
        normalize(values["service"]),
        normalize(values["assignee"]),
    )


def _sheet_name_for_timestamp(timestamp: str, template: str, timezone: str) -> str:
    """Resolve the monthly tab from an assignment's local timestamp."""
    if timestamp:
        assigned = datetime.strptime(timestamp, "%d/%m/%Y %H:%M")
    else:
        assigned = datetime.now(ZoneInfo(timezone))
    return template.format(month=assigned.month, year=assigned.year)


def _is_sheet_data_row(row: list[str]) -> bool:
    """Identify an assignment row independently of compacted CSV row numbers."""
    if len(row) < 5 or not row[1].strip() or not row[4].strip():
        return False
    try:
        datetime.strptime(row[0].strip(), "%d/%m/%Y %H:%M")
    except ValueError:
        return False
    return True


def _sheet_assignment_index(
    sheets: Iterable[tuple[str, list[list[str]]]],
) -> dict[str, tuple[str, ...]]:
    """Return the latest Sheet assignee(s) for each transaction/subscriber."""
    latest: dict[str, tuple[datetime, list[str]]] = {}
    for _sheet_name, rows in sheets:
        for row in rows:
            if not _is_sheet_data_row(row):
                continue
            assigned_at = datetime.strptime(row[0].strip(), "%d/%m/%Y %H:%M")
            identity = ticket_identity(row[1], _sheet_subscriber_key(row[2]))
            assignee = row[4].strip()
            current = latest.get(identity)
            if current is None or assigned_at > current[0]:
                latest[identity] = (assigned_at, [assignee])
            elif assigned_at == current[0] and normalize(assignee) not in {
                normalize(name) for name in current[1]
            }:
                current[1].append(assignee)
    return {identity: tuple(item[1]) for identity, item in latest.items()}


class SheetAssignmentSource(dict[str, tuple[str, ...]]):
    """Historical Sheet routing plus the identities present in this month."""

    def __init__(
        self,
        assignments: dict[str, tuple[str, ...]],
        current_keys: Iterable[str],
    ) -> None:
        super().__init__(assignments)
        self.current_keys = set(current_keys)


def _sheet_month_scores(
    rows: list[list[str]], fallback_names: tuple[str, ...] = (),
) -> dict[str, Decimal]:
    """Read the authoritative employee point totals from the monthly tab."""
    names: list[str] | None = None
    points: list[str] | None = None
    for row in rows:
        if not row:
            continue
        label = unaccent(row[0])
        if label == "nhan su thuc hien":
            names = row
        elif label == "diem quy doi":
            points = row
    # Merged/formula title cells are visible in Google Sheets but may be blank
    # in GViz CSV. In this workbook the score row is immediately followed by
    # the percentage row; align it with the Excel personnel column order.
    if (names is None or points is None) and fallback_names:
        for index in range(len(rows) - 1):
            if sum("%" in value for value in rows[index + 1]) < 2:
                continue
            names = [""] + list(fallback_names)
            points = rows[index]
            break
    if names is None or points is None:
        return {}
    result: dict[str, Decimal] = {}
    for index in range(1, min(len(names), len(points))):
        name = names[index].strip()
        raw = points[index].strip().replace(" ", "")
        if not name or not raw:
            continue
        try:
            result[name] = Decimal(raw.replace(",", "."))
        except InvalidOperation:
            continue
    return result


def _walk_mappings(value: object) -> Iterable[Mapping[object, object]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def _api_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", unaccent(value))


def _contract_type_from_api(payload: object, ticket: Ticket) -> str:
    """Read Loại HĐ from the raw Lấy thông tin response, never Kiểu lắp đặt."""
    preferred_keys = {
        "tenloaihd": 5,
        "tenloaihopdong": 5,
        "loaihd": 4,
        "loaihopdong": 4,
        "loaihinhhd": 3,
    }
    best: tuple[int, str] | None = None
    for record in _walk_mappings(payload):
        text_values = [str(value).strip() for value in record.values() if value is not None]
        if ticket.transaction_id not in text_values:
            continue
        if ticket.subscriber_id and ticket.subscriber_id not in text_values:
            continue
        for key, value in record.items():
            key_name = _api_key(key)
            score = preferred_keys.get(key_name)
            shown = str(value or "").strip()
            if score is None or not shown:
                continue
            if key_name.startswith("ma"):
                continue
            candidate = (score, shown)
            if best is None or candidate[0] > best[0]:
                best = candidate
    return best[1] if best else ""


def _installation_type_from_api(payload: object, ticket: Ticket) -> str:
    """Read Kiểu lắp đặt without ever treating it as Loại HĐ."""
    preferred_keys = {
        "kieulapdat": 5,
        "tenkieulapdat": 5,
        "kieuld": 4,
        "tenkieuld": 4,
    }
    best: tuple[int, str] | None = None
    for record in _walk_mappings(payload):
        text_values = [str(value).strip() for value in record.values() if value is not None]
        if ticket.transaction_id not in text_values:
            continue
        if ticket.subscriber_id and ticket.subscriber_id not in text_values:
            continue
        for key, value in record.items():
            key_name = _api_key(key)
            score = preferred_keys.get(key_name)
            shown = str(value or "").strip()
            if score is None or not shown or key_name.startswith("ma"):
                continue
            candidate = (score, shown)
            if best is None or candidate[0] > best[0]:
                best = candidate
    return best[1] if best else ""


def _ticket_metadata_from_api(payload: object, ticket: Ticket) -> dict[str, str]:
    """Read Sheet/detail fields from the raw ``Lấy thông tin`` response."""
    field_keys = {
        "subscriber_name": {"tentb", "tenthuebao", "tentbban", "tenthuebaoban"},
        "customer_name": {"tenkh", "tenkhachhang", "khachhang"},
        "notes": {"ghichu", "note"},
        "labor_address": {"diachild", "diachilaodong"},
        "connection_address": {"diachikn", "diachiketnoi"},
        "labor_province": {"tinhld", "tinhlaodong", "tentinhld", "tentinhlaodong"},
    }
    # The response can contain a short grid record as well as a fuller nested
    # record for the same sale transaction.  Do not stop at the first match:
    # that record can omit (or carry stale values for) Tên KH and Ghi chú,
    # which would make a project rule appear not to match.
    best_values: dict[str, str] | None = None
    best_score: tuple[int, int] | None = None
    for record in _walk_mappings(payload):
        text_values = [str(value).strip() for value in record.values() if value is not None]
        if ticket.transaction_id not in text_values:
            continue
        if ticket.subscriber_id and ticket.subscriber_id not in text_values:
            continue
        normalized = {_api_key(key): str(value or "").strip() for key, value in record.items()}
        values = {
            field: next(
                (normalized[key] for key in keys if normalized.get(key)), ""
            )
            for field, keys in field_keys.items()
        }
        if any(values.values()):
            # Prefer the record carrying the most requested fields, then the
            # one with more non-empty content.  This retains the usual fast
            # grid path while ensuring project predicates use the fullest
            # response available for the same ticket.
            score = (
                sum(bool(value) for value in values.values()),
                sum(len(value) for value in values.values()),
            )
            if best_score is None or score > best_score:
                best_values = values
                best_score = score
    return best_values or {field: "" for field in field_keys}


def _project_details_from_api(
    payload: object, ticket: Ticket
) -> tuple[str, str, str]:
    """Read project customer and routing addresses from a raw response."""
    values = _ticket_metadata_from_api(payload, ticket)
    return (
        values["customer_name"],
        values["labor_address"],
        values["connection_address"],
    )


def _province_from_address(address: str) -> str:
    """Use an explicit province/city phrase only when OneBSS leaves Tỉnh LĐ blank."""
    shown = str(address or "").strip()
    if not shown:
        return ""
    explicit = re.search(
        r"\b((?:tỉnh|thành phố|tp\.?)[ ]+[^,;()]+)",
        shown,
        flags=re.IGNORECASE,
    )
    if explicit:
        return explicit.group(1).strip()
    # Centrally governed cities are often written at the end without a prefix.
    normalized = unaccent(shown)
    for city in ("Hồ Chí Minh", "Hải Phòng", "Đà Nẵng", "Cần Thơ", "Hà Nội", "Huế"):
        if unaccent(city) in normalized:
            return city
    return ""


async def _row_matches_sale_subscriber(row: Locator, subscriber_id: str) -> bool:
    """Confirm a selected grid row by Mã thuê bao bán only.

    Mã TB thi công is a different identifier on OneBSS and must never be used
    to validate the sale subscriber selected from the assignment grid.
    """
    wanted = normalize(subscriber_id)
    if not wanted:
        return False
    cells = await row.locator("td").evaluate_all(
        """cells => cells.map(cell => ({
            text: cell.innerText,
            label: cell.getAttribute('aria-label') || ''
        }))"""
    )
    labeled = [
        cell for cell in cells
        if "mã thuê bao bán" in normalize(cell.get("label", ""))
    ]
    candidates = labeled or cells
    return any(normalize(cell.get("text", "")) == wanted for cell in candidates)


async def _row_matches_ticket(row: Locator, ticket: Ticket) -> bool:
    """Validate the selected sale row, including tickets without a subscriber id."""
    if ticket.subscriber_id:
        return await _row_matches_sale_subscriber(row, ticket.subscriber_id)
    wanted = normalize(ticket.transaction_id)
    if not wanted:
        return False
    cells = await row.locator("td").evaluate_all(
        """cells => cells.map(cell => ({
            text: cell.innerText,
            label: cell.getAttribute('aria-label') || ''
        }))"""
    )
    labeled = [
        cell for cell in cells
        if "mã giao dịch bán" in normalize(cell.get("label", ""))
    ]
    candidates = labeled or cells
    return any(normalize(cell.get("text", "")) == wanted for cell in candidates)


async def _visible(locator: Locator, timeout_ms: int = 30_000) -> Locator:
    """Wait for any matching element to become visible.

    OneBSS often displays a dialog shell before Vue/Syncfusion mounts its
    contents, so a one-shot count incorrectly reports that the element is
    missing.
    """
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    while asyncio.get_running_loop().time() < deadline:
        count = await locator.count()
        for index in range(count):
            candidate = locator.nth(index)
            if await candidate.is_visible():
                return candidate
        await asyncio.sleep(0.25)
    raise RuntimeError("Không tìm thấy phần tử đang hiển thị")


async def _set_hidden_checkbox(checkbox: Locator, checked: bool) -> None:
    """Toggle a Syncfusion checkbox whose native input is off-screen."""
    await checkbox.evaluate(
        "(element, wanted) => { if (element.checked !== wanted) element.click(); }",
        checked,
    )
    if await checkbox.is_checked() != checked:
        raise RuntimeError("OneBSS không cập nhật trạng thái checkbox")


async def _row_with_normalized_text(rows: Locator, wanted: str, timeout_ms: int) -> Locator:
    """Find a visible row without depending on the page's Unicode form."""
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    normalized_wanted = normalize(wanted)
    while asyncio.get_running_loop().time() < deadline:
        # Read one atomic snapshot. Calling inner_text() row by row can wait a
        # full Playwright timeout when Syncfusion replaces a row mid-loop.
        snapshot = await rows.evaluate_all(
            """elements => elements.map((element, index) => ({
                index,
                text: element.innerText,
                visible: !!(element.offsetWidth || element.offsetHeight ||
                            element.getClientRects().length)
            }))"""
        )
        for item in snapshot:
            if item["visible"] and normalized_wanted in normalize(item["text"]):
                return rows.nth(item["index"])
        await asyncio.sleep(0.25)
    raise RuntimeError(f"Không tìm thấy nhân viên {wanted} trong bảng OneBSS")


async def _click_row_with_normalized_text(rows: Locator, wanted: str, timeout_ms: int) -> None:
    """Find and click a virtualized row atomically in the current DOM snapshot."""
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    wanted_nfc = unicodedata.normalize("NFC", wanted).casefold()
    while asyncio.get_running_loop().time() < deadline:
        clicked = await rows.evaluate_all(
            """(elements, wanted) => {
                const clean = value => value.normalize('NFC')
                    .toLocaleLowerCase('vi-VN').replace(/\\s+/g, ' ').trim();
                const row = elements.find(element => clean(element.innerText).includes(wanted));
                if (!row) return false;
                for (const type of ['mousedown', 'mouseup', 'click']) {
                    row.dispatchEvent(new MouseEvent(type, {
                        bubbles: true, cancelable: true, button: 0
                    }));
                }
                return true;
            }""",
            wanted_nfc,
        )
        if clicked:
            return
        await asyncio.sleep(0.25)
    raise RuntimeError(f"Không thể chọn nhân viên {wanted} trong bảng OneBSS")


async def _click_in_normalized_row(
    rows: Locator, wanted: str, selector: str, timeout_ms: int
) -> None:
    """Atomically find a virtual row and click one of its descendants."""
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    wanted_nfc = unicodedata.normalize("NFC", wanted).casefold()
    while asyncio.get_running_loop().time() < deadline:
        clicked = await rows.evaluate_all(
            """(elements, args) => {
                const clean = value => value.normalize('NFC')
                    .toLocaleLowerCase('vi-VN').replace(/\\s+/g, ' ').trim();
                const row = elements.find(element => clean(element.innerText).includes(args.wanted));
                const target = row?.querySelector(args.selector);
                if (!target) return false;
                target.dispatchEvent(new MouseEvent('mousedown', {
                    bubbles: true, cancelable: true, button: 0
                }));
                target.dispatchEvent(new MouseEvent('mouseup', {
                    bubbles: true, cancelable: true, button: 0
                }));
                target.dispatchEvent(new MouseEvent('click', {
                    bubbles: true, cancelable: true, button: 0
                }));
                return true;
            }""",
            {"wanted": wanted_nfc, "selector": selector},
        )
        if clicked:
            return
        await asyncio.sleep(0.25)
    raise RuntimeError(f"Không thể mở nhiệm vụ của nhân viên {wanted}")


async def _point_in_normalized_row(
    rows: Locator, wanted: str, selector: str, timeout_ms: int
) -> dict[str, float]:
    """Return a current viewport point for a control in a virtualized row."""
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    wanted_nfc = unicodedata.normalize("NFC", wanted).casefold()
    while asyncio.get_running_loop().time() < deadline:
        point = await rows.evaluate_all(
            """(elements, args) => {
                const clean = value => value.normalize('NFC')
                    .toLocaleLowerCase('vi-VN').replace(/\\s+/g, ' ').trim();
                const row = elements.find(element => clean(element.innerText).includes(args.wanted));
                const target = row?.querySelector(args.selector);
                if (!target) return null;
                const rect = target.getBoundingClientRect();
                return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};
            }""",
            {"wanted": wanted_nfc, "selector": selector},
        )
        if point:
            return point
        await asyncio.sleep(0.25)
    raise RuntimeError(f"Không xác định được ô nhiệm vụ của nhân viên {wanted}")


async def _row_control_has_text(
    rows: Locator, wanted_row: str, selector: str, wanted_text: str
) -> bool:
    return await rows.evaluate_all(
        """(elements, args) => {
            const clean = value => value.normalize('NFC')
                .toLocaleLowerCase('vi-VN').replace(/\\s+/g, ' ').trim();
            const row = elements.find(element => clean(element.innerText).includes(args.row));
            const control = row?.querySelector(args.selector);
            return !!control && clean(control.innerText).includes(args.text);
        }""",
        {
            "row": unicodedata.normalize("NFC", wanted_row).casefold(),
            "selector": selector,
            "text": unicodedata.normalize("NFC", wanted_text).casefold(),
        },
    )


async def _row_has_selected_task(rows: Locator, wanted_row: str, wanted_text: str) -> bool:
    """Verify a committed treeselect value, excluding text merely typed in its input."""
    return await _row_control_has_text(
        rows,
        wanted_row,
        ".vue-treeselect__single-value, .vue-treeselect__multi-value-item",
        wanted_text,
    )


async def _close_assignment_dialogs(page: Page, timeout_ms: int = 8_000) -> None:
    """Close dialog remnants that otherwise cover the next batch action."""
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    dialogs = page.locator('[role="dialog"]', has_text="Giao phiếu nhân viên")
    while asyncio.get_running_loop().time() < deadline:
        visible_dialogs = []
        for index in range(await dialogs.count()):
            candidate = dialogs.nth(index)
            if await candidate.is_visible():
                visible_dialogs.append(candidate)
        if not visible_dialogs:
            return

        # Close the topmost dialog first. Syncfusion sometimes leaves an old
        # hidden shell in the DOM, so operate only on currently visible ones.
        dialog = visible_dialogs[-1]
        close = dialog.locator("button.e-dlg-closeicon-btn")
        if await close.count():
            await close.evaluate("element => element.click()")
        else:
            await page.keyboard.press("Escape")
        try:
            await dialog.wait_for(state="hidden", timeout=2_000)
        except Exception:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
    raise RuntimeError("Không đóng được cửa sổ Giao phiếu nhân viên của lô trước")


async def _visible_employee_grid(page: Page, timeout_ms: int) -> tuple[Locator, Locator]:
    """Reacquire the live assignment dialog and its employee grid.

    OneBSS replaces the dialog subtree after filtering an employee. Holding a
    locator scoped to the previous subtree can therefore miss a grid that is
    plainly visible on screen.
    """
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    while asyncio.get_running_loop().time() < deadline:
        dialogs = page.locator('[role="dialog"]', has_text="Giao phiếu nhân viên")
        for dialog_index in range(await dialogs.count() - 1, -1, -1):
            dialog = dialogs.nth(dialog_index)
            if not await dialog.is_visible():
                continue
            grids = dialog.locator(".e-grid")
            for grid_index in range(await grids.count()):
                grid = grids.nth(grid_index)
                if not await grid.is_visible():
                    continue
                # The task widget uniquely identifies the employee grid and is
                # more stable than Syncfusion header text during re-renders.
                if await grid.locator(".vue-treeselect__input").count():
                    return dialog, grid
        await asyncio.sleep(0.25)
    raise RuntimeError("Không tìm thấy bảng nhân viên đang hiển thị")


async def _visible_assigned_grid(dialog: Locator, timeout_ms: int) -> Locator:
    """Find the lower grid: Danh sách nhân viên đã giao nhiệm vụ."""
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    while asyncio.get_running_loop().time() < deadline:
        grids = dialog.locator(".e-grid")
        for index in range(await grids.count()):
            grid = grids.nth(index)
            if not await grid.is_visible() or await grid.locator(
                ".vue-treeselect__input"
            ).count():
                continue
            header = normalize(" ".join(
                await grid.locator(".e-gridheader .e-headertext").all_inner_texts()
            ))
            if "ngày giao" in header and "nhiệm vụ" in header:
                return grid
        await asyncio.sleep(0.25)
    raise RuntimeError("Không tìm thấy bảng nhân viên đã giao nhiệm vụ")


async def _assigned_row_snapshot(rows: Locator) -> list[dict[str, object]]:
    """Read assigned rows, including the hidden employee-name column.

    OneBSS renders ``Tên nhân viên`` in an ``e-hide`` cell. ``innerText`` omits
    that cell, which previously made reassignment believe the old employee was
    absent and add the new employee without deleting the old one.
    """
    return await rows.evaluate_all(
        """elements => elements.map((element, index) => ({
            index,
            text: element.textContent || '',
            visible: !!(element.offsetWidth || element.offsetHeight ||
                        element.getClientRects().length)
        }))"""
    )


async def _assigned_row_with_name(
    rows: Locator, wanted: str, timeout_ms: int
) -> Locator:
    """Find an assigned row by employee name, including hidden table cells."""
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    normalized_wanted = normalize(wanted)
    while asyncio.get_running_loop().time() < deadline:
        for item in await _assigned_row_snapshot(rows):
            if item["visible"] and normalized_wanted in normalize(item["text"]):
                return rows.nth(int(item["index"]))
        await asyncio.sleep(0.25)
    raise RuntimeError(
        f"Không tìm thấy {wanted} trong danh sách nhân viên đã giao nhiệm vụ"
    )


async def _assigned_name_count(rows: Locator, wanted: str) -> int:
    normalized_wanted = normalize(wanted)
    return sum(
        bool(item["visible"] and normalized_wanted in normalize(item["text"]))
        for item in await _assigned_row_snapshot(rows)
    )


class OneBSSClient:
    def __init__(self, session: BrowserSession):
        self.session = session
        self.page: Page | None = None
        self._ticket_details: dict[str, tuple[str, str, str, str, str, str]] = {}
        self._ticket_response_payload: object = None
        self._page_size = 500
        self._page_size_announced = False

    async def _remember_ticket_response(self, response) -> None:
        try:
            self._ticket_response_payload = await response.json()
        except Exception:
            self._ticket_response_payload = None

    async def open(self) -> Page:
        page = await self.session.page_for("onebss.vnpt.vn")
        if "onebss.vnpt.vn" not in page.url:
            await page.goto(self.session.config.onebss_url, wait_until="domcontentloaded")
        self.page = page
        return page

    async def wait_until_logged_in(self) -> None:
        page = self.page or await self.open()
        print("Đăng nhập OneBSS trong cửa sổ Chromium nếu được yêu cầu...")
        await page.wait_for_selector("#frmGiaoViecVIP", timeout=0)

    async def login_with_otp(
        self, username: str, password: str, otp_provider,
    ) -> None:
        """Submit saved credentials, request OTP, and verify the work screen."""
        page = await self.open()
        if await self.session_is_logged_in():
            return

        await self._wait_for_login_form(30)
        if await self.session_is_logged_in():
            return

        password_field = page.locator('input[type="password"]:visible').first
        if await password_field.count() == 0:
            raise OneBSSLoginError("Không nhận diện được ô mật khẩu trên trang OneBSS.")
        user_candidates = (
            'input[autocomplete="username"]:visible',
            'input[type="email"]:visible',
            'input[name*="user" i]:visible',
            'input[id*="user" i]:visible',
            'input[type="text"]:visible',
        )
        username_field = None
        for selector in user_candidates:
            candidate = page.locator(selector).first
            if await candidate.count():
                username_field = candidate
                break
        if username_field is None:
            raise OneBSSLoginError("Không nhận diện được ô tài khoản trên trang OneBSS.")

        try:
            await username_field.fill(username)
            await password_field.fill(password)
            form = password_field.locator("xpath=ancestor::form[1]")
            submit = form.locator('button[type="submit"], input[type="submit"]').first
            if not await submit.count():
                submit = page.get_by_role(
                    "button", name=re.compile(r"đăng nhập|log\s*in|sign\s*in|tiếp tục", re.I)
                ).first
            if await submit.count() and await submit.is_visible():
                await submit.click()
            else:
                await password_field.press("Enter")
            await self._wait_for_auth_transition(60)
            if await self.session_is_logged_in():
                return

            otp_fields = await self._visible_otp_fields()
            if not otp_fields:
                raise OneBSSLoginError(
                    "OneBSS chưa hiện ô OTP sau khi gửi tài khoản và mật khẩu."
                )
            print("OneBSS yêu cầu OTP; đang chờ mã từ Telegram (không ghi mã vào log).")
            code = await otp_provider()
            if not re.fullmatch(r"\d{4,8}", str(code)):
                raise OneBSSLoginError("Mã OTP Telegram không đúng định dạng.")
            if len(otp_fields) > 1:
                for field, digit in zip(otp_fields, code):
                    await field.fill(digit)
            else:
                await otp_fields[0].fill(code)
            form = otp_fields[0].locator("xpath=ancestor::form[1]")
            submit = form.locator('button[type="submit"], input[type="submit"]').first
            if not await submit.count():
                submit = page.get_by_role(
                    "button", name=re.compile(r"xác nhận|verify|submit|tiếp tục|continue", re.I)
                ).first
            if await submit.count() and await submit.is_visible():
                await submit.click()
            else:
                await otp_fields[-1].press("Enter")
            await self._wait_for_auth_transition(60)
            if not await self.session_is_logged_in():
                raise OneBSSLoginError("OneBSS không xác nhận đăng nhập thành công.")
        except OneBSSLoginError:
            raise
        except Exception as error:
            # Do not expose locator values, which can include user input.
            raise OneBSSLoginError(
                f"Không hoàn tất được đăng nhập OneBSS ({type(error).__name__})."
            ) from None

    async def _visible_otp_fields(self) -> list[Locator]:
        assert self.page
        selectors = (
            'input[autocomplete="one-time-code"]:visible',
            'input[name*="otp" i]:visible',
            'input[id*="otp" i]:visible',
            'input[placeholder*="otp" i]:visible',
            'input[placeholder*="mã xác" i]:visible',
            'input[name*="code" i]:visible',
            'input[id*="code" i]:visible',
            'input[maxlength="4"]:visible, input[maxlength="5"]:visible, '
            'input[maxlength="6"]:visible, input[maxlength="7"]:visible, '
            'input[maxlength="8"]:visible',
            'input[inputmode="numeric"][maxlength="1"]:visible',
        )
        for selector in selectors:
            locator = self.page.locator(selector)
            count = await locator.count()
            if count:
                return [locator.nth(index) for index in range(min(count, 8))]
        # Many VNPT sign-in pages use an unlabelled numeric code field.
        candidates = self.page.locator(
            'input[type="text"][inputmode="numeric"]:visible, '
            'input[type="tel"]:visible'
        )
        return [candidates.nth(index) for index in range(await candidates.count())]

    async def _wait_for_auth_transition(self, timeout_seconds: int) -> None:
        assert self.page
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if await self.session_is_logged_in() or await self._visible_otp_fields():
                return
            if await self.page.locator('input[type="password"]:visible').count():
                # Give the page a moment to render its inline credential error.
                alerts = self.page.locator('[role="alert"], .alert-danger, .validation-summary-errors')
                if await alerts.count() and (await alerts.first.inner_text()).strip():
                    raise OneBSSLoginError("OneBSS không chấp nhận thông tin đăng nhập.")
            await self.page.wait_for_timeout(500)
        if await self.page.locator('input[type="password"]:visible').count():
            raise OneBSSLoginError("OneBSS không chấp nhận thông tin đăng nhập.")

    async def _wait_for_login_form(self, timeout_seconds: int) -> None:
        assert self.page
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if await self.session_is_logged_in():
                return
            if await self.page.locator('input[type="password"]:visible').count():
                return
            if await self._visible_otp_fields():
                return
            await self.page.wait_for_timeout(500)

    async def reload_for_recovery(self) -> None:
        """Reload OneBSS and rebuild its unassigned-ticket snapshot."""
        if not self.page or self.page.is_closed():
            raise RuntimeError("Tab OneBSS đã đóng")
        timeout_ms = max(15_000, self.session.config.timeout_ms)
        print("OneBSS có dấu hiệu bị treo; đang tải lại tab trình duyệt...")
        await self.page.reload(
            wait_until="domcontentloaded", timeout=timeout_ms
        )
        await self.page.wait_for_selector("#frmGiaoViecVIP", timeout=timeout_ms)
        await self.page.wait_for_timeout(1_000)
        await self.ensure_unassigned_filters()
        await self.refresh_tickets()

    async def session_expiry_epoch(self) -> int | None:
        """Read only expiration metadata for OneBSS auth tokens/cookies."""
        context = self.session.context
        page = self.page
        if context is None or page is None:
            return None
        now = time.time()
        candidates: list[int] = []
        auth_cookie_name = re.compile(
            r"auth|access|jwt|token|session|sid", re.IGNORECASE
        )
        try:
            for cookie in await context.cookies(self.session.config.onebss_url):
                if not auth_cookie_name.search(cookie.get("name", "")):
                    continue
                expires = cookie.get("expires", -1)
                if isinstance(expires, (int, float)) and expires > 0:
                    candidates.append(int(expires))
                candidates.extend(_jwt_expiries(cookie.get("value", "")))
            storage_expiries = await page.evaluate(
                """() => {
                    const result = [];
                    const keyPattern = /auth|access|jwt|token|session|sid/i;
                    const walk = value => {
                        if (!value || typeof value !== 'object') return;
                        for (const [key, child] of Object.entries(value)) {
                            if (/^(exp|expires|expiresat|expiration|expirationtime)$/i.test(key)) {
                                const n = Number(child);
                                if (Number.isFinite(n) && n > 0) result.push(n);
                            } else if (child && typeof child === 'object') {
                                walk(child);
                            }
                        }
                    };
                    const inspect = storage => {
                        for (let i = 0; i < storage.length; i++) {
                            const key = storage.key(i) || '';
                            if (!keyPattern.test(key)) continue;
                            const value = storage.getItem(key) || '';
                            const parts = value.split('.');
                            if (parts.length >= 2) {
                                try {
                                    const normalized = parts[1].replace(/-/g, '+').replace(/_/g, '/');
                                    const payload = JSON.parse(atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')));
                                    walk(payload);
                                } catch (_) {}
                            }
                            try { walk(JSON.parse(value)); } catch (_) {}
                        }
                    };
                    try { inspect(localStorage); } catch (_) {}
                    try { inspect(sessionStorage); } catch (_) {}
                    return result;
                }"""
            )
            for value in storage_expiries:
                timestamp = int(value / 1000) if value > 10_000_000_000 else int(value)
                if timestamp > 0:
                    candidates.append(timestamp)
        except Exception:
            future = [timestamp for timestamp in candidates if timestamp > now]
            return min(future) if future else max(candidates, default=None)
        if not candidates:
            return None
        future = [timestamp for timestamp in candidates if timestamp > now]
        return min(future) if future else max(candidates)

    async def session_is_logged_in(self) -> bool:
        """Check the OneBSS work screen without exposing its session token."""
        page = self.page
        if page is None:
            return False
        try:
            return (
                "onebss.vnpt.vn" in page.url
                and await page.locator("#frmGiaoViecVIP").count() > 0
                and await page.locator("#frmGiaoViecVIP").is_visible()
            )
        except Exception:
            return False

    async def _main_grid(self) -> Locator:
        assert self.page
        grids = self.page.locator("#frmGiaoViecVIP .e-grid")
        for index in range(await grids.count()):
            grid = grids.nth(index)
            text = await grid.inner_text()
            if "Mã giao dịch bán" in text and "Mã thuê bao bán" in text and "Loại hình" in text:
                return grid
        raise RuntimeError("Không tìm thấy bảng danh sách phiếu OneBSS")

    async def ensure_assignment_filters(self, assignment_status: str) -> None:
        """Disable employee filtering and select Chưa giao/Tất cả."""
        assert self.page
        summary = self.page.locator("#frmGiaoViecVIP .form-control.bold").filter(
            has_text="Lọc theo nhân viên"
        ).first
        # OneBSS deployments render this popup in different wrapper levels;
        # the first page-level popup is the employee/status filter panel.
        panel = self.page.locator("#frmGiaoViecVIP .popupContainerControl1").first
        # The current Vue build keeps the panel hidden and native Playwright
        # uncheck() waits forever for visibility. Dispatch the same DOM events
        # directly; the selected assignment status is already Tất cả when
        # reassigning, so no dropdown interaction is required.
        if assignment_status == "Tất cả":
            checkbox = panel.locator("#chkLoc")
            if await checkbox.count() and await checkbox.is_checked():
                await checkbox.evaluate(
                    """element => {
                        element.checked = false;
                        element.dispatchEvent(new Event('input', {bubbles: true}));
                        element.dispatchEvent(new Event('change', {bubbles: true}));
                    }"""
                )
                await self.page.wait_for_timeout(500)
            return
        # Earlier runs may have hidden this legacy popup with an inline style.
        # Remove that style before asking Vue to display it again.
        await panel.evaluate("element => element.style.removeProperty('display')")
        backdrop = self.page.locator("#frmGiaoViecVIP .popupContainerControl1Drop")
        if await backdrop.count():
            await backdrop.evaluate_all(
                "elements => elements.forEach(element => element.style.removeProperty('display'))"
            )
        if not await panel.is_visible():
            await summary.click()
        checkbox = panel.locator("#chkLoc")
        if await checkbox.is_checked():
            await checkbox.uncheck()
        # Use the visible Select2 widget so Vue receives the same event sequence
        # as a human selection. The hidden native select alone does not update
        # this page's model reliably.
        assignment_select = panel.locator("select").nth(1)
        selected_text = ""
        checked_option = assignment_select.locator("option:checked")
        if await checked_option.count():
            selected_text = (await checked_option.inner_text()).strip()
        if normalize(selected_text) == normalize(assignment_status):
            if await panel.is_visible():
                await panel.evaluate("element => element.style.display = 'none'")
            if await backdrop.count():
                await backdrop.evaluate_all(
                    "elements => elements.forEach(element => element.style.display = 'none')"
                )
            return
        assignment_widget = assignment_select.locator(
            "xpath=following-sibling::span[contains(@class, 'select2-container')]"
        )
        await (await _visible(assignment_widget)).click()
        option = self.page.locator(
            ".select2-container--open .select2-results__option",
            has_text=assignment_status,
        )
        await (await _visible(option)).click()
        # This legacy component has no working close action: neither its
        # summary nor backdrop closes it once open. The selected value has
        # already updated Vue, so hide only the popup UI and its backdrop.
        if await panel.is_visible() or await backdrop.is_visible():
            await panel.evaluate("element => element.style.display = 'none'")
            await backdrop.evaluate_all(
                "elements => elements.forEach(element => element.style.display = 'none')"
            )

    async def ensure_unassigned_filters(self) -> None:
        await self.ensure_assignment_filters("Chưa giao")
        await self.clear_main_grid_filters()

    async def clear_main_grid_filters(self) -> None:
        """Clear every Syncfusion filter so a prior reassign cannot hide tickets."""
        assert self.page
        grid = await self._main_grid()
        count = await grid.locator(".e-filterbar input.e-input").count()
        for index in range(count):
            grid = await self._main_grid()
            inputs = grid.locator(".e-filterbar input.e-input")
            if index >= await inputs.count():
                break
            field = inputs.nth(index)
            if await field.input_value():
                await field.fill("")
                await field.press("Enter")
                await self.page.wait_for_timeout(250)

    async def ensure_main_grid_page_size(self, size: int = 500) -> bool:
        """Set the ticket grid to its largest page size.

        OneBSS uses different Syncfusion pager renderings between sessions.
        Prefer the underlying select (the most stable interface), then fall
        back to the visible dropdown used by the browser UI.
        """
        assert self.page
        grid = await self._main_grid()
        wanted = str(size)

        selects = grid.locator(".e-pager select, .e-gridpager select")
        for index in range(await selects.count()):
            select = selects.nth(index)
            options = [text.strip() for text in await select.locator("option").all_inner_texts()]
            if wanted not in options:
                continue
            selected = select.locator("option:checked")
            current = (await selected.first.inner_text()).strip() if await selected.count() else ""
            if current != wanted:
                try:
                    await select.select_option(label=wanted)
                except Exception:
                    await select.evaluate(
                        """(element, value) => {
                            const option = Array.from(element.options)
                              .find(item => item.textContent.trim() === value);
                            if (!option) throw new Error(`Page size ${value} is unavailable`);
                            element.value = option.value;
                            element.dispatchEvent(new Event('input', { bubbles: true }));
                            element.dispatchEvent(new Event('change', { bubbles: true }));
                        }""",
                        wanted,
                    )
                await self.page.wait_for_timeout(750)
            self._page_size = size
            if not self._page_size_announced:
                print(f"Đã chọn Items per page = {size} trên OneBSS.")
                self._page_size_announced = True
            return True

        pager = grid.locator(".e-pager, .e-gridpager")
        controls = pager.locator(
            ".e-pagerdropdown, .e-dropdownlist, [role='combobox'], .e-input-group"
        )
        for index in range(await controls.count()):
            control = controls.nth(index)
            if not await control.is_visible():
                continue
            shown = normalize(await control.inner_text())
            if shown == normalize(wanted):
                self._page_size = size
                if not self._page_size_announced:
                    print(f"Đã chọn Items per page = {size} trên OneBSS.")
                    self._page_size_announced = True
                return True
            await control.click(force=True)
            choices = self.page.locator(
                ".e-popup-open .e-list-item, .e-ddl.e-popup .e-list-item, "
                "[role='listbox']:visible [role='option']"
            )
            for choice_index in range(await choices.count()):
                choice = choices.nth(choice_index)
                if (
                    await choice.is_visible()
                    and (await choice.inner_text()).strip() == wanted
                ):
                    await choice.click(force=True)
                    await self.page.wait_for_timeout(750)
                    self._page_size = size
                    if not self._page_size_announced:
                        print(f"Đã chọn Items per page = {size} trên OneBSS.")
                        self._page_size_announced = True
                    return True
            await self.page.keyboard.press("Escape")
        return False

    async def visible_tickets(self, refresh: bool = True) -> list[Ticket]:
        grid = await self._main_grid()
        if refresh:
            assert self.page
            await self.ensure_unassigned_filters()
            action = await _visible(
                self.page.locator("#frmGiaoViecVIP .list-actions-top a", has_text="Lấy thông tin")
            )
            async with self.page.expect_response(
                lambda response: "giaoviec-vip/sp_khdn_lay_phieu_vip_v2" in response.url
                and response.status == 200,
                timeout=self.session.config.timeout_ms,
            ) as response_info:
                await action.click(force=True)
            await self._remember_ticket_response(await response_info.value)
            # Vue/Syncfusion renders after the API promise resolves. Waiting for
            # the next render prevents parsing rows left from the previous filter.
            await self.page.wait_for_timeout(1_500)
            try:
                await self.page.wait_for_function(
                    """
                    () => Array.from(document.querySelectorAll('#frmGiaoViecVIP .e-grid'))
                      .filter(grid => grid.innerText.includes('Mã giao dịch bán') &&
                                      grid.innerText.includes('Mã thuê bao bán'))
                      .some(grid => Array.from(grid.querySelectorAll('.e-gridcontent tr.e-row td'))
                        .some(cell => cell.getAttribute('aria-label')?.includes('column header Mã giao dịch bán') &&
                                      cell.textContent.trim().length > 0))
                    """,
                    timeout=self.session.config.timeout_ms,
                )
            except Exception:
                # A genuinely empty result is valid; parsing below will return [].
                pass
            await self.ensure_main_grid_page_size(self._page_size)
            grid = await self._main_grid()
        headers = [normalize(text) for text in await grid.locator(".e-gridheader .e-headertext").all_inner_texts()]
        rows = grid.locator(".e-gridcontent tr.e-row")
        result: list[Ticket] = []
        aliases = {
            "transaction_id": {"mã giao dịch bán"},
            "subscriber_id": {"mã thuê bao bán"},
            "service": {"dịch vụ"},
            "service_type": {"loại hình"},
            "contract_type": {"loại hđ"},
            "installation_type": {"kiểu lắp đặt", "kiểu lđ"},
            "channel_type": {"loại kênh"},
            "vip_status": {"vip xử lý"},
            "subscriber_name": {"tên thuê bao"},
            "customer_name": {"tên kh", "tên khách hàng"},
            "labor_address": {"địa chỉ lđ", "địa chỉ lao động"},
            "labor_province": {"tỉnh lđ", "tỉnh lao động"},
        }
        for row_index in range(await rows.count()):
            cell_data = await rows.nth(row_index).locator("td").evaluate_all(
                """cells => cells.map(cell => ({
                    text: cell.innerText,
                    label: cell.getAttribute('aria-label') || ''
                }))"""
            )
            cells = [cell["text"] for cell in cell_data]
            # Syncfusion adds a leading checkbox cell that has no .e-headertext.
            offset = 1 if len(cells) == len(headers) + 1 else 0
            record: dict[str, str] = {}
            raw: dict[str, str] = {}
            for index, value in enumerate(cells):
                header_index = index - offset
                header = ""
                aria_header = normalize(cell_data[index]["label"])
                # Syncfusion exposes the real column name in aria-label even
                # when horizontal virtualization splits or hides that column.
                for names in aliases.values():
                    match = next((name for name in names if name in aria_header), None)
                    if match:
                        header = match
                        break
                if not header and 0 <= header_index < len(headers):
                    header = headers[header_index]
                raw[header] = value.strip()
                for key, names in aliases.items():
                    if header in names:
                        record[key] = value.strip()
            # Survey/work-order tickets may legitimately have no subscriber
            # code. The transaction id is sufficient to identify them.
            if record.get("transaction_id"):
                record.setdefault("subscriber_id", "")
                ticket = Ticket(raw=raw, **record)
                contract_type = _contract_type_from_api(
                    self._ticket_response_payload, ticket
                )
                if contract_type:
                    ticket = replace(ticket, contract_type=contract_type)
                installation_type = _installation_type_from_api(
                    self._ticket_response_payload, ticket
                )
                if installation_type:
                    ticket = replace(ticket, installation_type=installation_type)
                metadata = _ticket_metadata_from_api(
                    self._ticket_response_payload, ticket
                )
                ticket = replace(
                    ticket,
                    subscriber_name=(
                        ticket.subscriber_name or metadata["subscriber_name"]
                    ),
                    customer_name=ticket.customer_name or metadata["customer_name"],
                    notes=ticket.notes or metadata["notes"],
                    labor_address=ticket.labor_address or metadata["labor_address"],
                    connection_address=(
                        ticket.connection_address or metadata["connection_address"]
                    ),
                    labor_province=(
                        ticket.labor_province or metadata["labor_province"]
                    ),
                )
                result.append(ticket)
        return result

    async def _ticket_row(self, ticket: Ticket) -> Locator:
        grid = await self._main_grid()
        rows = grid.locator(".e-gridcontent tr.e-row")
        for index in range(await rows.count()):
            row = rows.nth(index)
            text = await row.inner_text()
            if ticket.transaction_id in text and ticket.subscriber_id in text:
                return row
        raise RuntimeError(f"Không còn thấy phiếu {ticket.key} trên trang")

    async def _detail_value(self, label: str) -> str:
        """Read an exact field from OneBSS's selected-ticket detail form."""
        assert self.page
        rows = self.page.locator("#frmGiaoViecVIP .info-row")
        for index in range(await rows.count()):
            row = rows.nth(index)
            keys = row.locator(".key")
            if not await keys.count() or not await row.is_visible():
                continue
            if normalize(await keys.first.inner_text()) != normalize(label):
                continue
            field = row.locator("input")
            if await field.count():
                return (await field.first.input_value()).strip()
        return ""

    async def _technical_value(self, label: str) -> str:
        """Read one value from the HTML technical-information panel."""
        assert self.page
        labels = self.page.locator("#frmGiaoViecVIP b").filter(has_text=label)
        for index in range(await labels.count()):
            item = labels.nth(index)
            if not await item.is_visible():
                continue
            if normalize(await item.inner_text()) != normalize(label):
                continue
            return await item.evaluate(
                """element => {
                    let value = '';
                    for (let node = element.nextSibling;
                         node && node.nodeName !== 'BR';
                         node = node.nextSibling) {
                        value += node.textContent || '';
                    }
                    return value.replace(/^\\s*:\\s*/, '').trim();
                }"""
            )
        return ""

    async def enrich_ticket_details(
        self, tickets: list[Ticket], project_rules: list[ProjectRule]
    ) -> list[Ticket]:
        """Attach missing Sheet metadata and project-routing details.

        Prefer the main-grid/API values. Rules which combine Tên KH with Ghi
        chú always read both values from the selected detail form: OneBSS can
        return a non-empty but abbreviated customer value in its ticket list.
        If Tỉnh LĐ is blank, derive it only from an explicit province/city in
        Địa chỉ LĐ. Cache detail values by ticket occurrence so repeated
        planning after each batch does not click through the whole grid again.
        """
        assert self.page
        enriched: list[Ticket] = []
        customer_rules = any(
            "customer_name" in rule.match_fields for rule in project_rules
        )
        requires_detail_project_verification = any(
            "customer_name" in rule.match_fields and rule.required_contains
            for rule in project_rules
        )
        for ticket in tickets:
            if not ticket.labor_province:
                ticket = replace(
                    ticket,
                    labor_province=_province_from_address(ticket.labor_address),
                )
            visible_names = " ".join(
                value for value in (ticket.customer_name, ticket.subscriber_name) if value
            )
            matching_routed_rules = [
                rule for rule in project_rules
                if rule.route_field
                and unaccent(rule.contains) in unaccent(visible_names)
            ]
            matching_note_rules = [
                rule for rule in project_rules
                if any(field == "notes" for field, _ in rule.required_contains)
                and unaccent(rule.contains) in unaccent(visible_names)
            ]
            needs_customer = customer_rules and not ticket.customer_name
            needs_subscriber = not ticket.subscriber_name
            route_resolves = any(
                unaccent(location) in unaccent(getattr(ticket, route_field, ""))
                for rule in matching_routed_rules
                for route_field in (
                    (rule.route_field, "labor_province")
                    if rule.route_field == "labor_address"
                    else (rule.route_field,)
                )
                for route in rule.routes
                for location in route.locations
            )
            needs_address = (
                any(
                    rule.route_field == "labor_address"
                    for rule in matching_routed_rules
                )
                and not route_resolves
            )
            needs_notes = bool(matching_note_rules) and not ticket.notes
            if (
                not requires_detail_project_verification
                and not needs_customer
                and not needs_subscriber
                and not needs_address
                and not needs_notes
            ):
                # On this OneBSS screen Tên thuê bao is the grid rendering of
                # the detail field Tên KH. Avoid reopening every unrelated row.
                enriched.append(ticket if ticket.customer_name else replace(
                    ticket, customer_name=ticket.subscriber_name
                ))
                continue
            cached = self._ticket_details.get(ticket.key)
            if cached is None:
                row = await self._ticket_row(ticket)
                await row.click()
                deadline = asyncio.get_running_loop().time() + 3
                details_match = False
                while asyncio.get_running_loop().time() < deadline:
                    # Validate against the Mã thuê bao bán cell that was used
                    # to select this row. Mã TB thi công is unrelated and can
                    # legitimately contain a completely different value.
                    details_match = await _row_matches_ticket(row, ticket)
                    if details_match:
                        # OneBSS updates Địa chỉ LĐ and Tên KH independently.
                        # In production Tên KH can lag the selected grid row by
                        # more than two seconds, so reading immediately can mix
                        # the customer from the previous ticket with the new
                        # address and route the ticket to the wrong project.
                        await self.page.wait_for_timeout(2_500)
                        break
                    await asyncio.sleep(0.05)
                # Never copy values left over from a previously selected row.
                customer_name = (
                    await self._detail_value("Tên KH") if details_match else ""
                ) or ticket.customer_name
                subscriber_name = (
                    await self._detail_value("Tên TB") if details_match else ""
                ) or ticket.subscriber_name
                notes = (
                    await self._detail_value("Ghi chú") if details_match else ""
                ) or ticket.notes
                labor_address = (
                    await self._detail_value("Địa chỉ LĐ") if details_match else ""
                ) or ticket.labor_address
                labor_province = ticket.labor_province or _province_from_address(
                    labor_address
                )
                connection_address = ticket.connection_address
                cached = (
                    customer_name,
                    subscriber_name,
                    notes,
                    labor_address,
                    connection_address,
                    labor_province,
                )
                self._ticket_details[ticket.key] = cached
            (
                customer_name,
                subscriber_name,
                notes,
                labor_address,
                connection_address,
                labor_province,
            ) = cached
            enriched.append(
                replace(
                    ticket,
                    customer_name=customer_name,
                    subscriber_name=subscriber_name,
                    notes=notes,
                    labor_address=labor_address,
                    connection_address=connection_address,
                    labor_province=labor_province,
                )
            )
        return enriched

    async def refresh_tickets(self) -> None:
        """Reload the server-side list of tickets that are still unassigned."""
        assert self.page
        await _close_assignment_dialogs(self.page)
        action = await _visible(
            self.page.locator("#frmGiaoViecVIP .list-actions-top a", has_text="Lấy thông tin")
        )
        async with self.page.expect_response(
            lambda response: "giaoviec-vip/sp_khdn_lay_phieu_vip_v2" in response.url
            and response.status == 200,
            timeout=self.session.config.timeout_ms,
        ) as response_info:
            await action.click()
        await self._remember_ticket_response(await response_info.value)
        await self.page.wait_for_timeout(750)
        await self.page.wait_for_function(
            """() => !Array.from(document.querySelectorAll('.overlay-common.show'))
                .some(element => {
                    const style = getComputedStyle(element);
                    return style.display !== 'none' && style.visibility !== 'hidden';
                })"""
        )
        await self._main_grid()
        await self.ensure_main_grid_page_size(self._page_size)

    async def assign(self, assignments: list[Assignment]) -> None:
        """Assign a batch, reopening the popup once if its Vue state is broken."""
        for popup_attempt in range(2):
            try:
                await self._assign_once(assignments)
                return
            except AssignmentPopupError:
                await _close_assignment_dialogs(self.page)
                if popup_attempt:
                    raise
                await self.page.wait_for_timeout(500)

    async def _select_single_assignee(self, assignee: str) -> Locator:
        """Select one employee and commit the configured task in the open popup."""
        assert self.page
        last_error: Exception | None = None
        for _attempt in range(1, 4):
            try:
                dialog, employee_grid = await _visible_employee_grid(
                    self.page, self.session.config.timeout_ms
                )
                name_filter = employee_grid.locator(
                    ".e-filterbar input.e-input"
                ).nth(1)
                await name_filter.fill(unicodedata.normalize("NFC", assignee))
                await name_filter.press("Enter")
                dialog, employee_grid = await _visible_employee_grid(
                    self.page, self.session.config.timeout_ms
                )
                employee_rows = employee_grid.locator(".e-gridcontent tr.e-row")
                await _row_with_normalized_text(
                    employee_rows, assignee, self.session.config.timeout_ms
                )
                if not await _row_has_selected_task(
                    employee_rows, assignee, self.session.config.task
                ):
                    await _click_row_with_normalized_text(
                        employee_rows, assignee, self.session.config.timeout_ms
                    )
                    row = await _row_with_normalized_text(
                        employee_rows, assignee, self.session.config.timeout_ms
                    )
                    tree_input = row.locator(".vue-treeselect__input")
                    await tree_input.click()
                    portal = self.page.locator(
                        ".vue-treeselect__portal-target.vue-treeselect--open"
                    )
                    await portal.first.wait_for(state="attached", timeout=5_000)
                    await tree_input.fill(self.session.config.task)
                    task_label = await _visible(
                        portal.get_by_text(self.session.config.task, exact=True),
                        timeout_ms=8_000,
                    )
                    await task_label.hover()
                    await tree_input.press("Enter")
                deadline = asyncio.get_running_loop().time() + 8
                while asyncio.get_running_loop().time() < deadline:
                    if await _row_has_selected_task(
                        employee_rows, assignee, self.session.config.task
                    ):
                        return dialog
                    await asyncio.sleep(0.25)
                raise RuntimeError("Nhiệm vụ chưa xuất hiện trên dòng nhân viên")
            except Exception as error:
                last_error = error
                await self.page.keyboard.press("Escape")
                await self.page.wait_for_timeout(500)
        raise AssignmentPopupError(
            f"Không chọn được nhiệm vụ cho {assignee} sau 3 lần thử: {last_error}"
        ) from last_error

    async def _save_and_send_sms(self, dialog: Locator) -> None:
        assert self.page
        save = await _visible(dialog.locator("a", has_text="Ghi lại"))
        await save.click()
        await self.page.wait_for_timeout(750)
        await self.page.wait_for_function(
            "() => !document.querySelector('.overlay-common.show')"
        )
        sms = await _visible(dialog.locator("a", has_text="Gửi SMS"))
        await sms.click()
        confirmation = await _visible(
            self.page.locator(".el-message-box__wrapper").filter(
                has_text="Bạn có chắc chắn gửi SMS"
            )
        )
        confirm_button = await _visible(
            confirmation.locator("button").filter(has_text="OK")
        )
        await confirm_button.click()
        await confirmation.wait_for(state="hidden")

    async def _filter_main_grid_ticket(self, ticket: Ticket) -> Locator:
        """Filter the Tất cả grid to one exact transaction/subscriber pair."""
        assert self.page
        await self.clear_main_grid_filters()
        grid = await self._main_grid()
        inputs = grid.locator(".e-filterbar input.e-input")
        if await inputs.count() < 2:
            raise RuntimeError("Không tìm thấy ô lọc Mã giao dịch/Mã thuê bao")
        await inputs.nth(0).fill(ticket.transaction_id)
        await inputs.nth(0).press("Enter")
        await self.page.wait_for_timeout(700)
        if ticket.subscriber_id:
            grid = await self._main_grid()
            inputs = grid.locator(".e-filterbar input.e-input")
            await inputs.nth(1).fill(ticket.subscriber_id)
            await inputs.nth(1).press("Enter")
            await self.page.wait_for_timeout(700)
        return await self._ticket_row(ticket)

    async def reassign(
        self, ticket: Ticket, previous_assignee: str, new_assignee: str
    ) -> None:
        """Replace one existing employee while preserving all other assignees."""
        assert self.page
        for popup_attempt in range(2):
            try:
                await _close_assignment_dialogs(self.page)
                await self.ensure_assignment_filters("Tất cả")
                await self.clear_main_grid_filters()
                await self.refresh_tickets()
                grid = await self._main_grid()
                checkboxes = grid.locator(".e-gridcontent input.e-checkselect")
                for index in range(await checkboxes.count()):
                    checkbox = checkboxes.nth(index)
                    if await checkbox.is_checked():
                        await _set_hidden_checkbox(checkbox, False)
                row = await self._filter_main_grid_ticket(ticket)
                checkbox = row.locator("input.e-checkselect")
                if not await checkbox.is_checked():
                    await _set_hidden_checkbox(checkbox, True)
                await self.page.wait_for_timeout(750)
                await self.page.wait_for_function(
                    "() => !document.querySelector('.overlay-common.show')"
                )
                action = await _visible(
                    self.page.locator(
                        "#frmGiaoViecVIP .list-actions-top a", has_text="Giao việc"
                    )
                )
                await action.click()
                dialog, _employee_grid = await _visible_employee_grid(
                    self.page, self.session.config.timeout_ms
                )
                selected_rows = dialog.locator(".e-grid").first.locator(
                    ".e-gridcontent tr.e-row"
                )
                selected_text = " ".join(await selected_rows.all_inner_texts())
                if ticket.transaction_id not in selected_text or (
                    ticket.subscriber_id and ticket.subscriber_id not in selected_text
                ):
                    raise AssignmentPopupError(
                        "Phiếu trong popup không khớp lệnh giao lại"
                    )

                assigned_grid = await _visible_assigned_grid(
                    dialog, self.session.config.timeout_ms
                )
                assigned_rows = assigned_grid.locator(".e-gridcontent tr.e-row")
                snapshot = await _assigned_row_snapshot(assigned_rows)
                normalized_new = normalize(new_assignee)
                normalized_previous = normalize(previous_assignee)
                has_new = any(
                    item["visible"] and normalized_new in normalize(item["text"])
                    for item in snapshot
                )
                has_previous = any(
                    item["visible"]
                    and normalized_previous in normalize(item["text"])
                    for item in snapshot
                )

                if has_previous:
                    old_row = await _assigned_row_with_name(
                        assigned_rows, previous_assignee, self.session.config.timeout_ms
                    )
                    # The red X is a div.btn-danger on the current OneBSS UI,
                    # not a native button. Keep fallbacks for older versions.
                    remove = old_row.locator(
                        ".btn-danger, button, a, input[type='button']"
                    ).last
                    if not await remove.count():
                        raise RuntimeError(
                            f"Không tìm thấy nút Xóa của {previous_assignee}"
                        )
                    await remove.click()
                    # Some OneBSS versions ask for confirmation, others delete
                    # immediately. Accept only a currently visible confirmation.
                    await self.page.wait_for_timeout(300)
                    confirms = self.page.locator(".el-message-box__wrapper")
                    for index in range(await confirms.count()):
                        confirm = confirms.nth(index)
                        if not await confirm.is_visible():
                            continue
                        ok = confirm.locator("button", has_text="OK")
                        if await ok.count():
                            await ok.last.click()
                            await confirm.wait_for(state="hidden")
                            break
                    # Deletion is a separate OneBSS operation. Never add the
                    # replacement until the old row has actually disappeared.
                    deadline = asyncio.get_running_loop().time() + 10
                    while asyncio.get_running_loop().time() < deadline:
                        dialog, _employee_grid = await _visible_employee_grid(
                            self.page, self.session.config.timeout_ms
                        )
                        assigned_grid = await _visible_assigned_grid(
                            dialog, self.session.config.timeout_ms
                        )
                        assigned_rows = assigned_grid.locator(
                            ".e-gridcontent tr.e-row"
                        )
                        if not await _assigned_name_count(
                            assigned_rows, previous_assignee
                        ):
                            break
                        await self.page.wait_for_timeout(250)
                    else:
                        raise RuntimeError(
                            f"OneBSS chưa xóa {previous_assignee}; không thêm người mới"
                        )

                if not has_new:
                    dialog = await self._select_single_assignee(new_assignee)
                    await self._save_and_send_sms(dialog)

                # Treat reassignment as successful only when the live lower
                # grid contains the replacement exactly once and no old row.
                deadline = asyncio.get_running_loop().time() + 10
                while asyncio.get_running_loop().time() < deadline:
                    dialog, _employee_grid = await _visible_employee_grid(
                        self.page, self.session.config.timeout_ms
                    )
                    assigned_grid = await _visible_assigned_grid(
                        dialog, self.session.config.timeout_ms
                    )
                    assigned_rows = assigned_grid.locator(
                        ".e-gridcontent tr.e-row"
                    )
                    old_count = await _assigned_name_count(
                        assigned_rows, previous_assignee
                    )
                    new_count = await _assigned_name_count(
                        assigned_rows, new_assignee
                    )
                    if old_count == 0 and new_count == 1:
                        break
                    await self.page.wait_for_timeout(250)
                else:
                    raise RuntimeError(
                        "OneBSS chưa xác nhận giao lại: "
                        f"{previous_assignee}={old_count}, {new_assignee}={new_count}"
                    )
                await _close_assignment_dialogs(self.page)
                return
            except Exception as error:
                await _close_assignment_dialogs(self.page)
                if popup_attempt:
                    raise RuntimeError(
                        f"Không giao lại được phiếu {ticket.key}: {error}"
                    ) from error
                await self.page.wait_for_timeout(600)

    async def _assign_once(self, assignments: list[Assignment]) -> None:
        if not assignments:
            return
        assert self.page
        assignees = assignments[0].assignees
        if any(item.assignees != assignees for item in assignments):
            raise ValueError("Một lô chỉ được chứa cùng danh sách người nhận")

        # A successful save/SMS can still leave the Syncfusion dialog mounted
        # for a moment. Never let that stale dialog cover the next batch.
        await _close_assignment_dialogs(self.page)

        # Never inherit a selection from the previous batch.
        grid = await self._main_grid()
        checkboxes = grid.locator(".e-gridcontent input.e-checkselect")
        for index in range(await checkboxes.count()):
            checkbox = checkboxes.nth(index)
            if await checkbox.is_checked():
                # Syncfusion visually replaces this input with a styled span,
                # leaving the real checkbox hidden. A normal Playwright click
                # therefore waits forever for the input to become visible.
                await _set_hidden_checkbox(checkbox, False)

        for assignment in assignments:
            row = await self._ticket_row(assignment.ticket)
            checkbox = row.locator("input.e-checkselect")
            if not await checkbox.is_checked():
                await _set_hidden_checkbox(checkbox, True)

        # Refuse to click through an open filter/loading overlay. Clicking the
        # action with force here could assign a stale or unintended selection.
        # Row-selection updates create the loading overlay asynchronously, so
        # give it time to appear before waiting for its removal.
        await self.page.wait_for_timeout(750)
        await self.page.wait_for_function(
            """() => !Array.from(document.querySelectorAll(
                '.overlay-common.show'
              )).some(element => {
                const style = getComputedStyle(element);
                return style.display !== 'none' && style.visibility !== 'hidden';
              })"""
        )

        action = await _visible(
            self.page.locator("#frmGiaoViecVIP .list-actions-top a", has_text="Giao việc")
        )
        await action.click()
        try:
            dialog, employee_grid = await _visible_employee_grid(
                self.page, self.session.config.timeout_ms
            )
        except Exception as error:
            raise AssignmentPopupError(
                f"Popup Giao việc không tải được bảng nhân viên: {error}"
            ) from error
        selected_rows = dialog.locator(".e-grid").first.locator(".e-gridcontent tr.e-row")
        selected_texts = await selected_rows.all_inner_texts()
        expected = {
            (item.ticket.transaction_id, item.ticket.subscriber_id) for item in assignments
        }
        matched = {
            pair for pair in expected
            if any(pair[0] in text and pair[1] in text for text in selected_texts)
        }
        if len(selected_texts) != len(assignments) or matched != expected:
            raise AssignmentPopupError(
                "Danh sách phiếu trong popup không khớp lô dự kiến; đã hủy để tránh giao trùng"
            )
        employee_rows = employee_grid.locator(".e-gridcontent tr.e-row")
        # The dialog shell is displayed before its employee API/render is
        # complete. Do not treat that temporary empty grid as "not found".
        await employee_rows.first.wait_for(state="visible")

        for assignee_index, assignee in enumerate(assignees):
            last_error: Exception | None = None
            for _attempt in range(1, 4):
                checkbox_dispatched = False
                try:
                    # Every failed tree interaction can re-render the whole
                    # employee grid. Reacquire the grid, filter and rows on
                    # every attempt instead of reusing stale Vue state.
                    dialog, employee_grid = await _visible_employee_grid(
                        self.page, self.session.config.timeout_ms
                    )
                    employee_rows = employee_grid.locator(".e-gridcontent tr.e-row")
                    name_filter = employee_grid.locator(
                        ".e-filterbar input.e-input"
                    ).nth(1)
                    await name_filter.fill(unicodedata.normalize("NFC", assignee))
                    await name_filter.press("Enter")
                    # Filtering replaces the dialog/grid subtree. Reacquire
                    # both before locating and typing into the task control.
                    dialog, employee_grid = await _visible_employee_grid(
                        self.page, self.session.config.timeout_ms
                    )
                    employee_rows = employee_grid.locator(".e-gridcontent tr.e-row")
                    await _row_with_normalized_text(
                        employee_rows, assignee, self.session.config.timeout_ms
                    )
                    # The previous attempt may actually have selected the task
                    # before Vue detached its DOM. Treat verified state as
                    # success instead of clicking again.
                    if await _row_has_selected_task(
                        employee_rows, assignee, self.session.config.task
                    ):
                        last_error = None
                        break
                    await _click_row_with_normalized_text(
                        employee_rows, assignee, self.session.config.timeout_ms
                    )
                    row = await _row_with_normalized_text(
                        employee_rows, assignee, self.session.config.timeout_ms
                    )
                    tree_input = row.locator(".vue-treeselect__input")
                    await tree_input.click()
                    portal = self.page.locator(
                        ".vue-treeselect__portal-target.vue-treeselect--open"
                    )
                    await portal.first.wait_for(state="attached", timeout=5_000)
                    await tree_input.fill(self.session.config.task)
                    # Typing filters the tree to the exact task. Select it by
                    # keyboard so Vue receives trusted input events and no
                    # detached option node is clicked.
                    task_label = await _visible(
                        portal.get_by_text(self.session.config.task, exact=True),
                        timeout_ms=8_000,
                    )
                    # Hovering the exact result makes it the highlighted tree
                    # option; Enter then commits that option without clicking
                    # a DOM node that Vue is about to replace.
                    await task_label.hover()
                    await tree_input.press("Enter")
                    deadline = asyncio.get_running_loop().time() + 8
                    while asyncio.get_running_loop().time() < deadline:
                        if await _row_has_selected_task(
                            employee_rows, assignee, self.session.config.task
                        ):
                            checkbox_dispatched = True
                            break
                        await asyncio.sleep(0.25)
                    else:
                        raise RuntimeError("Nhiệm vụ chưa xuất hiện trên dòng nhân viên")
                    last_error = None
                    break
                except Exception as error:
                    last_error = error
                    if checkbox_dispatched:
                        # Do not re-filter after mutating the task selection;
                        # OneBSS discards unsaved task state when the row is
                        # rebuilt. Stop rather than silently saving no task.
                        break
                    await self.page.keyboard.press("Escape")
                    await self.page.wait_for_timeout(500)
            if last_error is not None:
                raise AssignmentPopupError(
                    f"Không chọn được nhiệm vụ cho {assignee} sau 3 lần thử: {last_error}"
                ) from last_error
            # Keep the final selected row intact until "Ghi lại". Clearing the
            # filter re-fetches the grid and loses its unsaved task value.
            if assignee_index < len(assignees) - 1:
                name_filter = employee_grid.locator(".e-filterbar input.e-input").nth(1)
                await name_filter.fill("")
                await name_filter.press("Enter")

        save = await _visible(dialog.locator("a", has_text="Ghi lại"))
        await save.click()
        # Saving re-renders names using OneBSS's own Unicode representation.
        # Waiting for the Excel spelling can time out even after a successful
        # save. Wait for the save/loading cycle instead.
        await self.page.wait_for_timeout(750)
        await self.page.wait_for_function(
            "() => !document.querySelector('.overlay-common.show')"
        )

        # Requirement: click Send SMS and accept the website's confirmation,
        # but do not wait for or infer delivery success.
        sms = await _visible(dialog.locator("a", has_text="Gửi SMS"))
        await sms.click()
        confirmation = await _visible(
            self.page.locator(".el-message-box__wrapper").filter(
                has_text="Bạn có chắc chắn gửi SMS"
            )
        )
        confirm_button = await _visible(
            confirmation.locator("button").filter(has_text="OK")
        )
        await confirm_button.click()
        await confirmation.wait_for(state="hidden")

        await _close_assignment_dialogs(self.page)


class GoogleSheetClient:
    def __init__(self, session: BrowserSession):
        self.session = session
        self.page: Page | None = None

    async def open(self) -> Page:
        page = await self.session.page_for("docs.google.com/spreadsheets")
        if self.session.config.sheet_url.split("/edit", 1)[0] not in page.url:
            await page.goto(self.session.config.sheet_url, wait_until="domcontentloaded")
        self.page = page
        print("Đăng nhập Google trong cửa sổ Chromium nếu được yêu cầu...")
        # Google Sheets has used both an id and a class for this container.
        # #docs-editor is the stable outer shell across those variants.
        await page.wait_for_selector("#docs-editor, .docs-sheet-container, #waffle-grid-container", timeout=0)
        return page

    async def activate_sheet(self, sheet_name: str | None = None) -> None:
        """Activate the exact visible sheet tab and verify it became active."""
        page = self.page or await self.open()
        names = page.locator(".docs-sheet-tab-name")
        wanted_name = sheet_name or self.session.config.sheet_name
        wanted = normalize(wanted_name)
        target: Locator | None = None
        for index in range(await names.count()):
            candidate = names.nth(index)
            if await candidate.is_visible() and normalize(await candidate.inner_text()) == wanted:
                target = candidate
                break
        if target is None:
            raise RuntimeError(f"Không tìm thấy tab {wanted_name}")
        tab = target.locator(
            "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), "
            "' docs-sheet-tab ')][1]"
        )
        await tab.click()
        await self.page.wait_for_function(
            "element => element.classList.contains('docs-sheet-active-tab')",
            arg=await tab.element_handle(),
        )

    async def assignment_source(
        self, score_member_names: tuple[str, ...] = (),
    ) -> tuple[dict[str, tuple[str, ...]], dict[str, Decimal]]:
        """Load historical routing, current-month identities, and monthly scores."""
        page = self.page or await self.open()
        tab_names = [
            text.strip()
            for text in await page.locator(".docs-sheet-tab-name").all_inner_texts()
            if text.strip()
        ]
        monthly_names = [
            name for name in tab_names
            if re.fullmatch(r"tháng\s+\d{1,2}/\d{4}", normalize(name))
        ]
        current_name = _sheet_name_for_timestamp(
            "",
            self.session.config.sheet_name_template,
            self.session.config.timezone,
        )
        if not any(normalize(name) == normalize(current_name) for name in monthly_names):
            monthly_names.append(current_name)

        exported: list[tuple[str, list[list[str]]]] = []
        for name in monthly_names:
            exported.append((name, await self.export_rows(name)))
        current_rows = next(
            (rows for name, rows in exported if normalize(name) == normalize(current_name)),
            [],
        )
        assignments = SheetAssignmentSource(
            _sheet_assignment_index(exported),
            _sheet_assignment_index([(current_name, current_rows)]),
        )
        scores = _sheet_month_scores(current_rows, score_member_names)
        print(
            f"Đã đọc Google Sheet làm dữ liệu chuẩn: {len(assignments)} mã phiếu, "
            f"{len(scores)} nhân sự có điểm tháng."
        )
        return assignments, scores

    async def append(self, assignments: Iterable[Assignment]) -> None:
        grouped: dict[str, tuple[list[list[str]], list[str]]] = {}
        for assignment in assignments:
            if not assignment.write_to_sheet:
                continue
            for assignee in assignment.assignees:
                values = {
                    "transaction_id": assignment.ticket.transaction_id,
                    "subscriber_id": assignment.ticket.subscriber_id,
                    "service": assignment.sheet_service or assignment.ticket.service_type or assignment.ticket.service,
                    "assignee": assignee,
                    "points": str(assignment.points_per_person),
                    "subscriber_name": assignment.ticket.subscriber_name,
                    "contract_type": assignment.ticket.contract_type,
                    "labor_address": assignment.ticket.labor_address,
                    "labor_province": assignment.ticket.labor_province,
                    "project_name": assignment.project_name,
                    "reassignment": (
                        "Giao lại"
                        if (
                            assignment.sheet_existing
                            if assignment.sheet_reassignment is None
                            else assignment.sheet_reassignment
                        ) else ""
                    ),
                }
                sheet_name = _sheet_name_for_timestamp(
                    assignment.sheet_timestamp,
                    self.session.config.sheet_name_template,
                    self.session.config.timezone,
                )
                rows, timestamps = grouped.setdefault(sheet_name, ([], []))
                rows.append([values[column] for column in self.session.config.sheet_columns])
                timestamps.append(assignment.sheet_timestamp)
        for sheet_name, (rows, timestamps) in grouped.items():
            await self.append_rows(rows, timestamps, sheet_name)

    async def append_records(self, records: Iterable[dict[str, str]]) -> None:
        records = list(records)
        grouped: dict[str, list[dict[str, str]]] = {}
        for record in records:
            sheet_name = _sheet_name_for_timestamp(
                record.get("sheet_timestamp", ""),
                self.session.config.sheet_name_template,
                self.session.config.timezone,
            )
            grouped.setdefault(sheet_name, []).append(record)
        for sheet_name, items in grouped.items():
            rows = [[record[column] for column in self.session.config.sheet_columns] for record in items]
            timestamps = [record.get("sheet_timestamp", "") for record in items]
            ordinals = [int(record.get("sheet_ordinal", 1)) for record in items]
            await self.append_rows(rows, timestamps, sheet_name, ordinals)

    async def append_rows(
        self, rows: list[list[str]], timestamps: list[str] | None = None,
        sheet_name: str | None = None,
        ordinals: list[int] | None = None,
    ) -> None:
        if not rows:
            return
        page = self.page or await self.open()
        sheet_name = sheet_name or self.session.config.sheet_name
        await self.activate_sheet(sheet_name)
        grid_candidates = page.locator(".docs-sheet-container, #docs-sheet-container, #waffle-grid-container")
        grid = await _visible(grid_candidates)
        timestamps = timestamps or [""] * len(rows)
        now_timestamp = datetime.now(ZoneInfo(self.session.config.timezone)).strftime(
            "%d/%m/%Y %H:%M"
        )
        timestamps = [value or now_timestamp for value in timestamps]
        if ordinals is None:
            ordinals = []
            input_counts: Counter[tuple[str, str, str, str, str]] = Counter()
            for row, stamp in zip(rows, timestamps, strict=True):
                identity = (
                    stamp,
                    row[0].strip(),
                    _sheet_subscriber_key(row[1]),
                    normalize(row[2]),
                    normalize(row[3]),
                )
                input_counts[identity] += 1
                ordinals.append(input_counts[identity])
        keyed_rows = list(zip(rows, timestamps, ordinals, strict=True))
        columns = self.session.config.sheet_columns
        invalid = [
            row for row, _stamp, _ordinal in keyed_rows
            if len(row) != len(columns)
        ]
        if invalid:
            raise RuntimeError(
                "Dữ liệu Google Sheet không khớp số cột đã cấu hình"
            )

        expected_rows = [
            (_sheet_assignment_key(stamp, row, columns), row, stamp, ordinal)
            for row, stamp, ordinal in keyed_rows
        ]
        last_missing = list(expected_rows)

        def existing_counts(exported: list[list[str]]) -> Counter[tuple[str, ...]]:
            return Counter(
                _sheet_assignment_key(
                    cell_row[0],
                    [
                        cell_row[index + 1] if len(cell_row) > index + 1 else ""
                        for index in range(len(columns))
                    ],
                    columns,
                )
                for cell_row in exported if _is_sheet_data_row(cell_row)
            )

        def missing_rows(
            counts: Counter[tuple[str, ...]],
        ) -> list[tuple[tuple[str, ...], list[str], str, int]]:
            return [
                item for item in expected_rows if counts[item[0]] < item[3]
            ]

        for attempt in range(1, 4):
            existing = await self.export_rows(sheet_name)
            existing_data = [
                cell_row for cell_row in existing if _is_sheet_data_row(cell_row)
            ]
            last_missing = missing_rows(existing_counts(existing_data))
            if not last_missing:
                return

            # GViz CSV removes trailing blank rows. Assignment data is kept
            # contiguous from data_start_row, so its count identifies the
            # first free physical row without relying on the visible viewport.
            start_row = self.session.config.sheet_data_start_row + len(existing_data)
            sheet_rows = [
                [stamp] + [
                    _sheet_text_input(value)
                    if column in {"transaction_id", "subscriber_id"}
                    else value
                    for column, value in zip(columns, row, strict=True)
                ]
                for _key, row, stamp, _ordinal in last_missing
            ]
            gid = page.url.rsplit("gid=", 1)[1].split("&", 1)[0].split("#", 1)[0]
            base = self.session.config.sheet_url.split("/edit", 1)[0]
            await page.goto(
                f"{base}/edit?gid={gid}#gid={gid}&range=A{start_row}",
                wait_until="domcontentloaded",
            )
            await page.wait_for_selector("#docs-editor")
            await page.wait_for_timeout(800)
            name_box = await _visible(page.locator("#t-name-box"))
            if await name_box.input_value() != f"A{start_row}":
                raise RuntimeError(
                    f"Google Sheet không có hàng {start_row}; cần bổ sung thêm hàng"
                )
            tsv = "\n".join(
                "\t".join(
                    value.replace("\t", " ").replace("\n", " ") for value in row
                )
                for row in sheet_rows
            )
            await page.evaluate("text => navigator.clipboard.writeText(text)", tsv)
            await page.keyboard.press("Meta+V")

            # Verify only the rows pasted in this attempt. A failed/partial
            # paste is retried at the newly calculated first free row, so a
            # successful row can never be appended twice.
            attempt_deadline = (
                asyncio.get_running_loop().time()
                + self.session.config.timeout_ms / 1000
            )
            while asyncio.get_running_loop().time() < attempt_deadline:
                await asyncio.sleep(1)
                current = await self.export_rows(sheet_name)
                last_missing = missing_rows(existing_counts(current))
                if not last_missing:
                    return
            if attempt < 3:
                print(
                    f"Google Sheet còn thiếu {len(last_missing)} dòng; "
                    f"thử ghi lại lần {attempt + 1}/3..."
                )

        details = ", ".join(
            f"{key[1]}/{key[2]} → {row[3]} (lần {ordinal})"
            for key, row, _stamp, ordinal in last_missing
        )
        raise RuntimeError(
            f"Google Sheet chưa xác nhận {len(last_missing)} dòng sau 3 lần ghi: "
            + details
        )

    async def ensure_rows_below(self, last_row: int, count: int) -> None:
        """Extend a sheet whose last data row is also its maximum row."""
        page = self.page or await self.open()
        name_box = await _visible(page.locator("#t-name-box"))
        # If the first target already exists, no structural edit is needed.
        await name_box.fill(f"A{last_row + 1}")
        await name_box.press("Enter")
        await page.wait_for_timeout(200)
        if await name_box.input_value() == f"A{last_row + 1}":
            return
        current_last = last_row
        for _ in range(count):
            await name_box.fill(f"A{current_last}")
            await name_box.press("Enter")
            await page.locator("#docs-insert-menu").click()
            main_items = page.locator('[role="menu"]:visible [role="menuitem"]')
            await main_items.nth(1).hover()  # Hàng / Rows
            submenus = page.locator('[role="menu"]:visible')
            await submenus.last.locator('[role="menuitem"]').last.click()
            current_last += 1
            await page.wait_for_timeout(100)

    async def export_rows(self, sheet_name: str | None = None) -> list[list[str]]:
        """Read the configured tab through Google's authenticated CSV view."""
        assert self.session.context
        sheet_id = self.session.config.sheet_url.split("/d/", 1)[1].split("/", 1)[0]
        url = (
            f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq"
            f"?tqx=out:csv&sheet={quote(sheet_name or self.session.config.sheet_name)}"
            f"&_={time.time_ns()}"
        )
        response = await self.session.context.request.get(url)
        if not response.ok:
            raise RuntimeError(f"Không đọc được Google Sheet (HTTP {response.status})")
        return list(csv.reader(io.StringIO(await response.text())))

    async def replace_assignee(
        self, record: Mapping[str, str], new_assignee: str
    ) -> None:
        """Replace the existing Sheet assignee; safe to call repeatedly."""
        page = self.page or await self.open()
        timestamp = record.get("sheet_timestamp", "")
        sheet_name = _sheet_name_for_timestamp(
            timestamp,
            self.session.config.sheet_name_template,
            self.session.config.timezone,
        )
        await self.activate_sheet(sheet_name)
        rows = await self.export_rows(sheet_name)
        data_rows = [row for row in rows if _is_sheet_data_row(row)]
        transaction_id = record["transaction_id"].strip()
        subscriber_id = _sheet_subscriber_key(record.get("subscriber_id", ""))
        previous = normalize(record["current_assignee"])
        wanted = normalize(new_assignee)

        def same_ticket(row: list[str]) -> bool:
            return (
                len(row) >= 5
                and row[1].strip() == transaction_id
                and _sheet_subscriber_key(row[2]) == subscriber_id
                and (not timestamp or row[0].strip() == timestamp)
            )

        matching = [
            (index, row) for index, row in enumerate(data_rows)
            if same_ticket(row)
        ]
        if any(normalize(row[4]) == wanted for _, row in matching):
            return
        target = next(
            ((index, row) for index, row in reversed(matching)
             if normalize(row[4]) == previous),
            None,
        )
        if target is None:
            raise RuntimeError(
                "Không tìm thấy dòng Google Sheet tương ứng với người nhận cũ"
            )
        physical_row = self.session.config.sheet_data_start_row + target[0]
        gid = page.url.rsplit("gid=", 1)[1].split("&", 1)[0].split("#", 1)[0]
        base = self.session.config.sheet_url.split("/edit", 1)[0]
        await page.goto(
            f"{base}/edit?gid={gid}#gid={gid}&range=E{physical_row}",
            wait_until="domcontentloaded",
        )
        await page.wait_for_selector("#docs-editor")
        cell_address = f"E{physical_row}"
        for attempt in range(1, 4):
            # Far-away rows are loaded lazily. Google may display an error if
            # editing starts before that region is ready; dismiss it, wait,
            # and reselect the exact cell before each bounded retry.
            await page.wait_for_timeout(2_500)
            loading_errors = page.locator("[role='dialog']").filter(
                has_text="Các ô này hiện đang được tải"
            )
            for index in range(await loading_errors.count()):
                loading_error = loading_errors.nth(index)
                if await loading_error.is_visible():
                    ok = loading_error.get_by_text("OK", exact=True)
                    if await ok.count():
                        await ok.click()
                        await loading_error.wait_for(state="hidden")
            name_box = await _visible(page.locator("#t-name-box"))
            await name_box.fill(cell_address)
            await name_box.press("Enter")
            await page.wait_for_timeout(750)
            if await name_box.input_value() != cell_address:
                raise RuntimeError("Google Sheet không chọn đúng ô người thực hiện")

            # Assignment columns use data-validation dropdowns. Choosing the
            # configured option is more reliable than pasting into a lazily
            # loaded cell and also prevents an invalid employee spelling.
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(300)
            options = page.locator(
                ".waffle-data-validation-auto-complete-row",
                has_text=new_assignee,
            )
            selected = False
            for index in range(await options.count()):
                option = options.nth(index)
                if (
                    await option.is_visible()
                    and normalize(await option.inner_text()) == wanted
                ):
                    await option.click()
                    selected = True
                    break
            if not selected:
                await page.keyboard.press("Escape")
                await page.evaluate(
                    "text => navigator.clipboard.writeText(text)", new_assignee
                )
                await page.keyboard.press("Meta+V")
                await page.keyboard.press("Enter")

            deadline = asyncio.get_running_loop().time() + 10
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(1)
                current = await self.export_rows(sheet_name)
                if any(
                    same_ticket(row) and normalize(row[4]) == wanted
                    for row in current if _is_sheet_data_row(row)
                ):
                    return
            if attempt < 3:
                await page.reload(wait_until="domcontentloaded")
                await page.wait_for_selector("#docs-editor")
        raise RuntimeError("Google Sheet chưa xác nhận người nhận mới")
