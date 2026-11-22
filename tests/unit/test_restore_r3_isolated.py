"""Issue #105 r3 독립 복원 보호 래퍼(scripts/restore_r3_isolated.py) 테스트.

FakeDocker·VerifyQdrant·VerifyNeo4j(r2 래퍼 테스트 더블)와 sample_only 번들만 쓴다. Docker·DB 접속 없음.
"""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r3_fixtures import final_sample_pins, pending_pins, stage1_pins  # noqa: E402
from test_restore_final_v140_r3 import Bundle  # noqa: E402
from test_restore_r2_isolated import FakeDocker, VerifyNeo4j, VerifyQdrant  # noqa: E402

from database.neo4j import load_graph as lg  # noqa: E402
from scripts import restore_profiles_v140 as profiles  # noqa: E402
from scripts import restore_r2_isolated as r2w  # noqa: E402
from scripts import restore_r3_isolated as wrap  # noqa: E402

VALUES = {"qdrant": "sample_only_r3_verify_key", "neo4j": "sample_only_r3_verify_password"}
ENV = {wrap.QDRANT_KEY_ENV: VALUES["qdrant"], wrap.NEO4J_PASSWORD_ENV: VALUES["neo4j"]}
GRAPH = lg.read_graph_input()


def container(cid, service, *, project=wrap.PROJECT, host_ip="127.0.0.1", volumes=None, mount_type="volume"):
    spec = wrap.SERVICES[service]
    return {"Id": cid, "Image": spec["image"],
            "Config": {"Labels": {"com.docker.compose.project": project, "com.docker.compose.service": service}},
            "State": {"Running": True, "StartedAt": "2026-11-22T00:00:00Z"},
            "NetworkSettings": {"Ports": {spec["container_port"]: [{"HostIp": host_ip, "HostPort": str(spec["port"])}]}},
            "Mounts": [{"Type": mount_type, "Name": name} for name in (volumes or sorted(spec["volumes"]))]}


def good_docker():
    return FakeDocker([container("q" * 64, "qdrant"), container("n" * 64, "neo4j")])


def make_ctx(docker=None, qdrant=None):
    from neo4j.exceptions import AuthError
    qdrant = qdrant if qdrant is not None else VerifyQdrant()

    def neo4j_factory(password):
        if password != VALUES["neo4j"]:
            raise AuthError("sample_only")
        return SimpleNamespace(close=lambda: None)

    return wrap.Context(dict(VALUES), docker=docker or good_docker(), qdrant_factory=lambda values: qdrant,
                        neo4j_factory=neo4j_factory, runner_factory=lambda driver: VerifyNeo4j(),
                        http_get=lambda url, timeout: SimpleNamespace(status_code=401), graph=GRAPH)


class FakeRestore:
    def __init__(self, apply_status="PASS", verify_status="PASS"):
        self.calls, self.apply_status, self.verify_status = [], apply_status, verify_status

    def __call__(self, args, settings=None, pins=None):
        self.calls.append({"apply": args.apply, "verify_only": args.verify_only, "settings": dict(settings),
                           "pins": pins})
        if args.verify_only:
            return {"status": self.verify_status}
        return {"status": self.apply_status if args.apply else "PREFLIGHT_PASS_NO_WRITES"}


class TargetAndSecretTests(unittest.TestCase):
    def test_only_fixed_r3_verify_addresses(self):
        wrap.require_target_urls()
        for qdrant, bolt in (("http://127.0.0.1:56556", wrap.NEO4J_URI), (wrap.QDRANT_URL, "bolt://127.0.0.1:56587"),
                             ("http://127.0.0.1:56646", wrap.NEO4J_URI), (wrap.QDRANT_URL, "bolt://127.0.0.1:56677"),
                             ("http://127.0.0.1:6333", wrap.NEO4J_URI)):
            with self.subTest(qdrant=qdrant, bolt=bolt), self.assertRaises((wrap.IsolationError, ValueError)):
                wrap.require_target_urls(qdrant, bolt)

    def test_secrets_are_dedicated_and_never_reused(self):
        self.assertEqual(wrap.read_secrets(ENV), VALUES)
        with self.assertRaisesRegex(wrap.IsolationError, "없습니다"):
            wrap.read_secrets({wrap.QDRANT_KEY_ENV: "x"})
        with self.assertRaisesRegex(wrap.IsolationError, "서로 달라야"):
            wrap.read_secrets({wrap.QDRANT_KEY_ENV: "same", wrap.NEO4J_PASSWORD_ENV: "same"})
        for other in ("QDRANT_API_KEY", "ISSUE91_VERIFY_NEO4J_PASSWORD", profiles.R3_ENVIRONMENTS["build"]["qdrant_key_env"]):
            with self.subTest(other=other), self.assertRaisesRegex(wrap.IsolationError, "재사용"):
                wrap.read_secrets({**ENV, other: VALUES["neo4j"]})


class DockerOwnershipTests(unittest.TestCase):
    def test_exact_r3_project_containers_pass(self):
        identity = make_ctx().identity()
        self.assertEqual(set(identity), {"qdrant", "neo4j"})

    def test_foreign_or_misconfigured_containers_are_refused(self):
        cases = {
            "r2_project": [container("q" * 64, "qdrant", project="issue91-r2-verify-20261119"),
                           container("n" * 64, "neo4j")],
            "extra_in_project": [container("q" * 64, "qdrant"), container("n" * 64, "neo4j"),
                                 {**container("x" * 64, "neo4j"), "NetworkSettings": {"Ports": {}}}],
            "all_interfaces": [container("q" * 64, "qdrant", host_ip="0.0.0.0"), container("n" * 64, "neo4j")],
            "bind_mount": [container("q" * 64, "qdrant", mount_type="bind"), container("n" * 64, "neo4j")],
            "r2_volume": [container("q" * 64, "qdrant", volumes=["issue91-r2-verify-20261119_qdrant_storage"]),
                          container("n" * 64, "neo4j")],
        }
        for name, containers in cases.items():
            with self.subTest(name=name), self.assertRaises(wrap.IsolationError):
                make_ctx(docker=FakeDocker(containers)).identity()

    def test_r2_defaults_are_unchanged(self):
        self.assertEqual(r2w.PROJECT, "issue91-r2-verify-20261119")
        self.assertEqual({s["port"] for s in r2w.SERVICES.values()}, {56556, 56587})


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.b = Bundle(Path(self.tmp.name))           # 복원 도구·source ZIP·compose까지 담은 번들
        self.env_before = dict(os.environ)

    def run_mode(self, mode, ctx=None, restore_fn=None, **kwargs):
        ctx = ctx or make_ctx()
        return wrap.run(mode, wrap.ROOT, self.b.folder, ctx, restore_fn=restore_fn, pins=self.b.pins, **kwargs)

    def test_preflight_passes_fixed_settings_without_touching_environment(self):
        fake = FakeRestore()
        result = self.run_mode("preflight", restore_fn=fake)
        self.assertEqual((result["db_writes"], result["servers"]["state"]), (0, "empty"))
        self.assertEqual(fake.calls[0]["settings"]["QDRANT_URL"], wrap.QDRANT_URL)
        self.assertEqual(fake.calls[0]["settings"]["NEO4J_URI"], wrap.NEO4J_URI)
        self.assertIs(fake.calls[0]["pins"], self.b.pins)
        self.assertEqual(dict(os.environ), self.env_before)
        self.assertNotIn(VALUES["qdrant"], str({k: v for k, v in result.items() if k != "servers"}))

    def test_apply_requires_matching_fingerprint_and_runs_verify_only_after(self):
        fake = FakeRestore()
        with self.assertRaisesRegex(wrap.IsolationError, "fingerprint"):
            self.run_mode("apply", restore_fn=fake, confirm_write=True, expected_fingerprint="0" * 64)
        self.assertEqual(fake.calls, [])
        ctx = make_ctx()
        current = ctx.snapshot()[2]
        result = self.run_mode("apply", ctx=ctx, restore_fn=fake, confirm_write=True, expected_fingerprint=current)
        self.assertEqual([(c["apply"], c["verify_only"]) for c in fake.calls], [(True, False), (False, True)])
        self.assertEqual(result["post_verify"]["status"], "PASS")

    def test_apply_failure_or_container_change_is_reported(self):
        ctx = make_ctx()
        current = ctx.snapshot()[2]
        with self.assertRaisesRegex(wrap.IsolationError, "부분 자산 보존"):
            self.run_mode("apply", ctx=ctx, restore_fn=FakeRestore(apply_status="FAILED"), confirm_write=True,
                          expected_fingerprint=current)
        docker = good_docker()
        ctx = make_ctx(docker=docker)
        current = ctx.snapshot()[2]

        def swap(args, settings=None, pins=None):
            docker.containers["q" * 64]["State"]["StartedAt"] = "2026-11-22T01:00:00Z"
            return {"status": "PASS"}
        with self.assertRaisesRegex(wrap.IsolationError, "컨테이너가 바뀌었습니다"):
            self.run_mode("apply", ctx=ctx, restore_fn=swap, confirm_write=True, expected_fingerprint=current)

    def test_non_empty_target_and_incomplete_verify_are_refused(self):
        busy = VerifyQdrant({})
        busy.collections = {"exp_b2_qwen3emb06b_v140_v1": {}}
        with self.assertRaisesRegex(wrap.IsolationError, "비어 있지 않습니다"):
            self.run_mode("preflight", ctx=make_ctx(qdrant=busy), restore_fn=FakeRestore())
        both = VerifyQdrant({})
        both.collections = {name: {} for name in wrap.EXPECTED_COLLECTIONS}
        state = make_ctx(qdrant=both).snapshot()[1]
        self.assertEqual(state["qdrant"]["state"], "restored_collection")
        with self.assertRaisesRegex(wrap.IsolationError, "complete"):
            self.run_mode("verify-only", ctx=make_ctx(qdrant=both), restore_fn=FakeRestore())

    def test_bundle_tool_must_match_source_tree(self):
        tool = self.b.folder / "restore_final_v140_r3.py"
        tool.write_bytes(b"# sample_only other tool\n")
        self.b.write_sums()                                            # 체크섬까지 다시 맞춘 변조
        with self.assertRaisesRegex(wrap.IsolationError, "소스 트리"):
            self.run_mode("preflight", restore_fn=FakeRestore())
        crlf = profiles.eol_normalized((wrap.ROOT / profiles.RESTORE_SCRIPT).read_bytes()).replace(b"\n", b"\r\n")
        tool.write_bytes(crlf)                                         # 줄바꿈만 다른 같은 도구는 허용
        self.b.write_sums()
        self.assertEqual(self.run_mode("preflight", restore_fn=FakeRestore())["db_writes"], 0)
        for pins in (pending_pins(), stage1_pins()):                  # 미확정·1차 상태는 거부
            with self.subTest(pins=profiles.pin_stage(pins)), self.assertRaises(profiles.ProfileError):
                wrap.check_source_and_bundle(wrap.ROOT, self.b.folder, pins)
        for pins in (None, final_sample_pins()):                      # 실제 C2·2차 모양: 게이트 통과 후 번들 불일치
            with self.subTest(pins="real" if pins is None else "final_sample"), \
                    self.assertRaisesRegex(ValueError, "manifest/counts differ"):
                wrap.check_source_and_bundle(wrap.ROOT, self.b.folder, pins)

    def test_source_archive_must_match_repo(self):
        self.b.write_source({profiles.PROFILE_SCRIPT: b"# sample_only other pins\n"})
        self.b.write()
        fake = FakeRestore()
        with self.assertRaisesRegex(ValueError, "differs from the bundle source"):
            self.run_mode("preflight", restore_fn=fake)
        self.assertEqual(fake.calls, [])

    def test_partial_restore_failure_propagates_and_is_not_verified_as_success(self):
        ctx = make_ctx()
        current = ctx.snapshot()[2]
        calls = []

        def partial(args, settings=None, pins=None):
            calls.append((args.apply, args.verify_only))
            raise ValueError("Snapshot upload failed: HTTP 500 (exp_b3_qwen3emb06b_v140_v1)")
        with self.assertRaisesRegex(ValueError, "Snapshot upload failed"):
            self.run_mode("apply", ctx=ctx, restore_fn=partial, confirm_write=True, expected_fingerprint=current)
        self.assertEqual(calls, [(True, False)])                       # verify-only로 넘어가지 않는다


class CliTests(unittest.TestCase):
    def test_apply_flags_and_report_overwrite(self):
        with self.assertRaises(SystemExit):
            wrap.main(["--bundle", ".", "--apply"])
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "r.json"
            report.write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit):
                wrap.main(["--bundle", folder, "--report", str(report)])

    def test_missing_secrets_fail_without_connection(self):
        environ = copy.deepcopy(dict(os.environ))
        for name in (wrap.QDRANT_KEY_ENV, wrap.NEO4J_PASSWORD_ENV):
            os.environ.pop(name, None)
        try:
            self.assertEqual(wrap.main(["--bundle", "."], ctx_factory=lambda values: self.fail("접속 금지")), 1)
        finally:
            os.environ.clear()
            os.environ.update(environ)


if __name__ == "__main__":
    unittest.main()
