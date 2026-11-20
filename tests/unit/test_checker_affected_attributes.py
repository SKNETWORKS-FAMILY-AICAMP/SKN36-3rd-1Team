"""Checker가 공통 목록과 검색 계층의 data_quality.affected_attributes를 그대로 받아들이는지 확인한다.

범위: 검색 결과 → Checker 수용 쪽만 본다. 검색 계층이 만드는 값이 공통 목록과 맞는지는
검색 산출값 대조 작업에서 따로 확인한다. DB·LLM 호출 없음, 정책은 가상(sample_only).
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.eligibility import checker  # noqa: E402
from backend.eligibility.checker import check_eligibility  # noqa: E402
from database import hybrid_search as hs  # noqa: E402
from database.eligibility_fields import ALL_ELIGIBILITY_FIELDS, SCOPED_ELIGIBILITY_FIELDS  # noqa: E402

PROFILE = {"age": 25, "region": None, "income": None}
# Checker가 조건 행이 없어도 확인 필요 행을 직접 추가하는 항목
ROW_FIELDS = {"age", "region", "income"}


def _policy(quality):
    """가상 정책 (sample_only): 25세가 충족하는 나이 조건 하나만 가진 AND 루트."""
    root = {"group_id": "ROOT", "logic": "AND", "groups": [],
            "conditions": [{"type": "AGE", "operator": "BETWEEN", "value": "19~35", "condition_id": "COND_AGE"}]}
    return {"policy_id": "POL_SAMPLE_ATTRS", "graph_status": "FOUND",
            "graph": {"policy": {"application_period": "상시신청"},
                      "eligibility": {"condition_tree": [root], "unknown_logic_groups": [], "structure_issues": []}},
            "data_quality": quality}


def _statuses(result, field):
    return [item["status"] for item in result["conditions"] if item["field"] == field]


def _search_quality(code):
    """검색 계층의 실제 함수로 경고 코드 하나에 대한 data_quality 요약을 만든다."""
    return hs._hold_summary([hs._annotate({"code": code, "level": "suspected", "message": "sample_only"})])


# --- 1. 공통 목록 값 하나하나 -------------------------------------------------------

@pytest.mark.parametrize("field", sorted(SCOPED_ELIGIBILITY_FIELDS))
def test_each_shared_field_is_accepted_as_is(field):
    quality = {"hold_auto_eligibility": True, "affected_attributes": [field]}
    assert checker._affected_fields(quality, {}, False) == {field}


@pytest.mark.parametrize("field", sorted(SCOPED_ELIGIBILITY_FIELDS))
def test_each_shared_field_holds_only_its_scope(field):
    result = check_eligibility(PROFILE, _policy({"hold_auto_eligibility": True, "affected_attributes": [field]}))

    assert _statuses(result, "eligibility") == ["needs_check"]
    if field == "age":
        assert _statuses(result, "age") == ["needs_check"]
    else:
        # 영향받지 않는 나이 판정은 그대로 둔다.
        assert _statuses(result, "age") == ["eligible"]
    if field in ROW_FIELDS - {"age"}:
        assert _statuses(result, field) == ["needs_check"]


# --- 2. 전체 보류(all)와 섞인 값 ---------------------------------------------------

@pytest.mark.parametrize("attrs", [["all"], ["all", "income"], ["age", "all", "region"]])
def test_all_takes_precedence_when_mixed(attrs):
    # 검색 계층은 경고 여러 개의 affects를 합치므로 "all"과 개별 항목이 함께 올 수 있다.
    quality = {"hold_auto_eligibility": True, "affected_attributes": attrs}
    assert ALL_ELIGIBILITY_FIELDS in checker._affected_fields(quality, {}, False)

    result = check_eligibility(PROFILE, _policy(quality))
    assert all(item["status"] == "needs_check" for item in result["conditions"])


# --- 3. 보류 아님·입력 이상 ----------------------------------------------------------

def test_attributes_ignored_when_hold_is_false():
    quality = {"hold_auto_eligibility": False, "affected_attributes": ["age", "all"]}
    assert checker._affected_fields(quality, {}, False) == set()

    result = check_eligibility(PROFILE, _policy(quality))
    assert _statuses(result, "age") == ["eligible"]
    assert _statuses(result, "eligibility") == ["eligible"]


@pytest.mark.parametrize("attrs", [[], None, "age", ["age", ""], ["age", 3], ["education"], ["target"],
                                   ["age", "education"], ["region", "unknown_field"]])
def test_unusable_attributes_fall_back_to_all(attrs):
    # education·target은 Checker 조건 필드명이지만 공통 목록에는 없으므로 보수적으로 전체 보류한다.
    # 정상 값과 섞여 와도 모르는 값을 건너뛰고 일부만 보류하지 않는다.
    quality = {"hold_auto_eligibility": True, "affected_attributes": attrs}
    assert checker._affected_fields(quality, {}, False) == {ALL_ELIGIBILITY_FIELDS}


@pytest.mark.parametrize("raw, expected", [("AGE", "age"), (" Income ", "income"), ("residence", "region"),
                                           ("location", "region"), ("household", "housing"),
                                           ("housing_household", "housing")])
def test_case_whitespace_and_aliases_are_normalized(raw, expected):
    quality = {"hold_auto_eligibility": True, "affected_attributes": [raw]}
    assert checker._affected_fields(quality, {}, False) == {expected}


# --- 4. 검색 계층 실제 출력 → Checker -------------------------------------------------

@pytest.mark.parametrize("code", sorted(hs.QUALITY_CODE_META))
def test_every_search_quality_code_is_accepted_without_fallback(code):
    meta = hs.QUALITY_CODE_META[code]
    quality = _search_quality(code)
    accepted = checker._affected_fields(quality, {}, False)

    if not meta["blocks_auto_eligibility"]:
        assert quality["hold_auto_eligibility"] is False
        assert accepted == set()
        return
    # 검색이 지정한 범위를 그대로 받는다. 모르는 값이라서 전체 보류로 넘어가지 않는다.
    assert accepted == set(meta["affects"])

    result = check_eligibility(PROFILE, _policy(quality))
    assert _statuses(result, "eligibility") == ["needs_check"]
    held_age = "age" in accepted or ALL_ELIGIBILITY_FIELDS in accepted
    assert _statuses(result, "age") == (["needs_check"] if held_age else ["eligible"])


def test_combined_search_warnings_are_accepted():
    # 서로 다른 경고가 합쳐진 요약 (예: 지역 상세 + 소득 5구간 OR + 빈 NOT 그룹)
    codes = [hs.QUALITY_REGION_DETAIL, "SUSPECTED_INCOME_ALL_BANDS_OR", hs.QUALITY_EMPTY_NOT_GROUP]
    quality = hs._hold_summary([hs._annotate({"code": c, "level": "suspected", "message": "sample_only"})
                                for c in codes])
    assert quality["affected_attributes"] == ["exclusion", "income", "region"]
    assert checker._affected_fields(quality, {}, False) == {"exclusion", "income", "region"}

    result = check_eligibility(PROFILE, _policy(quality))
    assert _statuses(result, "age") == ["eligible"]
    assert _statuses(result, "region") == ["needs_check"]
    assert _statuses(result, "income") == ["needs_check"]
    assert _statuses(result, "eligibility") == ["needs_check"]


def test_static_flag_values_are_accepted_through_search_summary():
    # 정적 목록 경고는 affects를 목록 값으로 직접 넣는다. 그 경로로 들어온 값도 그대로 받는다.
    # 공개 함수 assess_policy_data_quality를 거쳐 검색 결과와 같은 요약을 만든다.
    flags = [{"code": "MARRIAGE_CHILD_NOT_MODELED", "evidence_level": "handoff_text", "severity": "medium",
              "handoff_semantic_match": "suspected", "affects": ["marriage", "children"], "evidence": "sample_only"}]
    quality = hs.assess_policy_data_quality({"policy": {}}, flags)

    assert quality["hold_auto_eligibility"] is True
    assert quality["affected_attributes"] == ["children", "marriage"]
    assert checker._affected_fields(quality, {}, False) == {"children", "marriage"}
