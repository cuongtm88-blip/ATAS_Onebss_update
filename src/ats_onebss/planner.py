from __future__ import annotations

from decimal import Decimal

from .models import Assignment, ProjectRule, ServiceRule, Ticket
from .rules import (
    RuleError,
    canonical_member_name,
    eligible_members,
    match_project_rule,
    match_rule,
    member_group,
    voice_brandname_mode,
)
from .text import assignment_cohort_key, normalize, ticket_identity, unaccent


def plan_assignments(
    tickets: list[Ticket],
    rules: list[ServiceRule],
    initial_scores: dict[str, Decimal] | None = None,
    completed_keys: set[str] | None = None,
    use_backups: bool = False,
    project_rules: list[ProjectRule] | None = None,
    preferred_assignees: dict[str, tuple[str, ...]] | None = None,
    sheet_existing_keys: set[str] | None = None,
    member_target_ratios: dict[str, Decimal] | None = None,
    excluded_members: tuple[str, ...] = (),
    cohort_assignees: dict[str, str] | None = None,
    cohort_conflicts: dict[str, str] | None = None,
    manual_assignees: dict[str, str] | None = None,
) -> list[Assignment]:
    scores = {
        normalize(name): Decimal(str(value))
        for name, value in (initial_scores or {}).items()
    }
    target_ratios = {
        normalize(name): Decimal(str(value))
        for name, value in (member_target_ratios or {}).items()
    }
    if any(value <= 0 for value in target_ratios.values()):
        raise ValueError("Hệ số mục tiêu của nhân sự phải lớn hơn 0")
    excluded_names = tuple(
        canonical_member_name(rules, name) for name in excluded_members
    )
    excluded = {normalize(name) for name in excluded_names}

    member_names_by_group: dict[str, set[str]] = {}
    member_group_by_name: dict[str, str] = {}
    for rule in rules:
        for member in rule.members:
            key = normalize(member.name)
            prior_group = member_group_by_name.get(key)
            if prior_group and prior_group != member.group:
                raise RuleError(
                    f"Nhân viên {member.name} xuất hiện ở nhiều nhóm: "
                    f"{prior_group}, {member.group}"
                )
            member_group_by_name[key] = member.group
            member_names_by_group.setdefault(member.group, set()).add(key)
    def adjusted_load(name: str) -> Decimal:
        """Monthly completion ratio versus the employee's fixed home group."""
        key = normalize(name)
        group = member_group_by_name.get(key)
        group_names = member_names_by_group.get(group or "", set())
        average = (
            sum((scores.get(person, Decimal(0)) for person in group_names), Decimal(0))
            / len(group_names)
            if group_names else Decimal(0)
        )
        denominator = average * target_ratios.get(key, Decimal(1))
        if denominator > 0:
            return scores.get(key, Decimal(0)) / denominator
        return scores.get(key, Decimal(0)) / target_ratios.get(key, Decimal(1))

    def choose_member(
        members: list[str], rule: ServiceRule,
        pick_key: tuple[str, tuple[str, ...]],
    ) -> str:
        lowest = min(adjusted_load(name) for name in members)
        tied = [name for name in members if adjusted_load(name) == lowest]
        if any(member.target_share is not None for member in rule.members):
            shares = {
                normalize(member.name): member.target_share or Decimal(0)
                for member in rule.members
            }
            largest_share = max(shares.get(normalize(name), Decimal(0)) for name in tied)
            tied = [
                name for name in tied
                if shares.get(normalize(name), Decimal(0)) == largest_share
            ]
        cursor = last_pick.get(pick_key, -1) + 1
        selected = tied[cursor % len(tied)]
        last_pick[pick_key] = cursor
        return selected

    completed = completed_keys or set()
    result: list[Assignment] = []
    last_pick: dict[tuple[str, tuple[str, ...]], int] = {}
    preferred_assignees = preferred_assignees or {}
    manual_assignees = manual_assignees or {}
    current_sheet_keys = (
        sheet_existing_keys
        if sheet_existing_keys is not None
        else set(preferred_assignees)
    )

    candidates: list[
        tuple[
            int, Decimal, Ticket, ServiceRule, str, dict[str, list[str]], bool,
            bool, str,
            str,
        ]
    ] = []
    cohort_pins = dict(cohort_assignees or {})
    cohort_conflicts = cohort_conflicts if cohort_conflicts is not None else {}
    # If an already recorded ticket from this cohort is in the current queue,
    # preserve its recorded assignee as the cohort anchor unless we already
    # have a successful assignment saved locally.
    sheet_cohort_pins: dict[str, str] = {}
    for ticket in tickets:
        try:
            seed_rule = match_rule(rules, ticket)
        except RuleError:
            is_giam_sat = (
                normalize(ticket.service_type or ticket.service)
                == normalize("Voice Brandname")
                and "giam sat" in unaccent(ticket.vip_status)
            )
        else:
            is_giam_sat = voice_brandname_mode(seed_rule, ticket) == "giam_sat"
        if is_giam_sat:
            continue
        key = assignment_cohort_key(
            ticket.customer_name, ticket.labor_address,
            ticket.service_type or ticket.service,
        )
        prior = preferred_assignees.get(
            ticket_identity(ticket.transaction_id, ticket.subscriber_id)
        )
        if key and prior:
            sheet_cohort_pins.setdefault(
                key, canonical_member_name(rules, prior[0])
            )
    # The authoritative Sheet owner for any cohort represented in this queue
    # takes precedence over the local affinity cache.
    cohort_pins.update(sheet_cohort_pins)

    for ticket in tickets:
        if ticket.key in completed:
            continue
        identity = ticket_identity(ticket.transaction_id, ticket.subscriber_id)
        preferred = preferred_assignees.get(identity)
        manual_assignee = manual_assignees.get(ticket.key, "")
        on_current_sheet = identity in current_sheet_keys
        # A previously assigned ticket is always written as a new "Giao lại"
        # row, even when it was already present on this month's tab. Prior-month
        # history is also a replay and must not increase this month's score.
        in_sheet_history = bool(preferred) or on_current_sheet
        try:
            rule = match_rule(rules, ticket)
        except RuleError:
            if not preferred and not manual_assignee:
                raise
            # The authoritative Sheet is enough to route a returned ticket,
            # even if its historical service wording is no longer in Excel.
            service = ticket.service_type or ticket.service
            rule = ServiceRule(
                0, service, Decimal(0), "", (), service, send_to_api=False
            )
        # A service explicitly excluded from Google Sheet must not inherit an
        # old, erroneously recorded Sheet assignee. It is always routed from
        # the current Excel rule and remains absent from Sheet history.
        if not rule.write_to_sheet:
            preferred = None
        elif preferred and not manual_assignee:
            preferred = tuple(
                canonical_member_name(rules, assignee) for assignee in preferred
            )
            if any(normalize(assignee) in excluded for assignee in preferred):
                continue
        voice_mode = voice_brandname_mode(rule, ticket)
        # The current VIP state is stronger than Sheet history for supervision.
        # A returned Giam sat ticket must always go to Le Duc Tuan. Replays are
        # still appended as a new row and marked so they do not affect the score.
        # Xu ly keeps the original Sheet assignee when present.
        if manual_assignee:
            groups = {"Chỉ định thủ công": [manual_assignee]}
            try:
                project = match_project_rule(project_rules or [], ticket)
            except RuleError:
                project = None
            project_name = project.project_name if project else ""
            sheet_existing = in_sheet_history and rule.write_to_sheet
            sheet_reassignment = sheet_existing
        elif voice_mode == "giam_sat":
            groups = eligible_members(
                rule, ticket, use_backups, excluded_names
            )
            project_name = ""
            sheet_existing = in_sheet_history and rule.write_to_sheet
        elif preferred:
            groups = {
                f"Google Sheet {index}": [assignee]
                for index, assignee in enumerate(preferred, start=1)
            }
            try:
                project = match_project_rule(project_rules or [], ticket)
            except RuleError:
                project = None
            project_name = project.project_name if project else ""
            sheet_existing = in_sheet_history
        elif voice_mode == "xu_ly":
            groups = eligible_members(
                rule, ticket, use_backups, excluded_names
            )
            project_name = ""
            sheet_existing = False
        else:
            # Rules excluded from Sheet history (currently Thoại quốc tế) are
            # self-contained dispatch rules and must keep their Excel owner,
            # even when customer text also happens to match a named project.
            project = (
                match_project_rule(project_rules or [], ticket)
                if rule.write_to_sheet else None
            )
            if project:
                if normalize(project.assignee) in excluded:
                    continue
                groups = {member_group(rules, project.assignee): [project.assignee]}
                project_name = project.project_name
            else:
                groups = eligible_members(
                    rule, ticket, use_backups, excluded_names
                )
                project_name = ""
            sheet_existing = in_sheet_history and rule.write_to_sheet
        if not manual_assignee:
            sheet_reassignment = sheet_existing
        if not manual_assignee and voice_mode in {"xu_ly", "giam_sat"}:
            # For Voice Brandname, the old mode is inferred from its saved
            # assignee: Lê Đức Tuấn means Giam sat; anyone else means Xu ly.
            # The replay marker is only meaningful when the VIP mode matches.
            if preferred:
                previous_was_giam_sat = (
                    normalize(preferred[0]) == normalize("Lê Đức Tuấn")
                )
                sheet_reassignment = sheet_existing and (
                    previous_was_giam_sat == (voice_mode == "giam_sat")
                )
            else:
                sheet_reassignment = False
        eligible_count = sum(len(members) for members in groups.values())
        cohort_key = (
            "" if manual_assignee or voice_mode == "giam_sat" else assignment_cohort_key(
                ticket.customer_name, ticket.labor_address,
                ticket.service_type or ticket.service,
            )
        )
        planned_points = rule.points if rule.count_points else Decimal(0)
        # Scarce/fixed work is planned first; for equal eligibility, larger
        # point values first produce a tighter greedy load balance.
        candidates.append(
            (
                0 if sheet_existing else eligible_count,
                -planned_points,
                ticket,
                rule,
                project_name,
                groups,
                sheet_existing,
                sheet_reassignment,
                cohort_key,
                manual_assignee,
            )
        )

    candidates.sort(key=lambda item: (item[0], item[1]))
    for (
        _count, _negative_points, ticket, rule, project_name, groups,
        sheet_existing, sheet_reassignment, cohort_key, manual_assignee,
    ) in candidates:
        assignees: list[str] = []
        if manual_assignee:
            assignees.append(manual_assignee)
        elif cohort_key:
            eligible = list(dict.fromkeys(
                name for members in groups.values() for name in members
            ))
            pinned = cohort_pins.get(cohort_key)
            if pinned:
                selected = next(
                    (name for name in eligible if normalize(name) == normalize(pinned)),
                    None,
                )
                if selected is None:
                    if normalize(pinned) not in excluded or project_name:
                        cohort_conflicts[ticket.key] = pinned
                        continue
                    # For ordinary work, temporarily move this cohort to an
                    # eligible colleague when its saved owner is excluded.
                    # Project routes remain fixed and are never reassigned by
                    # this fallback. Keep the durable local pin unchanged so
                    # the original owner resumes the cohort after returning.
                    pick_key = ("cohort", tuple(normalize(name) for name in eligible))
                    selected = choose_member(eligible, rule, pick_key)
                    cohort_pins[cohort_key] = selected
            else:
                pick_key = ("cohort", tuple(normalize(name) for name in eligible))
                selected = choose_member(eligible, rule, pick_key)
                cohort_pins[cohort_key] = selected
            assignees.append(selected)
        else:
            if any(member.target_share is not None for member in rule.members):
                candidates = list(dict.fromkeys(
                    name for members in groups.values() for name in members
                ))
                pick_key = ("weighted", tuple(normalize(name) for name in candidates))
                assignees.append(choose_member(candidates, rule, pick_key))
            else:
                for group, members in groups.items():
                    pick_key = (group, tuple(normalize(name) for name in members))
                    assignees.append(choose_member(members, rule, pick_key))
        assignment = Assignment(
            ticket, tuple(assignees),
            rule.points if rule.count_points else Decimal(0), rule.row_number,
            rule.sheet_service or rule.service, project_name,
            sheet_existing=sheet_existing,
            sheet_reassignment=sheet_reassignment,
            write_to_sheet=rule.write_to_sheet,
            cohort_key=cohort_key,
            send_to_api=rule.send_to_api,
            manual_override=bool(manual_assignee),
        )
        if not sheet_existing:
            share = assignment.points_per_person
            for assignee in assignees:
                key = normalize(assignee)
                scores[key] = scores.get(key, Decimal(0)) + share
        result.append(assignment)
    return result
