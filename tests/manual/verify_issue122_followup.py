"""#122 후속 실제 R3D 읽기 전용 판정 전달 recorder (PR #148 미검증 시나리오 전용).

run_issue122_followup_readonly.ps1로 실행한다. DB에는 검색(읽기)만 하며 노드·관계·Point 수를 전후 비교한다.
- 사용자 프로필은 가상(sample_only)이다.
- 실제 LLM은 --llm일 때만, 아래 LLM_CASES 3건으로 고정한다(외부 호출 횟수 기록).
- 결과 파일은 exclusive-create(기존 파일 덮어쓰기 금지). 예외는 타입만 기록한다(비밀값 노출 방지).
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

from issue122_followup_oracles import overall, rows, semantic_mismatch  # noqa: E402

QUESTION = "이 정책의 자격 충족 여부와 신청 방법, 접수 상태를 알려줘"
# (시나리오, 선택 정책 또는 None=다중 검색, 프로필, 질문)
CASES = [
    ("r1_required_and_fail", "POL_GOV_024", {"age": 45}, QUESTION),
    ("r2_required_and_pass", "POL_GOV_024", {"age": 30}, QUESTION),
    ("r3_global_quality_hold", "POL_BOK_004", {"age": 45}, QUESTION),
    ("r4_or_income_hold", "POL_GOV_030", {"age": 20}, QUESTION),
    ("r5_required_age_fail_with_or", "POL_GOV_030", {"age": 30}, QUESTION),
    ("r6_multi_youth_rent", None, {"age": 45}, "청년 임대료나 월세 지원 정책 자격 알려줘"),
    ("r7_multi_disability", None, {"age": 30}, "장애인 자립 지원 정책 자격 알려줘"),
    # 실DB 확정 ineligible(소득 UNKNOWN + 필수 나이 불충족). PR #148은 이 사례를 Mock으로만 실행했다.
    ("r8_income_unknown_age_fail", "POL_GOV_007", {"age": 17}, QUESTION),
]
# 실DB에서 GOV_024는 region 경고로 종합 needs_check이므로, 확정 ineligible 의미 검증은 r8로 한다(사용자 승인 3회).
LLM_CASES = ("r1_required_and_fail", "r8_income_unknown_age_fail", "r6_multi_youth_rent")


def compare(row):
    """Checker·Pipeline(Generator 입력)·API를 정책별로 비교한다."""
    api = {c["policy_id"]: c for c in row["response"].get("policy_checks", [])}
    raw = {c["policy_id"]: c for c in row.get("checker", [])}
    gen = {c["policy_id"]: c for c in row.get("generator_checks", [])}
    ctx_policy = {c["document_id"]: c["policy_id"] for c in row.get("contexts", [])}
    out = []
    for pid, check in api.items():
        stages = {"checker": raw.get(pid), "pipeline": gen.get(pid), "api": check}
        item = {
            "policy_id": pid,
            "present_in_all_stages": all(stages.values()),
            "rows": {k: rows(v["conditions"]) if v else None for k, v in stages.items()},
            "overall": {k: overall(v["conditions"]) if v else None for k, v in stages.items()},
            "exclusion_reasons": {k: (v or {}).get("exclusion_reasons") for k, v in stages.items()},
            "reasons_equal": bool(stages["checker"]) and [c["reason"] for c in check["conditions"]]
            == [c["reason"] for c in stages["checker"]["conditions"]],
            "quality": {k: (row.get("quality", {}).get(pid) or {}).get(k)
                        for k in ("hold_auto_eligibility", "affected_attributes", "hold_reasons")},
        }
        item["rows_equal"] = item["present_in_all_stages"] and len({json.dumps(v) for v in item["rows"].values()}) == 1
        item["overall_equal"] = len(set(item["overall"].values())) == 1
        final = item["overall"]["api"]
        exc = item["exclusion_reasons"]
        item["exclusion_rule_ok"] = exc["api"] == exc["pipeline"] and (final == "ineligible" or exc["api"] is None)
        item["has_needs_check"] = any(s == "needs_check" for _, s in item["rows"]["api"])
        out.append(item)
    cited_docs = [c["document_id"] for c in row["response"].get("citations", [])]
    citation = {
        "cited_document_ids": cited_docs,
        "all_cited_in_contexts": all(d in ctx_policy for d in cited_docs),
        "cited_policies": sorted({ctx_policy.get(d, "?") for d in cited_docs}),
        "api_policies_subset_of_context_policies": set(api) <= set(ctx_policy.values()),
    }
    semantics = None
    if len(api) == 1:
        final = next(iter(out))["overall"]["api"] if out else None
        semantics = {"final_overall": final, "mismatch": semantic_mismatch(final, row["response"].get("answer", ""))}
    return out, citation, semantics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--llm", action="store_true")
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit("Output exists; refusing overwrite")
    from database import embedding
    embedding.load_env_file(args.env_file)
    from fastapi.testclient import TestClient
    from backend.api.main import create_app, _generate_answer
    from backend.eligibility.checker import check_eligibility
    from database.hybrid_search import HybridSearchSession
    from tests.integration.test_final_v140_smoke import _guard_targets
    _guard_targets()
    report = {
        "mode": "real_llm" if args.llm else "mock_generator", "llm_calls": 0, "llm_errors": 0,
        "model": os.getenv("LLM_MODEL", "gpt-5.4-mini") if args.llm else None,
        "manifest_sha256": hashlib.sha256(
            (ROOT / "data/releases/v1.4.0-candidate/manifest.json").read_bytes()).hexdigest(),
        "cases": [],
    }
    captured = {}

    def checker(profile, policy):
        result = check_eligibility(profile, policy)
        captured.setdefault("checker", []).append(deepcopy(result))
        captured.setdefault("quality", {})[policy["policy_id"]] = deepcopy(policy.get("data_quality"))
        captured.setdefault("structure", {})[policy["policy_id"]] = deepcopy(
            (policy.get("graph") or {}).get("eligibility"))
        return result

    def generator(question, contexts, checks, history, profile=None):
        captured["generator_checks"] = deepcopy(checks)
        captured["contexts"] = [{k: c.get(k) for k in ("source_id", "policy_id", "document_id", "chunk_id")}
                                for c in contexts]
        if args.llm:
            report["llm_calls"] += 1
            try:
                result = _generate_answer(question, contexts, checks, history, profile=profile)
            except Exception:
                report["llm_errors"] += 1
                raise
        else:
            sid = contexts[0]["source_id"]
            result = {"status": "answered", "answer": f"검증용 고정 응답 [{sid}]",
                      "used_source_ids": [sid], "follow_up_questions": []}
        captured["generator_output"] = deepcopy(result)
        return result

    try:
        app = create_app(session_factory=lambda: HybridSearchSession(profile="b2_06b_v140"),
                         check_fn=checker, generate_fn=generator)
        with TestClient(app) as client:
            session = app.state.session
            if session is None:
                raise RuntimeError("No search session")
            count_query = ("MATCH (n) OPTIONAL MATCH (n)-[r]->() "
                           "RETURN count(DISTINCT n) AS nodes, count(r) AS relationships")
            report["before"] = {"graph": session.runner.read_rows(count_query),
                                "points": session.client.count(session.collection, exact=True).count}
            cases = [c for c in CASES if c[0] in LLM_CASES] if args.llm else CASES
            if args.llm:
                assert len(cases) == 3
            try:
                for name, pid, supplied, question in cases:
                    captured.clear()
                    profile = {"age": None, "region": "", "income": None, **supplied}
                    row = {"scenario": name, "selected_policy_id": pid, "request_profile": profile,
                           "question": question}
                    body = {"question": question, "profile": profile, "history": []}
                    if pid:
                        body["selected_policy_id"] = pid
                    try:
                        response = client.post("/api/ask", json=body)
                        row.update(http=response.status_code, response=response.json(), **deepcopy(captured))
                        if response.status_code == 200:
                            row["comparison"], row["citation"], row["semantics"] = compare(row)
                    except Exception as exc:
                        row["error_type"] = type(exc).__name__
                    report["cases"].append(row)
                    print(json.dumps({"scenario": name, "http": row.get("http"),
                                      "error_type": row.get("error_type")}), flush=True)
            finally:
                report["after"] = {"graph": session.runner.read_rows(count_query),
                                   "points": session.client.count(session.collection, exact=True).count}
                report["counts_unchanged"] = report["before"] == report["after"]
    except Exception as exc:
        report["blocked_error_type"] = type(exc).__name__
    with args.out.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"cases": len(report["cases"]), "llm_calls": report["llm_calls"],
                      "blocked": report.get("blocked_error_type")}))
    return 2 if report.get("blocked_error_type") else 0


if __name__ == "__main__":
    raise SystemExit(main())
