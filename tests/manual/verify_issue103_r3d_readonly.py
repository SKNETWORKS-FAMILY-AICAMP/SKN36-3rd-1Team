"""#103 external PowerShell verifier. No .env, credential stores, DB writes or embeddings/indexing.

--preflight checks this process only. --llm makes at most 11 real OpenAI calls.
Reports are exclusive-create, secret-redacted, and always require a separate semantic review.
Query embeddings use the existing Ollama model; no document embeddings are generated.
"""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, quote_plus
from unittest.mock import patch
import warnings

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
REQUIRED = ("ISSUE105_R3D_VERIFY_QDRANT_API_KEY", "ISSUE105_R3D_VERIFY_NEO4J_PASSWORD")
SEJONG = "POL_YC_065"
KPASS = "POL_STG_MOLIT_001"
SPELLINGS = ("K-패스", "K패스", "k패스", "k-패스", "모두의 카드", "모두의카드")
FAQS = (
    (KPASS, "DOC_STG_MOLIT_001_FAQ_2026", "모두의 카드 한시 상향 혜택은 언제까지 적용돼?"),
    ("POL_STG_NTS_001", "DOC_STG_NTS_001_FAQ_2026", "2011년 이전에 중소기업에 취업했는데 중소기업 취업자 소득세 감면 받을 수 있어?"),
    ("POL_STG_SEOUL_001", "DOC_STG_SEOUL_001_FAQ_2026", "서울 청년수당 사업 공고는 어디서 확인할 수 있어?"),
)
LOANS = ("POL_003", "POL_004", "POL_006", "POL_007")
SECRET_NAMES = (*REQUIRED, "OPENAI_API_KEY", "QDRANT_API_KEY", "NEO4J_PASSWORD")


def redact(value, secrets):
    """Redact both raw and URL-encoded credentials, including dictionary keys."""
    if isinstance(value, dict):
        return {redact(str(key), secrets): redact(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in sorted(set(filter(None, secrets)), key=len, reverse=True):
            for variant in {secret, quote(secret, safe=""), quote_plus(secret)}:
                value = value.replace(variant, "[REDACTED]")
        return value
    if hasattr(value, "model_dump"):
        return redact(value.model_dump(), secrets)
    return value


class ReadOnlyQdrant:
    """Only SDK read/query methods required by the existing search/verifiers are exposed."""
    allowed = frozenset({"get_collections", "get_collection", "collection_exists", "get_aliases", "count", "scroll", "retrieve",
                         "search", "query_points"})

    def __init__(self, client):
        self.__client = client

    def __getattr__(self, name):
        if name not in self.allowed:
            raise PermissionError("Qdrant operation is not on the read-only allowlist")
        return getattr(self.__client, name)


def classify_error(error):
    """Do not serialize exception messages, requests, headers, tracebacks or reprs."""
    result = {"error_type": type(error).__name__,
              "backend": getattr(error, "backend", None), "kind": getattr(error, "kind", None)}
    status = getattr(error, "status_code", None)
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
        result["kind"] = {401: "auth", 403: "forbidden", 404: "not_found", 429: "rate_limit"}.get(status, "http_error")
    return result


def check_row(name, passed, **details):
    return {"check": name, "status": "PASS" if passed else "FAIL", **details}


def validate_trace(row, qclient, collection):
    """Compare adopted evidence against the very same DB point IDs, never release chunk IDs."""
    searches = row.get("search_results", [])
    hits = {str(hit["point_id"]): (policy["policy_id"], hit)
            for result in searches for policy in result.get("policies", []) for hit in policy.get("evidence", [])}
    contexts = row.get("generator_input", {}).get("contexts", [])
    requested = list(hits)
    payloads = {str(point.id): point.payload for point in qclient.retrieve(
        collection, ids=requested, with_payload=True, with_vectors=False)} if requested else {}
    problems = []
    for ctx in contexts:
        candidates = [(point_id, hit) for point_id, (pid, hit) in hits.items()
                      if pid == ctx["policy_id"] and hit["chunk_id"] == ctx["chunk_id"]]
        if len(candidates) != 1:
            problems.append("adopted_chunk_not_unique_in_search")
            continue
        point_id, hit = candidates[0]
        payload = payloads.get(point_id) or {}
        for key in ("policy_id", "document_id", "chunk_id"):
            if payload.get(key) != ctx.get(key):
                problems.append("adopted_db_payload_mismatch:" + key)
        if (payload.get("text") or "").strip() != ctx.get("text"):
            problems.append("adopted_db_payload_mismatch:text")
        if payload.get("source_url") != ctx.get("url"):
            problems.append("adopted_db_source_url_mismatch")
    response = row.get("fastapi_response", {})
    for citation in response.get("citations", []):
        if "chunk_id" in citation:
            problems.append("public_citation_contract_changed")
        if not any(c["document_id"] == citation["document_id"] and c["url"] == citation["url"] for c in contexts):
            problems.append("citation_not_in_adopted_context")
    numbers = {int(number) for number in re.findall(r"\[(\d+)\]", response.get("answer", ""))}
    if numbers != set(range(1, len(response.get("citations", [])) + 1)):
        problems.append("final_citation_number_mismatch")
    return check_row("same_B2_point_context_citation_trace", bool(contexts) and not problems, problems=problems)


def database_checks(report, qclient, runner):
    from database import full_index as fi, handoff_data as hd, hybrid_search as hs
    from database.neo4j import load_graph as lg
    from database.qdrant import store
    from scripts import restore_final_v140 as r2, restore_final_v140_r3 as r3, restore_profiles_v140 as profiles

    pins = profiles.require_finalized()
    raw = (hd.HANDOFF_DIR / "manifest.json").read_bytes()
    report["baseline"] = {"release_version": hd.dataset_version(), "manifest_raw_sha256": hashlib.sha256(raw).hexdigest(),
                          "manifest_lf_sha256": fi.manifest_sha256(), "r3_pins": pins,
                          "runbook": "docs/development/ISSUE105_R3_RUNBOOK.md sections 3, 5.10.14, 5.12"}
    checks = report["checks"]
    checks.append(check_row("manifest_matches_frozen_R3_pins", fi.manifest_sha256() == pins["manifest_lf_sha256"]))
    checksum_rows = hd.verify_manifest_checksums()
    checks.append(check_row("release_manifest_files", all(row["matched"] for row in checksum_rows),
                            mismatches=[row["file"] for row in checksum_rows if not row["matched"]]))
    documents = hd.load_documents(verify_hash=True)
    graph = lg.read_graph_input()
    graph_result = lg.verify_loaded_graph(graph, runner)
    report["neo4j"] = {key: graph_result[key] for key in ("ok", "totals", "after_counts", "count_mismatches")}
    checks.append(check_row("neo4j_full_graph_properties_and_links", graph_result["ok"]))
    metadata = hd.load_payload_metadata()
    hashes = r2.build_document_hashes(documents)
    report["qdrant"] = {}
    for key in profiles.COLLECTION_KEYS:
        spec = profiles.collection_spec(key)
        collection = spec["collection"]
        chunks, _ = fi.build_config_chunks(spec["config"], documents)
        records = r3.scan(qclient, collection)
        expected = {}
        for chunk in chunks:
            old = records.get(chunk["point_id"], {}).get("payload", {})
            run = {"embedding_model": spec["model"], "embedding_dim": spec["dimensions"],
                   "embedding_input_rule": fi.EMBEDDING_INPUT_RULE, "index_run_id": old.get("index_run_id")}
            expected[chunk["point_id"]] = store.build_point_payload(
                chunk, policy_meta=store.metadata_for_chunk(metadata, chunk),
                content_hash=hashes[chunk["document_id"]], run_info=run)
        vectors = r2.vector_digest(records)
        exact_payload = set(records) == set(expected) and all(records[pid]["payload"] == value for pid, value in expected.items())
        full = fi.verify_full_index(spec["config"], client=qclient, chunks=chunks, documents=documents, collection=collection)
        cross = hs.verify_cross_db(client=qclient, runner=runner, collection=collection)
        row = {"collection": collection, "actual_points": len(records), "expected_points": pins["collections"][key]["points"],
               "actual_policy_count": len({r["payload"].get("policy_id") for r in records.values()}),
               "actual_document_count": len({r["payload"].get("document_id") for r in records.values()}),
               "actual_vector_digest": vectors, "actual_payload_digest": r3.payload_digest(r2, records),
               "exact_release_payload_match": exact_payload, "full_index": full, "cross_db": cross}
        report["qdrant"][key] = row
        passed = (len(records) == row["expected_points"] and vectors == pins["collections"][key]["vector_digest"]
                  and exact_payload and full["passed"] and cross["status"] == "PASS")
        checks.append(check_row(key + "_frozen_snapshot_vector_payload_graph_baseline", passed))
    report["bundle_provenance"] = {"status": "BLOCKED", "reason": "No bundle file was supplied; DB content is independently compared with frozen R3 pins and reconstructed release payloads."}


def run_api_checks(report, qclient, runner, args):
    from fastapi.testclient import TestClient
    from backend.api.main import create_app
    from backend.llm import generator
    from database.hybrid_search import HybridSearchSession

    session = HybridSearchSession(client=qclient, runner=runner, profile="b2_06b_v140")
    original_search = session.search_policy_context
    current = {}

    def search(*a, **kw):
        result = original_search(*a, **kw)
        current.setdefault("search_results", []).append(deepcopy(result))
        return result

    def generate(question, contexts, checks, history, **kw):
        current["generator_input"] = deepcopy({"question": question, "contexts": contexts, "policy_checks": checks,
                                                 "history": history, **kw})
        result = generator.generate_answer(question, contexts, checks, history, **kw)
        current["generator_validated_output"] = deepcopy(result)
        return result

    original_call = generator._call_openai
    report["llm_settings"] = {"model": os.environ.get("LLM_MODEL") or generator.DEFAULT_MODEL,
                               "max_output_tokens": os.environ.get("LLM_MAX_OUTPUT_TOKENS") or generator.DEFAULT_MAX_OUTPUT_TOKENS,
                               "max_calls": args.max_llm_calls, "retries": 0,
                               "actual_cost": None, "cost_note": "No price estimate or usage-derived cost is asserted."}

    def call(messages):
        if report["actual_llm_calls"] >= args.max_llm_calls:
            raise generator.LLMUnavailableError("Verification call budget exhausted")
        report["actual_llm_calls"] += 1
        current["llm_messages"] = deepcopy(messages)
        raw = original_call(messages)  # Real SDK/network call; no stub or replay.
        current["llm_raw_response"] = raw
        return raw

    session.search_policy_context = search
    report["rag_search"] = []
    report["llm_cases"] = []
    try:
        for spelling in SPELLINGS:
            row = {"query": spelling + " 신청 방법 알려줘"}
            try:
                result = original_search(row["query"], k=10)
                ids = [p["policy_id"] for p in result["policies"]]
                row.update({"status": "PASS" if KPASS in ids else "FAIL", "policy_ids": ids, "search_result": result})
            except Exception as error:
                row.update({"status": "BLOCKED", **classify_error(error)})
            report["rag_search"].append(row)
        # Selected-policy queries isolate provenance, while the six searches above remain unfiltered.
        cases = [("sejong_method_homepage", SEJONG, "세종 청년위원회 신청 방법과 참고할 공식 홈페이지를 알려줘."),
                 ("sejong_link", SEJONG, "세종 청년위원회 신청 링크 알려줘."),
                 ("sejong_recruitment", SEJONG, "세종 청년위원회 지금 모집 중이야?")]
        cases += [("unknown_schedule", pid, "이 정책은 상시 신청 가능한가요? 현재 신청 기간을 알려줘.") for pid in LOANS]
        cases += [("faq", pid, question) for pid, _, question in FAQS]
        app = create_app(session_factory=lambda: session, generate_fn=generate)
        with TestClient(app) as client, patch.object(generator, "_call_openai", side_effect=call):
            catalogue = client.get("/api/policies").json()
            candidates = [p for p in catalogue["items"] if p.get("official_apply_url") and p["policy_id"] != SEJONG]
            preferred = next((p for p in candidates if p["policy_id"] == "POL_STG_SEOUL_001"), None)
            normal = preferred or (candidates[0] if candidates else None)
            if normal:
                cases.insert(3, ("normal_apply_url", normal["policy_id"], normal["policy_name"] + "와 세종시 위원회 청년참여 확대의 신청 방법과 공식 신청 주소를 각각 구분해서 알려줘."))
            report["catalogue"] = {"manifest_sha256": catalogue["manifest_sha256"], "total": catalogue["total"], "queries": []}
            for spelling in SPELLINGS:
                response = client.get("/api/policies", params={"q": spelling})
                ids = [p["policy_id"] for p in response.json().get("items", [])]
                report["catalogue"]["queries"].append(check_row(spelling, response.status_code == 200 and KPASS in ids,
                                                                               http=response.status_code, policy_ids=ids))
            for kind, pid, question in cases:
                current.clear()
                row = {"kind": kind, "selected_policy_id": pid, "question": question, "semantic_review": "BLOCKED"}
                try:
                    if not args.llm:
                        # Real search and same-B2 provenance remain available without LLM permission/key.
                        result = original_search(question, filters={"policy_id": pid}, k=10)
                        row.update({"status": "BLOCKED", "search_results": [result], "reason": "--llm was not enabled"})
                    else:
                        request = {"question": question, "profile": {"age": 27, "region": "", "income": None}, "history": []}
                        if kind != "normal_apply_url":
                            request["selected_policy_id"] = pid
                        row["required_policy_ids"] = [pid, SEJONG] if kind == "normal_apply_url" else [pid]
                        response = client.post("/api/ask", json=request)
                        row.update({**deepcopy(current), "http": response.status_code, "fastapi_response": response.json()})
                        error_code = row["fastapi_response"].get("detail", {}).get("code")
                        row["status"] = ("PASS" if response.status_code == 200 else "FAIL"
                                         if error_code in {"LLM_OUTPUT_INVALID", "ELIGIBILITY_OUTPUT_INVALID"} else "BLOCKED")
                        if response.status_code == 200:
                            body = row["fastapi_response"]
                            row["trace_check"] = validate_trace(row, qclient, session.collection)
                            actual_ids = {p["policy_id"] for p in body["policy_checks"]}
                            correct_ids = (set(row["required_policy_ids"]).issubset(actual_ids) if kind == "normal_apply_url" else actual_ids == {pid})
                            if row["trace_check"]["status"] != "PASS" or not correct_ids:
                                row["status"] = "FAIL"
                            check = next((p for p in body["policy_checks"] if p["policy_id"] == pid), {})
                            if pid == SEJONG and (check.get("official_apply_url") is not None or check.get("reception_status") != "unknown"):
                                row["status"] = "FAIL"
                            if pid in LOANS and check.get("reception_status") != "unknown":
                                row["status"] = "FAIL"
                            if kind == "normal_apply_url" and check.get("official_apply_url") != normal["official_apply_url"]:
                                row["status"] = "FAIL"
                            for item in body["policy_checks"]:
                                if item["policy_id"] == SEJONG and (item.get("official_apply_url") is not None or item.get("reception_status") != "unknown"):
                                    row["status"] = "FAIL"
                            if kind == "faq":
                                expected_doc = next(doc for p, doc, _ in FAQS if p == pid)
                                row["expected_document_id"] = expected_doc
                                if expected_doc not in {c["document_id"] for c in body["citations"]}:
                                    row["status"] = "FAIL"
                            raw = row.get("generator_validated_output", {}).get("answer")
                            row["pipeline_changed_answer"] = raw != body.get("answer")
                            row["pipeline_output"] = body
                    report["llm_cases"].append(row)
                except Exception as error:
                    report["llm_cases"].append({**row, **deepcopy(current), "status": "BLOCKED", **classify_error(error)})
    finally:
        session.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--preflight", action="store_true", help="No DB, Ollama or LLM calls")
    parser.add_argument("--connections-only", action="store_true", help="Authenticate DB connections only; no baseline scan, Ollama or LLM")
    parser.add_argument("--bundle-dir", type=Path, help="Optional existing extracted #105 bundle; never restored or modified")
    parser.add_argument("--llm", action="store_true", help="Enable real OpenAI calls (up to 11)")
    parser.add_argument("--max-llm-calls", type=int, default=11, choices=range(1, 12))
    args = parser.parse_args(argv)
    if args.connections_only and (args.llm or args.preflight):
        parser.error("--connections-only cannot be combined with --llm or --preflight")
    if args.out.exists():
        parser.error("Output already exists; choose a new filename")
    secrets = [os.environ.get(name, "") for name in SECRET_NAMES]
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("ignore")
    git_error = None
    try:
        git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        diff_hash = hashlib.sha256(subprocess.check_output(["git", "diff", "--binary"], cwd=ROOT, stderr=subprocess.DEVNULL)).hexdigest()
    except Exception as error:
        git_sha = diff_hash = None
        git_error = classify_error(error)
    report = {"schema": "issue103-r3d-readonly-v1", "executed_at_utc": datetime.now(timezone.utc).isoformat(),
              "mode": "PREFLIGHT" if args.preflight else "CONNECTIONS_ONLY" if args.connections_only else "REAL_R3D", "db_writes": 0, "document_embedding_calls": 0,
              "actual_llm_calls": 0, "max_llm_calls": args.max_llm_calls, "checks": [], "verdict": "BLOCKED",
              "semantic_review": "BLOCKED", "python": sys.executable,
              "credentials_present": {name: bool(os.environ.get(name)) for name in (*REQUIRED, "OPENAI_API_KEY")},
              "dependency_versions": {}, "source_git_sha": git_sha, "source_diff_sha256": diff_hash,
              "source_files_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                  "backend/rag/application_links.py", "backend/rag/pipeline.py", "backend/api/main.py",
                  "backend/api/schemas.py", "backend/api/policy_catalog.py", "backend/eligibility/checker.py",
                  "backend/llm/generator.py", "ai/prompts/system_prompt.py", "tests/manual/verify_issue103_r3d_readonly.py")}}
    modules = {"fastapi": "fastapi", "httpx": "httpx", "qdrant-client": "qdrant_client", "neo4j": "neo4j", "python-dotenv": "dotenv"}
    if args.llm:
        modules["openai"] = "openai"
    missing_modules = []
    for package, module in modules.items():
        if importlib.util.find_spec(module) is None:
            missing_modules.append(package)
        else:
            report["dependency_versions"][package] = importlib.metadata.version(package)
    missing_env = [name for name in REQUIRED if not os.environ.get(name)]
    if args.llm and not os.environ.get("OPENAI_API_KEY"):
        missing_env.append("OPENAI_API_KEY")
    report["preflight"] = {"status": "BLOCKED" if missing_env or missing_modules or git_error else "PASS",
                           "missing_env_names": missing_env, "missing_packages": missing_modules, "git_error": git_error}
    qclient = driver = None
    try:
        if not args.preflight and report["preflight"]["status"] == "PASS":
            # Child-process settings only; never read/write .env or persistent User/Machine variables.
            os.environ.update({"QDRANT_URL": "http://127.0.0.1:56756", "QDRANT_API_KEY": os.environ[REQUIRED[0]],
                               "NEO4J_URI": "bolt://127.0.0.1:56787", "NEO4J_USER": "neo4j",
                               "NEO4J_PASSWORD": os.environ[REQUIRED[1]],
                               "LIFEPOLICY_DATASET_DIR": str(ROOT / "data/releases/v1.4.0-candidate")})
            if os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") != "https://api.openai.com/v1":
                raise ValueError("Nonstandard OpenAI endpoint refused")
            from database import embedding
            from neo4j import GraphDatabase
            from qdrant_client import QdrantClient
            with patch.object(embedding, "load_env_file", return_value=False):
                from database.hybrid_search import ReadOnlyTimedRunner
                report["execution_stage"] = "qdrant_authentication"
                # The fixed loopback DB must not be routed through inherited HTTP proxy settings.
                qclient = QdrantClient(url="http://127.0.0.1:56756", api_key=os.environ[REQUIRED[0]], timeout=30, trust_env=False)
                readonly = ReadOnlyQdrant(qclient)
                readonly.get_collections()
                report["checks"].append(check_row("qdrant_authentication", True))
                report["execution_stage"] = "neo4j_authentication"
                driver = GraphDatabase.driver("bolt://127.0.0.1:56787", auth=("neo4j", os.environ[REQUIRED[1]]), connection_timeout=5)
                driver.verify_connectivity()
                report["checks"].append(check_row("neo4j_authentication", True))
                runner = ReadOnlyTimedRunner(driver)
                if not args.connections_only:
                    report["execution_stage"] = "database_baseline"
                    database_checks(report, readonly, runner)
                if args.bundle_dir and not args.connections_only:
                    from scripts import restore_final_v140_r3 as r3, restore_profiles_v140 as profiles

                    try:
                        package, _ = r3.check_bundle(args.bundle_dir, profiles.require_finalized())
                        source_check = r3.check_repo_matches_source(ROOT, args.bundle_dir, package)
                        matches = all(report["qdrant"][key]["actual_payload_digest"] == package["collections"][key]["payload_digest"]
                                      for key in profiles.COLLECTION_KEYS)
                        report["bundle_provenance"] = {"status": "PASS" if matches else "FAIL",
                                                       "source_commit": package["source_commit"], "source_check": source_check}
                    except Exception as error:
                        report["bundle_provenance"] = {"status": "BLOCKED", **classify_error(error)}
                # Do not incur LLM cost after a mismatched database baseline.
                if not args.connections_only and all(row["status"] == "PASS" for row in report["checks"]):
                    report["execution_stage"] = "rag_api_llm"
                    run_api_checks(report, readonly, runner, args)
                elif not args.connections_only:
                    report["api_execution"] = {"status": "BLOCKED", "reason": "Frozen DB baseline failed"}
    except Exception as error:
        report["execution_error"] = classify_error(error)
    finally:
        for resource in (driver, qclient):
            if resource is not None:
                try:
                    resource.close()
                except Exception as error:
                    report.setdefault("close_errors", []).append(classify_error(error))
    automated = [r["status"] for r in report["checks"] + report.get("rag_search", []) + report.get("llm_cases", [])]
    if report.get("bundle_provenance", {}).get("status") == "FAIL":
        automated.append("FAIL")
    report["successful_llm_responses"] = sum("llm_raw_response" in row for row in report.get("llm_cases", []))
    report["automated_status"] = ("FAIL" if "FAIL" in automated else "PASS"
                                  if automated and report.get("llm_cases") and all(s == "PASS" for s in automated)
                                  and not report.get("execution_error") and not report.get("close_errors") else "BLOCKED")
    if args.preflight:
        report["automated_status"] = report["preflight"]["status"]
    elif args.connections_only:
        report["automated_status"] = ("PASS" if len(report["checks"]) == 2 and all(s == "PASS" for s in automated)
                                      and not report.get("execution_error") and not report.get("close_errors") else "BLOCKED")
    report["verdict_reason"] = "Raw evidence requires independent semantic review; a zero-error HTTP run alone is not a final PASS."
    safe = redact(json.loads(json.dumps(report, default=str)), secrets)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(safe, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"out": str(args.out), "preflight": safe["preflight"], "actual_llm_calls": report["actual_llm_calls"],
                      "automated_status": report["automated_status"], "semantic_review": "BLOCKED"}, ensure_ascii=True))
    return 1 if report["automated_status"] == "FAIL" else 2 if report["automated_status"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
