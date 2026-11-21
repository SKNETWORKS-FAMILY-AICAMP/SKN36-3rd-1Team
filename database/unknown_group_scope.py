
"""Conservative, schema-driven scope for income-only UNKNOWN groups.

This module must not interpret or auto-approve the UNKNOWN income criteria.
"""

from __future__ import annotations

from database.eligibility_fields import (
    ALL_ELIGIBILITY_FIELDS,
    SCOPED_ELIGIBILITY_FIELDS,
)

UNKNOWN_WARNING = "UNKNOWN_CONDITION_GROUP"

# Applies to ordinary data_quality summaries,
# NOT to UNKNOWN's strict proof gate.
_QUALITY_ALIASES = {
    "residence": "region",
    "location": "region",
    "housing_household": "housing",
    "household": "housing",
}


def normalize_quality_affected_attributes(
    attributes: object,
) -> set[str]:
    """Fail closed on malformed fields, share Checker/Pipeline alias semantics."""
    if not isinstance(attributes, list) or not attributes:
        return {ALL_ELIGIBILITY_FIELDS}

    fields: set[str] = set()

    for value in attributes:
        if not isinstance(value, str) or not value.strip():
            return {ALL_ELIGIBILITY_FIELDS}

        name = value.strip().lower()
        field = _QUALITY_ALIASES.get(name, name)

        if field not in SCOPED_ELIGIBILITY_FIELDS | {ALL_ELIGIBILITY_FIELDS}:
            return {ALL_ELIGIBILITY_FIELDS}

        fields.add(field)

    return fields


def unavailable_group(group: object, depth: int) -> bool:
    """Shared traversal cutoff for Checker and UNKNOWN scope inspection."""
    return (
        depth > 10
        or not isinstance(group, dict)
        or bool(group.get("cycle"))
        or bool(group.get("not_loaded"))
    )


def can_preserve_scoped_failed_root(
    eligibility: dict,
    scoped_unknown: set[str] | None,
    affected: set[str],
    *,
    root_status: str,
    has_certain_required_failure: bool,
    hold_all: bool = False,
    walk_issues: set[str] | None = None,
) -> bool:
    """Single decision gate shared by Checker and Pipeline.

    The caller must supply proof of a *directly evaluated* failed condition on
    a mandatory AND-only path. The shared guard never infers that from a
    failed OR/NOT child or from the root status alone.
    """
    if hold_all or scoped_unknown is None or affected != scoped_unknown:
        return False

    if root_status != "ineligible" or not has_certain_required_failure:
        return False

    if walk_issues and (walk_issues - {"unknown_logic"}):
        return False

    trees = (
        eligibility.get("condition_tree")
        if isinstance(eligibility, dict)
        else None
    )

    return (
        isinstance(trees, list)
        and len(trees) == 1
        and isinstance(trees[0], dict)
        and str(trees[0].get("logic") or "").upper() == "AND"
        and not eligibility.get("structure_issues")
    )


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

    if (
        not isinstance(trees[0], dict)
        or str(trees[0].get("logic") or "").upper() != "AND"
    ):
        return False

    if not isinstance(declared, list) or not declared:
        return False

    if not all(
        isinstance(group_id, str) and group_id
        for group_id in declared
    ):
        return False

    seen: set[str] = set()
    found: set[str] = set()

    def visit(group: dict, all_and: bool, depth: int) -> bool:
        if unavailable_group(group, depth):
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

            if any(
                str(item.get("type") or "").upper() != "INCOME"
                for item in conditions
            ):
                return False

            found.add(group_id)
            return True

        if logic not in {"AND", "OR", "NOT"}:
            return False

        return all(
            visit(child, all_and and logic == "AND", depth + 1)
            for child in children
        )

    return (
        visit(trees[0], True, 0)
        and bool(found)
        and found == set(declared)
    )


def _blocking_warning_scope(
    warnings: object,
) -> tuple[set[str], set[str]] | None:
    """Merge validated warning scopes; legacy UNKNOWN `all` means income only.

    Only an UNKNOWN warning may use the legacy `all` scope. Any other
    global/unknown warning forces the caller back to a global hold.
    """
    if not isinstance(warnings, list) or not warnings:
        return None

    fields: set[str] = set()
    blocking_codes: set[str] = set()
    unknown_count = 0

    for warning in warnings:
        if (
            not isinstance(warning, dict)
            or not isinstance(warning.get("code"), str)
        ):
            return None

        if warning.get("blocks_auto_eligibility") is False:
            continue

        affects = warning.get("affects")

        if (
            not isinstance(affects, list)
            or not affects
            or any(
                not isinstance(field, str) or not field
                for field in affects
            )
        ):
            return None

        code = warning["code"]
        affected = set(affects)
        blocking_codes.add(code)

        if code == UNKNOWN_WARNING:
            unknown_count += 1

            if not affected <= {"income", "all"}:
                return None

            # Use Search's affects=[income] directly.
            # Legacy "all" is accepted only after strict validation.
            fields.update(affected - {"all"})

            if "all" in affected:
                fields.add("income")

        else:
            if not affected <= SCOPED_ELIGIBILITY_FIELDS:
                return None

            fields.update(affected)

    return (
        (fields, blocking_codes)
        if unknown_count == 1
        else None
    )


def _quality_summary_matches_warning_scope(
    quality: dict,
    fields: set[str],
    blocking_codes: set[str],
) -> bool:
    """Validate quality metadata against the blocking warning scope.

    Hold reasons must reference known blocking warning codes.
    Affected attributes must be within the verified warning scope.

    The summary may contain the legacy 'all' marker, but this does not
    independently authorize narrowing.
    """
    reasons = quality.get("hold_reasons")
    summary = quality.get("affected_attributes")

    reasons_valid = reasons is None or (
        isinstance(reasons, list)
        and all(
            isinstance(reason, str) and reason in blocking_codes
            for reason in reasons
        )
    )

    allowed_fields = fields | {"all"}

    summary_valid = (
        isinstance(summary, list)
        and bool(summary)
        and all(
            isinstance(field, str) and field in allowed_fields
            for field in summary
        )
    )

    return reasons_valid and summary_valid


def scoped_income_quality_attributes(
    eligibility: dict,
    quality: dict,
) -> set[str] | None:
    """Narrow only when structure, warnings and summary agree."""
    scope_allowed = (
        income_only_unknown_under_and(eligibility)
        and isinstance(quality, dict)
        and quality.get("hold_auto_eligibility") is True
    )

    if not scope_allowed:
        return None

    warning_scope = _blocking_warning_scope(
        quality.get("warnings")
    )

    if warning_scope is None:
        return None

    fields, blocking_codes = warning_scope

    return (
        fields
        if _quality_summary_matches_warning_scope(
            quality,
            fields,
            blocking_codes,
        )
        else None
    )
