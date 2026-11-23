"""Issue #105 r3 번들 복원(scripts/restore_final_v140_r3.py) 테스트.

번들 폴더는 실제 구성(snapshot 2·source ZIP·compose·복원 도구·package.json·SHA256SUMS)을 sample_only 바이트로 만든다.
source ZIP에는 --repo(이 저장소)와 대조하는 파일만 넣는다. 릴리스·그래프·교차 검증 함수는 mock으로 바꾼다.
Qdrant는 R3Qdrant(벡터 비트 보존), Neo4j는 MagicMock 실행기다. 실제 DB·Docker 접속 없음.
"""
import copy
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
B2 = profiles.R3_COLLECTIONS["b2"]["collection"]
B3 = profiles.R3_COLLECTIONS["b3"]["collection"]
OTHER = "exp_other_sample_only"
TOKEN_B2, TOKEN_B3 = "a" * 32, "b" * 32               # 테스트 고정 토큰 (실제는 secrets.token_hex(16))
TB2, TB3 = r3.temp_collection_name(B2, TOKEN_B2), r3.temp_collection_name(B3, TOKEN_B3)


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
                                "payload_digest": r3.payload_digest(r2, self.records[key]),   # 최종 상태 기준값
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


class RealPinBundleDigestTests(unittest.TestCase):
    """실제 R3_PINS(C2 stage2)로 만든 최소 번들(sample_only 바이트)에서 Point 수는 그대로 두고 vector_digest만 바꾸면
    해당 컬렉션에서 거부된다. B2·B3 각각 확인한다 (계획 대조가 아니라 번들 메타데이터 대조 경로)."""

    def build(self, folder: Path, tamper: str | None = None) -> None:
        pins = profiles.R3_PINS
        commit = "c" * 40
        members = {profiles.R3_COLLECTIONS[k]["snapshot_member"]: f"sample_only {k} snapshot".encode()
                   for k in profiles.COLLECTION_KEYS}
        members.update({f"source-{commit[:12]}.zip": b"sample_only source", r3.COMPOSE_MEMBER: b"sample_only compose",
                        r3.RESTORE_MEMBER: b"sample_only tool"})
        collections = {}
        for key in profiles.COLLECTION_KEYS:
            spec, pinned = profiles.collection_spec(key), pins["collections"][key]
            member = spec["snapshot_member"]
            collections[key] = {"collection": spec["collection"], "config": spec["config"],
                                "dimensions": spec["dimensions"], "points": pinned["points"],
                                "vector_digest": "0" * 64 if key == tamper else pinned["vector_digest"],
                                "payload_digest": "1" * 64,              # sample_only (형식만 검사되는 경로)
                                "snapshot": member, "snapshot_sha256": hashlib.sha256(members[member]).hexdigest()}
        package = {"schema_version": profiles.SCHEMA_VERSION, "bundle": profiles.BUNDLE_NAME,
                   "manifest_lf_sha256": pins["manifest_lf_sha256"], "release_counts": dict(pins["release_counts"]),
                   "collections": collections, "source_commit": commit, "source_archive": f"source-{commit[:12]}.zip",
                   "restore_tool": r3.RESTORE_MEMBER}
        members["package.json"] = json.dumps(package).encode()
        for name, data in members.items():
            (folder / name).write_bytes(data)
        (folder / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n"
                                                   for name, data in sorted(members.items())), encoding="utf-8")

    def test_real_final_pins_accept_matching_metadata_and_refuse_digest_only_change(self):
        self.assertEqual(profiles.pin_stage(), "stage2")
        with tempfile.TemporaryDirectory() as folder:
            self.build(Path(folder))
            package, snapshots = r3.check_bundle(folder)                 # 실제 최종 핀과 같은 메타데이터는 통과
            self.assertEqual(set(snapshots), set(profiles.COLLECTION_KEYS))
        for key in profiles.COLLECTION_KEYS:
            with self.subTest(tampered=key), tempfile.TemporaryDirectory() as folder:
                self.build(Path(folder), tamper=key)
                points = json.loads((Path(folder) / "package.json").read_text(encoding="utf-8"))["collections"][key]["points"]
                self.assertEqual(points, profiles.R3_PINS["collections"][key]["points"])   # Point 수는 같다
                with self.assertRaisesRegex(ValueError, rf"count/digest: {key}"):
                    r3.check_bundle(folder)


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

    def test_real_final_pins_pass_the_gate_and_reject_a_foreign_manifest(self):
        """실제 R3_PINS(C2 final)는 기준값 게이트를 통과하고, sample_only 번들의 manifest가 달라 거부된다."""
        with self.assertRaisesRegex(ValueError, "manifest/counts differ"):
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
                    pins="default", fail_upload=None, load_result=None, before_post=None, after_post=None,
                    tokens=(TOKEN_B2, TOKEN_B3)):
        """before_post(name, client): POST가 서버에서 처리되기 직전(검사 이후) 외부 작업을 흉내 낸다.
        after_post(name, client): 업로드가 끝난 직후, 복원 도구의 다음 검사 전에 외부 작업을 흉내 낸다.
        tokens: 임시 이름 토큰 (테스트만 고정값. 실제 기본값은 secrets.token_hex(16))."""
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

        def slices(name, *, client, chunks, documents, collection=None):
            self.calls["slices"].append((name, collection))
            return {"passed": True}

        def post(url, params, headers, files, timeout):
            name = url.split("/collections/")[1].split("/")[0]
            self.calls["uploads"].append((name, params["checksum"], params["priority"]))
            match = r3.TEMP_NAME.fullmatch(name)
            if match is None:                                      # 정식 이름 업로드는 HTTP까지 오면 안 된다
                raise AssertionError(f"snapshot upload to non-temporary name: {name}")
            if before_post:
                before_post(name, client)
            if name == fail_upload:
                return SimpleNamespace(status_code=500)
            key = next(k for k in profiles.COLLECTION_KEYS if profiles.R3_COLLECTIONS[k]["collection"] == match["canonical"])
            # 실제 Qdrant처럼 같은 이름 컬렉션이 있어도 통째로 덮어쓴다
            client.collections[name] = {p: {"payload": dict(r["payload"]), "vector": list(r["vector"])}
                                        for p, r in self.b.records[key].items()}
            if after_post:
                after_post(name, client)
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
                                                                              close=lambda: None),
                                    token_factory=iter(tokens).__next__)

    def final_client(self):
        """published 상태: 임시 컬렉션 2개 + 정식 alias 2개 (C안 복원 결과)."""
        client = R3Qdrant({TB2: self.b.records["b2"], TB3: self.b.records["b3"]})
        client.aliases = {B2: TB2, B3: TB3}
        return client

    def test_preflight_writes_nothing(self):
        client = R3Qdrant()
        result = self.run_restore(client)
        self.assertEqual(result["status"], "PREFLIGHT_PASS_NO_WRITES")
        self.assertEqual((client.writes, self.calls["uploads"], client.alias_ops), ([], [], []))

    def test_apply_uploads_both_snapshots_and_cross_checks_each_collection(self):
        client = R3Qdrant()
        result = self.run_restore(client, apply=True)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual([u[0] for u in self.calls["uploads"]], [TB2, TB3])          # 임시 이름에만 업로드
        self.assertTrue(all(u[2] == "snapshot" and len(u[1]) == 64 for u in self.calls["uploads"]))
        self.assertEqual(self.calls["cross"], [TB2, TB3])                             # 공개 전 임시 이름으로 검증
        self.assertEqual(self.calls["slices"], [(profiles.R3_COLLECTIONS["b2"]["config"], TB2),
                                                (profiles.R3_COLLECTIONS["b3"]["config"], TB3)])
        self.assertEqual({k: v["points"] for k, v in result["collections"].items()}, {"b2": 6, "b3": 6})
        self.assertEqual(result["physical_collections"], {"b2": TB2, "b3": TB3})
        self.assertEqual((result["aliases"], client.aliases), ({B2: TB2, B3: TB3}, {B2: TB2, B3: TB3}))
        self.assertEqual(client.alias_ops, [("create_alias", B2, TB2), ("create_alias", B3, TB3)])
        self.assertEqual(sorted(client.collections), sorted([TB2, TB3]))             # 정식 이름 컬렉션 없음
        self.assertIs(result["partial_publish"], False)
        self.assertIn("source_check", result)
        self.assertEqual(client.writes, [])                           # 정렬할 payload 없음

    def test_second_snapshot_failure_is_never_reported_as_success(self):
        """B2 업로드 성공 후 B3 업로드 실패: 예외로 멈추고 Neo4j 적재·교차 검증·공개로 넘어가지 않는다 (부분 자산 보존)."""
        client = R3Qdrant()
        with self.assertRaisesRegex(ValueError, "Snapshot upload failed"):
            self.run_restore(client, apply=True, fail_upload=TB3)
        self.assertEqual(sorted(client.collections), [TB2])
        self.assertEqual((self.calls["cross"], self.calls["slices"], client.alias_ops), ([], [], []))
        with self.assertRaisesRegex(ValueError, "requires empty"):     # 재실행은 빈 DB가 아니어서 거부
            self.run_restore(client, apply=True)

    def test_cli_reports_partial_failure_as_failed(self):
        client = R3Qdrant()
        def failing(args):                                # main이 부르는 restore를 부분 실패 시나리오로 바꾼다
            return self.run_restore(client, apply=True, fail_upload=TB3)
        with tempfile.TemporaryDirectory() as folder, patch.object(r3, "restore", side_effect=failing):
            report = Path(folder) / "report.json"
            self.assertEqual(r3.main(["--bundle", str(self.b.folder), "--apply", "--report", str(report)]), 1)
            output = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(output["status"], "FAILED")
        self.assertIn("Snapshot upload failed", output["reason"])
        self.assertNotIn(SETTINGS["QDRANT_API_KEY"], json.dumps(output))

    def test_neo4j_load_failure_after_uploads_is_not_success(self):
        client = R3Qdrant()
        with self.assertRaisesRegex(ValueError, "Neo4j restore verification failed"):
            self.run_restore(client, apply=True, load_result={"ok": False, "verification": {"ok": False}})
        self.assertEqual((self.calls["cross"], client.alias_ops, client.aliases), ([], [], {}))   # 공개 0회

    def test_verify_only_is_read_only_and_refuses_alignment_or_digest_drift(self):
        client = self.final_client()
        result = self.run_restore(client, verify_only=True)
        self.assertEqual((result["status"], result["aliases"]), ("PASS", {B2: TB2, B3: TB3}))
        self.assertEqual((client.writes, client.alias_ops), ([], []))
        drift = self.final_client()
        point = next(iter(drift.collections[TB3].values()))
        point["payload"]["category"] = "sample_only_drift"
        with self.assertRaisesRegex(ValueError, "verify-only refuses"):
            self.run_restore(drift, verify_only=True)
        changed = self.final_client()
        point = next(iter(changed.collections[TB2].values()))
        point["vector"] = sample_vector(500)
        with self.assertRaisesRegex(ValueError, "vector digest"):
            self.run_restore(changed, verify_only=True)
        self.assertEqual((drift.writes, changed.writes, drift.alias_ops, changed.alias_ops), ([], [], [], []))

    def test_cross_db_failure_on_either_collection_never_passes(self):
        bad = {TB3: {"status": "MISMATCH", "failures": [{"x": 1}], "qdrant_points": 6, "qdrant_count": 6}}
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
        with self.assertRaisesRegex(ValueError, "manifest/counts differ"):
            self.run_restore(R3Qdrant(), pins=None)                # 실제 R3_PINS(C2): 게이트 통과 후 번들 불일치 거부
        with self.assertRaisesRegex(ValueError, "manifest/counts differ"):
            self.run_restore(R3Qdrant(), pins=final_sample_pins())  # 2차 확정 모양: 게이트 통과 후 번들 불일치 거부


def foreign_records():
    """외부 작업이 만든 컬렉션 내용 (sample_only, 번들과 다른 벡터·payload)."""
    return {"sample_only_foreign_1": {"payload": {"sample_only": "foreign"}, "vector": sample_vector(901)},
            "sample_only_foreign_2": {"payload": {"sample_only": "foreign"}, "vector": sample_vector(902)}}


class RacingQdrant(R3Qdrant):
    """get_collections n번째 호출 직전에 외부 작업(inject[n])을 실행한다 — 검사 사이 끼어들기 흉내."""

    def __init__(self, inject, collections=None):
        super().__init__(collections)
        self.inject, self.list_calls = inject, 0

    def get_collections(self):
        self.list_calls += 1
        if self.list_calls in self.inject:
            self.inject[self.list_calls](self)
        return super().get_collections()


class CollectionRaceTests(unittest.TestCase):
    """#111 P1-3: 복원 중 외부 작업이 만든 컬렉션을 덮어쓰지 않고 PASS도 반환하지 않는다.

    --apply의 get_collections 호출 순서: 1 require_empty · 2 임시 B2 직전 · 3 임시 B2 직후 · 4 임시 B3 직전 ·
    5 임시 B3 직후 · 6 검증 전 · 7 공개 직전 · 8 공개 직후.
    RestoreTests의 준비·실행 도우미만 빌려 쓴다 (상속하면 기존 테스트가 한 번 더 돈다).
    """
    setUp = RestoreTests.setUp
    run_restore = RestoreTests.run_restore
    final_client = RestoreTests.final_client

    def assert_stopped_without_pass(self, uploads, client=None):
        self.assertEqual([u[0] for u in self.calls["uploads"]], uploads)
        self.assertEqual((self.calls["cross"], self.calls["slices"]), ([], []))    # Neo4j·교차 검증 단계 미진입
        if client is not None:
            self.assertEqual((client.alias_ops, client.aliases), ([], {}))          # 공개 0회

    def test_external_b3_created_right_after_b2_upload_is_never_overwritten(self):
        client, foreign = R3Qdrant(), foreign_records()
        before = r2.vector_digest(foreign)

        def create_b3(name, c):
            if name == TB2:
                c.collections[B3] = copy.deepcopy(foreign)
        with self.assertRaisesRegex(ValueError, f"collection set mismatch after uploading {TB2}.*unexpected=\\['{B3}'\\]"):
            self.run_restore(client, apply=True, after_post=create_b3)
        self.assert_stopped_without_pass([TB2], client)                           # B3 업로드 0회
        self.assertEqual(client.collections[B3], foreign)
        self.assertEqual(r2.vector_digest(client.collections[B3]), before)
        self.assertEqual(client.writes, [])

    def test_external_b3_created_between_after_and_before_checks_is_refused_before_b3_upload(self):
        foreign = foreign_records()
        client = RacingQdrant({4: lambda c: c.collections.__setitem__(B3, copy.deepcopy(foreign))})
        with self.assertRaisesRegex(ValueError, f"collection set mismatch before uploading {TB3}"):
            self.run_restore(client, apply=True)
        self.assert_stopped_without_pass([TB2], client)
        self.assertEqual(client.collections[B3], foreign)
        self.assertEqual(sorted(client.collections[TB2]), sorted(self.b.records["b2"]))  # 부분 자산 보존(삭제 없음)

    def test_external_canonical_b3_created_inside_temp_upload_post_is_preserved_and_fails(self):
        """(예전 잔여 위험 재현 테스트의 대체) 업로드가 임시 이름으로 가므로 POST 도중 생긴 정식 B3는 덮어써지지 않는다."""
        client, foreign = R3Qdrant(), foreign_records()

        def create_b3(name, c):
            if name == TB3:
                c.collections[B3] = copy.deepcopy(foreign)
        with self.assertRaisesRegex(ValueError, f"collection set mismatch after uploading {TB3}.*unexpected=\\['{B3}'\\]"):
            self.run_restore(client, apply=True, before_post=create_b3)
        self.assert_stopped_without_pass([TB2, TB3], client)
        self.assertEqual(client.collections[B3], foreign)                          # 외부 데이터 보존

    def test_b3_existing_before_restore_is_refused_and_preserved(self):
        for name in (B2, B3):
            with self.subTest(existing=name):
                self.calls = {"cross": [], "slices": [], "uploads": []}
                foreign = foreign_records()
                client = R3Qdrant({name: foreign})
                with self.assertRaisesRegex(ValueError, "requires empty"):
                    self.run_restore(client, apply=True)
                self.assert_stopped_without_pass([], client)
                self.assertEqual((sorted(client.collections), client.collections[name]), ([name], foreign))

    def test_unexpected_other_collection_is_refused_at_every_check(self):
        add_other = lambda c: c.collections.__setitem__(OTHER, foreign_records())  # noqa: E731
        cases = (("before", TB2, RacingQdrant({2: add_other}), None, []),         # require_empty 직후·B2 업로드 전
                 ("after", TB2, R3Qdrant(), TB2, [TB2]),
                 ("after", TB3, R3Qdrant(), TB3, [TB2, TB3]))
        for stage, target, client, after_name, uploads in cases:
            with self.subTest(stage=stage, target=target):
                self.calls = {"cross": [], "slices": [], "uploads": []}
                hook = (lambda name, c, n=after_name: add_other(c) if name == n else None) if after_name else None
                with self.assertRaisesRegex(ValueError, f"collection set mismatch {stage} uploading {target}"
                                                        f".*unexpected=\\['{OTHER}'\\]"):
                    self.run_restore(client, apply=True, after_post=hook)
                self.assert_stopped_without_pass(uploads, client)
                self.assertEqual(client.collections[OTHER], foreign_records())

    def test_b2_point_count_change_stops_before_b3_upload(self):
        def drop_point(name, c):
            if name == TB2:
                c.collections[TB2].pop(next(iter(c.collections[TB2])))
        client = R3Qdrant()
        with self.assertRaisesRegex(ValueError, f"point count changed before uploading {TB3}: {TB2}"):
            self.run_restore(client, apply=True, after_post=drop_point)
        self.assert_stopped_without_pass([TB2], client)

    def test_other_collection_created_inside_upload_post_is_detected(self):
        def add_other(name, c):
            if name == TB3:
                c.collections[OTHER] = foreign_records()
        client = R3Qdrant()
        with self.assertRaisesRegex(ValueError, f"collection set mismatch after uploading {TB3}"):
            self.run_restore(client, apply=True, before_post=add_other)
        self.assert_stopped_without_pass([TB2, TB3], client)

    def test_cli_reports_external_collection_race_as_failed(self):
        client = R3Qdrant()

        def create_b3(name, c):
            if name == TB2:
                c.collections[B3] = foreign_records()

        def racing(args):
            return self.run_restore(client, apply=True, after_post=create_b3)
        with tempfile.TemporaryDirectory() as folder, patch.object(r3, "restore", side_effect=racing):
            report = Path(folder) / "report.json"
            self.assertEqual(r3.main(["--bundle", str(self.b.folder), "--apply", "--report", str(report)]), 1)
            output = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual((output["status"], output["error_type"]), ("FAILED", "ValueError"))
        self.assertIn("collection set mismatch after uploading", output["reason"])
        self.assertIn("restore stopped; partial state preserved; inspect before retry", output["reason"])
        self.assertNotIn("overwritten", output["reason"])          # 사실보다 강한 표현 금지
        self.assertNotIn(SETTINGS["QDRANT_API_KEY"], json.dumps(output))
        self.assertEqual(client.collections[B3], foreign_records())


class TempCollectionTests(unittest.TestCase):
    """임시 이름 생성·충돌·정식 이름 업로드 금지."""
    setUp = RestoreTests.setUp
    run_restore = RestoreTests.run_restore
    assert_stopped_without_pass = CollectionRaceTests.assert_stopped_without_pass

    def test_default_temp_names_are_random_128_bit_and_keep_the_canonical_prefix(self):
        names = {r3.temp_collection_name(B2) for _ in range(20)}
        self.assertEqual(len(names), 20)
        for name in names:
            match = r3.TEMP_NAME.fullmatch(name)
            self.assertEqual(match["canonical"], B2)
            self.assertEqual(len(name) - len(B2) - len(r3.TEMP_INFIX), 32)
        for bad in ("A" * 32, "a" * 31, "../" + "a" * 29, ""):
            with self.subTest(token=bad), self.assertRaises(ValueError):
                r3.temp_collection_name(B2, bad)

    def test_temp_names_pass_the_real_experiment_collection_guard(self):
        """verify_full_index·verify_cross_db는 store 이름 보호(exp_ + 60자 이하)를 거친다. fake가 숨길 수 있어 실제 함수로 본다."""
        for canonical in (B2, B3):
            name = r3.temp_collection_name(canonical)
            self.assertEqual(store.assert_experiment_collection(name), name)
            self.assertLessEqual(len(name), 64)
        self.assertNotIn(TB2, store.PROTECTED_COLLECTIONS)

    def test_upload_to_public_name_is_refused_before_any_http_call(self):
        calls = []
        snapshot = Path(self.tmp.name) / "sample_only.snapshot"
        snapshot.write_bytes(b"sample_only")
        for name in (B2, B3, OTHER, B3 + r3.TEMP_INFIX + "z" * 32):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "non-temporary collection name"):
                r3.upload_snapshot(SETTINGS, name, snapshot, "0" * 64, http_post=lambda *a, **k: calls.append(a))
        self.assertEqual(calls, [])

    def test_temp_name_collision_found_by_checks_is_refused_before_upload(self):
        for call, target, uploads in ((2, TB2, []), (4, TB3, [TB2])):
            with self.subTest(target=target):
                self.calls = {"cross": [], "slices": [], "uploads": []}
                foreign = foreign_records()
                client = RacingQdrant({call: lambda c, t=target: c.collections.__setitem__(t, copy.deepcopy(foreign))})
                with self.assertRaisesRegex(ValueError, f"collection set mismatch before uploading {target}"):
                    self.run_restore(client, apply=True)
                self.assert_stopped_without_pass(uploads, client)
                self.assertEqual(client.collections[target], foreign)            # 충돌한 기존 데이터 불변

    def test_invalid_token_is_refused_before_writes(self):
        client = R3Qdrant()
        with self.assertRaisesRegex(ValueError, "Invalid temporary collection token"):
            self.run_restore(client, apply=True, tokens=("../" + "a" * 29, TOKEN_B3))
        self.assert_stopped_without_pass([], client)

    def test_known_defect_temp_name_created_inside_upload_post_is_overwritten(self):
        """[알려진 결함 재현 — 보호 검증 아님] 직전 검사 뒤 서버 존재 확인 전에 같은 임시 이름이 생기면 덮어쓴다.

        Qdrant 1.12.4 업로드 복구에 조건부 생성이 없어서다. 128비트 난수 이름이라 실행 밖에서 알기 어렵다는 확률적
        완화만 있다. 이 테스트의 PASS는 결함이 여전히 존재함을 뜻하며 안전성 근거로 세지 않는다.
        """
        client, foreign = R3Qdrant(), foreign_records()

        def create_temp(name, c):
            if name == TB3:
                c.collections[TB3] = copy.deepcopy(foreign)
        result = self.run_restore(client, apply=True, before_post=create_temp)
        self.assertEqual(result["status"], "PASS")                                 # 감지 못 함
        self.assertNotIn("sample_only_foreign_1", client.collections[TB3])         # 외부 내용이 사라짐


class AliasPublishTests(unittest.TestCase):
    """정식 alias 공개: 검증 완료 전 공개 0회, 서버의 같은 이름 컬렉션 거부, 부분 공개, alias 잔여 위험."""
    setUp = RestoreTests.setUp
    run_restore = RestoreTests.run_restore
    final_client = RestoreTests.final_client

    def test_existing_public_alias_is_refused_by_precheck_and_unchanged(self):
        dangling = R3Qdrant()
        dangling.aliases = {B2: "sample_only_target"}
        with self.assertRaisesRegex(ValueError, "requires no Qdrant aliases"):
            self.run_restore(dangling, apply=True)
        self.assertEqual((dangling.aliases, dangling.alias_ops, self.calls["uploads"]),
                         ({B2: "sample_only_target"}, [], []))
        with_target = R3Qdrant({"sample_only_target": foreign_records()})
        with_target.aliases = {B3: "sample_only_target"}
        with self.assertRaisesRegex(ValueError, "requires empty"):
            self.run_restore(with_target, apply=True)
        self.assertEqual((with_target.aliases, with_target.alias_ops), ({B3: "sample_only_target"}, []))
        preflight = R3Qdrant()
        preflight.aliases = {B3: "sample_only_target"}
        with self.assertRaisesRegex(ValueError, "requires no Qdrant aliases"):
            self.run_restore(preflight)                                              # preflight도 거부

    def test_digest_mismatch_in_temp_collection_publishes_nothing(self):
        def tamper(name, c):
            if name == TB2:
                next(iter(c.collections[TB2].values()))["vector"] = sample_vector(777)
        client = R3Qdrant()
        with self.assertRaisesRegex(ValueError, "vector digest differs"):
            self.run_restore(client, apply=True, after_post=tamper)
        self.assertEqual((client.alias_ops, client.aliases, self.calls["cross"]), ([], {}, []))

    def test_neo4j_or_cross_db_failure_publishes_nothing(self):
        cases = (dict(load_result={"ok": False, "verification": {"ok": False}}),
                 dict(cross={TB3: {"status": "MISMATCH", "failures": [{"x": 1}], "qdrant_points": 6,
                                   "qdrant_count": 6}}))
        for kwargs in cases:
            with self.subTest(case=sorted(kwargs)):
                self.calls = {"cross": [], "slices": [], "uploads": []}
                client = R3Qdrant()
                with self.assertRaises(ValueError):
                    self.run_restore(client, apply=True, **kwargs)
                self.assertEqual((client.alias_ops, client.aliases), ([], {}))
                self.assertEqual(sorted(client.collections), sorted([TB2, TB3]))     # 임시 컬렉션 보존(삭제 없음)

    def test_public_collection_created_just_before_publish_is_rejected_by_server_and_preserved(self):
        """공개 직전 검사 뒤 정식 B3 컬렉션 생성 → 서버(fake도 같음)가 B3 alias 생성을 거부. 앞의 B2 alias는 남는다."""
        client, foreign = R3Qdrant(), foreign_records()
        client.alias_hook = lambda c: c.collections.__setitem__(B3, copy.deepcopy(foreign))
        with self.assertRaises(r3.PublishError) as caught:
            self.run_restore(client, apply=True)
        self.assertEqual(client.collections[B3], foreign)                          # 외부 데이터 불변
        self.assertTrue(caught.exception.partial_publish)
        self.assertEqual(caught.exception.aliases, {B2: TB2})
        self.assertEqual(client.aliases, {B2: TB2})                                # 부분 공개된 alias를 지우지 않음
        self.assertEqual(sorted(client.collections), sorted([B3, TB2, TB3]))       # 임시 컬렉션도 보존

    def test_partial_publish_is_reported_as_failed_by_cli(self):
        client = R3Qdrant()
        client.alias_hook = lambda c: c.collections.__setitem__(B3, foreign_records())

        def partial(args):
            return self.run_restore(client, apply=True)
        with tempfile.TemporaryDirectory() as folder, patch.object(r3, "restore", side_effect=partial):
            report = Path(folder) / "report.json"
            self.assertEqual(r3.main(["--bundle", str(self.b.folder), "--apply", "--report", str(report)]), 1)
            output = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual((output["status"], output["error_type"]), ("FAILED", "PublishError"))
        self.assertIs(output["partial_publish"], True)
        self.assertEqual(output["aliases"], {B2: TB2})
        self.assertIn("Alias publish failed", output["reason"])
        self.assertNotIn(SETTINGS["QDRANT_API_KEY"], json.dumps(output))

    def test_known_defect_public_alias_created_after_precheck_is_overwritten(self):
        """[알려진 결함 재현 — 보호 검증 아님] 공개 직전 검사 뒤 외부가 정식 alias를 만들면 서버는 거부하지 않고 덮어쓴다.

        (1) 외부 alias가 우리 임시 컬렉션을 가리키면 감지도 못 하고 PASS. (2) 외부 컬렉션을 가리키면 그 컬렉션 때문에
        직후 검사가 FAILED를 내지만, 외부 alias 매핑은 이미 바뀐 뒤다. 두 경우 모두 alias 덮어쓰기는 막지 못한다.
        이 테스트의 PASS는 결함이 여전히 존재함을 뜻하며 안전성 근거로 세지 않는다.
        """
        silent = R3Qdrant()
        silent.alias_hook = lambda c: c.aliases.__setitem__(B3, TB2)
        result = self.run_restore(silent, apply=True)
        self.assertEqual(result["status"], "PASS")                                 # 감지 못 함
        self.assertEqual(silent.aliases[B3], TB3)                                  # 외부 매핑(B3→TB2)이 사라짐
        self.calls = {"cross": [], "slices": [], "uploads": []}
        loud = R3Qdrant()

        def external_alias(c):
            c.collections["sample_only_target"] = foreign_records()
            c.aliases[B3] = "sample_only_target"
        loud.alias_hook = external_alias
        with self.assertRaises(r3.PublishError) as caught:
            self.run_restore(loud, apply=True)
        self.assertFalse(caught.exception.partial_publish)
        self.assertEqual(loud.aliases[B3], TB3)                                    # FAILED지만 외부 매핑은 이미 바뀜
        self.assertEqual(loud.collections["sample_only_target"], foreign_records())  # Point 데이터는 남음

    def test_verify_only_requires_exact_published_state(self):
        def variant(change):
            client = self.final_client()
            change(client)
            return client
        cases = {
            "direct_public_collections": R3Qdrant({B2: self.b.records["b2"], B3: self.b.records["b3"]}),
            "temps_without_aliases": variant(lambda c: setattr(c, "aliases", {})),
            "partial_alias": variant(lambda c: c.aliases.pop(B3)),
            "swapped_alias": variant(lambda c: c.aliases.update({B2: TB3, B3: TB2})),
            "extra_alias": variant(lambda c: c.aliases.update({"sample_only_alias": TB2})),
            "extra_collection": variant(lambda c: c.collections.__setitem__(OTHER, {})),
            "non_temp_target": variant(lambda c: (c.collections.__setitem__(OTHER, c.collections.pop(TB2)),
                                                  c.aliases.update({B2: OTHER}))),
        }
        for name, client in cases.items():
            with self.subTest(state=name), self.assertRaisesRegex(ValueError, "published state"):
                self.run_restore(client, verify_only=True)
            self.assertEqual((client.writes, client.alias_ops), ([], []))

    def test_update_tool_still_refuses_a_published_database(self):
        """update 도구는 build 전용: 임시 이름(r3가 아닌 컬렉션)과 alias가 있는 DB를 모두 거부한다 (도구 변경 없음)."""
        from scripts import update_r3_collections as update
        with self.assertRaises(update.UpdateError):
            update.require_isolated_server(self.final_client())
        canonical_with_alias = R3Qdrant({B2: self.b.records["b2"], B3: self.b.records["b3"]})
        canonical_with_alias.aliases = {"sample_only_alias": B2}
        with self.assertRaisesRegex(update.UpdateError, "별칭"):
            update.require_isolated_server(canonical_with_alias)


class PayloadDigestTests(unittest.TestCase):
    """#111 P2-4: 최종 전체 payload가 기준(릴리스에서 만든 기대 payload + 번들 payload_digest)과 다르면 PASS하지 않는다."""
    setUp = RestoreTests.setUp
    run_restore = RestoreTests.run_restore
    final_client = RestoreTests.final_client

    def bundle_digest(self, key):
        return json.loads((self.b.folder / "package.json").read_text(encoding="utf-8"))["collections"][key]["payload_digest"]

    def test_valid_payload_passes_apply_and_verify_only(self):
        client = R3Qdrant()
        applied = self.run_restore(client, apply=True)
        self.assertEqual({k: v["payload_digest"] for k, v in applied["collections"].items()},
                         {k: self.bundle_digest(k) for k in profiles.COLLECTION_KEYS})
        self.calls = {"cross": [], "slices": [], "uploads": []}
        verified = self.run_restore(client, verify_only=True)
        self.assertEqual((applied["status"], verified["status"]), ("PASS", "PASS"))
        self.assertEqual(client.writes, [])

    def test_verify_only_refuses_any_payload_drift_in_either_collection_without_writing(self):
        cases = {
            "extra_field": (lambda p: p.__setitem__("sample_only_unexpected", "x"), "Final payload differs"),
            "deleted_null_field": (lambda p: p.pop("page_start"), "Final payload differs"),
            "deleted_field": (lambda p: p.pop("section_title"), "Unexpected protected payload difference"),
            "changed_field": (lambda p: p.__setitem__("region", "sample_only_region"),
                              "Unexpected protected payload difference"),
            "changed_index_run_id": (lambda p: p.__setitem__("index_run_id", "RUN_SAMPLE_ONLY_OTHER"),
                                     "payload digest differs from the bundle"),
            "changed_alignment_field": (lambda p: p.__setitem__("category", "sample_only_old"), "verify-only refuses"),
        }
        for collection in (TB2, TB3):
            for name, (change, message) in cases.items():
                with self.subTest(collection=collection, drift=name):
                    client = self.final_client()
                    change(next(iter(client.collections[collection].values()))["payload"])
                    with self.assertRaisesRegex(ValueError, message):
                        self.run_restore(client, verify_only=True)
                    self.assertEqual((client.writes, client.alias_ops), ([], []))      # verify-only 쓰기 0회

    def test_intended_alignment_in_apply_is_verified_after_alignment(self):
        """snapshot의 category가 옛 값이면 --apply가 의도된 정렬로 고치고, 고친 뒤 전체 digest가 기준과 같아야 PASS."""
        def old_category(name, c):
            if name == TB2:
                next(iter(c.collections[TB2].values()))["payload"]["category"] = "sample_only_old_category"
        client = R3Qdrant()
        result = self.run_restore(client, apply=True, after_post=old_category)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual((result["collections"]["b2"]["metadata_points_updated"],
                          result["collections"]["b3"]["metadata_points_updated"]), (1, 0))
        self.assertEqual(result["collections"]["b2"]["payload_digest"], self.bundle_digest("b2"))
        self.assertEqual(len(client.writes), 1)
        self.calls = {"cross": [], "slices": [], "uploads": []}
        self.assertEqual(self.run_restore(client, verify_only=True)["status"], "PASS")
        self.assertEqual(len(client.writes), 1)                                     # verify-only는 더 쓰지 않음

    def test_apply_with_unexpected_snapshot_payload_fails_before_publish_and_keeps_data(self):
        def extra(name, c):
            if name == TB3:
                next(iter(c.collections[TB3].values()))["payload"]["sample_only_unexpected"] = "x"
        client = R3Qdrant()
        with self.assertRaisesRegex(ValueError, "Final payload differs.*b3.*sample_only_unexpected"):
            self.run_restore(client, apply=True, after_post=extra)
        self.assertEqual((client.alias_ops, client.aliases, self.calls["cross"]), ([], {}, []))
        self.assertTrue(any("sample_only_unexpected" in r["payload"] for r in client.collections[TB3].values()))

    def test_bundle_payload_digest_mismatch_fails_both_modes(self):
        for key in profiles.COLLECTION_KEYS:
            with self.subTest(key=key):
                package = self.b.package()
                package["collections"][key]["payload_digest"] = "f" * 64
                self.b.write(package)
                client = R3Qdrant()
                with self.assertRaisesRegex(ValueError, f"payload digest differs from the bundle payload_digest: {key}"):
                    self.run_restore(client, apply=True)
                self.assertEqual((client.alias_ops, client.aliases), ([], {}))
                with self.assertRaisesRegex(ValueError, "payload digest differs"):
                    self.run_restore(self.final_client(), verify_only=True)
                self.b.write()

    def test_missing_or_invalid_bundle_payload_digest_is_refused_before_writes(self):
        for value in (None, "", "F" * 64, "0" * 63):
            with self.subTest(value=value):
                package = self.b.package()
                if value is None:
                    package["collections"]["b3"].pop("payload_digest")
                else:
                    package["collections"]["b3"]["payload_digest"] = value
                self.b.write(package)
                client = R3Qdrant()
                with self.assertRaisesRegex(ValueError, "payload_digest missing or invalid: b3"):
                    self.run_restore(client, apply=True)
                self.assertEqual((self.calls["uploads"], client.writes), ([], []))
        self.b.write()

    def test_alias_path_payload_digest_is_checked(self):
        client = self.final_client()
        physical, canonical = {"b2": TB2, "b3": TB3}, {"b2": B2, "b3": B3}
        entries = json.loads((self.b.folder / "package.json").read_text(encoding="utf-8"))["collections"]
        self.assertEqual(r3.verify_published_aliases(client, r2, physical, canonical, entries), {B2: TB2, B3: TB3})
        wrong = {**entries, "b3": {**entries["b3"], "payload_digest": "f" * 64}}
        with self.assertRaises(r3.PublishError) as caught:
            r3.verify_published_aliases(client, r2, physical, canonical, wrong)
        self.assertFalse(caught.exception.partial_publish)


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
