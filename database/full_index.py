"""
전체 246개 문서 Qdrant 색인 (실험 3종) — 기본은 dry-run(읽기 전용)이다.

  A_B2   : 전체 B2                       → exp_b2_qwen3emb06b_full_v1   (1,081 청크)
  B_B3   : 전체 B3                       → exp_b3_qwen3emb06b_full_v1   (1,117 청크)
  C_B3C1 : B3 243개 문서 + C1 3개 문서   → exp_b3c1_qwen3emb06b_full_v1 (1,141 청크)
  A_B2_4B: 전체 B2 + qwen3-embedding:4b(2560차원) → exp_b2_qwen3emb4b_full_v1 (1,081 청크, 모델 비교용)
공통: qwen3-embedding:0.6b, 1024차원, Cosine, 임베딩 입력 policy_section_body_v2, 질의 지시문 없음, point id = uuid5(chunk_id)

안전장치
- execute_full_index는 confirm_write=True가 아니면 아무것도 쓰지 않는다. CLI도 --execute --confirm-write 둘 다 있어야 쓴다.
- 기존 107 point 실험 컬렉션(exp_b2_qwen3emb06b_v1)과 그 밖의 컬렉션에는 쓰지 않는다 (구성표의 이름만 허용).
- 컬렉션 삭제·초기화 함수가 없다.
- 대상 컬렉션이 이미 있으면 먼저 검사(inspect_collection)한다.
    차원·거리 다름 / 예상 밖 point 있음 / 같은 id인데 payload(원문 위치·해시·모델·규칙·버전)가 다름 → "conflict" → 중단
    예상 point의 일부만 있음 → "partial" → 없는 point만 임베딩·적재(중단 후 이어 하기)
    예상과 정확히 같음 → "complete" → 쓰지 않음
- 적재 후 verify_full_index: 건수, id 집합, payload 원문 슬라이스·위치·해시, 모델·차원·규칙, 문서 246개·정책 237개 포함 여부.
- 실험 기록(run record): 구성·컬렉션·모델·차원·거리·입력 규칙·청킹 전략·manifest sha256·문서 해시 검증·청크 지문.

실행 (프로젝트 루트, .venv):
    .venv\\Scripts\\python.exe -m database.full_index --config A_B2 --config B_B3 --config C_B3C1        # dry-run
    .venv\\Scripts\\python.exe -m database.full_index --config B_B3 --execute --confirm-write          # 승인 후
"""

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from database import chunking
from database import embedding as em
from database import handoff_data
from database import llm_chunking
from database.qdrant import store

PROJECT_ROOT = Path(__file__).resolve().parents[1]
C1_PROPOSALS_DIR = PROJECT_ROOT / "docs" / "development" / "experiments" / "desktop_20261106"
RECORD_DIR = PROJECT_ROOT / "docs" / "development" / "experiments" / "full_index_20261106"

EMBEDDING_MODEL = "qwen3-embedding:0.6b"
EMBEDDING_DIM = 1024
DISTANCE = "Cosine"
EMBEDDING_INPUT_RULE = "policy_section_body_v2"
QUERY_TEMPLATE = None                               # 질의 지시문 없음 (검색 단계에서 사용)
C1_DOCUMENTS = ("DOC_LH_001", "DOC_NHUF_003", "DOC_NHUF_005")
PROTECTED_COLLECTIONS = ("exp_b2_qwen3emb06b_v1",)  # 기존 107 point 기준선 (이 모듈은 절대 쓰지 않는다)

FULL_INDEX_CONFIGS: dict[str, dict[str, Any]] = {
    "A_B2": {"collection": "exp_b2_qwen3emb06b_full_v1", "strategy": "B2", "expected_chunks": 1081},
    "B_B3": {"collection": "exp_b3_qwen3emb06b_full_v1", "strategy": "B3", "expected_chunks": 1117},
    "C_B3C1": {"collection": "exp_b3c1_qwen3emb06b_full_v1", "strategy": "B3+C1", "expected_chunks": 1141,
               "c1_documents": C1_DOCUMENTS},
    # 2026-11-06 모델 비교: 청킹(B2)은 A_B2와 같고 임베딩 모델·차원만 다르다. 차원 2560은 다운로드 후 실측값
    "A_B2_4B": {"collection": "exp_b2_qwen3emb4b_full_v1", "strategy": "B2", "expected_chunks": 1081,
                "model": "qwen3-embedding:4b", "dim": 2560},
}
# 구성에 model·dim이 없으면 기본값(0.6B, 1024)을 쓴다 → 기존 구성 3개의 동작·기록은 그대로다


def config_model(config: dict[str, Any]) -> tuple[str, int]:
    return config.get("model", EMBEDDING_MODEL), config.get("dim", EMBEDDING_DIM)
# payload 비교 필드: 같은 point id인데 이 값이 다르면 다른 실험 데이터로 보고 덮어쓰지 않는다
PAYLOAD_IDENTITY_FIELDS = ("chunk_id", "document_id", "policy_id", "char_start", "char_end", "page_start", "page_end",
                           "chunking_version", "content_hash", "embedding_model", "embedding_dim", "embedding_input_rule")
EMBED_BATCH = 32
CHARS_PER_TOKEN = 16169 / 11953     # 2026-11-05 데스크톱 실측(입력 글자 / Ollama prompt_eval_count). 추정에만 쓴다.


class FullIndexError(RuntimeError):
    """색인 계획·검증 실패 (쓰지 않고 중단해야 하는 상황)."""


# ---------------------------------------------------------------------------
# 1. 구성별 청크
# ---------------------------------------------------------------------------
def get_config(name: str) -> dict[str, Any]:
    if name not in FULL_INDEX_CONFIGS:
        raise FullIndexError(f"알 수 없는 구성: {name} {tuple(FULL_INDEX_CONFIGS)}")
    config = {"name": name, **FULL_INDEX_CONFIGS[name]}
    store.assert_experiment_collection(config["collection"])
    if config["collection"] in PROTECTED_COLLECTIONS:
        raise FullIndexError(f"보호 컬렉션에는 쓰지 않습니다: {config['collection']}")
    return config


def build_config_chunks(name: str, documents: list[dict[str, Any]],
                        proposals_dir: Path | str = C1_PROPOSALS_DIR) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    구성의 청크 전체를 만든다 (DB 접속 없음). 반환: (청크, 정보)
    C_B3C1: C1 대상 문서는 저장된 실제 LLM 제안으로 C1을 만들고, 하나라도 B3로 대체되면 구성 C가 아니므로 중단한다.
    """
    config = get_config(name)
    if config["strategy"] in ("B2", "B3"):
        chunks, _reports = chunking.chunk_documents(documents, strategy=config["strategy"])
        return chunks, {"c1": {}}
    c1_ids = set(config["c1_documents"])
    others = [document for document in documents if document["document_id"] not in c1_ids]
    chunks, _reports = chunking.chunk_documents(others, strategy="B3")
    c1_info = {}
    for document in documents:
        if document["document_id"] not in c1_ids:
            continue
        proposals = llm_chunking.load_saved_proposals(document, proposals_dir)
        c1_chunks, report = llm_chunking.chunk_document_with_sections(document, proposals)
        if report["used_fallback"]:
            raise FullIndexError(f"{document['document_id']} C1 재현 실패(B3 대체): {report['fallback_reasons']}")
        c1_info[document["document_id"]] = {"proposals": len(proposals), "accepted": len(report["accepted_sections"]),
                                            "chunks": len(c1_chunks), "issues": [i["issue"] for i in report["issues"]]}
        chunks += c1_chunks
    order = {document["document_id"]: index for index, document in enumerate(documents)}
    chunks.sort(key=lambda chunk: (order[chunk["document_id"]], chunk["char_start"], chunk["chunk_seq"]))
    return chunks, {"c1": c1_info}


def chunk_fingerprint(chunks: list[dict[str, Any]]) -> str:
    """청크 집합의 지문: (point_id, chunk_id, document_id, 위치, 본문 sha256) 정렬 목록의 sha256."""
    rows = sorted((c["point_id"], c["chunk_id"], c["document_id"], c["char_start"], c["char_end"],
                   hashlib.sha256(c["text"].encode("utf-8")).hexdigest()) for c in chunks)
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def manifest_sha256() -> str:
    """manifest.json의 sha256. 줄바꿈을 LF로 맞춘 뒤 계산한다 (Windows Git 체크아웃 CRLF 변환과 무관하게 같은 값)."""
    return hashlib.sha256(handoff_data.MANIFEST_PATH.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def make_run_info(name: str, run_id: str | None = None) -> dict[str, Any]:
    model, dim = config_model(get_config(name))
    return {"embedding_model": model, "embedding_dim": dim,
            "embedding_input_rule": EMBEDDING_INPUT_RULE,
            "index_run_id": run_id or f"RUN_FULL_{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"}


def expected_payload_identity(chunk: dict[str, Any], content_hash: str, model: str = EMBEDDING_MODEL,
                              dim: int = EMBEDDING_DIM) -> dict[str, Any]:
    return {"chunk_id": chunk["chunk_id"], "document_id": chunk["document_id"], "policy_id": chunk["policy_id"],
            "char_start": chunk["char_start"], "char_end": chunk["char_end"], "page_start": chunk["page_start"],
            "page_end": chunk["page_end"], "chunking_version": chunk["chunking_version"], "content_hash": content_hash,
            "embedding_model": model, "embedding_dim": dim,
            "embedding_input_rule": EMBEDDING_INPUT_RULE}


# ---------------------------------------------------------------------------
# 2. 기존 컬렉션 검사 (읽기 전용)
# ---------------------------------------------------------------------------
def _retrieve_payloads(client, collection: str, ids: list[str], batch: int = 256) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), batch):
        for record in client.retrieve(collection_name=collection, ids=ids[start:start + batch],
                                      with_payload=True, with_vectors=False):
            payloads[str(record.id)] = record.payload or {}
    return payloads


def inspect_collection(client, collection: str, chunks: list[dict[str, Any]], content_hashes: dict[str, str],
                       model: str = EMBEDDING_MODEL, dim: int = EMBEDDING_DIM) -> dict[str, Any]:
    """
    대상 컬렉션 상태를 읽기만 한다. 반환 state: absent / complete / partial / conflict
    conflict 이유: 차원·거리 불일치, 예상 밖 point, 같은 id의 payload 불일치(모델·차원 포함), 본문 불일치
    model·dim: 구성의 임베딩 모델과 차원 (기본값 0.6B·1024)
    """
    store.assert_experiment_collection(collection)
    expected = {chunk["point_id"]: chunk for chunk in chunks}
    if not client.collection_exists(collection):
        return {"state": "absent", "existing_points": 0, "missing": len(expected), "reasons": []}
    reasons: list[str] = []
    params = client.get_collection(collection).config.params.vectors
    if params.size != dim or str(params.distance).split(".")[-1].lower() != DISTANCE.lower():
        reasons.append(f"벡터 설정 불일치: size={params.size}, distance={params.distance}")
    existing = store.list_point_ids(client, collection)
    unexpected = sorted(existing - set(expected))
    if unexpected:
        reasons.append(f"예상 밖 point {len(unexpected)}개 (예: {unexpected[:3]})")
    present = sorted(existing & set(expected))
    mismatched = []
    for point_id, payload in _retrieve_payloads(client, collection, present).items():
        chunk = expected[point_id]
        identity = expected_payload_identity(chunk, content_hashes[chunk["document_id"]], model, dim)
        differing = [field for field in PAYLOAD_IDENTITY_FIELDS if payload.get(field) != identity[field]]
        if payload.get("text") != chunk["text"]:
            differing.append("text")
        if differing:
            mismatched.append({"point_id": point_id, "fields": differing})
    if mismatched:
        reasons.append(f"payload 불일치 {len(mismatched)}개 (예: {mismatched[:2]})")
    missing = len(set(expected) - existing)
    state = "conflict" if reasons else ("complete" if missing == 0 else "partial")
    return {"state": state, "existing_points": len(existing), "missing": missing, "reasons": reasons}


# ---------------------------------------------------------------------------
# 3. 계획 (dry-run)
# ---------------------------------------------------------------------------
def plan_full_index(name: str, *, documents: list[dict[str, Any]], policy_metadata: dict[str, dict[str, Any]],
                    client=None, proposals_dir: Path | str = C1_PROPOSALS_DIR) -> dict[str, Any]:
    """구성 하나의 dry-run 계획. client가 있으면 대상 컬렉션을 읽기 전용으로 검사한다. 아무것도 쓰지 않는다."""
    config = get_config(name)
    chunks, info = build_config_chunks(name, documents, proposals_dir)
    texts = [em.build_embedding_text(chunk, policy_metadata[chunk["policy_id"]], EMBEDDING_INPUT_RULE) for chunk in chunks]
    point_ids = [chunk["point_id"] for chunk in chunks]
    uuid_ok = all(chunk["point_id"] == chunking.make_point_id(chunk["chunk_id"]) for chunk in chunks)
    by_id = documents_by_id(documents)
    restore_mismatch = sum(chunk["text"] != by_id[chunk["document_id"]]["text"][chunk["char_start"]:chunk["char_end"]]
                           for chunk in chunks)
    all_documents = {document["document_id"] for document in documents}
    all_policies = {document["policy_id"] for document in documents}
    covered_documents = {chunk["document_id"] for chunk in chunks}
    covered_policies = {chunk["policy_id"] for chunk in chunks}
    content_hashes = {document["document_id"]: document["content_hash"] for document in documents}
    model, dim = config_model(config)
    inspection = (inspect_collection(client, config["collection"], chunks, content_hashes, model, dim)
                  if client is not None else None)
    total_chars = sum(len(text) for text in texts)
    plan = {
        "config": name, "collection": config["collection"], "strategy": config["strategy"],
        "model": model, "dim": dim, "distance": DISTANCE, "embedding_input_rule": EMBEDDING_INPUT_RULE,
        "query_template": QUERY_TEMPLATE, "manifest_sha256": manifest_sha256(),
        "chunks": len(chunks), "expected_chunks": config["expected_chunks"],
        "chunk_count_matches": len(chunks) == config["expected_chunks"],
        "versions": dict(sorted(Counter(chunk["chunking_version"] for chunk in chunks).items())),
        "unique_point_ids": len(set(point_ids)) == len(point_ids), "point_id_is_uuid5_chunk_id": uuid_ok,
        "restore_mismatch": restore_mismatch,
        "missing_documents": sorted(all_documents - covered_documents),
        "missing_policies": sorted(all_policies - covered_policies),
        "documents": len(covered_documents), "policies": len(covered_policies),
        "embedding_input_chars": total_chars, "estimated_tokens": int(total_chars / CHARS_PER_TOKEN),
        "max_input_chars": max(len(text) for text in texts),
        "chunk_fingerprint": chunk_fingerprint(chunks), "c1": info["c1"],
        "protected_collections_untouched": list(PROTECTED_COLLECTIONS),
        "collection_state": inspection,
    }
    plan["ready"] = (plan["chunk_count_matches"] and plan["unique_point_ids"] and uuid_ok and restore_mismatch == 0
                     and not plan["missing_documents"] and not plan["missing_policies"]
                     and (inspection is None or inspection["state"] != "conflict"))
    return plan


def git_state() -> dict[str, Any]:
    """실행 기록용 Git 상태: HEAD, 추적 파일 변경 여부, 미추적 파일 수 (내용은 기록하지 않는다)."""
    import subprocess

    def run(*args):
        return subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True).stdout.strip()
    return {"head": run("rev-parse", "HEAD"), "tracked_dirty": bool(run("status", "--porcelain", "--untracked-files=no")),
            "untracked": run("status", "--porcelain").count("??")}


def documents_by_id(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {document["document_id"]: document for document in documents}


# ---------------------------------------------------------------------------
# 4. 실행 (승인 후) · 검증
# ---------------------------------------------------------------------------
def execute_full_index(name: str, *, client, embedder, documents: list[dict[str, Any]],
                       policy_metadata: dict[str, dict[str, Any]], confirm_write: bool = False,
                       run_id: str | None = None, proposals_dir: Path | str = C1_PROPOSALS_DIR,
                       chunks: list[dict[str, Any]] | None = None, batch_size: int = EMBED_BATCH) -> dict[str, Any]:
    """
    구성 하나를 색인한다. confirm_write=False면 FullIndexError (쓰지 않음).
    conflict면 중단, complete면 쓰지 않음, absent/partial이면 없는 point만 배치 단위로 임베딩·upsert한다.
    chunks: 테스트용으로 청크 목록을 직접 넘길 수 있다 (기본은 build_config_chunks).
    """
    if not confirm_write:
        raise FullIndexError("confirm_write=True 없이 DB에 쓰지 않습니다 (dry-run은 plan_full_index).")
    config = get_config(name)
    if chunks is None:
        chunks, _info = build_config_chunks(name, documents, proposals_dir)
    content_hashes = {document["document_id"]: document["content_hash"] for document in documents}
    model, dim = config_model(config)
    before = inspect_collection(client, config["collection"], chunks, content_hashes, model, dim)
    if before["state"] == "conflict":
        raise FullIndexError(f"{config['collection']} 충돌 → 쓰지 않음: {before['reasons']}")
    run_info = make_run_info(name, run_id)
    written, embed_seconds, upsert_seconds = 0, 0.0, 0.0
    if before["state"] != "complete":
        store.ensure_collection(client, config["collection"], dim, DISTANCE)
        existing = store.list_point_ids(client, config["collection"])
        todo = [chunk for chunk in chunks if chunk["point_id"] not in existing]
        for start in range(0, len(todo), batch_size):
            batch = todo[start:start + batch_size]
            texts = [em.build_embedding_text(c, policy_metadata[c["policy_id"]], EMBEDDING_INPUT_RULE) for c in batch]
            started = time.perf_counter()
            vectors = embedder(texts)
            embed_seconds += time.perf_counter() - started
            points = store.build_points(batch, vectors, policy_metadata=policy_metadata,
                                        content_hashes=content_hashes, run_info=run_info)
            started = time.perf_counter()
            written += store.upsert_points(client, config["collection"], points)
            upsert_seconds += time.perf_counter() - started
    verification = verify_full_index(name, client=client, chunks=chunks, documents=documents)
    return {"config": name, "collection": config["collection"], "state_before": before, "written": written,
            "run_info": run_info, "embedding_seconds": round(embed_seconds, 3),
            "upsert_seconds": round(upsert_seconds, 3), "verification": verification}


def verify_full_index(name: str, *, client, chunks: list[dict[str, Any]], documents: list[dict[str, Any]]) -> dict[str, Any]:
    """적재 결과 검증 (읽기 전용): 건수·id 집합·payload(원문 슬라이스·위치·해시·모델·규칙)·문서/정책 포함."""
    config = get_config(name)
    content_hashes = {document["document_id"]: document["content_hash"] for document in documents}
    by_id = documents_by_id(documents)
    model, dim = config_model(config)
    inspection = inspect_collection(client, config["collection"], chunks, content_hashes, model, dim)
    payloads = _retrieve_payloads(client, config["collection"], [chunk["point_id"] for chunk in chunks])
    slice_mismatch = sum(1 for payload in payloads.values()
                         if by_id[payload["document_id"]]["text"][payload["char_start"]:payload["char_end"]] != payload.get("text"))
    documents_present = {payload["document_id"] for payload in payloads.values()}
    policies_present = {payload["policy_id"] for payload in payloads.values()}
    count = store.count_points(client, config["collection"])
    result = {"state": inspection["state"], "count": count, "expected": len(chunks),
              "id_set_equal": store.list_point_ids(client, config["collection"]) == {c["point_id"] for c in chunks},
              "payload_slice_mismatch": slice_mismatch,
              "missing_documents": sorted({d["document_id"] for d in documents} - documents_present),
              "missing_policies": sorted({d["policy_id"] for d in documents} - policies_present),
              "reasons": inspection["reasons"]}
    result["passed"] = (inspection["state"] == "complete" and count == len(chunks) and result["id_set_equal"]
                        and slice_mismatch == 0 and not result["missing_documents"] and not result["missing_policies"])
    return result


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="전체 색인 (기본 dry-run)")
    parser.add_argument("--config", action="append", choices=tuple(FULL_INDEX_CONFIGS), required=True)
    parser.add_argument("--no-db", action="store_true", help="Qdrant에 접속하지 않고 청크 계획만 계산")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--record", action="store_true", help="계획·결과를 RECORD_DIR에 JSON으로 남긴다")
    args = parser.parse_args(argv)
    documents = handoff_data.load_documents(verify_hash=True)
    bad = [r["file"] for r in handoff_data.verify_manifest_checksums() if not r["matched"]]
    if bad:
        raise FullIndexError(f"manifest 체크섬 불일치: {bad}")
    policy_metadata = handoff_data.load_policy_metadata()
    client = None if args.no_db else store.get_client()
    outputs = []
    for name in args.config:
        plan = plan_full_index(name, documents=documents, policy_metadata=policy_metadata, client=client)
        output: dict[str, Any] = {"mode": "dry-run", "plan": plan, "created_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        if args.execute:
            if not args.confirm_write:
                raise FullIndexError("--execute 에는 --confirm-write 가 함께 필요합니다.")
            if not plan["ready"]:
                raise FullIndexError(f"{name} 계획 검사 실패 → 쓰지 않음")
            model, dim = config_model(get_config(name))
            metrics: list[dict[str, Any]] = []
            embedder = em.make_ollama_embedder(model=model, expected_dim=dim, metrics_sink=metrics)
            output = {"mode": "execute", "plan": plan,
                      "result": execute_full_index(name, client=client, embedder=embedder, documents=documents,
                                                   policy_metadata=policy_metadata, confirm_write=True),
                      "ollama_metrics": em.summarize_ollama_metrics(metrics),
                      "git": git_state(), "created_at": output["created_at"]}
        outputs.append(output)
        if args.record:
            RECORD_DIR.mkdir(parents=True, exist_ok=True)
            (RECORD_DIR / f"{output['mode']}_{name}.json").write_text(json.dumps(output, ensure_ascii=False, indent=2),
                                                                     encoding="utf-8")
    print(json.dumps(outputs, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
