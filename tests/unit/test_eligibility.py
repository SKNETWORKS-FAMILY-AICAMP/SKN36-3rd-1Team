
"""SSOK 조건 판정 단위 테스트.

v1.4.0 데이터 교정 내역에 대한 회귀 테스트 포함.
실제 DB에 연결하지 않고 가상 정책 객체로 판정 로직을 검증한다.
"""

from copy import deepcopy

import pytest

from backend.eligibility.checker import check_eligibility


def make_policy(
    logic="AND",
    value="19~35",
    *,
    extra=None,
    quality=None,
    policy_id="POL_TEST",
):
    condition = {
        "type": "AGE",
        "operator": "BETWEEN",
        "value": value,
        "condition_id": "COND_AGE",
    }

    root = {
        "group_id": "ROOT",
        "logic": logic,
        "conditions": [condition],
        "groups": extra or [],
    }

    return {
        "policy_id": policy_id,
        "graph_status": "FOUND",
        "graph": {
            "policy": {
                "application_period": "상시신청",
            },
            "eligibility": {
                "condition_tree": [root],
                "unknown_logic_groups": [],
                "structure_issues": [],
            },
        },
        "data_quality": (
            quality
            if quality is not None
            else {
                "hold_auto_eligibility": False,
                "affected_attributes": [],
            }
        ),
    }


def states(result, field):
    return [
        condition["status"]
        for condition in result["conditions"]
        if condition["field"] == field
    ]


# ==========================================================
# 기존 단위 테스트
# ==========================================================

def test_age_range_boundaries():
    policy = make_policy()

    assert states(
        check_eligibility({"age": 19}, policy), "age"
    ) == ["eligible"]

    assert states(
        check_eligibility({"age": 35}, policy), "age"
    ) == ["eligible"]

    assert states(
        check_eligibility({"age": 36}, policy), "age"
    ) == ["ineligible"]

    assert states(
        check_eligibility({"age": None}, policy), "age"
    ) == ["needs_check"]


def test_unknown_and_missing_graph():
    policy = make_policy("UNKNOWN")

    result = check_eligibility({"age": 25}, policy)

    assert all(
        condition["status"] == "needs_check"
        for condition in result["conditions"]
    )

    policy["graph"] = None
    policy["graph_status"] = "MISSING_IN_GRAPH"

    assert states(
        check_eligibility({"age": 25}, policy),
        "eligibility",
    ) == ["needs_check"]


def test_or_not_three_valued_logic():
    child = {
        "group_id": "OTHER",
        "logic": "AND",
        "conditions": [
            {
                "type": "INCOME",
                "value": "중위소득_0_50_",
            }
        ],
        "groups": [],
    }

    policy = make_policy("OR", extra=[child])

    assert states(
        check_eligibility({"age": 25}, policy),
        "eligibility",
    )[-1] == "eligible"

    assert states(
        check_eligibility({"age": 50}, policy),
        "eligibility",
    )[-1] == "needs_check"

    policy = make_policy("NOT")

    assert states(
        check_eligibility({"age": 25}, policy),
        "eligibility",
    )[-1] == "ineligible"

    assert states(
        check_eligibility({"age": 50}, policy),
        "eligibility",
    )[-1] == "eligible"


def test_quality_only_region_held_and_age_preserved():
    policy = make_policy(
        quality={
            "hold_auto_eligibility": True,
            "affected_attributes": ["region"],
        }
    )

    result = check_eligibility({"age": 25}, policy)

    assert states(result, "age") == ["eligible"]
    assert states(result, "region") == ["needs_check"]


def test_quality_age_or_all_held():
    for affected in (["age"], ["all"], []):
        policy = make_policy(
            quality={
                "hold_auto_eligibility": True,
                "affected_attributes": affected,
            }
        )

        assert states(
            check_eligibility({"age": 25}, policy),
            "age",
        ) == ["needs_check"]


def test_bad_age_and_structural_warning():
    policy = make_policy(value="19~bad")

    assert states(
        check_eligibility({"age": 25}, policy),
        "age",
    ) == ["needs_check"]

    policy = make_policy()

    policy["graph"]["eligibility"]["structure_issues"] = [
        {"issue": "cycle"}
    ]

    result = check_eligibility({"age": 25}, policy)

    assert all(
        condition["status"] == "needs_check"
        for condition in result["conditions"]
    )


def test_period_text_and_no_mutation():
    policy = make_policy()
    original = deepcopy(policy)

    result = check_eligibility({"age": 25}, policy)

    assert result["reception_status"] == "unknown"
    assert policy == original


def test_income_is_never_decided():
    policy = make_policy()

    policy["graph"]["eligibility"]["condition_tree"][0]["conditions"] = [
        {
            "type": "INCOME",
            "value": "중위소득_0_50_",
        }
    ]

    result = check_eligibility(
        {
            "age": 25,
            "income": 1000,
        },
        policy,
    )

    assert states(result, "income") == ["needs_check"]


# ==========================================================
# 신규: v1.4.0 교정 데이터 회귀 테스트
# ==========================================================

@pytest.mark.parametrize(
    "age,expected",
    [
        (11, "ineligible"),
        (12, "eligible"),
        (24, "eligible"),
        (25, "ineligible"),
    ],
)
def test_corrected_age_gov_011(age, expected):
    """POL_GOV_011: 연령 조건 12~24세 교정."""

    policy = make_policy(
        value="12~24",
        policy_id="POL_GOV_011",
    )

    result = check_eligibility({"age": age}, policy)

    assert states(result, "age") == [expected]


@pytest.mark.parametrize(
    "age,expected",
    [
        (18, "ineligible"),
        (19, "eligible"),
        (65, "eligible"),
        (120, "eligible"),
    ],
)
def test_corrected_age_gov_016(age, expected):
    """POL_GOV_016: 만 19세 이상.

    v1.4.0에서는 상한 없음이 19~120으로 표현된다.
    """

    policy = make_policy(
        value="19~120",
        policy_id="POL_GOV_016",
    )

    result = check_eligibility({"age": age}, policy)

    assert states(result, "age") == [expected]


@pytest.mark.parametrize(
    "age,expected",
    [
        (5, "ineligible"),
        (6, "eligible"),
        (64, "eligible"),
        (65, "ineligible"),
    ],
)
def test_corrected_age_gov_023(age, expected):
    """POL_GOV_023: 연령 조건 6~64세 교정."""

    policy = make_policy(
        value="6~64",
        policy_id="POL_GOV_023",
    )

    result = check_eligibility({"age": age}, policy)

    assert states(result, "age") == [expected]


def test_removed_age_gov_008_not_mistaken_for_eligible():
    """POL_GOV_008: 특정 사업에만 적용되던 연령 조건 제거.

    남은 조건을 자동 판정할 수 없는 경우를 모사한다.
    """

    policy = make_policy(
        policy_id="POL_GOV_008",
    )

    root = policy["graph"]["eligibility"]["condition_tree"][0]

    root["conditions"] = [
        {
            "type": "SPECIAL_TARGET",
            "name": "세부 사업별 지원 대상 확인",
        }
    ]

    result = check_eligibility({"age": 65}, policy)

    # 제거된 연령 조건을 임의로 판정하지 않는다.
    assert states(result, "age") == []

    # 세부 지원사업 자격은 확인이 필요하다.
    assert states(result, "target") == ["needs_check"]

    assert states(
        result, "eligibility"
    )[-1] == "needs_check"


def test_empty_group_not_mistaken_for_eligible():
    """조건이 제거된 뒤 빈 그룹이 남아도 적격으로 판단하지 않는다."""

    policy = make_policy(
        policy_id="POL_GOV_008",
    )

    root = policy["graph"]["eligibility"]["condition_tree"][0]

    root["conditions"] = []
    root["groups"] = []

    result = check_eligibility({"age": 65}, policy)

    assert states(
        result, "eligibility"
    )[-1] == "needs_check"


def test_gov_025_housing_not_auto_decided():
    """POL_GOV_025: 불필요한 OR 그룹 제거 후 무주택 요건 유지.

    실제 가구 조건은 checker에서 자동 판정하지 않는다는 것을 검증한다.
    """

    policy = make_policy(
        policy_id="POL_GOV_025",
    )

    policy["graph"]["eligibility"]["condition_tree"][0]["conditions"] = [
        {
            "type": "HOUSING_HOUSEHOLD",
            "name": "무주택 요건",
        }
    ]

    result = check_eligibility(
        {"housing": "무주택"},
        policy,
    )

    assert states(result, "housing") == ["needs_check"]

    assert states(
        result, "eligibility"
    )[-1] == "needs_check"


def test_corrected_search_region_not_assumed_eligible():
    """POL_YC_002: 검색 지역 교정이 거주 자격 확정을 의미하지 않음."""

    policy = make_policy(
        policy_id="POL_YC_002",
    )

    policy["graph"]["policy"]["region"] = "전라남도"

    policy["graph"]["eligibility"]["condition_tree"][0]["conditions"] = [
        {
            "type": "REGION",
            "operator": "EQ",
            "value": "전라남도",
        }
    ]

    result = check_eligibility(
        {"region": "전라남도"},
        policy,
    )

    assert states(result, "region") == ["needs_check"]

    assert states(
        result, "eligibility"
    )[-1] == "needs_check"
