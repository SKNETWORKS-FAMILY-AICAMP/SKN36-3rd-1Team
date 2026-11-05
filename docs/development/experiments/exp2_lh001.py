"""
2차 실험: DOC_LH_001 73청크 추가 적재 + PDF 검색 (2026-11-05 노트북 실행 스크립트 보존본, 프로젝트 함수만 호출).

⚠ DB 쓰기 있음 — 사용자 승인 후에만 실행
- 전제: exp_b2_qwen3emb06b_v1 이 1024/Cosine이고 point가 정확히 34개(1차 실험 결과), DOC_LH_001 point 0개.
  조건이 다르면 쓰지 않고 멈춘다.
- DOC_LH_001 B2 청크 73개를 upsert(총 107개) → 기존 34개의 payload·벡터 해시가 그대로인지 확인
  → 같은 73개를 한 번 더 upsert해 중복 0 확인 → PDF 질문 6개 × 질의 방식 2개 검색.
- 임베딩 호출 인자: batch_size=4, timeout=180 (노트북 CPU에서 batch 16 / 60초는 시간 초과가 났음)
- 삭제·초기화 동작은 없다.

실행 조건: exp1_load_search.py와 같음
- 실행: .venv\\Scripts\\python.exe docs/development/experiments/exp2_lh001.py <결과 JSON 경로>
- 결과 예시: exp2_report.json (노트북 실측값)
"""
import hashlib
import json
import math
import sys
import time
import warnings

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
warnings.filterwarnings("ignore", message="Api key is used with an insecure connection.")
from database import chunking as ck, embedding as em, handoff_data as hd, vector_index as vi  # noqa: E402
from database.qdrant import store  # noqa: E402

OUT = sys.argv[1]
COLLECTION = "exp_b2_qwen3emb06b_v1"
OLD_DOCS = ["DOC_NHUF_003", "DOC_BOK_001", "DOC_YC_003", "DOC_GOV_006"]
NEW_DOC = "DOC_LH_001"
RULE = em.RULE_POLICY_SECTION_BODY_V2
TEMPLATES = {"A_none": None,
             "B_instruct": "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:{question}"}
QUESTIONS = [
    {"id": "P1", "question": "신혼·신생아 매입임대Ⅰ 주택의 임대기간과 재계약 가능 횟수는?", "evidence": "2년, 재계약 9회 가능", "has_policy_name": True},
    {"id": "P2", "question": "매입임대 주택에서 최장 몇 년까지 거주할 수 있나요?", "evidence": "최장 20년 거주", "has_policy_name": False},
    {"id": "P3", "question": "예비신혼부부는 언제까지 혼인신고를 해야 하나요?", "evidence": "입주일 전일까지 혼인신고", "has_policy_name": False},
    {"id": "P4", "question": "이번 모집공고에서 공급하는 주택은 총 몇 호인가요?", "evidence": "총 119호", "has_policy_name": False},
    {"id": "P5", "question": "서류를 우편으로 제출할 수 있나요? 제출 방법은?", "evidence": "서류제출 방법 : 온라인 제출", "has_policy_name": False},
    {"id": "P6", "question": "신청 관련 상담 전화번호는 무엇인가요?", "evidence": "1600-1004", "has_policy_name": False},
]
report = {"collection": COLLECTION, "new_document": NEW_DOC, "rule": RULE}


def fingerprint(record):
    payload = json.dumps(record.payload, ensure_ascii=False, sort_keys=True)
    vector = json.dumps([round(x, 7) for x in record.vector])
    return hashlib.sha256((payload + vector).encode()).hexdigest()


client = store.get_client()
report["qdrant_host"] = store.resolve_connection_settings()[0].split("//")[1]
report["ollama_host"] = em.resolve_ollama_base_url().split("//")[1]

# 1) 기존 컬렉션 설정·point 수
info = client.get_collection(COLLECTION)
cfg = info.config.params.vectors
report["before"] = {"size": cfg.size, "distance": str(cfg.distance), "count": store.count_points(client, COLLECTION),
                    "lh001_count": store.count_points(client, COLLECTION, {"document_id": NEW_DOC})}
if cfg.size != 1024 or str(cfg.distance) != "Cosine" or report["before"]["count"] != 34 or report["before"]["lh001_count"] != 0:
    report["stopped"] = "unexpected collection state"
    json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("STOP", report["before"])
    sys.exit(0)

# 2) 기존 34개 ID·payload·벡터 지문 기록
old_records, _ = client.scroll(COLLECTION, limit=100, with_payload=True, with_vectors=True)
old_fp = {str(r.id): fingerprint(r) for r in old_records}
report["before"]["old_ids"] = len(old_fp)
report["before"]["old_docs"] = sorted({r.payload["document_id"] for r in old_records})

# 3) 문서 content_hash 검증
documents = hd.load_documents(verify_hash=True)
document = vi.select_documents(documents, [NEW_DOC])[0]
text = document["text"]
for q in QUESTIONS:
    assert q["evidence"] in text, q
payload_chunks = hd.load_payload_chunks()
policy_metadata = hd.load_policy_metadata(payload_chunks)

# 4) B2 청크 73개
chunks = vi.prepare_chunks([document], "B2", payload_chunks)
report["chunk_count"] = len(chunks)
assert len(chunks) == 73, len(chunks)

# 5) 임베딩 입력
texts = [em.build_embedding_text(c, policy_metadata[c["policy_id"]], RULE) for c in chunks]
report["embedding_input_chars"] = {"total": sum(map(len, texts)), "body_total": sum(len(c["text"]) for c in chunks),
                                   "max_input": max(map(len, texts))}

# 6·7) 임베딩 + 검사
warm = []
em.make_ollama_embedder(metrics_sink=warm)(["가상 준비 문장 (sample_only)"])
metrics = []
t0 = time.perf_counter()
# 1회차(batch 16, timeout 60)는 첫 배치에서 시간 초과 → 호출 인자만 조정 (코드 기본값은 그대로)
EMBED_BATCH_SIZE, EMBED_TIMEOUT = 4, 180
report["embedding_call_args"] = {"batch_size": EMBED_BATCH_SIZE, "timeout": EMBED_TIMEOUT,
                                 "previous_attempt": "batch_size=16, timeout=60 → 첫 배치 ReadTimeout, upsert 전 중단"}
vectors = em.make_ollama_embedder(batch_size=EMBED_BATCH_SIZE, timeout=EMBED_TIMEOUT, metrics_sink=metrics)(texts)
wall = time.perf_counter() - t0
report["embedding"] = {
    "warmup": warm[0], "wall_seconds": round(wall, 3), "summary": em.summarize_ollama_metrics(metrics),
    "batches": [{"inputs": m["input_count"], "total_seconds": round(m["total_duration"] / 1e9, 3),
                 "load_seconds": round(m["load_duration"] / 1e9, 3), "prompt_eval_count": m["prompt_eval_count"],
                 "inputs_per_second": round(m["input_count"] / (m["total_duration"] / 1e9), 2),
                 "tokens_per_second": round(m["prompt_eval_count"] / (m["total_duration"] / 1e9), 1)} for m in metrics],
    "dims": sorted({len(v) for v in vectors}),
    "all_finite": all(math.isfinite(x) for v in vectors for x in v),
    "norm_min_max": [round(min(map(em.vector_norm, vectors)), 6), round(max(map(em.vector_norm, vectors)), 6)],
}

# 8) 신규 73개만 upsert
run_info = vi.make_run_info(embedding_model=em.DEFAULT_EMBEDDING_MODEL, embedding_dim=em.DEFAULT_EMBEDDING_DIM,
                            embedding_input_rule=RULE, index_run_id="RUN_20261105_B2_V2_EXP2_LH001")
points = store.build_points(chunks, vectors, policy_metadata=policy_metadata,
                            content_hashes={NEW_DOC: document["content_hash"]}, run_info=run_info)
new_ids = {p["id"] for p in points}
report["new_ids_overlap_old"] = len(new_ids & set(old_fp))
t0 = time.perf_counter()
store.upsert_points(client, COLLECTION, points)
report["upsert_seconds_first"] = round(time.perf_counter() - t0, 3)

# 9·10) 총 107, 기존 34개 불변
after_records, _ = client.scroll(COLLECTION, limit=500, with_payload=True, with_vectors=True)
after_fp = {str(r.id): fingerprint(r) for r in after_records}
report["after_first"] = {
    "count": store.count_points(client, COLLECTION),
    "lh001_count": store.count_points(client, COLLECTION, {"document_id": NEW_DOC}),
    "old_unchanged": all(after_fp.get(i) == f for i, f in old_fp.items()),
    "old_changed_ids": [i for i, f in old_fp.items() if after_fp.get(i) != f],
    "new_ids_present": new_ids <= set(after_fp),
}

# 11) 신규 point 원문 위치·PDF 페이지
markers = ck.find_page_markers(text)


def page_of(position):
    pages = [p for s, e, p in markers if s <= position]
    return pages[-1] if pages else None


lh_records = [r for r in after_records if r.payload["document_id"] == NEW_DOC]
report["page_check"] = {
    "text_mismatch": [r.payload["chunk_id"] for r in lh_records
                      if text[r.payload["char_start"]:r.payload["char_end"]] != r.payload["text"]],
    "page_missing": [r.payload["chunk_id"] for r in lh_records if r.payload["page_start"] is None],
    "page_start_ne_end": [r.payload["chunk_id"] for r in lh_records if r.payload["page_start"] != r.payload["page_end"]],
    "page_mismatch_marker": [r.payload["chunk_id"] for r in lh_records
                             if r.payload["page_start"] != page_of(r.payload["char_start"])],
    "page_range": [min(r.payload["page_start"] for r in lh_records), max(r.payload["page_start"] for r in lh_records)],
    "markers": len(markers),
    "missing_fields": sorted({f for r in lh_records for f in store.REQUIRED_PAYLOAD_FIELDS if f not in r.payload}),
}

# 12) 같은 신규 point 재적재 (벡터 재사용)
t0 = time.perf_counter()
store.upsert_points(client, COLLECTION, points)
report["upsert_seconds_second"] = round(time.perf_counter() - t0, 3)
report["after_second"] = {"count": store.count_points(client, COLLECTION),
                          "ids_same": store.list_point_ids(client, COLLECTION) == set(after_fp)}

# PDF 검색 (컬렉션 전체 107개 대상)
evidence_chunks = {q["id"]: [c["chunk_id"] for c in chunks if q["evidence"] in c["text"]] for q in QUESTIONS}
report["evidence_chunks"] = evidence_chunks
query_embed = em.make_ollama_embedder(batch_size=1)
results = []
for q in QUESTIONS:
    for name, template in TEMPLATES.items():
        t0 = time.perf_counter()
        qv = query_embed([em.build_query_text(q["question"], template)])[0]
        t_embed = time.perf_counter() - t0
        t0 = time.perf_counter()
        hits = store.search_chunks(client, COLLECTION, qv, k=5)
        t_search = time.perf_counter() - t0
        rows = [{"rank": h["rank"], "score": round(h["score"], 4), "document_id": h["document_id"], "chunk_id": h["chunk_id"],
                 "page": h["payload"].get("page_start"), "section_title": h["payload"].get("section_title"),
                 "contains_evidence": q["evidence"] in h["payload"]["text"],
                 "text_head": h["payload"]["text"][:80].replace("\r", " ").replace("\n", " ")} for h in hits]
        results.append({"id": q["id"], "question": q["question"], "template": name, "has_policy_name": q["has_policy_name"],
                        "evidence": q["evidence"], "evidence_chunks": evidence_chunks[q["id"]],
                        "lh001_in_top5": any(r["document_id"] == NEW_DOC for r in rows),
                        "lh001_rank1": bool(rows) and rows[0]["document_id"] == NEW_DOC,
                        "evidence_ranks": [r["rank"] for r in rows if r["contains_evidence"]],
                        "query_embed_seconds": round(t_embed, 3), "search_seconds": round(t_search, 4), "hits": rows})
report["search"] = results
report["collections_after"] = [c.name for c in client.get_collections().collections]
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("done")
