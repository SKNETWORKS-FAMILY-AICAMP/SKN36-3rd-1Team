
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
