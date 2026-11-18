"""Issue #91: r2 복원 번들 (restore_final_v140.py 호환) — 기본은 읽기 전용 계획.

번들 계약 (scripts/restore_final_v140.py check_bundle·restore와 같음, 평평한 폴더 하나):
  SHA256SUMS        "<sha256>  <이름>" (LF). package.json과 snapshot을 반드시 포함
  package.json      collection·dimensions(1024)·points(1139)·snapshot·snapshot_sha256 (check_bundle)
                    manifest_lf_sha256·vector_digest (restore가 원본 소스·복원 결과와 대조)
  <snapshot>        #91 Qdrant(56446)에서 만든 B2 1,139 Point snapshot (벡터 비트 보존)
  source-<sha12>.zip  확정 커밋의 git archive (작업 트리의 미커밋 파일은 들어가지 않는다)
  compose.yaml / restore_final_v140.py   같은 커밋의 issue43_restore.compose.yaml / 복원 도구

실행 모드 (서로 분리)
  계획(기본)          : 쓰기 없음. 커밋·manifest·복원 도구 기준·포함 파일·금지 파일을 점검해 출력한다.
  --create-snapshot   : [서버 쓰기] #91 Qdrant에 snapshot을 만들고 내려받는다(서버 /qdrant/snapshots에 파일이 생긴다).
                        Point·벡터·payload는 바꾸지 않으며, 전후 fingerprint가 같아야 한다.
  --build             : [로컬 쓰기] .cache 아래 출력 폴더에 ZIP을 만든다. 같은 이름이 있으면 거부한다.
                        .partial로 쓰고 압축 내용 전체를 다시 검증한 뒤에만 최종 이름으로 바꾼다.
기존 r1·pr29 번들·덤프·snapshot은 읽기만 한다. 인증 정보(.env, *.clixml 등)는 번들에 넣지 않는다.

실행 (프로젝트 루트, .venv):
    python scripts/build_restore_bundle_v140.py --source-commit <40자리 SHA>
    python scripts/build_restore_bundle_v140.py --create-snapshot --confirm-write --qdrant-url http://127.0.0.1:56446 \\
        --expect-fingerprint <update_b2_from_snapshot dry-run fingerprint>
    python scripts/build_restore_bundle_v140.py --build --confirm-write --source-commit <SHA> \\
        --snapshot <내려받은 snapshot> --snapshot-record <snapshot-record.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
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
from scripts import load_neo4j_r2_isolated as neo4j_r2  # noqa: E402
from scripts import restore_final_v140 as restore  # noqa: E402
from scripts import seed_b2_from_r1_vectors as seed  # noqa: E402
from scripts import update_b2_from_snapshot as qdrant_r2  # noqa: E402

DEFAULT_OUT_DIR = ROOT / ".cache" / "issue91-r2-20261119" / "bundle-r2"
SNAPSHOT_MEMBER = "b2-r2-v1.12.4.snapshot"
COMPOSE_SOURCE = "docs/development/issue43_restore.compose.yaml"
RESTORE_SOURCE = "scripts/restore_final_v140.py"
MANIFEST_SOURCE = "data/releases/v1.4.0-candidate/manifest.json"
REQUIRED_COMMIT_PATHS = (COMPOSE_SOURCE, RESTORE_SOURCE, MANIFEST_SOURCE, "requirements.txt")
EXPECTED_MANIFEST = seed.MANIFEST_LF_SHA256
R2_POINTS = neo4j_r2.QDRANT_R2_POINTS
R2_VECTOR_DIGEST = neo4j_r2.QDRANT_R2_VECTOR_DIGEST
# 벡터 출처 (seed 덤프 검증·10단계 08-index.json 기록)
VECTOR_PROVENANCE = {
    "reused_points": 1135, "reused_index_run_id": seed.DUMP_RUN["index_run_id"],
    "reused_vector_subset_digest": qdrant_r2.REUSE_SUBSET_DIGEST,
    "new_points": 4, "new_index_run_id": "RUN_FULL_A_B2_V140_20261119_015749",
    "new_point_ids": sorted(set(qdrant_r2.DELETE_TARGETS) | set(qdrant_r2.NEW_POINT_IDS)),
    "embedding_model": seed.DUMP_RUN["embedding_model"],
    "embedding_model_digest": "ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d",
    "new_points_ollama_version": "0.40.16",
    "reused_points_ollama_version": "unknown (기존 문서상 0.40.15로 추정, 직접 기록 없음)",
}
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SNAPSHOT_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,200}\.snapshot$")
# 번들·소스 아카이브에 들어가면 안 되는 이름 (값은 보지 않고 경로만 검사)
FORBIDDEN_NAME = re.compile(r"(^|/)(\.env(\.[^/]*)?|[^/]*\.clixml|[^/]*\.pem|[^/]*\.key|id_rsa[^/]*)$", re.IGNORECASE)
ALLOWED_ENV_EXAMPLE = re.compile(r"(^|/)\.env\.example$")


class BundleError(ValueError):
    """번들을 만들 수 없음. 메시지에 비밀값을 넣지 않는다."""


def forbidden_names(names) -> list[str]:
    return sorted(n for n in names if FORBIDDEN_NAME.search(n) and not ALLOWED_ENV_EXAMPLE.search(n))


# ---------------------------------------------------------------------------
# 1. 소스 커밋 (git, 읽기 전용)
# ---------------------------------------------------------------------------
def run_git(args: list[str], repo: Path = ROOT) -> bytes:
    # core.autocrlf=false: git archive가 사용자 설정(Windows 기본 true)에 따라 줄바꿈을 CRLF로 바꾸지 않게 한다
    # → 소스 ZIP은 커밋된 바이트 그대로이고 PC 설정과 무관하게 같은 내용이 된다.
    result = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=repo, capture_output=True)
    if result.returncode != 0:
        raise BundleError(f"git {args[0]} 실패 (exit {result.returncode})")
    return result.stdout


def resolve_commit(value: str | None, repo: Path = ROOT) -> str:
    """40자리 전체 SHA만 받는다 (HEAD·브랜치·짧은 SHA는 가리키는 대상이 바뀔 수 있어 거부)."""
    if not value or not FULL_SHA.match(value):
        raise BundleError("--source-commit 에는 40자리 전체 커밋 SHA를 넣어야 합니다 (HEAD·브랜치·짧은 SHA 거부)")
    resolved = run_git(["rev-parse", "--verify", "--quiet", f"{value}^{{commit}}"], repo).decode().strip()
    if resolved != value:
        raise BundleError("커밋 SHA가 저장소에서 확인되지 않습니다")
    return resolved


def commit_blob(sha: str, path: str, repo: Path = ROOT) -> bytes:
    return run_git(["show", f"{sha}:{path}"], repo)


def check_source_commit(sha: str, repo: Path = ROOT, expected_manifest: str = EXPECTED_MANIFEST) -> dict[str, Any]:
    """커밋 안의 manifest(LF SHA-256)·복원 도구 기준값·필수 파일·금지 파일을 확인한다."""
    names = run_git(["ls-tree", "-r", "--name-only", sha], repo).decode("utf-8").splitlines()
    missing = [path for path in REQUIRED_COMMIT_PATHS if path not in names]
    if missing:
        raise BundleError(f"커밋에 필수 파일이 없습니다: {missing}")
    bad = forbidden_names(names)
    if bad:
        raise BundleError(f"커밋에 인증 정보로 보이는 파일이 있습니다: {bad[:5]}")
    manifest = hashlib.sha256(commit_blob(sha, MANIFEST_SOURCE, repo).replace(b"\r\n", b"\n")).hexdigest()
    if manifest != expected_manifest:
        raise BundleError(f"커밋의 manifest SHA-256 {manifest} ≠ 고정값")
    tool = commit_blob(sha, RESTORE_SOURCE, repo).decode("utf-8")
    for needle in (f'COLLECTION = "{restore.COLLECTION}"', f"SNAPSHOT_POINTS = {R2_POINTS}",
                   f"SNAPSHOT_DIMENSIONS = {restore.SNAPSHOT_DIMENSIONS}"):
        if needle not in tool:
            raise BundleError(f"커밋의 복원 도구 기준이 r2와 다릅니다: {needle}")
    meta = run_git(["show", "-s", "--format=%H%n%cI%n%s", sha], repo).decode("utf-8").splitlines()
    return {"commit": sha, "short": sha[:12], "committed_at": meta[1], "subject": meta[2],
            "manifest_lf_sha256": manifest, "files": len(names)}


def working_tree_note(repo: Path = ROOT) -> dict[str, Any]:
    """작업 트리 상태 (정보용). 번들 소스에는 커밋된 내용만 들어간다."""
    lines = run_git(["status", "--porcelain"], repo).decode("utf-8").splitlines()
    return {"tracked_changes": sum(1 for line in lines if not line.startswith("??")),
            "untracked": sum(1 for line in lines if line.startswith("??")),
            "note": "source ZIP은 git archive <commit>이므로 미커밋·미추적 파일은 포함되지 않는다"}


def git_archive(sha: str, destination: Path, repo: Path = ROOT) -> list[str]:
    run_git(["archive", "--format=zip", "-o", str(destination), sha], repo)
    with zipfile.ZipFile(destination) as archive:
        names = archive.namelist()
    bad = forbidden_names(names)
    if bad:
        raise BundleError(f"소스 아카이브에 인증 정보로 보이는 파일이 있습니다: {bad[:5]}")
    return names


# ---------------------------------------------------------------------------
# 2. snapshot 생성 ([서버 쓰기] #91 Qdrant 56446만)
# ---------------------------------------------------------------------------
def create_snapshot(client, url: str, ref: dict[str, Any], out_dir: Path, *, confirm_write: bool = False,
                    expected_fingerprint: str | None = None, environ=None, http_get=None, verify_slices=None,
                    expected_points: int = R2_POINTS, vector_digest: str = R2_VECTOR_DIGEST) -> dict[str, Any]:
    """
    순서: 격리 서버 → 상태 complete·Point 수·전체 벡터 digest → fingerprint → verify_full_index
          → create_snapshot → 내려받기(.partial) → SHA-256·크기 대조 → 같은 fingerprint 재확인 → 기록.
    서버에는 snapshot 파일이 남는다 (컬렉션 데이터는 바꾸지 않는다). 지우지 않는다.
    """
    if not confirm_write:
        raise BundleError("confirm_write=True 없이 snapshot을 만들지 않습니다")
    qdrant_r2.require_isolated_url(url)
    api_key = qdrant_r2.read_api_key(environ)
    target = Path(out_dir) / SNAPSHOT_MEMBER
    record_path = Path(out_dir) / "snapshot-record.json"
    if target.exists() or record_path.exists():
        raise BundleError("snapshot·기록 파일이 이미 있습니다 (덮어쓰지 않음)")
    qdrant_r2.require_isolated_server(client)
    state = qdrant_r2.inspect_state(client, ref)
    if (state["state"] != "complete" or state["points"] != expected_points
            or state["full_vector_digest"] != vector_digest):
        raise BundleError(f"snapshot 전 상태 이상: {state['state']}, Point {state['points']}")
    qdrant_r2.require_fingerprint(client, state, expected_fingerprint)
    slices = (verify_slices or (lambda: fi.verify_full_index(seed.CONFIG, client=client, chunks=ref["chunks"],
                                                             documents=ref["documents"])))()
    if not slices.get("passed"):
        raise BundleError("verify_full_index 실패 → snapshot을 만들지 않음")

    description = client.create_snapshot(collection_name=qdrant_r2.COLLECTION, wait=True)
    name = getattr(description, "name", "") or ""
    if not SNAPSHOT_NAME.match(name):
        raise BundleError("서버가 돌려준 snapshot 이름 형식이 예상과 다릅니다")
    if http_get is None:
        import requests
        http_get = requests.get
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    response = http_get(f"{url.rstrip('/')}/collections/{qdrant_r2.COLLECTION}/snapshots/{name}",
                        headers={"api-key": api_key}, timeout=qdrant_r2.UPLOAD_TIMEOUT_SECONDS, stream=True)
    try:
        if getattr(response, "status_code", None) != 200:
            raise BundleError(f"snapshot 내려받기 실패: HTTP {getattr(response, 'status_code', None)}")
        with partial.open("xb") as stream:
            for block in response.iter_content(chunk_size=1024 * 1024):
                stream.write(block)
        sha, size = restore.file_sha(partial), partial.stat().st_size
        if (description.checksum and description.checksum != sha) or (description.size and description.size != size):
            raise BundleError("내려받은 snapshot의 SHA-256·크기가 서버 값과 다릅니다")
        after = qdrant_r2.inspect_state(client, ref)
        if qdrant_r2.server_fingerprint(client, after) != expected_fingerprint:
            raise BundleError("snapshot 후 컬렉션 fingerprint가 바뀌었습니다")
        partial.rename(target)
    except Exception:
        if partial.exists():
            partial.unlink()          # 이 함수가 만든 미완성 파일만 지운다
        raise
    finally:
        close = getattr(response, "close", None)
        if close:
            close()
    record = {"collection": qdrant_r2.COLLECTION, "points": after["points"], "dimensions": restore.SNAPSHOT_DIMENSIONS,
              "vector_digest": after["full_vector_digest"], "payload_digest": after["payload_digest"],
              "fingerprint": expected_fingerprint, "qdrant_version": qdrant_r2.EXPECTED_QDRANT_VERSION,
              "server_snapshot_name": name, "snapshot_file": SNAPSHOT_MEMBER, "snapshot_sha256": sha,
              "snapshot_size_bytes": size, "verify_full_index_passed": True,
              "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "server_state_note": "서버 /qdrant/snapshots에 snapshot 파일이 남아 있다 (컬렉션 데이터는 불변)"}
    with record_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return record


# ---------------------------------------------------------------------------
# 3. 번들 생성 ([로컬 쓰기] 출력 폴더만)
# ---------------------------------------------------------------------------
def bundle_name(sha: str) -> str:
    return f"rag-final-v140-r2-{sha[:12]}.zip"


def load_snapshot_record(snapshot: Path, record_path: Path, expected_points: int = R2_POINTS,
                         vector_digest: str = R2_VECTOR_DIGEST) -> dict[str, Any]:
    if not Path(snapshot).is_file() or not Path(record_path).is_file():
        raise BundleError("snapshot 또는 snapshot 기록 파일이 없습니다")
    record = json.loads(Path(record_path).read_text(encoding="utf-8"))
    if (record.get("collection") != restore.COLLECTION or record.get("points") != expected_points
            or record.get("dimensions") != restore.SNAPSHOT_DIMENSIONS or record.get("vector_digest") != vector_digest):
        raise BundleError("snapshot 기록의 컬렉션·Point·차원·vector_digest가 r2 기준과 다릅니다")
    if restore.file_sha(snapshot) != record.get("snapshot_sha256") or Path(snapshot).stat().st_size != record.get(
            "snapshot_size_bytes"):
        raise BundleError("snapshot 파일의 SHA-256·크기가 기록과 다릅니다")
    return record


def build_package(record: dict[str, Any], source: dict[str, Any], source_member: str) -> dict[str, Any]:
    return {
        "bundle": "rag-final-v140-r2", "release": seed.DATASET_VERSION,
        "collection": restore.COLLECTION, "dimensions": restore.SNAPSHOT_DIMENSIONS, "points": record["points"],
        "snapshot": SNAPSHOT_MEMBER, "snapshot_sha256": record["snapshot_sha256"],
        "snapshot_size_bytes": record["snapshot_size_bytes"], "qdrant_version": record["qdrant_version"],
        "manifest_lf_sha256": source["manifest_lf_sha256"], "vector_digest": record["vector_digest"],
        "release_counts": dict(zip(("policies", "documents", "release_chunks", "b2_points", "neo4j_nodes",
                                    "neo4j_relationships", "requires_condition"), restore.EXPECTED)),
        "neo4j_image": "neo4j 5.26.32 community (issue43_restore.compose.yaml digest)",
        "source_commit": source["commit"], "source_archive": source_member,
        "source_note": "git archive <source_commit> 그대로. 미커밋·미추적 작업 파일은 포함되지 않는다",
        "vector_provenance": VECTOR_PROVENANCE,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def write_checksums(folder: Path, members: list[str]) -> None:
    lines = "".join(f"{restore.file_sha(folder / name)}  {name}\n" for name in sorted(members))
    (folder / "SHA256SUMS").write_bytes(lines.encode("utf-8"))


def verify_bundle_zip(path: Path, expected_members: set[str] | None = None) -> dict[str, Any]:
    """ZIP을 새 임시 폴더에 풀어 SHA256SUMS 전체 대조 + restore.check_bundle로 복원 계약을 검증한다."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if any(n != PurePosixPath(n).name or n in ("", ".", "..") for n in names) or len(set(names)) != len(names):
            raise BundleError("번들 ZIP에 하위 경로·중복 이름이 있습니다")
        if expected_members is not None and set(names) != expected_members:
            raise BundleError(f"번들 구성 파일이 다릅니다: 누락 {sorted(expected_members - set(names))}, "
                              f"추가 {sorted(set(names) - expected_members)}")
        if forbidden_names(names):
            raise BundleError("번들에 인증 정보로 보이는 파일이 있습니다")
        with tempfile.TemporaryDirectory() as folder:
            archive.extractall(folder)
            listed = {line.split(None, 1)[1].strip().lstrip("*")
                      for line in Path(folder, "SHA256SUMS").read_text(encoding="utf-8").splitlines() if line.strip()}
            if listed != set(names) - {"SHA256SUMS"}:
                raise BundleError("SHA256SUMS 목록과 ZIP 구성 파일이 다릅니다")
            try:
                package, _snapshot = restore.check_bundle(folder)
            except (ValueError, OSError) as error:
                raise BundleError(f"복원 계약 검증 실패: {error}") from error
            with zipfile.ZipFile(Path(folder, package["source_archive"])) as source:
                if forbidden_names(source.namelist()):
                    raise BundleError("소스 아카이브에 인증 정보로 보이는 파일이 있습니다")
    return {"members": sorted(names), "package": package}


def build_bundle(snapshot: Path, record_path: Path, source_sha: str, out_dir: Path = DEFAULT_OUT_DIR, *,
                 repo: Path = ROOT, confirm_write: bool = False, expected_manifest: str = EXPECTED_MANIFEST,
                 expected_points: int = R2_POINTS, vector_digest: str = R2_VECTOR_DIGEST) -> dict[str, Any]:
    """
    staging(출력 폴더 안 새 임시 폴더)에 구성 파일을 만들고 check_bundle로 검증 → <이름>.zip.partial로 압축
    → 압축 내용 전체 재검증 → 최종 이름으로 바꾼 뒤 ZIP SHA-256(.sha256)과 보고서를 쓴다.
    실패하면 이 함수가 만든 staging·.partial만 지운다. 입력 snapshot·기록·저장소는 바꾸지 않는다.
    """
    if not confirm_write:
        raise BundleError("confirm_write=True 없이 번들을 만들지 않습니다")
    sha = resolve_commit(source_sha, repo)
    out_dir = Path(out_dir)
    final = out_dir / bundle_name(sha)
    side = [final.with_name(final.name + ".sha256"), final.with_name(final.name + ".report.json")]
    if final.exists() or any(path.exists() for path in side):
        raise BundleError("같은 이름의 번들·체크섬·보고서가 이미 있습니다 (덮어쓰지 않음)")
    record = load_snapshot_record(snapshot, record_path, expected_points, vector_digest)
    source = check_source_commit(sha, repo, expected_manifest)
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = out_dir / f".staging-{sha[:12]}-{uuid.uuid4().hex[:8]}"
    partial = final.with_name(final.name + ".partial")
    staging.mkdir()
    try:
        source_member = f"source-{sha[:12]}.zip"
        shutil.copyfile(snapshot, staging / SNAPSHOT_MEMBER)
        archived = git_archive(sha, staging / source_member, repo)
        (staging / "compose.yaml").write_bytes(commit_blob(sha, COMPOSE_SOURCE, repo))
        (staging / "restore_final_v140.py").write_bytes(commit_blob(sha, RESTORE_SOURCE, repo))
        package = build_package(record, source, source_member)
        (staging / "package.json").write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        members = [SNAPSHOT_MEMBER, source_member, "compose.yaml", "restore_final_v140.py", "package.json"]
        write_checksums(staging, members)
        restore.check_bundle(staging)
        with zipfile.ZipFile(partial, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in [*members, "SHA256SUMS"]:
                archive.write(staging / name, arcname=name)
        verified = verify_bundle_zip(partial, set(members) | {"SHA256SUMS"})
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
              "source_archive_files": len(archived), "working_tree": working_tree_note(repo)}
    with side[1].open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


# ---------------------------------------------------------------------------
# 4. 계획 (읽기 전용)
# ---------------------------------------------------------------------------
def plan(source_sha: str | None, out_dir: Path = DEFAULT_OUT_DIR, snapshot: Path | None = None,
         record_path: Path | None = None, repo: Path = ROOT) -> dict[str, Any]:
    output: dict[str, Any] = {"mode": "plan", "writes": 0, "out_dir": str(out_dir),
                              "members": [SNAPSHOT_MEMBER, "source-<sha12>.zip", "compose.yaml",
                                          "restore_final_v140.py", "package.json", "SHA256SUMS"],
                              "working_tree": working_tree_note(repo)}
    sha = resolve_commit(source_sha, repo)
    output["source"] = check_source_commit(sha, repo)
    output["bundle"] = str(Path(out_dir) / bundle_name(sha))
    output["bundle_exists"] = (Path(out_dir) / bundle_name(sha)).exists()
    if snapshot and record_path:
        output["snapshot_record"] = load_snapshot_record(snapshot, record_path)
    else:
        output["snapshot_record"] = "아직 없음 (--create-snapshot 단계에서 생성)"
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="r2 복원 번들 (기본 읽기 전용 계획)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--create-snapshot", action="store_true", help="[서버 쓰기] #91 Qdrant snapshot 생성·내려받기")
    mode.add_argument("--build", action="store_true", help="[로컬 쓰기] 번들 ZIP 생성")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--source-commit", help="40자리 전체 커밋 SHA")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--qdrant-url", help="--create-snapshot 전용, http://127.0.0.1:56446만 허용")
    parser.add_argument("--expect-fingerprint", help="update_b2_from_snapshot dry-run fingerprint")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--snapshot-record", type=Path)
    args = parser.parse_args(argv)
    if (args.create_snapshot or args.build) and not args.confirm_write:
        parser.error("--create-snapshot·--build 에는 --confirm-write 가 필요합니다")
    if args.create_snapshot and not (args.qdrant_url and args.expect_fingerprint):
        parser.error("--create-snapshot 에는 --qdrant-url 과 --expect-fingerprint 가 필요합니다")
    if args.build and not (args.source_commit and args.snapshot and args.snapshot_record):
        parser.error("--build 에는 --source-commit, --snapshot, --snapshot-record 가 필요합니다")
    client = None
    try:
        if args.create_snapshot:
            url = qdrant_r2.require_isolated_url(args.qdrant_url)
            ref = qdrant_r2.build_reference()
            client = qdrant_r2.make_isolated_client(url)
            result = create_snapshot(client, url, ref, args.out_dir, confirm_write=True,
                                     expected_fingerprint=args.expect_fingerprint)
        elif args.build:
            result = build_bundle(args.snapshot, args.snapshot_record, args.source_commit, args.out_dir,
                                  confirm_write=True)
        else:
            result = plan(args.source_commit, args.out_dir, args.snapshot, args.snapshot_record)
        result = {"status": "PASS", **result}
    except (BundleError, qdrant_r2.UpdateError, seed.SeedPlanError, ValueError) as error:
        result = {"status": "FAILED", "error_type": type(error).__name__, "reason": str(error)[:800],
                  "action": "원본 자산은 바꾸지 않았다. 원인을 확인한 뒤 새 출력 경로로 다시 실행한다."}
    finally:
        if client is not None:
            client.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
