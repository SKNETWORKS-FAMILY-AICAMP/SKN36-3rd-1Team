"""Read-only R3D stage recorder. Synthetic profiles; real LLM is explicit opt-in.

Run through run_issue122_readonly.ps1. Never loads or changes DB data.
Reports are exclusive-create; exceptions record type only to avoid secret leakage.
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

CASES = [
    ("income_failed_and", "POL_GOV_042", {"age": 45}),
    ("income_unknown", "POL_GOV_042", {"age": 10}),
    ("quality_hold", "POL_GOV_039", {"age": 30}),
    ("age80", "POL_005", {"age": 80}),
    ("child24", "POL_GOV_050", {"age": 30, "child_age_months": 24}),
    ("child25", "POL_GOV_050", {"age": 30, "child_age_months": 25}),
    ("child_missing", "POL_GOV_050", {"age": 30}),
    ("incheon_age", "POL_GOV_011", {"age": 30}),
    ("sejong_unknown_url", "POL_GOV_059", {"age": 30, "region": "세종"}),
    ("young_failed", "POL_GOV_007", {"age": 17}),
    ("and_or", "POL_GOV_016", {"age": 18}),
]


def statuses(check):
    return [(c.get("condition_id"), c["field"], c["status"]) for c in check.get("conditions", [])]


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
    report = {"mode": "real_llm" if args.llm else "mock_generator", "llm_calls": 0,
              "model": os.getenv("LLM_MODEL", "gpt-5.4-mini") if args.llm else None,
              "manifest_sha256": hashlib.sha256((ROOT / "data/releases/v1.4.0-candidate/manifest.json").read_bytes()).hexdigest(),
              "cases": []}
    captured = {}

    def checker(profile, policy):
        result = check_eligibility(profile, policy)
        if policy["policy_id"] == "POL_GOV_050":
            captured["direct_checker_month_boundaries"] = {
                str(month): check_eligibility({**profile, "child_age_months": month}, policy)
                for month in (24, 25, None)
            }
        captured.setdefault("checker", []).append(deepcopy(result))
        captured.setdefault("quality", []).append({"policy_id": policy["policy_id"], "data_quality": policy.get("data_quality"),
                                                   "eligibility_structure": (policy.get("graph") or {}).get("eligibility")})
        return result

    def generator(question, contexts, checks, history, profile=None):
        captured["generator_checks"] = deepcopy(checks)
        captured["contexts"] = [{k: c.get(k) for k in ("source_id", "policy_id", "document_id", "chunk_id")} for c in contexts]
        captured["generator_profile"] = profile
        if args.llm:
            report["llm_calls"] += 1
            result = _generate_answer(question, contexts, checks, history, profile=profile)
        else:
            sid = contexts[0]["source_id"]
            result = {"status": "answered", "answer": f"검증용 고정 응답 [{sid}]", "used_source_ids": [sid], "follow_up_questions": []}
        captured["generator_output"] = deepcopy(result)
        return result

    try:
        app = create_app(session_factory=lambda: HybridSearchSession(profile="b2_06b_v140"), check_fn=checker, generate_fn=generator)
        with TestClient(app) as client:
            session = app.state.session
            if session is None:
                raise RuntimeError("No search session")
            count_query = "MATCH (n) OPTIONAL MATCH (n)-[r]->() RETURN count(DISTINCT n) AS nodes, count(r) AS relationships"
            report["before"] = {"graph": session.runner.read_rows(count_query), "points": session.client.count(session.collection, exact=True).count}
            if args.llm:
                # Exactly the three external transmissions explicitly approved by the user.
                cases = [
                    ("income_failed_and", "POL_GOV_042", {"age": 45}),
                    ("age80", "POL_005", {"age": 80}),
                    ("sejong_unknown_url", "POL_GOV_059", {"age": 30, "region": "세종"}),
                ]
                assert len(cases) == 3
            else:
                cases = CASES
            try:
                for name, pid, supplied in cases:
                    captured.clear()
                    profile = {"age": None, "region": "", "income": None, **supplied}
                    row = {"scenario": name, "policy_id": pid, "request_profile": profile}
                    try:
                        response = client.post("/api/ask", json={"question": "이 정책의 자격 충족 여부와 신청 방법, 접수 상태를 알려줘", "profile": profile, "selected_policy_id": pid, "history": []})
                        row.update(http=response.status_code, response=response.json(), **deepcopy(captured))
                        api = row["response"].get("policy_checks", [])
                        raw = {c["policy_id"]: c for c in row.get("checker", [])}
                        gen = {c["policy_id"]: c for c in row.get("generator_checks", [])}
                        row["status_differences"] = [{"policy_id": c["policy_id"], "checker": statuses(raw[c["policy_id"]]), "pipeline": statuses(gen[c["policy_id"]]), "api": statuses(c)} for c in api if not statuses(raw[c["policy_id"]]) == statuses(gen[c["policy_id"]]) == statuses(c)]
                        docs = {c["document_id"] for c in row.get("contexts", []) if c["policy_id"] == pid}
                        row["citation_document_match"] = bool(api) and all(c["document_id"] in docs for c in row["response"].get("citations", []))
                        row["pipeline_api_exclusion_match"] = all(c.get("exclusion_reasons") == gen[c["policy_id"]].get("exclusion_reasons") for c in api)
                    except Exception as exc:
                        row["error_type"] = type(exc).__name__
                    report["cases"].append(row)
                    print(json.dumps({"scenario": name, "http": row.get("http"), "error_type": row.get("error_type")}), flush=True)
            finally:
                report["after"] = {"graph": session.runner.read_rows(count_query), "points": session.client.count(session.collection, exact=True).count}
                report["counts_unchanged"] = report["before"] == report["after"]
    except Exception as exc:
        report["blocked_error_type"] = type(exc).__name__
    with args.out.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"cases": len(report["cases"]), "llm_calls": report["llm_calls"], "blocked": report.get("blocked_error_type")}))
    return 2 if report.get("blocked_error_type") else 0


if __name__ == "__main__":
    raise SystemExit(main())
