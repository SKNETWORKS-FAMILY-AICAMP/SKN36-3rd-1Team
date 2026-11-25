"""Issue #102: 의미 점검 S10 v2 분류(assess_s10_dimension) 회귀 테스트.

DB 접속 없음 (점검 스크립트의 순수 함수만 호출). 문장·조건은 sample_only이며 사용자 비교 결과로 확인된
요건 형태(자녀 수·동일 등본·연령 예외 등)를 본뜬 가상 문장이다. 공식 원문이 아니다.

실행 (프로젝트 루트):
    .venv\\Scripts\\python.exe -m pytest tests/unit/test_neo4j_semantic_s10.py -q
"""

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import data_quality_flags as dqf  # noqa: E402

SCRIPT = PROJECT_ROOT / "docs/development/experiments/neo4j_semantic_20261106/neo4j_semantic_check.py"


@pytest.fixture(scope="module")
def sc():
    spec = importlib.util.spec_from_file_location("neo4j_semantic_check_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cond(cid, kind, operator, value, name="sample_only", unit=None):
    return cid, {"condition_id": cid, "type": kind, "operator": operator, "value": value, "name": name, "unit": unit}


def child(cid, operator, value):
    return cond(cid, "SPECIAL_TARGET", operator, value, name="대상 자녀 연령", unit="개월")


def assess(sc, name, eligibility_text, conditions=(), doc_text=""):
    positive = dict(conditions)
    related = sorted(cid for cid in positive if cid.startswith("COND_CHILD_") or cid in sc.CHILD_RELATED_IDS) \
        if name == "children" else sorted(cid for cid, c in positive.items()
                                          if sc.MARRIAGE_CONDITION.search(f"{c['name']} {c['value']}"))
    sources = [("DOC_SAMPLE_1", doc_text), ("policy.eligibility", eligibility_text)]
    return sc.assess_s10_dimension(name, sources, positive, related)


def test_rule_name_matches_flag_builder(sc):
    assert sc.S10_RULE == dqf.S10_RULE


def test_no_related_condition_with_requirement_text_is_unmodeled(sc):
    result = assess(sc, "children", "만 19세 미만 자녀를 둔 가구")
    assert result["classification"] == "unmodeled"
    assert result["requirement_lines"][0]["source"] == "policy.eligibility"


def test_child_count_and_same_registry_missing_is_incomplete(sc):
    # POL_STG_EX_001 형태: 그래프에는 자녀 연령 조건만 있음
    result = assess(sc, "children", "만 19세 미만 자녀 2명 이상, 부모와 동일 주민등록등본 등재",
                    [child("COND_CHILD_AGE_UNDER_228M", "LT", "228")])
    assert result["classification"] == "incomplete"
    assert {"child_count", "household_registry"} <= set(result["missing_details"])
    assert ("LT", "228") in result["text_child_ages"]                  # 연령 자체는 일치해도 불완전


def test_child_age_exception_is_incomplete(sc):
    # POL_BOK_068/069 형태: 만 12세 이하이나 초등학생은 연령 초과 가능
    result = assess(sc, "children", "만 12세 이하 아동 (단, 초등학생은 13세 이상도 포함 가능)",
                    [child("COND_CHILD_AGE_UNDER_156M", "LT", "156")])
    assert result["classification"] == "incomplete"
    assert "age_exception" in result["missing_details"]


def test_multiple_children_covered_but_age_exception_missing(sc):
    # POL_BOK_001 형태: 다자녀는 다른 그래프 조건이 표현(중복 보호 구분), 서비스별 연령·미숙아 예외는 누락
    result = assess(sc, "children", "만 12세 이하 아동 양육 가구, 다자녀 가구, 미숙아는 연령 예외",
                    [child("COND_CHILD_AGE_UNDER_156M", "LT", "156"),
                     cond("COND_HH_다자녀가구", "HOUSING_HOUSEHOLD", "EQUAL", "다자녀가구", name="가구주거조건: 다자녀가구")])
    assert result["classification"] == "incomplete"
    assert "age_exception" in result["missing_details"]
    assert result["covered_details"]["child_count"]["covered_by"] == ["COND_HH_다자녀가구"]


def test_covered_detail_only_is_review_not_consistent(sc):
    result = assess(sc, "children", "한부모 가구의 자녀",
                    [child("COND_CHILD_AGE_UNDER_108M", "LT", "108"),
                     cond("COND_HH_한부모가정_조손가정", "HOUSING_HOUSEHOLD", "EQUAL", "한부모가정_조손가정",
                          name="가구주거조건: 한부모가정/조손가정")])
    assert result["classification"] == "review"


def test_keyword_only_mention_is_review_not_requirement(sc):
    with_related = assess(sc, "children", "", [child("COND_CHILD_AGE_UNDER_24M", "LT", "24")], doc_text="아동수당 안내")
    assert (with_related["classification"], with_related["keyword_only"]) == ("review", True)
    without_related = assess(sc, "children", "", doc_text="아동수당 안내")
    assert without_related["classification"] == "review"                  # 보호는 유지, 미모델링 확정 아님


def test_matching_child_months_without_details_is_consistent(sc):
    # POL_GOV_050 형태 (PR #96 교정: LTE 24개월): 세부 요건이 없고 연령이 일치하면 불필요하게 보류하지 않음
    result = assess(sc, "children", "24개월 이하 자녀를 양육하는 가구", [child("COND_CHILD_AGE_LE_24M", "LTE", "24")])
    assert result["classification"] == "consistent"


def test_single_exact_child_age_is_consistent(sc):
    # 1. 단일 연령 완전 일치 (sample_only)
    result = assess(sc, "children", "24개월 미만 자녀를 양육하는 가구", [child("COND_CHILD_A", "LT", "24")])
    assert (result["classification"], result["child_age_check"]) == ("consistent", "single_exact")


def test_extra_graph_child_range_is_review(sc):
    # 2. 원문 LT 24 / 그래프 LT 24 + BETWEEN 36~60 (v2는 부분집합이라 consistent였음)
    result = assess(sc, "children", "24개월 미만 자녀를 양육하는 가구",
                    [child("COND_CHILD_A", "LT", "24"), child("COND_CHILD_B", "BETWEEN", "36~60")])
    assert (result["classification"], result["child_age_check"]) == ("review", "mismatch")


def test_missing_graph_child_range_is_review(sc):
    # 3. 원문 LT 24 + BETWEEN 36~60 / 그래프 LT 24
    result = assess(sc, "children", "24개월 미만 자녀 또는 36~60개월 자녀를 양육하는 가구",
                    [child("COND_CHILD_A", "LT", "24")])
    assert set(result["text_child_ages"]) == {("LT", "24"), ("BETWEEN", "36~60")}
    assert (result["classification"], result["child_age_check"]) == ("review", "mismatch")


def test_partial_boundary_mismatch_in_multiple_ages_is_review(sc):
    # 4. 복수 조건 중 LT/LTE 경계 불일치
    result = assess(sc, "children", "24개월 미만 자녀 또는 36~60개월 자녀를 양육하는 가구",
                    [child("COND_CHILD_A", "LTE", "24"), child("COND_CHILD_B", "BETWEEN", "36~60")])
    assert (result["classification"], result["child_age_check"]) == ("review", "mismatch")


def test_multiple_distinct_ages_exact_set_is_review(sc):
    # 5. 서로 다른 복수 연령 조건 집합이 같아도 AND/OR 동등성 미검증 → review
    result = assess(sc, "children", "24개월 미만 자녀 또는 36~60개월 자녀를 양육하는 가구",
                    [child("COND_CHILD_A", "LT", "24"), child("COND_CHILD_B", "BETWEEN", "36~60")])
    assert (result["classification"], result["child_age_check"]) == ("review", "multiple_conditions")


def test_repeated_same_age_is_not_multiple_conditions(sc):
    # 6. 같은 연령 표현 반복(24개월 미만·2세 미만) + 그래프의 같은 조건 중복 → 단일 조건으로 본다
    result = assess(sc, "children", "24개월 미만 자녀를 양육하는 가구\n대상: 2세 미만 자녀, 24개월 미만 자녀",
                    [child("COND_CHILD_A", "LT", "24"), child("COND_CHILD_A_DUP", "LT", "24")])
    assert result["text_child_ages"] == [("LT", "24")]
    assert (result["classification"], result["child_age_check"]) == ("consistent", "single_exact")


def test_detail_rules_still_take_priority_over_age_match(sc):
    # 7. 연령이 단일 완전 일치여도 세부 요건 누락·연령 예외는 기존대로 incomplete
    count = assess(sc, "children", "24개월 미만 자녀 2명 이상 양육 가구", [child("COND_CHILD_A", "LT", "24")])
    exception = assess(sc, "children", "24개월 미만 자녀 (단, 미숙아는 예외)", [child("COND_CHILD_A", "LT", "24")])
    assert (count["classification"], exception["classification"]) == ("incomplete", "incomplete")
    assert "child_count" in count["missing_details"] and "age_exception" in exception["missing_details"]


def test_mismatched_child_months_is_review(sc):
    result = assess(sc, "children", "24개월 미만 자녀를 양육하는 가구", [child("COND_CHILD_AGE_LE_24M", "LTE", "24")])
    assert result["classification"] == "review"


def test_priority_or_procedure_is_not_required_detail(sc):
    result = assess(sc, "children", "만 9세 미만 아동\n다자녀 가구 우선 선정",
                    [child("COND_CHILD_AGE_UNDER_108M", "LT", "108")])
    assert "child_count" not in result["missing_details"]
    assert result["non_requirement_mentions"]
    assert result["classification"] == "consistent"


def test_marriage_period_missing_is_incomplete(sc):
    result = assess(sc, "marriage", "혼인기간 7년 이내 신혼부부",
                    [cond("COND_TYPE_SAMPLE_NEWLYWED", "SPECIAL_TARGET", "EQUAL", "신혼부부", name="신혼부부 가구")])
    assert result["classification"] == "incomplete"
    assert "marriage_period" in result["missing_details"]


def test_no_text_dimension(sc):
    assert assess(sc, "marriage", "만 19세 이상 34세 이하")["classification"] == "no_text"
