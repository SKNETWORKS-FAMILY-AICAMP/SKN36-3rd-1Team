"""후속 질문 버튼의 요청문에 붙은 물음표를 뗀다('…정리해 주세요?' → '…정리해 주세요'). LLM 호출 없음.

실제 응답(2026-11-29, #165 실DB·실LLM 확인 중)에서 '서울특별시 청년수당의 신청제외대상을 정리해 주세요?'가 나왔다.
버튼으로 남길지(question 판정)는 그대로이고 문구만 고친다.
"""

import pytest

from backend.llm import generator as g


@pytest.mark.parametrize(("text", "expected"), [
    ("서울특별시 청년수당의 신청제외대상을 정리해 주세요?", "서울특별시 청년수당의 신청제외대상을 정리해 주세요"),
    ("신청 서류를 알려 주십시오？", "신청 서류를 알려 주십시오"),
    ("신청 방법 알려줘요??", "신청 방법 알려줘요"),
    ("신청 방법 알려줘?", "신청 방법 알려줘"),
])
def test_request_endings_lose_question_mark(text, expected):
    assert g._normalize_followup_question(text) == expected


@pytest.mark.parametrize("text", [
    "신청 서류는 무엇인가요?",
    "신청 방법을 알려 주실래요?",          # 의문문
    "온라인으로 신청할 수 있나요?",
    "주세요라는 표현이 맞나요?",            # 끝이 요청 어미가 아님
])
def test_real_questions_keep_question_mark(text):
    assert g._normalize_followup_question(text) == text


def test_model_response_follow_up_text_is_fixed():
    raw = {"status": "answered", "answer": "근거 [1].", "used_source_ids": [1],
           "follow_up_questions": [{"kind": "question", "text": "서울특별시 청년수당의 신청제외대상을 정리해 주세요?"}]}
    result = g._validate_model_response(raw, {1})
    assert [item["text"] for item in result["follow_up_questions"]] == ["서울특별시 청년수당의 신청제외대상을 정리해 주세요"]
