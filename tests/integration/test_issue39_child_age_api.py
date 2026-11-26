"""#39 자녀 월령(child_age_months) API·Pipeline 연동 테스트. DB·LLM 연결 없음.

AskRequest → UserProfile → Pipeline → Checker(실제 함수) → Generator(대역) → AskResponse 경로에서
값이 바뀌거나 빠지지 않는지, 신청자 나이(age)와 섞이지 않는지 확인한다.
정책 조건 fixture는 v1.4.0 릴리스의 POL_GOV_050 조건 노드(COND_CHILD_AGE_LE_24M)를 그대로 옮긴 것이고,
정책 객체 나머지는 판정용 sample_only 값이다.
"""

import logging
from importlib.util import find_spec
from unittest.mock import Mock

import pytest

from backend.eligibility.checker import check_eligibility

if find_spec("fastapi") is None or find_spec("httpx") is None:
    pytest.skip("API 검증에는 fastapi와 httpx가 필요합니다.", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from backend.api.main import create_app  # noqa: E402

BASE = {"question": "장애인가정 양육비용 지원을 받을 수 있나요?", "history": []}
PROFILE = {"age": 30, "region": "전북", "income": None}
INVALID_BODY = {"detail": {"code": "INVALID_REQUEST", "message": "질문 및 프로필 입력 형식을 확인해 주세요."}}

# data/releases/v1.4.0-candidate/neo4j/nodes_eligibility_condition.jsonl 의 COND_CHILD_AGE_LE_24M
LTE_24M = {
    "condition_id": "COND_CHILD_AGE_LE_24M", "type": "SPECIAL_TARGET", "name": "대상 자녀 연령: 24개월 이하",
    "operator": "LTE", "value": "24", "unit": "개월",
}
# 다른 정책이 쓰는 COND_CHILD_AGE_UNDER_24M(24개월 미만) 형태. LT 경로가 함께 유지되는지 본다.
LT_24M = {
    "condition_id": "COND_CHILD_AGE_UNDER_24M", "type": "SPECIAL_TARGET", "name": "대상 자녀 연령: 24개월 미만",
    "operator": "LT", "value": "24", "unit": "개월",
}


def child_policy(condition, pid="POL_GOV_050", extra_conditions=()):
    """sample_only: 실제 조건 노드만 남긴 판정용 정책 객체."""
    return {
        "policy_id": pid, "policy_name": "가상 자녀 월령 정책(sample_only)", "best_rank": 1,
        "graph_status": "FOUND", "data_quality": {"hold_auto_eligibility": False, "affected_attributes": []},
        "graph": {
            "policy": {"application_period": "상시신청"},
            "eligibility": {
                "condition_tree": [{"group_id": f"GRP_{pid}_ROOT", "logic": "AND",
                                    "conditions": [condition, *extra_conditions], "groups": []}],
                "unknown_logic_groups": [], "structure_issues": [],
            },
        },
        "evidence": [{"rank": 1, "chunk_id": f"CHUNK_{pid}_1", "document_id": f"DOC_{pid[4:]}",
                      "text": "가상 공고 원문", "source_url": "https://example.org/notice"}],
    }


def answer(**changes):
    return {"status": "answered", "answer": "원문 [1]", "used_source_ids": [1], "follow_up_questions": [], **changes}


class Harness:
    def __init__(self, policies, *, check_fn=check_eligibility, generated=None):
        self.session = Mock()
        self.session.search_policy_context.return_value = {"policies": policies}
        self.checks = []
        self.generator = Mock(return_value=generated or answer())

        def recording_check(profile, policy):
            self.checks.append(dict(profile))
            return check_fn(profile, policy)

        self.client = TestClient(create_app(session_factory=Mock(return_value=self.session),
                                            check_fn=recording_check, generate_fn=self.generator))

    def post(self, profile_changes=None, *, omit_child=False, **request_changes):
        profile = {**PROFILE, **(profile_changes or {})}
        if omit_child:
            profile.pop("child_age_months", None)
        with self.client as client:
            return client.post("/api/ask", json={**BASE, "profile": profile, **request_changes})


def condition(body, field):
    [check] = body["policy_checks"]
    return next(c for c in check["conditions"] if c["field"] == field)


# ---------- API 입력 검증 ----------

@pytest.mark.parametrize("value", [None, 0, 10, 23, 24, 25, 300])
def test_valid_child_age_months_is_accepted_and_forwarded_unchanged(value):
    h = Harness([child_policy(LTE_24M)])
    response = h.post({"child_age_months": value})
    assert response.status_code == 200
    # Checker와 Generator가 같은 값을 받는다. 0은 None으로 바뀌지 않고, 타입도 그대로 int다.
    checked = h.checks[0]["child_age_months"]
    generated = h.generator.call_args.kwargs["profile"]["child_age_months"]
    assert checked == generated == value
    assert type(checked) is type(value) and type(generated) is type(value)


def test_omitted_child_age_months_is_backward_compatible_and_means_unknown():
    h = Harness([child_policy(LTE_24M)])
    response = h.post(omit_child=True)
    assert response.status_code == 200
    assert h.checks[0]["child_age_months"] is None
    assert h.generator.call_args.kwargs["profile"] == {
        "age": 30, "region": "전북특별자치도", "income": None, "child_age_months": None,
    }
    # 응답 계약 키는 그대로다(자녀 월령을 응답에 되돌려 주지 않는다).
    assert set(response.json()) == {"status", "answer", "citations", "policy_checks", "follow_up_questions",
                                    "checked_at", "notice", "is_sample"}


@pytest.mark.parametrize("value", [-1, 24.5, 24.0, "24", "", True, False, [24], {"months": 24}])
def test_invalid_child_age_months_is_422_without_echo_or_search(value, caplog):
    h = Harness([child_policy(LTE_24M)])
    marker = {"child_age_months": value}
    with caplog.at_level(logging.DEBUG):
        response = h.post(marker)
    assert response.status_code == 422
    # 고정 문구만 돌려준다. Pydantic 기본 오류의 input·loc·msg를 노출하지 않는다.
    assert response.json() == INVALID_BODY
    assert "child_age_months" not in response.text
    assert "child_age_months" not in caplog.text
    h.session.search_policy_context.assert_not_called()
    h.generator.assert_not_called()


def test_invalid_value_with_distinctive_marker_is_not_echoed(caplog):
    h = Harness([child_policy(LTE_24M)])
    with caplog.at_level(logging.DEBUG):
        response = h.post({"child_age_months": "private-child-marker-77"})
    assert response.status_code == 422
    assert "private-child-marker-77" not in response.text
    assert "private-child-marker-77" not in caplog.text


def test_malformed_json_body_is_422():
    h = Harness([child_policy(LTE_24M)])
    with h.client as client:
        response = client.post("/api/ask", content=b'{"question": "q", "profile": {"child_age_months": 2',
                               headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    h.session.search_policy_context.assert_not_called()


def test_child_age_months_does_not_relax_existing_field_validation():
    h = Harness([child_policy(LTE_24M)])
    for changes in ({"age": True, "child_age_months": 10}, {"age": "30", "child_age_months": 10},
                    {"income": "10", "child_age_months": 10}, {"age": 121, "child_age_months": 10}):
        assert h.post(changes).status_code == 422


# ---------- Pipeline·Checker·Generator 전달 ----------

def test_child_age_is_not_a_search_filter_and_age_stays_applicant_age():
    h = Harness([child_policy(LTE_24M)])
    assert h.post({"age": 1, "child_age_months": 30}).status_code == 200
    _, kwargs = h.session.search_policy_context.call_args
    assert kwargs["filters"] == {"region": "전북특별자치도"}
    assert h.checks[0]["age"] == 1 and h.checks[0]["child_age_months"] == 30
    profile = h.generator.call_args.kwargs["profile"]
    assert (profile["age"], profile["child_age_months"]) == (1, 30)


def test_selected_policy_filter_is_unchanged_by_child_age():
    h = Harness([child_policy(LTE_24M)])
    assert h.post({"child_age_months": 3}, selected_policy_id="POL_GOV_050").status_code == 200
    assert h.session.search_policy_context.call_args.kwargs["filters"] == {"policy_id": "POL_GOV_050"}


@pytest.mark.parametrize("months,expected", [(0, "eligible"), (23, "eligible"), (24, "ineligible"),
                                             (25, "ineligible"), (None, "needs_check")])
def test_lt_child_condition_through_api(months, expected):
    """LT(24개월 미만) 경로도 Pipeline이 판정을 바꾸지 않는다."""
    h = Harness([child_policy(LT_24M, pid="POL_TEST_LT")])
    body = h.post({"child_age_months": months}).json()
    assert condition(body, "children")["status"] == expected


# 자녀 월령 조건 단독 결과(children)와 정책 종합 결과(eligibility)를 따로 확인한다.
# 이 fixture는 POL_GOV_050 루트 그룹에 자녀 월령 조건만 있으므로 둘이 같다.
@pytest.mark.parametrize("months,child_expected,omit", [
    (0, "eligible", False), (10, "eligible", False), (23, "eligible", False), (24, "eligible", False),
    (25, "ineligible", False), (None, "needs_check", False), (None, "needs_check", True),
])
def test_pol_gov_050_lte_24_boundary_through_api(months, child_expected, omit):
    h = Harness([child_policy(LTE_24M)])
    response = h.post({"child_age_months": months}, omit_child=omit)
    assert response.status_code == 200
    body = response.json()
    child = condition(body, "children")
    assert child["status"] == child_expected
    assert "24개월 이하" in child["reason"]
    assert condition(body, "eligibility")["status"] == child_expected
    # 정책 ID·출처·인용이 그대로 유지된다.
    assert body["policy_checks"][0]["policy_id"] == "POL_GOV_050"
    assert body["citations"][0]["document_id"] == "DOC_GOV_050"
    assert body["policy_checks"][0]["source_url"] == "https://example.org/notice"
    if child_expected == "needs_check":
        assert body["notice"]["kind"] == "warn"


def test_child_condition_is_reported_separately_from_overall_result():
    """자녀 월령이 충족돼도 다른 조건이 미확인이면 종합 결과는 needs_check로 남는다(sample_only 조건 추가)."""
    disabled_unknown = {"condition_id": "COND_SAMPLE_DISABLED", "type": "SPECIAL_TARGET",
                      "name": "장애인 가구(sample_only)", "operator": "EQ", "value": "true", "unit": None}
    h = Harness([child_policy(LTE_24M, extra_conditions=[disabled_unknown])])
    body = h.post({"child_age_months": 24}).json()
    assert condition(body, "children")["status"] == "eligible"
    assert condition(body, "eligibility")["status"] != "eligible"


@pytest.mark.parametrize("age", [0, 1, 2, 24, None])
def test_applicant_age_is_never_used_as_child_months(age):
    h = Harness([child_policy(LTE_24M)])
    body = h.post({"age": age}, omit_child=True).json()
    assert condition(body, "children")["status"] == "needs_check"


def test_missing_child_months_is_not_rewritten_as_missing_applicant_profile():
    """Checker의 자녀 월령 보류 사유를 Pipeline이 신청자 입력 누락 문구로 덮지 않는다."""
    h = Harness([child_policy(LTE_24M)])
    reason = condition(h.post(omit_child=True).json(), "children")["reason"]
    assert "자녀 월령" in reason
    assert "입력한 정보가 없어" not in reason


def test_failed_quality_scope_still_holds_child_condition():
    """기존 데이터 품질 경고 보류를 자녀 월령 입력이 해제하지 않는다."""
    item = child_policy(LTE_24M)
    item["data_quality"] = {"hold_auto_eligibility": True, "affected_attributes": ["all"]}
    h = Harness([item])
    body = h.post({"child_age_months": 3}).json()
    assert condition(body, "children")["status"] == "needs_check"
    assert body["notice"]["kind"] == "warn"


# ---------- ask_info 중복 질문·계약 유지 ----------

def test_ask_info_age_is_kept_when_age_missing_and_dropped_when_present():
    follow = [{"kind": "ask_info", "field": "age", "text": "나이를 알려주세요."}]
    h = Harness([child_policy(LTE_24M)], generated=answer(follow_up_questions=follow))
    assert h.post({"age": None, "child_age_months": 0}).json()["follow_up_questions"] == follow
    h = Harness([child_policy(LTE_24M)], generated=answer(follow_up_questions=follow))
    assert h.post({"age": 30, "child_age_months": 0}).json()["follow_up_questions"] == []


@pytest.mark.parametrize("months", [None, 0, 10])
def test_ask_info_child_age_months_is_not_activated_in_api_contract(months):
    """ask_info.field는 age/region/income만. 자녀 월령은 본문 안내로만 한다(§5.1)."""
    follow = [{"kind": "ask_info", "field": "child_age_months", "text": "자녀 월령을 알려주세요."}]
    h = Harness([child_policy(LTE_24M)], generated=answer(follow_up_questions=follow))
    response = h.post({"child_age_months": months})
    assert (response.status_code, response.json()["detail"]["code"]) == (502, "LLM_OUTPUT_INVALID")
