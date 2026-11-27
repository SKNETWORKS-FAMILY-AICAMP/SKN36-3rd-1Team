"""#79·#18·#90 실제 LLM 대표 시나리오 (수동 실행 전용, pytest 수집 대상 아님).

동결 릴리스 v1.4.0-candidate의 실제 조건 트리·근거 청크를 가상 검색 세션으로 넘기고,
실제 generator.generate_answer(OpenAI)와 FastAPI TestClient로 POST /api/ask를 실행한다.
Neo4j·Qdrant는 쓰지 않는다(R3D 실검색이 아님). 보낼 내용은 공개 정책 원문과 가상 나이뿐이다.

사용:
    python tests/manual/verify_issue79_18_90_llm_frozen_release.py --env-file <.env 경로> --output <새 JSON 경로>

--env-file에서 OPENAI_API_KEY·LLM_MODEL 등 LLM_ 접두 변수만 이 프로세스 환경에 넣는다. 값은 출력·기록하지 않는다.
"""

import argparse
import copy
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests/integration")]

from test_issue79_18_90_verification import DOCS, POLICIES, item  # noqa: E402

ICN_Q = "중소기업 재직청년 복지공유제 지원 지원할 수 있나요? 공식 신청 URL과 연령 근거를 알려주세요."
SCENARIOS = [
    {"id": "icn-18", "pid": "POL_STG_ICN_001", "age": 18, "question": ICN_Q},
    {"id": "icn-40", "pid": "POL_STG_ICN_001", "age": 40, "question": ICN_Q},
    {"id": "icn-none", "pid": "POL_STG_ICN_001", "age": None, "question": ICN_Q},
    {"id": "sejong-intern-link", "pid": "POL_GOV_059", "age": 25, "question": "세종시 청년 행정인턴 신청 링크랑 신청 방법 알려줘."},
    {"id": "sejong-committee-link", "pid": "POL_YC_065", "age": 27, "question": "세종시 위원회 청년참여 확대 신청 링크 알려줘. 지금 신청할 수 있어?"},
    {"id": "visit-only-online", "pid": "POL_GOV_016", "age": 30, "question": "장애인 자립생활주택 지원 온라인으로 신청할 수 있어?"},
    {"id": "guardian-45", "pid": "POL_GOV_042", "age": 45, "question": "발달재활서비스 지원(시자체) 우리 아이가 받을 수 있나요?"},
]
URL = re.compile(r"https?://[^\s)\]]+")
APPLY_CLAIM = re.compile(r"(?:신청|접수)\s*(?:URL|url|주소|링크|페이지)[^.\n]{0,40}https?://|https?://[^\s]+[^.\n]{0,30}(?:에서|로)\s*(?:온라인\s*)?(?:신청|접수)(?:할\s*수\s*있|하세요|가능)")
AGREED_LINK = "직접 신청 링크는 확인되지 않았어요"
AGREED_RECEPTION = "현재 접수 여부는 공식 공고에서 확인해 주세요"


def load_llm_env(path):
    from dotenv import dotenv_values

    values = dotenv_values(path)
    names = [k for k in values if k == "OPENAI_API_KEY" or k.startswith("LLM_")]
    for name in names:
        if values[name]:
            os.environ[name] = values[name]
    return sorted(names)


def run(scenario):
    from fastapi.testclient import TestClient

    from backend.api.main import create_app
    from backend.llm.generator import generate_answer

    policy = item(scenario["pid"])
    session = Mock()
    session.search_policy_context.side_effect = lambda *a, **kw: {"policies": [copy.deepcopy(policy)]}
    seen = {}

    def generate(question, contexts, checks, history, profile=None):
        seen["contexts"] = [{k: c.get(k) for k in ("source_id", "policy_id", "document_id", "chunk_id", "url")} for c in contexts]
        return generate_answer(question, contexts, checks, history, profile=profile)

    body = {"question": scenario["question"], "selected_policy_id": scenario["pid"],
            "profile": {"age": scenario["age"], "region": "", "income": None}, "history": []}
    started = time.monotonic()
    with TestClient(create_app(session_factory=lambda: session, generate_fn=generate)) as client:
        response = client.post("/api/ask", json=body)
    return response, seen.get("contexts"), round(time.monotonic() - started, 2)


def automatic_checks(scenario, payload):
    """기계 판정. 의미 판정(연령 설명 등)은 보고서에서 사람이 따로 기록한다."""
    pid = scenario["pid"]
    policy = POLICIES[pid]
    answer = payload.get("answer") or ""
    check = (payload.get("policy_checks") or [{}])[0]
    own_docs = {d for d, v in DOCS.items() if v["policy_id"] == pid}
    own_urls = {DOCS[d]["source_url"] for d in own_docs}
    result = {
        "apply_url_preserved": check.get("official_apply_url") == policy.get("official_apply_url"),
        "citations_same_policy": {c["document_id"] for c in payload.get("citations", [])} <= own_docs,
        "citation_urls_match_documents": all(c["url"] == DOCS[c["document_id"]]["source_url"] for c in payload.get("citations", [])),
        "foreign_urls_in_answer": sorted(set(URL.findall(answer)) - own_urls - {policy.get("official_apply_url")}),
    }
    if not policy.get("official_apply_url"):
        result["null_url_apply_claim_with_url"] = bool(APPLY_CLAIM.search(answer))
        result["agreed_missing_link_phrase"] = AGREED_LINK in answer
    if check.get("reception_status") == "unknown":
        result["agreed_reception_phrase"] = AGREED_RECEPTION in answer
        result["always_open_claim"] = bool(re.search(r"상시\s*(?:신청|접수)\s*(?:가능|할\s*수)", answer))
    statuses = {c["field"]: c["status"] for c in check.get("conditions", []) if c["field"] == "age"}
    overall = [c["status"] for c in check.get("conditions", []) if c["field"] == "eligibility"]
    result["api_age_status"] = statuses.get("age")
    result["api_overall_status"] = overall[-1] if overall else None
    result["exclusion_reasons_is_null"] = check.get("exclusion_reasons") is None
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    out = Path(args.output)
    if out.exists():
        raise SystemExit("output 파일이 이미 있습니다. 새 이름을 쓰세요.")
    names = load_llm_env(args.env_file)
    records = []
    for scenario in SCENARIOS:
        if args.only and scenario["id"] not in args.only:
            continue
        response, contexts, seconds = run(scenario)
        payload = response.json()
        records.append({**scenario, "http_status": response.status_code, "seconds": seconds,
                        "generator_contexts": contexts, "response": payload,
                        "automatic": automatic_checks(scenario, payload) if response.status_code == 200 else None})
        print(scenario["id"], response.status_code, seconds)
    kst = timezone(timedelta(hours=9))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "kind": "issue79-18-90 real LLM on frozen release (no Neo4j/Qdrant)",
        "executed_at": datetime.now(kst).isoformat(timespec="seconds"),
        "model": os.environ.get("LLM_MODEL") or "generator DEFAULT_MODEL",
        "env_names_loaded": names, "calls": len(records), "records": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
