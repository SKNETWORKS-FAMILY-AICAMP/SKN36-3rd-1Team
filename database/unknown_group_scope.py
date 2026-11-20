"""Conservative, schema-driven scope for income-only UNKNOWN groups.

This module must not interpret or auto-approve the UNKNOWN income criteria.
"""

from __future__ import annotations

from database.eligibility_fields import SCOPED_ELIGIBILITY_FIELDS

UNKNOWN_WARNING = "UNKNOWN_CONDITION_GROUP"


def income_only_unknown_under_and(eligibility: dict) -> bool:
    """True only when every UNKNOWN group is an income-only AND descendant.

    UNKNOWN itself remains needs_check. The exception merely permits a
    different, demonstrably failed required AND condition to remain failed.
    """
    if not isinstance(eligibility, dict) or eligibility.get("structure_issues"):
        return False
    trees = eligibility.get("condition_tree")
    declared = eligibility.get("unknown_logic_groups")
    if not isinstance(trees, list) or len(trees) != 1:
        return False
    if not isinstance(trees[0], dict) or str(trees[0].get("logic") or "").upper() != "AND":
        return False
    if not isinstance(declared, list) or not declared:
        return False
    if not all(isinstance(group_id, str) and group_id for group_id in declared):
        return False
    seen: set[str] = set()
    found: set[str] = set()

    def visit(group: dict, all_and: bool, depth: int) -> bool:
        if depth > 10 or not isinstance(group, dict):
            return False
        if group.get("cycle") or group.get("not_loaded"):
            return False
        conditions = group.get("conditions")
        children = group.get("groups")
        if not isinstance(conditions, list) or not isinstance(children, list):
            return False
        if any(not isinstance(item, dict) for item in conditions):
            return False
        group_id = group.get("group_id")
        if isinstance(group_id, str) and group_id:
            if group_id in seen:
                return False
            seen.add(group_id)
        logic = str(group.get("logic") or "UNKNOWN").upper()
        if logic == "UNKNOWN":
            if not all_and or not group_id or not conditions or children:
                return False
            if any(str(item.get("type") or "").upper() != "INCOME" for item in conditions):
                return False
            found.add(group_id)
            return True
        if logic not in {"AND", "OR", "NOT"}:
            return False
        return all(visit(child, all_and and logic == "AND", depth + 1)
                   for child in children)

    return visit(trees[0], True, 0) and bool(found) and found == set(declared)


def scoped_income_quality_attributes(eligibility: dict, quality: dict) -> set[str] | None:
    """Return provably scoped warning fields, or None for conservative fallback.

    The legacy summary may say affected_attributes=['all']; narrow it only
    when the complete warning inventory supports the income-only exception.
    """
    if not income_only_unknown_under_and(eligibility):
        return None
    if not isinstance(quality, dict) or quality.get("hold_auto_eligibility") is not True:
        return None
    warnings = quality.get("warnings")
    if not isinstance(warnings, list) or not warnings:
        return None
    fields: set[str] = {"income"}
    blocking_codes: set[str] = set()
    unknown_seen = 0
    for warning in warnings:
        if not isinstance(warning, dict) or not isinstance(warning.get("code"), str):
            return None
        if warning.get("blocks_auto_eligibility") is False:
            continue
        code = warning["code"]
        blocking_codes.add(code)
        affects = warning.get("affects")
        if not isinstance(affects, list) or not affects:
            return None
        if not all(isinstance(field, str) and field for field in affects):
            return None
        affected = set(affects)
        if code == UNKNOWN_WARNING:
            unknown_seen += 1
            if not affected <= {"all", "income"}:
                return None
        else:
            if not affected <= SCOPED_ELIGIBILITY_FIELDS:
                return None
            fields.update(affected)
    if unknown_seen != 1:
        return None
    reasons = quality.get("hold_reasons")
    if reasons is not None and (
        not isinstance(reasons, list)
        or any(not isinstance(reason, str) or reason not in blocking_codes for reason in reasons)
    ):
        return None
    summary = quality.get("affected_attributes")
    if not isinstance(summary, list) or not summary:
        return None
    if any(not isinstance(field, str) or field not in fields | {"all"} for field in summary):
        return None
    return fields
