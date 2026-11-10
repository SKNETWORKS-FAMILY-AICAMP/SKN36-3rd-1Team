"""v1.4.0 Checker 표시 전용 회귀 테스트. Neo4j·OpenAI 연결 없음."""

from copy import deepcopy

import pytest

from backend.eligibility.checker import (
    check_eligibility,
    summarize_conditions_for_display,
)


PROFILE = {"age": 25, "region": "서울특별시", "income": 1.5}


def _base_policy(policy_id, conditions, *, children=None, affected=None):
    return {
        "policy_id": policy_id,
        "graph_status": "FOUND",
        "graph": {
            "eligibility": {
                "condition_tree": [
                    {
                        "group_id": f"GRP_{policy_id}_ROOT",
                        "logic": "AND",
                        "conditions": conditions,
                        "groups": children or [],
                    }
                ],
                "unknown_logic_groups": [],
                "structure_issues": [],
            }
        },
        "data_quality": {
            "hold_auto_eligibility": bool(affected),
            "affected_attributes": affected or [],
        },
    }


def _gov008():
    # 서로 다른 condition_id 5개가 하나의 OR 그룹에 있는 형태.
    bands = ["0_50_", "51_75_", "76_100_", "101_200_", "200_초과"]
    income_or = {
        "group_id": "GRP_POL_GOV_008_OR_INC",
        "logic": "OR",
        "conditions": [
            {
                "condition_id": f"COND_INC_중위소득_{band}",
                "type": "INCOME",
                "value": f"중위소득_{band}",
            }
            for band in bands
        ],
        "groups": [],
    }
    return _base_policy(
        "POL_GOV_008",
        [{"type": "HOUSING_HOUSEHOLD", "name": "북한이탈주민"}],
        children=[income_or],
        affected=["income"],
    )


def _gov025():
    return _base_policy(
        "POL_GOV_025",
        [
            {"type": "AGE", "operator": "BETWEEN", "value": "0~120"},
            {"type": "HOUSING_HOUSEHOLD", "name": "무주택세대"},
            {"type": "INCOME", "value": "중위소득_0_50_"},
        ],
        affected=["region"],
    )


def test_gov008_keeps_five_income_conditions_in_original_result():
    policy = _gov008()
    snapshot = deepcopy(policy)
    raw = check_eligibility(PROFILE, policy)
    income = [x for x in raw["conditions"] if x["field"] == "income"]
    groups = [x for x in raw["conditions"] if x["field"] == "eligibility"]

    assert len(income) == 5
    assert all(x["status"] == "needs_check" for x in income)
    assert len(groups) == 2
    assert all(x["status"] == "needs_check" for x in groups)
    assert policy == snapshot


def test_gov008_display_has_only_one_identical_income_notice():
    raw = check_eligibility(PROFILE, _gov008())
    snapshot = deepcopy(raw)
    displayed = summarize_conditions_for_display(raw["conditions"])

    assert len([x for x in displayed if x["field"] == "income"]) == 1
    assert len([x for x in displayed if x["field"] == "eligibility"]) == 2
    assert len([x for x in raw["conditions"] if x["field"] == "income"]) == 5
    assert raw == snapshot


def test_different_income_reasons_are_preserved():
    items = [
        {"field": "income", "label": "소득", "status": "needs_check", "reason": "기준 A"},
        {"field": "income", "label": "소득", "status": "needs_check", "reason": "기준 A"},
        {"field": "income", "label": "소득", "status": "needs_check", "reason": "기준 B"},
        {"field": "income", "label": "소득", "status": "eligible", "reason": "검증된 조건"},
    ]

    displayed = summarize_conditions_for_display(items)
    assert [x["reason"] for x in displayed] == ["기준 A", "기준 B", "검증된 조건"]
    assert len(items) == 4


def test_group_and_other_field_rows_are_not_deduplicated():
    items = [
        {"field": "eligibility", "label": "자격", "status": "needs_check", "reason": "OR 그룹"},
        {"field": "eligibility", "label": "자격", "status": "needs_check", "reason": "OR 그룹"},
        {"field": "housing", "label": "기타", "status": "needs_check", "reason": "가구"},
        {"field": "housing", "label": "기타", "status": "needs_check", "reason": "가구"},
    ]

    assert len(summarize_conditions_for_display(items)) == 4


def test_gov025_age_zero_reason_is_cautious_without_status_change():
    raw = check_eligibility(PROFILE, _gov025())
    age = next(x for x in raw["conditions"] if x["field"] == "age")
    groups = [x for x in raw["conditions"] if x["field"] == "eligibility"]

    assert age["status"] == "eligible"
    assert "만 0세 이상" not in age["reason"]
    assert "만 0~120세" not in age["reason"]
    assert "연령 제한 없음" not in age["reason"]
    assert "공식 공고" in age["reason"]
    assert all(x["status"] == "needs_check" for x in groups)
    assert raw["reception_status"] == "unknown"


def test_nonzero_lower_bound_with_120_keeps_existing_wording():
    policy = _gov025()
    policy["graph"]["eligibility"]["condition_tree"][0]["conditions"][0]["value"] = "19~120"
    raw = check_eligibility(PROFILE, policy)
    age = next(x for x in raw["conditions"] if x["field"] == "age")

    assert age["status"] == "eligible"
    assert "만 19세 이상" in age["reason"]


def test_display_helper_rejects_invalid_types():
    with pytest.raises(TypeError):
        summarize_conditions_for_display({})
    with pytest.raises(TypeError):
        summarize_conditions_for_display([None])
