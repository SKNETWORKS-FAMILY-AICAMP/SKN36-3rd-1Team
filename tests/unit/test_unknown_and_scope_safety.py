"""Additional safety tests for #40 scoped UNKNOWN quality guards."""

from copy import deepcopy

import pytest

from backend.eligibility.checker import check_eligibility
from database.unknown_group_scope import (
    income_only_unknown_under_and,
    scoped_income_quality_attributes,
)


def _tree(kind="INCOME", parent_logic="AND", declared=None):
    group = {
        "group_id": "INC_UNKNOWN", "logic": "UNKNOWN",
        "conditions": [{"type": kind, "value": "pending"}], "groups": [],
    }
    return {
        "condition_tree": [{
            "group_id": "ROOT", "logic": parent_logic,
            "conditions": [{"type": "AGE", "operator": "BETWEEN", "value": "19~34"}],
            "groups": [group],
        }],
        "unknown_logic_groups": ["INC_UNKNOWN"] if declared is None else declared,
        "structure_issues": [],
    }


def _quality(extra=None):
    warnings = [{
        "code": "UNKNOWN_CONDITION_GROUP", "blocks_auto_eligibility": True,
        "affects": ["all"],
    }]
    if extra:
        warnings.append(extra)
    return {
        "hold_auto_eligibility": True,
        "affected_attributes": ["all"],
        "warnings": warnings,
    }


@pytest.mark.parametrize("kind,parent,declared", [
    ("AGE", "AND", None),
    ("INCOME", "OR", None),
    ("INCOME", "AND", ["WRONG_ID"]),
])
def test_unsupported_unknown_shape_is_not_scoped(kind, parent, declared):
    assert not income_only_unknown_under_and(_tree(kind, parent, declared))


def test_global_warning_is_not_narrowed():
    quality = _quality({
        "code": "NEEDS_REVIEW", "blocks_auto_eligibility": True,
        "affects": ["all"],
    })
    assert scoped_income_quality_attributes(_tree(), quality) is None


def test_scoped_warning_is_accumulated():
    quality = _quality({
        "code": "AGE_SOURCE_GRAPH_MISMATCH", "blocks_auto_eligibility": True,
        "affects": ["age"],
    })
    quality["affected_attributes"] = ["all", "age"]
    assert scoped_income_quality_attributes(_tree(), quality) == {"age", "income"}


def test_income_unknown_keeps_certain_age_failure():
    eligibility = _tree()
    policy = {
        "policy_id": "POL_ISSUE40_TEST", "graph_status": "FOUND",
        "graph": {"eligibility": eligibility}, "data_quality": _quality(),
    }
    original = deepcopy(policy)
    result = check_eligibility({"age": 45}, policy)
    age = [x for x in result["conditions"] if x["field"] == "age"]
    root = [x for x in result["conditions"] if x["field"] == "eligibility"][-1]
    assert age[0]["status"] == "ineligible"
    assert root["status"] == "ineligible"
    assert original == policy


@pytest.mark.parametrize("affects,expected", [
    (["income"], {"income"}),
    (["all"], {"income"}),  # explicitly supported legacy payload
    (["region"], None),
    (["income", "age"], None),
])
def test_search_warning_affects_is_validated_and_consumed(affects, expected):
    quality = _quality()
    quality["warnings"][0]["affects"] = affects
    assert scoped_income_quality_attributes(_tree(), quality) == expected


def test_multiple_unknown_groups_do_not_expand_scope_without_exact_metadata():
    eligibility = _tree()
    eligibility["condition_tree"][0]["groups"].append({
        "group_id": "SECOND", "logic": "UNKNOWN",
        "conditions": [{"type": "INCOME"}], "groups": [],
    })
    assert scoped_income_quality_attributes(eligibility, _quality()) is None
    eligibility["unknown_logic_groups"].append("SECOND")
    assert scoped_income_quality_attributes(eligibility, _quality()) == {"income"}
