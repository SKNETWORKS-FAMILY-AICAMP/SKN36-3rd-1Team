
"""Issue #39: 자녀 월령 LT/LTE 조건 판정 회귀 테스트."""

import pytest

from backend.eligibility import checker


def sample_child_age_policy(operator="LTE"):
    """자녀 월령 24개월 기준을 가진 테스트 정책."""
    return {
        "policy_id": "ISSUE39_TEST",
        "graph_status": "FOUND",
        "data_quality": {
            "hold_auto_eligibility": False,
            "affected_attributes": [],
        },
        "graph": {
            "policy": {
                "eligibility": "자녀 월령 기준",
            },
            "eligibility": {
                "unknown_logic_groups": [],
                "structure_issues": [],
                "condition_tree": [
                    {
                        "group_id": "ROOT",
                        "logic": "AND",
                        "conditions": [
                            {
                                "type": "SPECIAL_TARGET",
                                "name": "자녀 월령",
                                "unit": "개월",
                                "operator": operator,
                                "value": "24",
                            }
                        ],
                        "groups": [],
                    }
                ],
            },
        },
    }


def get_child_result(months, operator="LTE"):
    result = checker.check_eligibility(
        {
            "age": 30,
            "region": None,
            "income": None,
            "child_age_months": months,
        },
        sample_child_age_policy(operator),
    )

    return next(
        item
        for item in result["conditions"]
        if item["field"] == "children"
    )


@pytest.mark.parametrize(
    "months, expected",
    [
        (23, checker.OK),
        (24, checker.OK),
        (25, checker.NO),
    ],
)
def test_issue39_lte_24_months(months, expected):
    """24개월 이하는 24개월까지 포함한다."""
    result = get_child_result(months, "LTE")

    assert result["status"] == expected
    assert "24개월 이하" in result["reason"]


@pytest.mark.parametrize(
    "months, expected",
    [
        (23, checker.OK),
        (24, checker.NO),
        (25, checker.NO),
    ],
)
def test_issue39_lt_24_months(months, expected):
    """기존 24개월 미만 판정은 유지한다."""
    result = get_child_result(months, "LT")

    assert result["status"] == expected
    assert "24개월 미만" in result["reason"]


@pytest.mark.parametrize("operator", ["LT", "LTE"])
def test_issue39_missing_months(operator):
    """자녀 월령 미입력 시 자동 판정을 보류한다."""
    result = get_child_result(None, operator)

    assert result["status"] == checker.CHECK


@pytest.mark.parametrize(
    "months",
    [-1, 24.5, "24", True],
)
def test_issue39_invalid_months(months):
    """잘못된 월령은 자동 판정하지 않는다."""
    result = get_child_result(months)

    assert result["status"] == checker.CHECK


def test_issue39_applicant_age_is_not_child_age():
    """신청자 나이만으로 자녀 월령을 판정하지 않는다."""
    result = checker.check_eligibility(
        {
            "age": 24,
            "region": None,
            "income": None,
        },
        sample_child_age_policy(),
    )

    child_result = next(
        item
        for item in result["conditions"]
        if item["field"] == "children"
    )

    assert child_result["status"] == checker.CHECK


#39 REVIEW HARDENING
@pytest.mark.parametrize("months, status", [
    (0, checker.OK), (23, checker.OK), (24, checker.OK), (25, checker.NO),
])
def test_issue39_lte_24_edge_and_newborn(months, status):
    assert get_child_result(months, "LTE")["status"] == status


@pytest.mark.parametrize("operator, value", [
    ("GTE", "24"), ("GT", "24"), ("BETWEEN", "0~24"),
    ("LTE", "invalid"), ("LTE", "24.0"),
])
def test_issue39_unknown_or_inexact_child_condition_needs_check(operator, value):
    policy = sample_child_age_policy(operator)
    policy["graph"]["eligibility"]["condition_tree"][0]["conditions"][0]["value"] = value
    result = checker.check_eligibility({"age": 30, "child_age_months": 12}, policy)
    child = next(x for x in result["conditions"] if x["field"] == "children")
    assert child["status"] == checker.CHECK


def test_issue39_child_age_quality_hold_is_not_certified():
    policy = sample_child_age_policy("LTE")
    policy["data_quality"] = {"hold_auto_eligibility": True,
                              "affected_attributes": ["children"]}
    result = checker.check_eligibility({"age": 30, "child_age_months": 24}, policy)
    child = next(x for x in result["conditions"] if x["field"] == "children")
    assert child["status"] == checker.CHECK


def test_issue39_months_other_than_child_age_are_not_auto_graded():
    # Future SPECIAL_TARGET rules may measure other durations in months.
    policy = sample_child_age_policy("LTE")
    condition = policy["graph"]["eligibility"]["condition_tree"][0]["conditions"][0]
    condition["name"] = "혼인 기간"
    condition["condition_id"] = "COND_MARRIAGE_DURATION_24M"
    result = checker.check_eligibility({"age": 30, "child_age_months": 12}, policy)
    assert not any(x["field"] == "children" for x in result["conditions"])
    assert any(x["field"] == "target" and x["status"] == checker.CHECK
               for x in result["conditions"])


#39 ANONYMOUS CHILD MONTHS REGRESSION
@pytest.mark.parametrize("months, operator, expected", [
    (None, "LT", checker.CHECK),
    (23, "LT", checker.OK),
    (24, "LT", checker.NO),
    (24, "LTE", checker.OK),
    (25, "LTE", checker.NO),
])
def test_issue39_unnamed_legacy_month_condition(months, operator, expected):
    """Old API/graph fixtures without name or ID stay classified as children."""
    policy = sample_child_age_policy(operator)
    condition = policy["graph"]["eligibility"]["condition_tree"][0]["conditions"][0]
    condition.pop("name", None)
    condition.pop("condition_id", None)
    result = checker.check_eligibility(
        {"age": 40, "region": None, "income": None, "child_age_months": months},
        policy,
    )
    child = [item for item in result["conditions"] if item["field"] == "children"]
    assert len(child) == 1
    assert child[0]["status"] == expected
    assert not any(item["field"] == "age" for item in result["conditions"])


def test_issue39_non_child_month_condition_with_unrelated_id_is_never_auto_graded():
    policy = sample_child_age_policy("LTE")
    condition = policy["graph"]["eligibility"]["condition_tree"][0]["conditions"][0]
    condition.pop("name", None)
    condition["condition_id"] = "COND_MARRIAGE_DURATION_24M"
    result = checker.check_eligibility(
        {"age": 40, "region": None, "income": None, "child_age_months": 12},
        policy,
    )
    assert not any(item["field"] == "children" for item in result["conditions"])
    assert any(item["field"] == "target" and item["status"] == checker.CHECK
               for item in result["conditions"])


# PR #112 review #1: childcare usage period is not a child's month-age.
@pytest.mark.parametrize("condition_id", [None, "COND_CHILDCARE_SERVICE_USAGE_24M"])
def test_review112_service_usage_duration_is_not_child_month_age(condition_id):
    policy = sample_child_age_policy("LTE")
    condition = policy["graph"]["eligibility"]["condition_tree"][0]["conditions"][0]
    condition["name"] = "자녀 돌봄 서비스 이용 기간 24개월 이하"
    if condition_id is not None:
        condition["condition_id"] = condition_id
    result = checker.check_eligibility({"age": 30, "child_age_months": 23}, policy)
    assert not any(row["field"] == "children" for row in result["conditions"])
    assert any(row["field"] == "target" and row["status"] == checker.CHECK
               for row in result["conditions"])


@pytest.mark.parametrize("name", [
    "자녀 월령", "대상 자녀 연령: 24개월 이하", "영아의 월령",
])
def test_review112_genuine_child_age_names_still_evaluate(name):
    policy = sample_child_age_policy("LTE")
    condition = policy["graph"]["eligibility"]["condition_tree"][0]["conditions"][0]
    condition["name"] = name
    result = checker.check_eligibility({"age": 30, "child_age_months": 23}, policy)
    assert any(row["field"] == "children" and row["status"] == checker.OK
               for row in result["conditions"])


def test_review112_explicit_child_age_id_takes_priority():
    policy = sample_child_age_policy("LTE")
    condition = policy["graph"]["eligibility"]["condition_tree"][0]["conditions"][0]
    condition["name"] = ""
    condition["condition_id"] = "COND_CHILD_AGE_LE_24M"
    result = checker.check_eligibility({"age": 30, "child_age_months": 24}, policy)
    assert any(row["field"] == "children" and row["status"] == checker.OK
               for row in result["conditions"])
