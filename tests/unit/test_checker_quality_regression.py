"""v1.4.0 checker 품질 경고 회귀 테스트 (실제 DB 연결 없음)."""

from copy import deepcopy

import pytest

from backend.eligibility.checker import check_eligibility


def _policy(affected_attributes=None, age_value="19~120"):
    hold = affected_attributes is not None
    return {
        "policy_id": "POL_TEST_V140",
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
                                "value": age_value,
                            }
                        ],
                        "groups": [],
                    }
                ],
                "unknown_logic_groups": [],
                "structure_issues": [],
            }
        },
        "data_quality": {
            "hold_auto_eligibility": hold,
            "affected_attributes": affected_attributes or [],
        },
    }


def _status(result, field):
    return [
        item["status"]
        for item in result["conditions"]
        if item["field"] == field
    ]


@pytest.mark.parametrize(
    "affected_attributes",
    [
        ["housing"],  # 경고 속성은 있는데 조건 노드는 없는 경우
        ["household"],  # 주거 관련 별칭
        ["HOUSING_HOUSEHOLD"],  # Neo4j 조건 유형 이름
        ["region "],  # 공백 포함 속성명
        ["unknown_quality_field"],  # 예상 밖 속성명: 전체 보류
        ["age", None],  # 손상된 경고 목록: 전체 보류
    ],
)
def test_quality_warning_never_leaves_group_eligible(affected_attributes):
    result = check_eligibility(
        {"age": 25}, _policy(affected_attributes)
    )
    assert _status(result, "eligibility") == ["needs_check"]


def test_unaffected_age_stays_eligible_with_missing_housing_condition():
    result = check_eligibility({"age": 25}, _policy(["housing"]))
    assert _status(result, "age") == ["eligible"]
    assert _status(result, "eligibility") == ["needs_check"]


def test_known_age_quality_warning_holds_age():
    result = check_eligibility({"age": 25}, _policy(["age"]))
    assert _status(result, "age") == ["needs_check"]
    assert _status(result, "eligibility") == ["needs_check"]


def test_v140_open_ended_age_reason_is_not_120_year_limit():
    result = check_eligibility({"age": 65}, _policy())
    reason = next(
        item["reason"] for item in result["conditions"]
        if item["field"] == "age"
    )
    assert _status(result, "age") == ["eligible"]
    assert "19세 이상" in reason
    assert "상한 없음" in reason
    assert "19~120세" not in reason


def test_quality_guard_keeps_inputs_unchanged():
    policy = _policy(["household"])
    snapshot = deepcopy(policy)
    check_eligibility({"age": 25}, policy)
    assert policy == snapshot
