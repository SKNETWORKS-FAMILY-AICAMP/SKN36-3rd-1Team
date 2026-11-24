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
        self.assertEqual((verify["project"], verify["qdrant_port"], verify["bolt_port"]), ("issue105-r3d-verify", 56756, 56787))

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


OLD_VERIFY = {"project": "issue105-r3-verify", "compose": "docs/development/issue105_r3_verify.compose.yaml",
              "qdrant_port": 56656, "bolt_port": 56687, "qdrant_key_env": "ISSUE105_R3_VERIFY_QDRANT_API_KEY",
              "neo4j_password_env": "ISSUE105_R3_VERIFY_NEO4J_PASSWORD", "out_dir": ".cache/issue105-r3c"}
NEW_VERIFY = {"project": "issue105-r3d-verify", "compose": "docs/development/issue105_r3d_verify.compose.yaml",
              "qdrant_port": 56756, "bolt_port": 56787, "qdrant_key_env": "ISSUE105_R3D_VERIFY_QDRANT_API_KEY",
              "neo4j_password_env": "ISSUE105_R3D_VERIFY_NEO4J_PASSWORD", "out_dir": ".cache/issue105-r3d"}


class NewVerifyProfileTests(unittest.TestCase):
    """PR #111 새 독립 verify 환경(issue105-r3d-verify)과 이전 verify(issue105-r3-verify) 보존·거부 (Docker·DB 접속 없음)."""

    def test_new_verify_profile_values_and_wrapper_constants(self):
        self.assertEqual(profiles.environment("verify"), NEW_VERIFY)
        self.assertEqual((wrap.PROJECT, wrap.QDRANT_URL, wrap.NEO4J_URI, wrap.QDRANT_KEY_ENV, wrap.NEO4J_PASSWORD_ENV),
                         (NEW_VERIFY["project"], "http://127.0.0.1:56756", "bolt://127.0.0.1:56787",
                          NEW_VERIFY["qdrant_key_env"], NEW_VERIFY["neo4j_password_env"]))
        wrap.require_target_urls()                                              # 새 주소는 허용
        services = profiles.services_for("verify")
        self.assertEqual({s["port"] for s in services.values()}, {56756, 56787})
        volumes = set().union(*(s["volumes"] for s in services.values()))
        self.assertEqual(volumes, {"issue105-r3d-verify_qdrant_storage", "issue105-r3d-verify_neo4j_data",
                                   "issue105-r3d-verify_neo4j_logs"})
        self.assertFalse(volumes & {f"{OLD_VERIFY['project']}_{v}" for v in ("qdrant_storage", "neo4j_data", "neo4j_logs")})

    def test_old_verify_is_preserved_denied_and_not_selectable(self):
        self.assertEqual(profiles.PRESERVED_R3_ENVIRONMENTS["verify-6d0450f"], OLD_VERIFY)
        self.assertTrue({56656, 56687} <= profiles.FOREIGN_PORTS)
        self.assertTrue({OLD_VERIFY["qdrant_key_env"], OLD_VERIFY["neo4j_password_env"]} <= set(profiles.FOREIGN_SECRET_ENVS))
        self.assertIn(OLD_VERIFY["project"], profiles.FOREIGN_PROJECTS)
        with self.assertRaises(profiles.ProfileError):                          # 보존 항목은 도구가 고를 수 없다
            profiles.environment("verify-6d0450f")
        self.assertNotIn("verify-6d0450f", profiles.R3_ENVIRONMENTS)
        for qdrant, bolt in (("http://127.0.0.1:56656", wrap.NEO4J_URI), (wrap.QDRANT_URL, "bolt://127.0.0.1:56687")):
            with self.subTest(qdrant=qdrant, bolt=bolt), self.assertRaises((wrap.IsolationError, ValueError)):
                wrap.require_target_urls(qdrant, bolt)
        values = {wrap.QDRANT_KEY_ENV: "sample_only_r3d_key", wrap.NEO4J_PASSWORD_ENV: "sample_only_r3d_pw"}
        for old_env in (OLD_VERIFY["qdrant_key_env"], OLD_VERIFY["neo4j_password_env"]):
            with self.subTest(reused=old_env), self.assertRaisesRegex(wrap.IsolationError, "재사용"):
                wrap.read_secrets({**values, old_env: values[wrap.NEO4J_PASSWORD_ENV]})
        # 이전 verify 프로젝트 라벨의 컨테이너는 새 포트에 있어도 Docker 소유 확인에서 거부
        old_containers = [build_container("q" * 64, "qdrant", project=OLD_VERIFY["project"], port=56756),
                          build_container("n" * 64, "neo4j", project=OLD_VERIFY["project"], port=56787)]
        with self.assertRaises(wrap.IsolationError):
            wrap.r2w.verify_docker_ownership(FakeDocker(old_containers), project=wrap.PROJECT, services=wrap.SERVICES)

    def test_old_verify_output_folder_is_preserved_and_new_folder_is_not(self):
        r3c = ROOT / ".cache" / "issue105-r3c"
        self.assertTrue(profiles.is_preserved_output(r3c))                       # 경로 계산만 (파일을 읽거나 만들지 않음)
        self.assertTrue(profiles.is_preserved_output(r3c / "verify-run-new" / "report.json"))
        self.assertFalse(profiles.is_preserved_output(ROOT / ".cache" / "issue105-r3d" / "verify-run-01" / "report.json"))
        self.assertFalse(profiles.is_preserved_output(ROOT / ".cache" / "issue105-r3c-other" / "x.json"))  # 접두어만 같은 폴더
        with self.assertRaises(profiles.ProfileError):
            profiles.require_output_path(r3c / "bundle-new", "번들 출력")

    def test_wrapper_cli_refuses_reports_in_the_preserved_verify_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            preserved = Path(folder) / "preserved-r3c"
            preserved.mkdir()
            (preserved / "existing.json").write_bytes(b"{}")
            with patch.dict(profiles.PRESERVED_R3_ENVIRONMENTS["verify-6d0450f"], {"out_dir": str(preserved)}):
                with self.assertRaises(SystemExit):
                    wrap.main(["--bundle", str(preserved), "--report", str(preserved / "new.json")])
            self.assertEqual(sorted(p.name for p in preserved.iterdir()), ["existing.json"])   # 새 파일 없음

    def test_new_lock_path_differs_from_the_old_verify_environment(self):
        environ = {"LOCALAPPDATA": r"C:\sample_only\AppData\Local", "XDG_STATE_HOME": "/sample_only/state"}
        new = wrap.default_lock_path(environ=environ)
        old = wrap.default_lock_path(OLD_VERIFY["project"], OLD_VERIFY["qdrant_port"], environ=environ)
        self.assertEqual(new.name, "issue105-r3d-verify-56756.lock")
        self.assertEqual(old.name, "issue105-r3-verify-56656.lock")
        self.assertNotEqual(new, old)
        self.assertEqual(new.parent, old.parent)                                 # 같은 잠금 폴더, 다른 파일

    def test_existing_r3_pins_apply_unchanged_to_the_new_verify_profile(self):
        pins = profiles.require_finalized()                                      # 환경과 무관한 단일 기준값
        self.assertEqual((profiles.pin_stage(), pins["manifest_lf_sha256"][:8],
                          pins["collections"]["b2"]["points"], pins["collections"]["b3"]["points"],
                          pins["release_counts"]["neo4j_nodes"], pins["release_counts"]["neo4j_relationships"]),
                         ("stage2", "29b57236", 1233, 1320, 1133, 2674))
        self.assertEqual(bundle.VERIFY_COMPOSE, NEW_VERIFY["compose"])           # 새 번들의 compose.yaml 원본

    def test_bundle_package_labels_the_profile_compose(self):
        record = {"points": 1, "vector_digest": "1" * 64, "payload_digest": "2" * 64, "snapshot_sha256": "3" * 64,
                  "snapshot_size_bytes": 1, "qdrant_version": "1.12.4", "source": "sample_only", "plan_sha256": "4" * 64,
                  "plan_counts": {}}
        package = bundle.build_package({key: dict(record) for key in profiles.COLLECTION_KEYS},
                                       {"manifest_lf_sha256": "5" * 64, "commit": "6" * 40}, "source-666666666666.zip",
                                       {}, profiles.r3_pins())
        self.assertEqual(package["compose"], "compose.yaml (issue105_r3d_verify.compose.yaml)")   # 프로필 기준 표시


class RestoreWrapperTests(unittest.TestCase):
    def test_build_and_104_ports_are_denied_for_verify(self):
        # 이전 verify(56656/56687, 보존 verify-6d0450f)도 거부 대상에 들어간다. 현재 verify(56756/56787)만 허용.
        self.assertTrue({56646, 56677, 56666, 56697, 56656, 56687} <= wrap.DENIED_PORTS)
        self.assertFalse({56756, 56787} & wrap.DENIED_PORTS)
        for qdrant, bolt in (("http://127.0.0.1:56646", wrap.NEO4J_URI), (wrap.QDRANT_URL, "bolt://127.0.0.1:56677"),
                             ("http://127.0.0.1:56666", wrap.NEO4J_URI), (wrap.QDRANT_URL, "bolt://127.0.0.1:56697"),
                             ("http://127.0.0.1:56656", wrap.NEO4J_URI), (wrap.QDRANT_URL, "bolt://127.0.0.1:56687")):
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
