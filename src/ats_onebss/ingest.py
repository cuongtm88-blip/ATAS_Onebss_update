from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


INGEST_API_URL = "https://one-dieuhanh.fly.dev/api/ingest/tickets"
INGEST_API_TOKEN_ENV = "ATS_ONEBSS_INGEST_API_TOKEN"


class IngestApiError(RuntimeError):
    """A safe, user-presentable error from the ticket ingestion API."""


class IngestApiClient:
    def __init__(self, token: str = "", endpoint: str = INGEST_API_URL):
        self.token = token.strip()
        self.endpoint = endpoint

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    @classmethod
    def from_environment(cls) -> "IngestApiClient":
        return cls(os.environ.get(INGEST_API_TOKEN_ENV, ""))

    async def push_pending(self, records: list[dict[str, str]]) -> dict[str, Any]:
        if not self.enabled or not records:
            return {"invalid": []}
        tickets = [
            {
                "Ngày giao": row.get("sheet_timestamp", ""),
                "Mã giao dịch": row.get("transaction_id", ""),
                "Mã thuê bao": row.get("subscriber_id", ""),
                "Dịch vụ": row.get("service", ""),
                "Người thực hiện": row.get("assignee", ""),
                "Trạng thái": "Chưa xử lý",
                "Tỉnh": row.get("labor_province", ""),
                "Tên dự án": row.get("project_name", ""),
            }
            for row in records
        ]
        return await asyncio.to_thread(self._post, tickets)

    def _post(self, tickets: list[dict[str, str]]) -> dict[str, Any]:
        body = json.dumps({"tickets": tickets}, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            headers={
                "X-Ingest-Token": self.token,
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urlopen(request, timeout=25) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict) or not payload.get("ok"):
                    raise IngestApiError("API không xác nhận đã nhận phiếu.")
                return payload
            except HTTPError as error:
                try:
                    data = json.loads(error.read().decode("utf-8", errors="replace"))
                    detail = str(data.get("error") or data.get("message") or "")
                except (ValueError, AttributeError):
                    detail = ""
                message = f"API phiếu trả HTTP {error.code}"
                if detail:
                    if self.token:
                        detail = detail.replace(self.token, "[đã ẩn token]")
                    message += f" ({detail[:160]})"
                if error.code < 500:
                    raise IngestApiError(message) from None
                last_error = IngestApiError(message)
            except (URLError, TimeoutError, OSError, ValueError) as error:
                last_error = IngestApiError(
                    f"Không kết nối/đọc được API phiếu: {type(error).__name__}"
                )
            if attempt < 2:
                time.sleep(2)
        assert last_error is not None
        raise last_error
