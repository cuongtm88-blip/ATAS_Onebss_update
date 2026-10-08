import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ats_onebss.browser import (
    BrowserProfileInUseError,
    BrowserSession,
    OneBSSClient,
    _contract_type_from_api,
    _installation_type_from_api,
    _province_from_address,
    _project_details_from_api,
    _ticket_metadata_from_api,
)
from ats_onebss.models import ProjectRoute, ProjectRule, Ticket


def test_profile_lock_prevents_two_sessions(tmp_path: Path):
    first = BrowserSession(SimpleNamespace(browser_profile=tmp_path / "profile"))
    second = BrowserSession(SimpleNamespace(browser_profile=tmp_path / "profile"))
    first.config.browser_profile.mkdir(parents=True)
    first._acquire_profile_lock()
    try:
        try:
            second._acquire_profile_lock()
        except BrowserProfileInUseError:
            pass
        else:
            raise AssertionError("expected profile ownership error")
    finally:
        first._release_profile_lock()


def test_browser_session_can_restart_context_without_releasing_profile_lock():
    class FakeContext:
        def __init__(self):
            self.close = AsyncMock()
            self.set_default_timeout = lambda _timeout: None
            self.grant_permissions = AsyncMock()

    class FakeChromium:
        async def launch_persistent_context(self, **_kwargs):
            return replacement

    replacement = FakeContext()
    previous = FakeContext()
    session = BrowserSession(SimpleNamespace(
        browser_profile=Path("/tmp/profile"),
        browser_channel="chrome",
        headless=False,
        timeout_ms=30000,
    ))
    session.context = previous
    session.playwright = SimpleNamespace(chromium=FakeChromium())
    session._profile_lock = object()

    asyncio.run(session.restart_context())

    previous.close.assert_awaited_once()
    replacement.grant_permissions.assert_awaited_once()
    assert session.context is replacement
    assert session._profile_lock is not None


def test_onebss_reload_for_recovery_rebuilds_unassigned_queue():
    page = SimpleNamespace(
        is_closed=lambda: False,
        reload=AsyncMock(),
        wait_for_selector=AsyncMock(),
        wait_for_timeout=AsyncMock(),
    )
    config = SimpleNamespace(timeout_ms=30000)
    client = OneBSSClient(SimpleNamespace(config=config))
    client.page = page
    client.ensure_unassigned_filters = AsyncMock()
    client.refresh_tickets = AsyncMock()

    asyncio.run(client.reload_for_recovery())

    page.reload.assert_awaited_once_with(
        wait_until="domcontentloaded", timeout=30000
    )
    page.wait_for_selector.assert_awaited_once_with(
        "#frmGiaoViecVIP", timeout=30000
    )
    client.ensure_unassigned_filters.assert_awaited_once()
    client.refresh_tickets.assert_awaited_once()


def test_reads_contract_type_from_raw_onebss_response():
    payload = {
        "data": [
            {
                "maGdBan": "GD-KHAC",
                "maTbBan": "TB-KHAC",
                "tenLoaiHD": "Lắp đặt mới",
            },
            {
                "maGdBan": "CBG-GH/00013381",
                "maTbBan": "domain.vn",
                "maLoaiHD": "123",
                "tenLoaiHD": "Thay đổi thông tin - Gia hạn dịch vụ CNTT",
                "kieuLapDat": "Gia hạn và thay đổi gói cước CNTT/GTGT",
            },
        ]
    }
    ticket = Ticket("CBG-GH/00013381", "domain.vn", "Tên miền Việt Nam")
    assert _contract_type_from_api(payload, ticket) == (
        "Thay đổi thông tin - Gia hạn dịch vụ CNTT"
    )


def test_never_uses_installation_type_as_contract_type():
    payload = {
        "data": [{
            "maGdBan": "GD1",
            "maTbBan": "TB1",
            "kieuLapDat": "Gia hạn và thay đổi gói cước CNTT/GTGT",
        }]
    }
    assert _contract_type_from_api(payload, Ticket("GD1", "TB1", "Fiber")) == ""


def test_reads_installation_type_separately_from_raw_onebss_response():
    payload = {
        "data": [{
            "maGdBan": "CBG-GH/00013381",
            "maTbBan": "domain.vn",
            "tenLoaiHD": "Thay đổi thông tin - Gia hạn dịch vụ CNTT",
            "kieuLapDat": "Gia hạn và thay đổi gói cước CNTT/GTGT",
        }]
    }
    ticket = Ticket("CBG-GH/00013381", "domain.vn", "Tên miền Việt Nam")

    assert _installation_type_from_api(payload, ticket) == (
        "Gia hạn và thay đổi gói cước CNTT/GTGT"
    )


def test_reads_project_customer_and_labor_address_from_raw_response():
    payload = {"data": [{
        "maGdBan": "VNP-LD/00077698",
        "maTbBan": "llk0009b6",
        "tenKH": "Trung Tâm Quản Lý, Điều Hành Mạng (noc) - Chi Nhánh Tổng Công Ty Viễn Thông Mobifone",
        "diaChiLD": "Tòa nhà Mobifone Số 1, Phạm Văn Bạch, Thành phố Hà Nội",
    }]}
    ticket = Ticket("VNP-LD/00077698", "llk0009b6", "Kênh thuê riêng")
    assert _project_details_from_api(payload, ticket) == (
        "Trung Tâm Quản Lý, Điều Hành Mạng (noc) - Chi Nhánh Tổng Công Ty Viễn Thông Mobifone",
        "Tòa nhà Mobifone Số 1, Phạm Văn Bạch, Thành phố Hà Nội",
        "",
    )


def test_reads_connection_address_from_raw_onebss_response():
    payload = {"data": [{
        "maGdBan": "VNP-TD/00097957",
        "maTbBan": "MW000020934",
        "tenKH": "Tổng Cục Thuế",
        "diaChiLD": "Chi cục Thuế Cát Hải",
        "diaChiKN": "Cục Thuế TP. Hải Phòng, Tỉnh Hải Phòng",
    }]}
    ticket = Ticket("VNP-TD/00097957", "MW000020934", "Megawan")
    assert _project_details_from_api(payload, ticket) == (
        "Tổng Cục Thuế",
        "Chi cục Thuế Cát Hải",
        "Cục Thuế TP. Hải Phòng, Tỉnh Hải Phòng",
    )


def test_reads_sheet_metadata_from_raw_onebss_response():
    payload = {"data": [{
        "maGdBan": "00052711",
        "maTbBan": "",
        "tenTB": "CÔNG TY CỔ PHẦN THƯƠNG MẠI DỊCH VỤ VIỄN THÔNG RVC",
        "tenKH": "Khách hàng RVC",
        "ghiChu": "Kênh phục vụ HNTH",
        "diaChiLD": "BigC - 255-257 Hùng Vương, Tp Đà Nẵng",
        "tinhLD": "ĐNG",
    }]}

    values = _ticket_metadata_from_api(
        payload, Ticket("00052711", "", "Fiber")
    )

    assert values["subscriber_name"].startswith("CÔNG TY CỔ PHẦN")
    assert values["customer_name"] == "Khách hàng RVC"
    assert values["notes"] == "Kênh phục vụ HNTH"
    assert values["labor_address"].endswith("Tp Đà Nẵng")
    assert values["labor_province"] == "ĐNG"


def test_prefers_complete_api_metadata_for_project_predicates():
    """A short grid record must not hide the fuller matching detail record."""
    payload = {"data": [
        {
            "maGdBan": "VNP-LD/00078722",
            "maTbBan": "mwk00108j",
            "tenKH": "Ngân hàng Nhà nước khu vực 3",
        },
        {
            "maGdBan": "VNP-LD/00078722",
            "maTbBan": "mwk00108j",
            "tenKH": "Cục Quản Trị Ngân Hàng Nhà Nước Việt Nam",
            "ghiChu": "Kênh phục vụ HNTH kết nối đến MCU",
            "diaChiLD": "Tỉnh Điện Biên",
        },
    ]}

    values = _ticket_metadata_from_api(
        payload, Ticket("VNP-LD/00078722", "mwk00108j", "Megawan LT")
    )

    assert values["customer_name"] == "Cục Quản Trị Ngân Hàng Nhà Nước Việt Nam"
    assert values["notes"].startswith("Kênh phục vụ HNTH")


def test_infers_missing_labor_province_from_explicit_address():
    assert _province_from_address(
        "Toà nhà Elcom, Phường Cầu Giấy, Thành phố Hà Nội"
    ) == "Thành phố Hà Nội"
    assert _province_from_address("Điểm lắp đặt tại Tỉnh Khánh Hòa") == (
        "Tỉnh Khánh Hòa"
    )


def test_detail_enrichment_reads_subscriber_for_ticket_without_subscriber_id():
    class Cells:
        async def evaluate_all(self, _script):
            return [{
                "text": "00052711",
                "label": "00052711 column header Mã giao dịch bán",
            }]

    class Row:
        click = AsyncMock()

        def locator(self, selector):
            assert selector == "td"
            return Cells()

    client = OneBSSClient(SimpleNamespace())
    client.page = SimpleNamespace(wait_for_timeout=AsyncMock())
    client._ticket_row = AsyncMock(return_value=Row())
    client._detail_value = AsyncMock(side_effect=lambda label: {
        "Tên KH": "Khách hàng RVC",
        "Tên TB": "Thuê bao RVC",
        "Địa chỉ LĐ": "BigC, Tp Đà Nẵng",
    }.get(label, ""))
    ticket = Ticket(
        "00052711", "", "Fiber",
        labor_address="BigC, Tp Đà Nẵng",
    )

    enriched = asyncio.run(client.enrich_ticket_details([ticket], []))

    assert enriched[0].subscriber_name == "Thuê bao RVC"
    assert enriched[0].labor_province == "Tp Đà Nẵng"
    Row.click.assert_awaited_once()


def test_detail_enrichment_validates_sale_subscriber_not_construction_code():
    class Cells:
        async def evaluate_all(self, _script):
            return [{
                "text": "MN001008776",
                "label": "MN001008776 column header Mã thuê bao bán",
            }]

    class Row:
        click = AsyncMock()

        def locator(self, selector):
            assert selector == "td"
            return Cells()

    client = OneBSSClient(SimpleNamespace())
    client.page = SimpleNamespace(wait_for_timeout=AsyncMock())
    client._ticket_row = AsyncMock(return_value=Row())

    async def detail_value(label):
        assert label != "Mã TB thi công"
        return {
            "Tên KH": "Cục Viễn Thông & Cơ Yếu Bca",
            "Địa chỉ LĐ": "Thị xã Sơn Tây, Hà Nội, Việt Nam",
        }.get(label, "")

    client._detail_value = AsyncMock(side_effect=detail_value)
    client._technical_value = AsyncMock(side_effect=lambda label: {
        "Mã VNPT": "MN001008776",
        "Địa chỉ KN": "Điểm kết nối tại Hà Nội",
    }.get(label, ""))
    ticket = Ticket(
        "VNP-KP/00001068",
        "MN001008776",
        "Metronet",
        subscriber_name="Cục Viễn Thông Và Cơ Yếu-bộ Công An",
    )
    project = ProjectRule(
        name="Dự án BCA",
        contains="Cục Viễn Thông & Cơ Yếu Bca",
        match_fields=("customer_name", "subscriber_name"),
        priority=90,
        route_field="labor_address",
        routes=(ProjectRoute("Đào Anh Vũ", ("Hà Nội",)),),
    )

    enriched = asyncio.run(client.enrich_ticket_details([ticket], [project]))

    assert enriched[0].customer_name == "Cục Viễn Thông & Cơ Yếu Bca"
    assert enriched[0].labor_address == "Thị xã Sơn Tây, Hà Nội, Việt Nam"
    assert enriched[0].connection_address == ""
    client._technical_value.assert_not_awaited()
    client.page.wait_for_timeout.assert_not_awaited()


def test_detail_enrichment_verifies_note_qualified_project_from_form():
    """A non-empty grid customer must not bypass an HNTH form check."""
    class Cells:
        async def evaluate_all(self, _script):
            return [{
                "text": "mwk00109h",
                "label": "mwk00109h column header Mã thuê bao bán",
            }]

    class Row:
        click = AsyncMock()

        def locator(self, selector):
            assert selector == "td"
            return Cells()

    client = OneBSSClient(SimpleNamespace())
    client.page = SimpleNamespace(wait_for_timeout=AsyncMock())
    client._ticket_row = AsyncMock(return_value=Row())
    client._detail_value = AsyncMock(side_effect=lambda label: {
        "Tên KH": "Cục Quản Trị Ngân Hàng Nhà Nước Việt Nam",
        "Tên TB": "Ngân hàng nhà nước khu vực 7",
        "Ghi chú": "Kênh phục vụ HNTH kết nối đến MCU",
        "Địa chỉ LĐ": "Tỉnh Thanh Hóa",
    }.get(label, ""))
    ticket = Ticket(
        "VNP-LD/00078730", "mwk00109h", "Megawan LT",
        customer_name="Ngân hàng nhà nước khu vực 7",
        subscriber_name="Ngân hàng nhà nước khu vực 7",
    )
    project = ProjectRule(
        name="Cục Quản Trị NHNN - Kênh phục vụ HNTH",
        contains="Cục Quản Trị Ngân Hàng Nhà Nước Việt Nam",
        match_fields=("customer_name",),
        priority=95,
        fixed_assignee="Nguyễn Hoàng Dương",
        required_contains=(("notes", "Kênh phục vụ HNTH"),),
    )

    enriched = asyncio.run(client.enrich_ticket_details([ticket], [project]))

    assert enriched[0].customer_name == "Cục Quản Trị Ngân Hàng Nhà Nước Việt Nam"
    assert enriched[0].notes.startswith("Kênh phục vụ HNTH")
    Row.click.assert_awaited_once()


def test_detail_enrichment_keeps_labor_province_as_project_fallback():
    class Cells:
        async def evaluate_all(self, _script):
            return [{
                "text": "MW000020934",
                "label": "MW000020934 column header Mã thuê bao bán",
            }]

    class Row:
        click = AsyncMock()

        def locator(self, selector):
            assert selector == "td"
            return Cells()

    client = OneBSSClient(SimpleNamespace())
    client.page = SimpleNamespace(wait_for_timeout=AsyncMock())
    client._ticket_row = AsyncMock(return_value=Row())
    client._detail_value = AsyncMock(side_effect=lambda label: {
        "Tên KH": "Cục Viễn Thông & Cơ Yếu Bca",
        "Địa chỉ LĐ": "Điểm lắp đặt Cát Hải",
    }.get(label, ""))
    client._technical_value = AsyncMock(side_effect=lambda label: {
        "Mã VNPT": "MW000020934",
        "Địa chỉ KN": "Cục Thuế TP. Hải Phòng, Tỉnh Hải Phòng",
    }.get(label, ""))
    ticket = Ticket(
        "VNP-TD/00097957", "MW000020934", "Megawan",
        subscriber_name="Cục Viễn Thông & Cơ Yếu Bca",
        labor_province="Tp Hải Phòng",
    )
    project = ProjectRule(
        name="Dự án BCA",
        contains="Cục Viễn Thông & Cơ Yếu Bca",
        match_fields=("customer_name",),
        priority=90,
        route_field="labor_address",
        routes=(ProjectRoute("Vũ Thế Ninh", ("Hải Phòng",)),),
    )

    enriched = asyncio.run(client.enrich_ticket_details([ticket], [project]))

    assert enriched[0].labor_address == "Điểm lắp đặt Cát Hải"
    assert enriched[0].labor_province == "Tp Hải Phòng"
    assert enriched[0].connection_address == ""
    client._technical_value.assert_not_awaited()
