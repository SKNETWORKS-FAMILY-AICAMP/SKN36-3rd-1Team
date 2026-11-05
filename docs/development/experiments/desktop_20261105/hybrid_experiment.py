"""
DB 쓰기 없음 (읽기 전용): Qdrant는 count·query만, Neo4j는 읽기 전용 세션만 쓴다. Ollama 질의 임베딩 호출 있음.

Issue #5 Hybrid 검색 실험: 실제 질문 검색 + P6 정책 범위 필터 비교.
보존 정보: 2026-11-05 데스크톱(RTX 5070, Qdrant 107 point, Neo4j 1,034/3,615)에서 실행. 결과는 hybrid_experiment.json.
실행 위치: 프로젝트 루트에서
    .venv\\Scripts\\python.exe docs/development/experiments/desktop_20261105/hybrid_experiment.py <결과 JSON 경로>
질문은 기존 실험(exp1/exp2)의 원문 근거 스모크 질문을 그대로 쓴다 (성능 평가 아님).
"""
import json
import logging
import sys
import time
import warnings

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
logging.getLogger("neo4j").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")
from database import hybrid_search as hs  # noqa: E402
from database.qdrant import store  # noqa: E402

OUT = sys.argv[1]
INSTRUCT = "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:{question}"
QUESTIONS = [  # (id, question, expected policy, evidence string) — exp1/exp2 원문 그대로
    ("Q1", "신혼부부 전세자금 대출의 금리는 어떻게 되나요?", "POL_003", "연 1.9% ∼ 연 3.3%"),
    ("Q2", "아이돌봄서비스 시간제 서비스는 몇 살 아동까지 이용할 수 있나요?", "POL_BOK_001", "생후 3개월~12세 이하의 아동"),
    ("Q3", "콘텐츠 기업이 인턴을 채용하면 1인당 인건비를 얼마 지원받나요?", "POL_YC_003", "1인당 월 108만원"),
    ("Q4", "수산자원보호 직접지불금을 부정하게 받으면 어떤 벌칙이 있나요?", "POL_GOV_006", "1년 이하의 징역 또는 1천만원 이하의 벌금"),
    ("P2", "매입임대 주택에서 최장 몇 년까지 거주할 수 있나요?", "POL_001", "최장 20년 거주"),
]
P6_QUESTIONS = [("P6_original", "신청 관련 상담 전화번호는 무엇인가요?"),          # exp2_lh001.py 원문 (기준)
                ("P6_prompt_variant", "상담 전화번호는 어디인가요?")]             # 이번 지시문의 표현 (참고)
P6_EVIDENCE_CHUNKS = ["CHK_B2_DOC_LH_001_C001", "CHK_B2_DOC_LH_001_C039"]
P6_EVIDENCE = "1600-1004"


def summarize_tree(tree):
    return {"group_id": tree.get("group_id"), "logic": tree.get("logic"),
            "conditions": [c.get("condition_id") for c in tree.get("conditions", [])],
            "groups": [summarize_tree(child) for child in tree.get("groups", [])]}


client = store.get_client()
report = {"collection": hs.DEFAULT_COLLECTION, "points_before": store.count_points(client, hs.DEFAULT_COLLECTION)}
with hs.open_neo4j_runner() as runner:
    report["graph_before"] = runner.read_rows(hs.lg.QUERY_TOTAL_COUNTS)[0]
    hs.search_policy_evidence("모델 준비 (sample_only)", k=1, client=client)   # 모델 로딩을 측정에서 분리

    report["questions"] = []
    for qid, question, expected_policy, evidence in QUESTIONS:
        result = hs.search_policy_context(question, k=5, client=client, runner=runner)
        policies = []
        for entry in result["policies"]:
            graph = entry["graph"] or {}
            eligibility = graph.get("eligibility", {})
            policies.append({
                "policy_id": entry["policy_id"], "policy_name": entry["policy_name"], "best_rank": entry["best_rank"],
                "best_score": round(entry["best_score"], 4), "graph_status": entry["graph_status"],
                "evidence": [{"rank": h["rank"], "score": round(h["score"], 4), "chunk_id": h["chunk_id"],
                              "page_start": h["page_start"], "section_title": h["section_title"],
                              "contains_evidence": evidence in h["text"],
                              "text_head": h["text"][:70].replace("\n", " ").replace("\r", " ")} for h in entry["evidence"]],
                "evidence_document_check": entry["evidence_document_check"],
                "graph": {"organizations": [o.get("name") for o in graph.get("organizations", [])],
                          "regions": [r.get("name") for r in graph.get("regions", [])],
                          "categories": [c.get("name") for c in graph.get("categories", [])],
                          "target_groups": [t.get("name") for t in graph.get("target_groups", [])],
                          "life_stages": [s.get("name") for s in graph.get("life_stages", [])],
                          "documents": [d.get("document_id") for d in graph.get("documents", [])],
                          "modifies_condition_of": [m["policy_id"] for m in graph.get("modifies_condition_of", [])],
                          "condition_modified_by": [m["policy_id"] for m in graph.get("condition_modified_by", [])],
                          "condition_tree": [summarize_tree(t) for t in eligibility.get("condition_tree", [])],
                          "unknown_logic_groups": eligibility.get("unknown_logic_groups"),
                          "tree_matches_requires_condition": eligibility.get("tree_matches_requires_condition"),
                          "structure_issues": eligibility.get("structure_issues")} if graph else None})
        report["questions"].append({"id": qid, "question": question, "expected_policy_id": expected_policy,
                                    "expected_policy_rank": next((i + 1 for i, p in enumerate(result["policies"])
                                                                  if p["policy_id"] == expected_policy), None),
                                    "evidence_chunk_ranks": [h["rank"] for p in result["policies"] for h in p["evidence"]
                                                             if evidence in h["text"]],
                                    "latency_seconds": result["latency_seconds"], "warnings": result["warnings"],
                                    "missing_policy_ids": result["missing_policy_ids"], "policies": policies})

    # P6 필터 실험: 필터 없음 / document_id / policy_id × 지시문 없음·있음. 전체 순위는 k를 넉넉히(107) 잡아 확인
    report["p6"] = []
    filter_cases = [("none", None), ("document_id", {"document_id": "DOC_LH_001"}), ("policy_id", {"policy_id": "POL_001"})]
    for pid, question in P6_QUESTIONS:
        for template_name, template in (("A_none", None), ("B_instruct", INSTRUCT)):
            for case, filters in filter_cases:
                started = time.perf_counter()
                full = hs.search_policy_evidence(question, filters, 107, client=client, query_template=template)
                wall = time.perf_counter() - started
                hits = full["hits"]
                ranks = {h["chunk_id"]: (h["rank"], round(h["score"], 4)) for h in hits}
                report["p6"].append({
                    "id": pid, "question": question, "template": template_name, "filter": case, "filters": filters,
                    "candidates": len(hits),
                    "evidence_chunk_ranks": {cid: ranks.get(cid) for cid in P6_EVIDENCE_CHUNKS},
                    "first_rank_containing_evidence": next((h["rank"] for h in hits if P6_EVIDENCE in h["text"]), None),
                    "lh_in_top5": any(h["document_id"] == "DOC_LH_001" for h in hits[:5]),
                    "top5": [(h["rank"], round(h["score"], 4), h["chunk_id"], h["page_start"]) for h in hits[:5]],
                    "latency_seconds": {**full["latency_seconds"], "wall": round(wall, 4)}})
    report["graph_after"] = runner.read_rows(hs.lg.QUERY_TOTAL_COUNTS)[0]
    report["neo4j_notifications"] = hs.lg.classify_notifications(runner.notifications)
report["points_after"] = store.count_points(client, hs.DEFAULT_COLLECTION)
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
print("done")
