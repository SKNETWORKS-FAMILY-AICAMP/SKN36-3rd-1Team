"""Issue #105 복원 기준값(scripts/restore_profiles_v140.py)과 r3 Compose 정적 검사.

- R2 표가 #91 r2 도구의 실제 상수와 같은지 (r2 기준을 한곳에서 읽어도 값이 갈라지지 않게)
- R3 기준값은 최신 데이터 병합 전이므로 미확정(None)이고, 미확정이면 쓰기 경로가 막히는지
- r3 격리 포트·프로젝트·인증 변수가 공유·r1·r2 자산과 겹치지 않는지
"""
import re
import unittest
from pathlib import Path

from database import full_index as fi
from scripts import build_restore_bundle_v140 as b2b
from scripts import load_neo4j_r2_isolated as neo4j_r2
from scripts import restore_final_v140 as restore
from scripts import restore_profiles_v140 as profiles
from scripts import restore_r2_isolated as r2w
from scripts import seed_b2_from_r1_vectors as seed
from scripts import update_b2_from_snapshot as qdrant_r2

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
    def test_r3_pins_are_pending_until_latest_data_is_merged(self):
        self.assertFalse(profiles.is_finalized())
        missing = profiles.missing_pins()
        self.assertIn("manifest_lf_sha256", missing)
        self.assertIn("collections.b3.vector_digest", missing)
        with self.assertRaisesRegex(profiles.ProfileError, "확정되지 않았습니다"):
            profiles.require_finalized()

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
        copied = profiles.r3_pins()
        copied["manifest_lf_sha256"] = "b" * 64
        self.assertIsNone(profiles.R3_PINS["manifest_lf_sha256"])

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
