"""#79·#18·#90 후속 검증 (2026-11-27, dev 02659e2 기준).

PR #140(53정책 독립 연령 기대값·58청크·저장 답변 재생)과 PR #152(인천 18세 결론 보정·보정 문구 겹침)가
이미 다룬 범위는 다시 만들지 않는다. 이 파일은 그 뒤에도 남아 있던 범위만 추가한다.

- A. PR #140 fixture 밖의 신청자 AGE 40정책: 원문 문구로 확인한 독립 기대값 + 무상한(0~120) 23정책
- B. 신청자 나이와 수혜자 나이·OR 조건이 섞인 후보(데이터 담당 확인 전이라 xfail strict)
- C. #90 인천 POL_STG_ICN_001 연령 근거 문서·청크·검수 상태와 Checker·API
- D. #18 공식 신청 URL 8가지 시나리오와 신청 URL 장부 분류
- E. 문서·청크·Citation 계보(전체 릴리스)와 복수 정책 인용 번호 재배치

동결 릴리스(data/releases/v1.4.0-candidate)와 FastAPI TestClient만 사용한다. Neo4j·Qdrant·외부 LLM을 호출하지 않는다.
LLM 출력은 고정 문자열이고, D·E의 일부는 실제 generator.generate_answer 검증을 거치도록 _call_openai만 바꾼다.
"""

import copy
import json
import re
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from test_issue79_18_90_verification import CHUNKS, CONDITIONS, CORRECTIONS, DOCS, GROUPS, POLICIES, RELS, item

from backend.api.main import create_app
from backend.api.schemas import Citation
from backend.eligibility.checker import _condition, check_eligibility
from backend.llm import generator
from backend.rag.application_links import MISSING_LINK, UNKNOWN_RECEPTION

ROOT = Path(__file__).resolve().parents[2]
PR140_RULES = json.loads((ROOT / "tests/fixtures/pr140_age_expectations.json").read_text(encoding="utf-8"))["policies"]
PR140_IDS = {rule["policy_id"] for rule in PR140_RULES}
URL_EVIDENCE = json.loads((ROOT / "data/metadata/application_url_evidence_v1.4.0.json").read_text(encoding="utf-8"))
URL_REVIEW = json.loads((ROOT / "data/releases/v1.4.0-candidate/application_url_review.json").read_text(encoding="utf-8"))
ICN = "POL_STG_ICN_001"
ICN_NAME = "중소기업 재직청년 복지공유제 지원"
NO_UPPER = 120  # 데이터의 무상한 관례값. 121세 경계 입력으로 쓰지 않는다.


def chunk_text(document_id):
    return "\n".join(c["text"] for c in CHUNKS if c["document_id"] == document_id)


def age_tree_conditions(pid):
    """정책 조건 트리에 들어 있는 신청자 AGE 조건과 그 관계(근거 문서·검수 상태)."""
    groups = {gid for gid, g in GROUPS.items() if g["policy_id"] == pid}
    return [(CONDITIONS[r["target_id"]], r) for r in RELS
            if r["relation"] == "CONTAINS_CONDITION" and r["source_id"] in groups
            and CONDITIONS.get(r["target_id"], {}).get("type") == "AGE"]


def statuses(result):
    age = [c["status"] for c in result["conditions"] if c["field"] == "age"]
    overall = [c["status"] for c in result["conditions"] if c["field"] == "eligibility"][-1]
    return age, overall


def ask(policies, answer, *, age=27, used=(1,), question="신청 방법 알려줘", selected=None, real_generator=True):
    """가상 검색 결과 + 고정 LLM 출력으로 POST /api/ask를 호출한다.

    real_generator=True이면 generator.generate_answer의 응답 검증(인용 번호 등)을 실제로 거친다.
    """
    session = Mock()
    session.search_policy_context.side_effect = lambda *a, **kw: {"policies": [copy.deepcopy(p) for p in policies]}
    raw = {"status": "answered", "answer": answer, "used_source_ids": list(used), "follow_up_questions": []}
    seen = {}

    if real_generator:
        def sdk(messages):
            return json.dumps(raw, ensure_ascii=False)

        def generate(question_, contexts, checks, history, profile=None):
            seen["contexts"] = contexts
            return generator.generate_answer(question_, contexts, checks, history, profile=profile)

        patcher = patch.object(generator, "_call_openai", side_effect=sdk)
    else:
        def generate(question_, contexts, checks, history, profile=None):
            seen["contexts"] = contexts
            return raw

        patcher = patch.object(generator, "_call_openai", side_effect=AssertionError("외부 호출 금지"))
    body = {"question": question, "profile": {"age": age, "region": "", "income": None}, "history": []}
    if selected:
        body["selected_policy_id"] = selected
    with patcher, TestClient(create_app(session_factory=lambda: session, generate_fn=generate)) as client:
        response = client.post("/api/ask", json=body)
    return response, seen.get("contexts")


def ranked(*pids):
    out = []
    for rank, pid in enumerate(pids, 1):
        policy = item(pid)
        policy["best_rank"] = rank
        out.append(policy)
    return out


# ---------------------------------------------------------------------------
# A. PR #140 fixture 밖의 신청자 AGE 40정책
# ---------------------------------------------------------------------------

# 원문 문구는 각 정책의 근거 문서 청크에서 직접 읽어 확인했다(2026-11-27). Checker 출력에서 만든 값이 아니다.
# (하한, 상한) — 상한 None은 원문에 상한이 없음. 정수 나이로 쓴 '40세 미만'은 39세까지.
OFFICIAL_AGE = {
    "POL_GOV_002": (0, 65, "만 65세 이하"),
    "POL_GOV_005": (18, 39, "18세 이상 ~ 40세 미만"),
    "POL_GOV_007": (18, None, "만 18세 이상"),
    "POL_GOV_011": (12, 24, "만 12세 ~ 만 24세"),
    "POL_GOV_016": (19, None, "만 19세 이상"),
    "POL_GOV_017": (14, 64, "만 14세~64세"),
    "POL_GOV_023": (6, 64, "만 6세 이상부터 만 65세 미만"),
    "POL_GOV_024": (19, 39, "19세 ~ 39세"),
    "POL_GOV_030": (9, 24, "9세 이상에서 24세 이하"),
    "POL_GOV_063": (65, None, "65세 이상"),
}
# 신청자 기준으로 바로 쓸 수 없는 후보. 원문 근거와 사유를 보고서에 남기고, 경계 기대값으로 쓰지 않는다.
REVIEW_CANDIDATES = {
    "POL_GOV_022": "수혜자(18세 미만 보호대상아동) 나이. 신청 주체는 위탁가정이며 만 24세까지 연장 가능",
    "POL_GOV_042": "수혜자(만 18세 미만 장애아동·만 9세 미만 장애예견아동) 나이",
    "POL_GOV_036": "수혜자(18세 미만 결식우려 아동) 나이. 보호자 신청 여부 원문 확인 필요",
    "POL_GOV_059": "원문은 '19세 이상 39세 이하 청년 또는 세종시 소재 대학 재학생'(OR)인데 AGE가 필수 조건",
    "POL_GOV_029": "원문은 학년 기준(초4~중3). 10~16세는 환산값",
    "POL_GOV_049": "원문은 학년 기준(초·중학생), 근거 문서 청크에 나이 표기 없음. 6~16세는 환산값",
}
EXCLUSION_AGE = {"POL_005"}  # NOT 아래 75세 이상 지원 제외. tests/integration/test_age_verdict_api.py에서 검증.
NO_LIMIT = {
    "POL_GOV_009", "POL_GOV_010", "POL_GOV_012", "POL_GOV_013", "POL_GOV_014", "POL_GOV_015", "POL_GOV_025",
    "POL_GOV_031", "POL_GOV_032", "POL_GOV_033", "POL_GOV_035", "POL_GOV_037", "POL_GOV_038", "POL_GOV_039",
    "POL_GOV_040", "POL_GOV_041", "POL_GOV_045", "POL_GOV_047", "POL_GOV_052", "POL_GOV_055", "POL_GOV_060",
    "POL_GOV_062", "POL_GOV_064",
}


def oracle(lo, hi, age):
    if age is None:
        return "needs_check"
    return "eligible" if lo <= age <= (NO_UPPER if hi is None else hi) else "ineligible"


def boundaries(lo, hi):
    values = [lo - 1, lo, lo + 1]
    if hi is not None:
        values += [hi - 1, hi, hi + 1]
    return [v for v in dict.fromkeys(values) if 0 <= v <= NO_UPPER] + [None]


def test_every_release_age_policy_is_classified_once():
    """PR #140 53정책 + 이번 40정책이 릴리스의 신청자 AGE 정책 전부(93)이고 겹치지 않는다."""
    with_age = {g["policy_id"] for g in GROUPS.values() if age_tree_conditions(g["policy_id"])}
    mine = [set(OFFICIAL_AGE), set(REVIEW_CANDIDATES), EXCLUSION_AGE, NO_LIMIT]
    assert sum(map(len, mine)) == len(set().union(*mine)) == 40
    assert PR140_IDS.isdisjoint(set().union(*mine))
    assert with_age == PR140_IDS | set().union(*mine)
    assert len(with_age) == 93
    # 실제 릴리스의 AGE 연산자는 모두 BETWEEN(세)이다. LT/LTE/GT/GTE AGE는 없다.
    assert {(c["operator"], c["unit"]) for c in CONDITIONS.values() if c["type"] == "AGE"} == {("BETWEEN", "세")}


@pytest.mark.parametrize("pid", sorted(OFFICIAL_AGE))
def test_official_age_boundaries_reach_checker_and_api(pid):
    lo, hi, phrase = OFFICIAL_AGE[pid]
    [(condition, relation)] = age_tree_conditions(pid)
    # 데이터 값이 원문 기대값과 같고, 원문 문구가 연결된 근거 문서 청크에 실제로 있다.
    assert condition["value"] == f"{lo}~{NO_UPPER if hi is None else hi}"
    assert relation["evidence_document_id"] in {r["target_id"] for r in RELS
                                                if r["source_id"] == pid and r["relation"] == "SUPPORTED_BY"}
    assert phrase in chunk_text(relation["evidence_document_id"])

    policy = item(pid)
    for age in boundaries(lo, hi):
        expected = oracle(lo, hi, age)
        assert _condition(condition, {"age": age})["status"] == expected, (pid, age)
        result = check_eligibility({"age": age, "region": "", "income": None}, policy)
        age_rows, overall = statuses(result)
        if expected == "eligible":
            assert "ineligible" not in age_rows, (pid, age, age_rows)
        else:
            # 범위 밖·미입력은 개별 나이도 종합도 eligible이 되면 안 된다.
            assert "eligible" not in age_rows and overall != "eligible", (pid, age, age_rows, overall)
        if age is None or age in (lo, hi, lo - 1, (hi or NO_UPPER) + 1):
            response, _ = ask([policy], "나이 조건을 확인해 주세요 [1].", age=age, selected=pid, real_generator=False)
            assert response.status_code == 200, response.text
            api = response.json()["policy_checks"][0]
            api_age = [c["status"] for c in api["conditions"] if c["field"] == "age"]
            api_overall = [c["status"] for c in api["conditions"] if c["field"] == "eligibility"][-1]
            # Pipeline은 선택 정책 지역 보류만 더할 수 있고, Checker보다 확정 쪽으로 바꾸지 않는다.
            assert api_age == age_rows or (api_age and set(api_age) <= {"needs_check"}), (pid, age)
            assert api_overall in {overall, "needs_check"}, (pid, age, overall, api_overall)
            if api_overall != "ineligible":
                assert api["exclusion_reasons"] is None, (pid, age)


@pytest.mark.parametrize("pid", sorted(NO_LIMIT))
def test_no_upper_age_policies_never_reject_by_age(pid):
    [(condition, _)] = age_tree_conditions(pid)
    assert condition["value"] == f"0~{NO_UPPER}"
    for age in (0, 18, 64, 65, NO_UPPER):
        assert _condition(condition, {"age": age})["status"] == "eligible"
    assert _condition(condition, {"age": None})["status"] == "needs_check"
    age_rows, overall = statuses(check_eligibility({"age": None, "region": "", "income": None}, item(pid)))
    assert overall != "eligible"


# ---------------------------------------------------------------------------
# B. 신청자·수혜자 나이와 OR 조건 후보 (데이터 담당 확인 전)
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="데이터 확인 필요(#79): 수혜 아동 나이가 신청자 AGE로 저장돼 보호자 45세를 나이 미충족으로 판정")
@pytest.mark.parametrize("pid", ["POL_GOV_022", "POL_GOV_042"])
def test_guardian_age_is_not_judged_by_child_age(pid):
    assert any(word in POLICIES[pid]["eligibility"] for word in ("18세 미만", "만18세 미만"))
    age_rows, overall = statuses(check_eligibility({"age": 45, "region": "", "income": None}, item(pid)))
    assert "ineligible" not in age_rows and overall != "ineligible"


@pytest.mark.xfail(strict=True, reason="데이터 확인 필요(#79): 원문 OR('청년 또는 세종시 소재 대학 재학생')이 AGE 필수 조건으로 저장")
def test_sejong_intern_18_is_not_confirmed_ineligible():
    assert "또는 세종시에 소재한 대" in chunk_text("DOC_GOV_059")
    _, overall = statuses(check_eligibility({"age": 18, "region": "", "income": None}, item("POL_GOV_059")))
    assert overall != "ineligible"


# ---------------------------------------------------------------------------
# C. #90 인천 POL_STG_ICN_001
# ---------------------------------------------------------------------------

def test_incheon_age_condition_is_linked_to_2026_notice_document_and_chunk():
    rels = [r for r in RELS if r["target_id"] == "COND_AGE_19_39" and ICN in r["source_id"]]
    assert {r["relation"] for r in rels} == {"REQUIRES_CONDITION", "CONTAINS_CONDITION"}
    for rel in rels:
        assert rel["evidence_document_id"] == "DOC_STG_ICN_001_NOTICE_2026"
        assert rel["review_status"] == "verified"
        assert "DOC_STG_ICN_001_NOTICE_2026_P01_C01" in rel["evidence_location"]
    assert CONDITIONS["COND_AGE_19_39"]["value"] == "19~39"

    notice = DOCS["DOC_STG_ICN_001_NOTICE_2026"]
    assert notice["policy_id"] == ICN and notice["doc_type"] == "notice"
    assert notice["source_url"] == "https://youth.incheon.go.kr/bbs/bbsMsgDetail.do?msg_seq=491&bcd=notice"
    assert re.fullmatch(r"[0-9a-f]{64}", notice["content_hash"]) and re.fullmatch(r"[0-9a-f]{64}", notice["raw_source_sha256"])
    [chunk] = [c for c in CHUNKS if c["chunk_id"] == "DOC_STG_ICN_001_NOTICE_2026_P01_C01"]
    assert (chunk["policy_id"], chunk["document_id"], chunk["source_url"]) == (ICN, notice["document_id"], notice["source_url"])
    assert "19~39세" in chunk["text"]


def test_incheon_gov24_summary_and_ordinance_are_kept_distinct_from_notice():
    """정부24 구조화 요약(18~39)과 공식 공고(19~39)를 같은 근거로 섞지 않는다."""
    gov24 = chunk_text("DOC_STG_ICN_001_GOV24")
    assert "19~39세" not in gov24
    assert DOCS["DOC_STG_ICN_001_GOV24"]["source_type"] == "gov24_api"
    # 조례 문서는 제9조·제12조만 수록. 청년 정의(제2조 19~39세)는 릴리스에 없다(#90 후속 데이터 항목).
    assert "제2조" not in chunk_text("DOC_STG_ICN_001_ORD")
    # 교정 장부에 두 출처의 차이와 검수 해제 기록이 함께 남아 있다(데이터 담당 2026-11-20 기록).
    text = json.dumps(CORRECTIONS, ensure_ascii=False)
    assert "정부24 지원조건 코드는 18~39세" in text
    assert "review_status를 verified로 해제했다" in text


@pytest.mark.parametrize("age, direct", [(18, "ineligible"), (19, "eligible"), (39, "eligible"), (40, "ineligible"), (None, "needs_check")])
def test_incheon_checker_and_api_keep_individual_and_overall_separate(age, direct):
    condition = CONDITIONS["COND_AGE_19_39"]
    assert _condition(condition, {"age": age})["status"] == direct
    policy = item(ICN)
    # 종합 보류 사유는 UNKNOWN 나머지 그룹과 제외 대상 미모델링이다(검수 대기 needs_review가 아님).
    codes = {w["code"] for w in policy["data_quality"]["warnings"] if w.get("blocks_auto_eligibility")}
    assert {"UNKNOWN_CONDITION_GROUP", "EXCLUSION_NOT_MODELED"} <= codes

    response, _ = ask([policy], "나이 기준을 확인해 주세요 [1].", age=age, selected=ICN)
    assert response.status_code == 200, response.text
    check = response.json()["policy_checks"][0]
    age_row = next(c for c in check["conditions"] if c["field"] == "age")
    assert age_row["status"] == "needs_check"  # 품질 보류로 공개 나이 행도 보류
    if direct == "ineligible":
        assert f"만 {age}세가 포함되지 않습니다" in age_row["reason"]
    if direct == "eligible":
        assert "포함되지 않습니다" not in age_row["reason"]
    assert [c["status"] for c in check["conditions"] if c["field"] == "eligibility"][-1] == "needs_check"
    assert check["exclusion_reasons"] is None
    assert check["official_apply_url"] is None
    assert check["reception_status"] == "unknown"
    icn_urls = {d["source_url"] for d in DOCS.values() if d["policy_id"] == ICN}
    assert check["source_url"] in icn_urls
    assert {c["document_id"] for c in response.json()["citations"]} <= {d for d in DOCS if DOCS[d]["policy_id"] == ICN}


# ---------------------------------------------------------------------------
# D. #18 공식 신청 URL
# ---------------------------------------------------------------------------

VERIFIED_APPLY = {"POL_GOV_057", "POL_GOV_065", "POL_YC_006", "POL_STG_EX_001"}
UNVERIFIED_APPLY = {"POL_GOV_059", "POL_YC_009", "POL_STG_SEOUL_001"}


def test_apply_url_inventory_separates_verified_screens_from_unchecked_values():
    with_url = {pid for pid, p in POLICIES.items() if p.get("official_apply_url")}
    assert with_url == VERIFIED_APPLY | UNVERIFIED_APPLY
    corrected = {c["policy_id"]: c for c in CORRECTIONS["application_url_corrections"]}
    for pid in VERIFIED_APPLY:
        assert corrected[pid]["reason"] == "verified_apply_page"
        assert corrected[pid]["after"] == POLICIES[pid]["official_apply_url"]
    for pid in UNVERIFIED_APPLY:
        assert pid not in corrected
        assert URL_EVIDENCE["policies"][pid]["application_url"] == POLICIES[pid]["official_apply_url"]
    # 릴리스의 신청 URL 검수 파일도 같은 구분을 기록한다. 이 플래그는 API 응답에는 전달되지 않는다.
    review = {r["policy_id"]: r for r in URL_REVIEW}
    assert {pid for pid in with_url if review[pid]["reason"] == "manual_review_verified_apply_page"} == VERIFIED_APPLY
    # POL_YC_006은 신청기간 밖이라 '신청하기' 버튼까지만 확인했고 신청서는 보지 못해 live 확인은 false로 둔다.
    assert {pid for pid in with_url if review[pid]["live_application_page_verified"]} == VERIFIED_APPLY - {"POL_YC_006"}
    assert corrected["POL_YC_006"]["verified_stage"] == "apply_button_out_of_period"
    assert all(review[pid]["reason"] == "application_field_present" for pid in UNVERIFIED_APPLY)
    # 접수 화면 미확인. 특히 세종 행정인턴은 시청 첫 화면 주소다(#18 남은 신청 주소 확인 대상).
    assert POLICIES["POL_GOV_059"]["official_apply_url"] == "https://www.sejong.go.kr"
    for pid, policy in POLICIES.items():
        # 원문 주소·근거 문서 주소를 신청 주소로 복사하지 않는다.
        assert policy.get("official_apply_url") != policy["source_url"], pid
        assert policy.get("official_apply_url") not in {d["source_url"] for d in DOCS.values() if d["policy_id"] == pid}


@pytest.mark.parametrize("pid", sorted(VERIFIED_APPLY | UNVERIFIED_APPLY | {ICN, "POL_YC_065", "POL_GOV_016"}))
def test_api_passes_structured_apply_url_through_unchanged(pid):
    response, _ = ask([item(pid)], "정책 내용을 확인해 주세요 [1].", selected=pid, real_generator=False)
    assert response.status_code == 200, response.text
    check = response.json()["policy_checks"][0]
    assert check["official_apply_url"] == POLICIES[pid].get("official_apply_url")
    assert check["source_url"] in {d["source_url"] for d in DOCS.values() if d["policy_id"] == pid}


def test_scenario1_verified_apply_url_claim_is_kept():
    text = "승촌보 캠핑장 예약 화면에서 온라인으로 신청할 수 있어요 [1]."
    response, _ = ask([item("POL_GOV_065")], text, selected="POL_GOV_065")
    assert response.status_code == 200 and response.json()["answer"] == text


def test_scenario2_null_apply_url_web_claim_is_replaced_with_agreed_phrase():
    response, _ = ask([item(ICN)], "정부24 상세 페이지 홈페이지에서 온라인으로 신청할 수 있어요 [1].", selected=ICN)
    answer = response.json()["answer"]
    assert MISSING_LINK in answer and "온라인으로 신청할 수 있어요" not in answer


def test_scenario3_notice_only_policy_keeps_notice_check_guidance():
    text = "신청 공고는 인천청년포털 홈페이지에서 확인해 주세요 [1]."
    answer = ask([item(ICN)], text, selected=ICN)[0].json()["answer"]
    assert answer.startswith(text) and MISSING_LINK not in answer
    assert UNKNOWN_RECEPTION in answer


def test_scenario4_5_homepage_stored_as_apply_url_is_passed_through_not_upgraded():
    """세종 행정인턴: 신청 URL 값이 시청 첫 화면이고 공고 문서도 있다. API는 값을 그대로 전달하고 새 주소를 만들지 않는다."""
    response, contexts = ask([item("POL_GOV_059")], "세종시 홈페이지에서 신청할 수 있어요 [1].", selected="POL_GOV_059")
    body = response.json()
    assert body["policy_checks"][0]["official_apply_url"] == "https://www.sejong.go.kr"
    assert "DOC_GOV_059_SEJONG_NOTICE_2026" in {d for d in DOCS if DOCS[d]["policy_id"] == "POL_GOV_059"}
    urls = set(re.findall(r"https?://\S+", body["answer"]))
    assert not urls - {d["source_url"] for d in DOCS.values()} - {"https://www.sejong.go.kr"}


def test_scenario6_short_foreign_url_for_null_policy_is_corrected():
    pols = ranked(ICN, "POL_GOV_065")
    response, _ = ask(pols, f"{ICN_NAME}은 https://camp.xticket.kr/web/main 에서 신청할 수 있어요 [2].", used=(2,), question="두 정책 비교해줘")
    answer = response.json()["answer"]
    assert "camp.xticket.kr" not in answer and MISSING_LINK in answer


@pytest.mark.xfail(strict=True, reason="Pipeline 확인 필요(#103·#18): LINK_CLAIM이 URL~'신청' 100자 이내만 검사해 긴 URL(112자)을 다른 정책 신청 주소로 안내해도 보정되지 않음")
def test_scenario6_long_foreign_url_for_null_policy_is_corrected():
    pols = ranked(ICN, "POL_GOV_065")
    foreign = POLICIES["POL_GOV_065"]["official_apply_url"]
    assert len(foreign) > 100
    response, _ = ask(pols, f"{ICN_NAME}은 {foreign} 에서 신청할 수 있어요 [2].", used=(2,), question="두 정책 비교해줘")
    assert response.status_code != 200 or foreign not in response.json()["answer"]


def test_scenario7_visit_only_policy_online_claim_is_corrected():
    """POL_GOV_016 원문: '온라인/우편 접수 불가 (현장 방문 접수만 가능)', 신청 URL null."""
    assert "온라인/우편 접수 불가" in chunk_text("DOC_GOV_016")
    response, _ = ask([item("POL_GOV_016")], "홈페이지에서 온라인으로 신청할 수 있어요 [1].", selected="POL_GOV_016")
    answer = response.json()["answer"]
    assert "온라인으로 신청할 수 있어요" not in answer and MISSING_LINK in answer


def test_scenario8_unknown_reception_is_not_upgraded_to_always_open():
    response, _ = ask([item(ICN)], "상시 신청 가능합니다 [1].", selected=ICN)
    answer = response.json()["answer"]
    assert "상시 신청 가능" not in answer and UNKNOWN_RECEPTION.rstrip(".") in answer


@pytest.mark.xfail(strict=True, reason="Pipeline 확인 필요(#103 §14 후속): 링크 보정이 접수 문구를 붙인 뒤 접수 보정이 '상시' 절을 다시 바꿔 합의 문구가 두 번 나옴")
def test_scenario8_agreed_reception_phrase_appears_once():
    response, _ = ask([item(ICN)], "상시 신청 가능합니다 [1]. 정부24 상세 페이지에서 온라인으로 신청할 수 있어요 [1].", selected=ICN)
    assert response.json()["answer"].count(UNKNOWN_RECEPTION.rstrip(".")) == 1


# ---------------------------------------------------------------------------
# E. 문서·청크·Citation 계보
# ---------------------------------------------------------------------------

def test_release_chunk_document_policy_url_lineage_is_complete():
    supported = {(r["source_id"], r["target_id"]) for r in RELS if r["relation"] == "SUPPORTED_BY"}
    assert len({c["chunk_id"] for c in CHUNKS}) == len(CHUNKS) == 672
    assert {c["document_id"] for c in CHUNKS} == set(DOCS)  # 273문서 모두 청크가 있음
    for c in CHUNKS:
        doc = DOCS[c["document_id"]]
        assert c["policy_id"] in POLICIES
        assert (doc["policy_id"], c["source_url"]) == (c["policy_id"], doc["source_url"]), c["chunk_id"]
        assert (c["policy_id"], c["document_id"]) in supported, c["chunk_id"]


def test_public_citation_contract_does_not_carry_chunk_or_hash():
    """E2E-03-3: 공개 Citation에는 chunk_id·retrieved_at·content_hash가 없다(계약 변경 없이 BLOCKED로 기록)."""
    assert set(Citation.model_fields) == {"title", "url", "document_id", "page_number"}


def test_multi_policy_display_numbers_map_back_to_original_context_documents():
    pols = ranked(ICN, "POL_GOV_065")
    answer = f"{ICN_NAME}은 서해구 청년이 대상이에요 [5]. 승촌보 캠핑장 이용요금 감면은 예약 화면에서 신청해요 [2]."
    response, contexts = ask(pols, answer, used=(5, 2), question="두 정책 비교해줘")
    assert response.status_code == 200, response.text
    body = response.json()
    by_source = {str(c["source_id"]): c for c in contexts}
    original = [by_source["5"], by_source["2"]]
    shown = [int(n) for n in re.findall(r"\[(\d+)\]", body["answer"].split("\n\n")[0])]
    assert len(shown) == 2
    for number, context in zip(shown, original):
        citation = body["citations"][number - 1]
        assert citation["document_id"] == context["document_id"]
        assert citation["url"] == DOCS[context["document_id"]]["source_url"]
        assert DOCS[context["document_id"]]["policy_id"] == context["policy_id"]
        assert context["chunk_id"] in {c["chunk_id"] for c in CHUNKS if c["document_id"] == context["document_id"]}
    checked = {p["policy_id"] for p in body["policy_checks"]}
    assert {DOCS[c["document_id"]]["policy_id"] for c in body["citations"]} <= checked


@pytest.mark.xfail(strict=True, reason="Generator·Pipeline 확인 필요(#122): 정책명 전체를 쓴 문장이 다른 정책 문서를 인용해도 그대로 HTTP 200")
def test_sentence_naming_one_policy_does_not_cite_another_policys_document():
    pols = ranked(ICN, "POL_GOV_065")
    response, contexts = ask(pols, "승촌보 캠핑장 이용요금 감면은 예약 화면에서 신청해요 [1].", question="두 정책 비교해줘")
    assert [c["policy_id"] for c in contexts if str(c["source_id"]) == "1"] == [ICN]
    if response.status_code == 200:
        cited = {DOCS[c["document_id"]]["policy_id"] for c in response.json()["citations"]}
        assert cited == {"POL_GOV_065"}
