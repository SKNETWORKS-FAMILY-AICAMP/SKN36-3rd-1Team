"""retrieval_eval_v0(고정 55문항)를 v1.3.2 컬렉션과 Issue #21 컬렉션에 같은 방식으로 실행해 비교한다. 읽기 전용.

retrieval_run.run()은 데이터셋 manifest가 다르면 평가를 거부한다(설계상 무결성 장치). 이 실험은 그 장치를 바꾸지 않고:
- 질문 파일 해시·문항 수를 freeze 기록과 대조한다 (평가셋 불변 확인)
- 정답 근거 문서 본문이 v1.3.2 정제본과 후보 documents.jsonl에서 같은지 content_hash로 확인한다 (정답 위치 유효성)
- 같은 임베딩 모델(qwen3-embedding:0.6b, 지시문 없음)·같은 채점 함수(retrieval_run/retrieval_eval)를 쓴다.
실행: .venv/Scripts/python.exe docs/development/experiments/retrieval_eval_i21_20261107/compare_v132_vs_i21.py
"""
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from database import embedding as em  # noqa: E402
from database import retrieval_eval as rv  # noqa: E402
from database import retrieval_run as rr  # noqa: E402
from database.qdrant import store  # noqa: E402

OUT = Path(__file__).resolve().parent / "retrieval_eval_v0_v132_vs_i21.json"
CANDIDATE_DOCS = ROOT / "data/candidates/issue21-v1.4.0-rag/documents.jsonl"
COLLECTIONS = {
    "v132_B2": "exp_b2_qwen3emb06b_full_v1", "v132_B3": "exp_b3_qwen3emb06b_full_v1",
    "i21_B2": "exp_b2_qwen3emb06b_i21_v1", "i21_B3": "exp_b3_qwen3emb06b_i21_v1",
}


def frozen_questions():
    freeze = json.loads(rr.FREEZE_FILE.read_text(encoding="utf-8"))
    for name, expected in freeze["files"].items():
        actual = hashlib.sha256((rr.EVAL_DIR / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if actual != expected:
            raise SystemExit(f"평가 파일 변경됨: {name}")
    questions = rv.load_jsonl(rr.QUESTIONS_FILE)
    if (len(questions), sum(q["answerable"] for q in questions)) != (freeze["questions"], freeze["scored_questions"]):
        raise SystemExit("문항 수 불일치")
    return questions, freeze


def gold_documents_identical(questions):
    old = {d["document_id"]: d["content_hash"] for d in rv.load_jsonl(ROOT / "data/processed/documents.jsonl")}
    new = {d["document_id"]: d["content_hash"] for d in rv.load_jsonl(CANDIDATE_DOCS)}
    gold = {alt["document_id"] for q in questions if q["answerable"]
            for g in q["gold"]["required"] for alt in g["alternatives"]}
    differing = sorted(d for d in gold if old.get(d) is None or old.get(d) != new.get(d))
    return len(gold), differing


def collection_chunks(client, collection):
    chunks, offset = [], None
    while True:
        batch, offset = client.scroll(collection, limit=512, offset=offset, with_payload=True, with_vectors=False)
        chunks += [{k: p.payload.get(k) for k in ("document_id", "policy_id", "char_start", "char_end")} for p in batch]
        if offset is None:
            return chunks


def main():
    em.load_env_file()
    questions, freeze = frozen_questions()
    gold_count, differing = gold_documents_identical(questions)
    if differing:
        raise SystemExit(f"정답 근거 문서 본문이 다름: {differing}")
    client = store.get_client()
    embedder = em.make_ollama_embedder(model="qwen3-embedding:0.6b", expected_dim=1024)
    vectors, seconds = rr.embed_questions(questions, embedder, None)
    by_id = {q["question_id"]: q for q in questions}
    results = {}
    for label, collection in COLLECTIONS.items():
        evaluation = rr.evaluate_collection(client, collection, questions, vectors)
        chunks = collection_chunks(client, collection)
        results[label] = {"collection": collection, "points": len(chunks),
                          "documents": len({c["document_id"] for c in chunks}),
                          "unattainable": rv.attainability(questions, chunks),
                          "summary": rr.aggregate(evaluation["questions"], by_id),
                          "questions": evaluation["questions"]}
    record = {"created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "eval_set": freeze["eval_set"], "eval_files_sha256": freeze["files"],
              "eval_data_version": freeze["data_version"], "gold_documents_checked": gold_count,
              "gold_documents_identical": True, "query": {"model": "qwen3-embedding:0.6b", "dim": 1024,
              "query_template": None, "top_k": rr.TOP_K, "embedding_seconds": seconds},
              "limitations": ["평가셋은 v1.3.2 문서로 만든 55문항(채점 52). 후보의 신규 문서 15건에 대한 질문은 없다",
                              "후보 코퍼스는 문서 261건으로 늘어 같은 질문에 경쟁 청크가 더 많다",
                              "정답 위치는 문서 본문 기준이며 정답 문서 본문은 두 데이터셋에서 content_hash가 같다"],
              "results": results}
    OUT.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({label: {"points": r["points"], "documents": r["documents"],
                              **{k: r["summary"]["overall"].get(k) for k in ("questions", "recall_at_1", "recall_at_3",
                                                                              "recall_at_5", "mrr_at_5", "outcomes_at_5")},
                              "unattainable": len(r["unattainable"]) if isinstance(r["unattainable"], (list, dict)) else r["unattainable"]}
                      for label, r in results.items()}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
