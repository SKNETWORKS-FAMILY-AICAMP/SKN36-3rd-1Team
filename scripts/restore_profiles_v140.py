"""Issue #105: v1.4.0 복원 번들 기준값(r2·r3) — 표준 라이브러리만 사용, DB·파일 접근 없음.

r2(#91)와 r3(#105)의 기대값·manifest·vector digest·snapshot 정보를 한곳에서 분리한다.

- R2: #91에서 실제로 검증을 마친 값(동결). 기존 r2 도구(restore_final_v140·seed·update·load·build·restore_r2_isolated)의
  상수는 그대로 두고, 이 표의 값과 같은지는 tests/unit/test_restore_profiles_v140.py가 확인한다.
- R3: B2·B3 두 컬렉션 + Neo4j. 최신 데이터(#95·#96·#98·#104)가 병합되기 전이라 릴리스·결과 값은 아직 None이다.
  None이 하나라도 있으면 require_finalized()가 거부한다. R3_PINS 미확정 상태에서
    허용되는 쓰기 (WRITES_WITHOUT_PINS): build 격리 Qdrant에 원본 snapshot 복원(update_r3_collections --restore-snapshot,
      build Docker 소유 확인·빈 대상 컬렉션 필수), 새 로컬 파일(--write-plan 계획·--report 보고서, 기존 파일 덮어쓰기 거부)
    금지되는 쓰기 (WRITES_REQUIRING_PINS): payload 갱신·삭제(--apply), 임베딩(--index), Neo4j 적재(--load·--resume),
      snapshot 생성(--create-snapshot), 번들 ZIP(--build), 번들 복원(restore_final_v140_r3 --apply, restore_r3_isolated --apply)
  병합 후 build 서버에서 만든 계획(basis=server_snapshot)과 proposed_pins를 검토하고, 승인된 값만 R3_PINS에 옮겨 적는다.
  #31 덤프로 만든 계획(basis=dump_preview)은 사전 검토용이며 고정값·쓰기에 쓰지 않는다. 계산값을 자동으로 고정하지 않는다.
- R3의 원본(source) 값은 이미 있는 자산의 사실이다(최신 릴리스 값이 아님).
  B2 = r2 snapshot(#91 독립 복원으로 digest 확인), B3 = #31 snapshot(덤프·공유 DB scan digest와 같다고 기록,
  snapshot 자체를 격리 서버에 복원한 digest는 아직 미검증 → update_r3_collections가 복원 직후 대조한다).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

DATASET_VERSION = "1.4.0-candidate"
RELEASE_DIR = "data/releases/v1.4.0-candidate"
EMBEDDING_MODEL = "qwen3-embedding:0.6b"
EMBEDDING_DIM = 1024
EMBEDDING_INPUT_RULE = "policy_section_body_v2"
QDRANT_VERSION = "1.12.4"
NEO4J_VERSION = "5.26.32"
NEO4J_EDITION = "community"
QDRANT_IMAGE = "sha256:241edb9d7778327516ef218f8c74e1bd61b5ea42cd4f193cb8d0896199705636"
NEO4J_IMAGE = "sha256:42ef3ebfd671ebd01fdebb6dcbe80cf7b45eef2aa3d346ef4132f7ef31e67d2f"
RELEASE_COUNT_KEYS = ("policies", "documents", "release_chunks", "neo4j_nodes", "neo4j_relationships",
                      "requires_condition")


class ProfileError(ValueError):
    """기준값이 확정되지 않았거나 형식이 맞지 않음."""


# ---------------------------------------------------------------------------
# r2 (#91, 동결)
# ---------------------------------------------------------------------------
R2_PROFILE: dict[str, Any] = {
    "name": "r2",
    "bundle": "rag-final-v140-r2",
    "schema_version": 1,
    "manifest_lf_sha256": "d48ef11b599466b394bef15e275be26b24be9dfde1e74771c86b8e3d384acf50",
    "release_counts": {"policies": 246, "documents": 262, "release_chunks": 600, "neo4j_nodes": 1126,
                       "neo4j_relationships": 2668, "requires_condition": 211},
    "collections": {
        "b2": {"config": "A_B2_V140", "collection": "exp_b2_qwen3emb06b_v140_v1", "dimensions": EMBEDDING_DIM,
               "points": 1139,
               "vector_digest": "8577c3c8698fcb891fb35544eac01ec02066fb5c2ef0313d15b6a26e576da3d3",
               "snapshot_member": "b2-r2-v1.12.4.snapshot",
               "snapshot_sha256": "2e07c17ad529882fc638af172018cccf61e9477240d6f1630b2099e70640bca0"},
    },
    "bundle_zip": "rag-final-v140-r2-027f40b556c5.zip",
    "bundle_sha256": "dd28f8cd14c242bda2e6d5697d5cbf23725e6b7dee7b72f4781830429c579197",
    "source_commit": "027f40b556c5408a2f0b9547acef4964254e37b8",
}

# ---------------------------------------------------------------------------
# r3 (#105) — 원본 자산(사실)과 최신 릴리스 기준(병합 후 확정)
# ---------------------------------------------------------------------------
R3_SOURCES: dict[str, dict[str, Any]] = {
    "b2": {"label": "r2 snapshot (#91)",
           "default_path": ".cache/issue91-r2-20261119/bundle-r2/b2-r2-v1.12.4.snapshot",
           "snapshot_sha256": R2_PROFILE["collections"]["b2"]["snapshot_sha256"],
           "points": 1139, "vector_digest": R2_PROFILE["collections"]["b2"]["vector_digest"],
           "digest_basis": "#91 독립 복원(issue91-r2-verify-20261119)에서 snapshot 복원 후 scan으로 확인"},
    "b3": {"label": "B3 snapshot (#31 before)",
           "default_path": "venv/issue31_20261109/exp_b3_qwen3emb06b_v140_v1.before.snapshot",
           "snapshot_sha256": "502b7b7e1b923183a2992a5450e63470d2da5238317d9883f07382cdbb584ad8",
           "points": 1220, "vector_digest": "0a794e34a91f4314b6e469d6375177ac745b8b00e5ce3d4f0c2bc2f50d726371",
           "digest_basis": "#31 before/after 덤프와 공유·pr29 서버 scan의 digest. snapshot 자체의 격리 복원은 미검증"},
}

# 병합 후 확정할 값. None = 미확정 (require_finalized가 거부한다). 값은 dry-run의 proposed_pins를 검토해 옮겨 적는다.
R3_PINS: dict[str, Any] = {
    "manifest_lf_sha256": None,
    "release_counts": {key: None for key in RELEASE_COUNT_KEYS},
    "collections": {
        "b2": {"points": None, "vector_digest": None, "reuse_count": None, "reuse_vector_digest": None,
               "embed_count": None, "delete_count": None, "remove_count": 0, "plan_sha256": None},
        "b3": {"points": None, "vector_digest": None, "reuse_count": None, "reuse_vector_digest": None,
               "embed_count": None, "delete_count": None, "remove_count": 0, "plan_sha256": None},
    },
}

R3_COLLECTIONS: dict[str, dict[str, Any]] = {
    "b2": {"config": "A_B2_V140", "collection": "exp_b2_qwen3emb06b_v140_v1", "strategy": "B2",
           "chunk_prefix": "CHK_B2_", "dimensions": EMBEDDING_DIM, "model": EMBEDDING_MODEL,
           "snapshot_member": "b2-r3-v1.12.4.snapshot"},
    "b3": {"config": "B_B3_V140", "collection": "exp_b3_qwen3emb06b_v140_v1", "strategy": "B3",
           "chunk_prefix": "CHK_B3_", "dimensions": EMBEDDING_DIM, "model": EMBEDDING_MODEL,
           "snapshot_member": "b3-r3-v1.12.4.snapshot"},
}
COLLECTION_KEYS = tuple(R3_COLLECTIONS)

# 격리 Docker 프로젝트. build = 갱신·적재·snapshot 생성용, verify = 번들 독립 복원 검증용.
# 포트는 r2·공유·이전 작업 포트와 겹치지 않아야 한다(테스트가 확인).
R3_ENVIRONMENTS: dict[str, dict[str, Any]] = {
    "build": {"project": "issue105-r3-build", "compose": "docs/development/issue105_r3_build.compose.yaml",
              "qdrant_port": 56646, "bolt_port": 56677,
              "qdrant_key_env": "ISSUE105_R3_BUILD_QDRANT_API_KEY",
              "neo4j_password_env": "ISSUE105_R3_BUILD_NEO4J_PASSWORD"},
    "verify": {"project": "issue105-r3-verify", "compose": "docs/development/issue105_r3_verify.compose.yaml",
               "qdrant_port": 56656, "bolt_port": 56687,
               "qdrant_key_env": "ISSUE105_R3_VERIFY_QDRANT_API_KEY",
               "neo4j_password_env": "ISSUE105_R3_VERIFY_NEO4J_PASSWORD"},
}
# 원본·공유·r1·r2·이전 실험 DB 포트. r3 도구는 이 포트로는 절대 접속하지 않는다.
FOREIGN_PORTS = frozenset({6333, 6334, 16333, 36333, 46333, 46335, 46336, 56336, 56339, 56446, 56556,
                           7474, 7687, 17474, 17687, 18474, 18687, 19687, 47474, 47687,
                           57474, 57477, 57687, 57690, 56487, 56587})
# r3 도구가 쓰면 안 되는 인증 변수 (원본·공유·r2 값 재사용 금지)
FOREIGN_SECRET_ENVS = ("QDRANT_API_KEY", "NEO4J_PASSWORD", "ISSUE91_NEO4J_PASSWORD",
                       "ISSUE91_VERIFY_QDRANT_API_KEY", "ISSUE91_VERIFY_NEO4J_PASSWORD")

BUNDLE_NAME = "rag-final-v140-r3"
SCHEMA_VERSION = 2
RESTORE_SCRIPT = "scripts/restore_final_v140_r3.py"
PROFILE_SCRIPT = "scripts/restore_profiles_v140.py"
# 번들 복원 시 --repo가 번들의 source ZIP과 같은 소스인지 대조하는 파일 (줄바꿈 차이만 허용)
SOURCE_IDENTITY_FILES = (RESTORE_SCRIPT, PROFILE_SCRIPT, "scripts/restore_final_v140.py", f"{RELEASE_DIR}/manifest.json")

# 계획의 기준. 최종 고정값(plan_sha256)과 쓰기에는 build 서버에 실제 복원한 snapshot에서 만든 계획만 쓴다.
PLAN_BASIS_SERVER = "server_snapshot"
PLAN_BASIS_PREVIEW = "dump_preview"

WRITES_WITHOUT_PINS = ("update_r3_collections --restore-snapshot", "--write-plan (새 로컬 계획 파일)",
                       "--report (새 로컬 보고서 파일)")
WRITES_REQUIRING_PINS = ("update_r3_collections --apply", "update_r3_collections --index",
                         "load_neo4j_r3_isolated --load/--resume", "build_restore_bundle_r3 --create-snapshot",
                         "build_restore_bundle_r3 --build", "restore_final_v140_r3 --apply",
                         "restore_r3_isolated --apply")


def eol_normalized(data: bytes) -> bytes:
    """줄바꿈만 LF로 맞춘 바이트 (Windows core.autocrlf=true 작업 트리 CRLF ↔ Git blob LF 비교용). 내용 변경은 그대로 남는다."""
    return data.replace(b"\r\n", b"\n")


def same_text_ignoring_eol(left: bytes, right: bytes) -> bool:
    return eol_normalized(left) == eol_normalized(right)


def r3_pins() -> dict[str, Any]:
    """현재 R3_PINS의 복사본 (호출자가 바꿔도 원본은 그대로)."""
    return deepcopy(R3_PINS)


def missing_pins(pins: dict[str, Any] | None = None) -> list[str]:
    """None으로 남은 기준값의 경로 목록."""
    pins = R3_PINS if pins is None else pins
    missing: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key in value:
                walk(value[key], f"{path}.{key}" if path else key)
        elif value is None:
            missing.append(path)
    walk(pins, "")
    expected_keys = {"manifest_lf_sha256", "release_counts", "collections"}
    if set(pins) != expected_keys or set(pins.get("collections") or {}) != set(COLLECTION_KEYS):
        missing.append("<schema>")
    return missing


# 컬렉션별 최종 vector_digest는 임베딩이 끝나야 정해진다. 그래서 갱신 단계(payload 갱신·삭제·임베딩, Neo4j 적재)에서만
# 이 두 칸의 미확정을 허용한다. snapshot 생성·번들·번들 복원·교차 검증은 이 값까지 모두 확정되어야 한다.
UPDATE_STAGE_DEFERRED_PINS = tuple(f"collections.{key}.vector_digest" for key in ("b2", "b3"))


def require_finalized(pins: dict[str, Any] | None = None, *, deferred: tuple[str, ...] = ()) -> dict[str, Any]:
    """
    r3 기준값이 확정되었을 때만 통과한다. 미확정이면 쓰기 작업을 하지 않는다.
    deferred: 이 단계에서 아직 정해질 수 없는 칸 (UPDATE_STAGE_DEFERRED_PINS만 허용, 스키마 오류는 미룰 수 없다).
    """
    if set(deferred) - set(UPDATE_STAGE_DEFERRED_PINS):
        raise ProfileError(f"미룰 수 없는 기준값입니다: {sorted(set(deferred) - set(UPDATE_STAGE_DEFERRED_PINS))}")
    pins = R3_PINS if pins is None else pins
    missing = [path for path in missing_pins(pins) if path not in deferred]
    if missing:
        raise ProfileError("r3 기준값이 아직 확정되지 않았습니다 (최신 데이터 병합 후 dry-run의 proposed_pins를 검토해 "
                           f"R3_PINS에 기록): {missing[:8]}{' …' if len(missing) > 8 else ''}")
    return pins


def is_finalized(pins: dict[str, Any] | None = None) -> bool:
    return not missing_pins(pins)


def environment(name: str) -> dict[str, Any]:
    if name not in R3_ENVIRONMENTS:
        raise ProfileError(f"알 수 없는 r3 환경: {name}")
    return dict(R3_ENVIRONMENTS[name])


def services_for(name: str) -> dict[str, dict[str, Any]]:
    """Docker 소유 확인 기준: 서비스별 호스트 포트·컨테이너 포트·이미지 digest·프로젝트 전용 볼륨 이름."""
    env = environment(name)
    project = env["project"]
    return {"qdrant": {"port": env["qdrant_port"], "container_port": "6333/tcp", "image": QDRANT_IMAGE,
                       "volumes": {f"{project}_qdrant_storage"}},
            "neo4j": {"port": env["bolt_port"], "container_port": "7687/tcp", "image": NEO4J_IMAGE,
                      "volumes": {f"{project}_neo4j_data", f"{project}_neo4j_logs"}}}


def collection_spec(key: str) -> dict[str, Any]:
    if key not in R3_COLLECTIONS:
        raise ProfileError(f"알 수 없는 r3 컬렉션 키: {key} (허용: {', '.join(COLLECTION_KEYS)})")
    return {"key": key, **R3_COLLECTIONS[key]}


def release_counts_tuple(counts: dict[str, Any]) -> tuple:
    return tuple(counts[key] for key in RELEASE_COUNT_KEYS)
