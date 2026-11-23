"""PR #112 review: verify final answers and buttons without external LLM calls."""

import json

import pytest

from backend.llm import generator as g


def final_response(monkeypatch, text, profile, field=None):
    follow = {"kind": "ask_info" if field else "question", "text": text}
    if field:
        follow["field"] = field
    raw = {"status": "info", "answer": "공식 조건을 확인합니다.",
           "used_source_ids": [], "follow_up_questions": [follow]}
    monkeypatch.setattr(g, "_call_openai", lambda messages: json.dumps(raw))
    return g.generate_answer(
        "신청 조건을 확인해 주세요.",
        [{"source_id": 1, "policy_id": "TEST", "text": "공식 정책 안내"}],
        [], profile=profile,
    )


@pytest.mark.parametrize("months", [None, 0, 24])
@pytest.mark.parametrize("field", [None, "age", "child_age_months"])
@pytest.mark.parametrize("text", [
    "아이는 몇 개월부터 신청할 수 있나요?",
    "아이는 몇 개월까지 지원할 수 있나요?",
    "자녀는 몇 개월부터 지원받을 수 있나요?",
    "아기 월령에 따라 지원금은 어떻게 달라지나요?",
    "아기 월령은 어떻게 산정하나요?",
])
def test_policy_month_questions_survive_final_response(monkeypatch, months, field, text):
    result = final_response(monkeypatch, text, {"child_age_months": months}, field)
    assert result["follow_up_questions"] == [{"kind": "question", "text": text}]
    assert result["answer"] == "공식 조건을 확인합니다."


@pytest.mark.parametrize("months", [None, 0, 24])
@pytest.mark.parametrize("age", [None, 30])
@pytest.mark.parametrize("field", [None, "age", "child_age_months"])
@pytest.mark.parametrize("text", [
    "아이를 돌보는 보호자께서는 몇 살이신가요?",
    "아이를 돌보는 분은 몇 살이신가요?",
    "아이를 돌보는 보호자 본인의 나이를 알려 주세요.",
    "자녀의 보호자는 몇 살이신가요?",
])
def test_guardian_requests_use_applicant_age(monkeypatch, months, age, field, text):
    result = final_response(monkeypatch, text, {"age": age, "child_age_months": months}, field)
    expected = [] if age is not None else [{"kind": "ask_info", "field": "age", "text": text}]
    assert result["follow_up_questions"] == expected
    assert result["answer"] == "공식 조건을 확인합니다."


@pytest.mark.parametrize("months", [None, 0, 24])
@pytest.mark.parametrize("known", [False, True])
@pytest.mark.parametrize("model_field", [None, "age", "child_age_months"])
@pytest.mark.parametrize("text,target,value", [
    ("아이 월령은 알고 있으니 거주 지역을 알려 주세요.", "region", "서울특별시"),
    ("아이 월령을 확인한 뒤 월 소득을 알려 주세요.", "income", 0),
])
def test_actual_requested_field_controls_buttons(monkeypatch, months, known, model_field, text, target, value):
    profile = {"child_age_months": months, target: value if known else None}
    result = final_response(monkeypatch, text, profile, model_field)
    expected = [] if known else [{"kind": "ask_info", "field": target, "text": text}]
    assert result["follow_up_questions"] == expected
    assert result["answer"] == "공식 조건을 확인합니다."


@pytest.mark.parametrize("months", [None, 0, 24])
@pytest.mark.parametrize("text", ["현재 몇 개월인가요?", "자녀 나이는 몇 세인가요?"])
def test_explicit_month_field_retains_unit_and_deduplication(monkeypatch, months, text):
    result = final_response(monkeypatch, text, {"age": 30, "child_age_months": months}, "child_age_months")
    assert result["follow_up_questions"] == []
    suffix = " 자녀의 월령(개월 수)을 알려 주세요." if months is None else ""
    assert result["answer"] == "공식 조건을 확인합니다." + suffix


@pytest.mark.parametrize("age", [None, 30])
@pytest.mark.parametrize("months", [None, 0, 24])
@pytest.mark.parametrize("field", [None, "age", "child_age_months"])
def test_childs_own_age_is_not_applicant_age(monkeypatch, age, months, field):
    text = "자녀 본인의 나이를 알려 주세요."
    result = final_response(monkeypatch, text, {"age": age, "child_age_months": months}, field)
    assert result["follow_up_questions"] == []
    assert ("알려 주세요" in result["answer"]) == (months is None)


@pytest.mark.parametrize("age", [None, 30])
def test_spouses_own_age_does_not_become_applicant_button(monkeypatch, age):
    result = final_response(monkeypatch, "배우자 본인의 나이를 알려 주세요.", {"age": age}, "age")
    assert result["follow_up_questions"] == []


@pytest.mark.parametrize("text", [
    "자녀 본인의 소득을 알려 주세요.",
    "배우자 본인의 월 소득을 알려 주세요.",
])
def test_family_income_is_not_applicant_income(monkeypatch, text):
    result = final_response(monkeypatch, text, {"income": None}, "income")
    assert result["follow_up_questions"] == []


def test_explicit_month_field_without_profile_is_body_only(monkeypatch):
    result = final_response(monkeypatch, "현재 몇 개월인가요?", None, "child_age_months")
    assert result["follow_up_questions"] == []
    assert "자녀의 월령(개월 수)을 알려 주세요." in result["answer"]
