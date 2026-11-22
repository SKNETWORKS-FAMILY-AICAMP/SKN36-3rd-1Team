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


@pytest.mark.parametrize("direct_not", [False, True])
def test_issue78_unverified_full_age_range_is_not_exclusion(
    direct_not,
):
    """0~120세는 공식 기준 확인 전 확정 제외 사유가 아니다."""
    policy = sample_exclusion_policy()
    root = policy["graph"]["eligibility"]["condition_tree"][0]
    exclusion = root["groups"][0]

    age_condition = exclusion["groups"][0]["conditions"][0]
    age_condition["value"] = "0~120"

    if direct_not:
        exclusion["conditions"] = [age_condition]
        exclusion["groups"] = []

    result = checker.check_eligibility(
        {"age": 80, "region": None, "income": None},
        policy,
    )

    # 계산용 판정은 기존대로 유지한다.
    assert result["conditions"][-1]["status"] == checker.NO

    # 공식 확인이 필요한 범위를 확정 제외 사유로 사용하지 않는다.
    assert result["exclusion_reasons"] is None


def test_issue78_confirmed_age_with_other_unknown_exclusions():
    """다른 제외 항목이 미확인이어도 확인된 나이 사유는 유지한다."""
    policy = sample_exclusion_policy()
    root = policy["graph"]["eligibility"]["condition_tree"][0]
    exclusion_or = root["groups"][0]["groups"][0]

    exclusion_or["conditions"].append(
        {
            "type": "SPECIAL_TARGET",
            "name": "추가 제외 항목",
            "value": "공식 기준 확인 필요",
        }
    )

    result = checker.check_eligibility(
        {"age": 80, "region": None, "income": None},
        policy,
    )

    assert result["conditions"][-1]["status"] == checker.NO

    reasons = result["exclusion_reasons"]
    assert reasons is not None
    assert len(reasons) == 1
    assert reasons[0]["field"] == "age"
    assert "75" in reasons[0]["reason"]
    assert "제외" in reasons[0]["reason"]


@pytest.mark.parametrize("affected", [["age"], ["all"], []])
def test_issue78_affected_quality_does_not_confirm_exclusion(affected):
    """A quality-held age condition cannot simultaneously be an exclusion."""
    policy = sample_exclusion_policy()
    policy["data_quality"] = {
        "hold_auto_eligibility": True,
        "affected_attributes": affected,
    }
    result = checker.check_eligibility(
        {"age": 80, "region": None, "income": None}, policy
    )
    age_rows = [row for row in result["conditions"] if row["field"] == "age"]
    assert len(age_rows) == 1
    assert age_rows[0]["status"] == checker.CHECK
    assert result["conditions"][-1]["status"] == checker.CHECK
    assert result["exclusion_reasons"] is None


def test_issue78_only_one_walk_evaluates_each_condition(monkeypatch):
    """Exclusion display reuses _walk instead of calling _condition twice."""
    calls = []
    original = checker._condition

    def counted(condition, profile):
        calls.append(condition)
        return original(condition, profile)

    monkeypatch.setattr(checker, "_condition", counted)
    result = checker.check_eligibility(
        {"age": 80, "region": None, "income": None},
        sample_exclusion_policy(),
    )
    assert result["exclusion_reasons"]
    # One SPECIAL_TARGET + one AGE leaf. No second exclusion traversal.
    assert len(calls) == 2
