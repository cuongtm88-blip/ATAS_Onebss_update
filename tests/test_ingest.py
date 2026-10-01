import asyncio
import json

from ats_onebss.ingest import INGEST_API_TOKEN_ENV, IngestApiClient
from ats_onebss.ledger import Ledger
from ats_onebss.models import Assignment, Ticket


def test_ingest_api_payload_uses_exact_field_names_without_status(monkeypatch):
    request_data = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"ok":true,"invalid":[]}'

    def fake_urlopen(request, timeout):
        request_data["url"] = request.full_url
        request_data["headers"] = dict(request.header_items())
        request_data["body"] = request.data
        request_data["timeout"] = timeout
        return Response()

    monkeypatch.setattr("ats_onebss.ingest.urlopen", fake_urlopen)
    monkeypatch.setenv(INGEST_API_TOKEN_ENV, "secret-token")
    client = IngestApiClient.from_environment()
    result = asyncio.run(client.push_pending([{
        "transaction_id": "VNP-LD/0001",
        "subscriber_id": "mnk001",
        "service": "Fiber",
        "assignee": "Lương Tuấn Thanh",
        "sheet_timestamp": "29/09/2026 08:34",
        "labor_province": "Hà Nội",
        "project_name": "Dự án BCA",
        "subscriber_name": "Công ty Ví dụ",
        "contract_type": "Lắp đặt mới",
        "labor_address": "Số 1 Trần Phú, Hà Nội",
    }]))

    assert result == {"ok": True, "invalid": []}
    assert request_data["url"] == "https://one-dieuhanh.fly.dev/api/ingest/tickets"
    assert request_data["headers"]["X-ingest-token"] == "secret-token"
    assert json.loads(request_data["body"].decode("utf-8")) == {
        "tickets": [{
            "Ngày giao": "29/09/2026 08:34",
            "Mã giao dịch": "VNP-LD/0001",
            "Mã thuê bao": "mnk001",
            "Dịch vụ": "Fiber",
            "Người thực hiện": "Lương Tuấn Thanh",
            "Tỉnh": "Hà Nội",
            "Tên dự án": "Dự án BCA",
            "Tên thuê bao": "Công ty Ví dụ",
            "Loại HĐ": "Lắp đặt mới",
            "Địa chỉ lắp đặt": "Số 1 Trần Phú, Hà Nội",
        }]
    }


def test_ingest_outbox_only_contains_confirmed_new_assignments(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    assignment = ledger.stage(Assignment(
        Ticket(
            "GD1", "TB1", "Fiber", labor_province="Hà Nội",
            subscriber_name="Tên thuê bao mẫu", contract_type="Lắp đặt mới",
            labor_address="Số 1 Trần Phú, Hà Nội",
        ),
        ("An",),
        17,
        3,
    ))

    assert ledger.pending_ingest_rows() == []
    ledger.mark(assignment, "onebss_saved")
    pending = ledger.pending_ingest_rows()
    assert len(pending) == 1
    assert pending[0]["transaction_id"] == "GD1"
    assert pending[0]["labor_province"] == "Hà Nội"
    assert pending[0]["subscriber_name"] == "Tên thuê bao mẫu"
    assert pending[0]["contract_type"] == "Lắp đặt mới"
    assert pending[0]["labor_address"] == "Số 1 Trần Phú, Hà Nội"
    ledger.mark_ingest_keys({assignment.ledger_key})
    assert ledger.pending_ingest_rows() == []


def test_ingest_outbox_respects_service_api_opt_in(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    opted_out = ledger.stage(Assignment(
        Ticket("GD0", "TB0", "Fiber"), ("An",), 17, 3, send_to_api=False,
    ))
    opted_in = ledger.stage(Assignment(
        Ticket("GD1", "TB1", "Fiber"), ("Bình",), 17, 3, send_to_api=True,
    ))
    ledger.mark(opted_out, "onebss_saved")
    ledger.mark(opted_in, "onebss_saved")

    pending = ledger.pending_ingest_rows()

    assert [row["transaction_id"] for row in pending] == ["GD1"]
    assert pending[0]["rule_row"] == 3


def test_existing_ledger_rows_are_not_backfilled_into_api_outbox(tmp_path):
    path = tmp_path / "legacy.db"
    ledger = Ledger(path)
    with ledger.connect() as db:
        db.execute("DROP TABLE assignments")
        db.execute("""CREATE TABLE assignments (
            ticket_key TEXT NOT NULL, transaction_id TEXT NOT NULL,
            subscriber_id TEXT NOT NULL, service TEXT NOT NULL,
            assignee TEXT NOT NULL, points TEXT NOT NULL, rule_row INTEGER NOT NULL,
            onebss_saved INTEGER NOT NULL DEFAULT 0,
            sheet_saved INTEGER NOT NULL DEFAULT 0, sms_clicked INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, PRIMARY KEY (ticket_key, assignee)
        )""")
        db.execute("""INSERT INTO assignments VALUES
            ('OLD|1','OLD','1','Fiber','An','17',3,1,1,0,'2026-09-01')""")

    migrated = Ledger(path)
    assert migrated.pending_ingest_rows() == []
