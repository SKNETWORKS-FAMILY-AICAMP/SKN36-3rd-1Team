"""#39 Generator stage-1: month profile and no duplicate requests.

API/Pipeline/React child_age_months and ask_info integration is a separate team task.
"""

import pytest

from backend.llm import generator as g


@pytest.mark.parametrize(
    "value, expected",
    [(None, None), (0, 0), (10, 10), (24, 24), (-1, None), (10.0, None),
     ("10", None), (True, None), (False, None)],
)
def test_issue39_normalize_child_months_strict(value, expected):
    profile = g._normalize_profile({
        "age": 30, "region": "서울특별시", "income": 0.0,
        "child_age_months": value,
    })
    assert profile["child_age_months"] == expected
    assert profile["age"] == 30  # 신청자의 나이는 그대로 유지
    assert profile["income"] == 0.0


def test_issue39_missing_child_months_remains_none():
    profile = g._normalize_profile({"age": 30, "region": None, "income": None})
    assert profile.get("child_age_months") is None
    assert "child_age_months" not in profile  # old API profile remains backward-compatible


@pytest.mark.parametrize("text", [
    "자녀 월령을 알려 주세요.", "자녀는 몇 개월인가요?",
    "아이 나이가 어떻게 되나요?",
])
def test_issue39_filled_child_age_is_not_requested_again(text):
    profile = g._normalize_profile({
        "age": 30, "region": None, "income": None, "child_age_months": 0,
    })
    assert g._is_redundant_request(text, profile)


@pytest.mark.parametrize("text", [
    "이 정책의 자녀 월령 기준은 몇 개월인가요?",
    "자녀는 몇 개월 미만이어야 하나요?",
])
def test_issue39_policy_child_criteria_question_is_not_suppressed(text):
    profile = g._normalize_profile({
        "age": 30, "region": None, "income": None, "child_age_months": 10,
    })
    assert not g._is_redundant_request(text, profile)


def test_issue39_redundant_answer_request_is_removed_without_deleting_context():
    profile = g._normalize_profile({
        "age": 30, "region": None, "income": None, "child_age_months": 12,
    })
    answer = "조건 확인이 필요합니다. 자녀 월령을 알려 주세요."
    result = g._remove_redundant_answer_requests(answer, profile)
    assert "조건 확인이 필요합니다" in result
    assert "월령을 알려" not in result


def test_issue39_child_age_not_ask_info_until_api_contract_exists():
    profile = g._normalize_profile({
        "age": 30, "region": None, "income": None, "child_age_months": None,
    })
    raw = {
        "status": "info", "answer": "자녀 연령 확인이 필요합니다.",
        "used_source_ids": [], "follow_up_questions": [
            {"kind": "ask_info", "field": "child_age_months", "text": "자녀는 몇 개월인가요?"},
        ],
    }
    response = g._validate_model_response(raw, set(), profile)
    assert all(item.get("field") != "child_age_months" for item in response["follow_up_questions"])
    assert "자녀" in response["answer"]
    assert "개월" in response["answer"]


#39 REVIEW HARDENING
@pytest.mark.parametrize("kind, wrong_field", [
    ("ask_info", "age"), ("ask_info", "child_age_months"), ("question", None),
])
@pytest.mark.parametrize("text", [
    "아이 나이를 알려 주세요.",
    "아기는 몇 개월인가요?",
    "영유아 월령을 입력해 주세요.",
    "자녀 나이는 몇 세인가요?",
])
def test_issue39_child_age_never_misrouted_to_applicant_age(kind, wrong_field, text):
    # A child's month-age is NOT the applicant's age, even if the model tags it 'age'.
    profile = g._normalize_profile({
        "age": None, "region": None, "income": None, "child_age_months": None,
    })
    item = {"kind": kind, "text": text}
    if wrong_field is not None:
        item["field"] = wrong_field
    raw = {"status": "info", "answer": "정책 확인이 필요합니다.",
           "used_source_ids": [], "follow_up_questions": [item]}
    response = g._validate_model_response(raw, set(), profile)
    assert response["follow_up_questions"] == []
    assert "자녀" in response["answer"]


@pytest.mark.parametrize("value", [0, 12, 24])
@pytest.mark.parametrize("text", [
    "아이 나이를 알려 주세요.", "아기는 몇 개월인가요?",
    "자녀 월령을 알려 주세요.",
])
def test_issue39_child_months_already_given_never_asked_again(value, text):
    profile = g._normalize_profile({
        "age": None, "region": None, "income": None, "child_age_months": value,
    })
    for kind in ("ask_info", "question"):
        item = {"kind": kind, "text": text}
        if kind == "ask_info":
            item["field"] = "age"  # even a mis-tagged LLM output
        raw = {"status": "info", "answer": "자녀 월령 조건을 확인했습니다.",
               "used_source_ids": [], "follow_up_questions": [item]}
        response = g._validate_model_response(raw, set(), profile)
        assert response["follow_up_questions"] == []
        assert "알려 주세요" not in response["answer"]


def test_issue39_criteria_question_is_not_infant_profile_request():
    text = "이 정책의 자녀 월령 기준은 몇 개월인가요?"
    profile = g._normalize_profile({
        "age": None, "region": None, "income": None, "child_age_months": 24,
    })
    raw = {"status": "info", "answer": "조건을 확인합니다.",
           "used_source_ids": [],
           "follow_up_questions": [{"kind": "question", "text": text}]}
    response = g._validate_model_response(raw, set(), profile)
    assert response["follow_up_questions"] == [{"kind": "question", "text": text}]


def test_issue39_child_months_reach_llm_prompt_without_mutating_profile(monkeypatch):
    import json

    captured = []
    def fake_call(messages):
        captured.append(json.loads(messages[1]["content"].split("\n", 1)[1]))
        return json.dumps({
            "status": "info", "answer": "공식 조건을 확인합니다.",
            "used_source_ids": [], "follow_up_questions": [],
        }, ensure_ascii=False)

    monkeypatch.setattr(g, "_call_openai", fake_call)
    profile = {"age": 30, "region": "서울특별시", "income": 0,
               "child_age_months": 24, "debug": "omit"}
    contexts = [{"source_id": 5, "policy_id": "POL_TEST", "policy_name": "자녀 정책",
                 "text": "자녀 월령 24개월 이하입니다."}]
    checks = [{"policy_id": "POL_TEST", "conditions": [
        {"field": "children", "status": "eligible", "reason": "24개월 이하 조건 충족"}
    ], "reception_status": "unknown"}]
    result = g.generate_answer("신청할 수 있나요?", contexts, checks, profile=profile)
    assert result["status"] == "info"
    assert captured[0]["user_profile"]["child_age_months"] == 24
    assert captured[0]["user_profile"]["age"] == 30
    assert "debug" not in captured[0]["user_profile"]
    assert profile["child_age_months"] == 24  # no in-place mutation


# PR #112 follow-up: applicant age must not suppress missing child-month request.
@pytest.mark.parametrize("text", [
    "자녀 나이는 몇 세인가요?",
    "자녀 나이를 알려 주세요.",
    "아기는 몇 개월인가요?",
])
def test_issue39_applicant_age_does_not_silence_missing_child_months(text):
    profile = g._normalize_profile({
        "age": 30, "region": None, "income": None, "child_age_months": None,
    })
    assert not g._is_redundant_request(text, profile)
    for kind in ("ask_info", "question"):
        item = {"kind": kind, "text": text}
        if kind == "ask_info":
            item["field"] = "age"  # incorrectly supplied by LLM
        raw = {
            "status": "info", "answer": "확인이 필요합니다.",
            "used_source_ids": [], "follow_up_questions": [item],
        }
        result = g._validate_model_response(raw, set(), profile)
        assert result["follow_up_questions"] == []
        assert "자녀" in result["answer"]
        assert "알려" in result["answer"]


def test_issue39_missing_child_months_body_not_removed_by_applicant_age():
    profile = g._normalize_profile({
        "age": 30, "region": None, "income": None, "child_age_months": None,
    })
    answer = "조건 확인이 필요합니다. 자녀 나이를 알려 주세요."
    result = g._remove_redundant_answer_requests(answer, profile)
    assert "자녀 나이를 알려 주세요" in result


def test_issue39_policy_prefix_still_deduplicates_explicit_child_request():
    text = "이 정책 신청을 위해 자녀 월령을 알려 주세요."
    profile = g._normalize_profile({
        "age": 30, "region": None, "income": None, "child_age_months": 0,
    })
    assert g._requests_profile_value(text, "child_age_months")
    assert g._is_redundant_request(text, profile)
    answer = "신청 조건을 확인했습니다. " + text
    result = g._remove_redundant_answer_requests(answer, profile)
    assert "신청 조건을 확인했습니다" in result
    assert "자녀 월령을 알려" not in result


def test_issue39_policy_month_criteria_question_is_not_suppressed_by_new_guard():
    text = "이 정책의 자녀 월령 기준은 몇 개월인가요?"
    profile = g._normalize_profile({
        "age": 30, "region": None, "income": None, "child_age_months": 24,
    })
    assert not g._requests_profile_value(text, "child_age_months")
    assert not g._is_redundant_request(text, profile)


# #39 / PR #112 final natural-language regression: years phrasing means child months.
@pytest.mark.parametrize("text", [
    "아기는 몇 살인가요?",
    "자녀는 몇 살인가요?",
    "자녀는 몇 세인가요?",
    "아이가 몇 살인지 알려 주세요.",
    "영유아는 몇 살입니까?",
])
def test_issue39_child_years_question_is_not_applicant_age(text):
    missing = g._normalize_profile({
        "age": 30, "region": None, "income": None, "child_age_months": None,
    })
    assert g._requests_profile_value(text, "child_age_months")
    assert g._personal_information_request(text, missing) == "other"
    assert not g._is_redundant_request(text, missing)
    assert g._info_request_to_answer(text, "other") == "자녀의 월령(개월 수)을 알려 주세요."

    for kind in ("question", "ask_info"):
        item = {"kind": kind, "text": text}
        if kind == "ask_info":
            item["field"] = "age"  # mistaken model output must never request applicant age
        raw = {
            "status": "info", "answer": "확인할 정보가 있습니다.",
            "used_source_ids": [], "follow_up_questions": [item],
        }
        normalized = g._validate_model_response(raw, set(), missing)
        assert normalized["follow_up_questions"] == []
        assert "자녀의 월령(개월 수)을 알려 주세요." in normalized["answer"]

    for months in (0, 24):
        filled = {**missing, "child_age_months": months}
        assert g._is_redundant_request(text, filled)
        answer = g._remove_redundant_answer_requests(
            "정보를 확인했습니다. " + text, filled
        )
        assert answer == "정보를 확인했습니다."
        for kind in ("question", "ask_info"):
            item = {"kind": kind, "text": text}
            if kind == "ask_info":
                item["field"] = "age"
            raw = {
                "status": "info", "answer": "정보를 확인했습니다.",
                "used_source_ids": [], "follow_up_questions": [item],
            }
            normalized = g._validate_model_response(raw, set(), filled)
            assert normalized["follow_up_questions"] == []
            assert "알려 주세요" not in normalized["answer"]


@pytest.mark.parametrize("text", [
    "이 정책에서 자녀는 몇 살까지 지원되나요?",
    "자녀는 몇 세 미만이어야 하나요?",
    "이 정책의 자녀 나이 조건은 몇 살인가요?",
])
def test_issue39_child_years_criteria_question_remains_question(text):
    profile = g._normalize_profile({
        "age": 30, "region": None, "income": None, "child_age_months": 12,
    })
    assert not g._is_child_years_request(text)
    assert not g._requests_profile_value(text, "child_age_months")
    assert not g._is_redundant_request(text, profile)


# #39: Retain old text contract while supporting new years/months phrasing.
@pytest.mark.parametrize("text", [
    "자녀 나이는 몇 세인가요?",
    "자녀 나이는 몇 세인가요",
])
@pytest.mark.parametrize("kind, model_field", [
    ("ask_info", "child_age"),
    ("ask_info", "age"),
    ("question", None),
])
def test_issue39_legacy_child_age_text_compatibility(text, kind, model_field):
    profile = g._normalize_profile({
        "age": 30, "region": None, "income": None, "child_age_months": None,
    })
    item = {"kind": kind, "text": text}
    if model_field is not None:
        item["field"] = model_field
    raw = {
        "status": "info", "answer": "확인이 필요합니다.",
        "used_source_ids": [], "follow_up_questions": [item],
    }
    response = g._validate_model_response(raw, set(), profile)
    assert response["follow_up_questions"] == []
    assert "자녀 나이를 알려 주세요." in response["answer"]


def test_issue39_explicit_child_age_months_keeps_month_unit():
    assert g._info_request_to_answer(
        "자녀 나이는 몇 세인가요?", "child_age_months"
    ) == "자녀의 월령(개월 수)을 알려 주세요."
    assert g._info_request_to_answer(
        "아기는 몇 살인가요?", "other"
    ) == "자녀의 월령(개월 수)을 알려 주세요."


# PR #112 review #2: an explicit guardian/applicant age is NOT a child age.
@pytest.mark.parametrize("months", [None, 0])
@pytest.mark.parametrize("text", [
    "아이 돌봄 지원을 받으려면 보호자 나이를 알려 주세요.",
    "신청자 본인의 나이를 알려 주세요.",
])
def test_review112_guardian_age_is_applicant_age(months, text):
    profile = g._normalize_profile({
        "age": None, "region": None, "income": None,
        "child_age_months": months,
    })
    assert g._personal_information_request(text, profile) == "age"
    assert not g._requests_profile_value(text, "child_age_months")
    assert not g._is_redundant_request(text, profile)
    raw = {
        "status": "info", "answer": "신청자 나이를 확인합니다.",
        "used_source_ids": [],
        "follow_up_questions": [{
            "kind": "ask_info", "field": "age", "text": text,
        }],
    }
    result = g._validate_model_response(raw, set(), profile)
    assert result["follow_up_questions"] == [
        {"kind": "ask_info", "field": "age", "text": text}
    ]
    assert "자녀의 월령(개월 수)을 알려 주세요." not in result["answer"]


def test_review112_known_applicant_age_not_confused_with_missing_child_age():
    text = "아이 돌봄 지원을 받으려면 보호자 나이를 알려 주세요."
    profile = g._normalize_profile({
        "age": 30, "region": None, "income": None,
        "child_age_months": None,
    })
    assert g._is_redundant_request(text, profile)
    raw = {
        "status": "info", "answer": "정보를 확인했습니다.",
        "used_source_ids": [],
        "follow_up_questions": [{"kind": "ask_info", "field": "age", "text": text}],
    }
    result = g._validate_model_response(raw, set(), profile)
    assert result["follow_up_questions"] == []
    assert "월령(개월 수)을 알려" not in result["answer"]


# PR #112 review #3: method questions must remain follow-up questions.
@pytest.mark.parametrize("text", [
    "아기 월령은 어떻게 산정하나요?",
    "자녀 월령은 어떻게 계산하나요?",
    "아기 월령 계산 방법은 무엇인가요?",
])
def test_review112_child_months_calculation_method_question_kept(text):
    profile = g._normalize_profile({
        "age": 30, "region": None, "income": None, "child_age_months": 0,
    })
    assert g._is_child_months_method_question(text)
    assert not g._requests_profile_value(text, "child_age_months")
    assert not g._is_redundant_request(text, profile)
    raw = {
        "status": "info", "answer": "월령 기준을 확인합니다.",
        "used_source_ids": [],
        "follow_up_questions": [{"kind": "question", "text": text}],
    }
    result = g._validate_model_response(raw, set(), profile)
    assert result["follow_up_questions"] == [{"kind": "question", "text": text}]


def test_review112_reasking_known_child_months_is_still_removed():
    text = "아기는 몇 개월인가요?"
    profile = g._normalize_profile({
        "age": 30, "region": None, "income": None, "child_age_months": 0,
    })
    assert not g._is_child_months_method_question(text)
    assert g._requests_profile_value(text, "child_age_months")
    assert g._is_redundant_request(text, profile)
    raw = {
        "status": "info", "answer": "정보를 확인했습니다.",
        "used_source_ids": [],
        "follow_up_questions": [{"kind": "question", "text": text}],
    }
    result = g._validate_model_response(raw, set(), profile)
    assert result["follow_up_questions"] == []
