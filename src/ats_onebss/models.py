from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class Member:
    name: str
    group: str
    role: str
    target_share: Decimal | None = None


@dataclass(frozen=True)
class ServiceRule:
    row_number: int
    service: str
    points: Decimal
    condition: str
    members: tuple[Member, ...]
    sheet_service: str = ""
    count_points: bool = True
    write_to_sheet: bool = True
    send_to_api: bool = True


@dataclass(frozen=True)
class ProjectRoute:
    assignee: str
    locations: tuple[str, ...] = ()
    subscriber_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectRule:
    name: str
    contains: str
    match_fields: tuple[str, ...]
    priority: int
    fixed_assignee: str = ""
    route_field: str = ""
    routes: tuple[ProjectRoute, ...] = ()
    required_contains: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ProjectMatch:
    project_name: str
    assignee: str


@dataclass(frozen=True)
class Ticket:
    transaction_id: str
    subscriber_id: str
    service: str
    service_type: str = ""
    contract_type: str = ""
    installation_type: str = ""
    channel_type: str = ""
    vip_status: str = ""
    subscriber_name: str = ""
    customer_name: str = ""
    notes: str = ""
    labor_address: str = ""
    connection_address: str = ""
    labor_province: str = ""
    raw: dict[str, str] = field(default_factory=dict, compare=False)

    @property
    def key(self) -> str:
        return f"{self.transaction_id}|{self.subscriber_id}"


@dataclass(frozen=True)
class Assignment:
    ticket: Ticket
    assignees: tuple[str, ...]
    points: Decimal
    rule_row: int
    sheet_service: str = ""
    project_name: str = ""
    ledger_key: str = ""
    sheet_timestamp: str = ""
    sheet_existing: bool = False
    sheet_reassignment: bool | None = None
    write_to_sheet: bool = True
    cohort_key: str = ""
    send_to_api: bool = True
    manual_override: bool = False

    @property
    def points_per_person(self) -> Decimal:
        return self.points / len(self.assignees)
