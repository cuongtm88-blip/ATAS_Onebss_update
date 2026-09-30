from decimal import Decimal

from ats_onebss.ledger import Ledger
from ats_onebss.models import Assignment, Ticket
from ats_onebss.text import assignment_cohort_key


def test_reappearing_ticket_creates_a_new_occurrence(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    base = Assignment(Ticket("GD1", "TB1", "Fiber"), ("An",), Decimal("17"), 3)

    first = ledger.stage(base)
    ledger.mark(first, "onebss_saved")
    ledger.mark(first, "sheet_saved")
    second = ledger.stage(base)

    assert first.ledger_key == "GD1|TB1"
    assert second.ledger_key == "GD1|TB1#2"
    assert second.sheet_timestamp


def test_identical_same_minute_rows_receive_distinct_sheet_ordinals(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    base = Assignment(Ticket("GD1", "TB1", "Fiber"), ("An",), Decimal("17"), 3)

    first = ledger.stage(base)
    ledger.mark(first, "onebss_saved")
    second = ledger.stage(base)
    ledger.mark(second, "onebss_saved")

    pending = ledger.pending_sheet_rows()
    assert [row["sheet_ordinal"] for row in pending] == [1, 2]


def test_sheet_existing_occurrence_is_appended_as_reassignment_but_not_scored(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    repeated = ledger.stage(
        Assignment(
            Ticket("GD1", "TB1", "Fiber"),
            ("An",),
            Decimal("17"),
            3,
            sheet_existing=True,
        )
    )
    ledger.mark(repeated, "onebss_saved")

    pending = ledger.pending_sheet_rows()
    assert len(pending) == 1
    assert pending[0]["reassignment"] == "Giao lại"
    assert ledger.scores() == {}


def test_no_sheet_assignment_is_completed_without_sheet_outbox_or_score(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    assignment = ledger.stage(
        Assignment(
            Ticket("GD1", "TB1", "Thoại quốc tế"),
            ("Trần Mạnh Cường",),
            Decimal("0"),
            51,
            write_to_sheet=False,
        )
    )
    ledger.mark(assignment, "onebss_saved")

    assert ledger.pending_sheet_rows() == []
    assert ledger.scores() == {}
    with ledger.connect() as db:
        row = db.execute(
            "SELECT sheet_saved, sheet_existing FROM assignments WHERE ticket_key = ?",
            (assignment.ledger_key,),
        ).fetchone()
    assert tuple(row) == (1, 0)


def test_scores_are_limited_to_selected_month(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    with ledger.connect() as db:
        db.execute(
            """INSERT INTO assignments
               (ticket_key,transaction_id,subscriber_id,service,assignee,points,
                rule_row,onebss_saved,created_at,sheet_timestamp)
               VALUES ('A|1','A','1','Fiber','An','17',3,1,
                       '2026-08-01T00:00:00+00:00','01/08/2026 07:00')"""
        )
        db.execute(
            """INSERT INTO assignments
               (ticket_key,transaction_id,subscriber_id,service,assignee,points,
                rule_row,onebss_saved,created_at,sheet_timestamp)
               VALUES ('B|2','B','2','Fiber','An','27',3,1,
                       '2026-09-01T00:00:00+00:00','01/09/2026 07:00')"""
        )
    assert ledger.scores(2026, 8) == {"An": Decimal("17")}
    assert ledger.scores(2026, 9) == {"An": Decimal("27")}


def test_stores_named_project(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    assignment = Assignment(
        Ticket("GD1", "TB1", "Fiber"),
        ("An",),
        Decimal("17"),
        3,
        project_name="Dự án BCA",
    )
    ledger.stage(assignment)
    with ledger.connect() as db:
        value = db.execute(
            "SELECT project_name FROM assignments WHERE ticket_key = 'GD1|TB1'"
        ).fetchone()[0]
    assert value == "Dự án BCA"


def test_migrates_old_cohort_keys_to_address_grouping(tmp_path):
    path = tmp_path / "ledger.db"
    Ledger(path)
    with Ledger(path).connect() as db:
        db.execute(
            """INSERT INTO assignments
               (ticket_key,transaction_id,subscriber_id,service,assignee,points,
                rule_row,onebss_saved,created_at,customer_name,labor_province,
                labor_address,cohort_key)
               VALUES ('GD1|TB1','GD1','TB1','Fiber','An','17',3,1,'2026-09-01',
                       'Khách hàng A','Hà Nội','Số 10 Lê Lợi',
                       'old province cohort')"""
        )

    Ledger(path)

    with Ledger(path).connect() as db:
        actual = db.execute(
            "SELECT cohort_key FROM assignments WHERE ticket_key = 'GD1|TB1'"
        ).fetchone()[0]
    assert actual == assignment_cohort_key("Khách hàng A", "Số 10 Lê Lợi", "Fiber")


def test_pending_sheet_row_keeps_extended_onebss_fields(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    assignment = ledger.stage(
        Assignment(
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
            project_name="Dự án BCA",
        )
    )
    ledger.mark(assignment, "onebss_saved")

    row = ledger.pending_sheet_rows()[0]
    assert row["subscriber_name"] == "Tên thuê bao A"
    assert row["contract_type"] == "Gia hạn"
    assert row["labor_address"] == "Số 1 Trần Phú, Hà Nội"
    assert row["labor_province"] == "Thành phố Hà Nội"
    assert row["project_name"] == "Dự án BCA"


def test_dashboard_outbox_and_reassignment_transfer_points(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    assignment = ledger.stage(
        Assignment(Ticket("GD2", "TB2", "Fiber"), ("Người A",), Decimal("17"), 4)
    )
    ledger.mark(assignment, "onebss_saved")
    ledger.mark(assignment, "sheet_saved")

    pending = ledger.pending_dashboard_rows()
    assert len(pending) == 1
    assert pending[0]["original_assignee"] == "Người A"
    assert pending[0]["current_assignee"] == "Người A"
    assert pending[0]["sheet_saved"] == 1

    ledger.mark_dashboard_keys({assignment.ledger_key})
    assert ledger.pending_dashboard_rows() == []
    ledger.reassign(assignment.ledger_key, "Người A", "Người B")

    record = ledger.assignment_record(assignment.ledger_key, "Người A")
    assert record is not None
    assert record["current_assignee"] == "Người B"
    assert ledger.scores() == {"Người B": Decimal("17")}
