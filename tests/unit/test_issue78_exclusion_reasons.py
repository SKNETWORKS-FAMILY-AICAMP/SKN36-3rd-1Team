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

# ----- #78: composite NOT proofs; preserve conjunction/disjunction semantics -----

def _issue78_compound_age_policy():
    policy = sample_exclusion_policy()
    root = policy["graph"]["eligibility"]["condition_tree"][0]
    root["conditions"] = []
    root["groups"][0]["groups"] = [
        {
            "group_id": "EXCLUSION_AND",
            "logic": "AND",
            "conditions": [
                {"type": "AGE", "name": "65세 이상", "operator": "BETWEEN", "value": "65~120"},
                {"type": "AGE", "name": "80세 이하", "operator": "BETWEEN", "value": "1~80"},
            ],
            "groups": [],
        }
    ]
    return policy


@pytest.mark.parametrize(
    "age, root_status, excluded",
    [(64, checker.OK, False), (70, checker.NO, True), (81, checker.OK, False)],
)
def test_issue78_composite_and_boundaries(age, root_status, excluded):
    result = checker.check_eligibility({"age": age}, _issue78_compound_age_policy())
    assert result["conditions"][-1]["status"] == root_status
    reasons = result["exclusion_reasons"]
    if excluded:
        assert reasons is not None and len(reasons) == 1
        assert reasons[0]["field"] == "age"
        assert "복합 제외 조건" in reasons[0]["label"]
        assert "65" in reasons[0]["reason"]
        assert "80" in reasons[0]["reason"]
        assert "모두 충족" in reasons[0]["reason"]
    else:
        assert reasons is None


@pytest.mark.parametrize("age, composite", [(70, True), (100, False)])
def test_issue78_or_of_and_proof_not_split(age, composite):
    policy = _issue78_compound_age_policy()
    root = policy["graph"]["eligibility"]["condition_tree"][0]
    exclusion = root["groups"][0]
    compound = exclusion["groups"][0]
    exclusion["groups"] = [{
        "group_id": "EXCLUSION_OR",
        "logic": "OR",
        "conditions": [
            {"type": "AGE", "name": "95세 이상", "operator": "BETWEEN", "value": "95~110"}
        ],
        "groups": [compound],
    }]
    result = checker.check_eligibility({"age": age}, policy)
    assert result["conditions"][-1]["status"] == checker.NO
    reasons = result["exclusion_reasons"]
    assert reasons is not None and len(reasons) == 1
    assert ("복합 제외 조건" in reasons[0]["label"]) is composite
    if composite:
        assert "65" in reasons[0]["reason"] and "80" in reasons[0]["reason"]
    else:
        assert "95" in reasons[0]["reason"]


def test_issue78_and_of_or_proof_is_single_joint_reason():
    policy = _issue78_compound_age_policy()
    root = policy["graph"]["eligibility"]["condition_tree"][0]
    compound = root["groups"][0]["groups"][0]
    compound["conditions"] = [compound["conditions"][1]]
    compound["groups"] = [{
        "group_id": "CHOICE_OR",
        "logic": "OR",
        "conditions": [
            {"type": "AGE", "name": "65세 이상", "operator": "BETWEEN", "value": "65~120"},
            {"type": "AGE", "name": "66세 이상", "operator": "BETWEEN", "value": "66~120"},
        ],
        "groups": [],
    }]
    result = checker.check_eligibility({"age": 70}, policy)
    assert result["conditions"][-1]["status"] == checker.NO
    reasons = result["exclusion_reasons"]
    assert reasons is not None and len(reasons) == 1
    assert "복합 제외 조건" in reasons[0]["label"]
    assert "80" in reasons[0]["reason"]


def test_issue78_two_or_terms_are_independent_exclusions():
    policy = _issue78_compound_age_policy()
    root = policy["graph"]["eligibility"]["condition_tree"][0]
    disjunction = root["groups"][0]["groups"][0]
    disjunction["logic"] = "OR"
    result = checker.check_eligibility({"age": 70}, policy)
    assert result["conditions"][-1]["status"] == checker.NO
    reasons = result["exclusion_reasons"]
    assert reasons is not None and len(reasons) == 2
    assert all("복합 제외 조건" not in r["label"] for r in reasons)


def test_issue78_and_with_unverified_full_range_is_not_confirmed():
    policy = _issue78_compound_age_policy()
    root = policy["graph"]["eligibility"]["condition_tree"][0]
    compound = root["groups"][0]["groups"][0]
    compound["conditions"][1]["value"] = "0~120"
    result = checker.check_eligibility({"age": 70}, policy)
    assert result["conditions"][-1]["status"] == checker.NO
    assert result["exclusion_reasons"] is None


@pytest.mark.parametrize("affected", [["age"], ["all"], ["exclusion"]])
def test_issue78_compound_quality_hold_does_not_confirm_exclusion(affected):
    policy = _issue78_compound_age_policy()
    policy["data_quality"] = {
        "hold_auto_eligibility": True,
        "affected_attributes": affected,
    }
    result = checker.check_eligibility({"age": 70}, policy)
    assert result["exclusion_reasons"] is None


def test_issue78_incomplete_compound_and_is_not_confirmed():
    policy = _issue78_compound_age_policy()
    root = policy["graph"]["eligibility"]["condition_tree"][0]
    compound = root["groups"][0]["groups"][0]
    compound["conditions"].append({
        "type": "SPECIAL_TARGET",
        "name": "원문 확인이 필요한 추가 조건",
        "value": "미검증",
    })
    result = checker.check_eligibility({"age": 70}, policy)
    assert result["conditions"][-1]["status"] == checker.CHECK
    assert result["exclusion_reasons"] is None


def test_issue78_composite_does_not_recalculate_conditions(monkeypatch):
    calls = []
    original = checker._condition

    def counted(condition, profile):
        calls.append(condition)
        return original(condition, profile)

    monkeypatch.setattr(checker, "_condition", counted)
    result = checker.check_eligibility({"age": 70}, _issue78_compound_age_policy())
    assert result["exclusion_reasons"]
    assert len(calls) == 2

# ----- #78 P3: nested NOT, negative evidence, and De Morgan regressions -----


def _issue78_nested_not_policy():
    """NOT(AND(age>=75, NOT(age 80~85 exception)))."""
    policy = _issue78_compound_age_policy()
    root = policy["graph"]["eligibility"]["condition_tree"][0]
    conjunction = root["groups"][0]["groups"][0]
    conjunction["conditions"] = [
        {"type": "AGE", "name": "75세 이상", "operator": "BETWEEN", "value": "75~120"},
    ]
    conjunction["groups"] = [{
        "group_id": "INNER_NOT_EXCEPTION",
        "logic": "NOT",
        "conditions": [{
            "type": "AGE", "name": "80~85세 예외", "operator": "BETWEEN", "value": "80~85",
        }],
        "groups": [],
    }]
    return policy


@pytest.mark.parametrize(
    "age, status, excluded",
    [
        (74, checker.OK, False),
        (75, checker.NO, True),
        (79, checker.NO, True),
        (80, checker.OK, False),
        (82, checker.OK, False),
        (85, checker.OK, False),
        (86, checker.NO, True),
        (120, checker.NO, True),
        (None, checker.CHECK, False),
    ],
)
def test_issue78_nested_not_exception_boundaries(age, status, excluded):
    result = checker.check_eligibility({"age": age}, _issue78_nested_not_policy())
    assert result["conditions"][-1]["status"] == status
    reasons = result["exclusion_reasons"]
    if excluded:
        assert reasons is not None and len(reasons) == 1
        assert reasons[0]["field"] == "age"
        assert "복합 제외 조건" in reasons[0]["label"]
        assert "75" in reasons[0]["reason"]
        assert "80~85세 예외에 해당하지 않음" in reasons[0]["reason"]
    else:
        assert reasons is None


def test_issue78_nested_not_inside_or_remains_a_single_composite_proof():
    policy = _issue78_nested_not_policy()
    root = policy["graph"]["eligibility"]["condition_tree"][0]
    compound = root["groups"][0]["groups"][0]
    root["groups"][0]["groups"] = [{
        "group_id": "EXCLUSION_OR", "logic": "OR", "conditions": [
            {"type": "AGE", "name": "60~65세", "operator": "BETWEEN", "value": "60~65"},
        ], "groups": [compound],
    }]
    for age, expected_composite in ((79, True), (82, False), (63, False)):
        result = checker.check_eligibility({"age": age}, policy)
        reasons = result["exclusion_reasons"]
        if age == 82:
            assert reasons is None
        else:
            assert reasons is not None and len(reasons) == 1
            assert ("복합 제외 조건" in reasons[0]["label"]) is expected_composite


def test_issue78_nested_not_or_false_requires_all_failed_leaves():
    policy = _issue78_nested_not_policy()
    root = policy["graph"]["eligibility"]["condition_tree"][0]
    inner = root["groups"][0]["groups"][0]["groups"][0]
    inner["conditions"] = []
    inner["groups"] = [{
        "group_id": "EXCEPTIONS_OR", "logic": "OR", "conditions": [
            {"type": "AGE", "name": "80~82세 예외", "operator": "BETWEEN", "value": "80~82"},
            {"type": "AGE", "name": "83~85세 예외", "operator": "BETWEEN", "value": "83~85"},
        ], "groups": [],
    }]
    excluded = checker.check_eligibility({"age": 79}, policy)
    assert excluded["conditions"][-1]["status"] == checker.NO
    assert excluded["exclusion_reasons"] is not None
    assert len(excluded["exclusion_reasons"]) == 1
    reason = excluded["exclusion_reasons"][0]["reason"]
    assert "80~82세 예외에 해당하지 않음" in reason
    assert "83~85세 예외에 해당하지 않음" in reason
    assert checker.check_eligibility({"age": 84}, policy)["exclusion_reasons"] is None


def test_issue78_nested_not_and_false_needs_one_failed_leaf():
    policy = _issue78_nested_not_policy()
    root = policy["graph"]["eligibility"]["condition_tree"][0]
    inner = root["groups"][0]["groups"][0]["groups"][0]
    inner["conditions"] = []
    inner["groups"] = [{
        "group_id": "EXCEPTIONS_AND", "logic": "AND", "conditions": [
            {"type": "AGE", "name": "80~85세 예외", "operator": "BETWEEN", "value": "80~85"},
            {"type": "AGE", "name": "84~90세 예외", "operator": "BETWEEN", "value": "84~90"},
        ], "groups": [],
    }]
    for age in (79, 82):
        result = checker.check_eligibility({"age": age}, policy)
        assert result["conditions"][-1]["status"] == checker.NO
        assert result["exclusion_reasons"] is not None
        assert len(result["exclusion_reasons"]) == 1
    assert checker.check_eligibility({"age": 84}, policy)["exclusion_reasons"] is None


def test_issue78_double_nested_not_recovers_positive_evidence():
    policy = _issue78_nested_not_policy()
    root = policy["graph"]["eligibility"]["condition_tree"][0]
    inner = root["groups"][0]["groups"][0]["groups"][0]
    condition = inner["conditions"][0]
    inner["conditions"] = []
    inner["groups"] = [{
        "group_id": "SECOND_NOT", "logic": "NOT", "conditions": [condition], "groups": [],
    }]
    # NOT(AND(age>=75, NOT(NOT(80~85)))) => excluded at 82, not at 79.
    result = checker.check_eligibility({"age": 82}, policy)
    assert result["conditions"][-1]["status"] == checker.NO
    assert result["exclusion_reasons"] is not None
    assert len(result["exclusion_reasons"]) == 1
    assert "80~85" in result["exclusion_reasons"][0]["reason"]
    assert checker.check_eligibility({"age": 79}, policy)["exclusion_reasons"] is None


@pytest.mark.parametrize("affected", [["age"], ["all"], ["exclusion"], []])
def test_issue78_nested_not_quality_gate_stays_conservative(affected):
    policy = _issue78_nested_not_policy()
    policy["data_quality"] = {
        "hold_auto_eligibility": True, "affected_attributes": affected,
    }
    result = checker.check_eligibility({"age": 79}, policy)
    assert result["exclusion_reasons"] is None


def test_issue78_nested_not_unknown_exception_never_confirms_exclusion():
    policy = _issue78_nested_not_policy()
    inner = policy["graph"]["eligibility"]["condition_tree"][0]["groups"][0]["groups"][0]["groups"][0]
    inner["conditions"][0]["operator"] = "UNSUPPORTED"
    result = checker.check_eligibility({"age": 79}, policy)
    assert result["conditions"][-1]["status"] == checker.CHECK
    assert result["exclusion_reasons"] is None


def test_issue78_nested_not_reuses_one_walk(monkeypatch):
    calls = []
    original = checker._condition

    def counted(condition, profile):
        calls.append(condition)
        return original(condition, profile)

    monkeypatch.setattr(checker, "_condition", counted)
    result = checker.check_eligibility({"age": 79}, _issue78_nested_not_policy())
    assert result["exclusion_reasons"] is not None
    assert len(calls) == 2  # age >= 75 and one exception; no re-evaluation
