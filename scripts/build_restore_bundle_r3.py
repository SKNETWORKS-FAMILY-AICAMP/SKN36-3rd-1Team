"""Issue #105: r3 복원 번들(B2·B3 snapshot + 소스 + 복원 도구 + Compose + package.json + SHA256SUMS) — 기본 읽기 전용 계획.

단계
  --create-snapshot --collection b2|b3 [서버 쓰기, build 프로젝트 Qdrant만]
      build Docker 소유 확인 → 격리 서버 → build 서버 기준 계획(basis=server_snapshot) 대비 상태 complete
      → Point 수·전체 벡터 digest = R3_PINS → dry-run fingerprint(서버 상태 + Docker 식별 정보)
      → verify_full_index → .partial 배타적 생성(이미 있으면 서버 호출 없이 거부) → snapshot 생성 → 내려받기
      → fingerprint 재확인 → SHA-256 검증(서버 checksum, 없으면 서버 파일 sha256sum — 크기만으로 통과 없음)
      → 하드 링크로 최종 이름 + snapshot-record-<key>.json (기존 파일 덮어쓰기·삭제 없음, 검증 근거 기록)
  --build [로컬 쓰기, 출력 폴더만]
      커밋 확인(40자리 SHA, manifest LF = R3_PINS, 필수 파일, 인증 파일 이름 없음,
               커밋의 기준값 모듈 = 작업 트리 것 — 줄바꿈 차이만 허용, 커밋되지 않은 R3_PINS 변경은 거부)
      → staging(실행별 UUID) → check_bundle(r3) → <이름>.zip.partial(배타적 생성) → 엔트리 검사 후 압축 내용 전체 재검증
      → 하드 링크로 최종 이름 → .sha256·보고서 (최종 ZIP·.partial·.sha256·보고서가 하나라도 있으면 시작 전 거부, 기존 파일 삭제 없음)
      번들 구성(평평한 파일): b2-r3-v1.12.4.snapshot, b3-r3-v1.12.4.snapshot, source-<sha12>.zip, compose.yaml,
      restore_final_v140_r3.py, package.json, SHA256SUMS
      Neo4j 복원 데이터는 source ZIP의 data/releases/v1.4.0-candidate/neo4j/*이며, 파일별 SHA-256을 package.json에 적는다.
R3_PINS가 확정되지 않으면 snapshot 생성·번들 생성 모두 거부한다. 기존 r2 번들·snapshot은 읽지도 바꾸지도 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import full_index as fi  # noqa: E402
from scripts import build_restore_bundle_v140 as b2b  # noqa: E402  (git·ZIP·체크섬 헬퍼 재사용)
from scripts import plan_r3_vector_reuse as planner  # noqa: E402
from scripts import restore_final_v140 as restore  # noqa: E402
from scripts import restore_final_v140_r3 as restore_r3  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402
from scripts import restore_r2_isolated as r2w  # noqa: E402  (읽기 전용 docker 실행기 재사용)
from scripts import update_r3_collections as qdrant_r3  # noqa: E402

# build 프로필 출력 폴더(현재 r3d: .cache/issue105-r3d-build). #104(.cache/issue105-r3)·r3b(.cache/issue105-r3b)·이전 verify
# (.cache/issue105-r3c) 산출물은 보존 자산이라 create_snapshot·build_bundle이 거부한다.
DEFAULT_OUT_DIR = ROOT / profiles.environment("build")["out_dir"] / "bundle-r3"
VERIFY_COMPOSE = profiles.R3_ENVIRONMENTS["verify"]["compose"]
MANIFEST_SOURCE = f"{profiles.RELEASE_DIR}/manifest.json"
REQUIRED_COMMIT_PATHS = (VERIFY_COMPOSE, profiles.RESTORE_SCRIPT, profiles.PROFILE_SCRIPT,
                         "scripts/restore_final_v140.py", MANIFEST_SOURCE, "requirements.txt")
RESTORE_MEMBER = Path(profiles.RESTORE_SCRIPT).name
BundleError = b2b.BundleError
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
COLLECTION_NAME = re.compile(r"^[A-Za-z0-9_]{1,200}$")
QDRANT_SNAPSHOT_ROOT = "/qdrant/snapshots"       # Qdrant 1.12 기본 snapshot 경로 (컨테이너 안)


def snapshot_paths(out_dir: Path, key: str) -> tuple[Path, Path]:
    return Path(out_dir) / profiles.R3_COLLECTIONS[key]["snapshot_member"], Path(out_dir) / f"snapshot-record-{key}.json"


def require_new_output_dir(out_dir: Path) -> Path:
    """snapshot·번들 출력 폴더가 보존 자산 폴더(PRESERVED_R3_ENVIRONMENTS의 out_dir) 안이면 서버·로컬 쓰기 전에 거부한다."""
    if profiles.is_preserved_output(out_dir):
        raise BundleError("출력 폴더가 보존 자산 폴더(#104 r3·r3b·이전 verify) 안입니다 → 쓰지 않음 "
                          f"({profiles.environment('build')['out_dir']}/ 사용)")
    return Path(out_dir)


def partial_path(target: Path) -> Path:
    return target.with_name(target.name + ".partial")


def create_owned(path: Path):
    """새 파일을 배타적으로 만든다(open "xb"). 반환: (열린 스트림, 파일 식별자). 이미 있으면 FileExistsError."""
    stream = Path(path).open("xb")
    return stream, os.fstat(stream.fileno())


def remove_if_owned(path: Path, owned) -> None:
    """path가 이번 실행이 만든 파일(같은 st_dev·st_ino)일 때만 지운다. 다른 파일·없는 파일은 그대로 둔다."""
    try:
        current = os.stat(path)
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
        Path(path).unlink()


def link_no_overwrite(source: Path, target: Path, what: str) -> None:
    """하드 링크로 최종 이름을 만든다. 같은 이름이 이미 있으면 거부(덮어쓰기 없음)."""
    try:
        os.link(source, target)
    except FileExistsError as error:
        raise BundleError(f"최종 {what} 이름이 그 사이에 생겼습니다 (덮어쓰지 않음)") from error


def docker_exec_sha256(identity: dict[str, Any], collection: str, name: str, docker=None) -> str:
    """
    서버 snapshot 파일의 SHA-256을 컨테이너 안에서 읽기 전용으로 계산한다 (docker exec <컨테이너 ID> sha256sum).
    대상은 소유 확인을 통과한 build Qdrant 컨테이너 ID(이름 아님) + 검증한 컬렉션 이름 + 서버가 돌려준 snapshot 이름뿐이다.
    출력이 정확히 "<64 hex>  <같은 경로>" 한 줄이 아니면 실패한다.
    """
    container = str((identity.get("qdrant") or {}).get("id", ""))
    if not CONTAINER_ID.match(container) or not COLLECTION_NAME.match(collection) or not b2b.SNAPSHOT_NAME.match(name):
        raise BundleError("서버 snapshot SHA 확인 대상(컨테이너 ID·컬렉션·이름) 형식이 예상과 다릅니다")
    path = f"{QDRANT_SNAPSHOT_ROOT}/{collection}/{name}"
    lines = (docker or r2w.run_docker)(["exec", container, "sha256sum", "--", path]).strip().splitlines()
    match = re.fullmatch(r"([0-9a-f]{64})  (.+)", lines[0]) if len(lines) == 1 else None
    if not match or match.group(2) != path:
        raise BundleError("서버 snapshot SHA-256 출력 형식이 예상과 다릅니다")
    return match.group(1)


def verify_snapshot_integrity(description, local_sha: str, local_size: int, server_sha256) -> dict[str, Any]:
    """
    내려받은 파일이 서버 snapshot과 같은지 확인한다. 크기 일치만으로는 통과시키지 않는다.
    - 서버가 돌려준 checksum이 64자리 소문자 hex면 그 값과 로컬 SHA-256을 비교한다.
    - checksum이 없거나 형식이 다르면 server_sha256()(서버 파일 SHA, 읽기 전용)과 비교한다. 이것도 안 되면 실패.
    반환값(검증 근거)은 기록 JSON의 snapshot_integrity에 그대로 남긴다.
    """
    checksum, size = getattr(description, "checksum", None), getattr(description, "size", None)
    if size is not None and size != local_size:
        raise BundleError("내려받은 snapshot 크기가 서버 값과 다릅니다")
    evidence: dict[str, Any] = {"local_sha256": local_sha, "local_size_bytes": local_size,
                                "server_reported_size_bytes": size,
                                "server_reported_checksum": checksum if isinstance(checksum, str) else None}
    if isinstance(checksum, str) and SHA256_HEX.match(checksum):
        if checksum != local_sha:
            raise BundleError("내려받은 snapshot SHA-256이 서버 checksum과 다릅니다")
        evidence.update(method="server_reported_checksum", server_sha256=checksum)
    else:
        evidence["server_reported_checksum_status"] = "missing" if checksum is None else "invalid_format"
        try:
            server_sha = server_sha256()
        except Exception as error:
            raise BundleError(f"서버 checksum이 없고 서버 snapshot 파일 SHA도 확인하지 못했습니다 → 검증 불가 "
                              f"({type(error).__name__})") from error
        if not (isinstance(server_sha, str) and SHA256_HEX.match(server_sha)) or server_sha != local_sha:
            raise BundleError("내려받은 snapshot SHA-256이 서버 snapshot 파일 SHA-256과 다릅니다")
        evidence.update(method="server_file_sha256_docker_exec", server_sha256=server_sha)
    evidence["verified"] = True
    return evidence


# ---------------------------------------------------------------------------
# 1. snapshot 생성 ([서버 쓰기] build 프로젝트 Qdrant만)
# ---------------------------------------------------------------------------
def create_snapshot(client, url: str, plan: dict[str, Any], release: dict[str, Any], out_dir: Path, *,
                    confirm_write: bool = False, expected_fingerprint: str | None = None,
                    pins: dict[str, Any] | None = None, environ=None, http_get=None, verify_slices=None,
                    docker=None, server_sha256=None) -> dict:
    """
    파일 보호 (2026-11-22 보완)
      - 최종 snapshot·기록 JSON·.partial 중 하나라도 있으면 서버 쓰기 전에 거부한다 (기존 파일은 지우지도 덮어쓰지도 않음).
      - create_snapshot 호출 전에 .partial을 배타적으로 새로 만든다(open "xb"). 다른 작업이 먼저 만들었으면 거부 → 서버 호출 0회.
      - 실패 시 정리는 이번 실행이 만든 .partial(같은 파일 식별자)만 지운다. 강제 종료로 남은 .partial은 다음 실행이 보존·거부한다.
      - 최종 이름은 하드 링크(os.link)로 만든다 → 같은 이름이 생겨 있으면 FileExistsError로 거부(덮어쓰기 없음).
    무결성: verify_snapshot_integrity (서버 checksum 또는 서버 파일 SHA와 로컬 SHA 일치 필수, 크기만으로 통과 없음)
    """
    if not confirm_write:
        raise BundleError("confirm_write=True 없이 snapshot을 만들지 않습니다")
    require_new_output_dir(out_dir)
    pins = profiles.require_finalized(pins)
    planner.validate_plan(plan, release, pins, require_pins=True)
    key = plan["collection_key"]
    spec, pinned = profiles.collection_spec(key), pins["collections"][key]
    qdrant_r3.require_isolated_url(url)
    identity = qdrant_r3.require_build_ownership(docker)
    api_key = qdrant_r3.read_secret(qdrant_r3.API_KEY_ENV, environ)
    target, record_path = snapshot_paths(out_dir, key)
    partial = partial_path(target)
    existing = [p.name for p in (target, record_path, partial) if p.exists()]
    if existing:
        raise BundleError(f"snapshot·기록·.partial 파일이 이미 있습니다 (덮어쓰거나 지우지 않음): {existing}")
    qdrant_r3.require_isolated_server(client)
    state = qdrant_r3.inspect_state(client, plan, release)
    if (state["state"] != "complete" or state["points"] != pinned["points"]
            or state["full_vector_digest"] != pinned["vector_digest"]):
        raise BundleError(f"{key} snapshot 전 상태 이상: {state['state']}, Point {state['points']}")
    qdrant_r3.require_fingerprint(client, state, expected_fingerprint, identity)
    entry = release["collections"][key]
    slices = (verify_slices or (lambda: fi.verify_full_index(spec["config"], client=client, chunks=entry["chunks"],
                                                             documents=release["documents"])))()
    if not slices.get("passed"):
        raise BundleError("verify_full_index 실패 → snapshot을 만들지 않음")
    if http_get is None:
        import requests
        http_get = requests.get
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    try:
        stream, owned = create_owned(partial)        # 이번 실행 소유의 임시 파일 (배타적 생성)
    except FileExistsError as error:
        raise BundleError("다른 작업이 .partial을 먼저 만들었습니다 (지우지 않음, snapshot 생성 안 함)") from error
    response = None
    try:
        description = client.create_snapshot(collection_name=spec["collection"], wait=True)
        name = getattr(description, "name", "") or ""
        if not b2b.SNAPSHOT_NAME.match(name):
            raise BundleError("서버가 돌려준 snapshot 이름 형식이 예상과 다릅니다")
        response = http_get(f"{url.rstrip('/')}/collections/{spec['collection']}/snapshots/{name}",
                            headers={"api-key": api_key}, timeout=qdrant_r3.UPLOAD_TIMEOUT_SECONDS, stream=True)
        if getattr(response, "status_code", None) != 200:
            raise BundleError(f"snapshot 내려받기 실패: HTTP {getattr(response, 'status_code', None)}")
        for block in response.iter_content(chunk_size=1024 * 1024):
            stream.write(block)
        stream.close()
        sha, size = restore.file_sha(partial), partial.stat().st_size
        after = qdrant_r3.inspect_state(client, plan, release)
        after_identity = qdrant_r3.require_build_ownership(docker)
        if qdrant_r3.server_fingerprint(client, after, after_identity) != expected_fingerprint:
            raise BundleError("snapshot 후 컬렉션·컨테이너 fingerprint가 바뀌었습니다")
        integrity = verify_snapshot_integrity(
            description, sha, size,
            server_sha256 or (lambda: docker_exec_sha256(after_identity, spec["collection"], name, docker)))
        link_no_overwrite(partial, target, "snapshot")
        partial.unlink()
    except BaseException:
        stream.close()
        remove_if_owned(partial, owned)              # 이번 실행이 만든 미완성 파일만 지운다
        raise
    finally:
        close = getattr(response, "close", None)
        if close:
            close()
    record = {"collection_key": key, "collection": spec["collection"], "config": spec["config"],
              "points": after["points"], "dimensions": spec["dimensions"],
              "vector_digest": after["full_vector_digest"], "payload_digest": after["payload_digest"],
              "fingerprint": expected_fingerprint, "qdrant_version": profiles.QDRANT_VERSION,
              "docker_project": qdrant_r3.BUILD_PROJECT, "plan_basis": plan["basis"],
              "server_snapshot_name": name, "snapshot_file": target.name, "snapshot_sha256": sha,
              "snapshot_size_bytes": size, "snapshot_integrity": integrity, "verify_full_index_passed": True,
              "plan_sha256": planner.plan_sha256(plan), "plan_counts": plan["counts"],
              "source": plan["source"], "manifest_lf_sha256": plan["manifest_lf_sha256"],
              "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "server_state_note": "서버 /qdrant/snapshots에 snapshot 파일이 남아 있다 (컬렉션 데이터는 불변)"}
    try:
        planner.write_new_json(record_path, record)
    except Exception as error:
        raise BundleError(f"snapshot 파일은 검증·보존했지만 기록 JSON을 만들지 못했습니다 ({type(error).__name__}). "
                          "같은 경로 재실행은 거부되므로 파일을 사람이 확인한다") from error
    return record


# ---------------------------------------------------------------------------
# 2. 소스 커밋 (git, 읽기 전용)
# ---------------------------------------------------------------------------
def require_committed(sha: str, path: str, repo: Path, message: str) -> None:
    """
    작업 트리 파일이 커밋 blob과 같은 내용인지 확인한다. 줄바꿈(CRLF↔LF)만 다른 것은 허용한다
    (Windows core.autocrlf=true: blob은 LF, 작업 트리는 CRLF). 내용이 하나라도 다르거나 파일이 없으면 거부.
    """
    work = Path(repo) / path
    if not work.is_file() or not profiles.same_text_ignoring_eol(b2b.commit_blob(sha, path, repo), work.read_bytes()):
        raise BundleError(message)


def check_source_commit(sha: str, repo: Path = ROOT, pins: dict[str, Any] | None = None) -> dict[str, Any]:
    pins = profiles.require_finalized(pins)
    names = b2b.run_git(["ls-tree", "-r", "-z", "--name-only", sha], repo).decode("utf-8").split("\0")
    names = [n for n in names if n]
    missing = [path for path in REQUIRED_COMMIT_PATHS if path not in names]
    if missing:
        raise BundleError(f"커밋에 필수 파일이 없습니다: {missing}")
    bad = b2b.forbidden_names(names)
    if bad:
        raise BundleError(f"커밋에 인증 정보로 보이는 파일이 있습니다: {bad[:5]}")
    manifest = hashlib.sha256(b2b.commit_blob(sha, MANIFEST_SOURCE, repo).replace(b"\r\n", b"\n")).hexdigest()
    if manifest != pins["manifest_lf_sha256"]:
        raise BundleError(f"커밋의 manifest SHA-256 {manifest} ≠ r3 고정값")
    require_committed(sha, profiles.PROFILE_SCRIPT, repo,
                      "커밋의 기준값 모듈(restore_profiles_v140.py)이 작업 트리와 다릅니다 → 확정한 R3_PINS를 커밋한 뒤 빌드")
    meta = b2b.run_git(["show", "-s", "--format=%H%n%cI%n%s", sha], repo).decode("utf-8").splitlines()
    return {"commit": sha, "short": sha[:12], "committed_at": meta[1], "subject": meta[2],
            "manifest_lf_sha256": manifest, "files": len(names)}


def neo4j_release_files(sha: str, repo: Path = ROOT) -> dict[str, str]:
    """커밋 manifest에 적힌 neo4j/* 파일과 SHA-256 (Neo4j 복원 데이터 목록)."""
    manifest = json.loads(b2b.commit_blob(sha, MANIFEST_SOURCE, repo).decode("utf-8"))
    return {name: item["sha256"] for name, item in sorted((manifest.get("files") or {}).items())
            if name.startswith("neo4j/")}


# ---------------------------------------------------------------------------
# 3. 번들 ([로컬 쓰기] 출력 폴더만)
# ---------------------------------------------------------------------------
def bundle_name(sha: str) -> str:
    return f"{profiles.BUNDLE_NAME}-{sha[:12]}.zip"


def load_snapshot_record(key: str, snapshot: Path, record_path: Path, pins: dict[str, Any]) -> dict[str, Any]:
    spec, pinned = profiles.collection_spec(key), pins["collections"][key]
    if not Path(snapshot).is_file() or not Path(record_path).is_file():
        raise BundleError(f"{key} snapshot 또는 기록 파일이 없습니다")
    record = json.loads(Path(record_path).read_text(encoding="utf-8"))
    if (record.get("collection_key") != key or record.get("collection") != spec["collection"]
            or record.get("points") != pinned["points"] or record.get("dimensions") != spec["dimensions"]
            or record.get("vector_digest") != pinned["vector_digest"]
            or record.get("plan_sha256") != pinned["plan_sha256"]):
        raise BundleError(f"{key} snapshot 기록이 r3 고정값과 다릅니다")
    if restore.file_sha(snapshot) != record.get("snapshot_sha256") or Path(snapshot).stat().st_size != record.get(
            "snapshot_size_bytes"):
        raise BundleError(f"{key} snapshot 파일의 SHA-256·크기가 기록과 다릅니다")
    return record


def build_package(records: dict[str, dict[str, Any]], source: dict[str, Any], source_member: str,
                  neo4j_files: dict[str, str], pins: dict[str, Any]) -> dict[str, Any]:
    collections = {}
    for key in profiles.COLLECTION_KEYS:
        spec, record = profiles.collection_spec(key), records[key]
        collections[key] = {
            "collection": spec["collection"], "config": spec["config"], "dimensions": spec["dimensions"],
            "points": record["points"], "vector_digest": record["vector_digest"],
            "payload_digest": record["payload_digest"], "snapshot": spec["snapshot_member"],
            "snapshot_sha256": record["snapshot_sha256"], "snapshot_size_bytes": record["snapshot_size_bytes"],
            "qdrant_version": record["qdrant_version"], "embedding_model": spec["model"],
            "vector_provenance": {"source": record["source"], "plan_sha256": record["plan_sha256"],
                                  "plan_counts": record["plan_counts"]}}
    return {"schema_version": profiles.SCHEMA_VERSION, "bundle": profiles.BUNDLE_NAME,
            "release": profiles.DATASET_VERSION, "manifest_lf_sha256": source["manifest_lf_sha256"],
            "release_counts": dict(pins["release_counts"]), "collections": collections,
            "neo4j": {"version": f"{profiles.NEO4J_VERSION} {profiles.NEO4J_EDITION}", "image": profiles.NEO4J_IMAGE,
                      "data": "source ZIP의 data/releases/v1.4.0-candidate/neo4j/*", "release_files": neo4j_files},
            "restore_tool": RESTORE_MEMBER, "compose": f"compose.yaml ({Path(VERIFY_COMPOSE).name})",  # 프로필의 verify compose
            "source_commit": source["commit"], "source_archive": source_member,
            "source_note": "git archive <source_commit> 그대로. 미커밋·미추적 작업 파일은 포함되지 않는다",
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds")}


def check_zip_entries(archive: zipfile.ZipFile) -> list[str]:
    """
    번들 ZIP 엔트리가 모두 '평평한 일반 파일 이름'인지 확인한다 (풀기 전에 호출).
    거부: 하위 경로·절대 경로·드라이브(:)·역슬래시·제어 문자·'.'·'..'·빈 이름, 폴더 엔트리, 심볼릭 링크 등
    일반 파일이 아닌 유형, 암호화 엔트리, 중복 이름(대소문자만 다른 이름 포함 — Windows에서 같은 파일이 됨).
    """
    infos = archive.infolist()
    names = [info.filename for info in infos]
    for info in infos:
        name = info.filename
        file_type = (info.external_attr >> 16) & 0o170000
        if (name in ("", ".", "..") or name != PurePosixPath(name).name or "\\" in name or ":" in name
                or any(ord(ch) < 32 for ch in name) or info.is_dir()
                or file_type not in (0, stat.S_IFREG) or info.flag_bits & 0x1):
            raise BundleError("번들 ZIP에 하위 경로·특수 엔트리(폴더·링크·암호화 등)가 있습니다")
    if len(set(names)) != len(names) or len({n.casefold() for n in names}) != len(names):
        raise BundleError("번들 ZIP에 중복 이름이 있습니다")
    return names


def verify_bundle_zip(path: Path, expected_members: set[str] | None = None,
                      pins: dict[str, Any] | None = None) -> dict[str, Any]:
    """ZIP 엔트리 전체를 먼저 검사(check_zip_entries)한 뒤에만 새 임시 폴더에 풀어 SHA256SUMS 전체 대조 +
    r3 check_bundle로 복원 계약을 검증한다."""
    with zipfile.ZipFile(path) as archive:
        names = check_zip_entries(archive)
        if expected_members is not None and set(names) != expected_members:
            raise BundleError(f"번들 구성 파일이 다릅니다: 누락 {sorted(expected_members - set(names))}, "
                              f"추가 {sorted(set(names) - expected_members)}")
        if b2b.forbidden_names(names):
            raise BundleError("번들에 인증 정보로 보이는 파일이 있습니다")
        with tempfile.TemporaryDirectory() as folder:
            archive.extractall(folder)
            listed = {line.split(None, 1)[1].strip().lstrip("*")
                      for line in Path(folder, "SHA256SUMS").read_text(encoding="utf-8").splitlines() if line.strip()}
            if listed != set(names) - {"SHA256SUMS"}:
                raise BundleError("SHA256SUMS 목록과 ZIP 구성 파일이 다릅니다")
            try:
                package, _snapshots = restore_r3.check_bundle(folder, pins)
            except (ValueError, OSError) as error:
                raise BundleError(f"복원 계약 검증 실패: {error}") from error
            with zipfile.ZipFile(Path(folder, package["source_archive"])) as source:
                if b2b.forbidden_names(source.namelist()):
                    raise BundleError("소스 아카이브에 인증 정보로 보이는 파일이 있습니다")
    return {"members": sorted(names), "package": package}


def build_bundle(snapshots: dict[str, tuple[Path, Path]], source_sha: str, out_dir: Path = DEFAULT_OUT_DIR, *,
                 repo: Path = ROOT, confirm_write: bool = False, pins: dict[str, Any] | None = None) -> dict:
    """
    snapshots: {"b2": (snapshot, record), "b3": (snapshot, record)}.
    파일 보호 (2026-11-22 보완, create_snapshot과 같은 방식)
      - 최종 ZIP·.zip.partial·.zip.sha256·.zip.report.json 중 하나라도 있으면 쓰기 전에 거부 (지우거나 덮어쓰지 않음)
      - .zip.partial은 배타적으로 새로 만든다. 다른 작업이 먼저 만들었으면 거부
      - 실패 시 정리: 이번 실행이 만든 .partial(같은 파일 식별자)과 이번 실행이 만든 UUID staging 폴더만
      - 최종 ZIP은 하드 링크로 만든다 (같은 이름이 생겼으면 거부). .sha256·보고서는 새 파일로만 쓴다
      - ZIP 완성 뒤 .sha256·보고서 쓰기가 실패하면 이미 만든 ZIP(·.sha256)은 보존하고 오류로 멈춘다 (자동 재시도 없음)
    """
    if not confirm_write:
        raise BundleError("confirm_write=True 없이 번들을 만들지 않습니다")
    require_new_output_dir(out_dir)
    pins = profiles.require_finalized(pins)
    if set(snapshots) != set(profiles.COLLECTION_KEYS):
        raise BundleError("B2·B3 snapshot이 모두 필요합니다")
    sha = b2b.resolve_commit(source_sha, repo)
    out_dir = Path(out_dir)
    final = out_dir / bundle_name(sha)
    side = [final.with_name(final.name + ".sha256"), final.with_name(final.name + ".report.json")]
    partial = partial_path(final)
    existing = [path.name for path in (final, partial, *side) if path.exists()]
    if existing:
        raise BundleError(f"같은 이름의 번들·.partial·체크섬·보고서가 이미 있습니다 (덮어쓰거나 지우지 않음): {existing}")
    records = {key: load_snapshot_record(key, *snapshots[key], pins) for key in profiles.COLLECTION_KEYS}
    source = check_source_commit(sha, repo, pins)
    neo4j_files = neo4j_release_files(sha, repo)
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = out_dir / f".staging-{sha[:12]}-{uuid.uuid4().hex}"
    staging.mkdir()                                  # 이미 있으면 FileExistsError (기존 폴더는 건드리지 않음)
    stream = owned = None
    try:
        source_member = f"source-{sha[:12]}.zip"
        members = []
        for key in profiles.COLLECTION_KEYS:
            member = profiles.R3_COLLECTIONS[key]["snapshot_member"]
            shutil.copyfile(snapshots[key][0], staging / member)
            members.append(member)
        archived = b2b.git_archive(sha, staging / source_member, repo)
        (staging / "compose.yaml").write_bytes(b2b.commit_blob(sha, VERIFY_COMPOSE, repo))
        (staging / RESTORE_MEMBER).write_bytes(b2b.commit_blob(sha, profiles.RESTORE_SCRIPT, repo))
        package = build_package(records, source, source_member, neo4j_files, pins)
        (staging / "package.json").write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        members += [source_member, "compose.yaml", RESTORE_MEMBER, "package.json"]
        b2b.write_checksums(staging, members)
        restore_r3.check_bundle(staging, pins)
        try:
            stream, owned = create_owned(partial)    # 이번 실행 소유의 .zip.partial (배타적 생성)
        except FileExistsError as error:
            raise BundleError("다른 작업이 .zip.partial을 먼저 만들었습니다 (지우지 않음)") from error
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in [*members, "SHA256SUMS"]:
                archive.write(staging / name, arcname=name)
        stream.close()
        verified = verify_bundle_zip(partial, set(members) | {"SHA256SUMS"}, pins)
        link_no_overwrite(partial, final, "번들 ZIP")
        partial.unlink()
    except BaseException:
        if stream is not None:
            stream.close()
            remove_if_owned(partial, owned)          # 이번 실행이 만든 .partial만 지운다
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)   # 이번 실행이 만든 UUID staging 폴더만
    zip_sha = restore.file_sha(final)
    try:
        with side[0].open("x", encoding="utf-8") as checksum_stream:
            checksum_stream.write(f"{zip_sha} *{final.name}\n")
    except Exception as error:
        raise BundleError(f"번들 ZIP은 검증·보존했지만 .sha256을 만들지 못했습니다 ({type(error).__name__}). "
                          "같은 이름 재실행은 거부되므로 파일을 사람이 확인한다") from error
    report = {"status": "BUILT", "bundle": str(final), "bundle_sha256": zip_sha, "bundle_size_bytes": final.stat().st_size,
              "members": verified["members"], "package": package, "source": source,
              "source_archive_files": len(archived), "working_tree": b2b.working_tree_note(repo)}
    try:
        planner.write_new_json(side[1], report)
    except Exception as error:
        raise BundleError(f"번들 ZIP·.sha256은 보존했지만 보고서를 만들지 못했습니다 ({type(error).__name__}). "
                          "같은 이름 재실행은 거부되므로 파일을 사람이 확인한다") from error
    return report


def plan(source_sha: str | None, out_dir: Path = DEFAULT_OUT_DIR, repo: Path = ROOT) -> dict[str, Any]:
    """읽기 전용 계획. 기준값이 미확정이면 무엇이 남았는지만 보여 준다."""
    output: dict[str, Any] = {"mode": "plan", "writes": 0, "out_dir": str(out_dir),
                              "members": [s["snapshot_member"] for s in profiles.R3_COLLECTIONS.values()]
                              + ["source-<sha12>.zip", "compose.yaml", RESTORE_MEMBER, "package.json", "SHA256SUMS"],
                              "r3_pins_finalized": profiles.is_finalized(),
                              "missing_pins": profiles.missing_pins(), "working_tree": b2b.working_tree_note(repo)}
    snapshots = {}
    for key in profiles.COLLECTION_KEYS:
        snapshot, record = snapshot_paths(out_dir, key)
        snapshots[key] = {"snapshot": str(snapshot), "exists": snapshot.exists(), "record_exists": record.exists()}
    output["snapshots"] = snapshots
    if source_sha:
        sha = b2b.resolve_commit(source_sha, repo)
        output["bundle"] = str(Path(out_dir) / bundle_name(sha))
        output["bundle_exists"] = (Path(out_dir) / bundle_name(sha)).exists()
        if profiles.is_finalized():
            output["source"] = check_source_commit(sha, repo)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="r3 복원 번들 (기본 읽기 전용 계획)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--create-snapshot", action="store_true", help="[서버 쓰기] build 프로젝트 Qdrant snapshot 생성·내려받기")
    mode.add_argument("--build", action="store_true", help="[로컬 쓰기] 번들 ZIP 생성")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--collection", choices=profiles.COLLECTION_KEYS, help="--create-snapshot 대상")
    parser.add_argument("--plan", type=Path, help="--create-snapshot 전용, 해당 컬렉션의 재사용 계획 JSON")
    parser.add_argument("--source-commit", help="40자리 전체 커밋 SHA")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--qdrant-url", help=f"--create-snapshot 전용, http://127.0.0.1:{qdrant_r3.ISOLATED_QDRANT_PORT}만 허용")
    parser.add_argument("--expect-fingerprint", help="update_r3_collections dry-run fingerprint")
    args = parser.parse_args(argv)
    if (args.create_snapshot or args.build) and not args.confirm_write:
        parser.error("--create-snapshot·--build 에는 --confirm-write 가 필요합니다")
    if args.create_snapshot and not (args.qdrant_url and args.expect_fingerprint and args.collection and args.plan):
        parser.error("--create-snapshot 에는 --collection, --plan, --qdrant-url, --expect-fingerprint 가 필요합니다")
    if args.build and not args.source_commit:
        parser.error("--build 에는 --source-commit 이 필요합니다")
    if (args.create_snapshot or args.build) and profiles.is_preserved_output(args.out_dir):
        parser.error("--out-dir 이 보존 자산 폴더(#104 r3·r3b·이전 verify) 안입니다 → 쓰지 않음")
    client = None
    try:
        if args.create_snapshot:
            url = qdrant_r3.require_isolated_url(args.qdrant_url)
            profiles.require_finalized()
            release = planner.load_release()
            plan_obj = json.loads(args.plan.read_text(encoding="utf-8"))
            if plan_obj.get("collection_key") != args.collection:
                raise BundleError("--plan 의 컬렉션이 --collection 과 다릅니다")
            qdrant_r3.require_server_plan(plan_obj)
            client = qdrant_r3.make_isolated_client(url)
            result = create_snapshot(client, url, plan_obj, release, args.out_dir, confirm_write=True,
                                     expected_fingerprint=args.expect_fingerprint)
        elif args.build:
            result = build_bundle({key: snapshot_paths(args.out_dir, key) for key in profiles.COLLECTION_KEYS},
                                  args.source_commit, args.out_dir, confirm_write=True)
        else:
            result = plan(args.source_commit, args.out_dir)
        result = b2b.cli_success(result)
    except (BundleError, qdrant_r3.UpdateError, planner.PlanError, profiles.ProfileError, ValueError) as error:
        result = {"status": "FAILED", "error_type": type(error).__name__, "reason": str(error)[:800],
                  "action": "원본 자산은 바꾸지 않았다. 원인을 확인한 뒤 새 출력 경로로 다시 실행한다."}
    finally:
        if client is not None:
            client.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
