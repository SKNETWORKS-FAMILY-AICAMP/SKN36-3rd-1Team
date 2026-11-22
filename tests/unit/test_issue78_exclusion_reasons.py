
"""Issue #78: NOT exclusion reasons regression tests."""

import pytest

from backend.eligibility import checker


def sample_exclusion_policy():
    """75세 이상 제외 조건이 있는 테스트용 정책."""
    return {
        "policy_id": "ISSUE78_TEST",
        "graph_status": "FOUND",
        "data_quality": {
            "hold_auto_eligibility": False,
            "affected_attributes": [],
        },
        "graph": {
            "policy": {
                "eligibility": "만 75세 이상 제외",
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
                                "name": "기타 지원 요건",
                                "value": "추가 확인 필요",
                            },
                        ],
                        "groups": [
                            {
                                "group_id": "EXCLUSION",
                                "logic": "NOT",
                                "conditions": [],
                                "groups": [
                                    {
                                        "group_id": "EXCLUSION_OR",
                                        "logic": "OR",
                                        "conditions": [
                                            {
                                                "type": "AGE",
                                                "name": "75세 이상 제외",
                                                "operator": "BETWEEN",
                                                "value": "75~120",
                                            },
                                        ],
                                        "groups": [],
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
        },
    }


@pytest.mark.parametrize(
    "age, expected_root, should_exclude",
    [
        (74, checker.CHECK, False),
        (75, checker.NO, True),
        (76, checker.NO, True),
        (80, checker.NO, True),
        (None, checker.CHECK, False),
    ],
)
def test_issue78_age_exclusion_boundaries(
    age, expected_root, should_exclude
):
    result = checker.check_eligibility(
        {"age": age, "region": None, "income": None},
        sample_exclusion_policy(),
    )

    conditions = result["conditions"]

    # 기존 종합 판정은 바뀌면 안 된다.
    root = conditions[-1]
    assert root["status"] == expected_root

    # 제외 사유는 계산 결과와 분리한다.
    exclusions = result.get("exclusion_reasons")

    if should_exclude:
        assert exclusions
        assert any(
            item["field"] == "age"
            and "75" in item["reason"]
            and "제외" in item["reason"]
            for item in exclusions
        )
    else:
        assert not exclusions


def test_issue78_age_condition_status_is_not_flipped():
    """제외 사유를 표시해도 계산용 상태는 유지한다."""
    result = checker.check_eligibility(
        {"age": 80, "region": None, "income": None},
        sample_exclusion_policy(),
    )

    age_conditions = [
        item for item in result["conditions"]
        if item["field"] == "age"
    ]

    assert len(age_conditions) == 1
    assert age_conditions[0]["status"] == checker.OK

    # 실제 정책 종합 결과는 제외 대상이다.
    root = result["conditions"][-1]
    assert root["status"] == checker.NO


def test_issue78_optional_or_branch_does_not_confirm_exclusion():
    """OR의 선택적 NOT 분기만으로 정책 제외를 확정하지 않는다."""
    policy = sample_exclusion_policy()
    root = policy["graph"]["eligibility"]["condition_tree"][0]

    exclusion_group = root["groups"][0]

    # 나이가 18~29세가 아니라서 종합 판정은 ineligible.
    root["conditions"] = [
        {
            "type": "AGE",
            "name": "신청자 연령",
            "operator": "BETWEEN",
            "value": "18~29",
        }
    ]

    # OR의 다른 선택지는 충족한다.
    # 따라서 선택적 NOT 분기를 정책의 확정 제외 사유로
    # 안내하면 안 된다.
    root["groups"] = [
        {
            "group_id": "OPTIONAL_OR",
            "logic": "OR",
            "conditions": [
                {
                    "type": "AGE",
                    "name": "다른 선택지",
                    "operator": "BETWEEN",
                    "value": "0~120",
                }
            ],
            "groups": [exclusion_group],
        }
    ]

    result = checker.check_eligibility(
        {"age": 80, "region": None, "income": None},
        policy,
    )

    assert result["conditions"][-1]["status"] == checker.NO
    assert not result.get("exclusion_reasons")
