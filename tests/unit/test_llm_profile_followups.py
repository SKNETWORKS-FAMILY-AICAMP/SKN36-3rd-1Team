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


@pytest.mark.parametrize("text", [
    "임대차계약서에 따라 월세를 내고 계신가요?",
    "거주 형태가 독립된 공간인가요, 아니면 같은 집 안인가요?",
    "거주 형태가 ‘부모님과 별도 거주’(독립된 주거공간)인가요, 아니면 같은 집 안에서 방을 임차하는 형태인가요?",
    "현재 거주 지역이 경기도인가요?",
])
def test_direct_user_fact_questions_are_not_question_buttons(monkeypatch, text):
    stub(monkeypatch, response(follows=[{"kind": "question", "text": text}]))
    result = g.generate_answer("신청 가능한가요?", CONTEXTS, CHECKS)
    assert not any(item["kind"] == "question" for item in result["follow_up_questions"])
    assert "시·군·구를 알려" not in result["answer"]


@pytest.mark.parametrize("text", [
    "이 정책의 거주 지역 기준은 무엇인가요?",
    "임대차계약서는 언제 제출하나요?",
    "지원 대상의 거주 형태는 어떤가요?",
    "상담 담당자가 계신가요?",
    "이 정책의 지역 기준은 경기도가 아닌가요?",
    "임대차계약서가 없어도 신청할 수 있나요?",
])
def test_policy_fact_questions_remain_question_buttons(monkeypatch, text):
    stub(monkeypatch, response(follows=[{"kind": "question", "text": text}]))
    result = g.generate_answer("신청 가능한가요?", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == [{"kind": "question", "text": text}]


@pytest.mark.parametrize("text", [
    "거주 형태는 월세만 대상인가요?",
    "거주 형태는 전세도 대상인가요?",
    "거주 형태가 기숙사여도 대상인가요?",
    "거주 형태는 전세도 포함인가요?",
    "거주 형태는 공동주택도 허용인가요?",
    "거주 형태는 독립된 주거공간만 인정인가요?",
    "거주  형태는\n전세도 대상인가요？",
])
def test_housing_scope_questions_are_preserved(monkeypatch, text):
    stub(monkeypatch, response(follows=[{"kind": "question", "text": text}]))
    result = g.generate_answer("가상 주거 정책의 범위를 알려주세요", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == [{"kind": "question", "text": text}]


@pytest.mark.parametrize("text", [
    "거주 형태는 월세인가요?",
    "거주 형태가 전세인가요?",
    "거주 형태는 독립된 주거공간인가요?",
])
def test_current_housing_fact_requests_still_are_not_questions(monkeypatch, text):
    stub(monkeypatch, response(follows=[{"kind": "question", "text": text}]))
    result = g.generate_answer("가상 주거 정책 안내", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == []


@pytest.mark.parametrize("text", [
    "주거급여의 2026년 1인 가구 기준 금액도 알려주세요?",
    "주거급여 기준금액은 얼마인가요?",
    "소득 기준이 얼마인가요?",
    "2026년 기준 중위소득은 얼마인가요?",
    "지원 금액은 얼마인가요?",
])
@pytest.mark.parametrize("model_kind", ["question", "ask_info"])
def test_policy_criteria_questions_do_not_request_personal_income(monkeypatch, text, model_kind):
    follow = {"kind": model_kind, "text": text}
    if model_kind == "ask_info":
        follow["field"] = "income"
    stub(monkeypatch, response(follows=[follow]))
    result = g.generate_answer("지원 조건을 알려주세요", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == [{"kind": "question", "text": text}]


@pytest.mark.parametrize("text", [
    "본인의 월 소득을 알려주세요?",
    "현재 월 소득이 얼마인가요?",
    "귀하의 급여를 알려주세요?",
])
def test_personal_income_question_remains_ask_info(monkeypatch, text):
    stub(monkeypatch, response(follows=[{"kind": "question", "text": text}]))
    result = g.generate_answer("신청 가능한가요?", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == [
        {"kind": "ask_info", "text": text, "field": "income"}
    ]


@pytest.mark.parametrize("text", [
    "지원 금액을 계산하려면 현재 월 소득을 알려주세요?",
    "소득 기준을 확인하려면 본인의 월 소득을 알려주세요.",
])
@pytest.mark.parametrize("kind,field", [
    ("question", None), ("ask_info", "income"), ("ask_info", "region"),
])
def test_policy_keyword_does_not_hide_personal_income(monkeypatch, text, kind, field):
    follow = {"kind": kind, "text": text}
    if field is not None:
        follow["field"] = field
    stub(monkeypatch, response(follows=[follow]))
    actual = g.generate_answer("조건 확인", CONTEXTS, CHECKS)
    assert actual["follow_up_questions"] == [
        {"kind": "ask_info", "text": text, "field": "income"}
    ]


@pytest.mark.parametrize("text", [
    "주거급여 기준금액을 알려주세요.",
    "주거급여 기준금액을 알려주세요",
    "주거급여 기준금액을 알려주세요。",
])
@pytest.mark.parametrize("kind", ["question", "ask_info"])
def test_policy_criteria_request_without_question_mark(monkeypatch, text, kind):
    follow = {"kind": kind, "text": text}
    if kind == "ask_info":
        follow["field"] = "income"
    stub(monkeypatch, response(follows=[follow]))
    actual = g.generate_answer("지원 조건", CONTEXTS, CHECKS)
    assert actual["follow_up_questions"] == [{"kind": "question", "text": text}]


@pytest.mark.parametrize("kind,field", [
    ("question", None), ("ask_info", "region"), ("ask_info", "income"),
])
def test_mislabelled_mixed_question_is_split(monkeypatch, kind, field):
    follow = {"kind": kind, "text": "현재 거주 지역이 경기도인가요? 소득 기준은 얼마인가요?"}
    if field is not None:
        follow["field"] = field
    stub(monkeypatch, response(follows=[follow]))
    actual = g.generate_answer("신청 가능한가요?", CONTEXTS, CHECKS)
    assert actual["follow_up_questions"] == [
        {"kind": "ask_info", "text": "현재 거주 지역이 경기도인가요?", "field": "region"},
        {"kind": "question", "text": "소득 기준은 얼마인가요?"},
    ]


@pytest.mark.parametrize("kind", ["question", "ask_info"])
def test_mixed_income_and_policy_question(monkeypatch, kind):
    follow = {"kind": kind, "text": "본인의 월 소득을 알려주세요? 주거급여 기준금액은 얼마인가요?"}
    if kind == "ask_info":
        follow["field"] = "region"
    stub(monkeypatch, response(follows=[follow]))
    actual = g.generate_answer("지원 조건", CONTEXTS, CHECKS)
    assert actual["follow_up_questions"] == [
        {"kind": "ask_info", "text": "본인의 월 소득을 알려주세요?", "field": "income"},
        {"kind": "question", "text": "주거급여 기준금액은 얼마인가요?"},
    ]


@pytest.mark.parametrize("kind", ["question", "ask_info"])
def test_mixed_housing_scope_and_user_fact(monkeypatch, kind):
    follow = {"kind": kind, "text": "거주 형태는 전세도 대상인가요? 현재 거주 지역이 경기도인가요?"}
    if kind == "ask_info":
        follow["field"] = "income"
    stub(monkeypatch, response(follows=[follow]))
    actual = g.generate_answer("가상 정책", CONTEXTS, CHECKS)
    assert actual["follow_up_questions"] == [
        {"kind": "question", "text": "거주 형태는 전세도 대상인가요?"},
        {"kind": "ask_info", "text": "현재 거주 지역이 경기도인가요?", "field": "region"},
    ]


@pytest.mark.parametrize("text", [
    "청년월세 지원의 현재 월 소득 기준은 얼마인가요?",
    "청년월세 지원의 현재 월 소득 기준 금액은 얼마인가요?",
    "현재 월 소득 기준은 얼마인가요?",
    "청년월세 지원의 본인 소득 기준은 얼마인가요?",
    "2026년 기준 중위소득을 알려주세요.",
    "2026년 기준 중위 소득을 알려주세요.",
])
@pytest.mark.parametrize("kind,field", [
    ("question", None), ("ask_info", "income"), ("ask_info", "region"),
])
def test_policy_threshold_not_misread_as_personal_income(monkeypatch, text, kind, field):
    follow = {"kind": kind, "text": text}
    if field is not None:
        follow["field"] = field
    stub(monkeypatch, response(follows=[follow]))
    actual = g.generate_answer("정책 기준을 알고 싶어요", CONTEXTS, CHECKS)
    assert actual["follow_up_questions"] == [{"kind": "question", "text": text}]


@pytest.mark.parametrize("text", [
    "청년월세 지원 자격을 확인하려면 현재 월 소득을 알려주세요.",
    "지원 금액을 계산하려면 현재 월 소득을 알려주세요?",
    "소득 기준을 확인하려면 본인의 월 소득을 알려주세요.",
    "지원 금액을 계산하려면 소득을 알려주세요?",
])
@pytest.mark.parametrize("kind,field", [
    ("question", None), ("ask_info", "income"), ("ask_info", "region"),
])
def test_real_income_request_even_with_policy_words(monkeypatch, text, kind, field):
    follow = {"kind": kind, "text": text}
    if field is not None:
        follow["field"] = field
    stub(monkeypatch, response(follows=[follow]))
    actual = g.generate_answer("지원 자격을 확인해요", CONTEXTS, CHECKS)
    assert actual["follow_up_questions"] == [
        {"kind": "ask_info", "field": "income", "text": text}
    ]


@pytest.mark.parametrize("text", [
    "신청 서류는 무엇인가요?",
    "신청 절차는 어떻게 되나요?",
    "지원 대상은 누구인가요?",
    "지원금 신청 방법은 무엇인가요?",
])
@pytest.mark.parametrize("field", ["income", "region", "age"])
def test_mislabelled_policy_question_does_not_keep_wrong_field(monkeypatch, text, field):
    stub(monkeypatch, response(follows=[{"kind": "ask_info", "field": field, "text": text}]))
    actual = g.generate_answer("신청 조건", CONTEXTS, CHECKS)
    assert actual["follow_up_questions"] == [{"kind": "question", "text": text}]


@pytest.mark.parametrize("text", [
    "가구원 수는 몇 명인가요?",
    "학력은 어떻게 되시나요?",
    "서류도 알려드릴까요?",
])
def test_recovered_policy_question_never_exposes_user_or_offer(monkeypatch, text):
    stub(monkeypatch, response(follows=[{"kind": "ask_info", "field": "income", "text": text}]))
    actual = g.generate_answer("신청 조건", CONTEXTS, CHECKS)
    assert not any(item["kind"] == "question" for item in actual["follow_up_questions"])


def test_unknown_ask_info_not_reused_as_income(monkeypatch):
    stub(monkeypatch, response(follows=[{
        "kind": "ask_info", "field": "income", "text": "오늘 기분은 어떠세요?"
    }]))
    actual = g.generate_answer("신청 조건", CONTEXTS, CHECKS)
    assert actual["follow_up_questions"] == []


def test_policy_and_profile_split_do_not_leak_wrong_field(monkeypatch):
    stub(monkeypatch, response(follows=[{
        "kind": "ask_info", "field": "income",
        "text": "신청 서류는 무엇인가요? 현재 거주 지역이 경기도인가요?"
    }]))
    actual = g.generate_answer("신청 조건", CONTEXTS, CHECKS)
    assert actual["follow_up_questions"] == [
        {"kind": "question", "text": "신청 서류는 무엇인가요?"},
        {"kind": "ask_info", "field": "region", "text": "현재 거주 지역이 경기도인가요?"},
    ]




@pytest.mark.parametrize("text,wrong_field,expected_body", [
    ("자녀 나이는 몇 세인가요?", "child_age", "자녀 나이를 알려 주세요."),
    ("배우자의 소득을 알려주세요?", "income", None),
    ("부모님 나이는 몇 세인가요?", "age", None),
    ("가구원 수는 어떻게 확인하나요?", "household_size", "가구원 수를 알려 주세요."),
])
def test_nonself_or_unsupported_request_not_exposed_as_profile_button(
    monkeypatch, text, wrong_field, expected_body,
):
    stub(monkeypatch, response(follows=[{
        "kind": "ask_info", "field": wrong_field, "text": text,
    }]))
    result = g.generate_answer("조건 확인", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == []
    if expected_body:
        assert expected_body in result["answer"]



@pytest.mark.parametrize("text", [
    "몇 살이세요?",
    "몇 세이세요?",
    "나이가 어떻게 되나요?",
    "현재 나이가 몇 세인가요?",
    "만 나이를 알려주세요.",
    "연령을 입력해 주세요.",
    "생년월일을 알려주세요.",
])
@pytest.mark.parametrize("kind,field", [
    ("question", None), ("ask_info", "age"), ("ask_info", "region"),
])
@pytest.mark.parametrize("age", [None, 25])
def test_natural_personal_age_requests_and_already_provided_age(
    monkeypatch, text, kind, field, age,
):
    follow = {"kind": kind, "text": text}
    if field is not None:
        follow["field"] = field
    stub(monkeypatch, response(follows=[follow]))
    result = g.generate_answer("지원 조건을 확인하고 싶어요", CONTEXTS, CHECKS,
                               profile={"age": age})
    expected = [] if age is not None else [
        {"kind": "ask_info", "field": "age", "text": text}
    ]
    assert result["follow_up_questions"] == expected


@pytest.mark.parametrize("text", [
    "자녀 나이는 몇 세인가요?",
    "배우자의 나이가 어떻게 되나요?",
    "부모님은 몇 살이세요?",
    "가구원 중 미성년자는 몇 명인가요?",
])
@pytest.mark.parametrize("kind,field", [
    ("question", None), ("ask_info", "age"), ("ask_info", "income"),
])
def test_family_age_never_becomes_user_age_input(monkeypatch, text, kind, field):
    follow = {"kind": kind, "text": text}
    if field is not None:
        follow["field"] = field
    stub(monkeypatch, response(follows=[follow]))
    result = g.generate_answer("정책 확인", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == []


@pytest.mark.parametrize("text", [
    "청년월세 지원의 연령 기준은 몇 세인가요?",
    "이 정책은 만 몇 세까지 신청할 수 있나요?",
    "지원 대상 나이는 몇 세부터인가요?",
    "만 19세 이상만 신청할 수 있나요?",
])
@pytest.mark.parametrize("kind,field", [
    ("question", None), ("ask_info", "age"), ("ask_info", "income"),
])
def test_policy_age_thresholds_not_personal_age_requests(monkeypatch, text, kind, field):
    follow = {"kind": kind, "text": text}
    if field is not None:
        follow["field"] = field
    stub(monkeypatch, response(follows=[follow]))
    result = g.generate_answer("지원 조건", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == [{"kind": "question", "text": text}]



@pytest.mark.parametrize("text", [
    "지원 금액을 계산하려면 나이는 몇 세인가요?",
    "지원 금액을 계산하려면 몇 살이세요?",
    "신청 자격을 확인하려면 나이가 몇 세인가요?",
    "지원 대상 여부를 확인하려면 현재 나이가 어떻게 되나요?",
    "몇 살이세요?",
    "몇 세이세요?",
])
@pytest.mark.parametrize("kind,field", [
    ("question", None), ("ask_info", "age"),
    ("ask_info", "income"), ("ask_info", "region"),
])
@pytest.mark.parametrize("age", [None, 25])
def test_policy_prefixed_personal_age_request_still_requires_age(
    monkeypatch, text, kind, field, age,
):
    follow = {"kind": kind, "text": text}
    if field is not None:
        follow["field"] = field
    stub(monkeypatch, response(follows=[follow]))
    result = g.generate_answer(
        "가상 정책 자격 확인", CONTEXTS, CHECKS,
        profile={"age": age, "region": "서울특별시", "income": None},
    )
    expected = [] if age is not None else [
        {"kind": "ask_info", "text": text, "field": "age"}
    ]
    assert result["follow_up_questions"] == expected


@pytest.mark.parametrize("text", [
    "이 정책의 지원 금액은 얼마인가요?",
    "청년월세 지원의 연령 기준은 몇 세인가요?",
    "이 정책은 만 몇 세까지 신청할 수 있나요?",
    "현재 월 소득 기준은 얼마인가요?",
    "지원 대상 나이는 몇 세부터인가요?",
    "지원 대상 나이는 몇 세인가요?",
    "신청 서류는 무엇인가요?",
])
@pytest.mark.parametrize("kind,field", [
    ("question", None), ("ask_info", "age"),
    ("ask_info", "income"), ("ask_info", "region"),
])
def test_policy_age_fix_keeps_actual_policy_questions(monkeypatch, text, kind, field):
    follow = {"kind": kind, "text": text}
    if field is not None:
        follow["field"] = field
    stub(monkeypatch, response(follows=[follow]))
    result = g.generate_answer(
        "정책의 공식 기준", CONTEXTS, CHECKS,
        profile={"age": None, "region": "서울특별시", "income": None},
    )
    assert result["follow_up_questions"] == [{"kind": "question", "text": text}]


def test_existing_region_does_not_remove_missing_age(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "region", "text": "거주 지역을 알려주세요."},
        {"kind": "ask_info", "field": "age", "text": "나이가 어떻게 되나요?"},
    ]))
    result = g.generate_answer("조건 확인", CONTEXTS, CHECKS,
                               profile={"region": "서울특별시"})
    assert result["follow_up_questions"] == [
        {"kind": "ask_info", "field": "age", "text": "나이가 어떻게 되나요?"}
    ]


def test_confirmed_region_question_is_not_asked_again(monkeypatch):
    stub(monkeypatch, response(follows=[{"kind": "question", "text": "현재 거주 지역이 경기도인가요?"}]))
    result = g.generate_answer("신청 가능한가요?", CONTEXTS, CHECKS,
                              profile={"age": None, "region": "경기도", "income": None})
    assert result["follow_up_questions"] == []


@pytest.mark.parametrize("text", [
    "현재 거주 지역이 경기도인가요?!",
    "현재  거주지역은 경기도가 아닌가요?",
    "거주 형태가 독립된 공간인가요.",
    "월세를 내고 계시지 않나요?",
    "월세를 내고\n계신가요？",
    "현재 거주 지역이\n경기도인가요?",
])
def test_personal_request_variants_are_not_question_buttons(monkeypatch, text):
    stub(monkeypatch, response(follows=[{"kind": "question", "text": text}]))
    result = g.generate_answer("신청 가능한가요?", CONTEXTS, CHECKS)
    assert not any(q["kind"] == "question" for q in result["follow_up_questions"])


def test_mixed_user_request_and_policy_question_are_separated(monkeypatch):
    stub(monkeypatch, response(follows=[{
        "kind": "question", "text": "현재 거주 지역이 경기도인가요? 신청 서류는 무엇인가요?"
    }]))
    result = g.generate_answer("신청 가능한가요?", CONTEXTS, CHECKS, profile={"region": "경기도"})
    assert result["follow_up_questions"] == [{"kind": "question", "text": "신청 서류는 무엇인가요?"}]


def test_multiple_followup_sentences_keep_output_limit(monkeypatch):
    stub(monkeypatch, response(follows=[{
        "kind": "question", "text": "신청 서류는 무엇인가요? 신청 절차는 무엇인가요? 지원액은 얼마인가요? 담당 기관은 어디인가요?"
    }]))
    result = g.generate_answer("정책 안내", CONTEXTS, CHECKS)
    assert len(result["follow_up_questions"]) == 3


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
    assert result["follow_up_questions"] == []
    assert "가구원 수를 알려 주세요." in result["answer"]


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
    assert result["follow_up_questions"] == []
    assert "어느 구에 거주하시나요?" in result["answer"]  # 시·도 입력 칸을 다시 쓰지 않고 세부 지역 질문을 보존


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
    assert result["follow_up_questions"] == []
    assert "거주하시는 시·군·구를 알려 주세요." in result["answer"]


def test_district_resident_registration_is_converted(monkeypatch):
    question = "신청일 기준 서울특별시 강남구에 주민등록이 되어 있나요?"
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "region", "text": question}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS,
                               profile={"region": "서울특별시"})
    assert result["follow_up_questions"] == []
    assert "강남구 주민등록 여부를 알려 주세요." in result["answer"]


def test_municipality_ask_info_is_converted(monkeypatch):
    question = "경기도 수원시에 거주하고 있나요?"
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "region", "text": question}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS,
                               profile={"region": "경기도"})
    assert result["follow_up_questions"] == []
    assert "거주하시는 시·군·구를 알려 주세요." in result["answer"]


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
    assert result["follow_up_questions"] == []
    assert "거주하시는 시·군·구를 알려 주세요." in result["answer"]


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
    assert result["follow_up_questions"] == []
    assert "가구원 수를 알려 주세요." in result["answer"]


def test_unknown_field_interrogative_without_question_mark(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "child_age", "text": "자녀 나이는 몇 세인가요"}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == []
    assert "자녀 나이를 알려 주세요." in result["answer"]


def test_unknown_field_input_request_without_question_mark(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "household_size", "text": "가구원 수를 입력해 주세요!"}
    ]))
    result = g.generate_answer("질문", CONTEXTS, CHECKS)
    assert result["follow_up_questions"] == []
    assert "가구원 수를 알려 주세요." in result["answer"]


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
    assert result["follow_up_questions"] == []
    assert "거주하시는 시·군·구를 알려 주세요." in result["answer"]


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
    assert actual["follow_up_questions"] == []
    assert "거주하시는 시·군·구를 알려 주세요." in actual["answer"]


def test_p1_named_gu_ask_info_is_preserved_as_question(monkeypatch):
    text = "강남구에 주민등록되어 있나요?"
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "region", "text": text}
    ]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={"region": "서울특별시"})
    assert actual["follow_up_questions"] == []
    assert "강남구 주민등록 여부를 알려 주세요." in actual["answer"]


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


# ==========================================================
# PR review: question 버튼과 AI 사용자 정보 요청의 방향 분리
# ==========================================================

@pytest.mark.parametrize("request_text,field,expected", [
    ("현재 나이가 몇 살인가요?", "age", "age"),
    ("거주 지역은 어디인가요?", "region", "region"),
    ("현재 소득이 얼마인가요?", "income", "income"),
])
def test_pr_user_info_question_becomes_allowed_ask_info(monkeypatch, request_text, field, expected):
    stub(monkeypatch, response(follows=[{"kind": "question", "text": request_text}]))
    actual = g.generate_answer("신청 가능?", CONTEXTS, CHECKS, profile={})
    assert actual["follow_up_questions"] == [
        {"kind": "ask_info", "field": expected, "text": request_text}
    ]


@pytest.mark.parametrize("request_text", [
    "서울특별시의 어느 구에 거주하시나요?",
    "강남구에 주민등록되어 있나요?",
    "가구원 수가 몇 명인가요?",
    "가구원 수를 알려주세요.",
])
def test_pr_personal_information_request_never_question_button(monkeypatch, request_text):
    stub(monkeypatch, response(follows=[{"kind": "question", "text": request_text}]))
    actual = g.generate_answer("신청할 수 있나요?", CONTEXTS, CHECKS, profile={"region": "서울특별시"})
    assert actual["follow_up_questions"] == []
    assert "알려 주세요." in actual["answer"]


def test_pr_user_oriented_question_button_preserved(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "question", "text": "신청 서류는 무엇이 필요한가요?"},
        {"kind": "question", "text": "이 정책의 연령 기준은 어떻게 되나요?"},
    ]))
    actual = g.generate_answer("정책 안내", CONTEXTS, CHECKS, profile={"age": 25})
    assert actual["follow_up_questions"] == [
        {"kind": "question", "text": "신청 서류는 무엇이 필요한가요?"},
        {"kind": "question", "text": "이 정책의 연령 기준은 어떻게 되나요?"},
    ]


def test_pr_no_unsupported_reason_is_invented(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "household_size", "text": "가구원 수를 알려주세요."},
    ]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS)
    assert actual["answer"] == "자격 조건은 추가 확인이 필요합니다. 가구원 수를 알려 주세요."
    assert "지원 금액" not in actual["answer"]


def test_pr_request_in_answer_not_repeated(monkeypatch):
    stub(monkeypatch, response(
        answer="가구원 수를 알려 주세요.",
        follows=[{"kind": "ask_info", "field": "household_size", "text": "가구원 수를 알려주세요."}]
    ))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS)
    assert actual["answer"].count("가구원 수를 알려 주세요.") == 1


def test_pr_unknown_non_request_is_not_shown_as_button(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "unknown", "text": "추가 확인이 필요합니다."}
    ]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS)
    assert actual["follow_up_questions"] == []
    assert actual["answer"] == "자격 조건은 추가 확인이 필요합니다."


@pytest.mark.parametrize("user_question", [
    "몇 살부터 신청할 수 있나요?",
    "가구원 수가 몇 명이어야 하나요?",
    "강남구에 주민등록되어야 하나요?",
    "강남구에서 주민등록등본은 어디서 발급하나요?",
])
def test_pr_policy_questions_are_not_mistaken_for_personal_info(monkeypatch, user_question):
    stub(monkeypatch, response(follows=[{"kind": "question", "text": user_question}]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS)
    assert actual["follow_up_questions"] == [{"kind": "question", "text": user_question}]


@pytest.mark.parametrize("personal_request,expected", [
    ("직업이 무엇인가요?", "직업 정보를 알려 주세요."),
    ("학력을 알려주세요.", "학력을 알려 주세요."),
    ("근로 형태가 어떻게 되나요?", "근로 형태를 알려 주세요."),
])
def test_pr_other_personal_questions_move_into_answer(monkeypatch, personal_request, expected):
    stub(monkeypatch, response(follows=[{"kind": "question", "text": personal_request}]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS)
    assert actual["follow_up_questions"] == []
    assert expected in actual["answer"]


def test_pr_sensitive_personal_info_is_never_a_question_button(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "question", "text": "주민등록번호를 알려주세요."}
    ]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS)
    assert actual["follow_up_questions"] == []
    assert "주민등록번호" not in actual["answer"]


# ==========================================================
# PR #92 리뷰: 감지기가 놓친 정상 ask_info 보존
# ==========================================================

@pytest.mark.parametrize("request_text,field", [
    ("거주 지역은?", "region"),
    ("어느 지역에 거주하시나요?", "region"),
    ("정책 신청을 위해 나이가 몇 살인지 알려주세요.", "age"),
    ("지원 대상 확인을 위해 나이가 어떻게 되시나요?", "age"),
    ("가구 소득이 얼마인지 알려주시면 기준 금액과 비교해 드릴게요.", "income"),
    ("월 소득이 기준 금액보다 낮으신가요?", "income"),
    ("소득이 기준 중위소득 60% 이하인가요?", "income"),
])
def test_pr92_model_tagged_profile_request_is_kept(monkeypatch, request_text, field):
    """모델이 허용 필드로 태그한 사용자 입력 요청은 감지 정규식이 놓쳐도 버리지 않는다."""
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": field, "text": request_text}
    ]))
    actual = g.generate_answer("신청 가능한가요?", CONTEXTS, CHECKS, profile={})
    assert actual["follow_up_questions"] == [
        {"kind": "ask_info", "field": field, "text": request_text}
    ]


@pytest.mark.parametrize("policy_question", [
    "기준 중위소득 금액을 알려주세요.",
    "주거급여 기준임대료는 지역별로 얼마인가요?",
    "주거급여의 2026년 1인 가구 기준 금액도 알려주세요?",
])
def test_pr92_policy_criteria_mistagged_as_income_becomes_question(monkeypatch, policy_question):
    """정책 기준 질문은 모델이 ask_info/income으로 잘못 태그해도 question으로 복구한다."""
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "income", "text": policy_question}
    ]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={})
    assert actual["follow_up_questions"] == [
        {"kind": "question", "text": policy_question}
    ]


@pytest.mark.parametrize("request_text", [
    "월 소득이 기준 금액보다 낮으신가요?",
    "소득이 기준 중위소득 60% 이하인가요?",
])
def test_pr92_income_comparison_request_is_not_question_button(monkeypatch, request_text):
    """사용자 소득을 기준과 비교해 달라는 AI 요청은 사용자→AI 질문 버튼이 아니다."""
    stub(monkeypatch, response(follows=[{"kind": "question", "text": request_text}]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={})
    assert {"kind": "question", "text": request_text} not in actual["follow_up_questions"]


def test_pr92_existing_fixes_are_kept(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "question", "text": "전세도 대상인가요?"},
        {"kind": "question", "text": "거주 형태는 월세만 대상인가요?"},
        {"kind": "question", "text": "현재 월세로 살고 계신가요?"},
    ]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={})
    assert actual["follow_up_questions"] == [
        {"kind": "question", "text": "전세도 대상인가요?"},
        {"kind": "question", "text": "거주 형태는 월세만 대상인가요?"},
    ]


def test_pr92_family_age_is_not_profile_age():
    assert g._personal_information_request("자녀 나이가 몇 살인가요?", None) == "other"


def test_pr92_mixed_sentence_is_split(monkeypatch):
    stub(monkeypatch, response(follows=[
        {"kind": "question", "text": "지원 금액은 얼마인가요? 나이가 몇 세인가요?"}
    ]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={})
    assert actual["follow_up_questions"] == [
        {"kind": "question", "text": "지원 금액은 얼마인가요?"},
        {"kind": "ask_info", "field": "age", "text": "나이가 몇 세인가요?"},
    ]


# ==========================================================
# PR #92 남은 한계: question으로 온 사용자 정보 요청, 정책 요건 질문, 3개 제한
# ==========================================================

@pytest.mark.parametrize("request_text,expected", [
    ("어느 지역에 거주하시나요?", "region"),
    ("지원 대상 확인을 위해 나이가 어떻게 되시나요?", "age"),
])
def test_pr92_user_question_tagged_as_question_becomes_ask_info(monkeypatch, request_text, expected):
    stub(monkeypatch, response(follows=[{"kind": "question", "text": request_text}]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={})
    assert actual["follow_up_questions"] == [
        {"kind": "ask_info", "field": expected, "text": request_text}
    ]


@pytest.mark.parametrize("policy_question", [
    "어느 지역 거주자가 대상인가요?",
    "지원 대상 나이는 몇 세부터인가요?",
    "지원 대상 나이는 어떻게 되나요?",
    "몇 살부터 신청할 수 있나요?",
    "거주 형태는 월세만 대상인가요?",
    "신청하시려면 어떤 서류가 필요하신가요?",
    "지원금은 언제 받으시나요?",
])
def test_pr92_policy_question_stays_question(monkeypatch, policy_question):
    stub(monkeypatch, response(follows=[{"kind": "question", "text": policy_question}]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={})
    assert actual["follow_up_questions"] == [{"kind": "question", "text": policy_question}]


@pytest.mark.parametrize("policy_question,field", [
    ("지역 제한이 있는지 알려주세요.", "region"),
    ("연령 요건을 알려주세요.", "age"),
    ("소득 산정 방법을 알려주세요.", "income"),
    ("주거급여는 언제 지급되나요?", "income"),
])
def test_pr92_policy_requirement_mistagged_as_ask_info_becomes_question(monkeypatch, policy_question, field):
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": field, "text": policy_question}
    ]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={})
    assert actual["follow_up_questions"] == [{"kind": "question", "text": policy_question}]


def test_pr92_housing_benefit_word_is_not_income_input(monkeypatch):
    """'주거급여'의 '급여'를 사용자 소득 입력 요청으로 읽지 않는다."""
    stub(monkeypatch, response(follows=[
        {"kind": "ask_info", "field": "income", "text": "주거급여 신청 시 주의할 점을 알려주세요."}
    ]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={})
    assert not any(f["kind"] == "ask_info" for f in actual["follow_up_questions"])


def test_pr92_age_request_mentioning_policy_requirement_is_kept(monkeypatch):
    text = "만 나이를 알려주시면 해당 정책의 연령 요건에 맞는지 확인해 드릴게요."
    stub(monkeypatch, response(follows=[{"kind": "ask_info", "field": "age", "text": text}]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={})
    assert actual["follow_up_questions"] == [{"kind": "ask_info", "field": "age", "text": text}]


@pytest.mark.parametrize("kind,field", [("question", None), ("ask_info", "income")])
def test_pr92_spouse_income_comparison_is_not_profile_or_button(monkeypatch, kind, field):
    """배우자 소득은 본인 income 필드가 아니고, 사용자→AI 질문 버튼도 아니다."""
    text = "배우자 소득이 기준 중위소득 이하인가요?"
    follow = {"kind": kind, "text": text}
    if field:
        follow["field"] = field
    stub(monkeypatch, response(follows=[follow]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={})
    assert actual["follow_up_questions"] == []
    assert g._personal_information_request(text, None) == "other"


def test_pr92_split_does_not_drop_profile_request_at_limit(monkeypatch):
    """문장 분리로 4개가 돼도 아직 없는 프로필 입력 요청은 3개 제한에서 남긴다."""
    stub(monkeypatch, response(follows=[
        {"kind": "question", "text": "신청 서류는 무엇인가요?"},
        {"kind": "question", "text": "신청 절차는 어떻게 되나요?"},
        {"kind": "question", "text": "지원 금액은 얼마인가요? 나이가 몇 세인가요?"},
    ]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={})
    assert len(actual["follow_up_questions"]) == 3
    assert {"kind": "ask_info", "field": "age", "text": "나이가 몇 세인가요?"} in actual["follow_up_questions"]
    # 남은 항목은 원래 순서를 유지한다.
    assert actual["follow_up_questions"][0] == {"kind": "question", "text": "신청 서류는 무엇인가요?"}


# ==========================================================
# PR #92 최종 점검: 합성어·정책 기준 문장의 모델 field 오유지, 소득 비교 변형
# ==========================================================

@pytest.mark.parametrize("text,field", [
    ("연령 기준을 확인하시려면 공고를 보세요?", "age"),
    ("지역별 지원 내용이 궁금하신가요?", "region"),
    ("고령자 연령대별 지원 금액은 얼마인가요?", "age"),
    ("지역사회서비스 신청 방법은 무엇인가요?", "region"),
])
def test_pr92_compound_or_policy_word_does_not_keep_model_field(monkeypatch, text, field):
    """'지역별', '근로소득공제', '연령 기준'처럼 필드 단어가 정책 문맥에 쓰이면 입력 요청이 아니다."""
    stub(monkeypatch, response(follows=[{"kind": "ask_info", "field": field, "text": text}]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={})
    assert not any(f["kind"] == "ask_info" for f in actual["follow_up_questions"])


@pytest.mark.parametrize("text", [
    "수입이 기준보다 적으신 편인가요?",
    "소득이 기준 중위소득 이하가 아니신가요?",
    "급여가 기준 중위소득 50% 이하이신가요?",
])
def test_pr92_income_comparison_variants_become_income_request(monkeypatch, text):
    stub(monkeypatch, response(follows=[{"kind": "question", "text": text}]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={})
    assert actual["follow_up_questions"] == [{"kind": "ask_info", "field": "income", "text": text}]


def test_pr92_income_compound_policy_question_is_recovered(monkeypatch):
    """'근로소득공제'는 사용자 소득이 아니라 정책 질문이므로 question으로 복구한다."""
    text = "근로소득공제는 어떻게 계산하나요?"
    stub(monkeypatch, response(follows=[{"kind": "ask_info", "field": "income", "text": text}]))
    actual = g.generate_answer("질문", CONTEXTS, CHECKS, profile={})
    assert actual["follow_up_questions"] == [{"kind": "question", "text": text}]
