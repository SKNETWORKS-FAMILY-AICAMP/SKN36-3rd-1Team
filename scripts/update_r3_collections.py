"""Issue #105: r3 격리 Qdrant(build 프로젝트)의 B2·B3 컬렉션 부분 갱신 — 기본 dry-run.

r2 update_b2_from_snapshot의 단계·보호 장치를 컬렉션별로 일반화했다.

단계 (컬렉션마다 따로, --collection b2|b3)
  0. absent        : --restore-snapshot --confirm-write  원본 snapshot(R3_SOURCES)을 격리 서버에 업로드
                     (대상 컬렉션이 없고 서버에는 r3 컬렉션만 있을 때만. 복원 직후 Point 수·전체 벡터 digest를 원본 기록과 대조)
  1. source        : dry-run --write-plan <새 파일>      서버 scan으로 재사용 계획 작성 (DB 쓰기 없음)
                     → 계획·proposed_pins 검토 → R3_PINS 확정 (사람이 기록)
  2. payload_pending/delete_pending : --apply --confirm-write --expect-fingerprint  payload 최신화 → 삭제 대상 삭제
  3. ready_for_index/index_partial : --index  --confirm-write --expect-fingerprint --ollama-url
                     같은 클라이언트로 기존 execute_full_index 실행, 없는 Point만 임베딩 (누적 상한 = 남은 개수 ≤ 고정 embed_count)
  4. complete      : 읽기 전용 확인 → Neo4j 적재·교차 검증(load_neo4j_r3_isolated) → snapshot·번들(build_restore_bundle_r3)

보호 장치
  - 주소: http://127.0.0.1:<build qdrant_port>만. 공유·r1·r2 포트(FOREIGN_PORTS) 거부. QDRANT_URL·.env 미사용
  - 인증: ISSUE105_R3_BUILD_QDRANT_API_KEY만. 원본·공유·r2 인증값과 같으면 거부. 값은 출력하지 않는다
  - Docker 소유: 쓰기 전(그리고 dry-run fingerprint에) build 프로필 프로젝트(현재 issue105-r3b-build)의 qdrant·neo4j 서비스가 정확히 하나씩,
    실행 중, 고정 이미지 digest, 127.0.0.1 바인딩만, 프로젝트 전용 볼륨만인지 확인한다(restore_r2_isolated.verify_docker_ownership).
    다른 프로젝트가 같은 포트를 쓰거나, 프로젝트 이름이 같아도 볼륨이 다르면 거부. 쓰기 후 식별 정보가 바뀌어도 실패
  - 서버: Qdrant 1.12.4, 컬렉션은 r3 두 이름만(그 밖의 컬렉션이 하나라도 있으면 공유·기존 DB로 보고 거부), 1024·Cosine
  - 쓰기: --confirm-write 필수. 2·3단계는 R3_PINS 확정 + build 서버 기준 계획(basis=server_snapshot)이 고정값과 일치
    + dry-run fingerprint(서버 상태 + Docker 식별 정보) 일치일 때만. 원본 snapshot 복원(0단계)만 R3_PINS 없이 허용
  - 삭제: 계획에 기록된 chunk_id·본문 SHA-256·벡터 SHA-256이 모두 같은 Point만 지운다 (하나라도 다르면 아무것도 지우지 않음)
  - 컬렉션 삭제·초기화·재생성은 없다. 실패하면 볼륨을 보존하고 새 프로젝트에서 snapshot부터 다시 시작한다
"""
from __future__ import annotations

import argparse
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
from scripts import plan_r3_vector_reuse as planner  # noqa: E402
from scripts import restore_final_v140 as restore  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402
from scripts import restore_r2_isolated as r2w  # noqa: E402  (Docker 소유 확인 함수 재사용, 읽기 전용 docker ps·inspect)

ENV = profiles.environment("build")
BUILD_PROJECT = ENV["project"]
BUILD_SERVICES = profiles.services_for("build")
ISOLATED_QDRANT_PORT = ENV["qdrant_port"]
API_KEY_ENV = ENV["qdrant_key_env"]
R3_COLLECTION_NAMES = frozenset(spec["collection"] for spec in profiles.R3_COLLECTIONS.values())
UPLOAD_TIMEOUT_SECONDS = 300
WRITABLE_STATES = frozenset({"payload_pending", "delete_pending"})


class UpdateError(ValueError):
    """갱신할 수 없음. 메시지에 벡터 값·비밀값을 넣지 않는다."""


# ---------------------------------------------------------------------------
# 1. 대상 보호
# ---------------------------------------------------------------------------
def require_isolated_url(url: str, port: int = ISOLATED_QDRANT_PORT) -> str:
    restore.require_local_url(url)
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.port is None or parts.port in profiles.FOREIGN_PORTS or parts.port != port:
        raise UpdateError(f"r3 build 프로젝트 Qdrant 포트 {port}만 허용합니다 (공유·r2 포트 거부)")
    if parts.path not in ("", "/") or parts.query:
        raise UpdateError("주소에 경로·쿼리를 넣지 않습니다")
    return url


def read_secret(name: str, environ=None) -> str:
    """이 프로세스의 전용 환경 변수에서만 읽는다(.env 미사용). 원본·공유·r2 값과 같으면 거부. 값은 출력하지 않는다."""
    environ = os.environ if environ is None else environ
    value = environ.get(name, "")
    if not value.strip():
        raise UpdateError(f"{name}를 이 프로세스의 환경 변수로 설정하세요 (.env는 읽지 않음)")
    reused = [other for other in profiles.FOREIGN_SECRET_ENVS if environ.get(other) and environ.get(other) == value]
    if reused:
        raise UpdateError(f"원본·공유·r2 인증값을 재사용했습니다 (변수 이름: {reused})")
    return value


def require_build_ownership(docker=None) -> dict[str, Any]:
    """
    build 프로젝트 소유 확인 (읽기 전용 docker ps·inspect). 실패하면 UpdateError로 쓰기 전에 멈춘다.
    반환: 서비스별 {컨테이너 ID, 이미지, 시작 시각, 볼륨} — fingerprint에 넣어 쓰기 직전·직후 같은지 대조한다.
    """
    try:
        return r2w.verify_docker_ownership(docker or r2w.run_docker, project=BUILD_PROJECT, services=BUILD_SERVICES)
    except r2w.IsolationError as error:
        raise UpdateError(f"build Docker 소유 확인 실패 → 쓰지 않음: {error}") from error
    except Exception as error:      # docker 미설치·응답 형식 오류 등도 확인 실패로 본다 (fail-closed)
        raise UpdateError(f"build Docker 소유 확인 실패 → 쓰지 않음: {type(error).__name__}") from error


def require_same_ownership(identity: dict[str, Any], docker=None) -> None:
    if require_build_ownership(docker) != identity:
        raise UpdateError("쓰기 중 build 컨테이너 식별 정보가 바뀌었습니다 (부분 상태 보존, 재실행 전 dry-run으로 확인)")


def make_isolated_client(url: str, environ=None):
    require_isolated_url(url)
    api_key = read_secret(API_KEY_ENV, environ)
    from qdrant_client import QdrantClient
    return QdrantClient(url=url, api_key=api_key, timeout=30)


def server_collections(client) -> list[str]:
    version = getattr(client.info(), "version", None)
    if version != profiles.QDRANT_VERSION:
        raise UpdateError(f"Qdrant 서버 버전 {version} ≠ {profiles.QDRANT_VERSION}")
    return sorted(c.name for c in client.get_collections().collections)


def require_isolated_server(client) -> list[str]:
    """r3 컬렉션 외의 컬렉션·별칭이 있으면 거부. 있는 r3 컬렉션은 1024·Cosine이어야 한다."""
    names = server_collections(client)
    foreign = sorted(set(names) - R3_COLLECTION_NAMES)
    if foreign:
        raise UpdateError(f"격리 서버가 아닙니다: r3가 아닌 컬렉션 {len(foreign)}개")
    get_aliases = getattr(client, "get_aliases", None)
    if get_aliases is not None and list(get_aliases().aliases):
        raise UpdateError("격리 서버에 별칭이 있습니다")
    for name in names:
        params = client.get_collection(name).config.params.vectors
        if params.size != profiles.EMBEDDING_DIM or str(params.distance).split(".")[-1].lower() != "cosine":
            raise UpdateError(f"{name} 차원·거리가 1024·Cosine이 아닙니다")
    return names


def scan_collection(client, collection: str) -> dict[str, dict[str, Any]]:
    records, offset = {}, None
    while True:
        points, offset = client.scroll(collection, offset=offset, limit=128, with_payload=True, with_vectors=True)
        records.update({str(p.id): {"payload": p.payload or {}, "vector": p.vector} for p in points})
        if offset is None:
            return records


# ---------------------------------------------------------------------------
# 2. 원본 snapshot 복원 ([서버 쓰기] 대상 컬렉션이 없을 때만)
# ---------------------------------------------------------------------------
def restore_source_snapshot(client, url: str, key: str, snapshot: Path | str, confirm_write: bool = False,
                            environ=None, http_post=None, sources: dict[str, Any] | None = None,
                            docker=None) -> dict[str, Any]:
    """
    원본 snapshot(B2 = r2 snapshot, B3 = #31 snapshot)을 같은 이름의 새 컬렉션으로 올린다. R3_PINS 없이 허용되는 유일한 서버 쓰기.
    build Docker 소유 확인 → priority=snapshot은 같은 이름 컬렉션을 덮어쓰므로 대상이 이미 있으면 거부(다른 r3 컬렉션만 있는 것은 허용)
    → 파일 SHA-256 확인 + 같은 값을 checksum으로 보내 서버도 검증 → 복원 직후 Point 수·전체 벡터 digest·Docker 식별 정보 대조.
    """
    if not confirm_write:
        raise UpdateError("confirm_write=True 없이 snapshot을 올리지 않습니다")
    require_isolated_url(url)
    identity = require_build_ownership(docker)
    spec, source = profiles.collection_spec(key), (sources or profiles.R3_SOURCES)[key]
    api_key = read_secret(API_KEY_ENV, environ)
    snapshot = Path(snapshot)
    if not snapshot.is_file() or restore.file_sha(snapshot) != source["snapshot_sha256"]:
        raise UpdateError(f"{key} 원본 snapshot이 없거나 SHA-256이 기록과 다릅니다")
    names = require_isolated_server(client)
    if spec["collection"] in names:
        raise UpdateError(f"{spec['collection']}가 이미 있습니다 → snapshot을 올리지 않음 (덮어쓰기 금지)")
    if http_post is None:
        import requests
        http_post = requests.post
    with snapshot.open("rb") as stream:
        response = http_post(f"{url.rstrip('/')}/collections/{spec['collection']}/snapshots/upload",
                             params={"wait": "true", "priority": "snapshot", "checksum": source["snapshot_sha256"]},
                             headers={"api-key": api_key}, files={"snapshot": (snapshot.name, stream)},
                             timeout=UPLOAD_TIMEOUT_SECONDS)
    status = getattr(response, "status_code", None)
    if status != 200:
        raise UpdateError(f"snapshot 업로드 실패: HTTP {status} → 이 볼륨은 보존하고 새 프로젝트로 다시 시작")
    require_same_ownership(identity, docker)
    after = require_isolated_server(client)
    if spec["collection"] not in after:
        raise UpdateError("복원 후 대상 컬렉션이 없습니다")
    records = scan_collection(client, spec["collection"])
    digest = restore.vector_digest(records)
    if len(records) != source["points"] or digest != source["vector_digest"]:
        raise UpdateError(f"{key} 복원 결과가 원본 기록과 다릅니다 (Point {len(records)}개, "
                          f"digest 일치={digest == source['vector_digest']}) → 쓰지 말고 원인 확인")
    return {"status": "RESTORED", "collection": spec["collection"], "points": len(records),
            "vector_digest": digest, "snapshot_sha256": source["snapshot_sha256"], "docker_project": BUILD_PROJECT}


# ---------------------------------------------------------------------------
# 3. 상태 판정 (읽기 전용)
# ---------------------------------------------------------------------------
def expected_reuse_payloads(plan: dict[str, Any], release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    chunks = {c["point_id"]: c for c in release["collections"][plan["collection_key"]]["chunks"]}
    return {p: planner.reuse_payload(chunks[p], release["metadata"], release["content_hashes"], item["run"])
            for p, item in plan["reuse"].items()}


def target_matches(record: dict[str, Any], original: dict[str, Any], point_id: str) -> bool:
    """삭제 대상이 계획에 기록된 원본 그대로인지: chunk_id·본문 SHA-256·벡터 SHA-256 모두 같아야 한다."""
    payload = record.get("payload") or {}
    return (payload.get("chunk_id") == original.get("chunk_id")
            and original.get("text_sha256") is not None
            and planner.text_sha(payload.get("text", "")) == original["text_sha256"]
            and planner.point_vector_sha(point_id, record.get("vector")) == original.get("vector_sha"))


def is_source_state(records: dict[str, dict[str, Any]], key: str, sources: dict[str, Any] | None = None) -> bool:
    source = (sources or profiles.R3_SOURCES)[key]
    return len(records) == source["points"] and restore.vector_digest(records) == source["vector_digest"]


def inspect_state(client, plan: dict[str, Any], release: dict[str, Any],
                  expected: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """컬렉션 전체(벡터 포함)를 읽어 계획 대비 단계를 판정한다. 아무것도 쓰지 않는다."""
    records = scan_collection(client, plan["collection"])
    expected = expected if expected is not None else expected_reuse_payloads(plan, release)
    ids = set(records)
    reuse, delete, new = set(plan["reuse"]), set(plan["delete"]) | set(plan["remove"]), set(plan["new"])
    originals = {**plan["delete"], **plan["remove"]}
    sha = {p: planner.point_vector_sha(p, records[p]["vector"]) for p in ids}
    unexpected = sorted(ids - reuse - delete - new)
    missing = sorted(reuse - ids)
    mismatch = sorted(p for p in ids & reuse if sha[p] != plan["reuse"][p]["vector_sha"])
    stale = {p for p in ids & delete if sha[p] == originals[p]["vector_sha"]}
    # 삭제 대상 확인은 r2와 같은 기준: 벡터 SHA(stale 판정) + chunk_id + 본문 SHA-256
    bad_targets = sorted(p for p in stale if not target_matches(records[p], originals[p], p))
    reindexed = (ids & set(plan["delete"]) - stale) | (ids & new)
    leftover_remove = ids & set(plan["remove"]) - stale
    pending = sorted(p for p in ids & reuse if records[p]["payload"] != expected[p])
    present_reuse = {p: records[p] for p in ids & reuse}
    result: dict[str, Any] = {
        "collection_key": plan["collection_key"], "points": len(ids), "payload_pending": pending,
        "delete_present": sorted(stale), "reindexed": sorted(reindexed), "missing_for_index": [], "problems": [],
        "reuse_vector_digest": restore.vector_digest(present_reuse),
        "full_vector_digest": restore.vector_digest(records),
        "payload_digest": restore.digest([(p, records[p]["payload"]) for p in sorted(records)])}
    for name, values in (("unexpected_points", unexpected), ("missing_reused_points", missing),
                         ("vector_mismatch", mismatch), ("delete_target_mismatch", bad_targets),
                         ("removed_point_changed", sorted(leftover_remove))):
        if values:
            result["problems"].append({"type": name, "count": len(values), "examples": values[:5]})
    wanted_index = set(plan["delete"]) | new
    if result["problems"]:
        state = result["problems"][0]["type"]
    elif stale and reindexed:
        state = "unexpected_mix"
    elif not reindexed:
        if stale == delete:                     # 삭제 대상이 모두 남아 있음 (삭제 대상이 없을 수도 있다)
            state = "payload_pending" if pending else ("delete_pending" if stale else "ready_for_index")
        elif pending:
            state = "unexpected_order"          # 삭제는 payload 최신화가 끝난 뒤에만 한다
        else:
            state = "delete_pending" if stale else "ready_for_index"
    elif pending:
        state = "unexpected_order"
    else:
        state = "complete" if reindexed == wanted_index and len(ids) == plan["target_points"] else "index_partial"
    if state in ("ready_for_index", "index_partial"):
        result["missing_for_index"] = sorted(wanted_index - reindexed)
    result["state"] = state
    return result


def server_fingerprint(client, state: dict[str, Any], identity: dict[str, Any]) -> str:
    """dry-run에서 본 서버 상태 + build Docker 식별 정보의 지문. 쓰기 직전 다시 계산해 같아야 쓴다."""
    return restore.digest({"qdrant_version": getattr(client.info(), "version", None),
                           "collections": sorted(c.name for c in client.get_collections().collections),
                           "collection_key": state["collection_key"], "state": state["state"],
                           "points": state["points"], "full_vector_digest": state["full_vector_digest"],
                           "payload_digest": state["payload_digest"],
                           "docker": {"project": BUILD_PROJECT, "services": identity}})


def require_fingerprint(client, state: dict[str, Any], expected: str | None, identity: dict[str, Any]) -> None:
    if not expected:
        raise UpdateError("dry-run이 출력한 fingerprint를 expected_fingerprint로 넘겨야 씁니다")
    if server_fingerprint(client, state, identity) != expected:
        raise UpdateError("서버 fingerprint가 dry-run과 다릅니다 (대상 서버·데이터·컨테이너가 바뀜) → 쓰지 않음")


def check_full_index_ready(client, plan: dict[str, Any], release: dict[str, Any], expected_missing: int) -> dict:
    """기존 full_index.inspect_collection이 partial(없는 Point = expected_missing개)로 판정하는지 확인 (읽기 전용)."""
    entry = release["collections"][plan["collection_key"]]
    inspection = fi.inspect_collection(client, plan["collection"], entry["chunks"], release["content_hashes"],
                                       entry["spec"]["model"], entry["spec"]["dimensions"])
    if inspection["state"] != "partial" or inspection["missing"] != expected_missing or inspection["reasons"]:
        raise UpdateError(f"full_index가 '없는 {expected_missing}개만 적재' 상태로 보지 않습니다: "
                          f"{inspection['state']}, missing={inspection['missing']}")
    return inspection


# ---------------------------------------------------------------------------
# 4. 쓰기 (확정된 R3_PINS + 일치하는 계획 + fingerprint)
# ---------------------------------------------------------------------------
def require_write_ready(plan: dict[str, Any], release: dict[str, Any], pins: dict[str, Any] | None) -> None:
    """
    갱신 단계 쓰기 전 공통 조건: R3_PINS 확정(최종 vector_digest 두 칸만 아직 미확정 허용 — 임베딩 후에 정해짐),
    build 서버 기준 계획이 현재 릴리스·원본·고정값(plan_sha256 포함)과 일치.
    """
    planner.validate_plan(plan, release, pins, require_pins=True, deferred=profiles.UPDATE_STAGE_DEFERRED_PINS)


def delete_points(client, collection: str, point_ids: list[str], originals: dict[str, dict[str, Any]]) -> list[str]:
    """
    계획의 삭제 대상(originals)만, 지금 서버의 chunk_id·본문 SHA-256·벡터 SHA-256이 계획과 모두 같을 때만 지운다.
    하나라도 없거나 다르면 아무것도 지우지 않고 멈춘다.
    """
    targets = sorted(set(point_ids))
    if not targets or not set(targets) <= set(originals):
        raise UpdateError(f"삭제 허용 대상이 아닙니다: {sorted(set(targets) - set(originals)) or '빈 목록'}")
    current = {str(r.id): {"payload": r.payload or {}, "vector": r.vector}
               for r in client.retrieve(collection_name=collection, ids=targets, with_payload=True, with_vectors=True)}
    if set(current) != set(targets):
        raise UpdateError(f"존재하지 않는 Point는 삭제하지 않습니다: {sorted(set(targets) - set(current))}")
    mismatched = sorted(p for p in targets if not target_matches(current[p], originals[p], p))
    if mismatched:
        raise UpdateError(f"삭제 대상의 chunk_id·본문·벡터가 계획과 다릅니다 → 삭제하지 않음: {mismatched[:5]}")
    client.delete(collection_name=collection, points_selector=targets, wait=True)
    return targets


def apply_updates(client, plan: dict[str, Any], release: dict[str, Any], confirm_write: bool = False,
                  expected_fingerprint: str | None = None, pins: dict[str, Any] | None = None,
                  docker=None) -> dict[str, Any]:
    """payload 최신화 → 상태 재확인 → 삭제(계획의 delete·remove만, 원본 확인 후) → 상태 재확인. 벡터는 바꾸지 않는다."""
    if not confirm_write:
        raise UpdateError("confirm_write=True 없이 쓰지 않습니다")
    require_write_ready(plan, release, pins)
    identity = require_build_ownership(docker)
    require_isolated_server(client)
    expected = expected_reuse_payloads(plan, release)
    state = inspect_state(client, plan, release, expected)
    if state["state"] == "ready_for_index":
        return {"status": "ALREADY_READY", "payload_updates": 0, "deleted": [], "state": state["state"]}
    if state["state"] not in WRITABLE_STATES:
        raise UpdateError(f"쓰기 전 상태가 {state['state']}이므로 중단합니다")
    require_fingerprint(client, state, expected_fingerprint, identity)
    before = state["reuse_vector_digest"]
    for point_id in state["payload_pending"]:
        client.overwrite_payload(collection_name=plan["collection"], payload=expected[point_id],
                                 points=[point_id], wait=True)
    middle = inspect_state(client, plan, release, expected)
    if middle["state"] not in ("delete_pending", "ready_for_index") or middle["reuse_vector_digest"] != before:
        raise UpdateError(f"payload 최신화 후 상태 이상: {middle['state']}")
    originals = {**plan["delete"], **plan["remove"]}
    deleted = (delete_points(client, plan["collection"], middle["delete_present"], originals)
               if middle["delete_present"] else [])
    after = inspect_state(client, plan, release, expected)
    if after["state"] != "ready_for_index" or after["reuse_vector_digest"] != before:
        raise UpdateError(f"삭제 후 상태 이상: {after['state']}")
    if before != plan["reuse_vector_digest"]:
        raise UpdateError("재사용 벡터 digest가 계획과 다릅니다")
    require_same_ownership(identity, docker)
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


def index_missing(client, plan: dict[str, Any], release: dict[str, Any], embedder, confirm_write: bool = False,
                  expected_fingerprint: str | None = None, pins: dict[str, Any] | None = None,
                  docker=None) -> dict[str, Any]:
    """검증한 같은 클라이언트로 기존 execute_full_index를 실행해 없는 Point만 임베딩·적재한다."""
    if not confirm_write:
        raise UpdateError("confirm_write=True 없이 쓰지 않습니다")
    require_write_ready(plan, release, pins)
    pinned = (pins if pins is not None else profiles.R3_PINS)["collections"][plan["collection_key"]]
    identity = require_build_ownership(docker)
    require_isolated_server(client)
    state = inspect_state(client, plan, release)
    if state["state"] not in ("ready_for_index", "index_partial"):
        raise UpdateError(f"임베딩 전 상태가 {state['state']}이므로 중단합니다")
    require_fingerprint(client, state, expected_fingerprint, identity)
    missing = state["missing_for_index"]
    if len(missing) > pinned["embed_count"]:
        raise UpdateError(f"남은 임베딩 {len(missing)}개가 고정 embed_count {pinned['embed_count']}를 넘습니다")
    check_full_index_ready(client, plan, release, expected_missing=len(missing))
    guarded = limited_embedder(embedder, len(missing))
    entry = release["collections"][plan["collection_key"]]
    result = fi.execute_full_index(entry["spec"]["config"], client=client, embedder=guarded,
                                   documents=release["documents"], policy_metadata=release["metadata"],
                                   confirm_write=True, chunks=entry["chunks"])
    final = inspect_state(client, plan, release)
    if (result["written"] != len(missing) or not result["verification"]["passed"] or final["state"] != "complete"
            or final["reuse_vector_digest"] != plan["reuse_vector_digest"]):
        raise UpdateError(f"임베딩 후 검증 실패: state={final['state']}, written={result['written']}")
    # 2차 확정(stage2)이면 전체 벡터 digest도 확정값과 같아야 한다. 1차 확정(stage1)에서는 아직 None이라 비교하지 않는다.
    if pinned["vector_digest"] is not None and final["full_vector_digest"] != pinned["vector_digest"]:
        raise UpdateError("임베딩 후 전체 vector_digest가 R3_PINS 확정값과 다릅니다 → INDEXED로 보고하지 않음 "
                          f"(실제 {final['full_vector_digest'][:12]}…, 확정 {pinned['vector_digest'][:12]}…)")
    require_same_ownership(identity, docker)
    return {"status": "INDEXED", "written": result["written"], "embedded_texts": guarded.used["texts"],
            "run_info": result["run_info"], "verification": result["verification"], "state": final["state"],
            "full_vector_digest": final["full_vector_digest"]}


# ---------------------------------------------------------------------------
# 5. dry-run · CLI
# ---------------------------------------------------------------------------
NEXT_STEPS = {
    "absent": "--restore-snapshot --confirm-write 로 원본 snapshot 복원 (대상 컬렉션이 없을 때만)",
    "source": "--write-plan <새 파일> 로 계획 저장 → proposed_pins 검토 → R3_PINS 확정 → --plan 으로 다시 dry-run",
    "payload_pending": "--apply --confirm-write --expect-fingerprint <이 fingerprint>",
    "delete_pending": "--apply --confirm-write --expect-fingerprint <이 fingerprint>",
    "ready_for_index": "--index --confirm-write --expect-fingerprint <이 fingerprint> --ollama-url <loopback>",
    "index_partial": "같은 --index 명령을 새 fingerprint로 다시 실행 (없는 Point만 이어서 적재)",
    "complete": "읽기 전용 확인 후 load_neo4j_r3_isolated(적재·교차 검증) → build_restore_bundle_r3",
}


def dry_run(client, key: str, release: dict[str, Any], plan: dict[str, Any] | None = None,
            pins: dict[str, Any] | None = None, docker=None) -> dict[str, Any]:
    """읽기 전용. build Docker 소유 확인(실패하면 중단) 후, 계획이 없으면 원본 상태에서 서버 기준 계획을 만들어 보여 준다."""
    spec = profiles.collection_spec(key)
    identity = require_build_ownership(docker)
    names = require_isolated_server(client)
    output: dict[str, Any] = {"mode": "dry-run", "db_writes": 0, "embedding_calls": 0, "collection_key": key,
                              "collection": spec["collection"], "collections": names,
                              "docker_project": BUILD_PROJECT, "docker": identity,
                              "r3_pins_finalized": profiles.is_finalized(pins)}
    if spec["collection"] not in names:
        return {**output, "state": "absent", "next": NEXT_STEPS["absent"]}
    if plan is None:
        records = scan_collection(client, spec["collection"])
        if not is_source_state(records, key):
            return {**output, "state": "needs_plan", "points": len(records),
                    "next": "원본 상태가 아니므로 계획을 새로 만들 수 없다. 저장한 계획을 --plan 으로 넘긴다"}
        built = planner.build_plan(key, records, release, basis=profiles.PLAN_BASIS_SERVER)
        return {**output, "state": "source", "plan": planner.summarize(built), "plan_object": built,
                "proposed_pins": planner.proposed_pins(release, {key: built})["collections"][key],
                "next": NEXT_STEPS["source"]}
    require_server_plan(plan)
    planner.validate_plan(plan, release, pins)
    state = inspect_state(client, plan, release)
    output.update({k: v for k, v in state.items() if k not in ("payload_pending", "delete_present")})
    output.update({"payload_pending": len(state["payload_pending"]), "delete_present": state["delete_present"],
                   "reuse_vector_digest_matches_plan": state["reuse_vector_digest"] == plan["reuse_vector_digest"],
                   "plan_sha256": planner.plan_sha256(plan),
                   "fingerprint": server_fingerprint(client, state, identity),
                   "next": NEXT_STEPS.get(state["state"], "중단: 이 볼륨은 쓰지 말고 새 프로젝트에서 snapshot부터 다시 복원")})
    if state["state"] == "complete":
        output["proposed_final_pins"] = {"points": state["points"], "vector_digest": state["full_vector_digest"]}
    if state["state"] in ("ready_for_index", "index_partial"):
        inspection = check_full_index_ready(client, plan, release, len(state["missing_for_index"]))
        output["full_index_inspection"] = {k: v for k, v in inspection.items() if k != "reasons"}
    return output


def require_server_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """이 도구는 build 서버에서 만든 계획만 받는다 (덤프 계획 = 사전 검토용, plan_sha256 혼용 금지)."""
    if plan.get("basis") != profiles.PLAN_BASIS_SERVER:
        raise UpdateError(f"build 서버 snapshot 기준 계획(basis=server_snapshot)만 받습니다: basis={plan.get('basis')}")
    return plan


def load_plan(path: Path) -> dict[str, Any]:
    return require_server_plan(json.loads(Path(path).read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="r3 격리 Qdrant B2·B3 부분 갱신 (기본 dry-run)")
    parser.add_argument("--qdrant-url", required=True, help=f"http://127.0.0.1:{ISOLATED_QDRANT_PORT} (그 밖의 주소 거부)")
    parser.add_argument("--collection", required=True, choices=profiles.COLLECTION_KEYS)
    parser.add_argument("--plan", type=Path, help="저장한 재사용 계획 JSON (읽기만 함)")
    parser.add_argument("--write-plan", type=Path, help="원본 상태 dry-run의 계획을 저장할 새 파일 (DB 쓰기 아님)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--restore-snapshot", nargs="?", const="", metavar="SNAPSHOT",
                      help="빈 대상 컬렉션에 원본 snapshot 업로드 (기본: R3_SOURCES 경로)")
    mode.add_argument("--apply", action="store_true", help="payload 최신화 + 계획의 삭제 대상 삭제")
    mode.add_argument("--index", action="store_true", help="없는 Point만 임베딩 (같은 클라이언트로 full_index)")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--expect-fingerprint")
    parser.add_argument("--ollama-url", help="--index 전용 Ollama loopback 주소 (.env·OLLAMA_BASE_URL 미사용)")
    parser.add_argument("--report", type=Path, help="결과를 저장할 새 JSON 경로 (이미 있으면 거부)")
    args = parser.parse_args(argv)
    restoring = args.restore_snapshot is not None
    if restoring and not args.confirm_write:
        parser.error("--restore-snapshot 에는 --confirm-write 가 필요합니다")
    if (args.apply or args.index) and not (args.confirm_write and args.expect_fingerprint and args.plan):
        parser.error("--apply·--index 에는 --plan, --confirm-write, --expect-fingerprint 가 함께 필요합니다")
    if args.index and not args.ollama_url:
        parser.error("--index 에는 --ollama-url 이 필요합니다")
    if args.write_plan and (args.plan or restoring or args.apply or args.index):
        parser.error("--write-plan 은 계획 없는 dry-run에서만 씁니다")
    for path in (args.report, args.write_plan):
        if path and path.exists():
            parser.error("이미 있는 파일은 덮어쓰지 않습니다")
        if path and profiles.is_preserved_output(path):
            parser.error("보존 자산 폴더(#104 r3, .cache/issue105-r3) 안에는 새 파일도 만들지 않습니다")
    client = None
    try:
        url = require_isolated_url(args.qdrant_url)
        if args.apply or args.index:              # 기준값 미확정이면 접속 전에 멈춘다 (함수 안에서도 다시 확인)
            profiles.require_finalized(deferred=profiles.UPDATE_STAGE_DEFERRED_PINS)
        embedder = None
        if args.index:
            spec = profiles.collection_spec(args.collection)
            embedder = em.make_ollama_embedder(model=spec["model"], expected_dim=spec["dimensions"],
                                               base_url=restore.require_local_url(args.ollama_url))
        release = planner.load_release()
        plan = load_plan(args.plan) if args.plan else None
        client = make_isolated_client(url)
        if restoring:
            path = Path(args.restore_snapshot) if args.restore_snapshot else ROOT / profiles.R3_SOURCES[args.collection]["default_path"]
            output = {"result": restore_source_snapshot(client, url, args.collection, path, True)}
            output.update(dry_run(client, args.collection, release))
        elif args.apply:
            output = {"result": apply_updates(client, plan, release, True, args.expect_fingerprint)}
            output.update(dry_run(client, args.collection, release, plan))
        elif args.index:
            output = {"result": index_missing(client, plan, release, embedder, True, args.expect_fingerprint)}
            output.update(dry_run(client, args.collection, release, plan))
        else:
            output = dry_run(client, args.collection, release, plan)
        built = output.pop("plan_object", None)
        if args.write_plan:
            if built is None:
                raise UpdateError("원본 상태가 아니어서 저장할 계획이 없습니다")
            planner.write_new_json(args.write_plan, built)
            output["plan_written"] = str(args.write_plan)
        result = {"status": "PASS", **output}
    except (UpdateError, planner.PlanError, profiles.ProfileError, ValueError) as error:
        result = {"status": "FAILED", "error_type": type(error).__name__, "reason": str(error)[:800],
                  "action": "쓰기 상태를 dry-run으로 다시 확인한다. 중단 상태이면 이 볼륨은 두고 새 프로젝트에서 다시 시작한다."}
    except Exception as error:   # 접속·인증 오류: 설정값이 섞일 수 있어 종류만 남긴다
        result = {"status": "FAILED", "error_type": type(error).__name__,
                  "action": "접속 대상·컨테이너 상태·인증 환경 변수를 확인한다."}
    finally:
        if client is not None:
            client.close()
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.report:
        planner.write_new_json(args.report, result)
    print(text)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
