"""Issue #105 r3 번들 도구(scripts/build_restore_bundle_r3.py) 테스트.

임시 Git 저장소(sample_only)와 가짜 snapshot 바이트로만 검증한다. 실제 Qdrant·Docker·snapshot 생성은 없다.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r3_fixtures import FakeDocker, Sample, build_container, good_build_docker, sample_vector  # noqa: E402

from scripts import build_restore_bundle_r3 as bundle  # noqa: E402
from scripts import build_restore_bundle_v140 as b2b  # noqa: E402
from scripts import plan_r3_vector_reuse as planner  # noqa: E402
from scripts import restore_final_v140 as restore  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402
from scripts import update_r3_collections as update  # noqa: E402

ENV = {update.API_KEY_ENV: "sample_only_r3_build_key"}
URL = f"http://127.0.0.1:{update.ISOLATED_QDRANT_PORT}"


def git(repo, *args):
    return subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args], cwd=repo,
                          capture_output=True, check=True).stdout.decode().strip()


class TempRepo:
    """r3 필수 파일만 담은 sample_only 저장소. manifest는 실제 파일을 복사한다 (고정값은 테스트 pins로 넘긴다)."""

    def __init__(self, root: Path):
        self.path = root / "repo"
        for rel in bundle.REQUIRED_COMMIT_PATHS:
            target = self.path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundle.ROOT / rel, target)
        git(self.path, "init", "-q")
        git(self.path, "add", "-A")
        git(self.path, "-c", "user.name=sample_only", "-c", "user.email=sample_only@example.invalid",
            "commit", "-q", "-m", "sample_only r3 base")
        self.sha = git(self.path, "rev-parse", "HEAD")
        self.manifest = hashlib.sha256((self.path / bundle.MANIFEST_SOURCE).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        (self.path / ".env").write_text("SAMPLE_ONLY_SETTING=sample_only\n", encoding="utf-8")   # 미추적 (번들 제외)


def make_pins(manifest):
    pins = profiles.r3_pins()
    pins["manifest_lf_sha256"] = manifest
    pins["release_counts"] = {k: 1 for k in profiles.RELEASE_COUNT_KEYS}
    for key in profiles.COLLECTION_KEYS:
        pins["collections"][key].update(points=6, vector_digest=key[-1] * 64,
                                        reuse_count=3, reuse_vector_digest="c" * 64, embed_count=3, delete_count=2,
                                        remove_count=0, plan_sha256="e" * 64)
    return pins


def write_snapshots(folder: Path, pins):
    folder.mkdir(parents=True, exist_ok=True)
    result = {}
    for key in profiles.COLLECTION_KEYS:
        snapshot, record_path = bundle.snapshot_paths(folder, key)
        data = f"sample_only r3 {key} snapshot".encode()
        snapshot.write_bytes(data)
        spec = profiles.collection_spec(key)
        record = {"collection_key": key, "collection": spec["collection"], "config": spec["config"], "points": 6,
                  "dimensions": 1024, "vector_digest": pins["collections"][key]["vector_digest"],
                  "payload_digest": "9" * 64, "snapshot_sha256": hashlib.sha256(data).hexdigest(),
                  "snapshot_size_bytes": len(data), "qdrant_version": "1.12.4",
                  "plan_sha256": pins["collections"][key]["plan_sha256"],
                  "plan_counts": {"reuse": 3, "embed": 3}, "source": {"label": "sample_only"}}
        record_path.write_text(json.dumps(record), encoding="utf-8")
        result[key] = (snapshot, record_path)
    return result


class CommitEolTests(unittest.TestCase):
    """P1-1: Windows core.autocrlf=true(blob LF·작업 트리 CRLF)에서도 줄바꿈 차이만 있으면 통과, 내용 변경은 거부."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make_repo(self, autocrlf: bool):
        path = Path(self.tmp.name) / f"repo-autocrlf-{str(autocrlf).lower()}"
        for rel in bundle.REQUIRED_COMMIT_PATHS:
            target = path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            data = profiles.eol_normalized((bundle.ROOT / rel).read_bytes())
            target.write_bytes(data.replace(b"\n", b"\r\n") if autocrlf else data)   # autocrlf=true 작업 트리는 CRLF
        value = "true" if autocrlf else "false"
        run = lambda *args: subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", f"core.autocrlf={value}", *args],  # noqa: E731
                                           cwd=path, capture_output=True, check=True).stdout.decode().strip()
        run("init", "-q")
        run("config", "core.autocrlf", value)
        run("add", "-A")
        run("-c", "user.name=sample_only", "-c", "user.email=sample_only@example.invalid", "commit", "-q", "-m", "eol")
        sha = run("rev-parse", "HEAD")
        manifest = hashlib.sha256(profiles.eol_normalized((path / bundle.MANIFEST_SOURCE).read_bytes())).hexdigest()
        return path, sha, make_pins(manifest)

    def test_autocrlf_true_crlf_worktree_and_lf_blob_is_accepted(self):
        path, sha, pins = self.make_repo(True)
        blob = b2b.commit_blob(sha, profiles.PROFILE_SCRIPT, path)
        work = (path / profiles.PROFILE_SCRIPT).read_bytes()
        self.assertNotIn(b"\r\n", blob)                       # 전제: blob은 LF
        self.assertIn(b"\r\n", work)                          # 전제: 작업 트리는 CRLF (이전 코드는 여기서 항상 거부)
        self.assertNotEqual(blob, work)
        self.assertEqual(bundle.check_source_commit(sha, path, pins)["commit"], sha)

    def test_autocrlf_false_eol_only_difference_is_accepted(self):
        path, sha, pins = self.make_repo(False)
        work = path / profiles.PROFILE_SCRIPT
        work.write_bytes(work.read_bytes().replace(b"\n", b"\r\n"))       # 편집기가 줄바꿈만 바꾼 경우
        self.assertEqual(bundle.check_source_commit(sha, path, pins)["commit"], sha)

    def test_content_change_or_uncommitted_pins_are_refused_in_both_modes(self):
        for autocrlf in (True, False):
            with self.subTest(autocrlf=autocrlf):
                path, sha, pins = self.make_repo(autocrlf)
                work = path / profiles.PROFILE_SCRIPT
                original = work.read_bytes()
                newline = b"\r\n" if autocrlf else b"\n"
                changed = original.replace(b'"manifest_lf_sha256": None,', b'"manifest_lf_sha256": "' + b"a" * 64 + b'",', 1)
                self.assertNotEqual(changed, original)
                work.write_bytes(changed)                                  # 커밋하지 않은 R3_PINS 확정값
                with self.assertRaisesRegex(b2b.BundleError, "기준값 모듈"):
                    bundle.check_source_commit(sha, path, pins)
                work.write_bytes(original + b"# sample_only" + newline)    # 내용 한 줄 추가
                with self.assertRaisesRegex(b2b.BundleError, "기준값 모듈"):
                    bundle.check_source_commit(sha, path, pins)
                work.unlink()
                with self.assertRaisesRegex(b2b.BundleError, "기준값 모듈"):
                    bundle.check_source_commit(sha, path, pins)


class BuildBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = TempRepo(Path(self.tmp.name))
        self.pins = make_pins(self.repo.manifest)
        self.snapshots = write_snapshots(Path(self.tmp.name) / "snaps", self.pins)
        self.out = Path(self.tmp.name) / "out"

    def build(self, **kwargs):
        options = {"repo": self.repo.path, "confirm_write": True, "pins": self.pins, **kwargs}
        return bundle.build_bundle(self.snapshots, self.repo.sha, self.out, **options)

    def test_bundle_matches_r3_contract(self):
        report = self.build()
        final = Path(report["bundle"])
        self.assertEqual(final.name, f"rag-final-v140-r3-{self.repo.sha[:12]}.zip")
        self.assertEqual(set(report["members"]), {"b2-r3-v1.12.4.snapshot", "b3-r3-v1.12.4.snapshot",
                                                  f"source-{self.repo.sha[:12]}.zip", "compose.yaml",
                                                  "restore_final_v140_r3.py", "package.json", "SHA256SUMS"})
        package = report["package"]
        self.assertEqual((package["schema_version"], package["bundle"]), (2, "rag-final-v140-r3"))
        self.assertEqual(set(package["collections"]), {"b2", "b3"})
        manifest = json.loads((self.repo.path / bundle.MANIFEST_SOURCE).read_text(encoding="utf-8"))
        self.assertEqual(set(package["neo4j"]["release_files"]),
                         {n for n in manifest["files"] if n.startswith("neo4j/")})
        self.assertEqual(Path(str(final) + ".sha256").read_text(encoding="utf-8").split()[0], restore.file_sha(final))
        bundle.verify_bundle_zip(final, pins=self.pins)
        with zipfile.ZipFile(final) as archive:
            source = zipfile.ZipFile(archive.open(package["source_archive"]))
            self.assertNotIn(".env", source.namelist())
            self.assertEqual(archive.read("compose.yaml"), (self.repo.path / bundle.VERIFY_COMPOSE).read_bytes())
        self.assertEqual(list(self.out.glob(".staging-*")), [])

    def test_existing_bundle_is_never_overwritten(self):
        report = self.build()
        before = restore.file_sha(report["bundle"])
        with self.assertRaisesRegex(b2b.BundleError, "이미 있습니다"):
            self.build()
        self.assertEqual(restore.file_sha(report["bundle"]), before)

    def test_refusals_before_any_output(self):
        cases = {
            "pending_pins": dict(pins=None),
            "no_confirm": dict(confirm_write=False),
        }
        for name, kwargs in cases.items():
            with self.subTest(name=name), self.assertRaises((profiles.ProfileError, b2b.BundleError)):
                bundle.build_bundle(self.snapshots, self.repo.sha, self.out,
                                    **{"repo": self.repo.path, "confirm_write": True, "pins": self.pins, **kwargs})
        with self.assertRaisesRegex(b2b.BundleError, "B2·B3"):
            bundle.build_bundle({"b2": self.snapshots["b2"]}, self.repo.sha, self.out, repo=self.repo.path,
                                confirm_write=True, pins=self.pins)
        wrong = json.loads(json.dumps(self.pins))
        wrong["manifest_lf_sha256"] = "0" * 64
        with self.assertRaisesRegex(b2b.BundleError, "manifest"):
            self.build(pins=wrong)
        wrong = json.loads(json.dumps(self.pins))
        wrong["collections"]["b3"]["plan_sha256"] = "0" * 64
        with self.assertRaisesRegex(b2b.BundleError, "b3 snapshot 기록"):
            self.build(pins=wrong)
        (self.repo.path / profiles.PROFILE_SCRIPT).write_bytes(b"# sample_only uncommitted pins\n")
        with self.assertRaisesRegex(b2b.BundleError, "기준값 모듈"):
            self.build()
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))

    def test_failure_midway_leaves_no_partial_or_staging(self):
        with patch.object(b2b, "git_archive", side_effect=b2b.BundleError("sample_only archive failure")):
            with self.assertRaisesRegex(b2b.BundleError, "archive failure"):
                self.build()
        self.assertEqual([p.name for p in self.out.iterdir()], [])

    def test_verify_detects_tampered_or_extra_members(self):
        final = Path(self.build()["bundle"])
        tampered = Path(self.tmp.name) / "tampered.zip"
        with zipfile.ZipFile(final) as src, zipfile.ZipFile(tampered, "w") as dst:
            for name in src.namelist():
                data = src.read(name)
                dst.writestr(name, b"sample_only changed" if name == "b3-r3-v1.12.4.snapshot" else data)
        with self.assertRaisesRegex(b2b.BundleError, "복원 계약"):
            bundle.verify_bundle_zip(tampered, pins=self.pins)
        extra = Path(self.tmp.name) / "extra.zip"
        shutil.copyfile(final, extra)
        with zipfile.ZipFile(extra, "a") as archive:
            archive.writestr(".env", "SAMPLE_ONLY=1")
        with self.assertRaises(b2b.BundleError):
            bundle.verify_bundle_zip(extra, pins=self.pins)


class CreateSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.s = Sample()
        self.plans = {k: self.s.plan(k) for k in profiles.COLLECTION_KEYS}
        patcher = patch.dict(profiles.R3_SOURCES, self.s.sources)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = self.s.client()
        for key in profiles.COLLECTION_KEYS:
            self.complete(key)
        digests = {k: restore.vector_digest(self.client.collections[self.s.collection(k)])
                   for k in profiles.COLLECTION_KEYS}
        self.pins = self.s.pins_for(self.plans, digests)
        self.docker = good_build_docker()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def complete(self, key):
        """payload 최신화·삭제·재임베딩이 끝난 상태를 직접 만든다 (sample_only 벡터)."""
        plan, coll = self.plans[key], self.client.collections[self.s.collection(key)]
        chunks = {c["point_id"]: c for c in self.s.chunks[key]}
        for point_id in list(plan["delete"]):
            coll.pop(point_id)
        for point_id, item in plan["reuse"].items():
            coll[point_id]["payload"] = planner.reuse_payload(chunks[point_id], self.s.metadata,
                                                              self.s.release["content_hashes"], item["run"])
        for index, point_id in enumerate([*plan["delete"], *plan["new"]]):
            run = {**planner.expected_run(profiles.collection_spec(key)), "index_run_id": "RUN_SAMPLE_ONLY_NEW"}
            coll[point_id] = {"vector": sample_vector(300 + index),
                              "payload": planner.reuse_payload(chunks[point_id], self.s.metadata,
                                                               self.s.release["content_hashes"], run)}

    def create(self, key, **kwargs):
        plan = self.plans[key]
        docker = kwargs.get("docker") or self.docker
        fp = update.server_fingerprint(self.client, update.inspect_state(self.client, plan, self.s.release),
                                       update.require_build_ownership(self.docker))
        data = f"sample_only downloaded {key}".encode()
        options = {"confirm_write": True, "expected_fingerprint": fp, "pins": self.pins, "environ": ENV,
                   "docker": docker,
                   "http_get": lambda url, headers, timeout, stream: SimpleNamespace(
                       status_code=200, iter_content=lambda chunk_size: [data], close=lambda: None),
                   "verify_slices": lambda: {"passed": True}, **kwargs}
        return bundle.create_snapshot(self.client, URL, plan, self.s.release, Path(self.tmp.name), **options)

    def test_snapshot_and_record_for_each_collection(self):
        for key in profiles.COLLECTION_KEYS:
            with self.subTest(key=key):
                record = self.create(key)
                snapshot, record_path = bundle.snapshot_paths(Path(self.tmp.name), key)
                self.assertEqual(record["snapshot_sha256"], restore.file_sha(snapshot))
                self.assertEqual((record["points"], record["vector_digest"]),
                                 (6, self.pins["collections"][key]["vector_digest"]))
                self.assertEqual(json.loads(record_path.read_text(encoding="utf-8"))["plan_sha256"],
                                 self.pins["collections"][key]["plan_sha256"])
                with self.assertRaisesRegex(b2b.BundleError, "이미 있습니다"):
                    self.create(key)
        self.assertEqual(self.client.snapshots, [self.s.collection(k) for k in profiles.COLLECTION_KEYS])

    def test_refusals_without_server_snapshot(self):
        with self.assertRaises(profiles.ProfileError):
            self.create("b2", pins=profiles.r3_pins())
        foreign = FakeDocker([build_container("b" * 64, "qdrant", project="lifepolicy"),
                              build_container("c" * 64, "neo4j")])
        with self.assertRaisesRegex(update.UpdateError, "Docker 소유"):
            self.create("b2", docker=foreign)
        restarted = good_build_docker()
        restarted.containers["b" * 64]["State"]["StartedAt"] = "2026-11-22T10:00:00Z"
        with self.assertRaisesRegex(update.UpdateError, "fingerprint"):
            self.create("b2", docker=restarted)                       # fingerprint에 컨테이너 식별 정보 포함
        preview = dict(self.plans["b2"], basis=profiles.PLAN_BASIS_PREVIEW)
        with self.assertRaisesRegex(planner.PlanError, "server_snapshot"):
            bundle.create_snapshot(self.client, URL, preview, self.s.release, Path(self.tmp.name), confirm_write=True,
                                   expected_fingerprint="0" * 64, pins=self.pins, environ=ENV, docker=self.docker)
        with self.assertRaisesRegex(update.UpdateError, "fingerprint"):
            self.create("b2", expected_fingerprint="0" * 64)
        with self.assertRaisesRegex(b2b.BundleError, "verify_full_index"):
            self.create("b3", verify_slices=lambda: {"passed": False})
        self.client.collections[self.s.collection("b2")].pop(next(iter(self.plans["b2"]["new"])))
        with self.assertRaisesRegex(b2b.BundleError, "상태 이상"):
            self.create("b2")
        self.assertEqual(self.client.snapshots, [])


class PlanModeTests(unittest.TestCase):
    def test_plan_is_read_only_and_lists_missing_pins(self):
        with tempfile.TemporaryDirectory() as folder:
            output = bundle.plan(None, Path(folder))
            self.assertEqual(list(Path(folder).iterdir()), [])
        self.assertEqual((output["writes"], output["r3_pins_finalized"]), (0, False))
        self.assertIn("manifest_lf_sha256", output["missing_pins"])

    def test_cli_write_modes_require_flags(self):
        for extra in (["--build"], ["--create-snapshot", "--confirm-write"], ["--build", "--confirm-write"]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                bundle.main(extra)


if __name__ == "__main__":
    unittest.main()
