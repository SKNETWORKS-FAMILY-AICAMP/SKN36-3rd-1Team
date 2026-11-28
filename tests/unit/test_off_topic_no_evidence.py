"""정책과 관계없는 질문은 no_evidence로 답한다(ask 계약 v1.15).

검색은 항상 가장 가까운 정책을 돌려주므로, "오늘 저녁 메뉴 추천해줘" 같은 질문에도 원문이 붙는다.
모델이 질문과 원문이 관계없다고 판단하면 no_evidence를 고르고, 인용·후속 질문 없이 돌려준다.
Pipeline은 이 상태면 정책 카드와 출처를 비운다(기존 동작, test_official_requirements_api).
값은 모두 sample_only 가상 값이다.
"""
import pytest

from ai.prompts.system_prompt import SYSTEM_PROMPT
from backend.llm import generator as g


def _raw(status: str, answer: str, used=None, follow_ups=None) -> dict:
    return {
        "status": status,
        "answer": answer,
        "used_source_ids": used or [],
        "follow_up_questions": follow_ups or [],
    }


def test_model_may_answer_no_evidence_without_citations():
    result = g._validate_model_response(
        _raw("no_evidence", "정부 지원 정책 질문에 답하는 서비스예요.",
             follow_ups=[{"kind": "question", "text": "청년 정책을 알려 드릴까요?"}]),
        {1, 2},
    )
    assert result == {
        "status": "no_evidence",
        "answer": "정부 지원 정책 질문에 답하는 서비스예요.",
        "used_source_ids": [],
        "follow_up_questions": [],
    }


def test_no_evidence_with_citation_is_rejected():
    with pytest.raises(g.LLMOutputError, match="no_evidence"):
        g._validate_model_response(_raw("no_evidence", "관계없는 질문이에요 [1]", used=[1]), {1})


def test_unknown_status_is_still_rejected():
    with pytest.raises(g.LLMOutputError, match="answered/info/no_evidence"):
        g._validate_model_response(_raw("off_topic", "관계없는 질문이에요"), {1})


def test_answered_still_needs_citation():
    with pytest.raises(g.LLMOutputError, match="인용"):
        g._validate_model_response(_raw("answered", "가상 정책 안내"), {1})


def test_prompt_tells_model_when_to_use_no_evidence():
    assert "'answered', 'info', 'no_evidence' 중 하나" in SYSTEM_PROMPT
    assert "정부 지원 정책과 관계없거나" in SYSTEM_PROMPT
    assert "관계없는 정책을 소개하지 마세요" in SYSTEM_PROMPT
