"""
최소 벡터 적재·검색 흐름 (실험용).

  정제 문서 로드 → 선택한 문서만 청킹(A/B1/B2) → 정책 메타데이터 조인 → 임베딩 → 실험 컬렉션 적재
  → (같은 데이터 재적재 → 중복 확인) → 질문 벡터 검색

모든 외부 의존성(임베딩 함수, QdrantClient)은 인자로 받는다.
→ 테스트에서는 가짜 임베딩 + Qdrant 메모리 모드로, 실제 실행에서는 Ollama + Qdrant 서버로 같은 코드를 쓴다.
이 모듈을 import하는 것만으로는 어떤 서버에도 접속하지 않는다.
"""

import time
from typing import Any

from database import chunking
from database import handoff_data
from database.embedding import Embedder, build_embedding_text, build_query_text
from database.qdrant import store

# 최소 구축 대표 문서 후보 (2026-11-05 계획 보고 기준, 확정 아님)
DEFAULT_SAMPLE_DOCUMENT_IDS = ("DOC_LH_001", "DOC_NHUF_003", "DOC_BOK_001", "DOC_YC_003", "DOC_GOV_006")
STRATEGIES = ("A", "B1", "B2")


# ---------------------------------------------------------------------------
# 1. 문서 선택·청크 준비
# ---------------------------------------------------------------------------
def select_documents(documents: list[dict[str, Any]], document_ids: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
    """document_ids 순서대로 문서를 고른다. 없는 ID가 있으면 ValueError (추측해서 건너뛰지 않는다)."""
    by_id = {document["document_id"]: document for document in documents}
    missing = [document_id for document_id in document_ids if document_id not in by_id]
    if missing:
        raise ValueError(f"documents.jsonl에 없는 문서: {missing}")
    return [by_id[document_id] for document_id in document_ids]


def prepare_a_chunks(documents: list[dict[str, Any]], payload_chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    A안(기존 인계 청크 557건) 중 선택 문서의 청크를 B안과 같은 모양으로 맞춘다. 원본 dict는 바꾸지 않는다.
    - chunking_version = "A"
    - char_start/char_end: 원문에서 위치를 찾아 채운다(locate_chunks_in_document). 못 찾으면 None.
    - page_start/page_end: 페이지가 확인된 문서만 숫자(get_verified_page_number), 나머지는 None.
    """
    prepared = []
    for document in documents:
        own = [chunk for chunk in payload_chunks if chunk["document_id"] == document["document_id"]]
        positions = chunking.locate_chunks_in_document(document["text"], [chunk["text"] for chunk in own])
        for chunk, position in zip(own, positions):
            page = handoff_data.get_verified_page_number(chunk)
            prepared.append({
                **chunk,
                "chunking_version": "A",
                "char_start": position[0] if position else None,
                "char_end": position[1] if position else None,
                "page_start": page,
                "page_end": page,
            })
    return prepared


def prepare_chunks(
    documents: list[dict[str, Any]], strategy: str, payload_chunks: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """선택 문서의 청크를 만든다. B1/B2는 기존 chunk_documents를 그대로 호출하고, A는 인계 청크를 쓴다."""
    if strategy not in STRATEGIES:
        raise ValueError(f"알 수 없는 청킹 전략: {strategy} {STRATEGIES}")
    if strategy == "A":
        return prepare_a_chunks(documents, payload_chunks if payload_chunks is not None
                                else handoff_data.load_payload_chunks())
    chunks, _reports = chunking.chunk_documents(documents, strategy=strategy)
    return chunks


# ---------------------------------------------------------------------------
# 2. 적재
# ---------------------------------------------------------------------------
def index_chunks(
    chunks: list[dict[str, Any]],
    *,
    client,
    collection: str,
    embedder: Embedder,
    policy_metadata: dict[str, dict[str, Any]],
    content_hashes: dict[str, str],
    run_info: dict[str, Any],
) -> dict[str, Any]:
    """
    청크 → 임베딩 입력 문자열 → 벡터 → point → upsert 를 한 번 수행한다.
    반환: {"chunk_count", "upserted", "embedding_seconds", "upsert_seconds", "point_ids"}
    """
    store.assert_experiment_collection(collection)
    texts = [build_embedding_text(chunk, policy_metadata.get(chunk["policy_id"]), run_info["embedding_input_rule"])
             for chunk in chunks]

    started = time.perf_counter()
    vectors = embedder(texts)
    embedding_seconds = time.perf_counter() - started

    points = store.build_points(chunks, vectors, policy_metadata=policy_metadata,
                                content_hashes=content_hashes, run_info=run_info)
    started = time.perf_counter()
    upserted = store.upsert_points(client, collection, points)
    upsert_seconds = time.perf_counter() - started
    return {"chunk_count": len(chunks), "upserted": upserted,
            "embedding_seconds": round(embedding_seconds, 3), "upsert_seconds": round(upsert_seconds, 3),
            "point_ids": {point["id"] for point in points}}


def run_minimal_index(
    document_ids: list[str] | tuple[str, ...],
    *,
    strategy: str,
    collection: str,
    client,
    embedder: Embedder,
    run_info: dict[str, Any],
    documents: list[dict[str, Any]] | None = None,
    payload_chunks: list[dict[str, Any]] | None = None,
    verify_idempotent: bool = True,
) -> dict[str, Any]:
    """
    최소 적재 흐름 전체. 실험 컬렉션이 없으면 만들고(차원·거리 확인), 선택 문서만 적재한다.
    verify_idempotent=True 이면 같은 데이터를 한 번 더 적재해 point 수와 ID 집합이 그대로인지 확인한다.

    반환 보고 dict:
      collection, collection_status(created/exists), strategy, document_ids, run_info,
      chunk_count, points_before, points_after_first, points_after_second,
      id_set_unchanged, duplicates_found, first_pass, second_pass(없으면 None)
    """
    store.assert_experiment_collection(collection)
    if documents is None:
        documents = handoff_data.load_documents()
    if payload_chunks is None:
        payload_chunks = handoff_data.load_payload_chunks()
    selected = select_documents(documents, document_ids)
    chunks = prepare_chunks(selected, strategy, payload_chunks)
    policy_metadata = handoff_data.load_policy_metadata(payload_chunks)
    content_hashes = {document["document_id"]: document["content_hash"] for document in selected}

    collection_status = store.ensure_collection(client, collection, run_info["embedding_dim"])
    scope = {"document_id": list(document_ids)}
    points_before = store.count_points(client, collection, scope)

    arguments = dict(client=client, collection=collection, embedder=embedder, policy_metadata=policy_metadata,
                     content_hashes=content_hashes, run_info=run_info)
    first = index_chunks(chunks, **arguments)
    points_after_first = store.count_points(client, collection, scope)
    ids_after_first = store.list_point_ids(client, collection, scope)

    second, points_after_second, id_set_unchanged = None, None, None
    if verify_idempotent:
        second = index_chunks(chunks, **arguments)
        points_after_second = store.count_points(client, collection, scope)
        id_set_unchanged = store.list_point_ids(client, collection, scope) == ids_after_first

    expected = len(first["point_ids"])
    duplicates_found = points_after_first > expected or (points_after_second is not None
                                                        and points_after_second != points_after_first)
    return {
        "collection": collection, "collection_status": collection_status, "strategy": strategy,
        "document_ids": list(document_ids), "run_info": dict(run_info), "chunk_count": len(chunks),
        "points_before": points_before, "points_after_first": points_after_first,
        "points_after_second": points_after_second, "id_set_unchanged": id_set_unchanged,
        "duplicates_found": duplicates_found,
        "first_pass": {key: value for key, value in first.items() if key != "point_ids"},
        "second_pass": {key: value for key, value in second.items() if key != "point_ids"} if second else None,
    }


# ---------------------------------------------------------------------------
# 3. 검색
# ---------------------------------------------------------------------------
def run_smoke_search(
    questions: list[dict[str, Any]],
    *,
    client,
    collection: str,
    embedder: Embedder,
    query_template: str | None,
    documents: list[dict[str, Any]] | None = None,
    k: int = 5,
) -> list[dict[str, Any]]:
    """
    질문마다 Top-k 청크를 검색하고, 결과가 원문과 맞는지 확인한다.

    입력 questions: [{"question": "…", "expected_document_id": "DOC_…"}, ...]
      expected_document_id는 질문을 그 문서 원문에서 만들었다는 표시다. 성능 평가 정답셋이 아니다.
    query_template: 질의 지시문 사용 여부. None(질문 그대로) 또는 "{question}"이 들어간 문자열. 호출하는 쪽이 정한다.
    반환: 질문별 {"question", "expected_document_id", "expected_in_top_k", "latency_seconds",
                  "hits": [{rank, score, chunk_id, policy_id, document_id, text_matches_original}]}
      text_matches_original: payload text == 원문[char_start:char_end] (위치가 없으면 None)
    """
    store.assert_experiment_collection(collection)
    by_id = {document["document_id"]: document for document in (documents or [])}
    results = []
    for item in questions:
        query_text = build_query_text(item["question"], query_template)
        started = time.perf_counter()
        query_vector = embedder([query_text])[0]
        hits = store.search_chunks(client, collection, query_vector, k=k)
        latency = time.perf_counter() - started

        checked_hits = []
        for hit in hits:
            payload = hit["payload"]
            document = by_id.get(hit["document_id"])
            matches = None
            if document is not None and payload.get("char_start") is not None:
                matches = document["text"][payload["char_start"]:payload["char_end"]] == payload["text"]
            checked_hits.append({"rank": hit["rank"], "score": hit["score"], "chunk_id": hit["chunk_id"],
                                 "policy_id": hit["policy_id"], "document_id": hit["document_id"],
                                 "section_title": payload.get("section_title"),
                                 "text_matches_original": matches})
        results.append({
            "question": item["question"],
            "expected_document_id": item.get("expected_document_id"),
            "expected_in_top_k": (item.get("expected_document_id") in {hit["document_id"] for hit in hits}
                                  if item.get("expected_document_id") else None),
            "latency_seconds": round(latency, 3),
            "hits": checked_hits,
        })
    return results


def make_run_info(
    *, embedding_model: str, embedding_dim: int, embedding_input_rule: str, index_run_id: str
) -> dict[str, Any]:
    """payload에 남길 실행 정보 dict. 값을 빠뜨리지 않도록 이름 있는 인자로만 받는다."""
    return {"embedding_model": embedding_model.strip(), "embedding_dim": embedding_dim,
            "embedding_input_rule": embedding_input_rule, "index_run_id": index_run_id}
