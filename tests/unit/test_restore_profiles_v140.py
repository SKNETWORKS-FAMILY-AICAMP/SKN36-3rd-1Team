"""Issue #105 복원 기준값(scripts/restore_profiles_v140.py)과 r3 Compose 정적 검사.

- R2 표가 #91 r2 도구의 실제 상수와 같은지 (r2 기준을 한곳에서 읽어도 값이 갈라지지 않게)
- R3 기준값은 2차 확정(미확정 0개) 상태이고 그 값이 현재 릴리스와 같은지, 그리고 1차 확정·전부 미확정 상태의
  게이트 동작이 fixture(stage1_pins·pending_pins)로 계속 검증되는지
- r3 격리 포트·프로젝트·인증 변수가 공유·r1·r2 자산과 겹치지 않는지
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r3_fixtures import pending_pins, stage1_pins  # noqa: E402

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
    """세 상태를 각각 검증한다: 전부 미확정(pending_pins) / 1차 확정(stage1_pins) / 2차 확정(실제 R3_PINS)."""

    def test_real_r3_pins_are_fully_finalized(self):
        """2차 확정: 미확정 0개 → 기준값 게이트는 모든 단계를 통과시킨다 (각 단계의 다른 검사는 그대로)."""
        self.assertTrue(profiles.is_finalized())
        self.assertEqual(profiles.missing_pins(), [])
        self.assertIs(profiles.require_finalized(), profiles.R3_PINS)
        self.assertIs(profiles.require_finalized(deferred=profiles.UPDATE_STAGE_DEFERRED_PINS), profiles.R3_PINS)

    def test_stage1_fixture_defers_only_final_vector_digests(self):
        stage1 = stage1_pins()
        self.assertEqual(sorted(profiles.missing_pins(stage1)), sorted(profiles.UPDATE_STAGE_DEFERRED_PINS))
        with self.assertRaisesRegex(profiles.ProfileError, "vector_digest"):
            profiles.require_finalized(stage1)
        self.assertIs(profiles.require_finalized(stage1, deferred=profiles.UPDATE_STAGE_DEFERRED_PINS), stage1)
        # 1차 확정 값은 실제 R3_PINS와 vector_digest 두 칸만 다르다
        real = profiles.r3_pins()
        for key in profiles.COLLECTION_KEYS:
            real["collections"][key]["vector_digest"] = None
        self.assertEqual(stage1, real)

    def test_pending_fixture_is_refused_even_for_the_update_stage(self):
        with self.assertRaisesRegex(profiles.ProfileError, "확정되지 않았습니다"):
            profiles.require_finalized(pending_pins(), deferred=profiles.UPDATE_STAGE_DEFERRED_PINS)
        self.assertIn("manifest_lf_sha256", profiles.missing_pins(pending_pins()))

    def test_final_pins_are_well_formed_and_internally_consistent(self):
        pins = profiles.R3_PINS
        self.assertRegex(pins["manifest_lf_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(set(pins["release_counts"]), set(profiles.RELEASE_COUNT_KEYS))
        self.assertTrue(all(type(v) is int and v > 0 for v in pins["release_counts"].values()))
        digests = [pins["collections"][k]["vector_digest"] for k in profiles.COLLECTION_KEYS]
        self.assertEqual(len(set(digests)), len(digests))                         # 컬렉션마다 다른 값
        for key in profiles.COLLECTION_KEYS:
            with self.subTest(key=key):
                c = pins["collections"][key]
                self.assertRegex(c["vector_digest"], r"^[0-9a-f]{64}$")            # 2차 확정
                self.assertNotEqual(c["vector_digest"], c["reuse_vector_digest"])  # 신규 임베딩이 더해진 최종 값
                self.assertNotEqual(c["vector_digest"], profiles.R3_SOURCES[key]["vector_digest"])
                self.assertRegex(c["plan_sha256"], r"^[0-9a-f]{64}$")
                self.assertRegex(c["reuse_vector_digest"], r"^[0-9a-f]{64}$")
                self.assertEqual(c["reuse_count"] + c["embed_count"], c["points"])
                self.assertLessEqual(c["delete_count"], c["embed_count"])
                self.assertEqual(c["remove_count"], 0)
                # 재사용 + 삭제 후 재임베딩 = 원본 snapshot Point 수 (remove 0)
                self.assertEqual(c["reuse_count"] + c["delete_count"], profiles.R3_SOURCES[key]["points"])

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
        copied["collections"]["b2"]["vector_digest"] = None
        self.assertEqual(profiles.R3_PINS, original)
        self.assertTrue(profiles.is_finalized())

    def test_r3_collections_match_full_index_configs(self):
        for key in profiles.COLLECTION_KEYS:
            spec = profiles.collection_spec(key)
            config = fi.FULL_INDEX_CONFIGS[spec["config"]]
            self.assertEqual(config["collection"], spec["collection"])
            self.assertEqual(config["strategy"], spec["strategy"])
            self.assertEqual(fi.config_model(config), (spec["model"], spec["dimensions"]))
        with self.assertRaises(profiles.ProfileError):
            profiles.collection_spec("b4")

    def test_r3_sources_are_the_existing_assets(self):
        b2 = profiles.R2_PROFILE["collections"]["b2"]
        self.assertEqual((profiles.R3_SOURCES["b2"]["snapshot_sha256"], profiles.R3_SOURCES["b2"]["vector_digest"]),
                         (b2["snapshot_sha256"], b2["vector_digest"]))
        self.assertNotEqual(profiles.R3_SOURCES["b3"]["snapshot_sha256"], b2["snapshot_sha256"])
        members = {spec["snapshot_member"] for spec in profiles.R3_COLLECTIONS.values()}
        self.assertNotIn(b2["snapshot_member"], members)         # r2 snapshot 이름을 재사용하지 않는다


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
