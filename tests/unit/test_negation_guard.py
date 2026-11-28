"""답변이 제외 대상의 부정 접두어(미·비·무·불)를 빠뜨린 경우만 원문대로 되돌리는지 확인한다.

SEOUL_SOURCE는 v1.4.0-candidate 릴리스 청크 DOC_STG_SEOUL_001_INTRO_2026_C01(청년몽땅정보통 서울시 청년수당 소개)의
원문 일부를 그대로 옮긴 것이다. 나머지 원문은 형식 확인용 가상 문장(sample_only)이다. LLM·DB 호출 없음.
"""

import json

import pytest

from backend.llm import generator as g
from backend.llm.negation_guard import restore_negated_exclusions

SEOUL_SOURCE = (
    "지원대상자\n신청대상\n주민등록상 서울시에 거주하는 최종학력 졸업 후 미취업 상태인 청년\n"
    "연령요건\n만19세~34세\n\n신청제외대상\n\n주민등록상 서울시 미거주자(신청 시점 기준)\n재학생 및 휴학생\n"
)
SEOUL = [{"source_id": 4, "policy_id": "POL_STG_SEOUL_001", "text": SEOUL_SOURCE}]


def answer(text: str) -> dict:
    return {"status": "answered", "answer": text, "used_source_ids": [4], "follow_up_questions": []}


def fixed(text: str, contexts=SEOUL) -> str:
    return restore_negated_exclusions(answer(text), contexts)["answer"]


# --- 고쳐야 하는 경우(누락 방지) ---------------------------------------------


def test_real_case_dropped_prefix_is_restored():
    # 실제 답변(2026-11-29, #165 실DB·실LLM 확인 중)에서 나온 문장
    text = "원문상 서울시 거주자 제외, 재학생·휴학생 제외 등의 신청제외대상이 적혀 있습니다. [4]"
    assert fixed(text) == "원문상 서울시 미거주자 제외, 재학생·휴학생 제외 등의 신청제외대상이 적혀 있습니다. [4]"


@pytest.mark.parametrize(("wrong", "right"), [
    ("서울시 거주자는 제외돼요 [4].", "서울시 미거주자는 제외돼요 [4]."),
    ("서울시 거주자도 신청 제외 대상이에요 [4].", "서울시 미거주자도 신청 제외 대상이에요 [4]."),
    ("서울시 거주자(신청 시점 기준) 제외 [4]", "서울시 미거주자(신청 시점 기준) 제외 [4]"),
])
def test_particles_and_notes_between_term_and_exclusion(wrong, right):
    assert fixed(wrong) == right


@pytest.mark.parametrize(("wrong", "right"), [
    # 실제 답변(2026-11-29) 형식에서 접두어만 뺀 문장
    ("신청 제외 대상은 주민등록상 서울시 거주자, 재학생 및 휴학생, 군복무자입니다[4].",
     "신청 제외 대상은 주민등록상 서울시 미거주자, 재학생 및 휴학생, 군복무자입니다[4]."),
    ("원문상 주민등록상 서울시 거주자, 재학생 및 휴학생, 주 30시간 초과 근로하는 취업자 등은 신청 제외 대상이에요 [4].",
     "원문상 주민등록상 서울시 미거주자, 재학생 및 휴학생, 주 30시간 초과 근로하는 취업자 등은 신청 제외 대상이에요 [4]."),
    ("신청 제외대상으로는 재학생 및 휴학생, 서울시 거주자가 적혀 있습니다. [4]",
     "신청 제외대상으로는 재학생 및 휴학생, 서울시 미거주자가 적혀 있습니다. [4]"),
])
def test_exclusion_lists_are_restored(wrong, right):
    assert fixed(wrong) == right


def test_other_prefix_is_restored():
    source = "지원 제외 대상\n- 무주택자 (sample_only)\n"
    contexts = [{"source_id": 1, "policy_id": "POL_SAMPLE", "text": source}]
    assert fixed("주택자는 제외됩니다 [1].", contexts) == "무주택자는 제외됩니다 [1]."


def test_only_the_wrong_sentence_changes():
    text = "만 19~34세가 대상이에요 [4]. 서울시 거주자 제외 [4]."
    assert fixed(text) == "만 19~34세가 대상이에요 [4]. 서울시 미거주자 제외 [4]."


# --- 고치면 안 되는 경우(오탐 방지) ------------------------------------------


@pytest.mark.parametrize("text", [
    "서울시 거주자만 신청할 수 있어요 [4].",                       # 신청 대상 설명(제외 아님)
    "서울시 거주자만 신청할 수 있고 미거주자는 제외돼요 [4].",      # 제외 대상은 이미 원문대로
    "서울시 미거주자 제외 [4].",                                   # 이미 원문대로
    "서울시 거주자 제외.",                                         # 출처 번호 없음
    "서울시 실거주자 제외 [4].",                                   # 다른 합성어
    "제외 대상에 해당하지 않는 서울시 거주자는 신청할 수 있어요 [4].",  # 목록이 아닌 조건 설명
    "신청 제외 대상은 재학생이고, 서울시 거주자는 신청할 수 있어요 [4].",  # 목록 뒤 다른 서술
    "서울시 거주자만 신청할 수 있고, 미거주자 등은 신청 제외 대상이에요 [4].",  # 목록 앞 다른 서술
])
def test_unchanged(text):
    assert fixed(text) == text


def test_source_with_both_forms_is_ambiguous():
    source = "신청 대상: 서울시 거주자 (sample_only)\n신청 제외: 서울시 미거주자\n"
    contexts = [{"source_id": 1, "policy_id": "POL_SAMPLE", "text": source}]
    assert fixed("서울시 거주자 제외 [1].", contexts) == "서울시 거주자 제외 [1]."


def test_prefixed_term_outside_exclusion_context_is_ignored():
    # '미취업자'가 신청 대상으로만 나오면 '취업자 제외'는 원문과 같은 뜻일 수 있으므로 고치지 않는다.
    source = "신청 대상\n미취업자 (sample_only)\n"
    contexts = [{"source_id": 1, "policy_id": "POL_SAMPLE", "text": source}]
    assert fixed("취업자는 제외돼요 [1].", contexts) == "취업자는 제외돼요 [1]."


def test_other_policy_source_is_not_borrowed():
    other = [*SEOUL, {"source_id": 1, "policy_id": "POL_SAMPLE", "text": "가상 정책 원문 (sample_only)"}]
    assert fixed("서울시 거주자 제외 [1].", other) == "서울시 거주자 제외 [1]."


def test_input_is_not_mutated():
    original = answer("서울시 거주자 제외 [4].")
    snapshot = json.dumps(original, ensure_ascii=False)
    restore_negated_exclusions(original, SEOUL)
    assert json.dumps(original, ensure_ascii=False) == snapshot


# --- 생성기에 연결됐는지 ---------------------------------------------------------


def test_generate_answer_applies_guard(monkeypatch):
    contexts = [{**SEOUL[0], "source_id": 1, "policy_name": "서울특별시 청년수당", "url": "https://example.org"}]
    checks = [{"policy_id": "POL_STG_SEOUL_001", "reception_status": "closed",
               "conditions": [{"field": "eligibility", "status": "needs_check", "label": "자격 요건", "reason": "추가 확인"}]}]
    monkeypatch.setattr(g, "_call_openai", lambda messages: json.dumps({
        "status": "answered", "answer": "신청제외대상은 서울시 거주자 제외, 재학생 및 휴학생 제외예요 [1].",
        "used_source_ids": [1], "follow_up_questions": []}, ensure_ascii=False))
    result = g.generate_answer("서울 청년수당 제외 대상은?", contexts, checks, [])
    assert "서울시 미거주자 제외" in result["answer"]
    assert "서울시 거주자 제외" not in result["answer"]
