"""
1차 실제 적재·검색 실험 (2026-11-05 노트북에서 실행한 스크립트 보존본, 프로젝트 함수만 호출).

⚠ DB 쓰기 있음 — 사용자 승인 후에만 실행
- Qdrant 컬렉션 exp_b2_qwen3emb06b_v1 을 새로 만들고(이미 있으면 쓰지 않고 멈춤) 문서 4건의 B2 청크 34개를 upsert한다.
- 같은 point를 한 번 더 upsert해 중복 0을 확인한 뒤, 질문 4개 × 질의 방식 2개 검색을 한다.
- 삭제·초기화 동작은 없다.

실행 조건
- 프로젝트 루트에서 실행 (sys.path에 "."을 넣는다), .venv, .env(QDRANT_URL·QDRANT_API_KEY·OLLAMA_BASE_URL),
  Qdrant 서버 실행 중, Ollama에 qwen3-embedding:0.6b 설치
- 실행: .venv\\Scripts\\python.exe docs/development/experiments/exp1_load_search.py <결과 JSON 경로>
- 결과 예시: exp1_report.json (노트북 실측값)
- 다음 순서: exp2_lh001.py (DOC_LH_001 73개 추가 → 총 107개)
"""
import json
import sys
import time
import warnings

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
warnings.filterwarnings("ignore", message="Api key is used with an insecure connection.")
from database import handoff_data as hd, vector_index as vi, embedding as em  # noqa: E402
from database.qdrant import store  # noqa: E402

OUT = sys.argv[1]
COLLECTION = "exp_b2_qwen3emb06b_v1"
DOC_IDS = ["DOC_NHUF_003", "DOC_BOK_001", "DOC_YC_003", "DOC_GOV_006"]
RULE = em.RULE_POLICY_SECTION_BODY_V2
TEMPLATES = {
    "A_none": None,
    "B_instruct": "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:{question}",
}
QUESTIONS = [
    {"question": "신혼부부 전세자금 대출의 금리는 어떻게 되나요?", "expected_document_id": "DOC_NHUF_003",
     "evidence": "연 1.9% ∼ 연 3.3%"},
    {"question": "아이돌봄서비스 시간제 서비스는 몇 살 아동까지 이용할 수 있나요?", "expected_document_id": "DOC_BOK_001",
     "evidence": "생후 3개월~12세 이하의 아동"},
    {"question": "콘텐츠 기업이 인턴을 채용하면 1인당 인건비를 얼마 지원받나요?", "expected_document_id": "DOC_YC_003",
     "evidence": "1인당 월 108만원"},
    {"question": "수산자원보호 직접지불금을 부정하게 받으면 어떤 벌칙이 있나요?", "expected_document_id": "DOC_GOV_006",
     "evidence": "1년 이하의 징역 또는 1천만원 이하의 벌금"},
]
report = {"collection": COLLECTION, "document_ids": DOC_IDS, "rule": RULE}

# 0) 접속 + 컬렉션 사전 확인 (이미 있으면 쓰지 않고 멈춘다)
client = store.get_client()
existing = [c.name for c in client.get_collections().collections]
report["collections_before"] = existing
if COLLECTION in existing:
    info = client.get_collection(COLLECTION)
    report["stopped"] = {"reason": "collection already exists", "points": info.points_count,
                         "size": info.config.params.vectors.size, "distance": str(info.config.params.vectors.distance)}
    json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("STOP: collection exists")
    sys.exit(0)

# 1) 문서·content_hash 검증 (load_documents가 sha256(text) == content_hash 검사)
documents = hd.load_documents(verify_hash=True)
selected = vi.select_documents(documents, DOC_IDS)
by_id = {d["document_id"]: d for d in selected}
for q in QUESTIONS:
    assert q["evidence"] in by_id[q["expected_document_id"]]["text"], q
payload_chunks = hd.load_payload_chunks()
policy_metadata = hd.load_policy_metadata(payload_chunks)

# 2) B2 청크
chunks = vi.prepare_chunks(selected, "B2", payload_chunks)
report["chunk_count"] = len(chunks)
assert len(chunks) == 34, len(chunks)
report["chunks_per_document"] = {d: sum(1 for c in chunks if c["document_id"] == d) for d in DOC_IDS}
report["evidence_chunks"] = {q["expected_document_id"]: [c["chunk_id"] for c in chunks if q["evidence"] in c["text"]]
                             for q in QUESTIONS}

# 3·4) 임베딩 입력 (policy_section_body_v2)
texts = [em.build_embedding_text(c, policy_metadata[c["policy_id"]], RULE) for c in chunks]
report["embedding_input_chars"] = {"total": sum(map(len, texts)), "body_total": sum(len(c["text"]) for c in chunks)}

# 5) 임베딩: 준비 호출(모델 로딩 분리) → 본 호출 (batch 16)
warm_metrics = []
em.make_ollama_embedder(metrics_sink=warm_metrics)(["가상 준비 문장 (sample_only)"])
metrics = []
embed = em.make_ollama_embedder(batch_size=16, metrics_sink=metrics)
t0 = time.perf_counter()
vectors = embed(texts)
wall = time.perf_counter() - t0
report["embedding"] = {
    "warmup": warm_metrics[0],
    "wall_seconds": round(wall, 3),
    "summary": em.summarize_ollama_metrics(metrics),
    "batches": [{**m, "total_seconds": round(m["total_duration"] / 1e9, 3), "load_seconds": round(m["load_duration"] / 1e9, 3),
                 "inputs_per_second": round(m["input_count"] / (m["total_duration"] / 1e9), 2)} for m in metrics],
    "dims": sorted({len(v) for v in vectors}),
    "norm_min_max": [round(min(map(em.vector_norm, vectors)), 6), round(max(map(em.vector_norm, vectors)), 6)],
}

# 6) point → 컬렉션 생성 → upsert
run_info = vi.make_run_info(embedding_model=em.DEFAULT_EMBEDDING_MODEL, embedding_dim=em.DEFAULT_EMBEDDING_DIM,
                            embedding_input_rule=RULE, index_run_id="RUN_20261105_B2_V2_EXP1")
content_hashes = {d["document_id"]: d["content_hash"] for d in selected}
points = store.build_points(chunks, vectors, policy_metadata=policy_metadata, content_hashes=content_hashes,
                            run_info=run_info)
report["collection_status"] = store.ensure_collection(client, COLLECTION, run_info["embedding_dim"])
cfg = client.get_collection(COLLECTION).config.params.vectors
report["collection_config"] = {"size": cfg.size, "distance": str(cfg.distance)}
t0 = time.perf_counter()
store.upsert_points(client, COLLECTION, points)
report["upsert_seconds_first"] = round(time.perf_counter() - t0, 3)

# 7) 개수·ID 검사
expected_ids = {p["id"] for p in points}
report["after_first"] = {"count": store.count_points(client, COLLECTION),
                         "ids_match": store.list_point_ids(client, COLLECTION) == expected_ids,
                         "unique_point_ids": len(expected_ids)}

# 8) 재적재: 이미 계산한 벡터를 그대로 다시 upsert (임베딩 재호출 없음)
t0 = time.perf_counter()
store.upsert_points(client, COLLECTION, points)
report["upsert_seconds_second"] = round(time.perf_counter() - t0, 3)
report["after_second"] = {"count": store.count_points(client, COLLECTION),
                          "ids_match": store.list_point_ids(client, COLLECTION) == expected_ids}

# payload text == 원문 조각, 필수 필드, document_id 필터, 보호 규칙
records, _ = client.scroll(COLLECTION, limit=100, with_payload=True, with_vectors=False)
report["payload_text_mismatch"] = [
    r.payload["chunk_id"] for r in records
    if by_id[r.payload["document_id"]]["text"][r.payload["char_start"]:r.payload["char_end"]] != r.payload["text"]]
report["payload_missing_fields"] = sorted({f for r in records for f in store.REQUIRED_PAYLOAD_FIELDS if f not in r.payload})
report["filter_counts"] = {d: store.count_points(client, COLLECTION, {"document_id": d}) for d in DOC_IDS}
try:
    store.count_points(client, "lifepolicy_chunks")
    report["protected_guard"] = "NOT BLOCKED"
except store.CollectionGuardError as error:
    report["protected_guard"] = str(error)

# 검색: 같은 컬렉션·같은 문서 벡터. 질의 문자열(지시문 유무)만 다르다.
query_embed = em.make_ollama_embedder(batch_size=1)
results = []
for q in QUESTIONS:
    for name, template in TEMPLATES.items():
        qtext = em.build_query_text(q["question"], template)
        t0 = time.perf_counter()
        qv = query_embed([qtext])[0]
        t_embed = time.perf_counter() - t0
        t0 = time.perf_counter()
        hits = store.search_chunks(client, COLLECTION, qv, k=5)
        t_search = time.perf_counter() - t0
        rows = []
        for h in hits:
            p = h["payload"]
            rows.append({"rank": h["rank"], "score": round(h["score"], 4), "policy_id": h["policy_id"],
                         "document_id": h["document_id"], "chunk_id": h["chunk_id"],
                         "section_title": p.get("section_title"), "contains_evidence": q["evidence"] in p["text"],
                         "text_head": p["text"][:90].replace("\r", " ").replace("\n", " ")})
        results.append({
            "question": q["question"], "template": name, "expected_document_id": q["expected_document_id"],
            "evidence": q["evidence"],
            "expected_doc_in_top5": any(r["document_id"] == q["expected_document_id"] for r in rows),
            "expected_doc_rank1": bool(rows) and rows[0]["document_id"] == q["expected_document_id"],
            "evidence_ranks": [r["rank"] for r in rows if r["contains_evidence"]],
            "query_embed_seconds": round(t_embed, 3), "search_seconds": round(t_search, 4), "hits": rows})
report["search"] = results
report["collections_after"] = [c.name for c in client.get_collections().collections]
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("done")
