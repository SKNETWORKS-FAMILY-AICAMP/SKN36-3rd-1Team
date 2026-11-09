"""프로필 전달·중복 질문 방지·허용되지 않은 ask_info 회귀 테스트.

OpenAI API를 호출하지 않고 generator._call_openai를 대체한다.
"""

import json

import pytest

from backend.llm import generator as g


CONTEXTS = [
    {
        "source_id": 5,
        "policy_id": "POL_TEST",
        "policy_name": "청년 정책",
        "text": "지원 대상 연령은 만 19~34세입니다.",
    }
]
CHECKS = [
    {
        "policy_id": "POL_TEST",
        "conditions": [
            {"field": "region", "status": "needs_check", "reason": "지역 요건 원문 확인 필요"}
        ],
        "reception_status": "unknown",
    }
]


def stub(monkeypatch, response, captured=None):
    def fake(messages):
        if captured is not None:
            captured.append(json.loads(messages[1]["content"].split("\n", 1)[1]))
        return json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(g, "_call_openai", fake)


def response(answer="자격 조건은 추가 확인이 필요합니다.", follows=None, status="info"):
    return {
        "status": status,
        "answer": answer,
        "used_source_ids": [5] if "[5]" in answer else [],
        "follow_up_questions": follows if follows is not None else [],
    }


def test_profile_transmitted_without_mutation(monkeypatch):
    captured = []
    stub(monkeypatch, response(), captured)
    profile = {"age": 25, "region": "서울특별시", "income": 250, "debug": "ignore"}
    result = g.generate_answer("신청 가능한가요?", CONTEXTS, CHECKS, profile=profile)
    assert result["status"] == "info"
    assert captured[0]["user_profile"] == {
        "age": 25, "region": "서울특별시", "income": 250
    }
    assert profile == {"age": 25, "region": "서울특별시", "income": 250, "debug": "ignore"}


def test_old_call_is_compatible(monkeypatch):
    captured = []
    stub(monkeypatch, response(), captured)
    g.generate_answer("질문", CONTEXTS, CHECKS)
    assert "user_profile" not in captured[0]


def test_already_given_region_ask_info_removed(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "text": "거주 지역을 알려주세요.", "field": "region"},
        {"kind": "ask_info", "text": "나이가 어떻게 되나요?", "field": "age"},
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS, profile={"region": "서울특별시"})
    assert result["follow_up_questions"] == [
        {"kind": "ask_info", "text": "나이가 어떻게 되나요?", "field": "age"}
    ]


def test_zero_age_and_income_count_as_given(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "text": "나이를 알려주세요.", "field": "age"},
        {"kind": "ask_info", "text": "소득을 알려주세요.", "field": "income"},
        {"kind": "ask_info", "text": "거주 지역은 어디인가요?", "field": "region"},
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS, profile={"age": 0, "income": 0})
    assert result["follow_up_questions"] == [
        {"kind": "ask_info", "text": "거주 지역은 어디인가요?", "field": "region"}
    ]


def test_unknown_ask_info_converts_to_question(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "text": "가구원 수는 어떻게 확인하나요?", "field": "household_size"}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == [
        {"kind": "question", "text": "가구원 수는 어떻게 확인하나요?"}
    ]


@pytest.mark.parametrize("invalid_field", [None, {}, [], 123])
def test_malformed_ask_info_field_still_converts(monkeypatch, invalid_field):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "text": "추가 확인이 필요합니다.", "field": invalid_field}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS)
    # 허용되지 않은 필드는 question으로 바꾸지만, 안내문은 질문이 아니므로 제외한다.
    assert result["follow_up_questions"] == []


def test_question_type_redundant_region_removed(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "question", "text": "현재 거주 지역은 어디인가요?"},
        {"kind": "question", "text": "지원에 필요한 서류는 무엇인가요?"},
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS, profile={"region": "서울특별시"})
    assert result["follow_up_questions"] == [
        {"kind": "question", "text": "지원에 필요한 서류는 무엇인가요?"}
    ]


def test_body_redundant_region_request_removed_without_removing_citation(monkeypatch):
    stub(monkeypatch, response(
        answer="정책은 19~34세를 대상으로 합니다.[5] 거주 지역을 알려주세요.",
        status="answered",
    ))
    result = g.generate_answer("질문", CONTEXTS, CHECKS, profile={"region": "서울특별시"})
    assert "거주 지역을 알려주세요" not in result["answer"]
    assert result["used_source_ids"] == [5]


def test_district_request_in_body_is_preserved_when_only_sido_given(monkeypatch):
    stub(monkeypatch, response(
        answer="연령 기준은 19~34세입니다.[5] 어느 구에 거주하시나요?",
        status="answered",
        follows=[{"kind": "ask_info", "text": "어느 구에 거주하나요?", "field": "region"}],
    ))
    result = g.generate_answer("질문", CONTEXTS, CHECKS, profile={"region": "서울특별시"})
    assert "어느 구에 거주하시나요?" in result["answer"]
    assert result["follow_up_questions"] == []  # 시·도 입력 칸을 다시 쓰지 않음


def test_question_policy_eligibility_not_suppressed(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "question", "text": "이 정책의 연령 기준은 어떻게 되나요?"}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS, profile={"age": 25})
    assert len(result["follow_up_questions"]) == 1


def test_no_evidence_skips_llm_with_profile(monkeypatch):
    monkeypatch.setattr(g, "_call_openai", lambda _: pytest.fail("LLM 호출 금지"))
    result = g.generate_answer("질문", [], CHECKS, profile={"age": 25})
    assert result["status"] == "no_evidence"


def test_profile_invalid_type_rejected(monkeypatch):
    stub(monkeypatch, response())
    with pytest.raises(ValueError, match="profile"):
        g.generate_answer("질문", CONTEXTS, CHECKS, profile=["region", "서울특별시"])


def test_repeated_followup_is_deduplicated(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "question", "text": "지원 서류는 무엇인가요?"},
        {"kind": "question", "text": "지원 서류는 무엇인가요?"},
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS)
    assert len(result["follow_up_questions"]) == 1


def test_invalid_profile_values_are_not_marked_provided(monkeypatch):
    captured = []
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "age", "text": "나이를 알려주세요."}
    ]), captured)
    result = g.generate_answer("질문", CONTEXTS, CHECKS, profile={"age": True, "region": ""})
    assert captured[0]["user_profile"]["age"] is None
    assert captured[0]["user_profile"]["region"] is None
    assert len(result["follow_up_questions"]) == 1


def test_explanatory_followup_is_removed(monkeypatch):
    """F 실API에서 관측된 조건부 안내는 후속 질문으로 노출하지 않는다."""
    stub(monkeypatch, response(follows=[
        {
            "kind": "question",
            "text": "별도 모집 공고를 확인해 주시면 더 정확히 안내할 수 있습니다.",
        }
    ]))
    result = g.generate_answer("신청 가능한가요?", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == []


def test_valid_question_remains_after_explanatory_text_removed(monkeypatch):
    """유효한 의문문은 유지하고 안내문만 제거한다."""
    stub(monkeypatch, response(follows=[
        {"kind": "question", "text": "별도 공고를 확인해 주세요."},
        {"kind": "question", "text": "신청 절차는 어떻게 되나요?"},
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == [
        {"kind": "question", "text": "신청 절차는 어떻게 되나요?"}
    ]
