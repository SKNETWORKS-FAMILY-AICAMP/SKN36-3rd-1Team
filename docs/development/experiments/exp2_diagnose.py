"""
P3·P6 근거 청크 진단 (2026-11-05 노트북 실행 스크립트 보존본).

DB 쓰기 없음: Qdrant 읽기 전용 검색(k=107) + Ollama 질의 임베딩 2건.
전제: exp2_lh001.py 이후 107개 point. 프로젝트 루트에서 .venv로 실행.
"""
import sys
import warnings

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
from database import embedding as em, handoff_data as hd, vector_index as vi  # noqa: E402
from database.qdrant import store  # noqa: E402

client = store.get_client()
docs = hd.load_documents()
pc = hd.load_payload_chunks()
meta = hd.load_policy_metadata(pc)
chunks = {c["chunk_id"]: c for c in vi.prepare_chunks(vi.select_documents(docs, ["DOC_LH_001"]), "B2", pc)}
for cid in ("CHK_B2_DOC_LH_001_C007", "CHK_B2_DOC_LH_001_C010", "CHK_B2_DOC_LH_001_C001", "CHK_B2_DOC_LH_001_C039"):
    c = chunks[cid]
    d = em.describe_embedding_input(c, meta[c["policy_id"]], em.RULE_POLICY_SECTION_BODY_V2)
    print(f"\n## {cid} len={len(c['text'])} page={c['page_start']} title={c['section_title']!r}")
    print("   header:", repr(d["text"][: len(d["text"]) - len(c["text"])])[:160])
    print("   text  :", c["text"][:420].replace("\n", " ⏎ "))

embed = em.make_ollama_embedder(batch_size=1)
for question, targets in (("예비신혼부부는 언제까지 혼인신고를 해야 하나요?", ["CHK_B2_DOC_LH_001_C007", "CHK_B2_DOC_LH_001_C010"]),
                          ("신청 관련 상담 전화번호는 무엇인가요?", ["CHK_B2_DOC_LH_001_C001", "CHK_B2_DOC_LH_001_C039"])):
    hits = store.search_chunks(client, "exp_b2_qwen3emb06b_v1", embed([question])[0], k=107)
    ranks = {h["chunk_id"]: (h["rank"], round(h["score"], 4)) for h in hits}
    print(f"\n[{question}] 근거 청크 전체 순위(107개 중):", {t: ranks.get(t) for t in targets})
