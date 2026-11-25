"""Issue #105: v1.4.0 복원 번들 기준값(r2·r3) — 표준 라이브러리만 사용, DB·파일 접근 없음.

r2(#91)와 r3(#105)의 기대값·manifest·vector digest·snapshot 정보를 한곳에서 분리한다.

- R2: #91에서 실제로 검증을 마친 값(동결). 기존 r2 도구(restore_final_v140·seed·update·load·build·restore_r2_isolated)의
  상수는 그대로 두고, 이 표의 값과 같은지는 tests/unit/test_restore_profiles_v140.py가 확인한다.
- R3: B2·B3 두 컬렉션 + Neo4j. 최신 dev(#113·#118, 249정책) 병합 릴리스(manifest b3130722…, 2026-11-25 병합 ce7266b)
  기준 C0(pending) 단계다. 계획·임베딩 결과 값은 아직 미확정이다.
  #110 기준(manifest 29b57236…) C0~C2 확정값과 #104 기준(manifest 2e22d2a7…) 1·2차 확정값은 보존 자산 기록이며
  Runbook §5.5·§5.11에 남겼다.
  단계(R3_PIN_STAGE, pin_stage()로 실제 값과 대조): pending → stage1(최종 vector_digest 두 칸만 None) → stage2(None 0).
  None이 하나라도 있으면 require_finalized()가 거부한다. R3_PINS 미확정 상태에서
    허용되는 쓰기 (WRITES_WITHOUT_PINS): build 격리 Qdrant에 원본 snapshot 복원(update_r3_collections --restore-snapshot,
      build Docker 소유 확인·빈 대상 컬렉션 필수), 새 로컬 파일(--write-plan 계획·--report 보고서, 기존 파일 덮어쓰기 거부)
    금지되는 쓰기 (WRITES_REQUIRING_PINS): payload 갱신·삭제(--apply), 임베딩(--index), Neo4j 적재(--load·--resume),
      snapshot 생성(--create-snapshot), 번들 ZIP(--build), 번들 복원(restore_final_v140_r3 --apply, restore_r3_isolated --apply)
  병합 후 build 서버에서 만든 계획(basis=server_snapshot)과 proposed_pins를 검토하고, 승인된 값만 R3_PINS에 옮겨 적는다.
  #31 덤프로 만든 계획(basis=dump_preview)은 사전 검토용이며 고정값·쓰기에 쓰지 않는다. 계산값을 자동으로 고정하지 않는다.
- R3의 원본(source) 값은 이미 있는 자산의 사실이다(최신 릴리스 값이 아님).
  249정책 재구축(r3d)부터 B2·B3 모두 r3b 최종 snapshot(#110 기준 246정책 릴리스의 완료 상태)이 원본이다.
  벡터만 재사용 후보이며, 재사용 여부는 Point별 실제 임베딩 입력·모델·차원·입력 규칙으로 판정한다(plan_r3_vector_reuse).
  이전 원본(B2 = r2 snapshot 8577c3c8…, B3 = #31 snapshot 0a794e34…)은 r3b 구축에 쓴 기록이다(Runbook §5.11).
"""
from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
# r3d 원본 = r3b 최종 snapshot (2026-11-25 확인). 근거(로컬, Git 제외 .cache/issue105-r3b/, 읽기만 함):
#   - snapshot 파일 SHA-256: 파일을 다시 계산한 값 = bundle-r3b-20261123-01/snapshot-record-{b2,b3}.json의 snapshot_sha256
#   - points·vector_digest: snapshot record = 최종 dry-run 11-b2-final-dryrun.json·12-b3-final-dryrun.json(state=complete,
#     payload_pending 0)의 full_vector_digest = #110 C2 R3_PINS의 vector_digest
#   - 같은 snapshot을 담은 번들의 독립 복원 verify-only(21-verify-only.json) PASS, 전체 벡터·보호 payload 불변
#   payload는 #110(manifest 29b57236…) 기준이다. 최신 릴리스와의 차이는 계획(재사용·삭제 후 재임베딩·신규)이 판정한다.
R3_SOURCES: dict[str, dict[str, Any]] = {
    "b2": {"label": "r3b final snapshot (#110, B2)",
           "default_path": ".cache/issue105-r3b/bundle-r3b-20261123-01/b2-r3-v1.12.4.snapshot",
           "snapshot_sha256": "834f17b6a0c18c265573ebc3da59a29473d5fa0242001cca3d0c2749fa98ba89",
           "points": 1233, "vector_digest": "90d0f59318c6a7fb0a084bd1d1242a793cfd6ae6a4d04fb3145933ff5e17689d",
           "digest_basis": "r3b build 최종 dry-run(state=complete)·snapshot record·verify-only 독립 복원 PASS (#110 C2)"},
    "b3": {"label": "r3b final snapshot (#110, B3)",
           "default_path": ".cache/issue105-r3b/bundle-r3b-20261123-01/b3-r3-v1.12.4.snapshot",
           "snapshot_sha256": "91311f2d2f8a23369d302cee865b282c38b651b579aaca0d8f3bc95faf333ff8",
           "points": 1320, "vector_digest": "718046c382cffb43afbc5dd1866ac9c84fc8b25dc293f4bd2d4ac7e32bbd7120",
           "digest_basis": "r3b build 최종 dry-run(state=complete)·snapshot record·verify-only 독립 복원 PASS (#110 C2)"},
}

# r3 기준값. None = 미확정 (require_finalized가 거부한다). 값은 dry-run의 proposed_pins를 검토해 옮겨 적는다.
# C0 (2026-11-25, 최신 dev #113·#118 병합 ce7266b, 249정책): 릴리스 파일에서 직접 다시 계산해 확인한 값만 적었다.
#   manifest LF SHA-256·release_counts·컬렉션별 points(B2·B3 청킹 결과 수 = full_index expected_chunks).
#   load_release()가 이 값과 다르면 거부한다.
#   계획·임베딩 결과에서만 정해지는 값(reuse·embed·delete·plan_sha256·reuse_vector_digest·vector_digest)은 None이다.
#   remove_count 0은 "원본에만 있고 삭제 대상도 아닌 Point 0개"라는 예상이며, 계획의 값이 다르면 validate_plan이 거부한다.
#   (사전 오프라인 추정: B2 재사용 1229·삭제 후 재임베딩 4·신규 16, B3 1316·4·18 — 고정값 아님, 서버 계획으로만 확정)
# 다음: C1 = r3d build 서버(issue105-r3d-build)에 R3_SOURCES(r3b 최종 snapshot)를 복원한 뒤 만든 서버 계획의 검증값,
#       C2 = apply·index 완료 후 최종 dry-run의 전체 vector_digest. 계산값을 자동으로 고정하지 않는다.
# 보존 기록 (현재 값 아님): #110 기준 C0~C2(manifest 29b57236…, B2 90d0f593…·B3 718046c3…) → Runbook §5.5.2~5.5.3,
#   #104 기준 1·2차(manifest 2e22d2a7…, B2 d1fa3194…, B3 a471e21f…) → Runbook §5.5.
R3_PINS: dict[str, Any] = {
    "manifest_lf_sha256": "b31307223f7334ffb7588d55670318d07d36e3fc18a78f62e8c961fdad04f3da",
    "release_counts": {"policies": 249, "documents": 273, "release_chunks": 672, "neo4j_nodes": 1147,
                       "neo4j_relationships": 2702, "requires_condition": 211},
    "collections": {
        # C1 (2026-11-25): r3d build 서버 계획(basis=server_snapshot, .cache/issue105-r3d-build/03-<b2|b3>-plan.json)의 값.
        # C2 (2026-11-25): 최종 vector_digest = r3d build --index 뒤 state=complete 전체 벡터 digest
        #   (.cache/issue105-r3d-build/07-b2-index.json·08-b3-index.json, id_set_equal·누락 0·reuse digest = C1).
        "b2": {"points": 1249, "vector_digest": "7a39e5c9e6c6a7181c0827abcee0bf026160de3dd8dc7facd68b5df7861a1ab4",
               "reuse_count": 1229,
               "reuse_vector_digest": "ec0a272725b360e73617d5b7c14aeac61f9350a9307a33af4b8e20d249306094",
               "embed_count": 20, "delete_count": 4, "remove_count": 0,
               "plan_sha256": "16dc4a3ee31d62416bd59d10fe3bf2b47a160d1dc4eef9ff2fb5580454a1b994"},
        "b3": {"points": 1338, "vector_digest": "d3ed4140122a9a8f815ee7eb01b0aad7c57119eb7a96c139179ed30427c5237b",
               "reuse_count": 1316,
               "reuse_vector_digest": "40e2eb411da501ea5411fd8df52093092e8445f1f648cd5a3b7cae70ac924de2",
               "embed_count": 22, "delete_count": 4, "remove_count": 0,
               "plan_sha256": "164f6b12cd8d4400a9665624e68eb672c36463b5cc522ca6174d7214382fffa8"},
    },
}
# 선언한 단계. pin_stage()가 R3_PINS에서 계산한 단계와 같아야 한다(테스트가 확인) → 값만 바꾸고 단계를 잊는 실수를 막는다.
# 단계 이름: pending(C0) / stage1(C1) / stage2(C2, 최종 digest까지 확정).
R3_PIN_STAGE = "stage2"

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
# build는 최신 dev(249정책) 반영 재구축용 r3d 프로젝트다 (2026-11-25). r3b build(issue105-r3b-build, #110 기준 246정책
# 최종 상태)와 #104 기준 build(issue105-r3-build)는 아래 PRESERVED_R3_ENVIRONMENTS에 기록만 남기고, 그 포트·인증 변수·프로젝트는
# FOREIGN_*로 거부한다(r3 도구가 접속하지 않음). r3b 출력 폴더의 원본 snapshot은 R3_SOURCES로 읽기만 한다.
R3_ENVIRONMENTS: dict[str, dict[str, Any]] = {
    "build": {"project": "issue105-r3d-build", "compose": "docs/development/issue105_r3d_build.compose.yaml",
              "qdrant_port": 56766, "bolt_port": 56797,
              "qdrant_key_env": "ISSUE105_R3D_BUILD_QDRANT_API_KEY",
              "neo4j_password_env": "ISSUE105_R3D_BUILD_NEO4J_PASSWORD",
              "out_dir": ".cache/issue105-r3d-build"},
    # verify는 PR #111(P1-A~E) 코드로 새로 만드는 독립 복원 검증 환경이다 (2026-11-24). 이전 verify(issue105-r3-verify)는
    # §5.9 복원에 이미 쓴 DB라 아래 PRESERVED_R3_ENVIRONMENTS["verify-6d0450f"]로 옮겨 보존·거부한다.
    # out_dir는 이 환경의 새 보고서·번들을 둘 권장 위치를 기록한다(도구 기본값을 바꾸지 않음 — CLI의 --report·--out-dir로 지정).
    "verify": {"project": "issue105-r3d-verify", "compose": "docs/development/issue105_r3d_verify.compose.yaml",
               "qdrant_port": 56756, "bolt_port": 56787,
               "qdrant_key_env": "ISSUE105_R3D_VERIFY_QDRANT_API_KEY",
               "neo4j_password_env": "ISSUE105_R3D_VERIFY_NEO4J_PASSWORD",
               "out_dir": ".cache/issue105-r3d"},
}
# 보존 자산(DB 접속·쓰기 대상 아님): #104 기준 r3 build. B2 1233(d1fa3194…)·B3 1320(a471e21f…)·Neo4j 1133/2674,
# snapshot·ZIP·보고서는 .cache/issue105-r3/에 있다. 이 표는 기록과 거부 목록 생성용이며 도구가 선택할 수 없다.
PRESERVED_R3_ENVIRONMENTS: dict[str, dict[str, Any]] = {
    # r3b build (#110 기준 246정책 최종 상태, B2 1233·B3 1320·Neo4j 1133/2674, 2026-11-23). 컨테이너·볼륨·인증 파일과
    # .cache/issue105-r3b의 계획·보고서·snapshot·번들은 보존한다. 그 폴더에 새 파일을 만들지 않으며, 원본 snapshot 두 개는
    # R3_SOURCES.default_path로 읽기만 한다 (update_r3_collections --restore-snapshot이 SHA-256을 먼저 대조).
    "build-r3b": {"project": "issue105-r3b-build", "compose": "docs/development/issue105_r3b_build.compose.yaml",
                  "qdrant_port": 56666, "bolt_port": 56697,
                  "qdrant_key_env": "ISSUE105_R3B_BUILD_QDRANT_API_KEY",
                  "neo4j_password_env": "ISSUE105_R3B_BUILD_NEO4J_PASSWORD",
                  "out_dir": ".cache/issue105-r3b"},
    "build-104": {"project": "issue105-r3-build", "compose": "docs/development/issue105_r3_build.compose.yaml",
                  "qdrant_port": 56646, "bolt_port": 56677,
                  "qdrant_key_env": "ISSUE105_R3_BUILD_QDRANT_API_KEY",
                  "neo4j_password_env": "ISSUE105_R3_BUILD_NEO4J_PASSWORD",
                  "out_dir": ".cache/issue105-r3"},
    # 이전 verify (번들 rag-final-v140-r3-6d0450fd6c1d.zip 독립 복원·검색 검증에 쓴 DB, Runbook §5.9). 컨테이너·볼륨·
    # 인증 파일과 .cache/issue105-r3c의 번들·보고서·검색 증거는 보존한다. out_dir를 두어 그 폴더에 새 파일을 만들지 않는다.
    "verify-6d0450f": {"project": "issue105-r3-verify", "compose": "docs/development/issue105_r3_verify.compose.yaml",
                       "qdrant_port": 56656, "bolt_port": 56687,
                       "qdrant_key_env": "ISSUE105_R3_VERIFY_QDRANT_API_KEY",
                       "neo4j_password_env": "ISSUE105_R3_VERIFY_NEO4J_PASSWORD",
                       "out_dir": ".cache/issue105-r3c"},
}
# 원본·공유·r1·r2·이전 실험·보존 r3 DB 포트. r3 도구는 이 포트로는 절대 접속하지 않는다.
FOREIGN_PORTS = frozenset({6333, 6334, 16333, 36333, 46333, 46335, 46336, 56336, 56339, 56446, 56556,
                           7474, 7687, 17474, 17687, 18474, 18687, 19687, 47474, 47687,
                           57474, 57477, 57687, 57690, 56487, 56587,
                           *(env[name] for env in PRESERVED_R3_ENVIRONMENTS.values()
                             for name in ("qdrant_port", "bolt_port"))})
# r3 도구가 쓰면 안 되는 인증 변수 (원본·공유·r2·보존 r3 build 값 재사용 금지)
FOREIGN_SECRET_ENVS = ("QDRANT_API_KEY", "NEO4J_PASSWORD", "ISSUE91_NEO4J_PASSWORD",
                       "ISSUE91_VERIFY_QDRANT_API_KEY", "ISSUE91_VERIFY_NEO4J_PASSWORD",
                       *(env[name] for env in PRESERVED_R3_ENVIRONMENTS.values()
                         for name in ("qdrant_key_env", "neo4j_password_env")))
# r3 도구가 Docker 소유를 인정하면 안 되는 프로젝트 (보존 r3 build 등)
FOREIGN_PROJECTS = frozenset(env["project"] for env in PRESERVED_R3_ENVIRONMENTS.values())

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
# 기준값 게이트(R3_PINS 단계) 기준의 분류 목록이다. 실제 실행 가능 여부를 뜻하지 않는다.
# "restore_final_v140_r3 --apply"(본체 직접 apply)는 PR #111 P1-A 이후 R3_PINS 단계와 관계없이 CLI에서 항상 거부된다
# (종료 코드 2). 본체의 쓰기는 "restore_r3_isolated --apply"(래퍼가 쓰기 허가 객체를 넘김) 경로로만 실행된다.
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


# 단계별로 기준값 게이트를 통과하는 쓰기 (각 단계의 Docker 소유·fingerprint·서버 상태 검사는 별도로 그대로 적용된다)
PIN_STAGES = ("pending", "stage1", "stage2")
STAGE_ALLOWED_WRITES: dict[str, tuple[str, ...]] = {
    "pending": WRITES_WITHOUT_PINS,
    "stage1": (*WRITES_WITHOUT_PINS, "update_r3_collections --apply", "update_r3_collections --index",
               "load_neo4j_r3_isolated --load/--resume"),
    "stage2": (*WRITES_WITHOUT_PINS, *WRITES_REQUIRING_PINS, "load_neo4j_r3_isolated --cross-db (읽기 전용)"),
}


def pin_stage(pins: dict[str, Any] | None = None) -> str:
    """
    기준값에서 계산한 단계. pending = 계획·결과 값이 아직 없음(쓰기는 WRITES_WITHOUT_PINS만),
    stage1 = 최종 vector_digest 두 칸만 미확정(갱신 단계 쓰기 허용), stage2 = 미확정 0.
    스키마가 깨졌으면 pending으로 본다(어떤 확정 쓰기도 허용하지 않음).
    """
    missing = missing_pins(pins)
    if not missing:
        return "stage2"
    if "<schema>" not in missing and set(missing) <= set(UPDATE_STAGE_DEFERRED_PINS):
        return "stage1"
    return "pending"


def environment(name: str) -> dict[str, Any]:
    if name not in R3_ENVIRONMENTS:
        raise ProfileError(f"알 수 없는 r3 환경: {name}")
    env = R3_ENVIRONMENTS[name]
    secrets = (env["qdrant_key_env"], env["neo4j_password_env"])
    if (env["project"] in FOREIGN_PROJECTS or {env["qdrant_port"], env["bolt_port"]} & FOREIGN_PORTS
            or set(secrets) & set(FOREIGN_SECRET_ENVS)):
        raise ProfileError(f"r3 환경 {name}이 보존·외부 자산(프로젝트·포트·인증 변수)과 겹칩니다")
    return dict(env)


def _real(path) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(str(path))))


def is_preserved_output(path) -> bool:
    """path가 보존 자산 폴더(#104 .cache/issue105-r3 등) 자체이거나 그 안이면 True. (파일 내용은 읽지 않는다)"""
    target = _real(path)
    for env in PRESERVED_R3_ENVIRONMENTS.values():
        preserved = _real(os.path.join(PROJECT_ROOT, env["out_dir"]))
        if target == preserved or target.startswith(preserved.rstrip(os.sep) + os.sep):
            return True
    return False


def require_output_path(path, what: str = "출력 경로"):
    """r3 도구의 새 파일(보고서·계획·snapshot·번들)을 보존 자산 폴더 안에 만들지 않는다. CLI 인자 검사 단계에서 호출한다."""
    if path is not None and is_preserved_output(path):
        raise ProfileError(f"{what}가 보존 자산 폴더(#104 r3·r3b·이전 verify) 안입니다 → 새 파일도 만들지 않음 "
                           f"({R3_ENVIRONMENTS['build']['out_dir']}/ 사용)")
    return path


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
