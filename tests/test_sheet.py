import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from ats_onebss.browser import (
    GoogleSheetClient,
    _sheet_assignment_index,
    _sheet_assignment_key,
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


def test_sheet_assignment_key_ignores_sheet_text_formatting_differences():
    columns = (
        "transaction_id", "subscriber_id", "service", "assignee",
        "subscriber_name", "contract_type", "labor_address", "labor_province",
    )
    local_row = [
        "VNP-LD/00079409", "02838888191", "SIP", "Nguyễn Hoàng Dương",
        "Ngân Hàng TNHH Mtv Shinhan Việt Nam", "Lắp đặt mới",
        "''''Tầng 3, Tòa nhà A", "TP Hồ Chí Minh",
    ]
    sheet_row = [*local_row]
    sheet_row[6] = "'''Tầng 3, Tòa nhà A"

    assert _sheet_assignment_key("02/10/2026 15:56", local_row, columns) == (
        _sheet_assignment_key("02/10/2026 15:56", sheet_row, columns)
    )


def test_append_records_marks_already_present_rows_without_pasting():
    columns = (
        "transaction_id", "subscriber_id", "service", "assignee",
        "subscriber_name", "contract_type", "labor_address", "labor_province",
    )
    config = SimpleNamespace(
        sheet_columns=columns,
        sheet_name="Tháng 10/2026",
        sheet_name_template="Tháng {month}/{year}",
        timezone="Asia/Ho_Chi_Minh",
        sheet_data_start_row=9,
    )
    client = GoogleSheetClient(SimpleNamespace(config=config))
    page = SimpleNamespace(locator=Mock(), url="https://docs.google.com/spreadsheets/d/id/edit?gid=4")
    page.goto = AsyncMock()
    client.page = page
    client.activate_sheet = AsyncMock()
    existing_row = [
        "02/10/2026 15:56", "VNP-LD/00079409", "02838888191", "SIP",
        "Nguyễn Hoàng Dương", "Ngân Hàng TNHH Mtv Shinhan Việt Nam",
        "Lắp đặt mới", "'''Tầng 3, Tòa nhà A", "TP Hồ Chí Minh",
    ]
    client.export_rows = AsyncMock(return_value=[existing_row])

    with patch("ats_onebss.browser._visible", new=AsyncMock(return_value=object())):
        asyncio.run(client.append_records([{
            "sheet_timestamp": "02/10/2026 15:56",
            "sheet_ordinal": "1",
            "transaction_id": "VNP-LD/00079409",
            "subscriber_id": "02838888191",
            "service": "SIP",
            "assignee": "Nguyễn Hoàng Dương",
            "subscriber_name": "Ngân Hàng TNHH Mtv Shinhan Việt Nam",
            "contract_type": "Lắp đặt mới",
            "labor_address": "''''Tầng 3, Tòa nhà A",
            "labor_province": "TP Hồ Chí Minh",
        }]))

    client.export_rows.assert_awaited_once_with("Tháng 10/2026")
    client.activate_sheet.assert_awaited_once_with("Tháng 10/2026")
    page.goto.assert_not_awaited()


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


def test_google_sheet_append_marks_existing_ticket_in_column_k():
    config = SimpleNamespace(
        sheet_columns=(
            "transaction_id", "subscriber_id", "service", "assignee",
            "subscriber_name", "contract_type", "labor_address",
            "labor_province", "project_name", "reassignment",
        ),
        sheet_name_template="Tháng {month}/{year}",
        timezone="Asia/Ho_Chi_Minh",
    )
    client = GoogleSheetClient(SimpleNamespace(config=config))
    client.append_rows = AsyncMock()
    assignment = Assignment(
        Ticket("GD1", "TB1", "Fiber"), ("An",), Decimal("17"), 3,
        sheet_existing=True, sheet_timestamp="30/09/2026 14:00",
    )

    asyncio.run(client.append([assignment]))

    assert client.append_rows.await_args.args[0][0][-1] == "Giao lại"
