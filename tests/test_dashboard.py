import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from ats_onebss.dashboard import DashboardClient


def config(**overrides):
    values = {
        "dashboard_url": "https://project.supabase.co",
        "dashboard_service_key": "secret",
        "dashboard_enabled": True,
        "dashboard_worker_id": "worker-1",
        "timezone": "Asia/Ho_Chi_Minh",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_upsert_assignment_preserves_stable_original_identity(monkeypatch):
    client = DashboardClient(config())
    calls = []

    async def fake_request(method, path, payload=None, prefer="return=representation"):
        calls.append((method, path, payload, prefer))

    monkeypatch.setattr(client, "_request", fake_request)
    uploaded = asyncio.run(client.upsert_assignments([{
            "ledger_key": "GD|TB",
            "transaction_id": "GD",
            "subscriber_id": "TB",
            "service": "Fiber",
            "original_assignee": "Người A",
            "current_assignee": "Người B",
            "points": "17",
            "project_name": "",
            "sheet_timestamp": "03/09/2026 08:15",
            "sheet_saved": 1,
        }]))

    assert uploaded is True
    assert calls[0][1].endswith("on_conflict=ledger_key,original_assignee")
    row = calls[0][2][0]
    assert row["original_assignee"] == "Người A"
    assert row["current_assignee"] == "Người B"
    assert row["google_sheet_synced"] is True
    assert datetime.fromisoformat(row["assigned_at"]).astimezone(
        ZoneInfo("Asia/Ho_Chi_Minh")
    ).strftime("%d/%m/%Y %H:%M") == "03/09/2026 08:15"


def test_dashboard_is_optional_without_local_service_key():
    client = DashboardClient(config(dashboard_service_key=""))
    assert client.enabled is False


def test_dashboard_can_be_explicitly_disabled():
    client = DashboardClient(config(dashboard_enabled=False))
    assert client.enabled is False
    assert "chỉ ghi Google Sheet" in client.disabled_reason


def test_dashboard_rejects_documentation_placeholder_key():
    client = DashboardClient(
        config(dashboard_service_key="SERVICE_ROLE_KEY_CUA_BAN")
    )
    assert client.enabled is False
    assert "minh họa" in client.disabled_reason
