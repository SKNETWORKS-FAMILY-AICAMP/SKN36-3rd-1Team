"""Additional final-response regressions found in the PR #112 review."""
import json

import pytest

from backend.llm import generator as g


def final(text, months, age=None, kind="question", field=None, answer="정책 안내입니다."):
    item = {"kind": kind, "text": text}
    if field is not None:
        item["field"] = field
    profile = g._normalize_profile({"age": age, "region": None, "income": None,
                                    "child_age_months": months})
    return g._validate_model_response({"status": "info", "answer": answer,
        "used_source_ids": [], "follow_up_questions": [item]}, set(), profile)


POLICY_QUESTIONS = [
    "자녀 월령에 따라 지원금은 얼마인가요?",
    "자녀 월령에 따른 지원 금액을 알려 주세요.",
    "아이 월령에 따라 받을 수 있는 금액은 어떻게 되나요?",
    "자녀 월령에 따른 지원 금액은 얼마인가요?",
    "아이 돌봄 지원은 자녀가 몇 개월일 때부터 신청 가능한가요?",
    "자녀가 몇 개월이어야 지원되나요?",
    "월 소득을 어디에서 볼 수 있나요?",
    "월 소득은 어디서 알 수 있나요?",
    "자녀의 나이에 따라 지원금은 얼마인가요?",
]


@pytest.mark.parametrize("text", POLICY_QUESTIONS)
@pytest.mark.parametrize("months", [None, 0, 24])
@pytest.mark.parametrize("kind,field", [("question", None), ("ask_info", "age"),
    ("ask_info", "income"), ("ask_info", "child_age_months")])
def test_policy_question_preserves_text_and_answer(text, months, kind, field):
    out = final(text, months, 30, kind, field)
    assert out["follow_up_questions"] == [{"kind": "question", "text": text}]
    assert out["answer"] == "정책 안내입니다."


@pytest.mark.parametrize("text", [
    "보호자 나이는 알고 있으니 아이가 몇 개월인지 알려 주세요.",
    "신청자의 나이는 확인했으니 자녀의 월령을 알려 주세요.",
    "지원받으려면 아이가 몇 개월인지 알려 주세요.",
    "아이 돌봄 지원 대상 확인을 위해 자녀는 몇 개월인가요?",
])
@pytest.mark.parametrize("months", [None, 0, 24])
@pytest.mark.parametrize("age", [None, 30])
@pytest.mark.parametrize("kind,field", [("question", None), ("ask_info", "age"),
    ("ask_info", "child_age_months")])
def test_child_request_is_not_inferred_from_earlier_guardian_mention(text, months, age, kind, field):
    out = final(text, months, age, kind, field)
    assert out["follow_up_questions"] == []
    assert ("월령(개월 수)을 알려 주세요" in out["answer"]) == (months is None)


@pytest.mark.parametrize("text", POLICY_QUESTIONS[:6])
def test_body_policy_explanation_is_not_deleted(text):
    out = final("신청 서류는 무엇인가요?", 24, 30,
                answer="정책 안내입니다. " + text)
    assert text in out["answer"]


def test_generate_answer_preserves_age_dependent_benefit_question(monkeypatch):
    text = POLICY_QUESTIONS[0]
    monkeypatch.setattr(g, "_call_openai", lambda messages: json.dumps({
        "status": "info", "answer": "정책 안내입니다.", "used_source_ids": [],
        "follow_up_questions": [{"kind": "ask_info", "field": "child_age_months", "text": text}],
    }, ensure_ascii=False))
    out = g.generate_answer("지원 금액이 궁금해요.", [{"source_id": 1,
        "text": "회귀검증용 가상 근거입니다."}], [], profile={"age": 30,
        "region": None, "income": None, "child_age_months": 24})
    assert out["follow_up_questions"] == [{"kind": "question", "text": text}]


@pytest.mark.parametrize("text", [
    "아이를 돌보시는 분은 몇 살이신가요?",
    "자녀를 양육하는 분은 몇 살이신가요?",
    "보호자분께서는 나이가 어떻게 되시나요?",
    "신청자의 현재 만 나이를 알려 주세요.",
])
@pytest.mark.parametrize("months", [None, 0, 24])
@pytest.mark.parametrize("age", [None, 30])
@pytest.mark.parametrize("kind,field", [("question", None), ("ask_info", "age"),
    ("ask_info", "child_age_months")])
def test_guardian_request_handles_honorifics_without_using_child_months(text, months, age, kind, field):
    out = final(text, months, age, kind, field)
    assert out["answer"] == "정책 안내입니다."
    assert out["follow_up_questions"] == ([] if age is not None else [
        {"kind": "ask_info", "field": "age", "text": text}])
