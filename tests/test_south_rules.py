from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from ats_onebss.models import Member, ServiceRule, Ticket
from ats_onebss.planner import plan_assignments
from ats_onebss.rules import load_project_rules, load_rules, match_project_rule
from ats_onebss import gui


ROOT = Path(__file__).parents[1]
SOUTH_RULES = load_rules(ROOT / "MN" / "Giao_phieu_MN.xlsx")
SOUTH_PROJECTS = load_project_rules(ROOT / "MN" / "project_rules_MN.toml")


def test_south_bca_routes_all_requested_locations_and_does_not_match_cap():
    routes = {
        "Nguyễn Thị Hoàng Khanh": ["Hồ Chí Minh", "Bình Dương", "Vũng Tàu", "Bà Rịa - Vũng Tàu"],
        "Cao Châu Phương Thảo": ["Đồng Nai", "Bình Phước", "Vĩnh Long", "Bến Tre", "Trà Vinh"],
        "Phan Thị Kim Ngọc": ["Cần Thơ", "Sóc Trăng", "Hậu Giang", "Cà Mau", "Bạc Liêu"],
        "Trần Thị Hải Yến": ["Tây Ninh", "Long An", "Đồng Tháp", "Tiền Giang"],
        "Nguyễn Thị Quỳnh Hoa": ["Lâm Đồng", "Đắk Nông", "Bình Thuận", "An Giang", "Kiên Giang"],
    }
    for assignee, locations in routes.items():
        for location in locations:
            match = match_project_rule(SOUTH_PROJECTS, Ticket(
                "GD", "TB", "Fiber", customer_name="CAX BCA",
                labor_address=f"Đường số 1, {location}",
            ))
            assert match is not None and match.assignee == assignee, location

    assert match_project_rule(SOUTH_PROJECTS, Ticket(
        "GD", "TB", "Fiber", customer_name="CAP",
        labor_address="Bình Dương",
    )) is None


def test_south_sip_first_thirty_go_to_uyen_and_31st_is_balanced_normally():
    tickets = [Ticket(
        "GD", f"TB{i}", "Thuê bao SIP", service_type="Thuê bao SIP",
        customer_name="Khách hàng A",
    ) for i in range(1, 32)]
    result = plan_assignments(
        tickets, SOUTH_RULES, south_special_rules=True,
        planning_day="2026-10-14",
    )
    assert all(item.assignees == ("Nguyễn Tú Uyên",) for item in result[:30])
    assert result[30].assignees != ("Nguyễn Tú Uyên",)


def test_south_sip_trunking_ims_actions_go_to_tuyet_nhung():
    for installation in ("Khóa mở dịch vụ IMS", "Mở tạm dừng sử dụng IMS"):
        result = plan_assignments(
            [Ticket(
                "GD", "TB", "Thuê bao Sip Trunking",
                service_type="Thuê bao Sip Trunking",
                installation_type=installation,
            )],
            SOUTH_RULES,
            south_special_rules=True,
            planning_day="2026-10-14",
        )
        assert result[0].assignees == ("Trần Thị Tuyết Nhung",)


def test_south_voice_brandname_suspend_cancel_restore_go_to_tuyet_nhung():
    result = plan_assignments(
        [Ticket(
            "GD", "TB", "Voice Brandname", service_type="Voice Brandname",
            vip_status="Giam sat",
            installation_type="Tạm dừng/Hủy/Khôi phục số thuê bao",
        )],
        SOUTH_RULES,
        south_special_rules=True,
        planning_day="2026-10-14",
    )
    assert result[0].assignees == ("Trần Thị Tuyết Nhung",)


def test_south_group_dv_different_services_at_same_address_share_owner():
    members = (Member("An", "Nhóm 1", "Chính"), Member("Bình", "Nhóm 1", "Chính"))
    rules = [
        ServiceRule(3, service, Decimal("17"), "", members, service)
        for service in ("Fiber", "Internet trực tiếp")
    ]
    tickets = [Ticket(
        "GD1", "TB1", "Fiber", service_type="Fiber",
        customer_name="Khách hàng A", labor_address="10 Lê Lợi",
    ), Ticket(
        "GD2", "TB2", "Internet trực tiếp", service_type="Internet trực tiếp",
        customer_name="Khách hàng A", labor_address="10 Lê Lợi",
    )]
    result = plan_assignments(
        tickets, rules, south_special_rules=True, planning_day="2026-10-14",
    )
    assert result[0].assignees == result[1].assignees


def test_sip_ticket_31_uses_sheet_count_from_same_day():
    ticket = Ticket(
        "GD", "TB31", "Thuê bao SIP", service_type="Thuê bao SIP",
        customer_name="Khách hàng A",
    )
    result = plan_assignments(
        [ticket], SOUTH_RULES,
        initial_scores={"Nguyễn Tú Uyên": Decimal("10000")},
        south_special_rules=True,
        planning_day="2026-10-14",
        sip_transaction_counts={"2026-10-14\x1fgd": 30},
    )
    assert result[0].assignees != ("Nguyễn Tú Uyên",)


def test_windows_update_refreshes_stale_south_rule_copies_once(tmp_path, monkeypatch):
    packaged = tmp_path / "package"
    runtime = tmp_path / "runtime"
    packaged_mn = packaged / "MN"
    runtime_mn = runtime / "MN"
    packaged_mn.mkdir(parents=True)
    runtime_mn.mkdir(parents=True)
    for filename in ("project_rules_MN.toml", "Giao_phieu_MN.xlsx"):
        (packaged_mn / filename).write_text("new bundled rule", encoding="utf-8")
        (runtime_mn / filename).write_text("stale rule", encoding="utf-8")
    (runtime_mn / ".bundled-rules-version").write_text("old", encoding="utf-8")
    monkeypatch.setattr(gui, "resource_root", lambda: packaged)
    monkeypatch.setattr(gui, "user_data_root", lambda: runtime)
    monkeypatch.setattr(gui, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(gui.sys, "frozen", True, raising=False)

    gui.prepare_runtime_root()

    for filename in ("project_rules_MN.toml", "Giao_phieu_MN.xlsx"):
        assert (runtime_mn / filename).read_text(encoding="utf-8") == "new bundled rule"
    assert (runtime_mn / ".bundled-rules-version").read_text(encoding="utf-8") == (
        gui.SOUTH_RULES_BUNDLE_VERSION
    )
    (runtime_mn / "project_rules_MN.toml").write_text(
        "user edited rule", encoding="utf-8"
    )
    gui.prepare_runtime_root()
    assert (runtime_mn / "project_rules_MN.toml").read_text(encoding="utf-8") == (
        "user edited rule"
    )
