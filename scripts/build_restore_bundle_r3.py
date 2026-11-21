"""Issue #105: r3 복원 번들(B2·B3 snapshot + 소스 + 복원 도구 + Compose + package.json + SHA256SUMS) — 기본 읽기 전용 계획.

단계
  --create-snapshot --collection b2|b3 [서버 쓰기, build 프로젝트 Qdrant만]
      build Docker 소유 확인 → 격리 서버 → build 서버 기준 계획(basis=server_snapshot) 대비 상태 complete
      → Point 수·전체 벡터 digest = R3_PINS → dry-run fingerprint(서버 상태 + Docker 식별 정보)
      → verify_full_index → snapshot 생성 → 내려받기(.partial) → SHA-256·크기 대조 → fingerprint 재확인
      → snapshot-<key>.snapshot + snapshot-record-<key>.json (기존 파일 덮어쓰기 거부)
  --build [로컬 쓰기, 출력 폴더만]
      커밋 확인(40자리 SHA, manifest LF = R3_PINS, 필수 파일, 인증 파일 이름 없음,
               커밋의 기준값 모듈 = 작업 트리 것 — 줄바꿈 차이만 허용, 커밋되지 않은 R3_PINS 변경은 거부)
      → staging → check_bundle(r3) → <이름>.zip.partial → 압축 내용 전체 재검증 → 최종 이름 → .sha256·보고서
      번들 구성(평평한 파일): b2-r3-v1.12.4.snapshot, b3-r3-v1.12.4.snapshot, source-<sha12>.zip, compose.yaml,
      restore_final_v140_r3.py, package.json, SHA256SUMS
      Neo4j 복원 데이터는 source ZIP의 data/releases/v1.4.0-candidate/neo4j/*이며, 파일별 SHA-256을 package.json에 적는다.
R3_PINS가 확정되지 않으면 snapshot 생성·번들 생성 모두 거부한다. 기존 r2 번들·snapshot은 읽지도 바꾸지도 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
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
from scripts import update_r3_collections as qdrant_r3  # noqa: E402

DEFAULT_OUT_DIR = ROOT / ".cache" / "issue105-r3" / "bundle-r3"
VERIFY_COMPOSE = profiles.R3_ENVIRONMENTS["verify"]["compose"]
MANIFEST_SOURCE = f"{profiles.RELEASE_DIR}/manifest.json"
REQUIRED_COMMIT_PATHS = (VERIFY_COMPOSE, profiles.RESTORE_SCRIPT, profiles.PROFILE_SCRIPT,
                         "scripts/restore_final_v140.py", MANIFEST_SOURCE, "requirements.txt")
RESTORE_MEMBER = Path(profiles.RESTORE_SCRIPT).name
BundleError = b2b.BundleError


def snapshot_paths(out_dir: Path, key: str) -> tuple[Path, Path]:
    return Path(out_dir) / profiles.R3_COLLECTIONS[key]["snapshot_member"], Path(out_dir) / f"snapshot-record-{key}.json"


# ---------------------------------------------------------------------------
# 1. snapshot 생성 ([서버 쓰기] build 프로젝트 Qdrant만)
# ---------------------------------------------------------------------------
def create_snapshot(client, url: str, plan: dict[str, Any], release: dict[str, Any], out_dir: Path, *,
                    confirm_write: bool = False, expected_fingerprint: str | None = None,
                    pins: dict[str, Any] | None = None, environ=None, http_get=None, verify_slices=None,
                    docker=None) -> dict:
    if not confirm_write:
        raise BundleError("confirm_write=True 없이 snapshot을 만들지 않습니다")
    pins = profiles.require_finalized(pins)
    planner.validate_plan(plan, release, pins, require_pins=True)
    key = plan["collection_key"]
    spec, pinned = profiles.collection_spec(key), pins["collections"][key]
    qdrant_r3.require_isolated_url(url)
    identity = qdrant_r3.require_build_ownership(docker)
    api_key = qdrant_r3.read_secret(qdrant_r3.API_KEY_ENV, environ)
    target, record_path = snapshot_paths(out_dir, key)
    if target.exists() or record_path.exists():
        raise BundleError("snapshot·기록 파일이 이미 있습니다 (덮어쓰지 않음)")
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
    description = client.create_snapshot(collection_name=spec["collection"], wait=True)
    name = getattr(description, "name", "") or ""
    if not b2b.SNAPSHOT_NAME.match(name):
        raise BundleError("서버가 돌려준 snapshot 이름 형식이 예상과 다릅니다")
    if http_get is None:
        import requests
        http_get = requests.get
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    response = http_get(f"{url.rstrip('/')}/collections/{spec['collection']}/snapshots/{name}",
                        headers={"api-key": api_key}, timeout=qdrant_r3.UPLOAD_TIMEOUT_SECONDS, stream=True)
    try:
        if getattr(response, "status_code", None) != 200:
            raise BundleError(f"snapshot 내려받기 실패: HTTP {getattr(response, 'status_code', None)}")
        with partial.open("xb") as stream:
            for block in response.iter_content(chunk_size=1024 * 1024):
                stream.write(block)
        sha, size = restore.file_sha(partial), partial.stat().st_size
        if (description.checksum and description.checksum != sha) or (description.size and description.size != size):
            raise BundleError("내려받은 snapshot의 SHA-256·크기가 서버 값과 다릅니다")
        after = qdrant_r3.inspect_state(client, plan, release)
        after_identity = qdrant_r3.require_build_ownership(docker)
        if qdrant_r3.server_fingerprint(client, after, after_identity) != expected_fingerprint:
            raise BundleError("snapshot 후 컬렉션·컨테이너 fingerprint가 바뀌었습니다")
        partial.rename(target)
    except Exception:
        if partial.exists():
            partial.unlink()          # 이 함수가 만든 미완성 파일만 지운다
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
              "snapshot_size_bytes": size, "verify_full_index_passed": True,
              "plan_sha256": planner.plan_sha256(plan), "plan_counts": plan["counts"],
              "source": plan["source"], "manifest_lf_sha256": plan["manifest_lf_sha256"],
              "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "server_state_note": "서버 /qdrant/snapshots에 snapshot 파일이 남아 있다 (컬렉션 데이터는 불변)"}
    planner.write_new_json(record_path, record)
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
            "restore_tool": RESTORE_MEMBER, "compose": "compose.yaml (issue105_r3_verify.compose.yaml)",
            "source_commit": source["commit"], "source_archive": source_member,
            "source_note": "git archive <source_commit> 그대로. 미커밋·미추적 작업 파일은 포함되지 않는다",
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds")}


def verify_bundle_zip(path: Path, expected_members: set[str] | None = None,
                      pins: dict[str, Any] | None = None) -> dict[str, Any]:
    """ZIP을 새 임시 폴더에 풀어 SHA256SUMS 전체 대조 + r3 check_bundle로 복원 계약을 검증한다."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if any(n != PurePosixPath(n).name or n in ("", ".", "..") for n in names) or len(set(names)) != len(names):
            raise BundleError("번들 ZIP에 하위 경로·중복 이름이 있습니다")
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
    """snapshots: {"b2": (snapshot, record), "b3": (snapshot, record)}. 실패하면 이 함수가 만든 staging·.partial만 지운다."""
    if not confirm_write:
        raise BundleError("confirm_write=True 없이 번들을 만들지 않습니다")
    pins = profiles.require_finalized(pins)
    if set(snapshots) != set(profiles.COLLECTION_KEYS):
        raise BundleError("B2·B3 snapshot이 모두 필요합니다")
    sha = b2b.resolve_commit(source_sha, repo)
    out_dir = Path(out_dir)
    final = out_dir / bundle_name(sha)
    side = [final.with_name(final.name + ".sha256"), final.with_name(final.name + ".report.json")]
    if final.exists() or any(path.exists() for path in side):
        raise BundleError("같은 이름의 번들·체크섬·보고서가 이미 있습니다 (덮어쓰지 않음)")
    records = {key: load_snapshot_record(key, *snapshots[key], pins) for key in profiles.COLLECTION_KEYS}
    source = check_source_commit(sha, repo, pins)
    neo4j_files = neo4j_release_files(sha, repo)
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = out_dir / f".staging-{sha[:12]}-{uuid.uuid4().hex[:8]}"
    partial = final.with_name(final.name + ".partial")
    staging.mkdir()
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
        with zipfile.ZipFile(partial, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in [*members, "SHA256SUMS"]:
                archive.write(staging / name, arcname=name)
        verified = verify_bundle_zip(partial, set(members) | {"SHA256SUMS"}, pins)
        if final.exists():
            raise BundleError("같은 이름의 번들이 생겼습니다 (덮어쓰지 않음)")
        partial.rename(final)
    except Exception:
        if partial.exists():
            partial.unlink()
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    zip_sha = restore.file_sha(final)
    with side[0].open("x", encoding="utf-8") as stream:
        stream.write(f"{zip_sha} *{final.name}\n")
    report = {"status": "BUILT", "bundle": str(final), "bundle_sha256": zip_sha, "bundle_size_bytes": final.stat().st_size,
              "members": verified["members"], "package": package, "source": source,
              "source_archive_files": len(archived), "working_tree": b2b.working_tree_note(repo)}
    planner.write_new_json(side[1], report)
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
