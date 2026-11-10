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


@pytest.mark.parametrize(
    "condition_status, expected_group_status",
    [
        ("eligible", "needs_check"),
        ("ineligible", "ineligible"),
    ],
)
def test_missing_age_notice_updates_only_eligible_group(
    monkeypatch, condition_status, expected_group_status,
):
    """지역의 개별 판정은 유지하고, 미확인 나이는 적합 그룹만 보류한다."""
    import backend.eligibility.checker as checker

    source = policy(
        description="지원 연령: 만 18~29세",
        conditions=[{"type": "REGION", "name": "거주 지역"}],
    )
    snapshot = deepcopy(source)
    original_condition = checker._condition

    def fake_region_condition(condition, profile):
        if condition.get("type") == "REGION":
            return checker._result(
                "region", condition_status, "지역 조건 판정 결과입니다."
            )
        return original_condition(condition, profile)

    monkeypatch.setattr(checker, "_condition", fake_region_condition)
    result = checker.check_eligibility({"age": 40, "region": "서울특별시"}, source)

    assert source == snapshot
    rows = result["conditions"]
    assert [item["field"] for item in rows] == ["region", "eligibility", "age"]
    assert rows[0]["status"] == condition_status
    assert rows[1]["status"] == expected_group_status
    assert rows[2]["status"] == "needs_check"
    assert "데이터" in rows[2]["reason"] and "원문" in rows[2]["reason"]
    if condition_status == "eligible":
        assert "나이" in rows[1]["reason"]


def test_without_age_notice_eligible_group_stays_eligible(monkeypatch):
    """설명에서 나이 언급이 없으면 종합 판정을 보류하지 않는다."""
    import backend.eligibility.checker as checker

    source = policy(
        description="지원 대상: 서울특별시 거주자",
        conditions=[{"type": "REGION", "name": "거주 지역"}],
    )
    monkeypatch.setattr(
        checker,
        "_condition",
        lambda condition, profile: checker._result(
            "region", "eligible", "거주 지역이 확인되었습니다."
        ),
    )
    result = checker.check_eligibility({"age": 40}, source)
    assert [row["status"] for row in result["conditions"]] == [
        "eligible", "eligible",
    ]


def test_income_review_and_missing_age_hold_is_not_duplicated(monkeypatch):
    """소득 원문 확인과 나이 미추출이 동시에 있어도 종합 보류는 한 줄이다."""
    import backend.eligibility.checker as checker

    source = policy(conditions=[{"type": "REGION"}])
    source["graph"]["policy"]["income_source_review"] = "needs_review"
    monkeypatch.setattr(
        checker,
        "_condition",
        lambda condition, profile: checker._result(
            "region", "eligible", "거주 지역이 확인되었습니다."
        ),
    )
    result = checker.check_eligibility({"age": 40}, source)
    conditions = result["conditions"]
    assert len([row for row in conditions if row["field"] == "age"]) == 1
    assert len([row for row in conditions if row["field"] == "eligibility"]) == 1
    group = next(row for row in conditions if row["field"] == "eligibility")
    assert group["status"] == "needs_check"
    assert "소득" in group["reason"]
    assert "나이" in group["reason"]
    assert next(row for row in conditions if row["field"] == "region")["status"] == "eligible"


@pytest.mark.parametrize(
    "root_logic,region_status,expected_root_status",
    [
        ("AND", "eligible", "needs_check"),
        ("AND", "ineligible", "ineligible"),
        ("OR", "eligible", "needs_check"),
        ("OR", "ineligible", "needs_check"),
    ],
)
def test_missing_age_holds_only_root_not_nested_group(
    monkeypatch, root_logic, region_status, expected_root_status,
):
    """중첩 그룹 적합/개별 조건 판정은 유지하고 최상위 결과만 변경한다."""
    import backend.eligibility.checker as checker

    source = policy(
        description="신청자 만 19~39세, 기타 조건은 별도 확인",
        logic=root_logic,
        conditions=[{"type": "REGION"}],
    )
    source["graph"]["eligibility"]["condition_tree"][0]["groups"] = [
        {
            "group_id": "CHILD_GROUP",
            "logic": "OR",
            "conditions": [{"type": "EDUCATION"}],
            "groups": [],
        }
    ]
    original_condition = checker._condition

    def fake_condition(condition, profile):
        if condition.get("type") == "REGION":
            return checker._result("region", region_status, "지역 판정")
        if condition.get("type") == "EDUCATION":
            return checker._result("education", "eligible", "학력 판정")
        return original_condition(condition, profile)

    monkeypatch.setattr(checker, "_condition", fake_condition)
    snapshot = deepcopy(source)
    result = checker.check_eligibility({"age": 30}, source)

    assert source == snapshot
    assert [(row["field"], row["status"]) for row in result["conditions"]] == [
        ("region", region_status),
        ("education", "eligible"),
        ("eligibility", "eligible"),  # 내부 OR 그룹은 절대 보류 처리하지 않음
        ("eligibility", expected_root_status),
        ("age", "needs_check"),
    ]
    assert "나이" not in result["conditions"][2]["reason"]
    if expected_root_status == "needs_check":
        assert "나이" in result["conditions"][3]["reason"]
    else:
        assert "나이" not in result["conditions"][3]["reason"]


@pytest.mark.parametrize(
    "description",
    [
        "지원 대상: 자녀 만 18세 미만",
        "만 18세 미만 자녀가 있는 가구",
        "아동 연령: 만 5세 이하",
        "자녀: 만 18~29세",
        "만 18세 이하의 자녀 지원",
        "청년 가구의 자녀 만 18세 미만",
    ],
)
def test_child_age_text_is_not_applicant_age_when_graph_has_no_child_condition(
    description,
):
    """그래프에 자녀 조건마저 누락돼도 아동 연령을 신청자 연령으로 오인하지 않는다."""
    result = check_eligibility({"age": 40}, policy(description=description))
    assert age_rows(result) == []


@pytest.mark.parametrize(
    "description",
    [
        "신청자 만 19~39세, 자녀 24개월 미만",
        "자녀 만 18세 미만, 신청자 만 19~39세",
        "부양 자녀가 있는 신청자 만 19~39세",
        "자녀 만 18세 미만; 신청자 연령: 만 19~39세",
        "본인 연령 만 19~39세 / 자녀 만 18세 미만",
    ],
)
def test_applicant_age_is_not_suppressed_by_existing_child_month_condition(
    description,
):
    """자녀 월령과 신청자 나이가 함께 명시되면 신청자 AGE 누락을 알린다."""
    source = policy(
        description=description,
        conditions=[{
            "type": "SPECIAL_TARGET",
            "operator": "LT",
            "value": "24",
            "unit": "개월",
        }],
    )
    result = check_eligibility({"age": 30, "child_age_months": 12}, source)
    assert len(age_rows(result)) == 1
    assert age_rows(result)[0]["status"] == "needs_check"
    assert [(row["field"], row["status"]) for row in result["conditions"]] == [
        ("children", "eligible"),
        ("eligibility", "needs_check"),
        ("age", "needs_check"),
    ]


def test_ambiguous_age_with_only_child_month_condition_is_not_assumed_applicant_age():
    """연령의 주어가 없고 자녀 조건만 있으면 신청자 나이라고 추측하지 않는다."""
    source = policy(
        description="만 18~29세",
        conditions=[{
            "type": "SPECIAL_TARGET",
            "operator": "LT",
            "value": "24",
            "unit": "개월",
        }],
    )
    result = check_eligibility({"age": 30, "child_age_months": 12}, source)
    assert age_rows(result) == []
    assert [row["status"] for row in result["conditions"]] == [
        "eligible", "eligible",
    ]


def test_source_review_does_not_turn_nested_group_into_needs_check(monkeypatch):
    """소득 원문 확인이 있는 경우에도 하위 그룹의 개별 종합 결과는 보존."""
    import backend.eligibility.checker as checker

    source = policy(
        description="신청자 만 19~39세",
        conditions=[{"type": "REGION"}],
    )
    source["graph"]["policy"]["income_source_review"] = "needs_review"
    source["graph"]["eligibility"]["condition_tree"][0]["groups"] = [
        {
            "group_id": "INNER",
            "logic": "AND",
            "conditions": [{"type": "EDUCATION"}],
            "groups": [],
        }
    ]
    monkeypatch.setattr(
        checker,
        "_condition",
        lambda condition, profile: checker._result(
            "region" if condition.get("type") == "REGION" else "education",
            "eligible",
            "개별 조건은 충족됩니다.",
        ),
    )
    result = checker.check_eligibility({"age": 30}, source)
    inner = result["conditions"][2]
    root = result["conditions"][3]
    assert (inner["field"], inner["status"]) == ("eligibility", "eligible")
    assert (root["field"], root["status"]) == ("eligibility", "needs_check")
    assert "나이" in root["reason"] and "소득" in root["reason"]
    assert "소득" not in inner["reason"] and "나이" not in inner["reason"]