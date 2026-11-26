"""Keep upper bounds distinct from start months in the final response."""
import json

import pytest

from backend.llm import generator as g


@pytest.mark.parametrize("text,expected", [
    ("자녀 월령은 24개월 이하부터 해당합니다.", "자녀 월령은 24개월 이하에 해당합니다."),
    ("자녀 월령은 **24개월 이하**부터 해당합니다.", "자녀 월령은 **24개월 이하**에 해당합니다."),
    ("자녀 월령은 __24개월 이하__부터 해당합니다.", "자녀 월령은 __24개월 이하__에 해당합니다."),
    ("자녀는 12개월 미만부터 해당됩니다.", "자녀는 12개월 미만에 해당됩니다."),
    ("아이는 36 개월 이하 부터 해당합니다.", "아이는 36 개월 이하에 해당합니다."),
    ("아기 월령은 0개월 이하부터 해당합니다.", "아기 월령은 0개월 이하에 해당합니다."),
])
def test_only_the_incorrect_start_particle_is_repaired(text, expected):
    assert g._normalize_child_month_bound_wording(text) == expected


@pytest.mark.parametrize("text", [
    "자녀는 24개월부터 해당합니다.",
    "자녀는 24개월 이상부터 해당합니다.",
    "자녀 월령은 24개월 이하에 해당합니다.",
    "자녀 월령 기준은 24개월 미만입니다.",
    "자녀는 6개월부터 24개월까지 해당합니다.",
    "자녀 월령은 6개월 이상, 24개월 이하에 해당합니다.",
    "근속 기간은 24개월 이하부터 해당합니다.",
    "자녀 월령의 '24개월 이하부터 해당합니다'라는 표현은 잘못됐습니다.",
    "자녀 월령 기준은 확인되지 않았습니다.",
])
def test_valid_bounds_and_other_contexts_are_not_changed(text):
    assert g._normalize_child_month_bound_wording(text) == text


REAL_MODEL_ANSWER = (
    "자녀 월령은 원문상 **24개월 이하**부터 해당합니다. "
    "따라서 24개월은 포함되지만, 시작 월령은 이번 근거에서 확인되지 않았습니다. "
    "또한 이 자료는 회귀검증용 가상 정책이라 실제 복지정책이 아니며, "
    "접수 일정도 확인되지 않았습니다 [1]."
)


@pytest.mark.parametrize("months", [None, 0, 24])
def test_actual_model_output_is_repaired_in_final_validation(months):
    raw = {"status": "answered", "answer": REAL_MODEL_ANSWER, "used_source_ids": [1],
           "follow_up_questions": [{"kind": "question", "text": "이 정책의 신청 자격 조건은 무엇인가요?"}]}
    profile = g._normalize_profile({"age": 30, "region": "서울특별시", "income": 300,
                                    "child_age_months": months})
    out = g._validate_model_response(raw, {1}, profile)
    assert out["answer"] == REAL_MODEL_ANSWER.replace("**24개월 이하**부터", "**24개월 이하**에")
    assert "시작 월령은 이번 근거에서 확인되지 않았습니다" in out["answer"]
    assert out["used_source_ids"] == [1]
    assert out["follow_up_questions"] == raw["follow_up_questions"]
    assert raw["answer"] == REAL_MODEL_ANSWER


def test_actual_model_output_is_repaired_through_generate_answer(monkeypatch):
    monkeypatch.setattr(g, "_call_openai", lambda messages: json.dumps({
        "status": "answered", "answer": REAL_MODEL_ANSWER, "used_source_ids": [1],
        "follow_up_questions": [],
    }, ensure_ascii=False))
    out = g.generate_answer("아이 돌봄 지원은 몇 개월부터 시작되나요?", [{"source_id": 1,
        "text": "회귀검증용 가상 정책. 자녀 월령은 24개월 이하입니다."}], [],
        profile={"age": 30, "region": "서울특별시", "income": 300, "child_age_months": 24})
    assert "이하**부터" not in out["answer"]
    assert "이하**에 해당합니다" in out["answer"]
