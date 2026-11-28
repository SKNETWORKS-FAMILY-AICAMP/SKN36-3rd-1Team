"""#165 리팩터링 전 동작 고정 테스트.

backend 코드를 상수화·중복 정리하기 전에, 기존 테스트가 직접 확인하지 않던
경계값·문구를 지금 동작 그대로 고정한다. 리팩터링 후에도 같은 결과여야 한다.
DB·네트워크·LLM 호출 없음.
"""
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.api.policy_catalog import PolicyCatalog
from backend.eligibility.checker import KST, _safe_review_text
from backend.llm import generator as g
from backend.llm.generator import LLMOutputError
from backend.rag import age_verdict, application_links, pipeline, reception_notice
from backend.rag.context_builder import complete_condition_evidence


UNKNOWN_RECEPTION_TEXT = "현재 접수 여부는 공식 공고에서 확인해 주세요."
PROFILE = {"age": 30, "region": None, "income": None, "child_age_months": None}


# --- 묶음 1: 자녀 월령 안내 분기 -------------------------------------------------


@pytest.mark.parametrize(
    ("text", "field", "expected"),
    [
        # field가 child_age_months면 문장과 관계없이 월령 안내가 우선한다.
        ("자녀 나이를 알려주세요", "child_age_months", "자녀의 월령(개월 수)을 알려 주세요."),
        ("거주 구를 알려주세요", "child_age_months", "자녀의 월령(개월 수)을 알려 주세요."),
        ("가구원 수를 알려주세요", "child_age_months", "자녀의 월령(개월 수)을 알려 주세요."),
        # 명시된 child_age는 기존 '자녀 나이' 안내를 유지한다.
        ("아이 월령을 알려주세요", "child_age", "자녀 나이를 알려 주세요."),
        # field가 다른 값이어도 '자녀 나이' 문장은 기존 안내를 유지한다.
        ("자녀 나이를 알려주세요", "other", "자녀 나이를 알려 주세요."),
        ("자녀 나이를 알려주세요", None, "자녀 나이를 알려 주세요."),
        # 월령·개월 문장은 월령 안내로 바꾼다.
        ("아이 월령을 알려주세요", None, "자녀의 월령(개월 수)을 알려 주세요."),
        ("영유아 개월 수를 알려주세요", "other", "자녀의 월령(개월 수)을 알려 주세요."),
        ("자녀가 있는지 알려주세요", None, "자녀 나이를 알려 주세요."),
    ],
)
def test_info_request_child_branches(text, field, expected):
    assert g._info_request_to_answer(text, field) == expected


# --- 묶음 2: 이름 없는 숫자 경계값 -----------------------------------------------


def _follow(kind, index):
    item = {"kind": kind, "text": f"신청 서류는 무엇인가요 {index}?"}
    if kind == "ask_info":
        item = {"kind": kind, "text": "나이를 알려 주세요.", "field": "age"}
    return item


def test_follow_up_limit_keeps_three_and_prefers_ask_info():
    items = [_follow("question", 1), _follow("question", 2), _follow("ask_info", 3),
             _follow("question", 4), _follow("question", 5)]
    kept = g._limit_follow_ups(items)
    assert len(kept) == 3
    assert kept == [items[0], items[1], items[2]]
    assert g._limit_follow_ups(items[:3]) == items[:3]


def test_model_response_with_four_follow_ups_is_invalid():
    raw = {"status": "answered", "answer": "근거 [1].", "used_source_ids": [1],
           "follow_up_questions": [_follow("question", i) for i in range(4)]}
    with pytest.raises(LLMOutputError, match="최대 3개"):
        g._validate_model_response(raw, {1})


@pytest.mark.parametrize(("length", "removed"), [(75, True), (76, False)])
def test_redundant_request_length_boundary(length, removed):
    request = "나이를 알려 주세요."
    sentence = "가" * (length - len(request) - 1) + " " + request
    assert len(sentence) == length
    result = g._remove_redundant_answer_requests("본문입니다 [1]. " + sentence, PROFILE)
    assert (result == "본문입니다 [1].") is removed


def test_review_text_is_cut_at_300_characters():
    assert _safe_review_text("가" * 400) == "가" * 300
    assert _safe_review_text("  가   나  ") == "가 나"
    assert _safe_review_text(None) == ""


def test_condition_evidence_skips_more_than_five_contexts():
    selected = [{"source_id": i, "policy_id": "POL_A", "text": "본문"} for i in range(1, 7)]
    assert complete_condition_evidence(selected, selected, [], "질문", PROFILE) == selected


@pytest.fixture
def package(tmp_path):
    from test_policy_catalog_api import write_package

    write_package(tmp_path)
    return tmp_path


@pytest.mark.parametrize(("day", "soon_ids"), [(17, ["POL_E"]), (18, ["POL_E", "POL_A"])])
def test_soon_filter_uses_seven_day_window(package, day, soon_ids):
    # POL_A는 11-25 마감: 11-18에는 7일 남아 포함, 11-17에는 8일 남아 제외.
    # POL_E는 11-21 마감이라 두 날 모두 7일 이내다.
    now = datetime(2026, 11, day, 12, tzinfo=KST)
    with patch("backend.eligibility.checker.datetime", wraps=datetime) as clock, \
            patch("backend.api.policy_catalog.datetime", wraps=datetime) as catalog_clock:
        clock.now.return_value = catalog_clock.now.return_value = now
        result = PolicyCatalog(package).list(status="soon")
    assert sorted(item.policy_id for item in result.items) == sorted(soon_ids)


def test_policy_list_limit_is_capped_at_500(package):
    with TestClient(create_app(session_factory=lambda: None,
                               catalog_factory=lambda: PolicyCatalog(package))) as client:
        assert client.get("/api/policies?limit=500").status_code == 200
        assert client.get("/api/policies?limit=501").status_code == 422
        assert client.get("/api/policies").json()["limit"] == 500
    assert PolicyCatalog(package).list().limit == 500


# --- 묶음 3: 여러 파일에 있던 문구·정규식 ------------------------------------------


def test_unknown_reception_text_is_identical_everywhere():
    assert reception_notice.UNKNOWN_RECEPTION == UNKNOWN_RECEPTION_TEXT
    assert application_links.UNKNOWN_RECEPTION == UNKNOWN_RECEPTION_TEXT
    assert pipeline.RECEPTION_NOTICE["unknown"] == UNKNOWN_RECEPTION_TEXT


def test_citation_patterns_match_the_same_numbers():
    text = "근거 [1]와 [23], [x], [ 4], [５]"
    expected = ["1", "23", "５"]
    for pattern in (g.CITATION_PATTERN, pipeline.CITATION, application_links.CITATION, age_verdict.CITATION,
                    reception_notice.CITATION):
        assert pattern.findall(text) == expected


# --- 묶음 4: 소스에 직접 들어 있던 보이지 않는 문자 ---------------------------------


def test_protect_markers_use_private_use_characters():
    assert application_links.PROTECT == "{}"
    assert application_links.PROTECTED.pattern == "(\\d+)"
