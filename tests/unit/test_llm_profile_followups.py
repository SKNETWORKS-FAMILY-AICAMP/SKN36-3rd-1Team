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
    assert result["follow_up_questions"] == [
        {"kind": "question", "text": "어느 구에 거주하나요?"}
    ]  # 시·도 입력 칸을 다시 쓰지 않고 세부 지역 질문을 보존


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


# ==========================================================
# 회귀: 시·도 기입 후 시·군·구 질문이 사라지지 않도록 보존
# ==========================================================


def test_district_ask_info_is_converted_to_question(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "region", "text": "현재 강남구에 거주하나요?"}
    ]))
    result = g.generate_answer("신청 가능한가요?", CONTEXTS, CHECKS,
                               profile={"region": "서울특별시"})
    assert result["follow_up_questions"] == [
        {"kind": "question", "text": "현재 강남구에 거주하나요?"}
    ]


def test_district_resident_registration_is_converted(monkeypatch):
    question = "신청일 기준 서울특별시 강남구에 주민등록이 되어 있나요?"
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "region", "text": question}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS,
                               profile={"region": "서울특별시"})
    assert result["follow_up_questions"] == [
        {"kind": "question", "text": question}
    ]


def test_municipality_ask_info_is_converted(monkeypatch):
    question = "경기도 수원시에 거주하고 있나요?"
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "region", "text": question}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS,
                               profile={"region": "경기도"})
    assert result["follow_up_questions"] == [
        {"kind": "question", "text": question}
    ]


def test_plain_sido_repeat_is_still_removed(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "region", "text": "서울특별시에 거주하나요?"},
        {"kind": "ask_info", "field": "region", "text": "현재 거주 지역은 어디인가요?"},
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS,
                               profile={"region": "서울특별시"})
    assert result["follow_up_questions"] == []


def test_sido_alias_is_not_mistaken_for_district(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "region", "text": "서울시에 거주하나요?"}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS,
                               profile={"region": "서울특별시"})
    assert result["follow_up_questions"] == []


def test_district_already_given_is_not_asked_again(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "region", "text": "어느 구에 거주하나요?"}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS,
                               profile={"region": "서울특별시 강남구"})
    assert result["follow_up_questions"] == []


def test_region_ask_info_is_retained_if_profile_missing(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "region", "text": "어느 구에 거주하나요?"}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS, profile={"age": 25})
    assert result["follow_up_questions"] == [
        {"kind": "ask_info", "field": "region", "text": "어느 구에 거주하나요?"}
    ]


def test_full_sido_is_not_detail_when_profile_uses_abbreviation(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "region", "text": "서울특별시에 거주하나요?"}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS,
                               profile={"region": "서울시"})
    assert result["follow_up_questions"] == []


# ==========================================================
# 회귀: 2번 - 허용되지 않은 ask_info의 물음표 누락
# ==========================================================


def test_unknown_field_polite_request_without_question_mark(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "household_size", "text": "가구원 수를 알려주세요."}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == [
        {"kind": "question", "text": "가구원 수를 알려주실 수 있나요?"}
    ]


def test_unknown_field_interrogative_without_question_mark(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "child_age", "text": "자녀 나이는 몇 세인가요"}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == [
        {"kind": "question", "text": "자녀 나이는 몇 세인가요?"}
    ]


def test_unknown_field_input_request_without_question_mark(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "household_size", "text": "가구원 수를 입력해 주세요!"}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == [
        {"kind": "question", "text": "가구원 수를 알려주실 수 있나요?"}
    ]


def test_guide_statement_not_converted_into_question(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "household_size", "text": "가구원 수 확인이 필요합니다."},
        {"kind": "question", "text": "별도 공고를 확인해 주세요."},
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == []


def test_existing_profile_duplicate_still_removed_without_question_mark(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "household_size", "text": "거주 지역을 알려주세요"}
    ]))
    result = g.generate_answer(
        "질문", CONTEXTS, CHECKS, profile={"region": "서울특별시"}
    )
    assert result["follow_up_questions"] == []


def test_district_follow_up_without_question_mark_is_preserved(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "region", "text": "강남구에 거주하나요"}
    ]))
    result = g.generate_answer(
        "질문", CONTEXTS, CHECKS, profile={"region": "서울특별시"}
    )
    assert result["follow_up_questions"] == [
        {"kind": "question", "text": "강남구에 거주하나요?"}
    ]


def test_plain_question_without_question_mark_is_preserved(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "question", "text": "이 정책의 신청 절차는 어떻게 되나요"}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == [
        {"kind": "question", "text": "이 정책의 신청 절차는 어떻게 되나요?"}
    ]

# ==========================================================
# P1 regression: 동사 활용형을 시군구 지명으로 오인하지 않기
# ==========================================================


def test_p1_reasking_sido_ask_info_is_removed(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "region", "text": "거주하시는 시도는 어디인가요?"}
    ]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={"region": "서울특별시"})
    assert actual["follow_up_questions"] == []


def test_p1_reasking_region_question_with_polite_verb_is_removed(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "question", "text": "거주하시는 지역을 알려주세요."}
    ]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={"region": "서울특별시"})
    assert actual["follow_up_questions"] == []


def test_p1_reasking_sido_answer_body_is_removed(monkeypatch):
    stub(monkeypatch, response(
        status="answered",
        answer="지원 대상은 만 19~34세입니다.[5] 거주하시는 시도는 어디인가요?",
    ))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={"region": "서울특별시"})
    assert "거주하시는 시도는 어디인가요?" not in actual["answer"]
    assert actual["used_source_ids"] == [5]


def test_p1_district_ask_info_is_preserved_as_question(monkeypatch):
    text = "서울특별시의 어느 구에 거주하시나요?"
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "region", "text": text}
    ]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={"region": "서울특별시"})
    assert actual["follow_up_questions"] == [{"kind": "question", "text": text}]


def test_p1_named_gu_ask_info_is_preserved_as_question(monkeypatch):
    text = "강남구에 주민등록되어 있나요?"
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "region", "text": text}
    ]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={"region": "서울특별시"})
    assert actual["follow_up_questions"] == [{"kind": "question", "text": text}]


@pytest.mark.parametrize("phrase", [
    "이용하시는", "알려주시면", "거주하시는", "확인하시면", "입력하시는",
    "거주하시는 시도는 어디인가요?", "어느 시도에 거주하시나요?",
])
def test_p1_verb_forms_and_sido_are_not_district_requests(phrase):
    assert not g._is_detail_region_request(phrase, "서울특별시")


@pytest.mark.parametrize("phrase", [
    "수원시에 거주하고 있나요?", "영광군 거주자인가요?", "강남구는 거주지인가요?",
    "어느 구에 거주하시나요?", "광주시에 거주하나요?",
])
def test_p1_real_municipalities_are_still_detail_requests(phrase):
    assert g._is_detail_region_request(phrase, "경기도")
