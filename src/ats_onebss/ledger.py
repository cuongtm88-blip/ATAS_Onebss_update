from __future__ import annotations

import sqlite3
from dataclasses import replace
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .models import Assignment
from .text import assignment_cohort_key, normalize


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._initialize()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS assignments (
                    ticket_key TEXT NOT NULL,
                    transaction_id TEXT NOT NULL,
                    subscriber_id TEXT NOT NULL,
                    service TEXT NOT NULL,
                    assignee TEXT NOT NULL,
                    points TEXT NOT NULL,
                    rule_row INTEGER NOT NULL,
                    onebss_saved INTEGER NOT NULL DEFAULT 0,
                    sheet_saved INTEGER NOT NULL DEFAULT 0,
                    api_saved INTEGER NOT NULL DEFAULT 1,
                    sms_clicked INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (ticket_key, assignee)
                );
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(assignments)")}
            if "sheet_timestamp" not in columns:
                db.execute(
                    "ALTER TABLE assignments ADD COLUMN sheet_timestamp TEXT NOT NULL DEFAULT ''"
                )
            if "api_saved" not in columns:
                # Do not enqueue historical assignments when API sync is first
                # enabled; only assignments staged by the new version opt in.
                db.execute(
                    "ALTER TABLE assignments ADD COLUMN api_saved INTEGER NOT NULL DEFAULT 1"
                )
            if "project_name" not in columns:
                db.execute(
                    "ALTER TABLE assignments ADD COLUMN project_name TEXT NOT NULL DEFAULT ''"
                )
            for column in (
                "subscriber_name", "contract_type", "labor_address",
                "labor_province", "customer_name",
            ):
                if column not in columns:
                    db.execute(
                        f"ALTER TABLE assignments ADD COLUMN {column} "
                        "TEXT NOT NULL DEFAULT ''"
                    )
            if "original_assignee" not in columns:
                db.execute(
                    "ALTER TABLE assignments ADD COLUMN original_assignee TEXT NOT NULL DEFAULT ''"
                )
                db.execute(
                    "UPDATE assignments SET original_assignee = assignee WHERE original_assignee = ''"
                )
            if "dashboard_saved" not in columns:
                db.execute(
                    "ALTER TABLE assignments ADD COLUMN dashboard_saved INTEGER NOT NULL DEFAULT 0"
                )
            if "sheet_ordinal" not in columns:
                db.execute(
                    "ALTER TABLE assignments ADD COLUMN sheet_ordinal INTEGER NOT NULL DEFAULT 1"
                )
            if "sheet_existing" not in columns:
                db.execute(
                    "ALTER TABLE assignments ADD COLUMN sheet_existing INTEGER NOT NULL DEFAULT 0"
                )
            if "cohort_key" not in columns:
                db.execute(
                    "ALTER TABLE assignments ADD COLUMN cohort_key TEXT NOT NULL DEFAULT ''"
                )
            # Rebuild affinity keys from the current grouping dimensions. This
            # migrates older province-based keys without changing assignment
            # history; rows lacking an address cannot anchor an address cohort.
            history = db.execute(
                """
                SELECT rowid, customer_name, labor_address, service, assignee,
                       cohort_key
                FROM assignments
                """
            ).fetchall()
            for rowid, customer, address, service, assignee, old_key in history:
                if (
                    normalize(service) == normalize("Voice Brandname")
                    and normalize(assignee) == normalize("Lê Đức Tuấn")
                ):
                    cohort_key = ""
                else:
                    cohort_key = assignment_cohort_key(customer, address, service)
                if cohort_key != old_key:
                    db.execute(
                        "UPDATE assignments SET cohort_key = ? WHERE rowid = ?",
                        (cohort_key, rowid),
                    )
            # Older databases did not distinguish identical Sheet rows created
            # in the same minute. Rebuild stable ordinals so pending retries can
            # verify every occurrence instead of treating them as one row.
            rows = db.execute(
                """
                SELECT rowid, sheet_timestamp, transaction_id, subscriber_id,
                       service, assignee
                FROM assignments
                ORDER BY created_at, ticket_key, assignee
                """
            ).fetchall()
            occurrences: dict[tuple[str, str, str, str, str], int] = {}
            for rowid, stamp, transaction, subscriber, service, assignee in rows:
                identity = (stamp, transaction, subscriber, service, assignee)
                ordinal = occurrences.get(identity, 0) + 1
                occurrences[identity] = ordinal
                db.execute(
                    "UPDATE assignments SET sheet_ordinal = ? WHERE rowid = ?",
                    (ordinal, rowid),
                )

    def cohort_assignees(self) -> dict[str, str]:
        """Return the first successfully assigned employee for each cohort."""
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT cohort_key, assignee
                FROM assignments
                WHERE onebss_saved = 1 AND cohort_key <> ''
                ORDER BY created_at, rowid
                """
            ).fetchall()
        result: dict[str, str] = {}
        for key, assignee in rows:
            result.setdefault(key, assignee)
        return result

    def scores(self, year: int | None = None, month: int | None = None) -> dict[str, Decimal]:
        with self.connect() as db:
            if year is None or month is None:
                rows = db.execute(
                    """SELECT assignee, points FROM assignments
                       WHERE onebss_saved = 1 AND sheet_existing = 0"""
                ).fetchall()
            else:
                sheet_pattern = f"__/{month:02d}/{year} %"
                created_month = f"{year}-{month:02d}"
                rows = db.execute(
                    """
                    SELECT assignee, points FROM assignments
                    WHERE onebss_saved = 1
                      AND sheet_existing = 0
                      AND (sheet_timestamp LIKE ? OR
                           (sheet_timestamp = '' AND substr(created_at, 1, 7) = ?))
                    """,
                    (sheet_pattern, created_month),
                ).fetchall()
        scores: dict[str, Decimal] = {}
        for name, points in rows:
            value = Decimal(points)
            if value == 0:
                continue
            scores[name] = scores.get(name, Decimal(0)) + value
        return scores

    def completed_keys(self) -> set[str]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT DISTINCT ticket_key FROM assignments WHERE onebss_saved = 1"
            ).fetchall()
        return {row[0] for row in rows}

    def stage(self, assignment: Assignment) -> Assignment:
        share = assignment.points_per_person
        now = datetime.now(timezone.utc).isoformat()
        sheet_timestamp = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M")
        with self.connect() as db:
            # A failed browser action can leave staged rows behind. They are
            # not completed work and must not retain an obsolete assignee when
            # the planner is run again.
            db.execute(
                "DELETE FROM assignments WHERE (ticket_key = ? OR ticket_key LIKE ?) AND onebss_saved = 0",
                (assignment.ticket.key, f"{assignment.ticket.key}#%"),
            )
            stored_keys = [
                row[0] for row in db.execute(
                    "SELECT DISTINCT ticket_key FROM assignments WHERE ticket_key = ? OR ticket_key LIKE ?",
                    (assignment.ticket.key, f"{assignment.ticket.key}#%"),
                )
            ]
            occurrence = len(stored_keys) + 1
            ledger_key = (
                assignment.ticket.key if occurrence == 1
                else f"{assignment.ticket.key}#{occurrence}"
            )
            for assignee in assignment.assignees:
                sheet_ordinal = db.execute(
                    """
                    SELECT COUNT(*) + 1 FROM assignments
                    WHERE sheet_timestamp = ? AND transaction_id = ?
                      AND subscriber_id = ? AND service = ? AND assignee = ?
                    """,
                    (
                        sheet_timestamp,
                        assignment.ticket.transaction_id,
                        assignment.ticket.subscriber_id,
                        assignment.sheet_service or assignment.ticket.service_type or assignment.ticket.service,
                        assignee,
                    ),
                ).fetchone()[0]
                db.execute(
                    """
                    INSERT OR IGNORE INTO assignments
                    (ticket_key, transaction_id, subscriber_id, service, assignee, points,
                     rule_row, created_at, sheet_timestamp, project_name, original_assignee,
                     sheet_ordinal, sheet_existing, sheet_saved, api_saved, subscriber_name,
                     contract_type, labor_address, labor_province, customer_name,
                     cohort_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ledger_key,
                        assignment.ticket.transaction_id,
                        assignment.ticket.subscriber_id,
                        assignment.sheet_service or assignment.ticket.service_type or assignment.ticket.service,
                        assignee,
                        str(share),
                        assignment.rule_row,
                        now,
                        sheet_timestamp,
                        assignment.project_name,
                        assignee,
                        sheet_ordinal,
                        int(assignment.sheet_existing),
                        int(not assignment.write_to_sheet),
                        int(not assignment.send_to_api),
                        assignment.ticket.subscriber_name,
                        assignment.ticket.contract_type,
                        assignment.ticket.labor_address,
                        assignment.ticket.labor_province,
                        assignment.ticket.customer_name,
                        assignment.cohort_key,
                    ),
                )
        return replace(
            assignment, ledger_key=ledger_key, sheet_timestamp=sheet_timestamp
        )

    def mark(self, assignment: Assignment, field: str) -> None:
        if field not in {"onebss_saved", "sheet_saved", "sms_clicked", "dashboard_saved"}:
            raise ValueError(field)
        with self.connect() as db:
            db.execute(
                f"UPDATE assignments SET {field} = 1 WHERE ticket_key = ?",
                (assignment.ledger_key or assignment.ticket.key,),
            )

    def pending_sheet_rows(self) -> list[dict[str, str]]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT ticket_key, transaction_id, subscriber_id, service, assignee, points,
                       sheet_timestamp, sheet_ordinal, subscriber_name, contract_type,
                       labor_address, labor_province, project_name,
                       CASE WHEN sheet_existing = 1 THEN 'Giao lại' ELSE '' END
                           AS reassignment
                FROM assignments
                WHERE onebss_saved = 1 AND sheet_saved = 0
                ORDER BY created_at, ticket_key, assignee
                """
            ).fetchall()
        keys = [
            "ticket_key", "transaction_id", "subscriber_id", "service", "assignee",
            "points", "sheet_timestamp", "sheet_ordinal", "subscriber_name",
            "contract_type", "labor_address", "labor_province", "project_name",
            "reassignment",
        ]
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def mark_sheet_keys(self, ticket_keys: set[str]) -> None:
        if not ticket_keys:
            return
        placeholders = ",".join("?" for _ in ticket_keys)
        with self.connect() as db:
            db.execute(
                f"UPDATE assignments SET sheet_saved = 1 WHERE ticket_key IN ({placeholders})",
                tuple(ticket_keys),
            )

    def pending_ingest_rows(self) -> list[dict[str, str]]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT ticket_key, transaction_id, subscriber_id, service,
                       assignee, sheet_timestamp, labor_province, project_name,
                       rule_row
                FROM assignments
                WHERE onebss_saved = 1 AND api_saved = 0
                ORDER BY created_at, ticket_key, assignee
                """
            ).fetchall()
        keys = (
            "ticket_key", "transaction_id", "subscriber_id", "service",
            "assignee", "sheet_timestamp", "labor_province", "project_name",
            "rule_row",
        )
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def mark_ingest_keys(self, ticket_keys: set[str]) -> None:
        if not ticket_keys:
            return
        placeholders = ",".join("?" for _ in ticket_keys)
        with self.connect() as db:
            db.execute(
                f"UPDATE assignments SET api_saved = 1 WHERE ticket_key IN ({placeholders})",
                tuple(ticket_keys),
            )

    def pending_dashboard_rows(self) -> list[dict[str, str]]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT ticket_key, transaction_id, subscriber_id, service,
                       original_assignee, assignee, points, project_name,
                       sheet_timestamp, sheet_saved
                FROM assignments
                WHERE onebss_saved = 1 AND dashboard_saved = 0
                ORDER BY created_at, ticket_key, assignee
                """
            ).fetchall()
        keys = [
            "ledger_key", "transaction_id", "subscriber_id", "service",
            "original_assignee", "current_assignee", "points", "project_name",
            "sheet_timestamp", "sheet_saved",
        ]
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def mark_dashboard_keys(self, ticket_keys: set[str]) -> None:
        if not ticket_keys:
            return
        placeholders = ",".join("?" for _ in ticket_keys)
        with self.connect() as db:
            db.execute(
                f"UPDATE assignments SET dashboard_saved = 1 WHERE ticket_key IN ({placeholders})",
                tuple(ticket_keys),
            )

    def assignment_record(self, ledger_key: str, original_assignee: str) -> dict[str, str] | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT ticket_key, transaction_id, subscriber_id, service,
                       original_assignee, assignee, points, project_name,
                       sheet_timestamp, sheet_saved
                FROM assignments
                WHERE ticket_key = ? AND original_assignee = ? AND onebss_saved = 1
                """,
                (ledger_key, original_assignee),
            ).fetchone()
        if not row:
            return None
        keys = [
            "ledger_key", "transaction_id", "subscriber_id", "service",
            "original_assignee", "current_assignee", "points", "project_name",
            "sheet_timestamp", "sheet_saved",
        ]
        return dict(zip(keys, row, strict=True))

    def reassign(self, ledger_key: str, original_assignee: str, new_assignee: str) -> None:
        with self.connect() as db:
            duplicate = db.execute(
                "SELECT 1 FROM assignments WHERE ticket_key = ? AND assignee = ?",
                (ledger_key, new_assignee),
            ).fetchone()
            current = db.execute(
                "SELECT assignee FROM assignments WHERE ticket_key = ? AND original_assignee = ?",
                (ledger_key, original_assignee),
            ).fetchone()
            if not current:
                raise RuntimeError("Không tìm thấy bản ghi phiếu trong sổ ATS-OneBSS")
            if current[0] == new_assignee:
                return
            if duplicate:
                raise RuntimeError("Người nhận mới đã tồn tại trong cùng lần giao phiếu")
            db.execute(
                """
                UPDATE assignments
                SET assignee = ?, sheet_saved = 1, dashboard_saved = 1
                WHERE ticket_key = ? AND original_assignee = ?
                """,
                (new_assignee, ledger_key, original_assignee),
            )
