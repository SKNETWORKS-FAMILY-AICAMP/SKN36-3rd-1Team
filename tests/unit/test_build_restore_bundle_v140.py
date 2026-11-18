"""Issue #91 r2 복원 번들 도구(scripts/build_restore_bundle_v140.py) 테스트.

임시 Git 저장소(sample_only)와 가짜 snapshot 바이트로만 검증한다. 실제 Qdrant·Docker·snapshot 생성은 없다.
snapshot 생성 경로는 test_update_b2_from_snapshot의 Sample·FakeQdrant(벡터 비트 보존 테스트 더블)를 재사용한다.
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

from test_update_b2_from_snapshot import FakeQdrant, Sample, apply, sample_vector  # noqa: E402

from scripts import build_restore_bundle_v140 as bundle  # noqa: E402
from scripts import restore_final_v140 as restore  # noqa: E402
from scripts import update_b2_from_snapshot as qdrant_r2  # noqa: E402

SAMPLE_DIGEST = "a" * 64
SNAPSHOT_BYTES = b"sample_only snapshot bytes for issue91 r2"


def git(repo, *args):
    return subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args], cwd=repo,
                          capture_output=True, check=True).stdout.decode().strip()


def commit_all(repo, message, *paths):
    git(repo, "add", *(paths or ("-A",)))
    git(repo, "-c", "user.name=sample_only", "-c", "user.email=sample_only@example.invalid",
        "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


class TempRepo:
    """필수 파일(실제 복원 도구·compose·manifest·requirements)만 담은 sample_only 저장소."""

    def __init__(self, root: Path):
        self.path = root / "repo"
        for rel in bundle.REQUIRED_COMMIT_PATHS:
            target = self.path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundle.ROOT / rel, target)
        (self.path / "database").mkdir(parents=True, exist_ok=True)
        (self.path / "database" / "sample_only.py").write_bytes(b"VALUE = 1\n")      # 바이트 그대로 (LF)
        git(self.path, "init", "-q")
        self.sha = commit_all(self.path, "sample_only base")
        # 커밋 이후 작업 트리 변경 (번들 소스에 들어가면 안 됨)
        (self.path / "database" / "sample_only.py").write_bytes(b"VALUE = 2  # uncommitted\n")
        (self.path / ".env").write_text("SAMPLE_ONLY_SETTING=sample_only\n", encoding="utf-8")
        (self.path / "sample_only_secret.clixml").write_text("sample_only", encoding="utf-8")


def write_snapshot(folder: Path, points=restore.SNAPSHOT_POINTS, digest=SAMPLE_DIGEST, data=SNAPSHOT_BYTES):
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = folder / "input.snapshot"
    snapshot.write_bytes(data)
    record = {"collection": restore.COLLECTION, "points": points, "dimensions": restore.SNAPSHOT_DIMENSIONS,
              "vector_digest": digest, "snapshot_sha256": hashlib.sha256(data).hexdigest(),
              "snapshot_size_bytes": len(data), "qdrant_version": "1.12.4"}
    record_path = folder / "snapshot-record.json"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    return snapshot, record_path


class SourceCommitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = TempRepo(Path(self.tmp.name))

    def test_only_full_existing_sha_is_accepted(self):
        self.assertEqual(bundle.resolve_commit(self.repo.sha, self.repo.path), self.repo.sha)
        for value in ("HEAD", self.repo.sha[:12], "master", "main", "0" * 40, None, ""):
            with self.subTest(value=value), self.assertRaises(bundle.BundleError):
                bundle.resolve_commit(value, self.repo.path)

    def test_commit_checks_manifest_restore_tool_and_forbidden_files(self):
        info = bundle.check_source_commit(self.repo.sha, self.repo.path)
        self.assertEqual(info["manifest_lf_sha256"], bundle.EXPECTED_MANIFEST)
        with self.assertRaisesRegex(bundle.BundleError, "manifest"):
            bundle.check_source_commit(self.repo.sha, self.repo.path, expected_manifest="0" * 64)
        tool = self.repo.path / bundle.RESTORE_SOURCE
        tool.write_text(tool.read_text(encoding="utf-8").replace("SNAPSHOT_POINTS = 1139", "SNAPSHOT_POINTS = 1138"),
                        encoding="utf-8")
        old_tool = commit_all(self.repo.path, "sample_only old restore tool", bundle.RESTORE_SOURCE)
        with self.assertRaisesRegex(bundle.BundleError, "SNAPSHOT_POINTS"):
            bundle.check_source_commit(old_tool, self.repo.path)
        with_env = commit_all(self.repo.path, "sample_only tracked env")       # .env·.clixml이 추적됨
        with self.assertRaisesRegex(bundle.BundleError, "인증 정보"):
            bundle.check_source_commit(with_env, self.repo.path)

    def test_git_archive_contains_only_committed_tree(self):
        target = Path(self.tmp.name) / "source.zip"
        git(self.repo.path, "config", "core.autocrlf", "true")       # Windows 기본 설정이어도 커밋 바이트 그대로
        names = bundle.git_archive(self.repo.sha, target, self.repo.path)
        self.assertNotIn(".env", names)
        self.assertNotIn("sample_only_secret.clixml", names)
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(archive.read("database/sample_only.py").decode(), "VALUE = 1\n")
        note = bundle.working_tree_note(self.repo.path)
        self.assertEqual((note["tracked_changes"], note["untracked"]), (1, 2))

    def test_real_repository_head_has_no_tracked_credentials(self):
        head = bundle.run_git(["rev-parse", "HEAD"]).decode().strip()
        names = bundle.run_git(["ls-tree", "-r", "--name-only", head]).decode("utf-8").splitlines()
        self.assertEqual(bundle.forbidden_names(names), [])       # 이름만 검사, 값은 읽지 않음


class BuildBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.repo = TempRepo(root)
        self.snapshot, self.record = write_snapshot(root / "inputs")
        self.out = root / "out"

    def build(self, **kwargs):
        options = dict(repo=self.repo.path, confirm_write=True, vector_digest=SAMPLE_DIGEST)
        options.update(kwargs)
        return bundle.build_bundle(self.snapshot, self.record, self.repo.sha, self.out, **options)

    def assert_no_outputs(self):
        leftovers = list(self.out.iterdir()) if self.out.exists() else []
        self.assertEqual(leftovers, [])
        self.assertEqual(restore.file_sha(self.snapshot), hashlib.sha256(SNAPSHOT_BYTES).hexdigest())

    def test_bundle_matches_restore_contract(self):
        report = self.build()
        final = self.out / bundle.bundle_name(self.repo.sha)
        self.assertEqual(report["status"], "BUILT")
        self.assertIn(self.repo.sha[:12], final.name)
        self.assertIn("r2", final.name)
        self.assertEqual(report["bundle_sha256"], restore.file_sha(final))
        self.assertEqual((self.out / (final.name + ".sha256")).read_text(encoding="utf-8"),
                         f"{report['bundle_sha256']} *{final.name}\n")
        self.assertEqual(report["members"], sorted(["SHA256SUMS", "b2-r2-v1.12.4.snapshot", "compose.yaml",
                                                    "package.json", "restore_final_v140.py",
                                                    f"source-{self.repo.sha[:12]}.zip"]))
        package = report["package"]
        expected_types = {"collection": str, "dimensions": int, "points": int, "snapshot": str,
                          "snapshot_sha256": str, "manifest_lf_sha256": str, "vector_digest": str}
        for key, kind in expected_types.items():
            self.assertIsInstance(package[key], kind, key)
        self.assertEqual((package["collection"], package["dimensions"], package["points"]),
                         (restore.COLLECTION, 1024, 1139))
        self.assertEqual((package["vector_digest"], package["source_commit"]), (SAMPLE_DIGEST, self.repo.sha))
        self.assertEqual(package["manifest_lf_sha256"], bundle.EXPECTED_MANIFEST)
        with zipfile.ZipFile(final) as archive, tempfile.TemporaryDirectory() as folder:
            archive.extractall(folder)
            checked, snapshot = restore.check_bundle(folder)         # 기존 복원 도구의 계약 그대로
            self.assertEqual(snapshot.read_bytes(), SNAPSHOT_BYTES)
            sums = Path(folder, "SHA256SUMS").read_bytes()
            self.assertNotIn(b"\r\n", sums)
            self.assertEqual(Path(folder, "restore_final_v140.py").read_bytes(),
                             (self.repo.path / bundle.RESTORE_SOURCE).read_bytes())
            with zipfile.ZipFile(Path(folder, checked["source_archive"])) as source:
                self.assertEqual(bundle.forbidden_names(source.namelist()), [])
                self.assertEqual(source.read("database/sample_only.py").decode(), "VALUE = 1\n")
        self.assertEqual(sorted(p.name for p in self.out.iterdir()),
                         sorted([final.name, final.name + ".sha256", final.name + ".report.json"]))
        self.assertEqual(restore.file_sha(self.snapshot), hashlib.sha256(SNAPSHOT_BYTES).hexdigest())

    def test_existing_bundle_is_never_overwritten(self):
        report = self.build()
        before = restore.file_sha(Path(report["bundle"]))
        with self.assertRaisesRegex(bundle.BundleError, "덮어쓰지"):
            self.build()
        self.assertEqual(restore.file_sha(Path(report["bundle"])), before)

    def test_bad_inputs_stop_without_outputs(self):
        cases = {
            "confirm_write": dict(confirm_write=False),
            "manifest": dict(expected_manifest="0" * 64),
            "vector_digest": dict(vector_digest="b" * 64),
            "Point": dict(expected_points=1138),
        }
        for message, options in cases.items():
            with self.subTest(message), self.assertRaisesRegex(bundle.BundleError, message):
                self.build(**options)
            self.assert_no_outputs()

    def test_snapshot_missing_or_changed_is_rejected(self):
        self.snapshot.write_bytes(SNAPSHOT_BYTES + b"x")
        with self.assertRaisesRegex(bundle.BundleError, "SHA-256"):
            self.build()
        self.snapshot.unlink()
        with self.assertRaisesRegex(bundle.BundleError, "없습니다"):
            self.build()
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))

    def test_failure_midway_leaves_no_partial_or_staging(self):
        with patch.object(bundle, "git_archive", side_effect=bundle.BundleError("sample_only archive failure")):
            with self.assertRaisesRegex(bundle.BundleError, "archive failure"):
                self.build()
        self.assert_no_outputs()
        with patch.object(bundle, "verify_bundle_zip", side_effect=bundle.BundleError("sample_only verify failure")):
            with self.assertRaisesRegex(bundle.BundleError, "verify failure"):
                self.build()
        self.assert_no_outputs()

    def rewrite(self, source: Path, target: Path, change=None, drop=None, add=None):
        with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "x") as new:
            for name in old.namelist():
                if name == drop:
                    continue
                data = old.read(name)
                new.writestr(name, change(data) if change and name == bundle.SNAPSHOT_MEMBER else data)
            if add:
                new.writestr(add, "sample_only")
        return target

    def test_verify_detects_tampered_missing_or_forbidden_members(self):
        final = Path(self.build()["bundle"])
        expected = {"SHA256SUMS", bundle.SNAPSHOT_MEMBER, "compose.yaml", "package.json", "restore_final_v140.py",
                    f"source-{self.repo.sha[:12]}.zip"}
        bundle.verify_bundle_zip(final, expected)
        tampered = self.rewrite(final, self.out / "tampered.zip", change=lambda data: data + b"x")
        with self.assertRaisesRegex(bundle.BundleError, "복원 계약"):
            bundle.verify_bundle_zip(tampered)
        missing = self.rewrite(final, self.out / "missing.zip", drop="compose.yaml")
        with self.assertRaisesRegex(bundle.BundleError, "구성 파일"):
            bundle.verify_bundle_zip(missing, expected)
        with self.assertRaisesRegex(bundle.BundleError, "SHA256SUMS"):
            bundle.verify_bundle_zip(missing)
        extra = self.rewrite(final, self.out / "extra.zip", add=".env")
        with self.assertRaisesRegex(bundle.BundleError, "인증 정보"):
            bundle.verify_bundle_zip(extra)


class SnapshotQdrant(FakeQdrant):
    def __init__(self, points, data=SNAPSHOT_BYTES, checksum=None, **kwargs):
        super().__init__(points, **kwargs)
        self.data, self.snapshots = data, []
        self.checksum = checksum or hashlib.sha256(data).hexdigest()

    def create_snapshot(self, collection_name, wait=True):
        self.snapshots.append(collection_name)
        return SimpleNamespace(name=f"{collection_name}-sample_only.snapshot", size=len(self.data),
                               checksum=self.checksum, creation_time=None)


class CreateSnapshotTests(unittest.TestCase):
    URL = "http://127.0.0.1:56446"
    ENV = {qdrant_r2.API_KEY_ENV: "sample_only_key"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "out"
        self.s = Sample()
        self.client = self.complete_client()

    def complete_client(self, **kwargs):
        client = SnapshotQdrant(self.s.records, **kwargs)
        apply(client, self.s.ref)
        for index, key in enumerate(("D1", "D2", "E")):
            client.collections[qdrant_r2.COLLECTION][self.s.ids[key]] = {"vector": sample_vector(40 + index),
                                                                         "payload": {}}
        return client

    def state(self, client=None):
        client = client or self.client
        state = qdrant_r2.inspect_state(client, self.s.ref)
        return state, qdrant_r2.server_fingerprint(client, state)

    def http(self, data=SNAPSHOT_BYTES, status=200):
        self.requests = []

        def get(url, headers, timeout, stream):
            self.requests.append({"url": url, "headers": headers, "stream": stream})
            return SimpleNamespace(status_code=status, iter_content=lambda chunk_size: [data], close=lambda: None)
        return get

    def create(self, **kwargs):
        state, fingerprint = self.state()
        options = dict(confirm_write=True, expected_fingerprint=fingerprint, environ=self.ENV, http_get=self.http(),
                       verify_slices=lambda: {"passed": True}, expected_points=6,
                       vector_digest=state["full_vector_digest"])
        options.update(kwargs)
        return bundle.create_snapshot(self.client, self.URL, self.s.ref, self.out, **options)

    def test_snapshot_is_created_downloaded_and_collection_unchanged(self):
        before = (self.state()[1], {k: dict(v) for k, v in self.client.collections[qdrant_r2.COLLECTION].items()})
        writes_before = list(self.client.writes)          # apply()로 만든 준비 단계 쓰기
        record = self.create()
        target = self.out / bundle.SNAPSHOT_MEMBER
        self.assertEqual(target.read_bytes(), SNAPSHOT_BYTES)
        self.assertEqual((record["snapshot_sha256"], record["snapshot_size_bytes"]),
                         (hashlib.sha256(SNAPSHOT_BYTES).hexdigest(), len(SNAPSHOT_BYTES)))
        self.assertEqual(self.client.snapshots, [qdrant_r2.COLLECTION])
        self.assertEqual(self.requests[0]["url"],
                         f"{self.URL}/collections/{qdrant_r2.COLLECTION}/snapshots/{qdrant_r2.COLLECTION}-sample_only.snapshot")
        self.assertEqual(self.requests[0]["headers"], {"api-key": "sample_only_key"})
        self.assertEqual(self.state()[1], before[0])
        self.assertEqual(self.client.collections[qdrant_r2.COLLECTION], before[1])
        self.assertEqual(self.client.writes, writes_before)      # snapshot 생성은 Point·payload를 쓰지 않음
        saved = json.loads((self.out / "snapshot-record.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["snapshot_sha256"], record["snapshot_sha256"])
        self.assertNotIn("sample_only_key", json.dumps(saved))

    def test_guards_stop_before_creating_snapshot(self):
        cases = [("confirm_write", bundle.BundleError, dict(confirm_write=False)),
                 ("fingerprint", qdrant_r2.UpdateError, dict(expected_fingerprint="0" * 64)),
                 ("verify_full_index", bundle.BundleError, dict(verify_slices=lambda: {"passed": False})),
                 ("QDRANT_API_KEY", qdrant_r2.UpdateError, dict(environ={})),
                 ("상태 이상", bundle.BundleError, dict(expected_points=1139))]
        for message, error, options in cases:
            with self.subTest(message), self.assertRaisesRegex(error, message):
                self.create(**options)
        with self.assertRaises(ValueError):
            bundle.create_snapshot(self.client, "http://127.0.0.1:6333", self.s.ref, self.out, confirm_write=True)
        pending = SnapshotQdrant(self.s.records)
        _state, fingerprint = self.state(pending)
        with self.assertRaisesRegex(bundle.BundleError, "상태 이상"):
            bundle.create_snapshot(pending, self.URL, self.s.ref, self.out, confirm_write=True,
                                   expected_fingerprint=fingerprint, environ=self.ENV, http_get=self.http(),
                                   verify_slices=lambda: {"passed": True}, expected_points=6)
        self.assertEqual((self.client.snapshots, pending.snapshots), ([], []))
        self.assertFalse(self.out.exists())

    def test_checksum_mismatch_or_http_error_leaves_no_files(self):
        with self.assertRaisesRegex(bundle.BundleError, "SHA-256"):
            self.create(http_get=self.http(data=SNAPSHOT_BYTES + b"x"))
        with self.assertRaisesRegex(bundle.BundleError, "HTTP 500"):
            self.create(http_get=self.http(status=500))
        self.assertEqual(list(self.out.iterdir()), [])

    def test_existing_snapshot_file_is_not_overwritten(self):
        self.out.mkdir(parents=True)
        (self.out / bundle.SNAPSHOT_MEMBER).write_bytes(b"sample_only existing")
        with self.assertRaisesRegex(bundle.BundleError, "덮어쓰지"):
            self.create()
        self.assertEqual((self.out / bundle.SNAPSHOT_MEMBER).read_bytes(), b"sample_only existing")
        self.assertEqual(self.client.snapshots, [])


class CliExitCodeTests(unittest.TestCase):
    """main()의 종료 코드: 성공 0, 실패 non-zero. 작업 결과 상태(BUILT)는 result_status로 따로 남는다."""

    SHA = "f" * 40

    def run_main(self, argv):
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = bundle.main(argv)
        return code, json.loads(buffer.getvalue())

    def build_args(self):
        return ["--build", "--confirm-write", "--source-commit", self.SHA, "--snapshot", "s", "--snapshot-record", "r"]

    def test_successful_build_exits_zero_and_keeps_built_status(self):
        report = {"status": "BUILT", "bundle": "sample_only.zip", "bundle_sha256": "a" * 64, "members": ["x"]}
        with patch.object(bundle, "build_bundle", return_value=report):
            code, output = self.run_main(self.build_args())
        self.assertEqual(code, 0)
        self.assertEqual((output["status"], output["result_status"]), ("PASS", "BUILT"))
        self.assertEqual((output["bundle"], output["bundle_sha256"], output["members"]),
                         ("sample_only.zip", "a" * 64, ["x"]))

    def test_failed_build_exits_non_zero(self):
        with patch.object(bundle, "build_bundle", side_effect=bundle.BundleError("sample_only failure")):
            code, output = self.run_main(self.build_args())
        self.assertEqual((code, output["status"]), (1, "FAILED"))
        self.assertNotIn("result_status", output)

    def test_create_snapshot_success_and_failure_exit_codes(self):
        args = ["--create-snapshot", "--confirm-write", "--qdrant-url", "http://127.0.0.1:56446",
                "--expect-fingerprint", "0" * 64]
        client = SimpleNamespace(close=lambda: None)
        with patch.object(qdrant_r2, "build_reference", return_value={}), \
                patch.object(qdrant_r2, "make_isolated_client", return_value=client), \
                patch.object(bundle, "create_snapshot", return_value={"snapshot_sha256": "b" * 64, "points": 1139}):
            code, output = self.run_main(args)
        self.assertEqual((code, output["status"], output["points"]), (0, "PASS", 1139))
        self.assertNotIn("result_status", output)
        with patch.object(qdrant_r2, "build_reference", return_value={}), \
                patch.object(qdrant_r2, "make_isolated_client", return_value=client), \
                patch.object(bundle, "create_snapshot", side_effect=bundle.BundleError("sample_only")):
            code, output = self.run_main(args)
        self.assertEqual((code, output["status"]), (1, "FAILED"))

    def test_plan_failure_exits_non_zero(self):
        code, output = self.run_main(["--source-commit", "HEAD"])
        self.assertEqual((code, output["status"]), (1, "FAILED"))

    def test_cli_success_never_hides_a_failure(self):
        self.assertEqual(bundle.cli_success({"mode": "plan"}), {"status": "PASS", "mode": "plan"})
        self.assertEqual(bundle.cli_success({"status": "BUILT", "a": 1}),
                         {"status": "PASS", "result_status": "BUILT", "a": 1})


if __name__ == "__main__":
    unittest.main()
