from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .config import Config


class DashboardClient:
    """Small Supabase REST client used only by the local trusted agent."""

    def __init__(self, config: Config):
        self.config = config
        service_key = getattr(config, "dashboard_service_key", "").strip()
        normalized_key = service_key.upper().replace("-", "_")
        configured = bool(getattr(config, "dashboard_enabled", False))
        self.disabled_reason = (
            "đã tạm tắt trong config.toml; ATS-OneBSS chỉ ghi Google Sheet"
            if not configured else ""
        )
        if configured and service_key and (
            "SERVICE_ROLE_KEY_CUA_BAN" in normalized_key
            or "DAN_KHOA_SERVICE_ROLE" in normalized_key
        ):
            self.disabled_reason = (
                "biến ATS_ONEBSS_SUPABASE_SERVICE_KEY vẫn là nội dung minh họa, "
                "chưa phải service_role key thật"
            )
        self.enabled = bool(
            configured
            and getattr(config, "dashboard_url", "")
            and service_key
            and not self.disabled_reason
        )

    def _headers(self, prefer: str = "return=representation") -> dict[str, str]:
        return {
            "apikey": self.config.dashboard_service_key,
            "Authorization": f"Bearer {self.config.dashboard_service_key}",
            "Content-Type": "application/json",
            "Prefer": prefer,
        }

    async def _request(
        self, method: str, path: str, payload: Any = None,
        prefer: str = "return=representation",
    ) -> Any:
        if not self.enabled:
            return None

        def send() -> Any:
            body = None if payload is None else json.dumps(payload).encode("utf-8")
            last_error: Exception | None = None
            for attempt in range(3):
                request = Request(
                    f"{self.config.dashboard_url}{path}", body,
                    self._headers(prefer), method=method,
                )
                try:
                    with urlopen(request, timeout=30) as response:
                        text = response.read().decode("utf-8")
                    return json.loads(text) if text else None
                except HTTPError as error:
                    detail = error.read().decode("utf-8", errors="replace")
                    if error.code < 500:
                        raise RuntimeError(
                            f"Dashboard trả lỗi HTTP {error.code}: {detail[:500]}"
                        ) from error
                    last_error = RuntimeError(
                        f"Dashboard trả lỗi HTTP {error.code}: {detail[:500]}"
                    )
                except URLError as error:
                    last_error = RuntimeError(
                        f"Không kết nối được Dashboard: {error.reason}"
                    )
                if attempt < 2:
                    import time
                    time.sleep(2)
            assert last_error is not None
            raise last_error

        return await asyncio.to_thread(send)

    def _assigned_at(self, timestamp: str) -> str:
        if timestamp:
            value = datetime.strptime(timestamp, "%d/%m/%Y %H:%M")
            return value.replace(tzinfo=ZoneInfo(self.config.timezone)).isoformat()
        return datetime.now(ZoneInfo(self.config.timezone)).isoformat()

    async def upsert_assignments(self, records: list[dict[str, str]]) -> bool:
        if not self.enabled or not records:
            return False
        payload = [
            {
                "ledger_key": item["ledger_key"],
                "original_assignee": item["original_assignee"],
                "current_assignee": item["current_assignee"],
                "transaction_id": item["transaction_id"],
                "subscriber_id": item["subscriber_id"],
                "service": item["service"],
                "points": item["points"],
                "project_name": item.get("project_name", ""),
                "source": "automatic",
                "assigned_at": self._assigned_at(item.get("sheet_timestamp", "")),
                "google_sheet_synced": bool(item.get("sheet_saved")),
                "updated_at": datetime.now(ZoneInfo(self.config.timezone)).isoformat(),
            }
            for item in records
        ]
        await self._request(
            "POST",
            "/rest/v1/onebss_assignments?on_conflict=ledger_key,original_assignee",
            payload,
            prefer="resolution=merge-duplicates,return=minimal",
        )
        return True

    async def heartbeat(
        self, status: str = "online", command_id: str | None = None,
        error: str = "",
    ) -> None:
        if not self.enabled:
            return
        payload = {
            "worker_id": self.config.dashboard_worker_id,
            "hostname": self.config.dashboard_worker_id,
            "status": status,
            "current_command_id": command_id,
            "app_version": "0.2.0",
            "last_error": error[:1000],
            "last_seen": datetime.now(ZoneInfo(self.config.timezone)).isoformat(),
        }
        try:
            await self._request(
                "POST", "/rest/v1/onebss_workers?on_conflict=worker_id", payload,
                prefer="resolution=merge-duplicates,return=minimal",
            )
        except Exception as error_value:
            # A heartbeat is informative only; its failure must never interrupt
            # an OneBSS assignment that is already in progress.
            print(f"Chưa cập nhật được trạng thái agent lên Dashboard: {error_value}")

    async def claim_command(self) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        rows = await self._request(
            "POST", "/rest/v1/rpc/claim_onebss_reassignment",
            {"claiming_worker_id": self.config.dashboard_worker_id},
        )
        return rows[0] if rows else None

    async def assignment(self, assignment_id: str) -> dict[str, Any]:
        rows = await self._request(
            "GET",
            "/rest/v1/onebss_assignments?id=eq."
            + quote(assignment_id, safe="") + "&select=*",
        )
        if not rows:
            raise RuntimeError("Phiếu trên Dashboard không còn tồn tại")
        return rows[0]

    async def finish_command(
        self, command_id: str, status: str, *, error: str = "",
        result: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "status": status,
            "error_message": error[:2000],
            "result": result or {},
        }
        if status in {"completed", "failed"}:
            payload["completed_at"] = datetime.now(
                ZoneInfo(self.config.timezone)
            ).isoformat()
        if status == "queued":
            payload.update({"worker_id": None, "picked_at": None})
        await self._request(
            "PATCH",
            "/rest/v1/onebss_reassignment_commands?id=eq."
            + quote(command_id, safe=""),
            payload,
            prefer="return=minimal",
        )

    async def complete_reassignment(
        self, assignment_id: str, new_assignee: str
    ) -> None:
        await self._request(
            "PATCH",
            "/rest/v1/onebss_assignments?id=eq." + quote(assignment_id, safe=""),
            {
                "current_assignee": new_assignee,
                "last_reassigned_at": datetime.now(
                    ZoneInfo(self.config.timezone)
                ).isoformat(),
                "google_sheet_synced": True,
                "updated_at": datetime.now(
                    ZoneInfo(self.config.timezone)
                ).isoformat(),
            },
            prefer="return=minimal",
        )
