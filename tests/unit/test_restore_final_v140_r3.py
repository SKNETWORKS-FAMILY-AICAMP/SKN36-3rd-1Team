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

from r3_fixtures import (MANIFEST, MISSING_SCHEMA, RUN_OLD, ArmedTestGuard, GuardRefused, R3Qdrant,  # noqa: E402
                         Sample, final_sample_pins, keyword_payload_schema, pending_pins, sample_vector, stage1_pins)

from database import full_index as fi, handoff_data as hd, hybrid_search as hs  # noqa: E402
from database.neo4j import load_graph as lg  # noqa: E402
from database.qdrant import store  # noqa: E402
from scripts import restore_final_v140 as r2  # noqa: E402
from scripts import restore_final_v140_r3 as r3  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402
from scripts import restore_r3_isolated as wrap  # noqa: E402  (P1-B: 실제 IsolatedWriteGuard.check를 본체와 함께 검증)

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
    """현재 릴리스 기준 확정 모양(final_sample_pins: 실제 R3_PINS의 manifest·건수·points + sample_only 계획·digest)으로 만든
    최소 번들(sample_only 바이트)에서 Point 수는 그대로 두고 vector_digest만 바꾸면 해당 컬렉션에서 거부된다.
    B2·B3 각각 확인한다 (계획 대조가 아니라 번들 메타데이터 대조 경로). 실제 R3_PINS(C0 pending)는 같은 번들을 게이트에서 거부한다."""

    def setUp(self):
        self.pins = final_sample_pins()

    def build(self, folder: Path, tamper: str | None = None) -> None:
        pins = self.pins
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

    def test_final_shape_pins_accept_matching_metadata_and_refuse_digest_only_change(self):
        self.assertEqual(profiles.pin_stage(self.pins), "stage2")
        with tempfile.TemporaryDirectory() as folder:
            self.build(Path(folder))
            package, snapshots = r3.check_bundle(folder, self.pins)      # 확정 핀과 같은 메타데이터는 통과
            self.assertEqual(set(snapshots), set(profiles.COLLECTION_KEYS))
        for key in profiles.COLLECTION_KEYS:
            with self.subTest(tampered=key), tempfile.TemporaryDirectory() as folder:
                self.build(Path(folder), tamper=key)
                points = json.loads((Path(folder) / "package.json").read_text(encoding="utf-8"))["collections"][key]["points"]
                self.assertEqual(points, profiles.R3_PINS["collections"][key]["points"])   # Point 수는 실제 핀과 같다
                with self.assertRaisesRegex(ValueError, rf"count/digest: {key}"):
                    r3.check_bundle(folder, self.pins)

    def test_real_c0_pins_refuse_the_same_bundle_at_the_gate(self):
        self.assertEqual(profiles.pin_stage(), "pending")
        with tempfile.TemporaryDirectory() as folder:
            self.build(Path(folder))
            with self.assertRaisesRegex(profiles.ProfileError, "확정되지 않았습니다"):
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

    def test_real_c0_pins_refuse_every_bundle_at_the_gate(self):
        """실제 R3_PINS(C0 pending)는 계획·digest가 미확정이라 번들 내용을 보기 전에 기준값 게이트에서 거부한다."""
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
        self.events = []          # guard·쓰기 호출 순서 (#111 P1-B): ("guard", stage) / ("write", 이름)

    def run_restore(self, client, *, apply=False, verify_only=False, totals=None, cross=None, settings=SETTINGS,
                    pins="default", fail_upload=None, load_result=None, before_post=None, after_post=None,
                    tokens=(TOKEN_B2, TOKEN_B3), write_guard="default"):
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
            self.events.append(("write", f"upload:{name}"))
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

        def load_graph(*_args, **_kwargs):                   # 호출을 기록하는 것 말고는 예전 return_value와 같다
            self.events.append(("write", "load_graph"))
            hook = getattr(self, "on_load_graph", None)      # 적재 중 외부 변경 시나리오 (P1-B 테스트만 설정)
            if hook:
                hook()
            return load_result or {"ok": True, "verification": {"ok": True}}

        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {}))
            for target, name, value in (
                    (fi, "manifest_sha256", MANIFEST), (hd, "verify_manifest_checksums", []),
                    (hd, "load_documents", s.documents), (hd, "load_payload_metadata", s.metadata),
                    (hd, "load_payload_chunks", [None] * 6), (lg, "read_graph_input", object()),
                    (lg, "validate_graph_input", validation), (lg, "Neo4jDriverRunner", runner),
                    (lg, "verify_loaded_graph", {"ok": True})):
                stack.enter_context(patch.object(target, name, return_value=value))
            stack.enter_context(patch.object(lg, "load_graph", side_effect=load_graph))
            stack.enter_context(patch.object(fi, "build_config_chunks",
                                             side_effect=lambda name, documents: (chunks_by_config[name], {})))
            stack.enter_context(patch.object(hs, "verify_cross_db", side_effect=cross_db))
            stack.enter_context(patch.object(fi, "verify_full_index", side_effect=slices))
            args = Namespace(repo=ROOT, bundle=self.b.folder, apply=apply, verify_only=verify_only)
            return ORIGINAL_RESTORE(args, settings=settings, pins=self.b.pins if pins == "default" else pins,
                                    http_post=post, client_factory=lambda st: client,
                                    driver_factory=lambda st: SimpleNamespace(verify_connectivity=lambda: None,
                                                                              close=lambda: None),
                                    token_factory=iter(tokens).__next__,
                                    # apply는 격리 래퍼가 만드는 허가 객체를 흉내 낸 테스트 guard (P1-A). preflight·verify-only는 없음
                                    write_guard=(ArmedTestGuard(events=self.events) if apply else None)
                                    if isinstance(write_guard, str) and write_guard == "default" else write_guard)

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
            # 직접 --apply는 이제 종료 코드 2로 거부된다(P1-A, DirectApplyGuardTests). 실패 보고 경로는 모드와 같은 코드라
            # 시나리오는 그대로 두고 CLI 인자만 --verify-only로 둔다 (restore는 시나리오 함수로 바꿔 끼움).
            self.assertEqual(r3.main(["--bundle", str(self.b.folder), "--verify-only", "--report", str(report)]), 1)
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
        with self.assertRaisesRegex(profiles.ProfileError, "확정되지 않았습니다"):
            self.run_restore(R3Qdrant(), pins=None)                # 실제 R3_PINS(C0 pending): 기준값 게이트에서 거부
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
            # 직접 --apply는 이제 종료 코드 2로 거부된다(P1-A, DirectApplyGuardTests). 실패 보고 경로는 모드와 같은 코드라
            # 시나리오는 그대로 두고 CLI 인자만 --verify-only로 둔다 (restore는 시나리오 함수로 바꿔 끼움).
            self.assertEqual(r3.main(["--bundle", str(self.b.folder), "--verify-only", "--report", str(report)]), 1)
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
            # 직접 --apply는 이제 종료 코드 2로 거부된다(P1-A, DirectApplyGuardTests). 실패 보고 경로는 모드와 같은 코드라
            # 시나리오는 그대로 두고 CLI 인자만 --verify-only로 둔다 (restore는 시나리오 함수로 바꿔 끼움).
            self.assertEqual(r3.main(["--bundle", str(self.b.folder), "--verify-only", "--report", str(report)]), 1)
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


class DirectApplyGuardTests(unittest.TestCase):
    """#111 P1-A: 본체 직접 --apply 차단과 write_guard 필수 검증 (T-A1~A4). 거부는 번들·DB 접근 전에 일어나야 한다."""
    setUp = RestoreTests.setUp
    run_restore = RestoreTests.run_restore

    def test_a1_cli_direct_apply_exits_2_without_any_access(self):
        import io
        from contextlib import redirect_stderr
        import neo4j
        import qdrant_client
        import requests
        never = AssertionError("no access allowed")
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(r3, "restore", side_effect=never) as restore_mock, \
                patch.object(r3, "check_bundle", side_effect=never) as bundle_mock, \
                patch.object(qdrant_client, "QdrantClient", side_effect=never) as qdrant_mock, \
                patch.object(neo4j.GraphDatabase, "driver", side_effect=never) as driver_mock, \
                patch.object(requests, "post", side_effect=never) as post_mock, \
                patch.dict(os.environ, {"QDRANT_URL": "http://127.0.0.1:56656", "QDRANT_API_KEY": "sample_only_key",
                                        "NEO4J_URI": "bolt://127.0.0.1:56687", "NEO4J_USER": "neo4j",
                                        "NEO4J_PASSWORD": "sample_only_pw"}), redirect_stderr(stderr):
            report = Path(folder) / "report.json"
            with self.assertRaises(SystemExit) as caught:
                r3.main(["--bundle", str(self.b.folder), "--apply", "--report", str(report)])
            self.assertFalse(report.exists())                                   # 보고서도 만들지 않는다
        self.assertEqual(caught.exception.code, 2)
        for mock in (restore_mock, bundle_mock, qdrant_mock, driver_mock, post_mock):
            self.assertEqual(mock.call_count, 0)
        self.assertIn("scripts/restore_r3_isolated.py", stderr.getvalue())
        self.assertIn("direct --apply is refused", stderr.getvalue())
        self.assertNotIn("sample_only_key", stderr.getvalue())

    def bad_guards(self):
        class Unarmed(r3.WriteGuard):
            run_id = "c" * 32

            def check(self, stage, expected):
                pass

        class NotCallable(r3.WriteGuard):
            armed, run_id, check = True, "c" * 32, 1

        class TruthyArmed(r3.WriteGuard):
            armed, run_id = 1, "c" * 32          # True가 아니라 1 → armed is True 아님

            def check(self, stage, expected):
                pass

        duck = SimpleNamespace(armed=True, run_id="c" * 32, check=lambda stage, expected: None)  # 상속 안 한 같은 모양
        cases = {"none": (None, "requires a write_guard"), "base_class": (r3.WriteGuard(), "Invalid write_guard"),
                 "duck_typed": (duck, "Invalid write_guard"), "unarmed": (Unarmed(), "not armed"),
                 "truthy_armed": (TruthyArmed(), "not armed"), "check_not_callable": (NotCallable(), "not callable")}
        for label, run_id in (("empty", ""), ("upper", "C" * 32), ("short", "c" * 31), ("long", "c" * 33),
                              ("non_hex", "g" * 32), ("not_str", 12345), ("none", None)):
            cases[f"run_id_{label}"] = (ArmedTestGuard(run_id), "run_id must be 32 lowercase hex")
        return cases

    def test_a2_a3_invalid_write_guard_is_refused_before_bundle_or_db(self):
        for name, (guard, message) in self.bad_guards().items():
            with self.subTest(guard=name):
                calls = []
                record = lambda tag: (lambda *a, **k: calls.append(tag))  # noqa: E731
                with patch.object(r3, "check_bundle", side_effect=lambda *a, **k: calls.append("check_bundle")):
                    with self.assertRaisesRegex(ValueError, message):
                        ORIGINAL_RESTORE(Namespace(repo=ROOT, bundle=self.b.folder, apply=True, verify_only=False),
                                         settings=SETTINGS, pins=self.b.pins, http_post=record("http_post"),
                                         client_factory=record("client"), driver_factory=record("driver"),
                                         write_guard=guard)
                self.assertEqual(calls, [])                                     # 번들·HTTP·Qdrant·Neo4j 접근 0

    def test_a2_run_id_with_surrounding_whitespace_is_refused_before_any_access(self):
        """P1-A 보완: run_id 앞뒤 줄바꿈·공백은 32자리 hex가 아니다 (RUN_ID.match + $ 는 끝 '\\n'을 허용했다)."""
        import neo4j
        import qdrant_client
        import requests
        valid = "c" * 32
        bad = {"trailing_lf": valid + "\n", "trailing_space": valid + " ", "trailing_crlf": valid + "\r\n",
               "leading_lf": "\n" + valid}
        for name, run_id in bad.items():
            with self.subTest(run_id=name):
                calls = []
                record = lambda tag: (lambda *a, **k: calls.append(tag))  # noqa: E731
                client = R3Qdrant()
                with patch.object(r3, "check_bundle", side_effect=lambda *a, **k: calls.append("check_bundle")), \
                        patch.object(qdrant_client, "QdrantClient", side_effect=record("QdrantClient")), \
                        patch.object(neo4j.GraphDatabase, "driver", side_effect=record("GraphDatabase.driver")), \
                        patch.object(requests, "post", side_effect=record("requests.post")):
                    with self.assertRaisesRegex(ValueError, "run_id must be 32 lowercase hex"):
                        ORIGINAL_RESTORE(Namespace(repo=ROOT, bundle=self.b.folder, apply=True, verify_only=False),
                                         settings=SETTINGS, pins=self.b.pins, http_post=record("http_post"),
                                         client_factory=record("client_factory"),
                                         driver_factory=record("driver_factory"),
                                         write_guard=ArmedTestGuard(run_id))
                self.assertEqual(calls, [])                                     # 번들·Qdrant·Neo4j·HTTP 0
                self.assertEqual((client.writes, client.alias_ops), ([], []))  # DB 쓰기 0
        r3.require_write_guard(ArmedTestGuard(valid))                         # 정상 32자리 소문자 hex는 통과

    def test_a2_valid_guard_is_accepted_and_checked_before_each_write(self):
        guard = ArmedTestGuard()
        client = R3Qdrant()
        result = self.run_restore(client, apply=True, write_guard=guard)
        self.assertEqual(result["status"], "PASS")
        # P1-B: 정렬할 payload가 없는 정상 복원은 쓰기 4종(업로드 2·Neo4j 적재·alias 공개) 직전마다 정확히 1회씩
        self.assertEqual(guard.calls, ["before_qdrant_upload:b2", "before_qdrant_upload:b3", "before_neo4j_load",
                                       "before_alias_publish"])

    def test_a4_preflight_and_verify_only_need_no_guard(self):
        preflight_client = R3Qdrant()
        self.assertEqual(self.run_restore(preflight_client, write_guard=None)["status"], "PREFLIGHT_PASS_NO_WRITES")
        self.assertEqual((preflight_client.writes, self.calls["uploads"]), ([], []))
        published = RestoreTests.final_client(self)
        self.assertEqual(self.run_restore(published, verify_only=True, write_guard=None)["status"], "PASS")
        self.assertEqual((published.writes, published.alias_ops), ([], []))

    def test_a4_cli_preflight_and_verify_only_still_call_restore(self):
        seen = []

        def fake(args):
            seen.append((args.apply, args.verify_only))
            return {"status": "PREFLIGHT_PASS_NO_WRITES" if not args.verify_only else "PASS"}
        with patch.object(r3, "restore", side_effect=fake):
            self.assertEqual(r3.main(["--bundle", str(self.b.folder)]), 0)
            self.assertEqual(r3.main(["--bundle", str(self.b.folder), "--verify-only"]), 0)
        self.assertEqual(seen, [(False, False), (False, True)])


PREFLIGHT_FP = "e" * 64
IDENTITY = {"qdrant": {"id": "q" * 64, "started_at": "2026-11-22T00:00:00Z"},
            "neo4j": {"id": "n" * 64, "started_at": "2026-11-22T00:00:00Z"}}
ALIGNED_ORDER = [("guard", "before_qdrant_upload:b2"), ("write", f"upload:{TB2}"),
                 ("guard", "before_qdrant_upload:b3"), ("write", f"upload:{TB3}"),
                 ("guard", "before_payload_alignment:b2"), ("write", "set_payload"),
                 ("guard", "before_payload_alignment:b3"), ("write", "set_payload"),
                 ("guard", "before_neo4j_load"), ("write", "load_graph"),
                 ("guard", "before_alias_publish"), ("write", "alias_publish")]


class StubContext:
    """IsolatedWriteGuard가 쓰는 Context의 대역 (sample_only). Qdrant는 복원 중인 R3Qdrant 그대로,
    Neo4j 노드·관계 수는 load_graph 호출 여부(events)로 정한다. identity·neo4j_override를 바꿔 외부 변경을 흉내 낸다."""

    def __init__(self, client, test):
        self.values = {"qdrant": "sample_only_key", "neo4j": "sample_only_pw"}
        self.client, self.test = client, test
        self.identity_value, self.neo4j_override = copy.deepcopy(IDENTITY), None
        self.calls = []
        self.runner_factory = lambda driver: SimpleNamespace(read_rows=self.read_rows)

    def identity(self):
        self.calls.append("identity")
        return copy.deepcopy(self.identity_value)

    def snapshot(self):
        self.calls.append("snapshot")
        return self.identity(), {}, PREFLIGHT_FP

    def qdrant_factory(self, values):
        self.calls.append("qdrant")
        return self.client

    def neo4j_factory(self, password):
        self.calls.append("neo4j")
        return SimpleNamespace(close=lambda: None)

    def read_rows(self, query, parameters=None):
        assert query == lg.QUERY_TOTAL_COUNTS, query
        if self.neo4j_override is not None:
            return [dict(self.neo4j_override)]
        loaded = ("write", "load_graph") in self.test.events
        return [{"nodes": 10, "relationships": 12} if loaded else {"nodes": 0, "relationships": 0}]


class StageWriteGuardTests(unittest.TestCase):
    """#111 P1-B (T-B1~B8): 쓰기 5종(B2·B3 업로드, payload 정렬, Neo4j 적재, alias 공개) 직전마다 guard를 부르고,
    거부되면 그 쓰기와 이후 쓰기가 0회인지 본다. 실제 IsolatedWriteGuard.check도 본체와 함께(StubContext) 검증한다."""
    setUp = RestoreTests.setUp
    run_restore = RestoreTests.run_restore
    final_client = RestoreTests.final_client

    def instrument(self, client):
        """set_payload·alias 공개 호출을 events에 기록한다 (동작은 R3Qdrant 그대로). 같은 client에 두 번 걸지 않는다."""
        if getattr(client, "_instrumented", False):
            return client
        client._instrumented = True
        original = client.set_payload

        def set_payload(*args, **kwargs):
            self.events.append(("write", "set_payload"))
            hook = getattr(self, "on_set_payload", None)
            if hook:
                hook(client, args, kwargs)
            return original(*args, **kwargs)
        client.set_payload = set_payload
        client.alias_hook = lambda c: self.events.append(("write", "alias_publish"))
        return client

    @staticmethod
    def old_category(name, c):
        """두 임시 컬렉션 모두 Point 1개의 category를 옛 값으로 → 컬렉션마다 의도된 정렬 1회."""
        next(iter(c.collections[name].values()))["payload"]["category"] = "sample_only_old_category"

    def run_aligned(self, guard, client=None, after_post=None):
        client = self.instrument(client or R3Qdrant())

        def hooks(name, c):
            self.old_category(name, c)
            if after_post:
                after_post(name, c)
        return client, self.run_restore(client, apply=True, after_post=hooks, write_guard=guard)

    def isolated_guard(self, client, *, lock_checker=lambda: True, fingerprint=PREFLIGHT_FP):
        ctx = StubContext(client, self)
        guard = wrap.IsolatedWriteGuard("d" * 32, ctx=ctx, identity=copy.deepcopy(IDENTITY),
                                        preflight_fingerprint=fingerprint, lock_checker=lock_checker)
        return ctx, guard

    def writes(self):
        return [name for kind, name in self.events if kind == "write"]

    def package(self):
        return json.loads((self.b.folder / "package.json").read_text(encoding="utf-8"))["collections"]

    def assert_guard_right_before(self, write, stages):
        guard = ArmedTestGuard(events=self.events)
        _, result = self.run_aligned(guard)
        self.assertEqual(result["status"], "PASS")
        positions = [i for i, event in enumerate(self.events) if event == ("write", write)]
        self.assertEqual(len(positions), len(stages))
        for position, stage in zip(positions, stages):
            self.assertEqual(self.events[position - 1], ("guard", stage))       # 바로 앞 = 그 단계 guard

    # T-B1~B5: 각 쓰기 바로 앞에 해당 단계 guard
    def test_b1_guard_right_before_b2_upload(self):
        self.assert_guard_right_before(f"upload:{TB2}", ["before_qdrant_upload:b2"])

    def test_b2_guard_right_before_b3_upload(self):
        self.assert_guard_right_before(f"upload:{TB3}", ["before_qdrant_upload:b3"])

    def test_b3_guard_right_before_payload_alignment(self):
        self.assert_guard_right_before("set_payload", ["before_payload_alignment:b2", "before_payload_alignment:b3"])

    def test_b4_guard_right_before_neo4j_load(self):
        self.assert_guard_right_before("load_graph", ["before_neo4j_load"])

    def test_b5_guard_right_before_alias_publish(self):
        self.assert_guard_right_before("alias_publish", ["before_alias_publish"])

    # T-B8 (순서·횟수·기대값)
    def test_b8_guard_order_count_and_expected_states(self):
        guard = ArmedTestGuard(events=self.events)
        self.run_aligned(guard)
        self.assertEqual(self.events, ALIGNED_ORDER)                         # 정렬이 있는 복원: guard 6회
        pkg = self.package()
        expected = dict(guard.expectations)
        self.assertEqual(len(guard.expectations), 6)
        empty = {"collections": [], "aliases": {}, "alignment_changes": {}, "projected_payload_digests": {},
                 "points": {}, "vector_digests": {}, "payload_digests": {},
                 "protected_digests": {}, "neo4j_totals": {"nodes": 0, "relationships": 0},
                 "require_preflight_fingerprint": True}
        self.assertEqual(expected["before_qdrant_upload:b2"], empty)
        b3 = expected["before_qdrant_upload:b3"]
        self.assertEqual((b3["collections"], b3["points"], b3["vector_digests"], b3["payload_digests"],
                          b3["require_preflight_fingerprint"]),
                         ([TB2], {TB2: pkg["b2"]["points"]}, {TB2: pkg["b2"]["vector_digest"]}, {}, False))
        both_points = {TB2: pkg["b2"]["points"], TB3: pkg["b3"]["points"]}
        both_vectors = {TB2: pkg["b2"]["vector_digest"], TB3: pkg["b3"]["vector_digest"]}
        both_payloads = {TB2: pkg["b2"]["payload_digest"], TB3: pkg["b3"]["payload_digest"]}
        for stage, payloads, protected in (("before_payload_alignment:b2", {}, [TB2]),
                                           ("before_payload_alignment:b3", {TB2: both_payloads[TB2]}, [TB2, TB3]),
                                           ("before_neo4j_load", both_payloads, [TB2, TB3]),
                                           ("before_alias_publish", both_payloads, [TB2, TB3])):
            with self.subTest(stage=stage):
                value = expected[stage]
                self.assertEqual((value["collections"], value["aliases"], value["points"], value["vector_digests"],
                                  value["payload_digests"], sorted(value["protected_digests"]),
                                  value["require_preflight_fingerprint"]),
                                 ([TB2, TB3], {}, both_points, both_vectors, payloads, protected, False))
                totals = {"nodes": 10, "relationships": 12} if stage == "before_alias_publish" else \
                    {"nodes": 0, "relationships": 0}
                self.assertEqual(value["neo4j_totals"], totals)
                # 정렬 직전에만: 이번 컬렉션의 변경 목록(category만, Point 1개)과 정렬 후 기대 digest = 번들 payload_digest
                aligning = {"before_payload_alignment:b2": TB2, "before_payload_alignment:b3": TB3}.get(stage)
                if aligning:
                    key = "b2" if aligning == TB2 else "b3"
                    self.assertEqual(value["projected_payload_digests"], {aligning: pkg[key]["payload_digest"]})
                    self.assertEqual(list(value["alignment_changes"]), [aligning])
                    self.assertEqual([sorted(changed) for _, changed in value["alignment_changes"][aligning]],
                                     [["category"]])
                else:
                    self.assertEqual((value["alignment_changes"], value["projected_payload_digests"]), ({}, {}))
        # 정렬할 것이 없으면 정렬 guard도 없다 (쓰기 4종 직전 4회)
        self.events.clear()
        plain = ArmedTestGuard(events=self.events)
        self.run_restore(self.instrument(R3Qdrant()), apply=True, write_guard=plain)
        self.assertEqual(plain.calls, ["before_qdrant_upload:b2", "before_qdrant_upload:b3", "before_neo4j_load",
                                       "before_alias_publish"])
        self.assertEqual(self.writes(), [f"upload:{TB2}", f"upload:{TB3}", "load_graph", "alias_publish"])

    def test_b8_real_isolated_guard_accepts_the_true_state_at_every_stage(self):
        """기대값이 실제(대역) 서버 상태와 맞는지: 실제 IsolatedWriteGuard.check로 정렬 포함 전체 복원이 PASS."""
        client = R3Qdrant()
        ctx, guard = self.isolated_guard(client)
        client, result = self.run_aligned(guard, client=client)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(self.writes(), [name for kind, name in ALIGNED_ORDER if kind == "write"])
        self.assertEqual(ctx.calls.count("snapshot"), 1)                    # fingerprint 재확인은 첫 쓰기 직전 1회
        self.assertEqual(ctx.calls.count("qdrant"), 6)                      # 단계마다 새로 읽기

    # T-B7(일부)·요구 7: 단계마다 거부되면 그 쓰기와 이후 쓰기 0회
    def test_b7_rejection_at_each_stage_means_zero_following_writes(self):
        written_before = {"before_qdrant_upload:b2": [], "before_qdrant_upload:b3": [f"upload:{TB2}"],
                          "before_payload_alignment:b2": [f"upload:{TB2}", f"upload:{TB3}"],
                          "before_payload_alignment:b3": [f"upload:{TB2}", f"upload:{TB3}", "set_payload"],
                          "before_neo4j_load": [f"upload:{TB2}", f"upload:{TB3}", "set_payload", "set_payload"],
                          "before_alias_publish": [f"upload:{TB2}", f"upload:{TB3}", "set_payload", "set_payload",
                                                   "load_graph"]}
        for stage, writes in written_before.items():
            with self.subTest(stage=stage):
                self.events.clear()
                self.calls = {"cross": [], "slices": [], "uploads": []}
                guard = ArmedTestGuard(fail_at=stage, events=self.events)
                with self.assertRaises(GuardRefused):
                    client, _ = self.run_aligned(guard)
                self.assertEqual(self.events[-1], ("guard", stage))           # 거부 뒤 아무 쓰기도 없음
                self.assertEqual(self.writes(), writes)
                self.assertEqual(guard.calls[-1], stage)

    def test_b7_real_guard_rejections_leave_db_unchanged_after_the_check(self):
        client = R3Qdrant()
        _, guard = self.isolated_guard(client, fingerprint="0" * 64)          # 첫 쓰기 직전 fingerprint 불일치
        with self.assertRaisesRegex(wrap.IsolationError, "fingerprint"):
            self.run_aligned(guard, client=client)
        self.assertEqual((self.writes(), client.collections, client.writes, client.alias_ops), ([], {}, [], []))

    # T-B6: 컨테이너 ID·시작 시각 변경
    def test_b6_container_identity_change_is_refused_before_the_next_write(self):
        for field, value in (("started_at", "2026-11-22T01:00:00Z"), ("id", "x" * 64)):
            for service in ("qdrant", "neo4j"):
                with self.subTest(service=service, field=field):
                    self.events.clear()
                    client = R3Qdrant()
                    ctx, guard = self.isolated_guard(client)

                    def change(name, c, ctx=ctx, service=service, field=field, value=value):
                        if name == TB2:
                            ctx.identity_value[service][field] = value
                    with self.assertRaisesRegex(wrap.IsolationError, "컨테이너 식별"):
                        self.run_aligned(guard, client=client, after_post=change)
                    self.assertEqual(self.writes(), [f"upload:{TB2}"])        # B3 업로드·이후 쓰기 0
                    self.assertEqual((sorted(client.collections), client.writes, client.alias_ops), ([TB2], [], []))

    # T-B7: Qdrant 상태 불일치 (본체 자체 검사가 못 보는 변경을 guard가 잡는다)
    def test_b7_qdrant_state_mismatch_is_refused_at_each_stage(self):
        def vector_change(target):
            def change(name, c):
                if name == target:
                    next(iter(c.collections[target].values()))["vector"] = sample_vector(777)
            return change
        # 1) B2 업로드 뒤 B2 벡터 변경 → B3 업로드 직전 거부 (Point 수는 같아 본체 검사는 통과)
        client = R3Qdrant()
        _, guard = self.isolated_guard(client)
        with self.assertRaisesRegex(wrap.IsolationError, r"vector_digests.*before_qdrant_upload:b3"):
            self.run_aligned(guard, client=client, after_post=vector_change(TB2))
        self.assertEqual(self.writes(), [f"upload:{TB2}"])
        # 2) B3 업로드 뒤 B3 벡터 변경 → B2 정렬 직전 거부 (본체는 B3를 아직 검증하지 않은 시점)
        self.events.clear()
        client = R3Qdrant()
        _, guard = self.isolated_guard(client)
        with self.assertRaisesRegex(wrap.IsolationError, r"vector_digests.*before_payload_alignment:b2"):
            self.run_aligned(guard, client=client, after_post=vector_change(TB3))
        self.assertEqual((self.writes(), client.writes), ([f"upload:{TB2}", f"upload:{TB3}"], []))
        # 3) B3 정렬 중 B2 보호 필드 변경 → Neo4j 적재 직전 거부 (본체는 B2를 다시 읽지 않음)
        self.events.clear()
        client = R3Qdrant()
        _, guard = self.isolated_guard(client)

        def touch_b2(c, args, kwargs):
            if args and args[0] == TB3:
                next(iter(c.collections[TB2].values()))["payload"]["region"] = "sample_only_region"
        self.on_set_payload = touch_b2
        self.addCleanup(lambda: setattr(self, "on_set_payload", None))
        with self.assertRaisesRegex(wrap.IsolationError, r"payload_digests.*protected_digests.*before_neo4j_load"):
            self.run_aligned(guard, client=client)
        self.on_set_payload = None
        self.assertNotIn("load_graph", self.writes())
        self.assertEqual(client.alias_ops, [])
        # 4) Neo4j 적재 중 B3 payload 변경 → alias 공개 직전 거부 (본체의 공개 직전 검사는 컬렉션·alias만 본다)
        self.events.clear()
        client = R3Qdrant()
        _, guard = self.isolated_guard(client)

        def touch_b3():
            next(iter(client.collections[TB3].values()))["payload"]["region"] = "sample_only_region"
        self.on_load_graph = touch_b3
        self.addCleanup(lambda: setattr(self, "on_load_graph", None))
        with self.assertRaisesRegex(wrap.IsolationError, r"payload_digests.*before_alias_publish"):
            self.run_aligned(guard, client=client)
        self.on_load_graph = None
        self.assertEqual((client.alias_ops, client.aliases), ([], {}))
        self.assertNotIn("alias_publish", self.writes())

    # T-B7: Neo4j 상태 불일치
    def test_b7_neo4j_state_mismatch_is_refused(self):
        # 1) B2 업로드 뒤 외부가 Neo4j에 노드를 만듦 → B3 업로드 직전 거부
        client = R3Qdrant()
        ctx, guard = self.isolated_guard(client)

        def external_nodes(name, c):
            if name == TB2:
                ctx.neo4j_override = {"nodes": 5, "relationships": 0}
        with self.assertRaisesRegex(wrap.IsolationError, r"neo4j_totals.*before_qdrant_upload:b3"):
            self.run_aligned(guard, client=client, after_post=external_nodes)
        self.assertEqual(self.writes(), [f"upload:{TB2}"])
        # 2) 적재 뒤 관계 수가 기대(12)와 다름 → alias 공개 직전 거부
        self.events.clear()
        client = R3Qdrant()
        ctx, guard = self.isolated_guard(client)
        self.on_load_graph = lambda: setattr(ctx, "neo4j_override", {"nodes": 10, "relationships": 11})
        self.addCleanup(lambda: setattr(self, "on_load_graph", None))
        with self.assertRaisesRegex(wrap.IsolationError, r"neo4j_totals.*before_alias_publish"):
            self.run_aligned(guard, client=client)
        self.on_load_graph = None
        self.assertEqual((client.alias_ops, client.aliases), ([], {}))

    # 요구 11·12: armed·run_id 변경
    def test_armed_change_during_run_is_refused_before_the_next_write(self):
        for label, value in (("false", False), ("truthy_int", 1), ("none", None)):
            for guard_type in ("test", "isolated"):
                with self.subTest(armed=label, guard=guard_type):
                    self.events.clear()
                    client = R3Qdrant()
                    guard = ArmedTestGuard(events=self.events) if guard_type == "test" else \
                        self.isolated_guard(client)[1]

                    def disarm(name, c, guard=guard, value=value):
                        if name == TB2:
                            guard.armed = value
                    with self.assertRaisesRegex(ValueError, "not armed"):
                        self.run_aligned(guard, client=client, after_post=disarm)
                    self.assertEqual(self.writes(), [f"upload:{TB2}"])

    def test_run_id_change_during_run_is_refused_before_the_next_write(self):
        for label, value, message in (("other_valid", "f" * 32, "run_id changed"),
                                      ("invalid", "F" * 32, "run_id must be 32 lowercase hex"),
                                      ("trailing_lf", "d" * 32 + "\n", "run_id must be 32 lowercase hex")):
            for guard_type in ("test", "isolated"):
                with self.subTest(run_id=label, guard=guard_type):
                    self.events.clear()
                    client = R3Qdrant()
                    guard = ArmedTestGuard("d" * 32, events=self.events) if guard_type == "test" else \
                        self.isolated_guard(client)[1]

                    def change(name, c, guard=guard, value=value):
                        if name == TB2:
                            guard.run_id = value
                    with self.assertRaisesRegex(ValueError, message):
                        self.run_aligned(guard, client=client, after_post=change)
                    self.assertEqual(self.writes(), [f"upload:{TB2}"])

    # 요구 13: lock_checker 없음·False·예외 (실제 guard + 본체)
    def test_lock_checker_missing_false_or_raising_is_refused(self):
        def boom():
            raise OSError("sample_only lock lost")
        for label, checker in (("missing", None), ("false", lambda: False), ("raises", boom),
                               ("truthy_not_true", lambda: 1), ("none_result", lambda: None)):
            with self.subTest(lock_checker=label):
                self.events.clear()
                client = R3Qdrant()
                ctx, guard = self.isolated_guard(client, lock_checker=checker)
                with self.assertRaisesRegex(wrap.IsolationError, "잠금"):
                    self.run_aligned(guard, client=client)
                self.assertEqual((self.writes(), client.collections, client.alias_ops), ([], {}, []))
                self.assertEqual(ctx.calls, [])                              # 잠금 거부는 서버 조회보다 먼저
        # 처음엔 보유, B2 업로드 뒤 잃음 → B3 업로드 직전 거부
        self.events.clear()
        held = iter([True])
        client = R3Qdrant()
        _, guard = self.isolated_guard(client, lock_checker=lambda: next(held, False))
        with self.assertRaisesRegex(wrap.IsolationError, "잠금"):
            self.run_aligned(guard, client=client)
        self.assertEqual(self.writes(), [f"upload:{TB2}"])

    # P1-B 보완: 정렬 전 번들 기준 검증 (기준 = 릴리스 기대 payload + 번들 payload_digest, 서버에서 읽은 값 아님)
    def set_payload_targets(self, client):
        return [points for op, points in client.writes if op == "set_payload"]

    def test_unexpected_field_or_index_run_id_after_upload_is_refused_before_any_alignment_write(self):
        """업로드 뒤·정렬 전에 생긴 정렬 허용 필드 밖 차이: 정렬 쓰기(set_payload) 0회, Neo4j 적재·공개 0회.
        (보완 전에는 같은 컬렉션에 정렬할 category 차이가 있으면 set_payload가 먼저 1회 실행됐다)"""
        def extra_field(payload):
            payload["sample_only_unexpected"] = "x"

        def other_run_id(payload):
            payload["index_run_id"] = "RUN_SAMPLE_ONLY_OTHER_AFTER_UPLOAD"
        cases = {"extra_field": (extra_field, "Final payload differs from the release-derived payload: {key} "
                                              r"\(1 points, .*sample_only_unexpected"),
                 "index_run_id": (other_run_id, "Full payload digest differs from the bundle payload_digest: {key}$")}
        for target, key, written in ((TB2, "b2", [f"upload:{TB2}", f"upload:{TB3}"]),
                                     (TB3, "b3", [f"upload:{TB2}", f"upload:{TB3}", "set_payload"])):
            for name, (change, message) in cases.items():
                with self.subTest(collection=key, change=name):
                    self.events.clear()
                    client = self.instrument(R3Qdrant())
                    guard = ArmedTestGuard(events=self.events)

                    def tamper(uploaded, c, target=target, change=change):
                        if uploaded == target:              # 정렬할 category 차이(Point 첫 번째)와 같은 컬렉션
                            change(list(c.collections[target].values())[-1]["payload"])
                    with self.assertRaisesRegex(ValueError, message.format(key=key)):
                        self.run_aligned(guard, client=client, after_post=tamper)
                    self.assertEqual(self.writes(), written)                       # 이 컬렉션 정렬 쓰기 0회
                    self.assertEqual(len(self.set_payload_targets(client)), 0 if key == "b2" else 1)
                    self.assertNotIn(f"before_payload_alignment:{key}", guard.calls)
                    self.assertNotIn("load_graph", self.writes())
                    self.assertNotIn("alias_publish", self.writes())

    def test_allowed_category_and_life_stages_difference_is_aligned_with_the_real_guard(self):
        def both_fields(name, c):
            payload = list(c.collections[name].values())[-1]["payload"]
            payload["category"], payload["life_stages"] = "sample_only_old", ["sample_only_old_stage"]
        client = R3Qdrant()
        _, guard = self.isolated_guard(client)
        client, result = self.run_aligned(guard, client=client, after_post=both_fields)
        self.assertEqual(result["status"], "PASS")
        pkg = self.package()
        for key in profiles.COLLECTION_KEYS:
            with self.subTest(key=key):
                self.assertEqual(result["collections"][key]["metadata_points_updated"], 2)   # 첫 Point category + 끝 Point
                self.assertEqual(result["collections"][key]["metadata_field_updates"],
                                 {"category": 2, "life_stages": 1})
                self.assertEqual(result["collections"][key]["payload_digest"], pkg[key]["payload_digest"])
        self.assertEqual(len(self.set_payload_targets(client)), 4)
        self.assertEqual(result["aliases"], {B2: TB2, B3: TB3})

    def tamper_at_lock_call(self, call_number, change):
        """실제 guard의 n번째 check 안(잠금 확인 시점 = 본체 scan 뒤, guard 서버 조회 전)에 외부 변경을 흉내 낸다.
        정렬 포함 복원의 check 순서: 1 B2 업로드, 2 B3 업로드, 3 B2 정렬, 4 B3 정렬, 5 Neo4j 적재, 6 alias 공개."""
        calls = []

        def checker():
            calls.append(1)
            if len(calls) == call_number:
                change()
            return True
        return checker

    def test_real_guard_refuses_before_payload_alignment_b2_and_b3(self):
        def extra_field(payload):
            payload["sample_only_unexpected"] = "x"

        def other_run_id(payload):
            payload["index_run_id"] = "RUN_SAMPLE_ONLY_OTHER_AT_GUARD"

        def wrong_category(payload):                 # 정렬 계획에 없는 Point의 category → 보호 digest로는 못 잡는다
            payload["category"] = "sample_only_tampered"
        cases = {"extra_field": (extra_field, r"'protected_digests', 'projected_payload_digests'"),
                 "index_run_id": (other_run_id, r"'protected_digests', 'projected_payload_digests'"),
                 "unplanned_category": (wrong_category, r"\['projected_payload_digests'\]")}
        for target, key, call_number, written in ((TB2, "b2", 3, [f"upload:{TB2}", f"upload:{TB3}"]),
                                                  (TB3, "b3", 4, [f"upload:{TB2}", f"upload:{TB3}", "set_payload"])):
            for name, (change, mismatch) in cases.items():
                with self.subTest(stage=f"before_payload_alignment:{key}", change=name):
                    self.events.clear()
                    client = self.instrument(R3Qdrant())
                    checker = self.tamper_at_lock_call(
                        call_number,
                        lambda client=client, target=target, change=change:
                            change(list(client.collections[target].values())[-1]["payload"]))
                    _, guard = self.isolated_guard(client, lock_checker=checker)
                    with self.assertRaisesRegex(wrap.IsolationError,
                                                f"{mismatch}.*before_payload_alignment:{key}"):
                        self.run_aligned(guard, client=client)
                    self.assertEqual(self.writes(), written)            # 이 정렬 쓰기·Neo4j 적재·공개 0회
                    self.assertEqual(len(self.set_payload_targets(client)), 0 if key == "b2" else 1)
                    self.assertEqual((client.alias_ops, client.aliases), ([], {}))

    def test_lock_lost_right_before_alias_publish_is_refused(self):
        calls = []

        def checker():
            calls.append(1)
            return len(calls) < 6                    # 6번째 check = alias 공개 직전에 잠금 상실
        client = R3Qdrant()
        _, guard = self.isolated_guard(client, lock_checker=checker)
        with self.assertRaisesRegex(wrap.IsolationError, r"잠금.*before_alias_publish"):
            self.run_aligned(guard, client=client)
        self.assertEqual(len(calls), 6)
        self.assertEqual(self.writes()[-1], "load_graph")                     # 적재까지는 진행, 공개는 0회
        self.assertEqual((client.alias_ops, client.aliases), ([], {}))

    def test_real_run_lock_release_mid_run_stops_the_next_write(self):
        """P1-C T-C8: 실제 RunLock.is_held를 guard에 연결. 보유 중엔 진행, B2 업로드 뒤 해제되면 B3 업로드 직전 거부."""
        lock = wrap.RunLock(Path(self.tmp.name) / "locks" / "run.lock", {"run_id": "d" * 32})
        self.addCleanup(lock.release)
        lock.acquire()
        client = R3Qdrant()
        _, guard = self.isolated_guard(client, lock_checker=lock.is_held)

        def release_after_b2(name, c):
            if name == TB2:
                lock.release()
        with self.assertRaisesRegex(wrap.IsolationError, r"실행 잠금을 보유하고 있지 않습니다.*before_qdrant_upload:b3"):
            self.run_aligned(guard, client=client, after_post=release_after_b2)
        self.assertEqual(self.writes(), [f"upload:{TB2}"])                   # B3 업로드·정렬·적재·공개 0회
        self.assertEqual((sorted(client.collections), client.writes, client.alias_ops), ([TB2], [], []))

    # 요구 14: preflight·verify-only는 guard를 부르지 않고 그대로 동작
    def test_preflight_and_verify_only_never_call_the_guard(self):
        guard = ArmedTestGuard(fail_at="before", events=self.events)
        client = R3Qdrant()
        self.assertEqual(self.run_restore(client, write_guard=guard)["status"], "PREFLIGHT_PASS_NO_WRITES")
        published = self.final_client()
        self.assertEqual(self.run_restore(published, verify_only=True, write_guard=guard)["status"], "PASS")
        self.assertEqual((guard.calls, self.events), ([], []))
        self.assertEqual((client.writes, published.writes, published.alias_ops), ([], [], []))


class FailureStateTests(unittest.TestCase):
    """#111 P1-D (T-D1~D4): 실제 본체 restore를 fake DB에서 실패시키고, 래퍼의 inspect_failure_state가 남은 상태를
    읽기 전용으로 재조회·분류하는지 본다. 부분 공개(alias)와 부분 적재(컬렉션·Neo4j)를 따로 본다."""
    setUp = RestoreTests.setUp
    run_restore = RestoreTests.run_restore

    def fail_restore(self, client, error_type=ValueError, message="", **kwargs):
        self.update_calls = 0
        original = client.update_collection_aliases

        def counted(**kw):
            self.update_calls += 1
            return original(**kw)
        client.update_collection_aliases = counted
        with self.assertRaisesRegex(error_type, message) as caught:
            self.run_restore(client, apply=True, **kwargs)
        error = caught.exception
        progress = error.restore_progress
        report = wrap.inspect_failure_state(StubContext(client, self), "d" * 32, progress,
                                            f"restore:{progress['stage']}")
        return error, report

    def assert_untouched_after_failure(self, client, before):
        """자동 롤백·삭제·재시도 없음: 컬렉션·alias가 실패 시점 그대로, 공개 요청은 1회 이하."""
        self.assertEqual((sorted(client.collections), dict(client.aliases)), before)
        self.assertLessEqual(self.update_calls, 1)
        self.assertFalse([op for op, *_ in client.writes if op not in ("set_payload",)])

    def test_d1_only_b2_published_when_b3_public_collection_appears(self):
        client = R3Qdrant()
        client.alias_hook = lambda c: c.collections.__setitem__(B3, {})     # 공개 요청 처리 직전 외부 정식 이름 컬렉션
        error, report = self.fail_restore(client, r3.PublishError, "Alias publish failed")
        self.assertIs(error.partial_publish, True)
        self.assertEqual((report["partial_publish"], report["publish_state"], report["published_aliases"]),
                         (True, "partial", [B2]))
        self.assertEqual(report["alias_classification"], {B2: {"target": TB2, "class": "ours"},
                                                          B3: {"target": None, "class": "absent"}})
        self.assertEqual(report["collection_classification"],
                         {"collections": {B3: "canonical_name", TB2: "ours_temp", TB3: "ours_temp"}, "ours_absent": []})
        self.assertEqual((report["failed_stage"], report["run_id"], report["physical_collections"]),
                         ("restore:alias_publish", "d" * 32, {"b2": TB2, "b3": TB3}))
        self.assertEqual((report["neo4j_totals"], report["neo4j_data_present"]),
                         ({"nodes": 10, "relationships": 12}, True))
        self.assertEqual(report["load_progress"], {"uploads_completed": ["b2", "b3"], "payload_alignment_writes": {},
                                                   "neo4j_load_returned": True})
        self.assertEqual((report["aliases"], report["unavailable"]), ({B2: TB2}, {}))
        self.assert_untouched_after_failure(client, (sorted([B3, TB2, TB3]), {B2: TB2}))

    def test_d1_only_b3_published_after_external_b2_alias_removal(self):
        client = R3Qdrant()
        original = client.update_collection_aliases

        def publish_then_lose_b2(**kw):
            result = original(**kw)
            client.aliases.pop(B2)                                         # 공개 직후 외부가 B2 alias 삭제
            return result
        client.update_collection_aliases = publish_then_lose_b2
        _, report = self.fail_restore(client, r3.PublishError, "Published alias/collection state mismatch")
        self.assertEqual((report["partial_publish"], report["publish_state"], report["published_aliases"]),
                         (True, "partial", [B3]))
        self.assertEqual(report["alias_classification"][B2]["class"], "absent")
        self.assertEqual(report["alias_classification"][B3], {"target": TB3, "class": "ours"})
        self.assertEqual(report["failed_stage"], "restore:published_alias_verification")
        self.assert_untouched_after_failure(client, (sorted([TB2, TB3]), {B3: TB3}))

    def test_d4_alias_pointing_to_an_unexpected_collection(self):
        for label, foreign in (("one_foreign", (B2,)), ("both_foreign", (B2, B3))):
            with self.subTest(case=label):
                self.events.clear()
                client = R3Qdrant()
                original = client.update_collection_aliases

                def publish_then_redirect(foreign=foreign, client=client, original=original, **kw):
                    result = original(**kw)
                    client.collections[OTHER] = {}
                    for name in foreign:
                        client.aliases[name] = OTHER                         # 외부가 정식 alias를 다른 컬렉션으로
                    client.aliases["sample_only_extra_alias"] = TB2          # 정식 이름이 아닌 외부 alias
                    return result
                client.update_collection_aliases = publish_then_redirect
                _, report = self.fail_restore(client, r3.PublishError, "state mismatch")
                for name in foreign:
                    self.assertEqual(report["alias_classification"][name], {"target": OTHER, "class": "foreign_target"})
                self.assertEqual(report["alias_classification"]["sample_only_extra_alias"],
                                 {"target": TB2, "class": "unexpected"})
                self.assertEqual(report["collection_classification"]["collections"][OTHER], "unexpected")
                expected = (True, "partial") if label == "one_foreign" else (False, "none")
                self.assertEqual((report["partial_publish"], report["publish_state"]), expected)
                self.assertEqual(client.aliases[foreign[0]], OTHER)          # 외부 매핑을 되돌리지 않음

    def test_d2_alias_state_unreadable_is_unknown_not_empty(self):
        client = R3Qdrant()
        bad = {TB3: {"status": "MISMATCH", "failures": [{"x": 1}], "qdrant_points": 6, "qdrant_count": 6}}
        with self.assertRaisesRegex(ValueError, "Cross-DB validation failed") as caught:
            self.run_restore(client, apply=True, cross=bad)
        client.get_aliases = lambda: (_ for _ in ()).throw(RuntimeError(SETTINGS["QDRANT_API_KEY"]))
        progress = caught.exception.restore_progress
        report = wrap.inspect_failure_state(StubContext(client, self), "d" * 32, progress,
                                            f"restore:{progress['stage']}")
        self.assertEqual((report["partial_publish"], report["publish_state"], report["published_aliases"],
                          report["aliases"], report["alias_classification"]), (None, "unknown", None, None, None))
        self.assertEqual(report["unavailable"], {"qdrant_aliases": "RuntimeError"})
        self.assertEqual(sorted(report["collection_classification"]["collections"]), [TB2, TB3])   # 다른 항목은 계속
        self.assertNotIn(SETTINGS["QDRANT_API_KEY"], json.dumps(report))                          # 예외 메시지 미기록

    def test_d2_publish_failure_with_unreadable_aliases_reports_unknown(self):
        client = R3Qdrant()
        client.alias_hook = lambda c: setattr(c, "get_aliases", lambda: (_ for _ in ()).throw(OSError("x")))

        def refuse(**kw):
            client.alias_hook(client)
            raise RuntimeError("sample_only publish refused")
        client.update_collection_aliases = refuse
        with self.assertRaises(r3.PublishError) as caught:
            self.run_restore(client, apply=True)
        self.assertIsNone(caught.exception.partial_publish)                  # 모름 → None (False로 쓰지 않음)
        self.assertIsNone(caught.exception.aliases)
        self.assertIn("alias state could not be read (OSError)", str(caught.exception))

    def test_d_loaded_before_publish_is_not_partial_publish(self):
        client = R3Qdrant()
        bad = {TB3: {"status": "MISMATCH", "failures": [{"x": 1}], "qdrant_points": 6, "qdrant_count": 6}}
        _, report = self.fail_restore(client, ValueError, "Cross-DB validation failed", cross=bad)
        self.assertEqual((report["partial_publish"], report["publish_state"], report["aliases"]), (False, "none", {}))
        self.assertEqual(report["alias_classification"], {B2: {"target": None, "class": "absent"},
                                                          B3: {"target": None, "class": "absent"}})
        self.assertEqual((report["neo4j_totals"], report["neo4j_data_present"]),     # 적재는 됨 (부분 적재 ≠ 부분 공개)
                         ({"nodes": 10, "relationships": 12}, True))
        self.assertEqual(report["failed_stage"], "restore:cross_db_verification")
        self.assertEqual(report["load_progress"]["neo4j_load_returned"], True)
        self.assertEqual(self.update_calls, 0)                                       # 공개 요청 0회
        self.assert_untouched_after_failure(client, (sorted([TB2, TB3]), {}))

    def test_d_upload_failure_reports_partial_load_only(self):
        client = R3Qdrant()
        _, report = self.fail_restore(client, ValueError, "Snapshot upload failed", fail_upload=TB3)
        self.assertEqual(report["failed_stage"], "restore:qdrant_upload:b3")
        self.assertEqual(report["collection_classification"], {"collections": {TB2: "ours_temp"}, "ours_absent": [TB3]})
        self.assertEqual((report["neo4j_totals"], report["neo4j_data_present"], report["partial_publish"]),
                         ({"nodes": 0, "relationships": 0}, False, False))
        self.assertEqual(report["load_progress"]["uploads_completed"], ["b2"])
        self.assert_untouched_after_failure(client, ([TB2], {}))

    def test_d_failure_before_any_write_has_no_physical_names(self):
        client = R3Qdrant({OTHER: {}})                                     # 빈 DB가 아니어서 쓰기 전에 거부
        _, report = self.fail_restore(client, ValueError, "requires empty")
        self.assertIsNone(report["physical_collections"])
        self.assertEqual(report["collection_classification"]["collections"], {OTHER: "unexpected"})
        self.assertEqual(report["failed_stage"], "restore:pre_write_checks")


INDEX_FIELDS = store.PAYLOAD_INDEX_FIELDS            # 검증 기준은 store의 상수 그대로 (새로 정의하지 않음)


class PayloadIndexTests(unittest.TestCase):
    """#111 P1-E (T-E1~E14): 필수 payload 인덱스 8개(keyword)를 공개 직전(임시 컬렉션)·공개 직후·verify-only(정식 alias)
    에서 검증. 실패하면 PASS 아님, 공개 직전 실패는 공개 0회, 공개 뒤 실패는 alias 유지, 인덱스 생성·삭제 0회."""
    setUp = RestoreTests.setUp
    run_restore = RestoreTests.run_restore
    final_client = RestoreTests.final_client

    def instrument(self, client):
        """get_collection 읽기·공개 요청을 events에 기록하고 공개 요청 횟수를 센다."""
        self.update_calls = 0
        original_get, original_update = client.get_collection, client.update_collection_aliases

        def get_collection(name):
            self.events.append(("read", f"get_collection:{name}"))
            return original_get(name)

        def update(**kw):
            self.update_calls += 1
            self.events.append(("write", "alias_publish"))
            return original_update(**kw)
        client.get_collection, client.update_collection_aliases = get_collection, update
        return client

    def assert_no_index_writes(self, *clients):
        for client in clients:
            self.assertFalse([w for w in client.writes if w[0] in ("create_payload_index", "delete_payload_index")])

    def expected_entries(self, name, actual="keyword", status="ok", exists=True):
        return [{"collection": name, "field": field, "expected_type": "keyword", "actual_type": actual,
                 "exists": exists, "verification_status": status} for field in INDEX_FIELDS]

    # T-E1·E9·E11·E12: 정상 → 공개 직전(임시)·공개 직후(정식 alias) 각 8×2 PASS, 필드별 형식
    def test_e1_e9_e11_valid_indexes_pass_before_and_after_publish(self):
        client = self.instrument(R3Qdrant())
        guard = ArmedTestGuard(events=self.events)
        result = self.run_restore(client, apply=True, write_guard=guard)
        self.assertEqual(result["status"], "PASS")
        indexes = result["payload_indexes"]
        self.assertEqual(list(indexes), ["before_alias_publish", "after_alias_publish"])
        for stage, names in (("before_alias_publish", {"b2": TB2, "b3": TB3}),
                             ("after_alias_publish", {"b2": B2, "b3": B3})):
            for key, name in names.items():
                with self.subTest(stage=stage, key=key):
                    report = indexes[stage][key]
                    self.assertEqual((report["status"], report["collection"], report["stage"], report["missing_fields"],
                                      report["invalid_types"], report["extra_fields"]),
                                     ("PASS", name, stage, [], {}, []))
                    self.assertEqual(report["indexes"], self.expected_entries(name))       # T-E11 형식
        self.assert_no_index_writes(client)                                                  # T-E12

    def test_e7_index_check_runs_before_the_publish_guard_and_write(self):
        client = self.instrument(R3Qdrant())
        self.run_restore(client, apply=True, write_guard=ArmedTestGuard(events=self.events))
        tail = self.events[-6:]
        self.assertEqual(tail, [("read", f"get_collection:{TB2}"), ("read", f"get_collection:{TB3}"),
                                ("guard", "before_alias_publish"), ("write", "alias_publish"),
                                ("read", f"get_collection:{B2}"), ("read", f"get_collection:{B3}")])

    def fail_before_publish(self, schemas):
        client = self.instrument(R3Qdrant())
        client.payload_schemas.update(schemas)
        guard = ArmedTestGuard(events=self.events)
        with self.assertRaises(r3.PayloadIndexError) as caught:
            self.run_restore(client, apply=True, write_guard=guard)
        # T-E7: 공개 0회, 공개 guard도 부르지 않음, alias 없음, 인덱스 생성·삭제 0
        self.assertEqual((self.update_calls, client.alias_ops, client.aliases), (0, [], {}))
        self.assertNotIn("before_alias_publish", guard.calls)
        self.assertIn(("write", "load_graph"), self.events)                       # 적재까지 진행 → 부분 적재 상태
        self.assertEqual(caught.exception.restore_progress["stage"], "alias_publish")
        self.assert_no_index_writes(client)
        return caught.exception.report

    def test_e2_each_missing_field_fails_before_publish(self):
        for field in INDEX_FIELDS:
            with self.subTest(field=field):
                self.events.clear()
                others = [f for f in INDEX_FIELDS if f != field]
                report = self.fail_before_publish({TB2: keyword_payload_schema(6, fields=others)})
                b2 = report["collections"]["b2"]
                self.assertEqual((report["stage"], report["failed"], b2["status"], b2["missing_fields"],
                                  b2["invalid_types"]), ("before_alias_publish", ["b2"], "FAILED", [field], {}))
                entry = next(e for e in b2["indexes"] if e["field"] == field)
                self.assertEqual(entry, {"collection": TB2, "field": field, "expected_type": "keyword",
                                         "actual_type": None, "exists": False, "verification_status": "missing"})

    def test_e3_each_wrong_type_fails_before_publish(self):
        from qdrant_client.http import models
        for field in INDEX_FIELDS:
            with self.subTest(field=field):
                self.events.clear()
                schema = keyword_payload_schema(6)
                schema[field] = models.PayloadIndexInfo(data_type=models.PayloadSchemaType.TEXT, points=6)
                report = self.fail_before_publish({TB3: schema})
                b3 = report["collections"]["b3"]
                self.assertEqual((report["failed"], b3["missing_fields"], b3["invalid_types"]), (["b3"], [], {field: "text"}))
                entry = next(e for e in b3["indexes"] if e["field"] == field)
                self.assertEqual((entry["actual_type"], entry["exists"], entry["verification_status"]),
                                 ("text", True, "invalid_type"))

    def test_e4_e5_only_one_collection_failing_is_reported_alone(self):
        for key, name in (("b2", TB2), ("b3", TB3)):
            with self.subTest(failing=key):
                self.events.clear()
                report = self.fail_before_publish({name: keyword_payload_schema(6, fields=INDEX_FIELDS[1:])})
                other = "b3" if key == "b2" else "b2"
                self.assertEqual(report["failed"], [key])
                self.assertEqual((report["collections"][key]["status"], report["collections"][other]["status"]),
                                 ("FAILED", "PASS"))

    def test_e6_missing_none_empty_unreadable_or_unrecognized_schema_never_passes(self):
        calls = []

        def unreadable(name):          # 첫 조회(본체의 차원·Point 수 검증)는 정상, 인덱스 확인 조회에서 실패
            calls.append(name)
            if len(calls) == 1:
                return keyword_payload_schema(6)
            raise RuntimeError(f"sample_only {SETTINGS['QDRANT_API_KEY']}")
        cases = {
            "none": (None, "UNAVAILABLE", "payload_schema:NoneType"),
            "empty_dict": ({}, "FAILED", None),
            "no_attribute": (MISSING_SCHEMA, "UNAVAILABLE", "payload_schema:NoneType"),
            "lookup_error": (unreadable, "UNAVAILABLE", "RuntimeError"),
            "not_a_dict": ([("policy_id", "keyword")], "UNAVAILABLE", "payload_schema:list"),
            "unrecognized_values": ({field: 42 for field in INDEX_FIELDS}, "FAILED", None),
        }
        for label, (schema, status, error_type) in cases.items():
            with self.subTest(schema=label):
                self.events.clear()
                report = self.fail_before_publish({TB2: schema})
                b2 = report["collections"]["b2"]
                self.assertEqual(b2["status"], status)
                self.assertEqual(b2.get("error_type"), error_type)
                if label == "empty_dict":
                    self.assertEqual(b2["missing_fields"], list(INDEX_FIELDS))
                if label == "unrecognized_values":
                    self.assertEqual(b2["invalid_types"], {field: "unrecognized:int" for field in INDEX_FIELDS})
                self.assertFalse(SETTINGS["QDRANT_API_KEY"] in json.dumps(report))

    def test_e8_failure_right_after_publish_keeps_the_aliases(self):
        client = self.instrument(R3Qdrant())
        publish = client.update_collection_aliases

        def publish_then_lose_index(**kw):
            result = publish(**kw)
            client.payload_schemas[TB3] = keyword_payload_schema(6, fields=[f for f in INDEX_FIELDS if f != "region"])
            return result
        client.update_collection_aliases = publish_then_lose_index
        with self.assertRaises(r3.PayloadIndexError) as caught:
            self.run_restore(client, apply=True)
        error = caught.exception
        self.assertEqual((error.report["stage"], error.report["failed"]), ("after_alias_publish", ["b3"]))
        self.assertEqual((error.report["collections"]["b3"]["collection"],
                          error.report["collections"]["b3"]["missing_fields"]), (B3, ["region"]))
        self.assertEqual(error.restore_progress["stage"], "published_payload_index_verification")
        self.assertEqual((client.aliases, self.update_calls), ({B2: TB2, B3: TB3}, 1))    # alias 유지, 추가 요청 0
        self.assertEqual(sorted(client.collections), sorted([TB2, TB3]))
        report = wrap.inspect_failure_state(StubContext(client, self), "d" * 32, error.restore_progress,
                                            "restore:published_payload_index_verification")
        self.assertEqual((report["publish_state"], report["partial_publish"]), ("complete", False))   # P1-D 유지
        self.assert_no_index_writes(client)

    def test_e10_verify_only_fails_without_writes_and_passes_when_valid(self):
        from qdrant_client.http import models
        ok = self.final_client()
        result = self.run_restore(ok, verify_only=True)
        self.assertEqual(list(result["payload_indexes"]), ["verify_only"])
        self.assertEqual({k: (v["status"], v["collection"]) for k, v in result["payload_indexes"]["verify_only"].items()},
                         {"b2": ("PASS", B2), "b3": ("PASS", B3)})
        for label, schema in (("missing", keyword_payload_schema(6, fields=INDEX_FIELDS[:-1])),
                              ("wrong_type", keyword_payload_schema(6, data_type=models.PayloadSchemaType.INTEGER))):
            with self.subTest(case=label):
                client = self.final_client()
                client.payload_schemas[TB2] = schema
                with self.assertRaises(r3.PayloadIndexError) as caught:
                    self.run_restore(client, verify_only=True)
                self.assertEqual((caught.exception.report["stage"], caught.exception.report["failed"]),
                                 ("verify_only", ["b2"]))
                self.assertEqual((client.writes, client.alias_ops), ([], []))               # 쓰기 0
        self.assert_no_index_writes(ok)

    def test_extra_indexes_are_recorded_but_not_required_or_rejected(self):
        client = R3Qdrant()
        schema = keyword_payload_schema(6)
        schema.update(keyword_payload_schema(6, fields=["sample_only_extra"]))
        client.payload_schemas[TB2] = schema
        result = self.run_restore(client, apply=True)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["payload_indexes"]["before_alias_publish"]["b2"]["extra_fields"], ["sample_only_extra"])

    def test_e13_index_type_normalization(self):
        from qdrant_client.http import models
        keyword_forms = {"enum": models.PayloadIndexInfo(data_type=models.PayloadSchemaType.KEYWORD, points=1),
                         "string_coerced_by_model": models.PayloadIndexInfo(data_type="keyword", points=1),
                         "rest_dict": {"data_type": "keyword", "points": 1},
                         "plain_object_with_string": SimpleNamespace(data_type="keyword"),
                         "with_keyword_params": models.PayloadIndexInfo.model_validate(
                             {"data_type": "keyword", "params": {"type": "keyword", "is_tenant": False}, "points": 1})}
        for label, value in keyword_forms.items():
            with self.subTest(form=label):
                self.assertEqual(r3.normalize_index_type(value), "keyword")
        self.assertEqual(r3.normalize_index_type(models.PayloadIndexInfo(data_type=models.PayloadSchemaType.TEXT,
                                                                         points=1)), "text")
        for label, value in {"upper_string": SimpleNamespace(data_type="KEYWORD"),
                             "enum_repr_string": SimpleNamespace(data_type="PayloadSchemaType.KEYWORD")}.items():
            with self.subTest(not_keyword=label):
                self.assertNotEqual(r3.normalize_index_type(value), "keyword")       # 추정 정규화 없음
        for label, value in {"int": 42, "none": None, "no_data_type": SimpleNamespace(points=1),
                             "data_type_int": {"data_type": 1}, "bare_string": "keyword"}.items():
            with self.subTest(unrecognized=label):
                self.assertIsNone(r3.normalize_index_type(value))

    def test_e14_installed_qdrant_client_payload_schema_contract(self):
        """설치된 qdrant-client의 실제 반환 모델 계약 (추측하지 않고 소스·모델로 확인)."""
        import importlib.metadata
        import typing
        from qdrant_client.http import models
        self.assertTrue(importlib.metadata.version("qdrant-client").startswith("1.12."))
        annotation = models.CollectionInfo.model_fields["payload_schema"].annotation
        self.assertEqual(typing.get_origin(annotation), dict)
        key_type, value_type = typing.get_args(annotation)
        self.assertIs(key_type, str)
        self.assertIs(value_type, models.PayloadIndexInfo)
        self.assertIs(models.PayloadIndexInfo.model_fields["data_type"].annotation, models.PayloadSchemaType)
        self.assertTrue(issubclass(models.PayloadSchemaType, str))
        self.assertEqual(models.PayloadSchemaType.KEYWORD.value, "keyword")
        parsed = models.PayloadIndexInfo.model_validate({"data_type": "keyword", "points": 1233})   # REST JSON 모양
        self.assertIs(parsed.data_type, models.PayloadSchemaType.KEYWORD)
        self.assertEqual(r3.normalize_index_type(parsed), "keyword")
        self.assertIsInstance(next(iter(keyword_payload_schema(1).values())), models.PayloadIndexInfo)  # fixture도 같은 모델


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
