import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ats_onebss.browser import (
    GoogleSheetClient,
    _sheet_assignment_index,
    _sheet_name_for_timestamp,
    _sheet_month_scores,
    _is_sheet_data_row,
    _sheet_subscriber_key,
    _sheet_text_input,
)
from ats_onebss.models import Assignment, Ticket


def test_sheet_subscriber_key_treats_text_marker_as_blank():
    assert _sheet_subscriber_key("") == ""
    assert _sheet_subscriber_key("'") == ""
    assert _sheet_subscriber_key("'02433215206") == "02433215206"
    assert _sheet_subscriber_key("02433215206") == "02433215206"


def test_sheet_text_input_preserves_leading_zeroes():
    assert _sheet_text_input("00052432") == "'00052432"
    assert _sheet_text_input("") == ""


def test_sheet_name_uses_assignment_month():
    assert _sheet_name_for_timestamp(
        "01/09/2026 00:05", "Tháng {month}/{year}", "Asia/Ho_Chi_Minh"
    ) == "Tháng 9/2026"


def test_identifies_only_assignment_rows():
    assert _is_sheet_data_row([
        "03/09/2026 08:15", "VNP-LD/1", "tb1", "Fiber", "An"
    ])
    assert not _is_sheet_data_row(["", "Mã giao dịch", "Mã thuê bao", "Dịch vụ", "Người thực hiện"])
    assert not _is_sheet_data_row(["24/02/2026 14:33", "CNTT", "", "", ""])


def test_sheet_assignment_index_uses_latest_row_and_preserves_joint_assignees():
    index = _sheet_assignment_index([
        ("Tháng 8/2026", [[
            "31/08/2026 10:00", "GD1", "TB1", "Fiber", "Người cũ"
        ]]),
        ("Tháng 9/2026", [
            ["03/09/2026 09:00", "GD1", "TB1", "Fiber", "Người mới"],
            ["03/09/2026 09:00", "GD1", "TB1", "Fiber", "Người cùng làm"],
        ]),
    ])
    assert index["gd1|tb1"] == ("Người mới", "Người cùng làm")


def test_sheet_month_scores_reads_authoritative_totals():
    rows = [
        ["Nhân sự thực hiện", "An", "Bình"],
        ["Tổng phiếu giao", "2", "3"],
        ["Điểm quy đổi", "17", "29.5"],
    ]
    assert _sheet_month_scores(rows) == {
        "An": Decimal("17"), "Bình": Decimal("29.5")
    }


def test_sheet_month_scores_handles_blank_merged_labels_from_gviz():
    rows = [
        ["", "An", ""],
        ["", "2", "3"],
        ["", "17", "29"],
        ["", "58%", "100%"],
    ]
    assert _sheet_month_scores(rows, ("An", "Bình")) == {
        "An": Decimal("17"), "Bình": Decimal("29")
    }


def test_google_sheet_append_skips_assignment_excluded_by_rule():
    client = GoogleSheetClient(SimpleNamespace())
    client.append_rows = AsyncMock()
    assignment = Assignment(
        Ticket("GD1", "TB1", "Thoại quốc tế"),
        ("Trần Mạnh Cường",),
        Decimal("0"),
        51,
        write_to_sheet=False,
    )

    asyncio.run(client.append([assignment]))

    client.append_rows.assert_not_awaited()


def test_google_sheet_append_maps_extended_onebss_fields():
    config = SimpleNamespace(
        sheet_columns=(
            "transaction_id", "subscriber_id", "service", "assignee",
            "subscriber_name", "contract_type", "labor_address",
            "labor_province", "project_name",
        ),
        sheet_name_template="Tháng {month}/{year}",
        timezone="Asia/Ho_Chi_Minh",
    )
    client = GoogleSheetClient(SimpleNamespace(config=config))
    client.append_rows = AsyncMock()
    assignment = Assignment(
        Ticket(
            "GD1",
            "TB1",
            "Fiber",
            contract_type="Gia hạn",
            subscriber_name="Tên thuê bao A",
            labor_address="Số 1 Trần Phú, Hà Nội",
            labor_province="Thành phố Hà Nội",
        ),
        ("An",),
        Decimal("17"),
        3,
        sheet_service="Fiber",
        project_name="Dự án BCA",
        sheet_timestamp="14/09/2026 10:30",
    )

    asyncio.run(client.append([assignment]))

    client.append_rows.assert_awaited_once_with(
        [[
            "GD1", "TB1", "Fiber", "An", "Tên thuê bao A", "Gia hạn",
            "Số 1 Trần Phú, Hà Nội", "Thành phố Hà Nội", "Dự án BCA",
        ]],
        ["14/09/2026 10:30"],
        "Tháng 9/2026",
    )
