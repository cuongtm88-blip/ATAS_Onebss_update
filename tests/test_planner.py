from decimal import Decimal

from ats_onebss.models import Member, ProjectRule, ServiceRule, Ticket
from ats_onebss.planner import plan_assignments
from ats_onebss.rules import member_score_key
from ats_onebss.text import assignment_cohort_key


def test_dual_group_employee_is_balanced_against_current_service_group():
    group_1 = ServiceRule(
        3, "Fiber", Decimal("17"), "",
        (
            Member("Lê Đức Tuấn", "Nhóm 1", "Chính"),
            Member("An", "Nhóm 1", "Chính"),
        ),
    )
    group_2 = ServiceRule(
        4, "SMS Brandname", Decimal("20"), "",
        (
            Member("Lê Đức Tuấn", "Nhóm 2", "Chính"),
            Member("Bình", "Nhóm 2", "Chính"),
        ),
    )
    scores = {
        member_score_key("Nhóm 1", "Lê Đức Tuấn"): Decimal("100"),
        "An": Decimal("0"),
        member_score_key("Nhóm 2", "Lê Đức Tuấn"): Decimal("0"),
        "Bình": Decimal("100"),
    }
    result = plan_assignments(
        [
            Ticket("GD1", "TB1", "Fiber", "Fiber"),
            Ticket("GD2", "TB2", "SMS Brandname", "SMS Brandname"),
        ],
        [group_1, group_2],
        initial_scores=scores,
    )

    assigned = {item.ticket.service: item.assignees for item in result}
    assert assigned["Fiber"] == ("An",)
    assert assigned["SMS Brandname"] == ("Lê Đức Tuấn",)


def test_balances_points_and_splits_between_groups():
    rule = ServiceRule(
        3,
        "Fiber",
        Decimal("10"),
        "",
        (
            Member("An", "Nhóm 1", "Chính"),
            Member("Bình", "Nhóm 1", "Chính"),
            Member("Hoa", "Nhóm 2", "Chính"),
        ),
    )
    tickets = [Ticket(f"GD{i}", f"TB{i}", "Fiber", "Fiber") for i in range(2)]
    result = plan_assignments(tickets, [rule])
    assert result[0].assignees == ("An", "Hoa")
    assert result[1].assignees == ("Bình", "Hoa")
    assert result[0].points_per_person == Decimal("5")


def test_uses_existing_score_and_skips_completed():
    rule = ServiceRule(
        3,
        "Fiber",
        Decimal("17"),
        "",
        (Member("An", "Nhóm 1", "Chính"), Member("Bình", "Nhóm 1", "Chính")),
    )
    old = Ticket("GD0", "TB0", "Fiber", "Fiber")
    new = Ticket("GD1", "TB1", "Fiber", "Fiber")
    result = plan_assignments(
        [old, new], [rule], {"An": Decimal("20"), "Bình": Decimal("1")}, {old.key}
    )
    assert len(result) == 1
    assert result[0].assignees == ("Bình",)


def test_propagates_google_sheet_service():
    rule = ServiceRule(
        3, "ISDN 30B+D cáp đồng", Decimal("17"), "",
        (Member("An", "Nhóm 1", "Chính"),), "B-FONE",
    )
    result = plan_assignments(
        [Ticket("GD1", "TB1", "ISDN 30B+D cáp đồng")], [rule]
    )
    assert result[0].sheet_service == "B-FONE"


def test_project_rule_overrides_service_members_and_is_recorded():
    rule = ServiceRule(
        3, "Fiber", Decimal("17"), "",
        (
            Member("An", "Nhóm 1", "Chính"),
            Member("Nguyễn Hoàng Dương", "Nhóm 1", "Phụ"),
        ),
    )
    project = ProjectRule(
        name="Đài THVN",
        contains="Đài THVN",
        match_fields=("subscriber_name",),
        priority=100,
        fixed_assignee="Nguyễn Hoàng Dương",
    )
    ticket = Ticket(
        "GD1", "TB1", "Fiber", subscriber_name="Đài THVN - Trung tâm kỹ thuật"
    )
    result = plan_assignments([ticket], [rule], project_rules=[project])
    assert result[0].assignees == ("Nguyễn Hoàng Dương",)
    assert result[0].project_name == "Đài THVN"


def test_manual_assignee_overrides_project_rules_and_leave_exclusion_for_one_ticket():
    rule = ServiceRule(
        3, "Fiber", Decimal("17"), "",
        (Member("Nguyễn Duy Thành", "Nhóm 1", "Chính"),
         Member("Lê Đức Vinh", "Nhóm 1", "Chính")),
    )
    project = ProjectRule(
        name="Dự án X", contains="Dự án X", match_fields=("customer_name",),
        priority=90, fixed_assignee="Nguyễn Duy Thành",
    )
    ticket = Ticket("GD1", "TB1", "Fiber", customer_name="Dự án X")

    result = plan_assignments(
        [ticket], [rule], project_rules=[project],
        excluded_members=("Nguyễn Duy Thành",),
        manual_assignees={ticket.key: "Lê Đức Vinh"},
    )

    assert result[0].assignees == ("Lê Đức Vinh",)
    assert result[0].project_name == "Dự án X"
    assert result[0].manual_override is True


def test_excluded_member_is_removed_from_normal_balancing():
    rule = ServiceRule(
        3, "Fiber", Decimal("17"), "",
        (
            Member("An", "Nhóm 1", "Chính"),
            Member("Bình", "Nhóm 1", "Phụ"),
        ),
    )
    result = plan_assignments(
        [Ticket("GD1", "TB1", "Fiber")], [rule],
        use_backups=True, excluded_members=("An",),
    )
    assert result[0].assignees == ("Bình",)


def test_excluded_cohort_owner_falls_back_for_non_project_ticket():
    rule = ServiceRule(
        3, "Internet trực tiếp", Decimal("27"), "",
        (
            Member("An", "Nhóm 1", "Chính"),
            Member("Bình", "Nhóm 1", "Chính"),
        ),
    )
    ticket = Ticket(
        "GD1", "TB1", "Internet trực tiếp",
        customer_name="Khách hàng A", labor_address="1 Lê Lợi",
        labor_province="Thái Nguyên",
    )
    cohort = assignment_cohort_key(
        ticket.customer_name, ticket.labor_address, ticket.service
    )
    conflicts = {}
    result = plan_assignments(
        [ticket], [rule], excluded_members=("An",),
        cohort_assignees={cohort: "An"}, cohort_conflicts=conflicts,
    )

    assert result[0].assignees == ("Bình",)
    assert conflicts == {}


def test_same_customer_address_and_service_share_lowest_load_owner():
    rule = ServiceRule(
        3, "Internet trực tiếp", Decimal("27"), "",
        (Member("An", "Nhóm 1", "Chính"), Member("Bình", "Nhóm 1", "Chính")),
    )
    tickets = [
        Ticket(
            "GD1", "TB1", "Internet trực tiếp", customer_name="Khách hàng A",
            labor_address="Số 10, Lê Lợi", labor_province="Hà Nội",
        ),
        Ticket(
            "GD2", "TB2", "Internet trực tiếp", customer_name="Khách hàng A",
            labor_address="  SỐ 10,  LÊ LỢI ", labor_province="Hưng Yên",
        ),
    ]

    result = plan_assignments(
        tickets, [rule], {"An": Decimal("50"), "Bình": Decimal("0")}
    )

    assert [item.assignees for item in result] == [("Bình",), ("Bình",)]


def test_different_addresses_do_not_share_a_cohort_pin():
    rule = ServiceRule(
        3, "Internet trực tiếp", Decimal("27"), "",
        (Member("An", "Nhóm 1", "Chính"), Member("Bình", "Nhóm 1", "Chính")),
    )
    first = Ticket(
        "GD1", "TB1", "Internet trực tiếp", customer_name="Khách hàng A",
        labor_address="Số 10, Lê Lợi",
    )
    second = Ticket(
        "GD2", "TB2", "Internet trực tiếp", customer_name="Khách hàng A",
        labor_address="Số 20, Lê Lợi",
    )
    first_cohort = assignment_cohort_key(
        first.customer_name, first.labor_address, first.service
    )

    result = plan_assignments(
        [first, second], [rule], {"An": Decimal("100"), "Bình": Decimal("0")},
        cohort_assignees={first_cohort: "An"},
    )

    assert [item.assignees for item in result] == [("An",), ("Bình",)]


def test_excluded_project_owner_leaves_ticket_unassigned():
    rule = ServiceRule(
        3, "Fiber", Decimal("17"), "",
        (
            Member("An", "Nhóm 1", "Chính"),
            Member("Bình", "Nhóm 1", "Phụ"),
        ),
    )
    project = ProjectRule(
        name="Dự án cố định",
        contains="Khách hàng A",
        match_fields=("customer_name",),
        priority=100,
        fixed_assignee="An",
    )
    result = plan_assignments(
        [Ticket("GD1", "TB1", "Fiber", customer_name="Khách hàng A")],
        [rule], project_rules=[project], excluded_members=("An",),
    )
    assert result == []


def test_excluded_google_sheet_owner_leaves_returned_ticket_unassigned():
    rule = ServiceRule(
        3, "Fiber", Decimal("17"), "",
        (
            Member("An", "Nhóm 1", "Chính"),
            Member("Bình", "Nhóm 1", "Phụ"),
        ),
    )
    result = plan_assignments(
        [Ticket("GD1", "TB1", "Fiber")], [rule],
        preferred_assignees={"gd1|tb1": ("An",)},
        excluded_members=("An",),
    )
    assert result == []


def test_larger_tickets_are_planned_first_for_tighter_balance():
    members = (
        Member("An", "Nhóm 1", "Chính"),
        Member("Bình", "Nhóm 1", "Chính"),
    )
    small = ServiceRule(3, "Nhỏ", Decimal("10"), "", members)
    large = ServiceRule(4, "Lớn", Decimal("30"), "", members)
    tickets = [Ticket("GD1", "TB1", "Nhỏ"), Ticket("GD2", "TB2", "Lớn")]
    result = plan_assignments(tickets, [small, large])
    assert [item.points for item in result] == [Decimal("30"), Decimal("10")]
    assert result[0].assignees != result[1].assignees


def test_google_sheet_assignee_overrides_balancing_and_project_rule():
    rule = ServiceRule(
        3, "Fiber", Decimal("17"), "",
        (
            Member("An", "Nhóm 1", "Chính"),
            Member("Bình", "Nhóm 1", "Chính"),
        ),
    )
    project = ProjectRule(
        name="Dự án cố định",
        contains="Khách hàng A",
        match_fields=("customer_name",),
        priority=100,
        fixed_assignee="An",
    )
    ticket = Ticket(
        "GD1", "TB1", "Fiber", customer_name="Khách hàng A"
    )
    result = plan_assignments(
        [ticket], [rule], {"An": Decimal(0), "Bình": Decimal(100)},
        project_rules=[project],
        preferred_assignees={"gd1|tb1": ("Bình",)},
    )
    assert result[0].assignees == ("Bình",)
    assert result[0].sheet_existing is True


def test_google_sheet_assignee_is_canonicalized_from_excel_member_name():
    rule = ServiceRule(
        3, "Fiber", Decimal("17"), "",
        (Member("Đặng Văn Minh", "Nhóm 1", "Chính"),),
    )
    result = plan_assignments(
        [Ticket("GD1", "TB1", "Fiber")], [rule],
        preferred_assignees={"gd1|tb1": ("Đăng Văn Minh",)},
    )
    assert result[0].assignees == ("Đặng Văn Minh",)
    assert result[0].sheet_existing is True


def test_previous_month_sheet_assignee_is_kept_and_marked_as_reassignment():
    rule = ServiceRule(
        3, "Fiber", Decimal("17"), "",
        (Member("An", "Nhóm 1", "Chính"),),
    )
    result = plan_assignments(
        [Ticket("GD1", "TB1", "Fiber")], [rule],
        preferred_assignees={"gd1|tb1": ("An",)},
        sheet_existing_keys=set(),
    )

    assert result[0].assignees == ("An",)
    assert result[0].sheet_existing is True
    assert result[0].project_name == ""


def test_google_sheet_repeat_does_not_change_balance_score():
    rule = ServiceRule(
        3, "Fiber", Decimal("10"), "",
        (
            Member("An", "Nhóm 1", "Chính"),
            Member("Bình", "Nhóm 1", "Chính"),
        ),
    )
    repeated = Ticket("GD1", "TB1", "Fiber")
    fresh = Ticket("GD2", "TB2", "Fiber")
    result = plan_assignments(
        [repeated, fresh], [rule],
        preferred_assignees={"gd1|tb1": ("An",)},
    )
    fresh_assignment = next(item for item in result if item.ticket.key == fresh.key)
    assert fresh_assignment.assignees == ("An",)


def test_no_sheet_rule_ignores_historical_sheet_row_and_has_zero_points():
    rule = ServiceRule(
        3,
        "Thoại quốc tế",
        Decimal("99"),
        "Không tính điểm, không đưa vào danh sách (Vẫn phân phiếu)",
        (Member("Trần Mạnh Cường", "Điều phối", "Chính"),),
        "Thoại quốc tế",
        count_points=False,
        write_to_sheet=False,
    )
    result = plan_assignments(
        [Ticket(
            "GD1", "TB1", "Thoại quốc tế",
            customer_name="Khách hàng dự án",
        )],
        [rule],
        project_rules=[ProjectRule(
            name="Dự án trùng tên khách hàng",
            contains="Khách hàng dự án",
            match_fields=("customer_name",),
            priority=100,
            fixed_assignee="Người dự án",
        )],
        preferred_assignees={"gd1|tb1": ("Người ghi nhầm",)},
    )

    assert result[0].assignees == ("Trần Mạnh Cường",)
    assert result[0].points == Decimal("0")
    assert result[0].sheet_existing is False
    assert result[0].write_to_sheet is False


def test_primary_and_backup_are_balanced_together_by_points():
    rule = ServiceRule(
        3, "Fiber", Decimal("17"), "",
        (
            Member("Người chính", "Nhóm 1", "Chính"),
            Member("Người phụ", "Nhóm 1", "Phụ"),
        ),
        "Fiber",
    )
    result = plan_assignments(
        [Ticket("GD1", "TB1", "Fiber")], [rule],
        initial_scores={"Người chính": Decimal("100"), "Người phụ": Decimal("20")},
        use_backups=True,
    )
    assert result[0].assignees == ("Người phụ",)


def test_member_target_ratio_allows_configured_person_more_points():
    rule = ServiceRule(
        3, "Fiber", Decimal("17"), "",
        (
            Member("Người 110", "Nhóm 1", "Chính"),
            Member("Người 100", "Nhóm 1", "Phụ"),
        ),
        "Fiber",
    )
    result = plan_assignments(
        [Ticket("GD1", "TB1", "Fiber")],
        [rule],
        initial_scores={
            "Người 110": Decimal("105"),
            "Người 100": Decimal("100"),
        },
        use_backups=True,
        member_target_ratios={"Người 110": Decimal("1.10")},
    )
    assert result[0].assignees == ("Người 110",)


def test_percentage_rule_assigns_one_person_across_both_groups_by_month_ratio():
    weighted = ServiceRule(
        3, "Fiber", Decimal("17"), "",
        (
            Member("Ứng viên nhóm 1", "Nhóm 1", "Chính", Decimal("0.9")),
            Member("Ứng viên nhóm 2", "Nhóm 2", "Chính", Decimal("0.1")),
        ),
    )
    cohort_members = ServiceRule(
        4, "Khác", Decimal("17"), "",
        (
            Member("Ứng viên nhóm 1", "Nhóm 1", "Chính"),
            Member("Đồng đội nhóm 1", "Nhóm 1", "Chính"),
            Member("Ứng viên nhóm 2", "Nhóm 2", "Chính"),
            Member("Đồng đội nhóm 2", "Nhóm 2", "Chính"),
        ),
    )
    result = plan_assignments(
        [Ticket("GD1", "TB1", "Fiber")],
        [weighted, cohort_members],
        initial_scores={
            "Ứng viên nhóm 1": Decimal("150"),
            "Đồng đội nhóm 1": Decimal("50"),
            "Ứng viên nhóm 2": Decimal("80"),
            "Đồng đội nhóm 2": Decimal("120"),
        },
    )

    assert result[0].assignees == ("Ứng viên nhóm 2",)


def test_percentage_rule_uses_share_only_to_break_equal_month_ratio():
    rule = ServiceRule(
        3, "Fiber", Decimal("17"), "",
        (
            Member("Tỷ trọng cao", "Nhóm 1", "Chính", Decimal("0.7")),
            Member("Tỷ trọng thấp", "Nhóm 2", "Chính", Decimal("0.3")),
        ),
    )
    result = plan_assignments([Ticket("GD1", "TB1", "Fiber")], [rule])

    assert result[0].assignees == ("Tỷ trọng cao",)


def test_lower_adjusted_load_wins_across_primary_and_backup_roles():
    rule = ServiceRule(
        3, "Fiber", Decimal("17"), "",
        (
            Member("Chính", "Nhóm 1", "Chính"),
            Member("Phụ", "Nhóm 1", "Phụ"),
        ),
        "Fiber",
    )
    result = plan_assignments(
        [Ticket("GD1", "TB1", "Fiber")],
        [rule],
        initial_scores={
            "Chính": Decimal("50"),
            "Phụ": Decimal("100"),
        },
        use_backups=True,
    )
    assert result[0].assignees == ("Chính",)


def _voice_rule():
    return ServiceRule(
        15, "Voice Brandname", Decimal("20"), "",
        (
            Member("Lê Đức Tuấn", "Nhóm 2", "Chính"),
            Member("Đỗ Thị Thu Trang", "Nhóm 2", "Chính"),
            Member("Ngô Thùy Trang", "Nhóm 2", "Chính"),
            Member("Nguyễn Thị Thu Trang", "Nhóm 2", "Chính"),
        ),
    )


def test_returned_voice_giam_sat_overrides_google_sheet_assignee():
    ticket = Ticket(
        "V06/LD/260903/05435", "dvk0007c0", "Voice Brandname",
        vip_status="Giam sat",
    )
    result = plan_assignments(
        [ticket], [_voice_rule()],
        preferred_assignees={
            "v06/ld/260903/05435|dvk0007c0": ("Nguyễn Thị Thu Trang",),
        },
    )
    assert result[0].assignees == ("Lê Đức Tuấn",)
    assert result[0].sheet_existing is True
    assert result[0].sheet_reassignment is False


def test_returned_voice_xu_ly_keeps_google_sheet_assignee():
    ticket = Ticket(
        "GD1", "TB1", "Voice Brandname", vip_status="Xu ly",
    )
    result = plan_assignments(
        [ticket], [_voice_rule()],
        preferred_assignees={"gd1|tb1": ("Nguyễn Thị Thu Trang",)},
    )
    assert result[0].assignees == ("Nguyễn Thị Thu Trang",)
    assert result[0].sheet_existing is True
    assert result[0].sheet_reassignment is True


def test_returned_voice_giam_sat_from_le_duc_tuan_marks_reassignment():
    ticket = Ticket("GD1", "TB1", "Voice Brandname", vip_status="Giam sat")
    result = plan_assignments(
        [ticket], [_voice_rule()],
        preferred_assignees={"gd1|tb1": ("Lê Đức Tuấn",)},
    )
    assert result[0].assignees == ("Lê Đức Tuấn",)
    assert result[0].sheet_existing is True
    assert result[0].sheet_reassignment is True


def test_returned_voice_xu_ly_from_le_duc_tuan_does_not_mark_reassignment():
    ticket = Ticket("GD1", "TB1", "Voice Brandname", vip_status="Xu ly")
    result = plan_assignments(
        [ticket], [_voice_rule()],
        preferred_assignees={"gd1|tb1": ("Lê Đức Tuấn",)},
    )
    assert result[0].assignees == ("Lê Đức Tuấn",)
    assert result[0].sheet_existing is True
    assert result[0].sheet_reassignment is False


def test_percentage_services_use_excel_shares_and_do_not_pin_customer_cohorts():
    rule = ServiceRule(
        3, "SIP vendor name", Decimal("25"), "",
        (
            Member("Dương", "Nhóm 1", "Chính", Decimal("0.7")),
            Member("Hằng", "Nhóm 1", "Chính", Decimal("0.3")),
        ),
        sheet_service="SIP",
    )
    tickets = [
        Ticket(
            f"GD{i}", f"TB{i}", rule.service, customer_name="Cùng khách",
            labor_address="Cùng địa chỉ",
        )
        for i in range(10)
    ]

    result = plan_assignments(tickets, [rule])

    assert sum(item.assignees == ("Dương",) for item in result) == 7
    assert sum(item.assignees == ("Hằng",) for item in result) == 3
    assert all(item.cohort_key == "" for item in result)


def test_percentage_services_continue_monthly_share_from_sheet_history():
    rule = ServiceRule(
        3, "MegaWan vendor name", Decimal("17"), "",
        (
            Member("Dương", "Nhóm 1", "Chính", Decimal("0.7")),
            Member("Hằng", "Nhóm 1", "Chính", Decimal("0.3")),
        ),
        sheet_service="SIP",
    )
    history = {
        **{f"history-{index}": "Dương" for index in range(7)},
        **{f"history-{index + 7}": "Hằng" for index in range(3)},
    }
    result = plan_assignments(
        [Ticket(f"GD{i}", f"TB{i}", rule.service) for i in range(10)],
        [rule], service_assignments={"SIP": history},
    )

    assert sum(item.assignees == ("Dương",) for item in result) == 7
    assert sum(item.assignees == ("Hằng",) for item in result) == 3


def test_percentage_routing_does_not_change_other_services():
    rule = ServiceRule(
        3, "Fiber", Decimal("17"), "",
        (
            Member("Dương", "Nhóm 1", "Chính", Decimal("0.7")),
            Member("Hằng", "Nhóm 1", "Chính", Decimal("0.3")),
        ),
    )
    tickets = [
        Ticket(
            f"GD{i}", f"TB{i}", "Fiber", customer_name="Cùng khách",
            labor_address="Cùng địa chỉ",
        )
        for i in range(4)
    ]
    result = plan_assignments(tickets, [rule])

    assert len({item.assignees for item in result}) == 1
    assert all(item.cohort_key for item in result)
