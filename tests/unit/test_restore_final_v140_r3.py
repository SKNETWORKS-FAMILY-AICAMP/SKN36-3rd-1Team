"""Issue #105 r3 번들 복원(scripts/restore_final_v140_r3.py) 테스트.

번들 폴더는 실제 구성(snapshot 2·source ZIP·compose·복원 도구·package.json·SHA256SUMS)을 sample_only 바이트로 만든다.
source ZIP에는 --repo(이 저장소)와 대조하는 파일만 넣는다. 릴리스·그래프·교차 검증 함수는 mock으로 바꾼다.
Qdrant는 R3Qdrant(벡터 비트 보존), Neo4j는 MagicMock 실행기다. 실제 DB·Docker 접속 없음.
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from argparse import Namespace
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r3_fixtures import (MANIFEST, RUN_OLD, R3Qdrant, Sample, final_sample_pins, pending_pins,  # noqa: E402
                         sample_vector, stage1_pins)

from database import full_index as fi, handoff_data as hd, hybrid_search as hs  # noqa: E402
from database.neo4j import load_graph as lg  # noqa: E402
from database.qdrant import store  # noqa: E402
from scripts import restore_final_v140 as r2  # noqa: E402
from scripts import restore_final_v140_r3 as r3  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = {"QDRANT_URL": "http://127.0.0.1:56656", "QDRANT_API_KEY": "sample_only_key",
            "NEO4J_URI": "bolt://127.0.0.1:56687", "NEO4J_USER": "neo4j", "NEO4J_PASSWORD": "sample_only_pw"}
SOURCE_COMMIT = "1" * 40
ORIGINAL_RESTORE = r3.restore          # CLI 테스트가 r3.restore를 바꿔 끼워도 시나리오는 원래 함수를 부른다


def neo4j_release_files(root=ROOT):
    manifest = json.loads((root / profiles.RELEASE_DIR / "manifest.json").read_text(encoding="utf-8"))
    return {n: item["sha256"] for n, item in sorted(manifest["files"].items()) if n.startswith("neo4j/")}


class Bundle:
    """최종 상태 Point(최신 payload + 원본 실행 정보)와 그에 맞는 확정 pins·번들 폴더."""

    def __init__(self, folder: Path):
        self.s = Sample()
        self.folder = folder
        self.records = {}
        for index, key in enumerate(profiles.COLLECTION_KEYS):
            points = {}
            for n, chunk in enumerate(self.s.chunks[key]):
                payload = store.build_point_payload(chunk, policy_meta=self.s.metadata[chunk["policy_id"]],
                                                    content_hash=self.s.release["content_hashes"][chunk["document_id"]],
                                                    run_info=RUN_OLD)
                points[chunk["point_id"]] = {"payload": payload, "vector": sample_vector(index * 20 + n)}
            self.records[key] = points
        plans = {k: self.s.plan(k) for k in profiles.COLLECTION_KEYS}
        self.pins = self.s.pins_for(plans, {k: r2.vector_digest(self.records[k]) for k in profiles.COLLECTION_KEYS})
        self.pins["release_counts"] = dict(self.s.release["counts"])
        self.archive = f"source-{SOURCE_COMMIT[:12]}.zip"
        self.write_source()
        (self.folder / r3.COMPOSE_MEMBER).write_bytes(b"# sample_only compose\n")
        (self.folder / r3.RESTORE_MEMBER).write_bytes((ROOT / profiles.RESTORE_SCRIPT).read_bytes())
        self.write()

    def write_source(self, overrides=None):
        """--repo와 대조하는 파일만 담은 source ZIP (git archive처럼 LF)."""
        with zipfile.ZipFile(self.folder / self.archive, "w") as archive:
            for path in profiles.SOURCE_IDENTITY_FILES:
                data = (overrides or {}).get(path, profiles.eol_normalized((ROOT / path).read_bytes()))
                if data is not None:
                    archive.writestr(path, data)

    def package(self):
        collections = {}
        for key in profiles.COLLECTION_KEYS:
            spec = profiles.collection_spec(key)
            data = f"sample_only {key} snapshot".encode()
            (self.folder / spec["snapshot_member"]).write_bytes(data)
            collections[key] = {"collection": spec["collection"], "config": spec["config"], "dimensions": 1024,
                                "points": 6, "vector_digest": self.pins["collections"][key]["vector_digest"],
                                "snapshot": spec["snapshot_member"],
                                "snapshot_sha256": hashlib.sha256(data).hexdigest()}
        return {"schema_version": 2, "bundle": profiles.BUNDLE_NAME, "manifest_lf_sha256": MANIFEST,
                "release_counts": dict(self.pins["release_counts"]), "collections": collections,
                "neo4j": {"release_files": neo4j_release_files()}, "restore_tool": r3.RESTORE_MEMBER,
                "source_commit": SOURCE_COMMIT, "source_archive": self.archive}

    def members(self):
        return [profiles.R3_COLLECTIONS[k]["snapshot_member"] for k in profiles.COLLECTION_KEYS] + [
            self.archive, r3.COMPOSE_MEMBER, r3.RESTORE_MEMBER, "package.json"]

    def write(self, package=None, members=None):
        package = package or self.package()
        (self.folder / "package.json").write_text(json.dumps(package), encoding="utf-8")
        self.write_sums(members)
        return package

    def write_sums(self, members=None):
        (self.folder / "SHA256SUMS").write_text(
            "".join(f"{r2.file_sha(self.folder / m)}  {m}\n" for m in (members or self.members())), encoding="utf-8")


class CheckBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.b = Bundle(Path(self.tmp.name))

    def test_valid_bundle_returns_both_snapshots_and_matches_repo(self):
        package, snapshots = r3.check_bundle(self.b.folder, self.b.pins)
        self.assertEqual(set(snapshots), {"b2", "b3"})
        self.assertEqual(package["schema_version"], 2)
        result = r3.check_repo_matches_source(ROOT, self.b.folder, package)
        self.assertEqual(result["neo4j_release_files"], len(neo4j_release_files()))

    def test_pending_pins_refuse_every_bundle(self):
        for name, pins in (("pending", pending_pins()), ("stage1", stage1_pins())):
            with self.subTest(name=name):
                with self.assertRaises(profiles.ProfileError):
                    r3.check_bundle(self.b.folder, pins)           # 미확정·1차 상태는 최종 복원 불가

    def test_final_shape_pins_pass_the_gate_and_reject_a_foreign_manifest(self):
        """2차 확정 모양(현재 릴리스 manifest)은 기준값 게이트를 통과하고, sample_only 번들의 manifest가 달라 거부된다."""
        with self.assertRaisesRegex(ValueError, "manifest/counts differ"):
            r3.check_bundle(self.b.folder, final_sample_pins())

    def test_real_c0_pins_refuse_every_bundle(self):
        with self.assertRaisesRegex(profiles.ProfileError, "확정되지 않았습니다"):
            r3.check_bundle(self.b.folder)

    def test_r2_package_and_wrong_contents_are_refused(self):
        cases = {
            "r2_schema": lambda p: {"collection": "exp_b2_qwen3emb06b_v140_v1", "points": 1139, "snapshot": "x"},
            "missing_b3": lambda p: {**p, "collections": {"b2": p["collections"]["b2"]}},
            "count": lambda p: {**p, "collections": {**p["collections"],
                                                     "b3": {**p["collections"]["b3"], "points": 7}}},
            "digest": lambda p: {**p, "collections": {**p["collections"],
                                                      "b2": {**p["collections"]["b2"], "vector_digest": "0" * 64}}},
            "manifest": lambda p: {**p, "manifest_lf_sha256": "0" * 64},
            "swapped_name": lambda p: {**p, "collections": {**p["collections"],
                                                            "b2": {**p["collections"]["b2"],
                                                                   "collection": "exp_b3_qwen3emb06b_v140_v1"}}},
            "short_commit": lambda p: {**p, "source_commit": SOURCE_COMMIT[:12]},
            "archive_not_commit": lambda p: {**p, "source_commit": "2" * 40},
            "restore_tool": lambda p: {**p, "restore_tool": "restore_final_v140.py"},
        }
        for name, change in cases.items():
            with self.subTest(name=name):
                self.b.write(change(self.b.package()))
                with self.assertRaises(ValueError):
                    r3.check_bundle(self.b.folder, self.b.pins)

    def test_sha256sums_must_list_exactly_the_bundle_members(self):
        """source ZIP·복원 도구·compose를 체크섬 목록에서 빼거나 다른 파일을 끼우면 거부 (fail-closed)."""
        for missing in (self.b.archive, r3.RESTORE_MEMBER, r3.COMPOSE_MEMBER):
            with self.subTest(missing=missing):
                self.b.write_sums([m for m in self.b.members() if m != missing])
                with self.assertRaisesRegex(ValueError, "exactly"):
                    r3.check_bundle(self.b.folder, self.b.pins)
        (self.b.folder / "extra.txt").write_text("sample_only", encoding="utf-8")
        self.b.write_sums(self.b.members() + ["extra.txt"])
        with self.assertRaisesRegex(ValueError, "exactly"):
            r3.check_bundle(self.b.folder, self.b.pins)

    def test_tampered_snapshot_source_or_escaping_path_is_refused(self):
        member = profiles.R3_COLLECTIONS["b3"]["snapshot_member"]
        (self.b.folder / member).write_bytes(b"sample_only tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            r3.check_bundle(self.b.folder, self.b.pins)
        self.b.write()
        (self.b.folder / self.b.archive).write_bytes(b"sample_only tampered source")   # 체크섬 목록은 그대로
        with self.assertRaisesRegex(ValueError, "checksum"):
            r3.check_bundle(self.b.folder, self.b.pins)
        self.b.write_source()
        self.b.write()
        with (self.b.folder / "SHA256SUMS").open("a", encoding="utf-8") as stream:
            stream.write(f"{'0' * 64}  ../outside.txt\n")
        with self.assertRaisesRegex(ValueError, "path/checksum"):
            r3.check_bundle(self.b.folder, self.b.pins)


class RepoSourceTests(unittest.TestCase):
    """--repo가 번들 source ZIP과 다른 소스이면 복원하지 않는다 (체크섬까지 다시 맞춘 변조 포함)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.b = Bundle(Path(self.tmp.name))

    def check(self):
        package, _ = r3.check_bundle(self.b.folder, self.b.pins)
        return r3.check_repo_matches_source(ROOT, self.b.folder, package)

    def test_source_with_different_restore_tool_or_pins_module_is_refused(self):
        for path in (profiles.RESTORE_SCRIPT, profiles.PROFILE_SCRIPT, f"{profiles.RELEASE_DIR}/manifest.json"):
            with self.subTest(path=path):
                self.b.write_source({path: b"# sample_only other content\n"})
                self.b.write()                                     # SHA256SUMS도 다시 맞춘 변조
                with self.assertRaisesRegex(ValueError, "differs from the bundle source"):
                    self.check()
        self.b.write_source({profiles.PROFILE_SCRIPT: None})       # 파일이 빠진 source ZIP
        self.b.write()
        with self.assertRaisesRegex(ValueError, "differs from the bundle source"):
            self.check()

    def test_crlf_only_difference_is_accepted(self):
        crlf = profiles.eol_normalized((ROOT / profiles.RESTORE_SCRIPT).read_bytes()).replace(b"\n", b"\r\n")
        self.b.write_source({profiles.RESTORE_SCRIPT: crlf})
        self.b.write()
        self.assertIn("source_identity_files", self.check())

    def test_neo4j_release_files_must_match_repo_manifest(self):
        package = self.b.package()
        files = dict(package["neo4j"]["release_files"])
        files[next(iter(files))] = "0" * 64
        self.b.write({**package, "neo4j": {"release_files": files}})
        with self.assertRaisesRegex(ValueError, "release_files"):
            self.check()
        self.b.write({**package, "neo4j": {}})
        with self.assertRaisesRegex(ValueError, "release_files"):
            self.check()


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.b = Bundle(Path(self.tmp.name))
        self.calls = {"cross": [], "slices": [], "uploads": []}

    def run_restore(self, client, *, apply=False, verify_only=False, totals=None, cross=None, settings=SETTINGS,
                    pins="default", fail_upload=None, load_result=None):
        s = self.b.s
        runner = MagicMock()
        totals = totals or ([[{"nodes": 10, "relationships": 12}]] if verify_only
                            else [[{"nodes": 0, "relationships": 0}], [{"nodes": 10, "relationships": 12}]])
        runner.read_rows.side_effect = totals
        chunks_by_config = {profiles.R3_COLLECTIONS[k]["config"]: s.chunks[k] for k in profiles.COLLECTION_KEYS}

        def cross_db(*, client, runner, collection):
            self.calls["cross"].append(collection)
            return (cross or {}).get(collection, {"status": "PASS", "failures": [], "qdrant_points": 6,
                                                  "qdrant_count": 6, "all_points_read": True, "summary": {}})

        def slices(name, *, client, chunks, documents):
            self.calls["slices"].append(name)
            return {"passed": True}

        def post(url, params, headers, files, timeout):
            name = url.split("/collections/")[1].split("/")[0]
            self.calls["uploads"].append((name, params["checksum"], params["priority"]))
            if name == fail_upload:
                return SimpleNamespace(status_code=500)
            key = next(k for k in profiles.COLLECTION_KEYS if profiles.R3_COLLECTIONS[k]["collection"] == name)
            client.collections[name] = {p: {"payload": dict(r["payload"]), "vector": list(r["vector"])}
                                        for p, r in self.b.records[key].items()}
            return SimpleNamespace(status_code=200)

        validation = SimpleNamespace(ok=True, node_counts={"sample": 10},
                                     relationship_counts={"REQUIRES_CONDITION": 1, "sample": 11})
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {}))
            for target, name, value in (
                    (fi, "manifest_sha256", MANIFEST), (hd, "verify_manifest_checksums", []),
                    (hd, "load_documents", s.documents), (hd, "load_payload_metadata", s.metadata),
                    (hd, "load_payload_chunks", [None] * 6), (lg, "read_graph_input", object()),
                    (lg, "validate_graph_input", validation), (lg, "Neo4jDriverRunner", runner),
                    (lg, "load_graph", load_result or {"ok": True, "verification": {"ok": True}}),
                    (lg, "verify_loaded_graph", {"ok": True})):
                stack.enter_context(patch.object(target, name, return_value=value))
            stack.enter_context(patch.object(fi, "build_config_chunks",
                                             side_effect=lambda name, documents: (chunks_by_config[name], {})))
            stack.enter_context(patch.object(hs, "verify_cross_db", side_effect=cross_db))
            stack.enter_context(patch.object(fi, "verify_full_index", side_effect=slices))
            args = Namespace(repo=ROOT, bundle=self.b.folder, apply=apply, verify_only=verify_only)
            return ORIGINAL_RESTORE(args, settings=settings, pins=self.b.pins if pins == "default" else pins,
                              http_post=post, client_factory=lambda st: client,
                              driver_factory=lambda st: SimpleNamespace(verify_connectivity=lambda: None,
                                                                        close=lambda: None))

    def final_client(self):
        return R3Qdrant({profiles.R3_COLLECTIONS[k]["collection"]: self.b.records[k] for k in profiles.COLLECTION_KEYS})

    def test_preflight_writes_nothing(self):
        client = R3Qdrant()
        result = self.run_restore(client)
        self.assertEqual(result["status"], "PREFLIGHT_PASS_NO_WRITES")
        self.assertEqual((client.writes, self.calls["uploads"]), ([], []))

    def test_apply_uploads_both_snapshots_and_cross_checks_each_collection(self):
        client = R3Qdrant()
        result = self.run_restore(client, apply=True)
        self.assertEqual(result["status"], "PASS")
        names = [profiles.R3_COLLECTIONS[k]["collection"] for k in profiles.COLLECTION_KEYS]
        self.assertEqual([u[0] for u in self.calls["uploads"]], names)
        self.assertTrue(all(u[2] == "snapshot" and len(u[1]) == 64 for u in self.calls["uploads"]))
        self.assertEqual(self.calls["cross"], names)
        self.assertEqual(self.calls["slices"], [profiles.R3_COLLECTIONS[k]["config"] for k in profiles.COLLECTION_KEYS])
        self.assertEqual({k: v["points"] for k, v in result["collections"].items()}, {"b2": 6, "b3": 6})
        self.assertIn("source_check", result)
        self.assertEqual(client.writes, [])                           # 정렬할 payload 없음

    def test_second_snapshot_failure_is_never_reported_as_success(self):
        """B2 업로드 성공 후 B3 업로드 실패: 예외로 멈추고 Neo4j 적재·교차 검증으로 넘어가지 않는다 (부분 자산 보존)."""
        client = R3Qdrant()
        b3 = profiles.R3_COLLECTIONS["b3"]["collection"]
        with self.assertRaisesRegex(ValueError, "Snapshot upload failed"):
            self.run_restore(client, apply=True, fail_upload=b3)
        self.assertEqual(sorted(client.collections), [profiles.R3_COLLECTIONS["b2"]["collection"]])
        self.assertEqual((self.calls["cross"], self.calls["slices"]), ([], []))
        with self.assertRaisesRegex(ValueError, "requires empty"):     # 재실행은 빈 DB가 아니어서 거부
            self.run_restore(client, apply=True)

    def test_cli_reports_partial_failure_as_failed(self):
        client = R3Qdrant()
        b3 = profiles.R3_COLLECTIONS["b3"]["collection"]
        def failing(args):                                # main이 부르는 restore를 부분 실패 시나리오로 바꾼다
            return self.run_restore(client, apply=True, fail_upload=b3)
        with tempfile.TemporaryDirectory() as folder, patch.object(r3, "restore", side_effect=failing):
            report = Path(folder) / "report.json"
            self.assertEqual(r3.main(["--bundle", str(self.b.folder), "--apply", "--report", str(report)]), 1)
            output = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(output["status"], "FAILED")
        self.assertIn("Snapshot upload failed", output["reason"])
        self.assertNotIn(SETTINGS["QDRANT_API_KEY"], json.dumps(output))

    def test_neo4j_load_failure_after_uploads_is_not_success(self):
        with self.assertRaisesRegex(ValueError, "Neo4j restore verification failed"):
            self.run_restore(R3Qdrant(), apply=True, load_result={"ok": False, "verification": {"ok": False}})
        self.assertEqual(self.calls["cross"], [])

    def test_verify_only_is_read_only_and_refuses_alignment_or_digest_drift(self):
        client = self.final_client()
        self.assertEqual(self.run_restore(client, verify_only=True)["status"], "PASS")
        self.assertEqual(client.writes, [])
        drift = self.final_client()
        point = next(iter(drift.collections["exp_b3_qwen3emb06b_v140_v1"].values()))
        point["payload"]["category"] = "sample_only_drift"
        with self.assertRaisesRegex(ValueError, "verify-only refuses"):
            self.run_restore(drift, verify_only=True)
        changed = self.final_client()
        point = next(iter(changed.collections["exp_b2_qwen3emb06b_v140_v1"].values()))
        point["vector"] = sample_vector(500)
        with self.assertRaisesRegex(ValueError, "vector digest"):
            self.run_restore(changed, verify_only=True)
        self.assertEqual((drift.writes, changed.writes), ([], []))

    def test_cross_db_failure_on_either_collection_never_passes(self):
        bad = {"exp_b3_qwen3emb06b_v140_v1": {"status": "MISMATCH", "failures": [{"x": 1}], "qdrant_points": 6,
                                              "qdrant_count": 6}}
        with self.assertRaisesRegex(ValueError, "Cross-DB validation failed"):
            self.run_restore(self.final_client(), verify_only=True, cross=bad)

    def test_repo_source_mismatch_stops_before_any_connection(self):
        self.b.write_source({profiles.PROFILE_SCRIPT: b"# sample_only other pins\n"})
        self.b.write()
        client = R3Qdrant()
        with self.assertRaisesRegex(ValueError, "differs from the bundle source"):
            self.run_restore(client, apply=True)
        self.assertEqual((client.writes, self.calls["uploads"]), ([], []))

    def test_non_empty_target_or_bad_settings_are_refused_before_writes(self):
        busy = R3Qdrant({"exp_other_sample_only": {}})
        with self.assertRaisesRegex(ValueError, "requires empty"):
            self.run_restore(busy, apply=True)
        self.assertEqual(self.calls["uploads"], [])
        with self.assertRaisesRegex(ValueError, "loopback"):
            self.run_restore(R3Qdrant(), settings={**SETTINGS, "QDRANT_URL": "http://10.0.0.5:56656"})
        with self.assertRaisesRegex(ValueError, "missing"):
            self.run_restore(R3Qdrant(), settings={**SETTINGS, "NEO4J_PASSWORD": ""})
        for pins in (pending_pins(), stage1_pins()):
            with self.assertRaises(profiles.ProfileError):
                self.run_restore(R3Qdrant(), pins=pins)
        with self.assertRaisesRegex(profiles.ProfileError, "확정되지 않았습니다"):
            self.run_restore(R3Qdrant(), pins=None)                # 실제 R3_PINS(C0): 기준값 게이트에서 거부
        with self.assertRaisesRegex(ValueError, "manifest/counts differ"):
            self.run_restore(R3Qdrant(), pins=final_sample_pins())  # 2차 확정 모양: 게이트 통과 후 번들 불일치 거부


class CliTests(unittest.TestCase):
    def test_existing_report_is_not_overwritten_and_failures_hide_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "report.json"
            report.write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit):
                r3.main(["--bundle", folder, "--report", str(report)])
            fresh, final = Path(folder) / "fresh.json", Path(folder) / "final.json"
            with patch.dict(os.environ, {"QDRANT_API_KEY": "sample_only_never_printed"}):
                with patch.object(profiles, "R3_PINS", stage1_pins()):
                    self.assertEqual(r3.main(["--bundle", folder, "--report", str(fresh)]), 1)
                with patch.object(profiles, "R3_PINS", final_sample_pins()):
                    self.assertEqual(r3.main(["--bundle", folder, "--report", str(final)]), 1)   # 빈 폴더: 번들 없음
            output = json.loads(fresh.read_text(encoding="utf-8"))
            final_output = json.loads(final.read_text(encoding="utf-8"))
        self.assertEqual(output["error_type"], "ProfileError")
        self.assertNotEqual(final_output["error_type"], "ProfileError")   # 2차 확정 모양은 기준값 게이트 통과
        for result in (output, final_output):
            self.assertNotIn("sample_only_never_printed", json.dumps(result))

    def test_r2_restore_tool_is_unchanged_by_r3(self):
        self.assertEqual((r2.COLLECTION, r2.SNAPSHOT_POINTS, r2.EXPECTED),
                         ("exp_b2_qwen3emb06b_v140_v1", 1139, (246, 262, 600, 1139, 1126, 2668, 211)))


if __name__ == "__main__":
    unittest.main()
