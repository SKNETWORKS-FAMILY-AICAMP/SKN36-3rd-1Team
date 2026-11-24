
"""#40 UNKNOWN 소득 그룹의 AND 판정 회귀 테스트."""

from copy import deepcopy

from backend.eligibility.checker import check_eligibility


def make_policy(*, additional_warning=False):
    warnings = [
        {
            "code": "UNKNOWN_CONDITION_GROUP",
            "affects": ["all"],
            "blocks_auto_eligibility": True,
        }
    ]

    if additional_warning:
        warnings.append(
            {
                "code": "NEEDS_REVIEW",
                "affects": ["all"],
                "blocks_auto_eligibility": True,
            }
        )

    return {
        "policy_id": "POL_GOV_047",
        "graph_status": "FOUND",
        "graph": {
            "eligibility": {
                "condition_tree": [
                    {
                        "group_id": "ROOT",
                        "logic": "AND",
                        "conditions": [
                            {
                                "type": "AGE",
                                "operator": "BETWEEN",
                                "value": "19~34",
                            }
                        ],
                        "groups": [
                            {
                                "group_id": "INCOME_UNKNOWN",
                                "logic": "UNKNOWN",
                                "conditions": [
                                    {
                                        "type": "INCOME",
                                        "value": "중위소득_0_50_",
                                    }
                                ],
                                "groups": [],
                            }
                        ],
                    }
                ],
                "unknown_logic_groups": ["INCOME_UNKNOWN"],
                "structure_issues": [],
            }
        },
        "data_quality": {
            "hold_auto_eligibility": True,
            "affected_attributes": ["all"],
            "warnings": warnings,
        },
    }


def statuses(result, field):
    return [
        item["status"]
        for item in result["conditions"]
        if item["field"] == field
    ]


def test_and_known_age_failure_remains_ineligible():
    """확정된 필수 나이 조건 미충족은 UNKNOWN 소득과 분리."""
    result = check_eligibility({"age": 45}, make_policy())

    assert statuses(result, "age") == ["ineligible"]
    assert statuses(result, "eligibility")[-1] == "ineligible"


def test_and_age_passes_income_remains_unknown():
    """나이를 충족해도 소득이 불명확하면 종합 판정 보류."""
    result = check_eligibility({"age": 25}, make_policy())

    assert statuses(result, "age") == ["eligible"]
    assert statuses(result, "eligibility")[-1] == "needs_check"


def test_other_global_warning_preserves_conservative_hold():
    """소득 UNKNOWN 이외의 전체 보류 경고는 해제하지 않는다."""
    result = check_eligibility(
        {"age": 45},
        make_policy(additional_warning=True),
    )

    assert statuses(result, "eligibility")[-1] == "needs_check"


def test_checker_does_not_mutate_policy():
    """Checker는 입력 정책 데이터를 변경하지 않는다."""
    policy = make_policy()
    original = deepcopy(policy)

    check_eligibility({"age": 45}, policy)

    assert policy == original
