"""Final-response intent regressions: explanations versus personal input."""
import json

import pytest

from backend.llm import generator as g


def response(text, profile, kind="question", field=None):
    followup = {"kind": kind, "text": text}
    if field is not None:
        followup["field"] = field
    return g._validate_model_response({
        "status": "info", "answer": "정책 안내입니다.", "used_source_ids": [],
        "follow_up_questions": [followup],
    }, set(), g._normalize_profile(profile))


METHODS = [
    "월 소득을 어디서 확인하나요?",
    "월 소득은 어디에서 확인할 수 있나요?",
    "어디서 월 소득을 확인하나요?",
    "월 소득은 어떻게 계산하나요?",
    "월 소득을 조회하는 방법은 무엇인가요?",
    "월 소득 확인 방법을 알려 주세요.",
    "월 소득은 어떤 사이트에서 조회하나요?",
    "거주 지역을 어디서 확인하나요?",
    "만 나이는 어떻게 계산하나요?",
    "아기 월령을 계산하는 방법은 무엇인가요?",
    "월 소득을 확인하려면 어디로 가야 하나요?",
    "월 소득은 어디에서 확인 가능한가요?",
    "소득은 어떻게 조회합니까?",
    "월 소득 산정 방식은 무엇인가요?",
]
THRESHOLDS = [
    "아이 돌봄 지원은 몇 개월부터 시작되나요?",
    "아이는 몇 개월부터 신청할 수 있나요?",
    "아이 돌봄 지원은 몇 개월까지 받을 수 있나요?",
    "몇 개월부터 아이 돌봄 지원이 시작되나요?",
    "자녀 지원은 몇 개월까지 가능한가요?",
    "영유아 보육 서비스는 몇 개월부터 이용 가능한가요?",
    "아기 지원은 몇 개월부터 가능한가요?",
    "아이 돌봄 서비스는 몇 개월에 종료되나요?",
    "아이 돌봄 지원의 시작 월령은 몇 개월인가요?",
    "이 서비스의 신청 가능 월령은 몇 개월인가요?",
]


@pytest.mark.parametrize("text", METHODS + THRESHOLDS)
@pytest.mark.parametrize("filled", [False, True])
@pytest.mark.parametrize("kind,field", [("question", None), ("ask_info", "income"),
                                       ("ask_info", "child_age_months")])
def test_explanation_remains_question_through_final_response(text, filled, kind, field):
    profile = {"age": 30 if filled else None, "region": "서울특별시" if filled else None,
               "income": 300 if filled else None, "child_age_months": 24 if filled else None}
    out = response(text, profile, kind, field)
    assert out["answer"] == "정책 안내입니다."
    assert out["follow_up_questions"] == [{"kind": "question", "text": text}]


@pytest.mark.parametrize("text,field", [
    ("월 소득을 알려 주세요.", "income"),
    ("현재 월 소득은 얼마인가요?", "income"),
    ("월 소득은 어떻게 되나요?", "income"),
    ("거주 지역을 알려 주세요.", "region"),
    ("현재 거주 지역은 어디인가요?", "region"),
    ("보호자께서는 몇 살이신가요?", "age"),
    ("아이 월령은 알고 있으니 거주 지역을 알려 주세요.", "region"),
    ("아이 월령을 확인한 뒤 월 소득을 알려 주세요.", "income"),
    ("월 소득을 확인해서 알려 주세요.", "income"),
    ("소득 조회 방법은 나중에 안내하고, 현재 월 소득을 알려 주세요.", "income"),
])
@pytest.mark.parametrize("filled", [False, True])
def test_real_input_requests_still_use_the_requested_profile_field(text, field, filled):
    profile = {"age": None, "region": None, "income": None, "child_age_months": 24}
    if filled:
        profile[field] = {"age": 30, "region": "서울특별시", "income": 300}[field]
    out = response(text, profile)
    if filled:
        assert out["follow_up_questions"] == []
    else:
        assert out["follow_up_questions"][0]["kind"] == "ask_info"
        assert out["follow_up_questions"][0]["field"] == field


@pytest.mark.parametrize("months", [None, 0, 24])
def test_child_input_request_remains_body_only_and_deduplicated(months):
    out = response("자녀의 월령을 알려 주세요.", {"age": 30, "region": None,
                   "income": None, "child_age_months": months})
    assert out["follow_up_questions"] == []
    assert ("알려 주세요" in out["answer"]) == (months is None)


def test_generation_pipeline_preserves_real_model_shaped_questions(monkeypatch):
    texts = [METHODS[0], THRESHOLDS[0]]
    monkeypatch.setattr(g, "_call_openai", lambda messages: json.dumps({
        "status": "info", "answer": "정책 안내입니다.", "used_source_ids": [],
        "follow_up_questions": [{"kind": "question", "text": t} for t in texts],
    }, ensure_ascii=False))
    out = g.generate_answer("정책을 설명해 주세요.",
        [{"source_id": 1, "text": "가상 정책 근거입니다."}], [],
        profile={"age": 30, "region": None, "income": 300, "child_age_months": 24})
    assert out["follow_up_questions"] == [{"kind": "question", "text": t} for t in texts]
