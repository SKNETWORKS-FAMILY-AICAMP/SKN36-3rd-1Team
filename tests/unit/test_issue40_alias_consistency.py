"""#40: quality attribute normalization stays consistent across layers."""

import pytest

from backend.eligibility.checker import check_eligibility
from backend.rag.pipeline import _policy_check
from database.unknown_group_scope import normalize_quality_affected_attributes


@pytest.mark.parametrize("raw, expected", [
    (["household"], {"housing"}),
    (["HOUSING_HOUSEHOLD "], {"housing"}),
    (["residence"], {"region"}),
    (["location"], {"region"}),
    (["AGE", "housing"], {"age", "housing"}),
    (["not_a_field"], {"all"}),
    (["age", None], {"all"}),
    ("housing", {"all"}),
    ([], {"all"}),
])
def test_quality_attribute_aliases(raw, expected):
    assert normalize_quality_affected_attributes(raw) == expected


@pytest.mark.parametrize("attribute", ["household", "residence", "HOUSING_HOUSEHOLD "])
def test_checker_and_pipeline_preserve_unaffected_age(attribute):
    policy = {
        "policy_id": "P_ALIAS", "policy_name": "가상 정책", "graph_status": "FOUND",
        "graph": {
            "policy": {},
            "eligibility": {
                "condition_tree": [{
                    "group_id": "ROOT", "logic": "AND",
                    "conditions": [{"type": "AGE", "operator": "BETWEEN", "value": "19~34"}],
                    "groups": [],
                }],
                "unknown_logic_groups": [], "structure_issues": [],
            },
        },
        "data_quality": {
            "hold_auto_eligibility": True,
            "affected_attributes": [attribute],
        },
    }
    profile = {"age": 25, "region": None, "income": None}
    checked = check_eligibility(profile, policy)
    presented = _policy_check(policy, checked, profile, {"url": "https://example.org/notice"})
    for result in (checked, presented):
        age_statuses = [x["status"] for x in result["conditions"] if x["field"] == "age"]
        root_statuses = [x["status"] for x in result["conditions"] if x["field"] == "eligibility"]
        assert age_statuses == ["eligible"]
        assert root_statuses[-1] == "needs_check"
