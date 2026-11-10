from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


EMPTY_CALENDAR: dict[str, Any] = {"version": 1, "overrides": [], "leave": []}
CALENDAR_STATUSES = {"holiday", "workday"}
LEAVE_SESSIONS = {"full", "morning", "afternoon"}


def _iso_date(value: object, label: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} không đúng định dạng YYYY-MM-DD.") from error


def validate_calendar(data: object) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Lịch làm việc phải là một đối tượng JSON.")
    overrides = data.get("overrides", [])
    leave = data.get("leave", [])
    if not isinstance(overrides, list) or not isinstance(leave, list):
        raise ValueError("Lịch phải có danh sách overrides và leave.")

    cleaned_overrides: list[dict[str, str]] = []
    override_dates: set[str] = set()
    for item in overrides:
        if not isinstance(item, dict):
            raise ValueError("Một dòng ngày nghỉ/ngày làm bù không hợp lệ.")
        day = _iso_date(item.get("date"), "Ngày lịch")
        status = str(item.get("status", ""))
        if status not in CALENDAR_STATUSES:
            raise ValueError(f"Trạng thái ngày {day} không hợp lệ.")
        if day in override_dates:
            raise ValueError(f"Ngày {day} xuất hiện nhiều lần trong lịch.")
        override_dates.add(day)
        cleaned_overrides.append({"date": day, "status": status})

    cleaned_leave: list[dict[str, str]] = []
    intervals: dict[str, list[tuple[date, date]]] = {}
    for item in leave:
        if not isinstance(item, dict):
            raise ValueError("Một dòng nghỉ phép không hợp lệ.")
        member = str(item.get("member", "")).strip()
        if not member:
            raise ValueError("Nghỉ phép cần có tên nhân sự.")
        start = date.fromisoformat(_iso_date(item.get("start_date"), "Ngày bắt đầu"))
        end = date.fromisoformat(_iso_date(item.get("end_date"), "Ngày kết thúc"))
        if end < start:
            raise ValueError(f"Ngày kết thúc nghỉ của {member} trước ngày bắt đầu.")
        session = str(item.get("session", ""))
        if session not in LEAVE_SESSIONS:
            raise ValueError(f"Buổi nghỉ của {member} không hợp lệ.")
        cleaned_item = {
            "member": member,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "session": session,
        }
        raw_percent = item.get("deduction_percent")
        if raw_percent not in (None, ""):
            try:
                deduction_percent = Decimal(str(raw_percent))
            except (InvalidOperation, TypeError, ValueError) as error:
                raise ValueError(
                    f"Mức trừ/ngày công của {member} không hợp lệ."
                ) from error
            if not deduction_percent.is_finite() or not (
                Decimal(0) <= deduction_percent <= Decimal(100)
            ):
                raise ValueError(
                    f"Mức trừ/ngày công của {member} phải từ 0 đến 100%."
                )
            cleaned_item["deduction_percent"] = format(
                deduction_percent.normalize(), "f"
            )
        member_key = member.casefold()
        for previous_start, previous_end in intervals.setdefault(member_key, []):
            if start <= previous_end and previous_start <= end:
                raise ValueError(
                    f"Lịch nghỉ của {member} bị trùng ngày; hãy gộp hoặc sửa các dòng."
                )
        intervals[member_key].append((start, end))
        cleaned_leave.append(cleaned_item)
    cleaned_overrides.sort(key=lambda item: item["date"])
    cleaned_leave.sort(key=lambda item: (item["start_date"], item["member"].casefold()))
    return {"version": 1, "overrides": cleaned_overrides, "leave": cleaned_leave}


def load_calendar(path: str | Path | None) -> dict[str, Any]:
    if path is None or not Path(path).exists():
        return validate_calendar(EMPTY_CALENDAR)
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Không đọc được lịch làm việc {path}: {error}") from error
    return validate_calendar(raw)


def save_calendar(path: str | Path, data: object) -> dict[str, Any]:
    cleaned = validate_calendar(data)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(target)
    return cleaned


def working_dates(year: int, month: int, calendar: dict[str, Any]) -> list[date]:
    overrides = {item["date"]: item["status"] for item in calendar["overrides"]}
    first = date(year, month, 1)
    next_month = date(year + (month == 12), month % 12 + 1, 1)
    result: list[date] = []
    day = first
    while day < next_month:
        status = overrides.get(day.isoformat())
        if status == "holiday":
            pass
        elif status == "workday" or day.weekday() < 5:
            result.append(day)
        day += timedelta(days=1)
    return result


def default_leave_deduction_percent(
    year: int, month: int, calendar: dict[str, Any],
) -> Decimal:
    """Return the percentage of a monthly target represented by one workday."""
    workday_count = len(working_dates(year, month, calendar))
    if not workday_count:
        return Decimal(0)
    return Decimal(100) / Decimal(workday_count)


def monthly_availability_ratios(
    calendar: dict[str, Any], member_names: tuple[str, ...] | list[str],
    year: int, month: int, as_of: date | None = None,
) -> dict[str, Decimal]:
    """Return each member's month target after leave days reached so far.

    Future leave dates do not reduce the target early. Once a leave date is
    reached, its configured daily deduction is included in the month ratio.
    """
    workdays = working_dates(year, month, calendar)
    if not workdays:
        return {name: Decimal(1) for name in member_names}
    effective_date = as_of or date.today()
    day_set = set(workdays)
    ratios: dict[str, Decimal] = {}
    for name in member_names:
        unavailable = Decimal(0)
        for entry in calendar["leave"]:
            if entry["member"].casefold() != name.casefold():
                continue
            start = date.fromisoformat(entry["start_date"])
            end = date.fromisoformat(entry["end_date"])
            leave_units = Decimal("0.5") if entry["session"] != "full" else Decimal(1)
            deduction_percent = entry.get("deduction_percent")
            if deduction_percent is None:
                deduction_percent = default_leave_deduction_percent(
                    year, month, calendar,
                )
            else:
                deduction_percent = Decimal(deduction_percent)
            affected_workdays = sum(
                1 for day in day_set
                if start <= day <= end and day <= effective_date
            )
            unavailable += (
                leave_units * deduction_percent / Decimal(100)
                * Decimal(len(workdays)) * Decimal(affected_workdays)
            )
        ratios[name] = max(
            Decimal("0.001"),
            (Decimal(len(workdays)) - unavailable) / Decimal(len(workdays)),
        )
    return ratios


def members_on_leave(calendar: dict[str, Any], when: datetime) -> tuple[str, ...]:
    day = when.date()
    current = when.timetz().replace(tzinfo=None)
    absent: dict[str, str] = {}
    for entry in calendar["leave"]:
        if not (
            date.fromisoformat(entry["start_date"])
            <= day
            <= date.fromisoformat(entry["end_date"])
        ):
            continue
        session = entry["session"]
        active = (
            session == "full"
            or session == "morning" and time(8) <= current < time(12)
            or session == "afternoon" and time(13) <= current < time(17)
        )
        if active:
            absent[entry["member"].casefold()] = entry["member"]
    return tuple(absent.values())


def is_working_day(day: date, calendar: dict[str, Any]) -> bool:
    overrides = {item["date"]: item["status"] for item in calendar["overrides"]}
    status = overrides.get(day.isoformat())
    if status == "holiday":
        return False
    if status == "workday":
        return True
    return day.weekday() < 5


def is_working_session(when: datetime) -> bool:
    current = when.timetz().replace(tzinfo=None)
    return time(8) <= current < time(12) or time(13) <= current < time(17)
