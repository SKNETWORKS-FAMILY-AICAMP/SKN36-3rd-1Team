"""Issue #91 2단계: r1 벡터 snapshot을 복원한 격리 컬렉션의 부분 갱신 — 기본은 읽기 전용 dry-run.

전제: 새(빈) Docker 프로젝트·볼륨의 Qdrant에 B2 snapshot(1,138 Point)만 복원되어 있다.
벡터는 snapshot 그대로 두고(재정규화 없음) 다음만 바꾼다.
  1. 재사용 1,135 Point: payload를 최신 build_point_payload 값으로 교체 (overwrite_payload, 벡터 불변)
  2. 본문이 바뀐 기존 3 Point: 고정 ID·해시 확인 후 삭제
  3. 이후 기존 full_index(A_B2_V140) execute가 없는 4 Point(3 + 신규 1)만 임베딩한다 (이 스크립트는 하지 않음)

기준값은 scripts/seed_b2_from_r1_vectors.py의 덤프 검증(SHA-256·r1 vector_digest·실행 정보)과 분류를 재사용한다.
덤프 벡터와 복원 컬렉션 벡터는 Point마다 정확히 같아야 한다(허용 오차 없음).

상태 (inspect_state, 실제 Point를 읽어 판정)
  payload_pending  : 1,138개, 벡터 원본 그대로, 최신 payload가 아닌 재사용 Point 있음 → --apply 가능
  delete_pending   : 재사용 payload 최신화 완료, 삭제 대상 일부·전부 남음 → --apply 가능 (이어서 삭제)
  ready_for_index  : 1,135개, payload 최신, 벡터 원본 그대로 → full_index execute 차례
  index_partial    : 새 Point 일부 적재 → full_index execute 재실행 (없는 Point만 이어서 적재)
  complete         : 1,139개 → full_index 검증·복원 검증 차례
  그 밖(unexpected_points, missing_reused_points, vector_mismatch, delete_target_mismatch, unexpected_order,
        unexpected_mix) → 쓰지 않고 멈춘다. 복구는 그 볼륨을 버리고 새 볼륨에 snapshot부터 다시 복원한다.

실행 대상 보호 (Issue #91 3단계)
  - Qdrant 주소는 --qdrant-url 인자로만 정한다. QDRANT_URL 환경 변수·.env는 읽지 않는다.
    API 키는 이 프로세스의 환경 변수 QDRANT_API_KEY에서만 읽는다(.env 미사용, 출력 안 함).
  - loopback + 명시적 전용 포트(6333·6334 거부), 서버 버전 1.12.4, 컬렉션이 대상 하나뿐이어야 한다.
  - dry-run이 출력한 fingerprint(서버 버전·컬렉션·Point·payload·벡터 digest)를 --expect-fingerprint로
    넘겨야 쓴다. 쓰기 직전 같은 클라이언트로 다시 계산해 다르면 쓰지 않는다.
  - 4개 임베딩(--index)은 검증한 같은 클라이언트를 full_index.execute_full_index에 넘긴다.
    Ollama 주소도 --ollama-url 인자(loopback)로만 정하며, 예상 개수를 넘는 임베딩 요청은 거부한다.

실행 (프로젝트 루트, .venv). 전용 포트는 docs/development/issue91_r2_qdrant.compose.yaml의 56446 하나만 허용:
    python scripts/update_b2_from_snapshot.py --qdrant-url http://127.0.0.1:56446            # dry-run
    python scripts/update_b2_from_snapshot.py --qdrant-url ... --restore-snapshot --confirm-write   # 빈 서버에만
    python scripts/update_b2_from_snapshot.py --qdrant-url ... --apply --confirm-write --expect-fingerprint <값>
    python scripts/update_b2_from_snapshot.py --qdrant-url ... --index --confirm-write --expect-fingerprint <값> \\
        --ollama-url http://127.0.0.1:11434
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import embedding as em, full_index as fi  # noqa: E402
from database.qdrant import store  # noqa: E402
from scripts import restore_final_v140 as restore  # noqa: E402
from scripts import seed_b2_from_r1_vectors as seed  # noqa: E402

COLLECTION = restore.COLLECTION              # full_index A_B2_V140·복원 도구와 같은 이름이어야 한다
DIMENSIONS = restore.SNAPSHOT_DIMENSIONS
FINAL_POINTS = 1139
# 기본 포트는 기존 공유 로컬 DB로 보고 거부한다. #91 전용 compose(docs/development/issue91_r2_qdrant.compose.yaml)의
# 호스트 포트 하나만 허용한다.
SHARED_QDRANT_PORTS = frozenset({6333, 6334})
ISOLATED_QDRANT_PORT = 56446
# pr29 snapshot(b2-v1.12.4.snapshot)을 만든 서버 버전. 다른 버전 서버에는 쓰지 않는다.
EXPECTED_QDRANT_VERSION = "1.12.4"
API_KEY_ENV = "QDRANT_API_KEY"
# pr29 번들의 B2 snapshot (ZIP·풀어 둔 파일·SHA256SUMS 모두 같은 값). 읽기만 한다.
DEFAULT_SNAPSHOT = ROOT / "venv" / "issue31_20261109" / "rag-local-v140-pr29-20261109" / "b2-v1.12.4.snapshot"
SNAPSHOT_SHA256 = "4f7a67ea9b087d5a19da805c6ceb150ba98b11eee580e34b18f40488c4f89c3c"
SNAPSHOT_POINTS = seed.DUMP_POINTS
UPLOAD_TIMEOUT_SECONDS = 300

# 본문 변경으로 삭제 후 다시 임베딩할 기존 Point. 덤프 기준 본문 SHA-256과 단일 벡터 digest(restore.digest).
DELETE_TARGETS: dict[str, dict[str, str]] = {
    "5ed6279a-8add-5157-87f4-3341944495ab": {
        "chunk_id": "CHK_B2_DOC_BOK_054_C002",
        "text_sha256": "14dc3c9e8f2d138d896a7c065273ba2010c7f9972d7877f916cdc03bc5f8d057",
        "vector_digest": "dd4eec05252893fae3eed4f2c218fa3f7cfe80de4eafabd80a7360e91a319f14"},
    "51273d15-dac7-58e2-8e1c-c27f5e5dad86": {
        "chunk_id": "CHK_B2_DOC_BOK_054_C003",
        "text_sha256": "6173604f5153c02f7c3540f7edf8bce72efd6f915a5ad2a608579358c77afe94",
        "vector_digest": "fee030c802026c935da4179fb32a3b9e17e7e8215551389e79890fa63c6b4fa4"},
    "6d4ff9df-64b7-5c79-89d2-3f343af1b745": {
        "chunk_id": "CHK_B2_DOC_BOK_054_C004",
        "text_sha256": "98577fbeda279e892434df3652d74830593cbac7515f49bd157a3136c16fff0e",
        "vector_digest": "b405de40e128c4a12cae045d88387ba613dcd2a3e00c34355775f6cafa744191"},
}
NEW_POINT_IDS = frozenset({"7fa02bbf-4c6c-5d8f-b314-6f94d9c34b14"})   # CHK_B2_DOC_BOK_031_MOE_2026_C001
# 재사용 1,135 Point 벡터의 부분 digest (dry-run 계획 reuse_vector_subset_digest)
REUSE_SUBSET_DIGEST = "6c6672ca028797455755d19388b1d703c6b7a0cdfd18a3093f30d7d34731b34c"
PINS = {"delete": DELETE_TARGETS, "new": NEW_POINT_IDS, "reuse_digest": REUSE_SUBSET_DIGEST, "total": FINAL_POINTS}

WRITABLE_STATES = frozenset({"payload_pending", "delete_pending"})


class UpdateError(ValueError):
    """갱신할 수 없음. 메시지에 벡터 값·비밀값을 넣지 않는다."""


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 1. 대상 보호
# ---------------------------------------------------------------------------
def require_isolated_url(url: str) -> str:
    """자격 증명 없는 loopback 주소이면서 #91 전용 포트(56446)만 허용한다 (6333·6334 등 다른 포트 거부)."""
    restore.require_local_url(url)
    port = urlsplit(url).port
    if port is None or port in SHARED_QDRANT_PORTS or port != ISOLATED_QDRANT_PORT:
        raise UpdateError(f"#91 전용 compose 포트 {ISOLATED_QDRANT_PORT}만 허용합니다 (기본 포트 6333·6334 거부)")
    return url


def read_api_key(environ=None) -> str:
    """API 키는 이 프로세스의 환경 변수에서만 읽는다 (.env 미사용). 값은 출력하지 않는다."""
    api_key = (environ if environ is not None else os.environ).get(API_KEY_ENV, "").strip()
    if not api_key:
        raise UpdateError(f"{API_KEY_ENV}를 이 프로세스의 환경 변수로 설정하세요 (.env는 읽지 않음)")
    return api_key


def make_isolated_client(url: str, environ=None):
    """--qdrant-url 그대로 접속하는 클라이언트. QDRANT_URL·.env는 보지 않고, API 키는 프로세스 환경 변수만 쓴다."""
    require_isolated_url(url)
    api_key = read_api_key(environ)
    from qdrant_client import QdrantClient
    return QdrantClient(url=url, api_key=api_key, timeout=30)


def server_collections(client) -> list[str]:
    """서버 버전이 1.12.4인지 확인하고 컬렉션 이름 목록을 돌려준다 (읽기 전용)."""
    version = getattr(client.info(), "version", None)
    if version != EXPECTED_QDRANT_VERSION:
        raise UpdateError(f"Qdrant 서버 버전 {version} ≠ {EXPECTED_QDRANT_VERSION}")
    return sorted(collection.name for collection in client.get_collections().collections)


def require_isolated_server(client) -> list[str]:
    """서버 버전이 1.12.4이고 대상 컬렉션 하나만 있어야 한다 (기존·공유 DB에는 다른 컬렉션이 있으므로 거부된다)."""
    server_collections(client)
    store.assert_experiment_collection(COLLECTION)
    config = fi.get_config(seed.CONFIG)
    if config["collection"] != COLLECTION or COLLECTION in fi.PROTECTED_COLLECTIONS:
        raise UpdateError("대상 컬렉션이 A_B2_V140 구성과 다르거나 보호 컬렉션입니다")
    names = sorted(collection.name for collection in client.get_collections().collections)
    if names != [COLLECTION]:
        raise UpdateError(f"격리 서버가 아닙니다: 컬렉션 {len(names)}개 (대상 하나만 있어야 함)")
    params = client.get_collection(COLLECTION).config.params.vectors
    if params.size != DIMENSIONS or str(params.distance).split(".")[-1].lower() != "cosine":
        raise UpdateError("대상 컬렉션 차원·거리가 1024·Cosine이 아닙니다")
    return names


def server_fingerprint(client, state: dict[str, Any]) -> str:
    """dry-run에서 본 서버 상태의 지문. 쓰기 직전 다시 계산해 같아야 쓴다 (사이에 서버·데이터가 바뀌면 거부)."""
    return restore.digest({"qdrant_version": getattr(client.info(), "version", None),
                           "collections": sorted(c.name for c in client.get_collections().collections),
                           "state": state["state"], "points": state["points"],
                           "full_vector_digest": state["full_vector_digest"],
                           "payload_digest": state["payload_digest"]})


def require_fingerprint(client, state: dict[str, Any], expected: str | None) -> None:
    if not expected:
        raise UpdateError("dry-run이 출력한 fingerprint를 expected_fingerprint로 넘겨야 씁니다")
    if server_fingerprint(client, state) != expected:
        raise UpdateError("서버 fingerprint가 dry-run과 다릅니다 (대상 서버·데이터가 바뀜) → 쓰지 않음")


def restore_snapshot(client, url: str, snapshot: Path | str, confirm_write: bool = False, environ=None,
                     http_post=None, expected_sha256: str = SNAPSHOT_SHA256,
                     expected_points: int = SNAPSHOT_POINTS) -> dict[str, Any]:
    """
    빈 격리 서버(1.12.4, 컬렉션 0개)에만 pr29 B2 snapshot을 업로드해 대상 컬렉션을 만든다.
    priority=snapshot은 같은 이름 컬렉션의 기존 데이터보다 snapshot을 우선하므로, 컬렉션이 하나라도 있으면 거부한다.
    업로드 전 파일 SHA-256을 확인하고 같은 값을 checksum으로 보내 서버도 검증하게 한다. snapshot 파일은 읽기만 한다.
    """
    if not confirm_write:
        raise UpdateError("confirm_write=True 없이 snapshot을 올리지 않습니다")
    require_isolated_url(url)
    api_key = read_api_key(environ)
    snapshot = Path(snapshot)
    if not snapshot.is_file() or restore.file_sha(snapshot) != expected_sha256:
        raise UpdateError("snapshot 파일이 없거나 SHA-256이 고정값과 다릅니다")
    names = server_collections(client)
    if names:
        raise UpdateError(f"빈 서버가 아닙니다: 컬렉션 {len(names)}개 → snapshot을 올리지 않음")
    if http_post is None:
        import requests
        http_post = requests.post
    with snapshot.open("rb") as stream:
        response = http_post(f"{url.rstrip('/')}/collections/{COLLECTION}/snapshots/upload",
                             params={"wait": "true", "priority": "snapshot", "checksum": expected_sha256},
                             headers={"api-key": api_key}, files={"snapshot": (snapshot.name, stream)},
                             timeout=UPLOAD_TIMEOUT_SECONDS)
    status = getattr(response, "status_code", None)
    if status != 200:
        # 응답 본문·헤더는 출력하지 않는다 (요청 헤더의 키가 섞이지 않도록 상태 코드만 남긴다).
        raise UpdateError(f"snapshot 업로드 실패: HTTP {status} → 이 볼륨은 보존하고 새 프로젝트로 다시 시작")
    after = server_collections(client)
    count = client.count(collection_name=COLLECTION, exact=True).count if after == [COLLECTION] else None
    if after != [COLLECTION] or count != expected_points:
        raise UpdateError(f"복원 후 컬렉션·Point 수 이상: 컬렉션 {after}, Point {count}")
    return {"status": "RESTORED", "collection": COLLECTION, "points": count, "snapshot_sha256": expected_sha256}


# ---------------------------------------------------------------------------
# 2. 기준값 (덤프 + 최신 릴리스, DB 없음)
# ---------------------------------------------------------------------------
def reference_from_plan(plan: dict[str, Any], records: dict[str, dict[str, Any]], release: dict[str, Any],
                        pins: dict[str, Any] = PINS) -> dict[str, Any]:
    """seed 분류 결과를 갱신 기준으로 바꾸고 삭제·신규 ID, 삭제 대상 해시, 재사용 벡터 digest를 고정값과 대조한다."""
    reuse = {item["point_id"]: item["payload"] for item in plan["reuse"]}
    embed = {item["point_id"] for item in plan["embed"]}
    delete = {point_id for point_id in embed if point_id in records}
    new = embed - delete
    if delete != set(pins["delete"]) or new != set(pins["new"]):
        raise UpdateError(f"삭제·신규 대상이 고정값과 다릅니다: 삭제 {sorted(delete)}, 신규 {sorted(new)}")
    for point_id, pin in pins["delete"].items():
        payload, vector = records[point_id]["payload"], records[point_id]["vector"]
        if (payload.get("chunk_id") != pin["chunk_id"] or text_sha(payload.get("text", "")) != pin["text_sha256"]
                or restore.digest([(point_id, vector)]) != pin["vector_digest"]):
            raise UpdateError(f"삭제 대상 해시가 고정값과 다릅니다: {point_id}")
    if len(reuse) + len(embed) != pins["total"]:
        raise UpdateError(f"최종 Point 수 {len(reuse) + len(embed)} ≠ {pins['total']}")
    vectors = {point_id: records[point_id]["vector"] for point_id in [*reuse, *delete]}
    if restore.vector_digest({p: {"vector": vectors[p]} for p in reuse}) != pins["reuse_digest"]:
        raise UpdateError("재사용 벡터 부분 digest가 고정값과 다릅니다")
    return {"reuse_payloads": reuse, "delete": delete, "new": new, "vectors": vectors,
            "delete_pins": pins["delete"], "reuse_digest": pins["reuse_digest"], "total": pins["total"],
            "chunks": release["chunks"], "content_hashes": release["content_hashes"],
            "documents": release.get("documents"), "metadata": release.get("metadata")}


def build_reference(dump_path: Path | str = seed.DEFAULT_DUMP) -> dict[str, Any]:
    """1단계 검증을 그대로 거친다: r1 기록 → 덤프 SHA-256·형식 → vector_digest → 실행 정보 → 릴리스 → 분류."""
    seed.check_r1_record()
    records = seed.load_dump(dump_path)
    seed.check_vector_digest(records)
    seed.check_dump_run(records)
    release = seed.load_release()
    plan = seed.classify(records, release["chunks"], release["metadata"], release["content_hashes"])
    seed.validate_plan(plan, records)
    return reference_from_plan(plan, records, release)


# ---------------------------------------------------------------------------
# 3. 상태 판정 (읽기 전용)
# ---------------------------------------------------------------------------
def inspect_state(client, ref: dict[str, Any]) -> dict[str, Any]:
    """컬렉션 전체(벡터 포함)를 읽어 갱신 단계를 판정한다. 아무것도 쓰지 않는다."""
    records = restore.scan(client)
    ids = set(records)
    reuse, delete, new = set(ref["reuse_payloads"]), ref["delete"], ref["new"]
    result: dict[str, Any] = {"points": len(ids), "payload_pending": [], "delete_present": [], "reindexed": [],
                              "missing_for_index": [], "problems": []}
    unexpected = sorted(ids - reuse - delete - new)
    missing = sorted(reuse - ids)
    mismatch = sorted(p for p in ids & reuse if records[p]["vector"] != ref["vectors"][p])
    stale = {p for p in ids & delete if records[p]["vector"] == ref["vectors"][p]}
    bad_targets = sorted(p for p in stale
                         if records[p]["payload"].get("chunk_id") != ref["delete_pins"][p]["chunk_id"]
                         or text_sha(records[p]["payload"].get("text", "")) != ref["delete_pins"][p]["text_sha256"])
    reindexed = (ids & delete - stale) | (ids & new)
    pending = sorted(p for p in ids & reuse if records[p]["payload"] != ref["reuse_payloads"][p])
    present_reuse = {p: records[p] for p in ids & reuse}
    result.update({
        "payload_pending": pending, "delete_present": sorted(stale), "reindexed": sorted(reindexed),
        "reuse_vector_digest": restore.vector_digest(present_reuse),
        "full_vector_digest": restore.vector_digest(records),
        "payload_digest": restore.digest([(p, records[p]["payload"]) for p in sorted(records)]),
        "payload_pending_fields": _field_counts(records, ref, pending)})
    for name, values in (("unexpected_points", unexpected), ("missing_reused_points", missing),
                         ("vector_mismatch", mismatch), ("delete_target_mismatch", bad_targets)):
        if values:
            result["problems"].append({"type": name, "count": len(values), "examples": values[:5]})
    if result["problems"]:
        state = result["problems"][0]["type"]
    elif stale and reindexed:
        state = "unexpected_mix"
    elif not reindexed:
        if stale == delete:
            state = "payload_pending" if pending else "delete_pending"
        elif pending:
            state = "unexpected_order"          # 삭제는 payload 최신화가 끝난 뒤에만 한다
        else:
            state = "delete_pending" if stale else "ready_for_index"
    elif pending:
        state = "unexpected_order"
    else:
        state = "complete" if reindexed == delete | new and len(ids) == ref["total"] else "index_partial"
    if state in ("ready_for_index", "index_partial"):
        result["missing_for_index"] = sorted((delete | new) - reindexed)
    result["state"] = state
    return result


def _field_counts(records, ref, pending) -> dict[str, int]:
    counts: dict[str, int] = {}
    for point_id in pending:
        old, new = records[point_id]["payload"], ref["reuse_payloads"][point_id]
        for field in set(old) | set(new):
            if old.get(field) != new.get(field):
                counts[field] = counts.get(field, 0) + 1
    return dict(sorted(counts.items()))


def check_full_index_ready(client, ref: dict[str, Any], expected_missing: int | None = None) -> dict[str, Any]:
    """기존 full_index.inspect_collection이 partial(없는 Point = 삭제 3 + 신규 1)로 판정하는지 확인한다 (읽기 전용).
    expected_missing: index_partial 재실행이면 남은 개수, 생략하면 처음 상태의 4개."""
    inspection = fi.inspect_collection(client, COLLECTION, ref["chunks"], ref["content_hashes"])
    if expected_missing is None:
        expected_missing = len(ref["delete"]) + len(ref["new"])
    if inspection["state"] != "partial" or inspection["missing"] != expected_missing or inspection["reasons"]:
        raise UpdateError(f"full_index가 '없는 {expected_missing}개만 적재' 상태로 보지 않습니다: "
                          f"{inspection['state']}, missing={inspection['missing']}")
    return inspection


# ---------------------------------------------------------------------------
# 4. 쓰기 (--apply --confirm-write 일 때만)
# ---------------------------------------------------------------------------
def delete_points(client, point_ids: list[str], allowed: set[str]) -> list[str]:
    """허용된 삭제 대상만, 지금 실제로 있는 Point만 삭제한다. 하나라도 어긋나면 아무것도 지우지 않는다."""
    targets = sorted(set(point_ids))
    if not targets or not set(targets) <= set(allowed):
        raise UpdateError(f"삭제 허용 대상이 아닙니다: {sorted(set(targets) - set(allowed)) or '빈 목록'}")
    present = {str(record.id) for record in client.retrieve(collection_name=COLLECTION, ids=targets,
                                                            with_payload=False, with_vectors=False)}
    if present != set(targets):
        raise UpdateError(f"존재하지 않는 Point는 삭제하지 않습니다: {sorted(set(targets) - present)}")
    client.delete(collection_name=COLLECTION, points_selector=targets, wait=True)
    return targets


def apply_updates(client, ref: dict[str, Any], confirm_write: bool = False,
                  expected_fingerprint: str | None = None) -> dict[str, Any]:
    """payload 최신화 → 상태 재확인 → 삭제 → 상태 재확인. 벡터는 바꾸지 않는다. 컬렉션 삭제·초기화는 없다.
    쓰기 직전 같은 클라이언트로 서버·상태를 다시 검증하고 dry-run fingerprint와 대조한다."""
    if not confirm_write:
        raise UpdateError("confirm_write=True 없이 쓰지 않습니다")
    require_isolated_server(client)
    state = inspect_state(client, ref)
    if state["state"] == "ready_for_index":
        return {"status": "ALREADY_READY", "payload_updates": 0, "deleted": [], "state": state["state"]}
    if state["state"] not in WRITABLE_STATES:
        raise UpdateError(f"쓰기 전 상태가 {state['state']}이므로 중단합니다")
    require_fingerprint(client, state, expected_fingerprint)
    before = state["reuse_vector_digest"]
    for point_id in state["payload_pending"]:
        client.overwrite_payload(collection_name=COLLECTION, payload=ref["reuse_payloads"][point_id],
                                 points=[point_id], wait=True)
    middle = inspect_state(client, ref)
    if middle["state"] not in ("delete_pending", "ready_for_index") or middle["reuse_vector_digest"] != before:
        raise UpdateError(f"payload 최신화 후 상태 이상: {middle['state']}")
    deleted = delete_points(client, middle["delete_present"], ref["delete"]) if middle["delete_present"] else []
    after = inspect_state(client, ref)
    if after["state"] != "ready_for_index" or after["reuse_vector_digest"] != before:
        raise UpdateError(f"삭제 후 상태 이상: {after['state']}")
    if before != ref["reuse_digest"]:
        raise UpdateError("재사용 벡터 digest가 고정값과 다릅니다")
    return {"status": "UPDATED", "payload_updates": len(state["payload_pending"]), "deleted": deleted,
            "state": after["state"], "reuse_vector_digest": before, "missing_for_index": after["missing_for_index"]}


def limited_embedder(embedder, limit: int):
    """누적 임베딩 입력이 limit개를 넘으면 호출 전에 거부한다 (예상 밖 대량 재임베딩 방지)."""
    used = {"texts": 0}

    def embed(texts):
        if used["texts"] + len(texts) > limit:
            raise UpdateError(f"임베딩 요청이 예상 {limit}개를 넘습니다 → 중단")
        used["texts"] += len(texts)
        return embedder(texts)
    embed.used = used
    return embed


def index_missing(client, ref: dict[str, Any], embedder, confirm_write: bool = False,
                  expected_fingerprint: str | None = None) -> dict[str, Any]:
    """
    검증한 같은 클라이언트로 기존 execute_full_index를 실행해 없는 Point(최대 4개)만 임베딩·적재한다.
    쓰기 직전: 격리 서버 → 상태(ready_for_index/index_partial) → fingerprint → full_index partial 판정을 확인한다.
    """
    if not confirm_write:
        raise UpdateError("confirm_write=True 없이 쓰지 않습니다")
    if not ref.get("documents") or ref.get("metadata") is None:
        raise UpdateError("기준값에 릴리스 문서·메타데이터가 없습니다")
    require_isolated_server(client)
    state = inspect_state(client, ref)
    if state["state"] not in ("ready_for_index", "index_partial"):
        raise UpdateError(f"임베딩 전 상태가 {state['state']}이므로 중단합니다")
    require_fingerprint(client, state, expected_fingerprint)
    missing = state["missing_for_index"]
    check_full_index_ready(client, ref, expected_missing=len(missing))
    guarded = limited_embedder(embedder, len(missing))
    result = fi.execute_full_index(seed.CONFIG, client=client, embedder=guarded, documents=ref["documents"],
                                   policy_metadata=ref["metadata"], confirm_write=True, chunks=ref["chunks"])
    final = inspect_state(client, ref)
    if (result["written"] != len(missing) or not result["verification"]["passed"] or final["state"] != "complete"
            or final["reuse_vector_digest"] != ref["reuse_digest"]):
        raise UpdateError(f"임베딩 후 검증 실패: state={final['state']}, written={result['written']}")
    return {"status": "INDEXED", "written": result["written"], "embedded_texts": guarded.used["texts"],
            "run_info": result["run_info"], "verification": result["verification"], "state": final["state"],
            "reuse_vector_digest": final["reuse_vector_digest"]}


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------
NEXT_STEPS = {
    "payload_pending": "--apply --confirm-write --expect-fingerprint <이 fingerprint> 로 payload 최신화와 3개 삭제",
    "delete_pending": "--apply --confirm-write --expect-fingerprint <이 fingerprint> 로 남은 삭제 대상 삭제",
    "ready_for_index": "--index --confirm-write --expect-fingerprint <이 fingerprint> --ollama-url <loopback> "
                       "(같은 클라이언트로 full_index 실행, 4개만 임베딩. full_index CLI 직접 실행은 쓰지 않는다)",
    "index_partial": "같은 --index 명령을 새 fingerprint로 다시 실행 (없는 Point만 이어서 적재)",
    "complete": "읽기 전용 확인 후 Neo4j 적재·교차 검증·r2 패키징 단계로",
}


def summarize(state: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
    return {"state": state["state"], "points": state["points"], "problems": state["problems"],
            "payload_pending": len(state["payload_pending"]), "payload_pending_fields": state["payload_pending_fields"],
            "delete_present": [{"point_id": p, "chunk_id": ref["delete_pins"][p]["chunk_id"]}
                               for p in state["delete_present"]],
            "reindexed": state["reindexed"], "missing_for_index": state["missing_for_index"],
            "reuse_vector_digest": state["reuse_vector_digest"],
            "reuse_vector_digest_matches": state["reuse_vector_digest"] == ref["reuse_digest"],
            "full_vector_digest": state["full_vector_digest"],
            "full_vector_digest_matches_r1": state["full_vector_digest"] == seed.R1_VECTOR_DIGEST,
            "next": NEXT_STEPS.get(state["state"], "중단: 이 볼륨은 쓰지 말고 새 볼륨에 snapshot부터 다시 복원")}


def dry_run(client, ref: dict[str, Any]) -> dict[str, Any]:
    """읽기 전용: 서버 확인 → 상태 판정 → fingerprint 출력. 쓰기·임베딩 0회."""
    if server_collections(client) == []:
        return {"mode": "dry-run", "db_writes": 0, "embedding_calls": 0, "qdrant_version": EXPECTED_QDRANT_VERSION,
                "collections": [], "state": "empty_server", "points": 0,
                "next": "--restore-snapshot --confirm-write 로 pr29 B2 snapshot 복원 (빈 서버에서만 허용)"}
    names = require_isolated_server(client)
    state = inspect_state(client, ref)
    output: dict[str, Any] = {"mode": "dry-run", "db_writes": 0, "embedding_calls": 0,
                              "qdrant_version": getattr(client.info(), "version", None), "collections": names,
                              **summarize(state, ref), "fingerprint": server_fingerprint(client, state)}
    if state["state"] in ("ready_for_index", "index_partial"):
        output["full_index_inspection"] = {k: v for k, v in check_full_index_ready(
            client, ref, expected_missing=len(state["missing_for_index"])).items() if k != "reasons"}
    return output


def run(client, ref: dict[str, Any], mode: str = "dry-run", confirm_write: bool = False,
        expected_fingerprint: str | None = None, embedder=None, url: str | None = None,
        snapshot: Path | str | None = None) -> dict[str, Any]:
    if mode == "dry-run":
        return dry_run(client, ref)
    if mode == "restore-snapshot":
        result = restore_snapshot(client, url, snapshot, confirm_write)
    elif mode == "apply":
        result = apply_updates(client, ref, confirm_write, expected_fingerprint)
    elif mode == "index":
        if embedder is None:
            raise UpdateError("--index 에는 임베딩 함수가 필요합니다")
        result = index_missing(client, ref, embedder, confirm_write, expected_fingerprint)
    else:
        raise UpdateError(f"알 수 없는 실행 모드: {mode}")
    after = dry_run(client, ref)
    return {**after, "mode": mode, "db_writes": None, "result": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="snapshot 복원 B2 컬렉션 부분 갱신 (기본 dry-run)")
    parser.add_argument("--qdrant-url", required=True, help="전용 compose 프로젝트의 loopback 주소 (명시적 포트)")
    parser.add_argument("--dump", type=Path, default=seed.DEFAULT_DUMP, help="기준 덤프 JSON (읽기만 함)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--restore-snapshot", nargs="?", const=DEFAULT_SNAPSHOT, type=Path, metavar="SNAPSHOT",
                      help="빈 격리 서버에만 pr29 B2 snapshot 업로드 (기본: pr29 번들의 b2-v1.12.4.snapshot)")
    mode.add_argument("--apply", action="store_true", help="payload 최신화 + 삭제 대상 3개 삭제")
    mode.add_argument("--index", action="store_true", help="없는 Point만 임베딩 (같은 클라이언트로 full_index)")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--expect-fingerprint", help="직전 dry-run이 출력한 fingerprint")
    parser.add_argument("--ollama-url", help="--index 전용 Ollama loopback 주소 (.env·OLLAMA_BASE_URL 미사용)")
    parser.add_argument("--report", type=Path, help="결과를 저장할 새 JSON 경로 (이미 있으면 거부)")
    args = parser.parse_args(argv)
    if args.restore_snapshot and not args.confirm_write:
        parser.error("--restore-snapshot 에는 --confirm-write 가 필요합니다")
    writing = args.apply or args.index
    if writing and not (args.confirm_write and args.expect_fingerprint):
        parser.error("--apply·--index 에는 --confirm-write 와 --expect-fingerprint 가 함께 필요합니다")
    if args.index and not args.ollama_url:
        parser.error("--index 에는 --ollama-url 이 필요합니다")
    if args.report and args.report.exists():
        parser.error("이미 있는 보고서는 덮어쓰지 않습니다")
    client = None
    try:
        url = require_isolated_url(args.qdrant_url)
        embedder = None
        if args.index:
            model, dim = fi.config_model(fi.get_config(seed.CONFIG))
            embedder = em.make_ollama_embedder(model=model, expected_dim=dim,
                                               base_url=restore.require_local_url(args.ollama_url))
        ref = build_reference(args.dump)
        client = make_isolated_client(url)
        mode_name = ("restore-snapshot" if args.restore_snapshot else "apply" if args.apply
                     else "index" if args.index else "dry-run")
        result = {"status": "PASS", **run(client, ref, mode_name, args.confirm_write, args.expect_fingerprint,
                                          embedder, url=url, snapshot=args.restore_snapshot)}
    except (UpdateError, seed.SeedPlanError, ValueError) as error:
        result = {"status": "FAILED", "reason": str(error),
                  "action": "쓰기 상태를 dry-run으로 다시 확인한다. 판정이 중단 상태이면 이 볼륨을 버리고 snapshot부터 다시 복원한다."}
    finally:
        if client is not None:
            client.close()
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        with args.report.open("x", encoding="utf-8") as stream:
            stream.write(text + "\n")
    print(text)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
