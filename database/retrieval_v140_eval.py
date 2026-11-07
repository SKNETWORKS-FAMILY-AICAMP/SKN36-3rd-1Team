"""승인 v1.4.0 B2/B3 읽기 전용 비교. 기존 미검수 정답을 공식 gold로 승격하지 않는다.

실행: LIFEPOLICY_DATASET_DIR 지정 후 python -m database.retrieval_v140_eval --output <새 경로>
질의 임베딩은 한 번 생성해 공유한다. 검색 시간은 임베딩을 제외한 wall time이다.
"""

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import time
from collections import Counter
from pathlib import Path

from database import embedding as em
from database import full_index as fi
from database import handoff_data as hd
from database import hybrid_search as hs
from database import retrieval_eval as rv
from database.qdrant import store

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "docs/development/retrieval_eval"
PROFILES = ("b2_06b_v140", "b3_06b_v140")
METHODS = tuple((profile, mode) for profile in PROFILES for mode in ("vector", "hybrid"))


def lf_hash(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def latency_summary(values):
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0, "mean_ms": None, "p95_ms": None}
    return {"samples": len(values), "mean_ms": statistics.mean(values) * 1000,
            "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1] * 1000}


def reference_diagnostic(targets, hits, k):
    """미검수 선언 집합에 대한 진단. 실제 관련 정책 전체의 Recall로 해석하지 않는다."""
    targets = set(targets)
    if not targets:
        return None
    found = targets & {hit["policy_id"] for hit in hits[:k]}
    first = next((i for i, hit in enumerate(hits[:k], 1) if hit["policy_id"] in targets), None)
    return {"any_declared_target": bool(found), "declared_target_coverage": len(found) / len(targets),
            "first_declared_target_rank": first, "reciprocal_rank_diagnostic": 1 / first if first else 0}


def flatten_hits(result):
    if "hits" in result:
        return result["hits"]
    hits = [hit for policy in result["policies"] for hit in policy["evidence"]]
    return sorted(hits + result.get("unlinked_hits", []), key=lambda hit: hit["rank"])


def load_cases(documents):
    """구 v0 freeze는 그대로 검사하고, 새 릴리스로의 이전은 별도 근거 대조로 기록한다."""
    freeze = json.loads((EVAL / "retrieval_eval_v0.freeze.json").read_text(encoding="utf-8"))
    for name, expected in freeze["files"].items():
        if lf_hash(EVAL / name) != expected:
            raise ValueError(f"고정 질문 파일 해시 불일치: {name}")
    by_document = {doc["document_id"]: doc for doc in documents}
    policy_ids = {doc["policy_id"] for doc in documents}
    v0 = rv.load_jsonl(EVAL / "questions_candidates_v0.jsonl")
    span_checks = []
    cases = []
    for question in v0:
        for evidence in question["gold"]["required"]:
            for alt in evidence["alternatives"]:
                doc = by_document.get(alt["document_id"])
                valid = doc is not None and doc["text"][alt["char_start"]:alt["char_end"]] == evidence["evidence_text"]
                span_checks.append({"question_id": question["question_id"], **alt, "exact_match": valid})
        cases.append({"id": question["question_id"], "query": question["question"], "source": "v0_candidate",
                      "targets": question["policy_ids"] if question["answerable"] else [], "v0": question})
    if not all(check["exact_match"] for check in span_checks):
        raise ValueError("v0 근거 구간이 승인 릴리스와 불일치: 이전 평가 중단")
    benchmark_path = ROOT / "data/staging/evaluation/retrieval_benchmark_30.json"
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    audit = []
    for row in benchmark:
        missing_policies = sorted(set(row["target_policies"]) - policy_ids)
        missing_documents = sorted(set(row["target_documents"]) - set(by_document))
        audit.append({"id": row["query_id"], "missing_policy_ids": missing_policies,
                      "missing_document_ids": missing_documents,
                      "document_reference_usable": bool(row["target_documents"]) and not missing_documents})
        cases.append({"id": row["query_id"], "query": row["query"], "source": "staging_unreviewed",
                      "targets": row["target_policies"] if not missing_policies else [],
                      "declared_targets": row["target_policies"]})
    return cases, {"confirmed_human_reviewed_questions": 0, "v0_freeze": freeze,
                   "v0_span_checks": span_checks, "benchmark_reference_audit": audit,
                   "benchmark_lf_sha256": lf_hash(benchmark_path)}


def region_cases(cases):
    by_id = {case["id"]: case for case in cases}
    selected = [("Q02", "서울특별시"), ("Q07", "서울특별시"), ("Q04", "인천광역시"),
                ("RQ033", "충청남도"), ("RQ045", "전북특별자치도"), ("RQ035", "서울특별시")]
    probes = [{**by_id[key], "id": f"region-{key}", "filters": {"region": region}}
              for key, region in selected]
    for region in ("전라남도", "광주광역시"):
        probes.append({"id": f"region-{region}", "query": "전남 광주 평생교육이용권 신청 조건과 지원 금액을 알려주세요.",
                       "source": "exploratory_new", "targets": ["POL_GOV_061"], "filters": {"region": region}})
    return probes


def summarize(records):
    summary = {}
    for profile, mode in METHODS:
        rows = [r for r in records if r["profile"] == profile and r["mode"] == mode and not r.get("filters")]
        errors = [r for r in rows if "error" in r]
        good = [r for r in rows if "error" not in r]
        metrics = {"questions": len(rows), "errors": len(errors), "search_latency": latency_summary([r["seconds"] for r in good]),
                   "formal_hit_at_5": None, "formal_hit_at_10": None, "formal_recall": None, "formal_mrr": None,
                   "reason": "No confirmed human-reviewed gold; below are exploratory declared-reference diagnostics."}
        for k in (5, 10):
            diagnostics = [r[f"reference_{k}"] for r in good if r[f"reference_{k}"] is not None]
            metrics[f"exploratory_at_{k}"] = {
                "declared_reference_questions": len(diagnostics),
                "questions_with_declared_target": sum(d["any_declared_target"] for d in diagnostics),
                "mean_declared_set_coverage": statistics.mean(d["declared_target_coverage"] for d in diagnostics) if diagnostics else None,
                "mean_reciprocal_declared_rank": statistics.mean(d["reciprocal_rank_diagnostic"] for d in diagnostics) if diagnostics else None,
                "mean_unique_policies": statistics.mean(len({h["policy_id"] for h in r["hits"][:k]}) for r in good) if good else None,
                "mean_unique_documents": statistics.mean(len({h["document_id"] for h in r["hits"][:k]}) for r in good) if good else None,
                "machine_span_outcomes": dict(Counter(r[f"span_{k}"]["outcome"] for r in good if r.get(f"span_{k}") is not None))}
        summary[f"{profile}:{mode}"] = metrics
    return summary


def run(output):
    if hd.dataset_version() != "1.4.0-candidate":
        raise ValueError("승인 v1.4.0 데이터셋을 import 전에 지정해야 합니다")
    if output.exists():
        raise ValueError("기존 평가 경로를 덮어쓰지 않습니다")
    documents = hd.load_documents()
    cases, audit = load_cases(documents)
    audit["machine_span_attainability"] = {
        profile: rv.attainability([case["v0"] for case in cases if "v0" in case],
                                  fi.build_config_chunks(config, documents)[0])
        for profile, config in ((PROFILES[0], "A_B2_V140"), (PROFILES[1], "B_B3_V140"))}
    probes = region_cases(cases)
    output.mkdir(parents=True)
    em.load_env_file()
    from neo4j import GraphDatabase

    client = store.get_client()
    driver = GraphDatabase.driver("bolt://127.0.0.1:17687",
                                 auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
                                 connection_timeout=5, connection_acquisition_timeout=10)
    runner = hs.ReadOnlyTimedRunner(driver)
    real_embedder = em.make_ollama_embedder(model="qwen3-embedding:0.6b", expected_dim=1024, batch_size=1)
    vectors, embedding_seconds = {}, []

    def cached_embedder(texts):
        return [vectors[text] for text in texts]

    cached_embedder.embedding_model = "qwen3-embedding:0.6b"
    cached_embedder.embedding_dim = 1024
    records = []
    sessions = {p: hs.HybridSearchSession(client=client, runner=runner, embedder=cached_embedder, profile=p) for p in PROFILES}
    try:
        # 모든 질의에 같은 모델·무지시문 벡터. 지연은 독립 기록하며 search-only에 섞지 않는다.
        queries = list(dict.fromkeys(c["query"] for c in cases + probes))
        for i, query in enumerate(queries):
            started = time.perf_counter()
            vectors[query] = real_embedder([query])[0]
            embedding_seconds.append(time.perf_counter() - started)
            if (i + 1) % 10 == 0:
                print(f"Embedded {i + 1}/{len(queries)}", flush=True)
        # 연결·캐시 cold start는 질문별 측정에서 제외. 실제 모델 cold start는 임베딩 시간에 남는다.
        for profile, mode in METHODS:
            function = sessions[profile].search_policy_evidence if mode == "vector" else sessions[profile].search_policy_context
            function(cases[0]["query"], k=10)
        with (output / "queries.jsonl").open("w", encoding="utf-8", newline="\n") as log:
            for index, case in enumerate(cases + probes):
                order = METHODS[index % 4:] + METHODS[:index % 4]
                for profile, mode in order:
                    row = {key: case[key] for key in ("id", "query", "source", "targets")}
                    row.update(profile=profile, mode=mode, filters=case.get("filters"))
                    started = time.perf_counter()
                    try:
                        function = sessions[profile].search_policy_evidence if mode == "vector" else sessions[profile].search_policy_context
                        result = function(case["query"], filters=case.get("filters"), k=10)
                        row["seconds"] = time.perf_counter() - started
                        hits = flatten_hits(result)
                        keys = ("rank", "chunk_id", "document_id", "policy_id", "score", "char_start", "char_end", "region", "region_match", "source_url")
                        row["hits"] = [{key: hit.get(key) for key in keys} for hit in hits]
                        row["region_search"] = result.get("region_search")
                        row["warnings"] = result.get("warnings")
                        if mode == "hybrid":
                            row["missing_policy_ids"] = result["missing_policy_ids"]
                            row["data_quality_summary"] = result["data_quality_summary"]
                            row["eligibility_evaluated"] = [p["graph"]["eligibility"]["evaluated"] for p in result["policies"] if p.get("graph")]
                        for k in (5, 10):
                            row[f"reference_{k}"] = reference_diagnostic(case["targets"], hits, k)
                            row[f"span_{k}"] = rv.score_question(case["v0"], hits, k=k) if case.get("v0") and case["targets"] else None
                        if case.get("filters") and mode == "vector":
                            exact = store.search_chunks(client, sessions[profile].collection, vectors[case["query"]], k=10, filters=case["filters"])
                            row["legacy_exact_policy_ids"] = [h["policy_id"] for h in exact]
                    except Exception as error:
                        row["error"] = {"type": type(error).__name__, "message": hs._safe_message(error)}
                    records.append(row)
                    log.write(json.dumps(row, ensure_ascii=False) + "\n")
                    log.flush()
                if (index + 1) % 10 == 0:
                    print(f"Searched {index + 1}/{len(cases + probes)} x 4", flush=True)
    finally:
        for session in sessions.values():
            session.close()
        client.close()
        driver.close()
    paired = []
    for case in cases + probes:
        for profile in PROFILES:
            pair = [r for r in records if r["id"] == case["id"] and r["profile"] == profile]
            if len(pair) == 2 and all("error" not in r for r in pair):
                paired.append({"id": case["id"], "profile": profile,
                               "same_chunk_ranking": [h["chunk_id"] for h in pair[0]["hits"]] == [h["chunk_id"] for h in pair[1]["hits"]]})
    report = {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "generator_lf_sha256": lf_hash(Path(__file__)),
              "dataset": str(hd.HANDOFF_DIR.relative_to(ROOT)), "manifest_lf_sha256": lf_hash(hd.HANDOFF_DIR / "manifest.json"),
              "model": "qwen3-embedding:0.6b", "dimensions": 1024, "top_k": 10,
              "question_count": len(cases), "region_probe_count": len(probes), "audit": audit,
              "timing": "Warm connections, one measured call per question/method; rotated method order; excludes cached shared query embedding. p95 nearest rank.",
              "embedding": latency_summary(embedding_seconds), "methods": summarize(records), "vector_hybrid_pairs": paired,
              "error_count": sum("error" in r for r in records),
              "recommendation": "b2_06b_v140 provisional; no human-reviewed gold available; not a claim of superiority"}
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "errors": report["error_count"], "queries": len(cases), "region_probes": len(probes)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
