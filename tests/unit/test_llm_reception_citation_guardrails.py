"""PR #46 리뷰 후 Generator 경계 사례 회귀 테스트. OpenAI 호출 없음."""

import json

import pytest

from ai.prompts.system_prompt import SYSTEM_PROMPT
from backend.llm import generator as g


def _raw(answer, *, status="answered", used_source_ids=None):
    return {
        "status": status,
        "answer": answer,
        "used_source_ids": [1] if used_source_ids is None else used_source_ids,
        "follow_up_questions": [],
    }


@pytest.mark.parametrize("source_text", ["[1]", "[0001]"])
def test_valid_citation_keeps_existing_contract(source_text):
    result = g._validate_model_response(
        _raw(f"공식 원문에 기록되어 있습니다 {source_text}"), {1}
    )
    assert result["status"] == "answered"
    assert result["used_source_ids"] == [1]


def test_multiple_citations_are_deduplicated_and_sorted():
    result = g._validate_model_response(
        _raw("근거 [2]와 [1], 다시 [2]", used_source_ids=[2, 1]), {1, 2}
    )
    assert result["used_source_ids"] == [1, 2]


@pytest.mark.parametrize("number", ["9" * 5000, "0" * 5000, "9" * 5000 + "1"])
def test_very_long_citation_raises_llm_output_error(number):
    with pytest.raises(g.LLMOutputError, match="제공되지 않은 source_id"):
        g._validate_model_response(_raw(f"답변 [{number}]"), {1})


def test_nonexistent_short_citation_rejected():
    with pytest.raises(g.LLMOutputError, match="제공되지 않은 source_id"):
        g._validate_model_response(_raw("답변 [2]"), {1})


def test_no_citation_for_answered_rejected():
    with pytest.raises(g.LLMOutputError, match="원문 인용"):
        g._validate_model_response(_raw("답변만 있음"), {1})


def test_json_very_long_integer_raises_llm_output_error(monkeypatch):
    huge = "9" * 5000
    model_output = (
        '{"status":"answered","answer":"공식 원문 [1]",'
        f'"used_source_ids":[{huge}],"follow_up_questions":[]}}'
    )
    monkeypatch.setattr(g, "_call_openai", lambda _messages: model_output)
    contexts = [{"source_id": 1, "policy_id": "TEST", "policy_name": "테스트", "text": "공식 원문"}]
    with pytest.raises(g.LLMOutputError, match="유효한 JSON"):
        g.generate_answer("지원 가능한가요?", contexts, [])


def test_json_normal_answer_unchanged(monkeypatch):
    model_output = json.dumps(_raw("공식 원문 [1]"), ensure_ascii=False)
    monkeypatch.setattr(g, "_call_openai", lambda _messages: model_output)
    contexts = [{"source_id": 1, "policy_id": "TEST", "policy_name": "테스트", "text": "공식 원문"}]
    result = g.generate_answer("지원 가능한가요?", contexts, [])
    assert result["used_source_ids"] == [1]
    assert result["answer"] == "공식 원문 [1]"


def test_prompts_define_both_reception_and_eligibility():
    for state in ("closed", "before", "open", "unknown", "needs_check", "ineligible"):
        assert state in SYSTEM_PROMPT
    assert "접수가 마감된 상태" in SYSTEM_PROMPT
    assert "접수가 아직 시작되지" in SYSTEM_PROMPT
    assert "접수 상태를 확인할 수 없" in SYSTEM_PROMPT
    assert "서로 다른 정보" in SYSTEM_PROMPT
    assert "모든 정책에 신청 가능" in SYSTEM_PROMPT
