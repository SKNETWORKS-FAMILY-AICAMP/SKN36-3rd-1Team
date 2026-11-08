"""
Retrieval 평가 실행기 — retrieval_eval_v0(고정 질문)을 전체 색인 컬렉션들에서 같은 조건으로 검색·채점한다. (읽기 전용)

같은 조건
- 질문 텍스트 그대로(질의 지시문 없음), 같은 임베딩 모델 qwen3-embedding:0.6b
- 질문 55개를 "한 번만" 임베딩하고 그 벡터를 모든 컬렉션 검색에 재사용한다 (임베딩 차이가 비교에 끼어들지 않게)
- Top-K=5, 필터 없음, store.search_chunks(query_points) 같은 설정. Graph 확장 없음.

실행 전 불변성 검사 (하나라도 실패하면 검색하지 않는다)
- retrieval_eval_v0.freeze.json의 파일 해시(줄바꿈 LF 정규화)와 실제 질문 파일 해시 일치
- freeze의 manifest sha256 = 현재 manifest = 각 컬렉션 색인 계획(full_index)의 manifest
- 질문 수 55 / 채점 52 / 검증 55
- 각 컬렉션: 상태 complete(건수·id·payload·원문 일치), 1024차원·Cosine, 모델·입력 규칙 (full_index.inspect_collection)

채점: database.retrieval_eval.score_question / summarize / attainability (고정 기준 그대로)
- Recall@1·@3·@5 = Top-1/3/5로 잘라 각각 채점한 hit 비율, MRR@5 = 1/첫 정답 순위(5위 안에 없으면 0)

결과 파일: 질문별 Top-5(rank, score, chunk_id, document_id, policy_id, char_start, char_end, chunking_version, 본문 길이),
          판정, 컬렉션별·유형별·문서 그룹별 지표. 청크 본문은 저장하지 않는다.

실행 (프로젝트 루트, .venv):
    .venv\\Scripts\\python.exe -m database.retrieval_run --record
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from database import embedding as em
from database import full_index as fi
from database import handoff_data
from database import retrieval_eval as rv
from database.qdrant import store

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = PROJECT_ROOT / "docs" / "development" / "retrieval_eval"
FREEZE_FILE = EVAL_DIR / "retrieval_eval_v0.freeze.json"
QUESTIONS_FILE = EVAL_DIR / "questions_candidates_v0.jsonl"
RECORD_DIR = PROJECT_ROOT / "docs" / "development" / "experiments" / "retrieval_eval_20261106"
CONFIGS = ("A_B2", "B_B3", "C_B3C1")
K_VALUES = (1, 3, 5)
TOP_K = 5
C1_DOCUMENTS = fi.C1_DOCUMENTS
# 질의 지시문 (실험 전에 고정, 2026-11-06). instruct_v1 = Qwen3-Embedding 모델 카드 예시 문장, 2026-11-05 지시문 실험과 같은 문장
QUERY_TEMPLATES: dict[str, str | None] = {
    "none": None,
    "instruct_v1": "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:{question}",
}


class EvalIntegrityError(RuntimeError):
    """평가 데이터·컬렉션 불변성 검사 실패 → 검색하지 않는다."""


def _lf_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_frozen_questions(eval_dir: Path = EVAL_DIR) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """freeze 기록과 파일 해시·manifest를 검사하고 질문을 돌려준다. 다르면 EvalIntegrityError."""
    freeze = json.loads((eval_dir / FREEZE_FILE.name).read_text(encoding="utf-8"))
    problems = []
    for name, expected in freeze["files"].items():
        actual = _lf_sha256(eval_dir / name)
        if actual != expected:
            problems.append(f"{name} 해시 불일치")
    if not fi.manifest_equivalent(freeze["manifest_sha256"]):
        problems.append("manifest 해시 불일치")
    questions = rv.load_jsonl(eval_dir / QUESTIONS_FILE.name)
    counts = (len(questions), sum(q["answerable"] for q in questions), sum(q["verification"] == "verified" for q in questions))
    if counts != (freeze["questions"], freeze["scored_questions"], freeze["verified"]):
        problems.append(f"질문 수 불일치 {counts}")
    if problems:
        raise EvalIntegrityError("; ".join(problems))
    return questions, freeze


def check_collections(client, documents: list[dict[str, Any]], configs=CONFIGS) -> dict[str, dict[str, Any]]:
    """각 구성의 컬렉션이 색인 계획과 정확히 같은지(complete) 읽기 전용으로 확인한다."""
    content_hashes = {d["document_id"]: d["content_hash"] for d in documents}
    report, problems = {}, []
    for name in configs:
        config = fi.get_config(name)
        model, dim = fi.config_model(config)
        chunks, _info = fi.build_config_chunks(name, documents)
        state = fi.inspect_collection(client, config["collection"], chunks, content_hashes, model, dim)
        report[name] = {"collection": config["collection"], "expected_points": len(chunks),
                        "actual_points": state["existing_points"], "state": state["state"],
                        "chunk_fingerprint": fi.chunk_fingerprint(chunks), "manifest_sha256": fi.manifest_sha256(),
                        "model": model, "dim": dim, "distance": fi.DISTANCE,
                        "embedding_input_rule": fi.EMBEDDING_INPUT_RULE, "chunks": chunks}
        if state["state"] != "complete" or len(chunks) != config["expected_chunks"]:
            problems.append(f"{name}: {state['state']} {state['reasons']}")
    if problems:
        raise EvalIntegrityError("; ".join(problems))
    return report


def embed_questions(questions: list[dict[str, Any]], embedder,
                    query_template: str | None = fi.QUERY_TEMPLATE) -> tuple[list[list[float]], float]:
    """질문을 한 번만 임베딩한다. query_template=None이면 질문 텍스트 그대로(지시문 없음)."""
    texts = [em.build_query_text(question["question"], query_template) for question in questions]
    started = time.perf_counter()
    vectors = embedder(texts)
    return vectors, round(time.perf_counter() - started, 3)


def _hit_record(hit: dict[str, Any]) -> dict[str, Any]:
    payload = hit["payload"]
    return {"rank": hit["rank"], "score": round(hit["score"], 6), "chunk_id": hit["chunk_id"],
            "document_id": hit["document_id"], "policy_id": hit["policy_id"],
            "char_start": payload.get("char_start"), "char_end": payload.get("char_end"),
            "chunking_version": payload.get("chunking_version"), "text_chars": len(payload.get("text") or "")}


def evaluate_collection(client, collection: str, questions: list[dict[str, Any]], vectors: list[list[float]],
                        k: int = TOP_K) -> dict[str, Any]:
    """한 컬렉션에서 질문별 Top-k 검색·채점 (읽기 전용)."""
    rows, seconds = [], []
    for question, vector in zip(questions, vectors):
        started = time.perf_counter()
        hits = [_hit_record(hit) for hit in store.search_chunks(client, collection, vector, k=k)]
        seconds.append(time.perf_counter() - started)
        scores = {f"at_{cut}": rv.score_question(question, hits, k=cut) for cut in K_VALUES}
        rows.append({"question_id": question["question_id"], "question_type": question["question_type"],
                     "answerable": question["answerable"], "hits": hits, "scores": scores,
                     "search_seconds": round(seconds[-1], 4)})
    return {"collection": collection, "questions": rows,
            "search_seconds": {"total": round(sum(seconds), 3), "mean": round(sum(seconds) / len(seconds), 4),
                               "max": round(max(seconds), 4)}}


def aggregate(rows: list[dict[str, Any]], questions_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """전체·질문 유형별·C1 대상 문서 여부별 지표."""
    def metrics(selected):
        scored = [row for row in selected if row["answerable"]]
        if not scored:
            return {"questions": 0}
        result = {"questions": len(scored)}
        for cut in K_VALUES:
            result[f"recall_at_{cut}"] = round(sum(r["scores"][f"at_{cut}"]["outcome"] == "hit" for r in scored) / len(scored), 4)
        result["mrr_at_5"] = round(sum(r["scores"]["at_5"]["reciprocal_rank"] for r in scored) / len(scored), 4)
        result["outcomes_at_5"] = dict(Counter(r["scores"]["at_5"]["outcome"] for r in scored))
        return result

    by_type = defaultdict(list)
    for row in rows:
        by_type[row["question_type"]].append(row)

    def in_c1(row):
        gold = questions_by_id[row["question_id"]]["gold"]["required"]
        return any(alt["document_id"] in C1_DOCUMENTS for g in gold for alt in g["alternatives"])

    general = [row for row in rows if not row["answerable"]]
    return {"overall": metrics(rows),
            "by_type": {name: metrics(items) for name, items in sorted(by_type.items()) if name != "general_no_single_answer"},
            "c1_target_questions": metrics([row for row in rows if in_c1(row)]),
            "non_c1_questions": metrics([row for row in rows if not in_c1(row)]),
            "general_questions": [{"question_id": row["question_id"],
                                   "distinct_policies": len({h["policy_id"] for h in row["hits"]}),
                                   "distinct_documents": len({h["document_id"] for h in row["hits"]}),
                                   "policies": [h["policy_id"] for h in row["hits"]]} for row in general],
            "failures_at_5": [{"question_id": row["question_id"], "outcome": row["scores"]["at_5"]["outcome"],
                               "first_gold_document_rank": row["scores"]["at_5"].get("first_gold_document_rank")}
                              for row in rows if row["answerable"] and row["scores"]["at_5"]["outcome"] != "hit"]}


def git_state() -> dict[str, Any]:
    def run(*args):
        return subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True).stdout.strip()
    return {"head": run("rev-parse", "HEAD"), "tracked_dirty": bool(run("status", "--porcelain", "--untracked-files=no")),
            "untracked": run("status", "--porcelain").count("??")}


def run(client, embedder, *, documents=None, eval_dir: Path = EVAL_DIR, configs=CONFIGS,
        query_template_name: str = "none") -> dict[str, Any]:
    """
    불변성 검사 → 모델별로 질문 1회 임베딩 → 컬렉션별 검색·채점 → 집계. 아무것도 쓰지 않는다.
    embedder: 임베딩 함수 하나(모든 구성이 같은 모델일 때) 또는 {모델 이름: 임베딩 함수}
    query_template_name: QUERY_TEMPLATES의 이름 ("none" = 지시문 없음)
    """
    if query_template_name not in QUERY_TEMPLATES:
        raise EvalIntegrityError(f"알 수 없는 질의 지시문: {query_template_name}")
    query_template = QUERY_TEMPLATES[query_template_name]
    questions, freeze = load_frozen_questions(eval_dir)
    documents = documents if documents is not None else handoff_data.load_documents(verify_hash=True)
    collections = check_collections(client, documents, configs)
    by_id = {q["question_id"]: q for q in questions}
    vectors_by_model: dict[str, list[list[float]]] = {}
    model_info: dict[str, dict[str, Any]] = {}
    for name in configs:
        model = collections[name].get("model", fi.EMBEDDING_MODEL)
        dim = collections[name].get("dim", fi.EMBEDDING_DIM)
        if model in vectors_by_model:
            continue
        model_embedder = embedder[model] if isinstance(embedder, dict) else embedder
        vectors, embed_seconds = embed_questions(questions, model_embedder, query_template)
        if len(vectors) != len(questions) or any(len(v) != dim for v in vectors):
            raise EvalIntegrityError(f"{model} 질문 임베딩 개수·차원 불일치")
        vectors_by_model[model] = vectors
        model_info[model] = {"dim": dim, "embedding_seconds": embed_seconds, "questions_embedded": len(vectors)}
    results = {}
    for name in configs:
        model = collections[name].get("model", fi.EMBEDDING_MODEL)
        evaluation = evaluate_collection(client, collections[name]["collection"], questions, vectors_by_model[model])
        unattainable = rv.attainability(questions, collections[name]["chunks"])
        results[name] = {**{k: v for k, v in collections[name].items() if k != "chunks"},
                         "unattainable": unattainable, "search_seconds": evaluation["search_seconds"],
                         "summary": aggregate(evaluation["questions"], by_id), "questions": evaluation["questions"]}
    first_model = next(iter(model_info))
    return {"created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "eval_set": freeze["eval_set"], "eval_files_sha256": freeze["files"], "manifest_sha256": freeze["manifest_sha256"],
            "data_version": freeze["data_version"], "git": git_state(),
            "query": {"model": first_model if len(model_info) == 1 else sorted(model_info),
                      "dim": model_info[first_model]["dim"] if len(model_info) == 1 else None,
                      "models": model_info, "query_template_name": query_template_name,
                      "query_template": query_template, "top_k": TOP_K, "filters": None, "embedded_once": True,
                      "embedding_seconds": round(sum(info["embedding_seconds"] for info in model_info.values()), 3)},
            "results": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retrieval 평가 실행 (읽기 전용)")
    parser.add_argument("--config", action="append", choices=tuple(fi.FULL_INDEX_CONFIGS),
                        help="평가할 구성 (기본: A_B2, B_B3, C_B3C1)")
    parser.add_argument("--query-template", default="none", choices=tuple(QUERY_TEMPLATES))
    parser.add_argument("--output", default="retrieval_eval_v0_results.json", help="RECORD_DIR 아래 결과 파일 이름")
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args(argv)
    configs = tuple(args.config) if args.config else CONFIGS
    client = store.get_client()
    embedders = {}
    for name in configs:
        model, dim = fi.config_model(fi.get_config(name))
        embedders.setdefault(model, em.make_ollama_embedder(model=model, expected_dim=dim))
    output = run(client, embedders, configs=configs, query_template_name=args.query_template)
    summary = {name: result["summary"]["overall"] for name, result in output["results"].items()}
    if args.record:
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        (RECORD_DIR / args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": summary, "query": output["query"], "git": output["git"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
