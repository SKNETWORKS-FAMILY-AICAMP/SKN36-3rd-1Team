"""Issue #105/#110: r3b build 프로필과 #104 보존 자산 격리 테스트 (DB·Docker·네트워크 접속 없음).

#104 기준 r3 build(issue105-r3-build, 56646/56677, ISSUE105_R3_BUILD_* 인증, .cache/issue105-r3)는 보존 자산이다.
r3 도구는 r3b build(issue105-r3b-build, 56666/56697, ISSUE105_R3B_BUILD_* 인증, .cache/issue105-r3b)만 대상으로 한다.
출력 경로 거부를 실제로 실행하는 테스트는 보존 폴더 설정을 임시 폴더로 바꿔서 확인한다 → 거부가 깨져도 실제
.cache/issue105-r3에는 아무것도 생기지 않는다.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r3_fixtures import FakeDocker, build_container, good_build_docker  # noqa: E402

from scripts import build_restore_bundle_r3 as bundle  # noqa: E402
from scripts import build_restore_bundle_v140 as b2b  # noqa: E402
from scripts import load_neo4j_r3_isolated as neo  # noqa: E402
from scripts import plan_r3_vector_reuse as planner  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402
from scripts import restore_r3_isolated as wrap  # noqa: E402
from scripts import update_r3_collections as update  # noqa: E402

ROOT = Path(profiles.PROJECT_ROOT)
R3B = {"project": "issue105-r3b-build", "compose": "docs/development/issue105_r3b_build.compose.yaml",
       "qdrant_port": 56666, "bolt_port": 56697, "qdrant_key_env": "ISSUE105_R3B_BUILD_QDRANT_API_KEY",
       "neo4j_password_env": "ISSUE105_R3B_BUILD_NEO4J_PASSWORD", "out_dir": ".cache/issue105-r3b"}
OLD = {"project": "issue105-r3-build", "qdrant_port": 56646, "bolt_port": 56677,
       "qdrant_key_env": "ISSUE105_R3_BUILD_QDRANT_API_KEY", "neo4j_password_env": "ISSUE105_R3_BUILD_NEO4J_PASSWORD"}
OLD_VOLUMES = {"qdrant": ["issue105-r3-build_qdrant_storage"],
               "neo4j": ["issue105-r3-build_neo4j_data", "issue105-r3-build_neo4j_logs"]}


class TempPreserved:
    """보존 폴더(#104 out_dir) 설정을 임시 폴더로 바꾼다. 테스트 안의 거부 대상 경로는 모두 이 폴더 안이다."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "preserved-104"
        self.path.mkdir()
        (self.path / "existing-report.json").write_bytes(b"{}")
        self.patcher = patch.dict(profiles.PRESERVED_R3_ENVIRONMENTS["build-104"], {"out_dir": str(self.path)})
        self.patcher.start()
        return self

    def listing(self):
        return sorted((str(p.relative_to(self.path)), p.read_bytes() if p.is_file() else None) for p in self.path.rglob("*"))

    def __exit__(self, *exc):
        self.patcher.stop()
        self.tmp.cleanup()


class ProfileValuesTests(unittest.TestCase):
    def test_r3b_build_profile_final_values(self):
        self.assertEqual(profiles.environment("build"), R3B)
        self.assertEqual((update.BUILD_PROJECT, update.ISOLATED_QDRANT_PORT, update.API_KEY_ENV),
                         (R3B["project"], 56666, R3B["qdrant_key_env"]))
        self.assertEqual((neo.ISOLATED_BOLT_PORT, neo.PASSWORD_ENV), (56697, R3B["neo4j_password_env"]))
        verify = profiles.environment("verify")
        self.assertEqual((verify["project"], verify["qdrant_port"], verify["bolt_port"]), ("issue105-r3-verify", 56656, 56687))

    def test_r3b_services_own_only_r3b_volumes(self):
        services = profiles.services_for("build")
        volumes = set().union(*(s["volumes"] for s in services.values()))
        self.assertEqual(volumes, {"issue105-r3b-build_qdrant_storage", "issue105-r3b-build_neo4j_data",
                                   "issue105-r3b-build_neo4j_logs"})
        self.assertFalse(volumes & set().union(*map(set, OLD_VOLUMES.values())))

    def test_preserved_104_ports_secrets_and_project_are_denied(self):
        self.assertEqual({k: profiles.PRESERVED_R3_ENVIRONMENTS["build-104"][k] for k in OLD}, OLD)
        self.assertTrue({56646, 56677} <= profiles.FOREIGN_PORTS)
        self.assertTrue({OLD["qdrant_key_env"], OLD["neo4j_password_env"]} <= set(profiles.FOREIGN_SECRET_ENVS))
        self.assertIn(OLD["project"], profiles.FOREIGN_PROJECTS)
        for name, env in profiles.R3_ENVIRONMENTS.items():
            with self.subTest(env=name):
                self.assertFalse({env["qdrant_port"], env["bolt_port"]} & profiles.FOREIGN_PORTS)
                self.assertNotIn(env["project"], profiles.FOREIGN_PROJECTS)
                self.assertFalse({env["qdrant_key_env"], env["neo4j_password_env"]} & set(profiles.FOREIGN_SECRET_ENVS))

    def test_environment_refuses_a_profile_that_overlaps_preserved_assets(self):
        for field, value in (("qdrant_port", 56646), ("bolt_port", 56677), ("project", OLD["project"]),
                             ("qdrant_key_env", OLD["qdrant_key_env"]), ("neo4j_password_env", OLD["neo4j_password_env"])):
            with self.subTest(field=field), patch.dict(profiles.R3_ENVIRONMENTS, {"build": {**R3B, field: value}}):
                with self.assertRaisesRegex(profiles.ProfileError, "보존"):
                    profiles.environment("build")

    def test_preserved_record_matches_the_untouched_104_compose(self):
        text = (ROOT / "docs/development/issue105_r3_build.compose.yaml").read_text(encoding="utf-8")
        self.assertIn("name: issue105-r3-build\n", text.replace("\r\n", "\n"))
        for needle in ('"127.0.0.1:56646:6333"', '"127.0.0.1:56677:7687"', OLD["qdrant_key_env"], OLD["neo4j_password_env"]):
            self.assertIn(needle, text)
        new = (ROOT / R3B["compose"]).read_text(encoding="utf-8")
        body = "\n".join(line for line in new.splitlines() if not line.lstrip().startswith("#"))
        for needle in ("56646", "56677", "ISSUE105_R3_BUILD_", "issue105-r3-build"):
            self.assertNotIn(needle, body)                       # r3b compose 본문은 #104 값을 쓰지 않는다


class OutputPathTests(unittest.TestCase):
    def test_preserved_folder_and_children_are_detected(self):
        preserved = ROOT / ".cache" / "issue105-r3"
        for path in (preserved, preserved / "x.json", preserved / "bundle-r3" / "b2-r3-v1.12.4.snapshot",
                     Path(".cache/issue105-r3/plan-b2.json").resolve()):
            with self.subTest(path=str(path)):
                self.assertTrue(profiles.is_preserved_output(path))
                with self.assertRaises(profiles.ProfileError):
                    profiles.require_output_path(path)
        self.assertEqual(profiles.is_preserved_output(str(preserved / "X.JSON").upper()), os.name == "nt")
        for path in (ROOT / ".cache" / "issue105-r3b" / "x.json", ROOT / ".cache" / "issue105-r3-other" / "x.json",
                     ROOT / ".cache" / "issue105-r3b", bundle.DEFAULT_OUT_DIR):
            with self.subTest(path=str(path)):
                self.assertFalse(profiles.is_preserved_output(path))
                self.assertEqual(profiles.require_output_path(path), path)

    def test_default_bundle_output_is_the_r3b_folder(self):
        self.assertEqual(bundle.DEFAULT_OUT_DIR, ROOT / ".cache" / "issue105-r3b" / "bundle-r3")


def docker_with(qdrant_project=None, neo4j_project=None, qdrant_volumes=None, neo4j_volumes=None, ports=None):
    ports = ports or {}
    return FakeDocker([
        build_container("b" * 64, "qdrant", project=qdrant_project or R3B["project"], volumes=qdrant_volumes,
                        port=ports.get("qdrant")),
        build_container("c" * 64, "neo4j", project=neo4j_project or R3B["project"], volumes=neo4j_volumes,
                        port=ports.get("neo4j"))])


DOCKER_CASES = {
    "104_project_on_r3b_ports": dict(qdrant_project=OLD["project"], neo4j_project=OLD["project"],
                                     qdrant_volumes=OLD_VOLUMES["qdrant"], neo4j_volumes=OLD_VOLUMES["neo4j"]),
    "r3b_label_with_104_volumes": dict(qdrant_volumes=OLD_VOLUMES["qdrant"], neo4j_volumes=OLD_VOLUMES["neo4j"]),
    "104_containers_on_104_ports": dict(qdrant_project=OLD["project"], neo4j_project=OLD["project"],
                                        qdrant_volumes=OLD_VOLUMES["qdrant"], neo4j_volumes=OLD_VOLUMES["neo4j"],
                                        ports={"qdrant": 56646, "neo4j": 56677}),
    "mixed_qdrant_from_104": dict(qdrant_project=OLD["project"], qdrant_volumes=OLD_VOLUMES["qdrant"]),
}


class UpdateToolTests(unittest.TestCase):
    URL = f"http://127.0.0.1:{update.ISOLATED_QDRANT_PORT}"

    def test_only_r3b_qdrant_port_and_never_104_or_verify(self):
        self.assertEqual(update.require_isolated_url(self.URL), self.URL)
        for url in ("http://127.0.0.1:56646", "http://127.0.0.1:56677", "http://127.0.0.1:56656", "http://localhost:56646"):
            with self.subTest(url=url), self.assertRaises((update.UpdateError, ValueError)):
                update.require_isolated_url(url)

    def test_104_secret_values_are_not_reused(self):
        value = "sample_only_same_value"
        for old in (OLD["qdrant_key_env"], OLD["neo4j_password_env"]):
            with self.subTest(old=old), self.assertRaisesRegex(update.UpdateError, "재사용"):
                update.read_secret(update.API_KEY_ENV, {update.API_KEY_ENV: value, old: value})
        self.assertEqual(update.read_secret(update.API_KEY_ENV, {update.API_KEY_ENV: value, OLD["qdrant_key_env"]: "other"}),
                         value)

    def test_104_containers_or_volumes_are_never_taken_as_r3b(self):
        self.assertEqual(set(update.require_build_ownership(good_build_docker())), {"qdrant", "neo4j"})
        for name, kwargs in DOCKER_CASES.items():
            with self.subTest(case=name), self.assertRaisesRegex(update.UpdateError, "Docker 소유"):
                update.require_build_ownership(docker_with(**kwargs))

    def test_cli_refuses_report_or_plan_in_preserved_folder_before_connecting(self):
        with TempPreserved() as preserved, patch.object(update, "make_isolated_client",
                                                         side_effect=AssertionError("접속 금지")):
            before = preserved.listing()
            for flag in ("--report", "--write-plan"):
                with self.subTest(flag=flag), self.assertRaises(SystemExit):
                    update.main(["--qdrant-url", self.URL, "--collection", "b2", flag, str(preserved.path / "new.json")])
            self.assertEqual(preserved.listing(), before)


class Neo4jToolTests(unittest.TestCase):
    URL = f"bolt://127.0.0.1:{neo.ISOLATED_BOLT_PORT}"

    def test_only_r3b_bolt_port_and_never_104_or_verify(self):
        self.assertEqual(neo.require_isolated_bolt_url(self.URL), self.URL)
        for url in ("bolt://127.0.0.1:56677", "bolt://127.0.0.1:56646", "bolt://127.0.0.1:56687"):
            with self.subTest(url=url), self.assertRaises((neo.Neo4jR3Error, ValueError)):
                neo.require_isolated_bolt_url(url)

    def test_104_containers_or_volumes_are_refused(self):
        for name, kwargs in DOCKER_CASES.items():
            with self.subTest(case=name), self.assertRaisesRegex(neo.Neo4jR3Error, "Docker 소유"):
                neo.ownership(docker_with(**kwargs))

    def test_cli_refuses_report_in_preserved_folder_before_connecting(self):
        with TempPreserved() as preserved, patch("neo4j.GraphDatabase.driver", side_effect=AssertionError("접속 금지")):
            before = preserved.listing()
            with self.assertRaises(SystemExit):
                neo.main(["--neo4j-url", self.URL, "--report", str(preserved.path / "new.json")])
            self.assertEqual(preserved.listing(), before)


class BundleToolTests(unittest.TestCase):
    def test_snapshot_and_bundle_refuse_preserved_output_before_any_write(self):
        client = SimpleNamespace(create_snapshot=lambda **kwargs: (_ for _ in ()).throw(AssertionError("서버 쓰기 금지")))
        with TempPreserved() as preserved:
            before = preserved.listing()
            for out_dir in (preserved.path, preserved.path / "bundle-r3"):
                with self.subTest(out_dir=out_dir.name):
                    with self.assertRaisesRegex(b2b.BundleError, "보존 자산"):
                        bundle.create_snapshot(client, UpdateToolTests.URL, {}, {}, out_dir, confirm_write=True,
                                               expected_fingerprint="0" * 64)
                    with self.assertRaisesRegex(b2b.BundleError, "보존 자산"):
                        bundle.build_bundle({}, "0" * 40, out_dir, confirm_write=True)
            self.assertEqual(preserved.listing(), before)

    def test_cli_refuses_preserved_out_dir(self):
        with TempPreserved() as preserved:
            before = preserved.listing()
            for argv in (["--build", "--confirm-write", "--source-commit", "0" * 40],
                         ["--create-snapshot", "--confirm-write", "--collection", "b2", "--plan", "sample_only.json",
                          "--qdrant-url", "http://127.0.0.1:56666", "--expect-fingerprint", "0" * 64]):
                with self.subTest(mode=argv[0]), self.assertRaises(SystemExit):
                    bundle.main([*argv, "--out-dir", str(preserved.path / "bundle-r3")])
            self.assertEqual(preserved.listing(), before)

    def test_plan_mode_on_preserved_folder_stays_read_only(self):
        with TempPreserved() as preserved:
            before = preserved.listing()
            output = bundle.plan(None, preserved.path)
            self.assertEqual((output["writes"], preserved.listing()), (0, before))


class RestoreWrapperTests(unittest.TestCase):
    def test_build_and_104_ports_are_denied_for_verify(self):
        self.assertTrue({56646, 56677, 56666, 56697} <= wrap.DENIED_PORTS)
        self.assertFalse({56656, 56687} & wrap.DENIED_PORTS)
        for qdrant, bolt in (("http://127.0.0.1:56646", wrap.NEO4J_URI), (wrap.QDRANT_URL, "bolt://127.0.0.1:56677"),
                             ("http://127.0.0.1:56666", wrap.NEO4J_URI), (wrap.QDRANT_URL, "bolt://127.0.0.1:56697")):
            with self.subTest(qdrant=qdrant, bolt=bolt), self.assertRaises((wrap.IsolationError, ValueError)):
                wrap.require_target_urls(qdrant, bolt)

    def test_verify_secrets_never_reuse_104_or_r3b_build_values(self):
        values = {wrap.QDRANT_KEY_ENV: "sample_only_verify_key", wrap.NEO4J_PASSWORD_ENV: "sample_only_verify_pw"}
        for other in (OLD["qdrant_key_env"], OLD["neo4j_password_env"], R3B["qdrant_key_env"], R3B["neo4j_password_env"]):
            with self.subTest(other=other), self.assertRaisesRegex(wrap.IsolationError, "재사용"):
                wrap.read_secrets({**values, other: values[wrap.NEO4J_PASSWORD_ENV]})

    def test_cli_refuses_report_in_preserved_folder(self):
        with TempPreserved() as preserved:
            before = preserved.listing()
            with self.assertRaises(SystemExit):
                wrap.main(["--bundle", str(preserved.path), "--report", str(preserved.path / "new.json")])
            self.assertEqual(preserved.listing(), before)


class PlannerCliTests(unittest.TestCase):
    def test_planner_report_in_preserved_folder_is_refused(self):
        with TempPreserved() as preserved, patch.object(planner, "load_release", side_effect=AssertionError("읽기 금지")):
            before = preserved.listing()
            with self.assertRaises(SystemExit):
                planner.main(["--report", str(preserved.path / "new.json")])
            self.assertEqual(preserved.listing(), before)


if __name__ == "__main__":
    unittest.main()
