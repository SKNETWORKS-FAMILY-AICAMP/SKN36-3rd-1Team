"""실제 API 호출 없는 LLM 계약 단위 테스트."""

import json
import pytest

from backend.llm import generator as g

CTX = [{"source_id": 1, "policy_id": "POL_TEST", "policy_name": "시험 정책", "text": "만 19세 이상 35세 이하 신청 가능", "url": "https://example.org"}]
CHECKS = [{"policy_id": "POL_TEST", "conditions": [{"field": "age", "status": "needs_check", "label": "나이", "reason": "추가 확인"}], "reception_status": "unknown"}]


def fake_model(monkeypatch, output):
    monkeypatch.setattr(g, "_call_openai", lambda messages: json.dumps(output, ensure_ascii=False))


def test_answer_contract_and_citation(monkeypatch):
    fake_model(monkeypatch, {"status": "answered", "answer": "연령 기준은 19~35세입니다 [1].", "used_source_ids": [1], "follow_up_questions": []})
    result = g.generate_answer("연령 조건은?", CTX, CHECKS)
    assert result["status"] == "answered" and result["used_source_ids"] == [1]


def test_no_evidence_skips_model(monkeypatch):
    monkeypatch.setattr(g, "_call_openai", lambda x: pytest.fail("LLM은 호출하면 안 됨"))
    assert g.generate_answer("질문", [], [])["status"] == "no_evidence"


def test_invalid_citation_rejected(monkeypatch):
    fake_model(monkeypatch, {"status": "answered", "answer": "출처 [9]", "used_source_ids": [9], "follow_up_questions": []})
    with pytest.raises(g.LLMOutputError):
        g.generate_answer("질문", CTX, CHECKS)


def test_missing_citation_rejected(monkeypatch):
    fake_model(monkeypatch, {"status": "answered", "answer": "연령 기준입니다.", "used_source_ids": [], "follow_up_questions": []})
    with pytest.raises(g.LLMOutputError):
        g.generate_answer("질문", CTX, CHECKS)


def test_follow_up_field(monkeypatch):
    fake_model(monkeypatch, {"status": "info", "answer": "거주 지역을 알려주세요.", "used_source_ids": [], "follow_up_questions": [{"kind": "ask_info", "text": "거주 지역은?", "field": "region"}]})
    assert g.generate_answer("질문", CTX, CHECKS)["follow_up_questions"][0]["field"] == "region"


def test_invalid_follow_up_field(monkeypatch):
    fake_model(monkeypatch, {"status": "info", "answer": "정보 필요", "used_source_ids": [], "follow_up_questions": [{"kind": "ask_info", "text": "자녀 나이?", "field": "child_age"}]})
    with pytest.raises(g.LLMOutputError):
        g.generate_answer("질문", CTX, CHECKS)


def test_timeout_propagates(monkeypatch):
    def timeout(messages):
        raise g.LLMTimeoutError("시간 초과")
    monkeypatch.setattr(g, "_call_openai", timeout)
    with pytest.raises(g.LLMTimeoutError):
        g.generate_answer("질문", CTX, CHECKS)


def test_invalid_json(monkeypatch):
    monkeypatch.setattr(g, "_call_openai", lambda messages: "not a json")
    with pytest.raises(g.LLMOutputError):
        g.generate_answer("질문", CTX, CHECKS)


def test_history_trim_and_no_mutation(monkeypatch):
    source = []
    def capture(messages):
        source.append(json.loads(messages[1]["content"].split("\n", 1)[1]))
        return json.dumps({"status": "info", "answer": "확인 필요", "used_source_ids": [], "follow_up_questions": []})
    monkeypatch.setattr(g, "_call_openai", capture)
    histories = [{"role": "user", "text": str(i)} for i in range(15)]
    original = json.dumps(CTX, ensure_ascii=False)
    g.generate_answer("질문", CTX, CHECKS, histories)
    assert len(source[0]["history_for_conversation_only"]) == 10
    assert json.dumps(CTX, ensure_ascii=False) == original


def test_duplicate_source_ids_rejected(monkeypatch):
    with pytest.raises(ValueError):
        g.generate_answer("질문", CTX + CTX, CHECKS)


def test_used_ids_mismatch_body_is_authoritative(monkeypatch):
    fake_model(monkeypatch, {"status": "answered", "answer": "본문 근거 [1]", "used_source_ids": [9], "follow_up_questions": []})
    assert g.generate_answer("질문", CTX, CHECKS)["used_source_ids"] == [1]

import pytest
from backend.llm.generator import generate_answer


@pytest.mark.parametrize(
    "invalid_contexts",
    [None, {}, "", 123],
)
def test_invalid_contexts_rejected(invalid_contexts):
    with pytest.raises(ValueError, match="contexts는 리스트"):
        generate_answer(
            question="청년 지원 정책 알려줘",
            contexts=invalid_contexts,
            policy_checks=[],
        )


def test_empty_contexts_returns_no_evidence():
    result = generate_answer(
        question="청년 지원 정책 알려줘",
        contexts=[],
        policy_checks=[],
    )

    assert result["status"] == "no_evidence"
    assert result["used_source_ids"] == []
    assert result["follow_up_questions"] == []