
"""#42 나이 조건 미추출 안내 테스트. 모두 가상 그래프이며 DB/API 호출은 없다."""

from copy import deepcopy

import pytest

from backend.eligibility.checker import check_eligibility


TARGET_IDS = [
    "POL_GOV_008", "POL_GOV_046",
    "POL_YC_002", "POL_YC_014", "POL_YC_018", "POL_YC_021",
    "POL_YC_048", "POL_YC_049", "POL_YC_057", "POL_YC_063", "POL_YC_064",
    "POL_YC_003", "POL_YC_007", "POL_YC_009", "POL_YC_010",
    "POL_YC_015", "POL_YC_017", "POL_YC_028",
]


def policy(policy_id="POL_TEST", description="만 18~29세", logic="AND", conditions=None):
    if conditions is None:
        conditions = [{"type": "EDUCATION", "name": "학력 요건"}]
    return {
        "policy_id": policy_id,
        "graph_status": "FOUND",
        "graph": {
            "policy": {"eligibility": description},
            "eligibility": {
                "condition_tree": [
                    {
                        "group_id": "ROOT",
                        "logic": logic,
                        "conditions": conditions,
                        "groups": [],
                    }
                ],
                "unknown_logic_groups": [],
                "structure_issues": [],
            },
        },
        "data_quality": {
            "hold_auto_eligibility": False,
            "affected_attributes": [],
        },
    }


def age_rows(result):
    return [row for row in result["conditions"] if row["field"] == "age"]


@pytest.mark.parametrize("policy_id", TARGET_IDS)
def test_issue42_target_age_notice(policy_id):
    text = "연령: 0~0세 (API 오류; 공식 확인 필요)" if policy_id in {
        "POL_YC_003", "POL_YC_007", "POL_YC_009", "POL_YC_010",
        "POL_YC_015", "POL_YC_017", "POL_YC_028",
    } else "지원 연령: 만 18~29세"
    source = policy(policy_id, text)
    snapshot = deepcopy(source)
    result = check_eligibility({"age": 40}, source)
    assert source == snapshot
    assert len(age_rows(result)) == 1
    assert age_rows(result)[0] == {
        "field": "age", "label": "나이",
        "status": "needs_check",
        "reason": "이 정책의 나이 기준이 데이터에 정리되지 않아 공식 원문에서 확인이 필요합니다.",
    }
    # 다른 조건의 세부 판정/결합 상태는 기존대로 유지되어야 한다.
    assert result["conditions"][0]["field"] == "education"
    assert result["conditions"][1]["field"] == "eligibility"
    assert result["conditions"][1]["status"] == "needs_check"


@pytest.mark.parametrize("policy_id", [
    "POL_GOV_026", "POL_GOV_028", "POL_GOV_044",
    "POL_GOV_053", "POL_GOV_065",
])
def test_no_source_age_limit_no_notice(policy_id):
    assert age_rows(check_eligibility({"age": 40}, policy(
        policy_id, "원문 검증 완료: 별도 나이 제한 없음"
    ))) == []


def test_monthly_child_target_not_applicant_age():
    source = policy(
        "POL_GOV_050", "자녀 만 18세 미만 및 24개월 미만 기준",
        conditions=[{
            "type": "SPECIAL_TARGET", "operator": "LT", "value": "24", "unit": "개월",
        }],
    )
    result = check_eligibility({"age": 40}, source)
    assert age_rows(result) == []
    assert any(row["field"] == "children" for row in result["conditions"])


def test_existing_nested_age_not_duplicated():
    source = policy(conditions=[])
    source["graph"]["eligibility"]["condition_tree"][0]["groups"] = [{
        "group_id": "AGE", "logic": "AND",
        "conditions": [{"type": "AGE", "operator": "BETWEEN", "value": "18~29"}],
        "groups": [],
    }]
    result = check_eligibility({"age": 40}, source)
    assert len(age_rows(result)) == 1
    assert age_rows(result)[0]["status"] == "ineligible"
    assert age_rows(result)[0]["label"] == "나이"


@pytest.mark.parametrize("description", [
    "미취업 청년 지원; 나이 제한 없음", "지원 대상: 만 24개월 미만 자녀",
    "접수기간: 2026년 11월", "20세대 공급", "", None,
])
def test_no_numeric_applicant_age_mention_no_notice(description):
    assert age_rows(check_eligibility({"age": 30}, policy(description=description))) == []


def test_unknown_group_does_not_add_age_notice():
    source = policy(logic="UNKNOWN")
    assert age_rows(check_eligibility({"age": 30}, source)) == []


def test_nested_unknown_group_does_not_add_age_notice():
    source = policy()
    source["graph"]["eligibility"]["condition_tree"][0]["groups"] = [{
        "group_id": "NESTED", "logic": "UNKNOWN", "conditions": [], "groups": []
    }]
    assert age_rows(check_eligibility({"age": 30}, source)) == []


def test_unknown_meta_flag_does_not_add_age_notice():
    source = policy()
    source["graph"]["eligibility"]["unknown_logic_groups"] = ["ROOT"]
    assert age_rows(check_eligibility({"age": 30}, source)) == []


def test_global_quality_hold_does_not_add_age_notice():
    source = policy()
    source["data_quality"] = {
        "hold_auto_eligibility": True,
        "affected_attributes": ["all"],
    }
    assert age_rows(check_eligibility({"age": 30}, source)) == []


def test_existing_age_quality_notice_not_duplicated():
    source = policy()
    source["data_quality"] = {
        "hold_auto_eligibility": True,
        "affected_attributes": ["age"],
    }
    result = check_eligibility({"age": 30}, source)
    assert len(age_rows(result)) == 1


@pytest.mark.parametrize("review_kind", ["income", "household"])
def test_source_review_does_not_suppress_missing_age_notice(review_kind):
    source = policy()
    source["graph"]["policy"][f"{review_kind}_source_review"] = "needs_review"
    result = check_eligibility({"age": 30}, source)
    assert len(age_rows(result)) == 1
    assert age_rows(result)[0]["label"] == "나이"
    assert any(
        row["field"] == ("income" if review_kind == "income" else "housing")
        and row["status"] == "needs_check"
        for row in result["conditions"]
    )


def test_missing_tree_does_not_add_notice():
    source = policy()
    source["graph"]["eligibility"]["condition_tree"] = []
    assert age_rows(check_eligibility({"age": 30}, source)) == []


def test_bad_tree_does_not_add_notice():
    source = policy()
    source["graph"]["eligibility"]["condition_tree"][0]["groups"] = "INVALID"
    assert age_rows(check_eligibility({"age": 30}, source)) == []


def test_month_string_does_not_count_as_applicant_age():
    source = policy(description="자녀 24개월 이내 지원")
    assert age_rows(check_eligibility({"age": 30}, source)) == []


def test_eligible_group_state_unchanged_by_notice():
    source = policy(description="만 18~29세", conditions=[
        {"type": "SPECIAL_TARGET", "operator": "LT", "value": "24", "unit": "개월"},
    ])
    # 자녀 월령 정책은 신청자 나이 누락에 해당하지 않는다.
    result = check_eligibility({"age": 30, "child_age_months": 12}, source)
    assert age_rows(result) == []
    assert [row["status"] for row in result["conditions"]] == ["eligible", "eligible"]


def test_structural_error_in_nested_condition_does_not_add_age_notice():
    source = policy(conditions=[None])
    assert age_rows(check_eligibility({"age": 30}, source)) == []


def test_many_root_groups_are_not_treated_as_known_combination():
    source = policy()
    source["graph"]["eligibility"]["condition_tree"].append({
        "group_id": "SECOND", "logic": "AND", "conditions": [], "groups": [],
    })
    assert age_rows(check_eligibility({"age": 30}, source)) == []


def test_age_notice_uses_same_label_as_existing_age_conditions():
    result = check_eligibility({"age": 30}, policy())
    assert age_rows(result)[0]["label"] == "나이"
    assert age_rows(result)[0]["status"] == "needs_check"


def test_individual_income_quality_warning_does_not_hide_age_notice():
    source = policy()
    source["data_quality"] = {
        "hold_auto_eligibility": True,
        "affected_attributes": ["income"],
    }
    result = check_eligibility({"age": 30}, source)
    assert len(age_rows(result)) == 1
    assert any(item["field"] == "income" for item in result["conditions"])


def test_tree_walk_is_called_once_per_group(monkeypatch):
    import backend.eligibility.checker as checker

    source = policy()
    root = source["graph"]["eligibility"]["condition_tree"][0]
    root["groups"].append({
        "group_id": "NESTED", "logic": "OR",
        "conditions": [{"type": "EDUCATION", "name": "학력"}],
        "groups": [],
    })

    visits = []
    original_walk = checker._walk

    def counting_walk(group, *args, **kwargs):
        visits.append(id(group))
        return original_walk(group, *args, **kwargs)

    monkeypatch.setattr(checker, "_walk", counting_walk)
    result = checker.check_eligibility({"age": 30}, source)
    assert len(age_rows(result)) == 1
    assert len(visits) == len(set(visits)) == 2
