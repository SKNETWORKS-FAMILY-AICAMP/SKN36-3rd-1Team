"""#36 소득·가구 원문 검토 회귀 테스트; DB·LLM 연결 없음."""

from copy import deepcopy

import pytest

from backend.eligibility.checker import check_eligibility


def policy(*, policy_node=None, logic="AND", conditions=None, quality=None):
    return {
        "policy_id": "POL_TEST_SOURCE_REVIEW",
        "graph_status": "FOUND",
        "graph": {
            "policy": policy_node or {},
            "eligibility": {
                "condition_tree": [{
                    "group_id": "ROOT", "logic": logic,
                    "conditions": conditions if conditions is not None else [{
                        "type": "AGE", "operator": "BETWEEN", "value": "19~35",
                    }],
                    "groups": [],
                }],
                "unknown_logic_groups": [], "structure_issues": [],
            },
        },
        "data_quality": quality or {"hold_auto_eligibility": False, "affected_attributes": []},
    }


def matching(result, label):
    return [x for x in result["conditions"] if x["label"] == label]


@pytest.mark.parametrize("status", ["not_specified", "not_reviewed", "specified", None, "unexpected"])
def test_non_needs_review_does_not_create_extra_items(status):
    result = check_eligibility(
        {"age": 25}, policy(policy_node={"income_source_review": status})
    )
    assert len(result["conditions"]) == 2
    assert matching(result, "소득 요건") == []
    assert matching(result, "자녀 월령") == []


@pytest.mark.parametrize("prefix,label,expected_phrase", [
    ("income", "소득 요건", "필요한 소득 정보를"),
    ("household", "가구 요건", "가구 관련 추가 정보를"),
])
def test_needs_review_adds_existing_reason_without_new_contract_key(prefix, label, expected_phrase):
    node = {
        f"{prefix}_source_review": "needs_review",
        f"{prefix}_followup": "ask_user",
        f"{prefix}_check_point": "공식 선정 기준",
        f"{prefix}_source_note": "추가 서류 확인",
    }
    before = deepcopy(node)
    result = check_eligibility({"age": 25}, policy(policy_node=node))
    found = matching(result, label)
    assert len(found) == 1
    assert found[0]["status"] == "needs_check"
    assert "공식 선정 기준" in found[0]["reason"]
    assert "추가 서류 확인" in found[0]["reason"]
    assert expected_phrase in found[0]["reason"]
    assert "followup" not in found[0]
    assert {x["status"] for x in matching(result, "나이")} == {"eligible"}
    assert matching(result, "자격 요건")[0]["status"] == "needs_check"
    assert node == before


@pytest.mark.parametrize("followup", ["check_notice", "unexpected", {"value": "ask_user"}, None])
def test_notice_and_unknown_followup_are_conservative(followup):
    result = check_eligibility({"age": 25}, policy(policy_node={
        "income_source_review": "needs_review",
        "income_followup": followup,
    }))
    found = matching(result, "소득 요건")
    assert len(found) == 1
    assert "공식 공고" in found[0]["reason"]


def test_both_source_reviews_are_independent_and_no_mutation():
    p = policy(policy_node={
        "income_source_review": "needs_review",
        "household_source_review": "needs_review",
        "income_followup": "check_notice",
        "household_followup": "ask_user",
    })
    before = deepcopy(p)
    result = check_eligibility({"age": 25}, p)
    assert len(matching(result, "소득 요건")) == 1
    assert len(matching(result, "가구 요건")) == 1
    assert p == before


def test_needs_review_does_not_overwrite_clear_and_ineligible():
    result = check_eligibility({"age": 40}, policy(policy_node={
        "income_source_review": "needs_review",
    }))
    assert matching(result, "나이")[0]["status"] == "ineligible"
    assert matching(result, "자격 요건")[0]["status"] == "ineligible"


def test_unknown_group_remains_needs_check_in_mvp():
    result = check_eligibility({"age": 25}, policy(
        logic="UNKNOWN", policy_node={"household_source_review": "needs_review"},
    ))
    assert all(x["status"] == "needs_check" for x in result["conditions"])


def test_non_month_special_target_does_not_compare_user_age_as_child():
    result = check_eligibility(
        {"age": 25}, policy(conditions=[{
            "type": "SPECIAL_TARGET", "name": "장애인가정", "value": "장애인",
        }])
    )
    assert result["conditions"][0]["status"] == "needs_check"


@pytest.mark.parametrize("months,expected", [(0, "eligible"), (23, "eligible"), (24, "ineligible"), (25, "ineligible"), (None, "needs_check"), (True, "needs_check")])
def test_child_months_exclusive_upper_bound(months, expected):
    profile = {"age": 30}
    if months is not None:
        profile["child_age_months"] = months
    result = check_eligibility(profile, policy(conditions=[{
        "type": "SPECIAL_TARGET", "unit": "개월", "operator": "LT",
        "value": "24", "name": "24개월 미만 자녀",
    }]))
    assert matching(result, "자녀 월령")[0]["status"] == expected


def test_child_months_ambiguous_operator_does_not_auto_qualify():
    result = check_eligibility({"age": 30, "child_age_months": 23}, policy(conditions=[{
        "type": "SPECIAL_TARGET", "unit": "개월", "operator": "BETWEEN",
        "value": "0~24", "name": "자녀 지원",
    }]))
    assert matching(result, "자녀 월령")[0]["status"] == "needs_check"


def test_child_months_between_does_not_guess_exclusive_bound_even_with_name():
    result = check_eligibility({"age": 30, "child_age_months": 24}, policy(conditions=[{
        "type": "SPECIAL_TARGET", "unit": "개월", "operator": "BETWEEN",
        "value": "0~24", "name": "24개월 미만 자녀",
    }]))
    assert matching(result, "자녀 월령")[0]["status"] == "needs_check"


def test_income_ask_user_does_not_reask_already_given_income():
    result = check_eligibility({"age": 25, "income": 260}, policy(policy_node={
        "income_source_review": "needs_review",
        "income_followup": "ask_user",
    }))
    reason = matching(result, "소득 요건")[0]["reason"]
    assert "입력된 소득" in reason
    assert "소득 정보를 추가로 확인" not in reason


def test_child_months_explicitly_over_limit_is_not_automatically_invalid_input():
    result = check_eligibility({"age": 30, "child_age_months": 300}, policy(conditions=[{
        "type": "SPECIAL_TARGET", "unit": "개월", "operator": "LT",
        "value": "24", "name": "24개월 미만 자녀",
    }]))
    assert matching(result, "자녀 월령")[0]["status"] == "ineligible"


def test_not_specified_preserves_existing_income_condition_without_extra_request():
    p = policy(
        policy_node={"income_source_review": "not_specified", "income_followup": "ask_user"},
        conditions=[{"type": "INCOME", "name": "기준 중위소득", "value": "100%"}],
    )
    result = check_eligibility({"age": 25}, p)
    income_items = [x for x in result["conditions"] if x["field"] == "income"]
    assert len(income_items) == 1
    assert "추가로 확인할 내용" not in income_items[0]["reason"]
    assert "followup" not in income_items[0]


def test_source_review_eligible_group_is_held_but_clear_age_condition_is_not():
    p = policy(policy_node={
        "income_source_review": "needs_review", "income_followup": "check_notice",
    })
    result = check_eligibility({"age": 25}, p)
    assert matching(result, "자격 요건")[0]["status"] == "needs_check"
    assert matching(result, "나이")[0]["status"] == "eligible"
    assert all("followup" not in item for item in result["conditions"])


def test_income_nonfinite_profile_is_not_treated_as_provided():
    result = check_eligibility({"age": 25, "income": float("inf")}, policy(policy_node={
        "income_source_review": "needs_review", "income_followup": "ask_user",
    }))
    assert "추가로 확인" in matching(result, "소득 요건")[0]["reason"]
