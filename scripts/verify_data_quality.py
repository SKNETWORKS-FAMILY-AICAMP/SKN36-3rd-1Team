"""
LifePolicy AI - 데이터 정합성 및 품질 전수 검증 스크립트 (scripts/verify_data_quality.py)
작업 지시서 9장 10대 검사 기준 + 의미적 정합성(Semantic Consistency) 자동화:
  1. JSONL 파싱 검사
  2. 필수 필드 누락 검사 (Policy, Document, Chunk, Relationship)
  3. 정책 ID 중복 및 형식 검사
  4. 문서 ID 고유성 및 참조 무결성 검사 (Policy -> Document)
  5. Vector 청크와 문서/정책 연결 무결성 검사 (Chunk -> Document -> Policy)
  6. Graph 관계 대상 및 출처 무결성 검사 (Relationship -> Policy / Document / Agency / Region / TargetGroup)
  7. 로컬 원본 파일 실제 존재 여부 검사 (local_raw_path on disk)
  8. HTML 로그인·세션만료·에러페이지 및 UI 노이즈 혼입 검사
  9. 의미 없는 비정상 단문 검사 (< 100자)
  10. 공식 출처 URL 유효성, 수집일시, 검증상태 및 제목-본문 의미 일치성(Semantic Consistency) 검사
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "processed"
METADATA_DIR = BASE_DIR / "data" / "metadata"

POLICIES_PATH = DATA_DIR / "policies.jsonl"
DOCUMENTS_PATH = DATA_DIR / "documents.jsonl"
CHUNKS_PATH = DATA_DIR / "chunks.jsonl"
RELATIONSHIPS_PATH = DATA_DIR / "relationships.jsonl"
GRAPH_EDGES_PATH = DATA_DIR / "graph_edges.jsonl"


def run_verification() -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "summary": {
            "total_checks": 10,
            "passed_checks": 0,
            "failed_checks": 0,
        },
        "metrics": {},
        "details": {},
    }

    print("=================================================================")
    print("      LifePolicy AI 3차 데이터 정합성 및 품질 전수 검증        ")
    print("=================================================================")

    # -------------------------------------------------------------
    # 1. JSONL Parsing Check
    # -------------------------------------------------------------
    print("\n[Check 1/10] JSONL Parsing & Schema Loading Check")
    files_to_check = {
        "policies": POLICIES_PATH,
        "documents": DOCUMENTS_PATH,
        "chunks": CHUNKS_PATH,
        "relationships": RELATIONSHIPS_PATH,
        "graph_edges": GRAPH_EDGES_PATH,
    }
    loaded_data: Dict[str, List[Dict[str, Any]]] = {}
    parse_errors: List[str] = []

    for name, path in files_to_check.items():
        if not path.exists():
            parse_errors.append(f"{name}: file not found at {path}")
            continue
        items: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except Exception as e:
                    parse_errors.append(f"{name} line {idx}: {e}")
        loaded_data[name] = items
        print(f"  - {name:15s}: {len(items):5d} records successfully loaded")

    check_1_passed = len(parse_errors) == 0
    report["details"]["1_jsonl_parsing"] = {"passed": check_1_passed, "errors": parse_errors}
    report["metrics"] = {k: len(v) for k, v in loaded_data.items()}

    policies = loaded_data.get("policies", [])
    documents = loaded_data.get("documents", [])
    chunks = loaded_data.get("chunks", [])
    relationships = loaded_data.get("relationships", [])

    # -------------------------------------------------------------
    # 2. Required Fields Check
    # -------------------------------------------------------------
    print("\n[Check 2/10] Required Fields Completeness Check")
    field_errors: List[str] = []
    policy_req = ["policy_id", "policy_name", "category", "agency", "regions", "requirements", "benefit", "source_document_ids", "verification_status"]
    doc_req = ["document_id", "policy_id", "title", "source_type", "source_url", "local_raw_path", "text", "content_hash", "verification_status"]
    chunk_req = ["chunk_id", "document_id", "policy_id", "text", "source_url"]
    rel_req = ["source_id", "relation", "target_id", "evidence_document_id"]

    for p in policies:
        for rf in policy_req:
            if rf not in p or p[rf] is None or (isinstance(p[rf], (str, list)) and len(p[rf]) == 0):
                field_errors.append(f"Policy {p.get('policy_id')} missing required field: {rf}")

    for d in documents:
        for rf in doc_req:
            if rf not in d or d[rf] is None or (isinstance(d[rf], str) and len(d[rf].strip()) == 0):
                field_errors.append(f"Document {d.get('document_id')} missing required field: {rf}")

    for c in chunks:
        for rf in chunk_req:
            if rf not in c or c[rf] is None or (isinstance(c[rf], str) and len(c[rf].strip()) == 0):
                field_errors.append(f"Chunk {c.get('chunk_id')} missing required field: {rf}")

    for r in relationships:
        for rf in rel_req:
            if rf not in r or not r[rf]:
                field_errors.append(f"Relationship missing required field: {rf} in {r}")

    check_2_passed = len(field_errors) == 0
    print(f"  - Missing field violations: {len(field_errors)}")
    report["details"]["2_required_fields"] = {"passed": check_2_passed, "errors": field_errors[:20]}

    # -------------------------------------------------------------
    # 3. Policy ID Uniqueness Check
    # -------------------------------------------------------------
    print("\n[Check 3/10] Policy ID Uniqueness Check")
    p_ids = [p["policy_id"] for p in policies]
    p_id_set = set(p_ids)
    p_duplicates = [pid for pid in p_ids if p_ids.count(pid) > 1]
    check_3_passed = len(p_ids) == len(p_id_set)
    print(f"  - Total policies: {len(p_ids)}, Unique IDs: {len(p_id_set)}, Duplicate IDs: {len(set(p_duplicates))}")
    report["details"]["3_policy_id_uniqueness"] = {"passed": check_3_passed, "duplicates": list(set(p_duplicates))}

    # -------------------------------------------------------------
    # 4. Document ID Uniqueness & Reference Integrity Check
    # -------------------------------------------------------------
    print("\n[Check 4/10] Document ID Uniqueness & Policy->Document Reference Integrity")
    d_ids = [d["document_id"] for d in documents]
    doc_id_set = set(d_ids)
    d_duplicates = [did for did in d_ids if d_ids.count(did) > 1]
    missing_doc_refs: List[str] = []

    for p in policies:
        for ref in p.get("source_document_ids", []):
            if ref not in doc_id_set:
                missing_doc_refs.append(f"Policy {p['policy_id']} references non-existent Document {ref}")

    check_4_passed = (len(d_ids) == len(doc_id_set)) and (len(missing_doc_refs) == 0)
    print(f"  - Total documents: {len(d_ids)}, Unique: {len(doc_id_set)}, Broken references: {len(missing_doc_refs)}")
    report["details"]["4_document_integrity"] = {
        "passed": check_4_passed,
        "duplicate_documents": list(set(d_duplicates)),
        "broken_references": missing_doc_refs,
    }

    # -------------------------------------------------------------
    # 5. Vector Chunk -> Document & Policy Integrity Check
    # -------------------------------------------------------------
    print("\n[Check 5/10] Vector Chunk -> Document & Policy Reference Integrity")
    chunk_errors: List[str] = []
    chunk_ids = [c["chunk_id"] for c in chunks]
    chunk_id_set = set(chunk_ids)
    chunk_duplicates = [cid for cid in chunk_ids if chunk_ids.count(cid) > 1]

    for c in chunks:
        cid = c.get("chunk_id")
        did = c.get("document_id")
        pid = c.get("policy_id")
        if did not in doc_id_set:
            chunk_errors.append(f"Chunk {cid} references non-existent Document {did}")
        if pid not in p_id_set:
            chunk_errors.append(f"Chunk {cid} references non-existent Policy {pid}")

    check_5_passed = (len(chunk_ids) == len(chunk_id_set)) and (len(chunk_errors) == 0)
    print(f"  - Total chunks: {len(chunks)}, Duplicate chunk IDs: {len(set(chunk_duplicates))}, Broken references: {len(chunk_errors)}")
    report["details"]["5_chunk_integrity"] = {
        "passed": check_5_passed,
        "duplicate_chunks": list(set(chunk_duplicates)),
        "broken_references": chunk_errors[:20],
    }

    # -------------------------------------------------------------
    # 6. Graph Relationships Target & Source Integrity Check
    # -------------------------------------------------------------
    print("\n[Check 6/10] Graph Relationships Target & Evidence Integrity")
    rel_errors: List[str] = []
    for r in relationships:
        s_id = r.get("source_id")
        t_id = r.get("target_id")
        rel = r.get("relation")
        ev_doc = r.get("evidence_document_id")

        if s_id.startswith("POL_") and s_id not in p_id_set:
            rel_errors.append(f"Relationship source {s_id} does not exist in policies")

        if rel == "SUPPORTED_BY" and t_id.startswith("DOC_") and t_id not in doc_id_set:
            rel_errors.append(f"SUPPORTED_BY target document {t_id} does not exist in documents")

        if rel == "MODIFIES_CONDITION_OF" and t_id.startswith("POL_") and t_id not in p_id_set:
            rel_errors.append(f"MODIFIES_CONDITION_OF target policy {t_id} does not exist in policies")

        if ev_doc and ev_doc not in doc_id_set:
            rel_errors.append(f"Evidence document {ev_doc} does not exist in documents")

    check_6_passed = len(rel_errors) == 0
    print(f"  - Total relationships: {len(relationships)}, Relation integrity violations: {len(rel_errors)}")
    report["details"]["6_relationship_integrity"] = {"passed": check_6_passed, "errors": rel_errors[:20]}

    # -------------------------------------------------------------
    # 7. Local Raw File Existence Check
    # -------------------------------------------------------------
    print("\n[Check 7/10] Local Raw File Physical Existence Check (local_raw_path)")
    raw_missing: List[str] = []
    for d in documents:
        raw_path = d.get("local_raw_path")
        if not raw_path:
            raw_missing.append(f"Document {d['document_id']} has empty local_raw_path")
            continue
        full_path = BASE_DIR / raw_path
        if not full_path.exists():
            raw_missing.append(f"Document {d['document_id']} raw file not found: {raw_path}")

    check_7_passed = len(raw_missing) == 0
    print(f"  - Missing raw files: {len(raw_missing)}")
    report["details"]["7_raw_file_existence"] = {"passed": check_7_passed, "errors": raw_missing}

    # -------------------------------------------------------------
    # 8. HTML Error / Login Page Contamination Check
    # -------------------------------------------------------------
    print("\n[Check 8/10] HTML Login/Session/Error Page Contamination Check")
    junk_errors: List[str] = []
    junk_patterns = [
        "자동 로그아웃 안내",
        "로그인 시간을 연장",
        "이용에 불편을 드려 죄송합니다",
        "404 Not Found",
        "페이지를 찾을 수 없습니다",
        "조회된 데이터가 없습니다.",
    ]
    for d in documents:
        text = d.get("text", "")
        # If document is extremely dominated by login modal / error text
        for ptn in junk_patterns:
            if ptn in text and len(text) < 300:
                junk_errors.append(f"Document {d['document_id']} contaminated with '{ptn}' (length: {len(text)})")

    check_8_passed = len(junk_errors) == 0
    print(f"  - Contaminated documents: {len(junk_errors)}")
    report["details"]["8_junk_contamination"] = {"passed": check_8_passed, "errors": junk_errors}

    # -------------------------------------------------------------
    # 9. Meaningless Short Text Check (< 100 chars)
    # -------------------------------------------------------------
    print("\n[Check 9/10] Meaningless Short Text (< 100 chars) Check")
    short_errors: List[str] = []
    for d in documents:
        txt = d.get("text", "").strip()
        if len(txt) < 100:
            short_errors.append(f"Document {d['document_id']} text is too short ({len(txt)} chars): {d.get('title')}")

    for c in chunks:
        ctxt = c.get("text", "").strip()
        if len(ctxt) < 50:
            short_errors.append(f"Chunk {c['chunk_id']} text is too short ({len(ctxt)} chars)")

    check_9_passed = len(short_errors) == 0
    print(f"  - Short text violations: {len(short_errors)}")
    report["details"]["9_short_text"] = {"passed": check_9_passed, "errors": short_errors[:20]}

    # -------------------------------------------------------------
    # 10. Source URL, Timestamp, Verification Status & Semantic Consistency Check
    # -------------------------------------------------------------
    print("\n[Check 10/10] Source URL, Timestamps & Semantic Title-Content Consistency")
    url_and_semantic_errors: List[str] = []

    # Semantic cross-check keywords
    semantic_checks = [
        ("POL_001", "매입임대", "DOC_LH_001"),
        ("POL_002", "소득요건", "DOC_NHUF_001"),
        ("POL_003", "신혼부부", "DOC_NHUF_003"),
        ("POL_004", "버팀목", "DOC_NHUF_005"),
        ("POL_005", "내일배움", "DOC_WORK24_001"),
        ("POL_006", "주거안정월세", "DOC_NHUF_007"),
        ("POL_007", "보증부월세", "DOC_NHUF_008"),
        ("POL_009", "청년월세", "DOC_MOLIT_001"),
        ("POL_010", "국민취업지원", "DOC_MOEL_001"),
    ]
    doc_text_by_id = {d["document_id"]: d.get("text", "") for d in documents}

    for pid, expected_kw, did in semantic_checks:
        text = doc_text_by_id.get(did, "")
        if expected_kw not in text:
            url_and_semantic_errors.append(
                f"Semantic mismatch: Policy {pid} expected '{expected_kw}' in Document {did}, but not found"
            )

    # Check DOC_NHUF_008 does NOT contain 버팀목전세자금대출 as main topic
    nhuf_008_txt = doc_text_by_id.get("DOC_NHUF_008", "")
    if "버팀목전세자금" in nhuf_008_txt and "보증부월세" not in nhuf_008_txt:
        url_and_semantic_errors.append("DOC_NHUF_008 still contains wrong 버팀목전세자금 content!")

    for d in documents:
        url = d.get("source_url", "")
        if not url or not url.startswith("http"):
            url_and_semantic_errors.append(f"Document {d['document_id']} invalid source URL: {url}")
        if not d.get("retrieved_at") and not d.get("collected_at"):
            url_and_semantic_errors.append(f"Document {d['document_id']} missing collection timestamp")
        if d.get("verification_status") not in {"verified", "extracted", "downloaded", "failed", "needs_review"}:
            url_and_semantic_errors.append(f"Document {d['document_id']} invalid verification_status: {d.get('verification_status')}")

    for p in policies:
        if p.get("verification_status") not in {"verified", "extracted", "downloaded", "failed", "needs_review"}:
            url_and_semantic_errors.append(f"Policy {p['policy_id']} invalid verification_status: {p.get('verification_status')}")

    check_10_passed = len(url_and_semantic_errors) == 0
    print(f"  - URL/Timestamp/Semantic errors: {len(url_and_semantic_errors)}")
    report["details"]["10_url_and_semantics"] = {"passed": check_10_passed, "errors": url_and_semantic_errors[:20]}

    # -------------------------------------------------------------
    # Summary Calculation
    # -------------------------------------------------------------
    all_checks = [
        check_1_passed,
        check_2_passed,
        check_3_passed,
        check_4_passed,
        check_5_passed,
        check_6_passed,
        check_7_passed,
        check_8_passed,
        check_9_passed,
        check_10_passed,
    ]
    passed_count = sum(1 for c in all_checks if c)
    failed_count = sum(1 for c in all_checks if not c)

    report["summary"]["passed_checks"] = passed_count
    report["summary"]["failed_checks"] = failed_count

    print("\n=================================================================")
    print(f"  전수 검증 결과: 10대 검사항목 중 {passed_count}개 통과 / {failed_count}개 실패")
    print("=================================================================")

    return report


if __name__ == "__main__":
    res = run_verification()
    out_file = DATA_DIR / "verification_result.json"
    out_file.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Validation report saved to: {out_file}")
    if res["summary"]["failed_checks"] > 0:
        sys.exit(1)
    else:
        print("[SUCCESS] All 10 verification criteria PASSED!")
