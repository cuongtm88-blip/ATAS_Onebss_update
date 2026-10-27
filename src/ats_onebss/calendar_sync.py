from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Iterable

from .attendance import load_calendar, save_calendar, validate_calendar

CALENDAR_COLUMNS = (
    "event_id", "operation", "record_type", "record_key", "date", "status",
    "member", "start_date", "end_date", "session", "updated_at",
)
HEADER = list(CALENDAR_COLUMNS)


def _records(calendar: dict[str, Any]) -> dict[str, tuple[str, dict[str, str]]]:
    result: dict[str, tuple[str, dict[str, str]]] = {}
    for record_type in ("overrides", "leave"):
        for item in calendar[record_type]:
            payload = dict(item)
            raw = json.dumps(
                [record_type, payload], ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            result[key] = (record_type, payload)
    return result


def _event_row(operation: str, key: str, record_type: str, payload: dict[str, str]) -> list[str]:
    values = {field: "" for field in CALENDAR_COLUMNS}
    values.update({
        "event_id": str(uuid.uuid4()),
        "operation": operation,
        "record_type": record_type,
        "record_key": key,
        **payload,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    return [values[column] for column in CALENDAR_COLUMNS]


def _cloud_state(rows: Iterable[list[str]]) -> dict[str, tuple[str, dict[str, str]]]:
    state: dict[str, tuple[str, dict[str, str]]] = {}
    for row in rows:
        padded = (row + [""] * len(CALENDAR_COLUMNS))[:len(CALENDAR_COLUMNS)]
        values = dict(zip(CALENDAR_COLUMNS, padded, strict=True))
        if values["record_type"] not in {"overrides", "leave"}:
            continue
        key = values["record_key"]
        if not key:
            continue
        if values["operation"] == "delete":
            state.pop(key, None)
        elif values["operation"] == "put":
            fields = (
                ("date", "status") if values["record_type"] == "overrides"
                else ("member", "start_date", "end_date", "session")
            )
            state[key] = (
                values["record_type"],
                {field: values[field] for field in fields},
            )
    return state


def _calendar_from_records(records: dict[str, tuple[str, dict[str, str]]]) -> dict[str, Any]:
    data = {"version": 1, "overrides": [], "leave": []}
    for record_type, payload in records.values():
        data[record_type].append(payload)
    return validate_calendar(data)


async def sync_calendar(
    local_path: str | Path,
    baseline_path: str | Path,
    sheet_rows: list[list[str]],
    append_rows: Callable[[list[list[str]]], Awaitable[Any]],
) -> dict[str, Any]:
    """Merge local edits with the append-only regional Sheet event log."""
    local = load_calendar(local_path)
    baseline = load_calendar(baseline_path)
    local_records = _records(local)
    baseline_records = _records(baseline)
    cloud_records = _cloud_state(sheet_rows)

    events: list[list[str]] = []
    for key in baseline_records.keys() - local_records.keys():
        record_type, payload = baseline_records[key]
        events.append(_event_row("delete", key, record_type, payload))
        cloud_records.pop(key, None)
    for key in local_records.keys() - baseline_records.keys():
        record_type, payload = local_records[key]
        events.append(_event_row("put", key, record_type, payload))
        cloud_records[key] = (record_type, payload)

    if events:
        await append_rows(events)

    merged = _calendar_from_records(cloud_records)
    save_calendar(local_path, merged)
    save_calendar(baseline_path, merged)
    return merged

