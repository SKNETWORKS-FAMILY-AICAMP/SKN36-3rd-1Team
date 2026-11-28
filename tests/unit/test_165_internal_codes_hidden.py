"""#165: 내부 상태 코드(UNKNOWN·조건 type 값)가 사용자에게 보이는 사유 문구에 나오지 않는다. 가상 정책(sample_only)."""

import pytest

from backend.eligibility.checker import UNKNOWN_GROUP_REASON, check_eligibility

PROFILE = {"age": 25, "region": None, "income": None}


def tree_policy(logic: str, condition: dict) -> dict:
    tree = {"group_id": "G_SAMPLE", "logic": logic, "groups": [], "conditions": [condition]}
    return {"policy_id": "POL_SAMPLE", "graph_status": "FOUND",
            "graph": {"policy": {}, "eligibility": {"condition_tree": [tree]}}, "data_quality": {}}


def reasons(result: dict) -> list[str]:
    return [row["reason"] for row in result["conditions"]]


def test_unknown_group_reason_has_no_internal_enum():
    age = {"type": "AGE", "operator": "BETWEEN", "value": "19~34", "name": "가상 나이"}
    result = check_eligibility(PROFILE, tree_policy("UNKNOWN", age))
    assert result["conditions"][0]["reason"] == UNKNOWN_GROUP_REASON
    assert all("UNKNOWN" not in reason for reason in reasons(result))


@pytest.mark.parametrize("kind", ["SPECIAL_TARGET", "EMPLOYMENT", "MARRIAGE"])
def test_unnamed_condition_reason_has_no_type_code(kind):
    result = check_eligibility(PROFILE, tree_policy("AND", {"type": kind, "operator": "EQ", "value": "Y"}))
    row = result["conditions"][0]
    assert row["reason"] == "이 조건은(는) 추가 확인이 필요합니다."
    assert all(kind not in reason for reason in reasons(result))


def test_named_condition_keeps_its_name():
    result = check_eligibility(PROFILE, tree_policy("AND", {"type": "SPECIAL_TARGET", "name": "가상 대상", "value": "Y"}))
    assert result["conditions"][0]["reason"] == "가상 대상은(는) 추가 확인이 필요합니다."
