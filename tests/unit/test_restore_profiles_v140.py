"""Issue #105 복원 기준값(scripts/restore_profiles_v140.py)과 r3 Compose 정적 검사.

- R2 표가 #91 r2 도구의 실제 상수와 같은지 (r2 기준을 한곳에서 읽어도 값이 갈라지지 않게)
- R3 기준값은 최신 dev(249정책) 기준 C0(pending) 상태이고 확정된 칸(manifest·건수·points)이 현재 릴리스와 같은지,
  그리고 1차·2차 확정·전부 미확정 상태의 게이트 동작이 fixture(stage1_pins·final_sample_pins·pending_pins)로 계속 검증되는지
- R3 원본(R3_SOURCES)이 r3b 최종 snapshot의 기록값(#110 C2 최종 digest)과 같은지
- r3 격리 포트·프로젝트·인증 변수가 공유·r1·r2 자산과 겹치지 않는지
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r3_fixtures import SAMPLE_PLAN_VALUES, final_sample_pins, pending_pins, stage1_pins  # noqa: E402

from database import full_index as fi  # noqa: E402
from scripts import build_restore_bundle_v140 as b2b  # noqa: E402
from scripts import load_neo4j_r2_isolated as neo4j_r2  # noqa: E402
from scripts import restore_final_v140 as restore  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402
from scripts import restore_r2_isolated as r2w  # noqa: E402
from scripts import seed_b2_from_r1_vectors as seed  # noqa: E402
from scripts import update_b2_from_snapshot as qdrant_r2  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


class R2ProfileTests(unittest.TestCase):
    def test_r2_profile_matches_the_frozen_r2_tools(self):
        r2 = profiles.R2_PROFILE
        b2 = r2["collections"]["b2"]
        self.assertEqual(r2["manifest_lf_sha256"], seed.MANIFEST_LF_SHA256)
        self.assertEqual(profiles.release_counts_tuple(r2["release_counts"]),
                         (*restore.EXPECTED[:3], *restore.EXPECTED[4:]))
        self.assertEqual((b2["collection"], b2["dimensions"], b2["points"]),
                         (restore.COLLECTION, restore.SNAPSHOT_DIMENSIONS, restore.SNAPSHOT_POINTS))
        self.assertEqual(b2["points"], qdrant_r2.FINAL_POINTS)
        self.assertEqual(b2["vector_digest"], neo4j_r2.QDRANT_R2_VECTOR_DIGEST)
        self.assertEqual(b2["snapshot_member"], b2b.SNAPSHOT_MEMBER)
        self.assertEqual(r2["schema_version"], 1)


class R3PinTests(unittest.TestCase):
    """단계별 검증: 전부 미확정(pending_pins) / 1차(stage1_pins) / 2차(final_sample_pins) + 실제 R3_PINS(C0, 249정책)."""

    PLAN_FIELDS = ("vector_digest", "reuse_count", "reuse_vector_digest", "embed_count", "delete_count", "plan_sha256")

    def assert_well_formed(self, pins):
        """확정된 칸은 형식·상호 일관성을 지켜야 한다 (미확정 칸은 None만 허용)."""
        if pins["manifest_lf_sha256"] is not None:
            self.assertRegex(pins["manifest_lf_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(set(pins["release_counts"]), set(profiles.RELEASE_COUNT_KEYS))
        self.assertTrue(all(v is None or (type(v) is int and v > 0) for v in pins["release_counts"].values()))
        for key in profiles.COLLECTION_KEYS:          # subTest를 쓰지 않는다 (호출자가 AssertionError를 직접 받아야 함)
            c = pins["collections"][key]
            self.assertEqual(c["remove_count"], 0, key)
            for field in ("vector_digest", "reuse_vector_digest", "plan_sha256"):
                if c[field] is not None:
                    self.assertRegex(c[field], r"^[0-9a-f]{64}$", f"{key}.{field}")
            if c["vector_digest"] is not None:
                self.assertNotEqual(c["vector_digest"], c["reuse_vector_digest"], key)   # 신규 임베딩이 더해진 최종 값
                self.assertNotEqual(c["vector_digest"], profiles.R3_SOURCES[key]["vector_digest"], key)
            if None not in (c["points"], c["reuse_count"], c["embed_count"], c["delete_count"]):
                self.assertEqual(c["reuse_count"] + c["embed_count"], c["points"], key)
                self.assertLessEqual(c["delete_count"], c["embed_count"], key)
                # 재사용 + 삭제 후 재임베딩 = 원본 snapshot Point 수 (remove 0)
                self.assertEqual(c["reuse_count"] + c["delete_count"], profiles.R3_SOURCES[key]["points"], key)
        finals = [pins["collections"][k]["vector_digest"] for k in profiles.COLLECTION_KEYS
                  if pins["collections"][k]["vector_digest"] is not None]
        self.assertEqual(len(set(finals)), len(finals))                            # 컬렉션마다 다른 값

    def test_real_r3_pins_are_c2_stage2_for_the_249_release(self):
        """C2(stage2): C1 계획 값에 더해 r3d build --index 뒤(state=complete) 최종 전체 vector_digest까지 확정. 미확정 0."""
        pins = profiles.R3_PINS
        self.assertEqual((profiles.R3_PIN_STAGE, profiles.pin_stage()), ("stage2", "stage2"))
        self.assertEqual(pins["manifest_lf_sha256"], "b31307223f7334ffb7588d55670318d07d36e3fc18a78f62e8c961fdad04f3da")
        self.assertEqual(pins["release_counts"], {"policies": 249, "documents": 273, "release_chunks": 672,
                                                  "neo4j_nodes": 1147, "neo4j_relationships": 2702,
                                                  "requires_condition": 211})
        self.assertEqual({k: pins["collections"][k]["points"] for k in profiles.COLLECTION_KEYS}, {"b2": 1249, "b3": 1338})
        self.assertEqual({k: pins["collections"][k]["remove_count"] for k in profiles.COLLECTION_KEYS}, {"b2": 0, "b3": 0})
        plan_values = {k: {f: pins["collections"][k][f] for f in ("reuse_count", "embed_count", "delete_count",
                                                                  "reuse_vector_digest", "plan_sha256")}
                       for k in profiles.COLLECTION_KEYS}
        self.assertEqual(plan_values, {
            "b2": {"reuse_count": 1229, "embed_count": 20, "delete_count": 4,
                   "reuse_vector_digest": "ec0a272725b360e73617d5b7c14aeac61f9350a9307a33af4b8e20d249306094",
                   "plan_sha256": "16dc4a3ee31d62416bd59d10fe3bf2b47a160d1dc4eef9ff2fb5580454a1b994"},
            "b3": {"reuse_count": 1316, "embed_count": 22, "delete_count": 4,
                   "reuse_vector_digest": "40e2eb411da501ea5411fd8df52093092e8445f1f648cd5a3b7cae70ac924de2",
                   "plan_sha256": "164f6b12cd8d4400a9665624e68eb672c36463b5cc522ca6174d7214382fffa8"}})
        for key in profiles.COLLECTION_KEYS:                       # reuse + embed = 목표 Point 수
            c = pins["collections"][key]
            self.assertEqual(c["reuse_count"] + c["embed_count"], c["points"])
        self.assertEqual({k: pins["collections"][k]["vector_digest"] for k in profiles.COLLECTION_KEYS}, {
            "b2": "7a39e5c9e6c6a7181c0827abcee0bf026160de3dd8dc7facd68b5df7861a1ab4",
            "b3": "d3ed4140122a9a8f815ee7eb01b0aad7c57119eb7a96c139179ed30427c5237b"})   # C2 최종 digest
        self.assertEqual(profiles.missing_pins(), [])
        self.assertTrue(profiles.is_finalized())
        self.assertIs(profiles.require_finalized(), profiles.R3_PINS)
        self.assert_well_formed(pins)

    def test_110_c2_final_values_are_kept_only_as_the_r3b_source_record(self):
        """#110 C2 최종 vector_digest·Point 수는 이제 R3_PINS가 아니라 r3d의 원본(r3b 최종 snapshot) 기록이다."""
        expected = {"b2": (1233, "90d0f59318c6a7fb0a084bd1d1242a793cfd6ae6a4d04fb3145933ff5e17689d"),
                    "b3": (1320, "718046c382cffb43afbc5dd1866ac9c84fc8b25dc293f4bd2d4ac7e32bbd7120")}
        for key, (points, digest) in expected.items():
            source = profiles.R3_SOURCES[key]
            self.assertEqual((source["points"], source["vector_digest"]), (points, digest), key)
            self.assertNotIn(digest, {profiles.R3_PINS["collections"][k]["vector_digest"] for k in profiles.COLLECTION_KEYS})
        self.assertNotEqual(profiles.R3_PINS["manifest_lf_sha256"],
                            "29b57236c36587233a90c8f159d2e98388bd96e427cccdac102462626f79a6b7")   # #110 릴리스가 아님

    def test_declared_stage_must_match_the_values(self):
        """단계 상수와 실제 값이 어긋나면(값만 바꾸고 단계를 잊음) 실패해야 한다."""
        self.assertIn(profiles.R3_PIN_STAGE, profiles.PIN_STAGES)
        self.assertEqual(profiles.pin_stage(), profiles.R3_PIN_STAGE)
        self.assertEqual([profiles.pin_stage(p) for p in (pending_pins(), stage1_pins(), final_sample_pins())],
                         ["pending", "stage1", "stage2"])

    def test_stage1_fixture_defers_only_final_vector_digests(self):
        stage1 = stage1_pins()
        self.assertEqual(sorted(profiles.missing_pins(stage1)), sorted(profiles.UPDATE_STAGE_DEFERRED_PINS))
        with self.assertRaisesRegex(profiles.ProfileError, "vector_digest"):
            profiles.require_finalized(stage1)
        self.assertIs(profiles.require_finalized(stage1, deferred=profiles.UPDATE_STAGE_DEFERRED_PINS), stage1)
        # 1차 확정 모양은 2차 모양과 vector_digest 두 칸만 다르고, 릴리스 값(manifest·건수·points)은 실제 R3_PINS와 같다
        final = final_sample_pins()
        for key in profiles.COLLECTION_KEYS:
            final["collections"][key]["vector_digest"] = None
        self.assertEqual(stage1, final)
        real = profiles.R3_PINS
        self.assertEqual((stage1["manifest_lf_sha256"], stage1["release_counts"]),
                         (real["manifest_lf_sha256"], real["release_counts"]))
        self.assertEqual({k: stage1["collections"][k]["points"] for k in profiles.COLLECTION_KEYS},
                         {k: real["collections"][k]["points"] for k in profiles.COLLECTION_KEYS})

    def test_pending_fixture_is_refused_even_for_the_update_stage(self):
        with self.assertRaisesRegex(profiles.ProfileError, "확정되지 않았습니다"):
            profiles.require_finalized(pending_pins(), deferred=profiles.UPDATE_STAGE_DEFERRED_PINS)
        self.assertIn("manifest_lf_sha256", profiles.missing_pins(pending_pins()))

    def test_final_shape_is_well_formed_and_internally_consistent(self):
        pins = final_sample_pins()
        self.assertTrue(profiles.is_finalized(pins))
        self.assert_well_formed(pins)
        for key in profiles.COLLECTION_KEYS:
            self.assertEqual({f: pins["collections"][key][f] for f in SAMPLE_PLAN_VALUES[key]}, SAMPLE_PLAN_VALUES[key])
        broken = final_sample_pins()
        broken["collections"]["b2"]["reuse_count"] += 1                          # 합계가 어긋나면 잡아낸다
        with self.assertRaises(AssertionError):
            self.assert_well_formed(broken)

    def test_final_pins_match_the_checked_out_release(self):
        """기준값의 manifest·건수·Point 수가 현재 체크아웃된 릴리스 파일에서 다시 계산한 값과 같다."""
        from database import handoff_data as hd
        from scripts import plan_r3_vector_reuse as planner
        if hd.dataset_version() != profiles.DATASET_VERSION:
            self.skipTest("NOT_RUN: v1.4.0-candidate 데이터셋이 아님")
        release = planner.load_release(profiles.r3_pins())             # 고정값과 다르면 PlanError
        self.assertEqual(release["manifest_lf_sha256"], profiles.R3_PINS["manifest_lf_sha256"])
        self.assertEqual(release["counts"], profiles.R3_PINS["release_counts"])
        for key in profiles.COLLECTION_KEYS:
            self.assertEqual(len(release["collections"][key]["chunks"]), profiles.R3_PINS["collections"][key]["points"])

    def test_finalized_pins_pass_and_schema_changes_are_detected(self):
        pins = profiles.r3_pins()
        pins["manifest_lf_sha256"] = "a" * 64
        pins["release_counts"] = {k: 1 for k in profiles.RELEASE_COUNT_KEYS}
        for key in profiles.COLLECTION_KEYS:
            pins["collections"][key] = {k: (v if v is not None else 1) for k, v in pins["collections"][key].items()}
        self.assertIs(profiles.require_finalized(pins), pins)
        del pins["collections"]["b3"]
        self.assertIn("<schema>", profiles.missing_pins(pins))
        self.assertFalse(profiles.is_finalized(pins))

    def test_r3_pins_copy_does_not_change_the_module(self):
        original = profiles.r3_pins()
        copied = profiles.r3_pins()
        copied["manifest_lf_sha256"] = "b" * 64
        copied["collections"]["b2"]["vector_digest"] = "c" * 64
        self.assertEqual(profiles.R3_PINS, original)
        self.assertEqual(profiles.pin_stage(), profiles.R3_PIN_STAGE)

    def test_r3_collections_match_full_index_configs(self):
        for key in profiles.COLLECTION_KEYS:
            spec = profiles.collection_spec(key)
            config = fi.FULL_INDEX_CONFIGS[spec["config"]]
            self.assertEqual(config["collection"], spec["collection"])
            self.assertEqual(config["strategy"], spec["strategy"])
            self.assertEqual(fi.config_model(config), (spec["model"], spec["dimensions"]))
        with self.assertRaises(profiles.ProfileError):
            profiles.collection_spec("b4")

    def test_r3_sources_are_the_existing_r3b_final_snapshots(self):
        """r3d 원본 = r3b 최종 snapshot (전체 SHA-256은 파일 재계산·snapshot record와 대조해 기록한 값)."""
        expected = {"b2": ("834f17b6a0c18c265573ebc3da59a29473d5fa0242001cca3d0c2749fa98ba89",
                           ".cache/issue105-r3b/bundle-r3b-20261123-01/b2-r3-v1.12.4.snapshot"),
                    "b3": ("91311f2d2f8a23369d302cee865b282c38b651b579aaca0d8f3bc95faf333ff8",
                           ".cache/issue105-r3b/bundle-r3b-20261123-01/b3-r3-v1.12.4.snapshot")}
        r2 = profiles.R2_PROFILE["collections"]["b2"]
        for key, (sha, path) in expected.items():
            source = profiles.R3_SOURCES[key]
            self.assertEqual((source["snapshot_sha256"], source["default_path"]), (sha, path), key)
            self.assertRegex(source["vector_digest"], r"^[0-9a-f]{64}$")
            self.assertNotEqual(source["snapshot_sha256"], r2["snapshot_sha256"], key)     # r2 snapshot이 아님
            self.assertNotEqual(source["vector_digest"], r2["vector_digest"], key)
            # 원본은 보존 자산(r3b) 폴더 안에 있다 → 도구는 읽기만 하고 같은 폴더에 새 파일을 만들 수 없다
            self.assertTrue(profiles.is_preserved_output(ROOT / path), key)
        self.assertNotEqual(profiles.R3_SOURCES["b2"]["snapshot_sha256"], profiles.R3_SOURCES["b3"]["snapshot_sha256"])
        # 새 snapshot 이름(R3_COLLECTIONS)은 r2 이름을 재사용하지 않는다
        members = {spec["snapshot_member"] for spec in profiles.R3_COLLECTIONS.values()}
        self.assertNotIn(r2["snapshot_member"], members)


class IsolationTests(unittest.TestCase):
    def test_r3_ports_and_projects_do_not_overlap_existing_assets(self):
        ports = [env[name] for env in profiles.R3_ENVIRONMENTS.values() for name in ("qdrant_port", "bolt_port")]
        self.assertEqual(len(set(ports)), len(ports))
        self.assertFalse(set(ports) & profiles.FOREIGN_PORTS)
        self.assertFalse(set(ports) & r2w.DENIED_PORTS)
        self.assertTrue(r2w.DENIED_PORTS <= profiles.FOREIGN_PORTS)
        self.assertTrue({qdrant_r2.ISOLATED_QDRANT_PORT, neo4j_r2.ISOLATED_BOLT_PORT} <= profiles.FOREIGN_PORTS)
        projects = {env["project"] for env in profiles.R3_ENVIRONMENTS.values()}
        self.assertEqual(len(projects), 2)
        self.assertNotIn(r2w.PROJECT, projects)

    def test_services_for_each_environment_own_only_project_volumes(self):
        for name, env in profiles.R3_ENVIRONMENTS.items():
            with self.subTest(env=name):
                services = profiles.services_for(name)
                self.assertEqual({s["port"] for s in services.values()}, {env["qdrant_port"], env["bolt_port"]})
                self.assertEqual((services["qdrant"]["image"], services["neo4j"]["image"]),
                                 (profiles.QDRANT_IMAGE, profiles.NEO4J_IMAGE))
                volumes = set().union(*(s["volumes"] for s in services.values()))
                self.assertTrue(all(v.startswith(env["project"] + "_") for v in volumes))
                self.assertEqual(len(volumes), 3)
        self.assertEqual(r2w.SERVICES["qdrant"]["image"], profiles.QDRANT_IMAGE)      # r2와 같은 이미지 digest

    def test_eol_helper_ignores_only_line_endings(self):
        self.assertTrue(profiles.same_text_ignoring_eol(b"a\r\nb\r\n", b"a\nb\n"))
        self.assertFalse(profiles.same_text_ignoring_eol(b"a\nb\n", b"a\nc\n"))
        self.assertFalse(profiles.same_text_ignoring_eol(b"a\nb\n", b"a\nb\n\n"))

    def test_pin_free_and_pin_requiring_writes_are_documented(self):
        self.assertEqual(len(profiles.WRITES_WITHOUT_PINS), 3)
        self.assertFalse(set(profiles.WRITES_WITHOUT_PINS) & set(profiles.WRITES_REQUIRING_PINS))
        runbook = (ROOT / "docs" / "development" / "ISSUE105_R3_RUNBOOK.md").read_text(encoding="utf-8")
        for entry in ("--restore-snapshot", "--apply", "--index", "--load", "--create-snapshot", "--build"):
            self.assertIn(entry, runbook)

    def test_r3_secret_variables_are_dedicated(self):
        names = [env[n] for env in profiles.R3_ENVIRONMENTS.values() for n in ("qdrant_key_env", "neo4j_password_env")]
        self.assertEqual(len(set(names)), 4)
        self.assertFalse(set(names) & set(profiles.FOREIGN_SECRET_ENVS))
        self.assertTrue({r2w.QDRANT_KEY_ENV, r2w.NEO4J_PASSWORD_ENV, neo4j_r2.PASSWORD_ENV}
                        <= set(profiles.FOREIGN_SECRET_ENVS))


class ComposeStaticTests(unittest.TestCase):
    """docs/development/issue105_r3_{build,verify}.compose.yaml 정적 검사 (Docker 실행 없음)."""

    def test_each_compose_uses_only_its_project_ports_secrets_and_images(self):
        for name, env in profiles.R3_ENVIRONMENTS.items():
            with self.subTest(env=name):
                text = (ROOT / env["compose"]).read_text(encoding="utf-8")
                body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
                self.assertRegex(body, rf"(?m)^name: {re.escape(env['project'])}$")
                self.assertEqual(sorted(re.findall(r'"(127\.0\.0\.1:\d+:\d+)"', body)),
                                 sorted([f"127.0.0.1:{env['bolt_port']}:7687", f"127.0.0.1:{env['qdrant_port']}:6333"]))
                self.assertIn(profiles.QDRANT_IMAGE, body)
                self.assertIn(profiles.NEO4J_IMAGE, body)
                self.assertEqual(set(re.findall(r"\$\{([A-Z0-9_]+):\?", body)),
                                 {env["qdrant_key_env"], env["neo4j_password_env"]})
                for forbidden in ("external:", "container_name", "0.0.0.0", "network_mode", "privileged", "./",
                                  "QDRANT_API_KEY}", "NEO4J_PASSWORD}"):
                    self.assertNotIn(forbidden, body, forbidden)
                volume_lines = re.findall(r"(?m)^\s+- (\S+):/", body)
                self.assertEqual(sorted(volume_lines), ["neo4j_data", "neo4j_logs", "qdrant_storage"])
                self.assertNotIn("down -v", body)


if __name__ == "__main__":
    unittest.main()
