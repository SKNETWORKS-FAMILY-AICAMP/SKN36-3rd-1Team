"""#35 최종 리뷰: 화면 표시·공유 지역/나이 검사·OpenAI 클라이언트 회귀 검증."""

from copy import deepcopy
import sys
from types import ModuleType, SimpleNamespace

import pytest

from backend.eligibility import checker as c
from backend.llm import generator as g
from database.region_search import ALIASES, SIDOS


def _policy(logic):
    return {
        "policy_id": "POL_TEST_GROUP_DISPLAY",
        "graph_status": "FOUND",
        "graph": {
            "eligibility": {
                "condition_tree": [{
                    "group_id": "GRP_POL_INTERNAL_123",
                    "logic": logic,
                    "conditions": [{"type": "AGE", "operator": "BETWEEN", "value": "19~34"}],
                    "groups": [],
                }],
                "unknown_logic_groups": [],
                "structure_issues": [],
            }
        },
        "data_quality": {"hold_auto_eligibility": False, "affected_attributes": []},
    }


@pytest.mark.parametrize("logic,expected_status,fragment", [
    ("AND", "eligible", "모든 조건"),
    ("OR", "eligible", "하나 이상"),
    ("NOT", "ineligible", "충족되지"),
])
def test_group_reason_is_readable_without_internal_ids(logic, expected_status, fragment):
    result = c.check_eligibility({"age": 25}, _policy(logic))
    groups = [item for item in result["conditions"] if item["field"] == "eligibility"]
    assert len(groups) == 1
    assert groups[0]["status"] == expected_status
    assert fragment in groups[0]["reason"]
    assert "GRP_" not in groups[0]["reason"]
    assert set(groups[0]) == {"field", "label", "status", "reason"}


def test_household_review_display_merged_and_original_unchanged():
    policy = _policy("AND")
    policy["graph"]["eligibility"]["condition_tree"][0]["conditions"].append(
        {"type": "HOUSING_HOUSEHOLD", "name": "무주택세대"}
    )
    policy["graph"]["policy"] = {
        "household_source_review": "needs_review",
        "household_followup": "check_notice",
        "household_check_point": "가구원 수 확인",
    }
    before = deepcopy(policy)
    result = c.check_eligibility({"age": 25}, policy)
    raw = result["conditions"]
    snap = deepcopy(raw)
    assert len([x for x in raw if x["field"] == "housing"]) == 2
    displayed = c.summarize_conditions_for_display(raw)
    household = [x for x in displayed if x["field"] == "housing"]
    assert len(household) == 1
    assert "무주택세대" in household[0]["reason"]
    assert "가구원 수 확인" in household[0]["reason"]
    assert raw == snap
    assert policy == before
    assert all("GRP_POL" not in str(x["reason"]) for x in displayed)


def test_household_without_review_does_not_erase_distinct_conditions():
    rows = [
        {"field": "housing", "status": "needs_check", "label": "가구", "reason": "무주택"},
        {"field": "housing", "status": "needs_check", "label": "가구", "reason": "세대주"},
        {"field": "housing", "status": "eligible", "label": "가구", "reason": "확정"},
    ]
    assert c.summarize_conditions_for_display(rows) == rows


def test_age_validation_is_shared_checker_function():
    assert g._is_valid_age is c._is_valid_age
    assert c._is_valid_age(0) and c._is_valid_age(120)
    assert not c._is_valid_age(True) and not c._is_valid_age(121)


def test_sido_source_is_all_17_regions():
    assert len(SIDOS) == 17
    assert set(ALIases_value for ALIases_value in ALIASES.values()) == set(SIDOS)
    assert len(g._SIDO_VARIANTS) == 17
    for full in SIDOS:
        assert full in g._SIDO_VARIANTS[full]


@pytest.mark.parametrize("profile_region,same_area", [
    ("서울특별시", "서울시"),
    ("서울특별시", "서울"),
    ("부산광역시", "부산시"),
    ("세종특별자치시", "세종시"),
    ("경기도", "경기"),
    ("강원특별자치도", "강원"),
    ("제주특별자치도", "제주"),
])
def test_sido_aliases_do_not_trigger_district_request(profile_region, same_area):
    assert not g._is_detail_region_request(f"{same_area}에 거주하시나요?", profile_region)


@pytest.mark.parametrize("region,text", [
    ("서울특별시", "강남구에 주민등록되어 있나요?"),
    ("경기도", "수원시에 거주하시나요?"),
    ("전북특별자치도", "전주시 거주인가요?"),
])
def test_real_district_requests_still_detected(region, text):
    assert g._is_detail_region_request(text, region)


@pytest.fixture
def fake_openai(monkeypatch):
    sdk = ModuleType("openai")

    class OpenAIError(Exception):
        pass

    sdk.OpenAIError = OpenAIError
    sdk.APIStatusError = type("APIStatusError", (OpenAIError,), {})
    sdk.APIConnectionError = type("APIConnectionError", (OpenAIError,), {})
    sdk.APITimeoutError = type("APITimeoutError", (OpenAIError,), {})
    created = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            created.append(kwargs)
            self.chat = SimpleNamespace(completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content="{}")
                )])
            ))

    sdk.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", sdk)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("LLM_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    g._get_openai_client.cache_clear()
    yield created
    g._get_openai_client.cache_clear()


def test_openai_client_reused_for_same_settings(fake_openai):
    messages = [{"role": "user", "content": "질문"}]
    assert g._call_openai(messages) == "{}"
    assert g._call_openai(messages) == "{}"
    assert len(fake_openai) == 1


def test_openai_client_recreated_when_timeout_changes(fake_openai, monkeypatch):
    messages = [{"role": "user", "content": "질문"}]
    g._call_openai(messages)
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "19")
    g._call_openai(messages)
    assert len(fake_openai) == 2
    assert fake_openai[1]["timeout"] == 19.0
