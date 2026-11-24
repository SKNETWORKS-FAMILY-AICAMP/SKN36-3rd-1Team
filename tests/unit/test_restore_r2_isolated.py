"""Issue #91 독립 복원 보호 래퍼(scripts/restore_r2_isolated.py) 테스트.

가짜 Docker CLI(ps·inspect만 흉내), 기존 FakeQdrant·FakeNeo4j, 임시 번들 폴더만 쓴다.
실제 Docker·Qdrant·Neo4j 접속과 restore 쓰기는 없다 (restore 함수는 테스트 더블로 바꿔 끼운다).
"""
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_load_neo4j_r2_isolated import FakeNeo4j  # noqa: E402
from test_update_b2_from_snapshot import FakeQdrant  # noqa: E402

from database import full_index as fi  # noqa: E402
from database.neo4j import load_graph as lg  # noqa: E402
from scripts import restore_final_v140 as restore  # noqa: E402
from scripts import restore_r2_isolated as wrap  # noqa: E402

VALUES = {"qdrant": "sample_only_verify_key", "neo4j": "sample_only_verify_password"}
ENV = {wrap.QDRANT_KEY_ENV: VALUES["qdrant"], wrap.NEO4J_PASSWORD_ENV: VALUES["neo4j"]}
GRAPH = lg.read_graph_input()


def container(cid, service, *, project=wrap.PROJECT, running=True, host_ip="127.0.0.1", image=None, volumes=None,
              mount_type="volume", extra_ports=None, started="2026-11-19T00:00:00Z"):
    spec = wrap.SERVICES[service]
    return {"Id": cid, "Image": image or spec["image"],
            "Config": {"Labels": {"com.docker.compose.project": project, "com.docker.compose.service": service}},
            "State": {"Running": running, "StartedAt": started},
            "NetworkSettings": {"Ports": {spec["container_port"]: [{"HostIp": host_ip, "HostPort": str(spec["port"])}],
                                          **(extra_ports or {})}},
            "Mounts": [{"Type": mount_type, "Name": name} for name in (volumes or sorted(spec["volumes"]))]}


class FakeDocker:
    """docker ps(필터 label/publish) / docker inspect만 응답한다. 다른 명령이 오면 실패."""

    def __init__(self, containers):
        self.containers = {c["Id"]: c for c in containers}
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[0] == "ps":
            kind, value = args[-1].split("=", 1)
            if kind == "label":
                _key, project = value.split("=", 1)
                ids = [i for i, c in self.containers.items()
                       if c["Config"]["Labels"].get("com.docker.compose.project") == project]
            else:
                ids = [i for i, c in self.containers.items()
                       if any(b and b[0]["HostPort"] == value for b in c["NetworkSettings"]["Ports"].values())]
            return "\n".join(sorted(ids))
        if args[0] == "inspect":
            return json.dumps([self.containers[i] for i in args[1:]])
        raise AssertionError(f"허용하지 않은 docker 명령: {args}")


def good_docker():
    return FakeDocker([container("q" * 64, "qdrant"), container("n" * 64, "neo4j")])


class VerifyQdrant(FakeQdrant):
    def __init__(self, points=None, **kwargs):
        super().__init__(points or {}, **kwargs)
        if points is None:
            self.collections = {}
        self.aliases = []

    def get_aliases(self):
        return SimpleNamespace(aliases=[SimpleNamespace(alias_name=a, collection_name="x") for a in self.aliases])


class VerifyNeo4j(FakeNeo4j):
    def read_rows(self, query, parameters=None):
        if query == "CALL db.info() YIELD name RETURN name":
            return [{"name": getattr(self, "database_name", "neo4j")}]
        return super().read_rows(query, parameters)


def make_ctx(docker=None, qdrant=None, runner=None, http_status=401, accept_any_password=False):
    from neo4j.exceptions import AuthError
    qdrant = qdrant if qdrant is not None else VerifyQdrant()
    runner = runner if runner is not None else VerifyNeo4j()

    def neo4j_factory(password):
        if password != VALUES["neo4j"] and not accept_any_password:
            raise AuthError("sample_only")
        return SimpleNamespace(close=lambda: None)

    ctx = wrap.Context(dict(VALUES), docker=docker or good_docker(), qdrant_factory=lambda values: qdrant,
                       neo4j_factory=neo4j_factory, runner_factory=lambda driver: runner,
                       http_get=lambda url, timeout: SimpleNamespace(status_code=http_status), graph=GRAPH)
    return ctx, qdrant, runner


class FakeRestore:
    """restore.restore 대역. 호출 시점의 접속 환경 변수를 기록한다 (값 자체는 테스트 고정값)."""

    def __init__(self, apply_status="PASS", verify_status="PASS", side_effect=None):
        self.calls, self.apply_status, self.verify_status, self.side_effect = [], apply_status, verify_status, side_effect

    def __call__(self, args):
        env = {k: os.environ.get(k) for k in ("QDRANT_URL", "NEO4J_URI", "NEO4J_USER", "QDRANT_API_KEY", "NEO4J_PASSWORD")}
        self.calls.append((args.apply, args.verify_only, env))
        if self.side_effect:
            self.side_effect(args)
        if args.verify_only:
            return {"status": self.verify_status, "graph_totals": {"nodes": 1169, "relationships": 2752},
                    "cross_db": {"status": "PASS", "qdrant_points": 1139}}
        if args.apply:
            return {"status": self.apply_status}
        return {"status": "PREFLIGHT_PASS_NO_WRITES"}


def make_bundle(folder: Path, snapshot=b"sample_only snapshot", tool=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "b2.snapshot").write_bytes(snapshot)
    (folder / "restore_final_v140.py").write_bytes(tool or (wrap.ROOT / "scripts" / "restore_final_v140.py").read_bytes())
    package = {"collection": restore.COLLECTION, "dimensions": 1024, "points": 1139, "snapshot": "b2.snapshot",
               "snapshot_sha256": hashlib.sha256(snapshot).hexdigest(), "manifest_lf_sha256": "d" * 64,
               "vector_digest": "v" * 64, "source_commit": "s" * 40}
    (folder / "package.json").write_text(json.dumps(package), encoding="utf-8")
    names = ["b2.snapshot", "restore_final_v140.py", "package.json"]
    (folder / "SHA256SUMS").write_bytes("".join(f"{restore.file_sha(folder / n)}  {n}\n" for n in names).encode())
    return folder


class TargetAndSecretTests(unittest.TestCase):
    def test_only_fixed_targets_are_allowed(self):
        wrap.require_target_urls()
        for qdrant_url, neo4j_uri in (("http://127.0.0.1:56446", wrap.NEO4J_URI), ("http://127.0.0.1:6333", wrap.NEO4J_URI),
                                      (wrap.QDRANT_URL, "bolt://127.0.0.1:56487"), (wrap.QDRANT_URL, "bolt://127.0.0.1:7687"),
                                      (wrap.QDRANT_URL, "neo4j://127.0.0.1:56587"), ("http://10.0.0.5:56556", wrap.NEO4J_URI)):
            with self.subTest(qdrant=qdrant_url, neo4j=neo4j_uri), self.assertRaises(ValueError):
                wrap.require_target_urls(qdrant_url, neo4j_uri)
        self.assertTrue({6333, 36333, 56446, 7687, 17687, 18687, 56487} <= wrap.DENIED_PORTS)
        self.assertFalse({56556, 56587} & wrap.DENIED_PORTS)

    def test_secrets_must_be_dedicated_and_not_reused(self):
        self.assertEqual(wrap.read_secrets(ENV), VALUES)
        for environ, message in (({}, "없습니다"), ({wrap.QDRANT_KEY_ENV: "same", wrap.NEO4J_PASSWORD_ENV: "same"}, "달라야"),
                                 ({**ENV, "QDRANT_API_KEY": VALUES["qdrant"]}, "QDRANT_API_KEY"),
                                 ({**ENV, "ISSUE91_NEO4J_PASSWORD": VALUES["neo4j"]}, "ISSUE91_NEO4J_PASSWORD"),
                                 ({**ENV, "NEO4J_PASSWORD": VALUES["qdrant"]}, "NEO4J_PASSWORD")):
            with self.subTest(message), self.assertRaisesRegex(wrap.IsolationError, message):
                wrap.read_secrets(environ)

    def test_user_environment_is_overridden_only_during_restore(self):
        with patch.dict(os.environ, {"QDRANT_URL": "http://127.0.0.1:6333", "NEO4J_URI": "bolt://127.0.0.1:7687",
                                     "NEO4J_PASSWORD": "shared_sample_only"}, clear=False):
            with wrap.isolated_environment(wrap.ROOT, VALUES) as wanted:
                self.assertEqual((os.environ["QDRANT_URL"], os.environ["NEO4J_URI"]), (wrap.QDRANT_URL, wrap.NEO4J_URI))
                self.assertEqual(os.environ["NEO4J_PASSWORD"], VALUES["neo4j"])
                wrap.require_environment_unchanged(wanted)
                os.environ["NEO4J_URI"] = "bolt://127.0.0.1:7687"
                with self.assertRaisesRegex(wrap.IsolationError, "NEO4J_URI"):
                    wrap.require_environment_unchanged(wanted)
            self.assertEqual((os.environ["QDRANT_URL"], os.environ["NEO4J_URI"], os.environ["NEO4J_PASSWORD"]),
                             ("http://127.0.0.1:6333", "bolt://127.0.0.1:7687", "shared_sample_only"))


class DockerOwnershipTests(unittest.TestCase):
    def test_expected_project_containers_pass_with_read_only_commands(self):
        docker = good_docker()
        identity = wrap.verify_docker_ownership(docker)
        self.assertEqual(set(identity), {"qdrant", "neo4j"})
        self.assertTrue(all(call[0] in ("ps", "inspect") for call in docker.calls))

    def test_unexpected_docker_state_is_rejected(self):
        q, n = "q" * 64, "n" * 64
        cases = {
            "서비스가 아닙니다": [container(q, "qdrant", project="issue91-r2-qdrant-20261119"), container(n, "neo4j")],
            "정확히 1개": [container(q, "qdrant"), container("x" * 64, "qdrant", project="other"), container(n, "neo4j")],
            "이미지": [container(q, "qdrant", image="sha256:" + "0" * 64), container(n, "neo4j")],
            "포트 게시": [container(q, "qdrant", host_ip="0.0.0.0"), container(n, "neo4j")],
            "전용 볼륨": [container(q, "qdrant", volumes=["issue91-r2-qdrant-20261119_qdrant_storage"]), container(n, "neo4j")],
            "볼륨이 아닙": [container(q, "qdrant", mount_type="bind"), container(n, "neo4j")],
            "실행 중": [container(q, "qdrant", running=False), container(n, "neo4j")],
            "기대한 2개": [container(q, "qdrant"), container(n, "neo4j"),
                        {**container("z" * 64, "qdrant"), "NetworkSettings": {"Ports": {}}}],
            "0개": [container(n, "neo4j")],
        }
        for message, containers in cases.items():
            with self.subTest(message), self.assertRaisesRegex(wrap.IsolationError, message.replace("볼륨이 아닙", "전용 볼륨")):
                wrap.verify_docker_ownership(FakeDocker(containers))
        extra = [container(q, "qdrant", extra_ports={"6334/tcp": [{"HostIp": "127.0.0.1", "HostPort": "56557"}]}),
                 container(n, "neo4j")]
        with self.assertRaisesRegex(wrap.IsolationError, "포트 게시"):
            wrap.verify_docker_ownership(FakeDocker(extra))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bundle = make_bundle(Path(self.tmp.name) / "bundle")

    def run_mode(self, mode, ctx, restore_fn, **kwargs):
        return wrap.run(mode, wrap.ROOT, self.bundle, ctx, restore_fn=restore_fn, **kwargs)

    def test_preflight_uses_only_fixed_targets_and_does_not_write(self):
        ctx, qdrant, runner = make_ctx()
        fake = FakeRestore()
        with patch.dict(os.environ, {"QDRANT_URL": "http://127.0.0.1:6333", "NEO4J_URI": "bolt://127.0.0.1:7687"}):
            output = self.run_mode("preflight", ctx, fake)
            self.assertEqual(os.environ["QDRANT_URL"], "http://127.0.0.1:6333")   # 호출 뒤 원래 값 복원
        apply, verify_only, env = fake.calls[0]
        self.assertEqual((apply, verify_only), (False, False))
        self.assertEqual((env["QDRANT_URL"], env["NEO4J_URI"], env["NEO4J_USER"]), (wrap.QDRANT_URL, wrap.NEO4J_URI, "neo4j"))
        self.assertEqual((env["QDRANT_API_KEY"], env["NEO4J_PASSWORD"]), (VALUES["qdrant"], VALUES["neo4j"]))
        self.assertEqual((output["db_writes"], output["servers"]["state"]), (0, "empty"))
        self.assertRegex(output["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual((qdrant.writes, runner.writes()), ([], []))
        self.assertNotIn(VALUES["qdrant"], json.dumps(output))
        self.assertNotIn(VALUES["neo4j"], json.dumps(output))

    def test_non_empty_targets_or_disabled_auth_stop_before_restore(self):
        foreign_runner = VerifyNeo4j()
        foreign_runner.nodes[("Policy", "POL_SAMPLE_ONLY")] = {"policy_id": "POL_SAMPLE_ONLY"}
        with_collection = VerifyQdrant({})
        with_alias = VerifyQdrant()
        with_alias.aliases = ["sample_only"]
        cases = {"neo4j_foreign_data": make_ctx(runner=foreign_runner)[0],
                 "qdrant_restored_collection": make_ctx(qdrant=with_collection)[0],
                 "qdrant_foreign_collections": make_ctx(qdrant=with_alias)[0],
                 "401이 아닙니다": make_ctx(http_status=200)[0],
                 "인증 꺼짐": make_ctx(accept_any_password=True)[0]}
        for message, ctx in cases.items():
            fake = FakeRestore()
            with self.subTest(message), self.assertRaisesRegex(wrap.IsolationError, message):
                self.run_mode("preflight", ctx, fake)
            self.assertEqual(fake.calls, [])

    def test_wrong_versions_or_database_stop(self):
        runner = VerifyNeo4j(version="5.26.0")
        named = VerifyNeo4j()
        named.database_name = "system"
        for message, ctx in (("Qdrant", make_ctx(qdrant=VerifyQdrant(version="1.13.0"))[0]),
                             ("Neo4j", make_ctx(runner=runner)[0]), ("기본 DB", make_ctx(runner=named)[0])):
            with self.subTest(message), self.assertRaisesRegex(wrap.IsolationError, message):
                self.run_mode("preflight", ctx, FakeRestore())

    def test_apply_requires_flags_and_matching_fingerprint(self):
        ctx = make_ctx()[0]
        fake = FakeRestore()
        for options, message in ((dict(), "confirm-write"), (dict(confirm_write=True), "expect-fingerprint"),
                                 (dict(confirm_write=True, expected_fingerprint="0" * 64), "fingerprint")):
            with self.subTest(options), self.assertRaisesRegex(wrap.IsolationError, message):
                self.run_mode("apply", ctx, fake, **options)
        self.assertEqual(fake.calls, [])

    def apply_ok(self, ctx, fake):
        fp = self.run_mode("preflight", ctx, FakeRestore())["fingerprint"]
        with patch.object(fi, "verify_full_index", return_value={"passed": True, "count": 1139}):
            return self.run_mode("apply", ctx, fake, confirm_write=True, expected_fingerprint=fp)

    def test_apply_calls_restore_once_then_read_only_verification(self):
        ctx = make_ctx()[0]
        fake = FakeRestore()
        output = self.apply_ok(ctx, fake)
        self.assertEqual([(a, v) for a, v, _ in fake.calls], [(True, False), (False, True)])
        self.assertTrue(all(env["QDRANT_URL"] == wrap.QDRANT_URL and env["NEO4J_URI"] == wrap.NEO4J_URI
                            for _, _, env in fake.calls))
        self.assertTrue(output["post_verify"]["ok"])

    def test_failed_restore_is_not_pass_and_not_retried(self):
        for fake, message in ((FakeRestore(apply_status="FAILED"), "FAILED"),
                              (FakeRestore(verify_status="FAILED"), "복원 후 검증 실패")):
            ctx = make_ctx()[0]
            with self.subTest(message), self.assertRaisesRegex(wrap.IsolationError, message):
                self.apply_ok(ctx, fake)
            self.assertEqual(sum(1 for apply, _, _ in fake.calls if apply), 1)
        ctx = make_ctx()[0]
        with self.assertRaisesRegex(wrap.IsolationError, "복원 후 검증 실패"):
            fp = self.run_mode("preflight", ctx, FakeRestore())["fingerprint"]
            with patch.object(fi, "verify_full_index", return_value={"passed": False}):
                self.run_mode("apply", ctx, FakeRestore(), confirm_write=True, expected_fingerprint=fp)

    def test_target_change_during_restore_is_detected(self):
        ctx = make_ctx()[0]

        def swap_env(args):
            if args.apply:
                os.environ["QDRANT_URL"] = "http://127.0.0.1:6333"

        with self.assertRaisesRegex(wrap.IsolationError, "QDRANT_URL"):
            self.apply_ok(ctx, FakeRestore(side_effect=swap_env))
        ctx2 = make_ctx()[0]

        def swap_container(args):
            if args.apply:
                ctx2.docker.containers["q" * 64]["State"]["StartedAt"] = "2026-11-19T09:99:99Z"

        with self.assertRaisesRegex(wrap.IsolationError, "컨테이너가 바뀌"):
            self.apply_ok(ctx2, FakeRestore(side_effect=swap_container))

    def test_verify_only_requires_complete_target(self):
        fake = FakeRestore()
        with self.assertRaisesRegex(wrap.IsolationError, "empty"):
            self.run_mode("verify-only", make_ctx()[0], fake)
        runner = VerifyNeo4j()
        lg.load_graph(GRAPH, runner, batch_size=500, verify=False)
        qdrant = VerifyQdrant({})
        ctx = make_ctx(qdrant=qdrant, runner=runner)[0]
        with patch.object(fi, "verify_full_index", return_value={"passed": True}):
            output = self.run_mode("verify-only", ctx, fake)
        self.assertEqual((output["servers"]["state"], output["db_writes"]), ("complete", 0))
        self.assertEqual([(a, v) for a, v, _ in fake.calls], [(False, True)])

    def test_bundle_source_mismatch_stops(self):
        ctx = make_ctx()[0]
        tampered = make_bundle(Path(self.tmp.name) / "tampered")
        (tampered / "b2.snapshot").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            wrap.run("preflight", wrap.ROOT, tampered, ctx, restore_fn=FakeRestore())
        other_tool = make_bundle(Path(self.tmp.name) / "tool", tool=b"# sample_only other tool\n")
        with self.assertRaisesRegex(wrap.IsolationError, "restore_final_v140.py"):
            wrap.run("preflight", wrap.ROOT, other_tool, ctx, restore_fn=FakeRestore())
        with self.assertRaisesRegex(wrap.IsolationError, "--repo"):
            wrap.run("preflight", Path(self.tmp.name), self.bundle, ctx, restore_fn=FakeRestore())


class CliTests(unittest.TestCase):
    def test_cli_failure_is_non_zero_without_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = make_bundle(Path(folder) / "bundle")
            ctx = make_ctx(http_status=200)[0]
            buffer = io.StringIO()
            with patch.dict(os.environ, ENV), contextlib.redirect_stdout(buffer):
                code = wrap.main(["--bundle", str(bundle)], ctx_factory=lambda values: ctx)
            output = json.loads(buffer.getvalue())
        self.assertEqual((code, output["status"]), (1, "FAILED"))
        self.assertNotIn(VALUES["qdrant"], buffer.getvalue())
        self.assertNotIn(VALUES["neo4j"], buffer.getvalue())

    def test_cli_preflight_success_and_apply_flags(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = make_bundle(Path(folder) / "bundle")
            ctx = make_ctx()[0]
            buffer = io.StringIO()
            with patch.dict(os.environ, ENV), patch.object(restore, "restore", FakeRestore()), \
                    contextlib.redirect_stdout(buffer):
                code = wrap.main(["--bundle", str(bundle)], ctx_factory=lambda values: ctx)
            self.assertEqual((code, json.loads(buffer.getvalue())["status"]), (0, "PASS"))
            for extra in (["--apply"], ["--apply", "--confirm-write"], ["--apply", "--verify-only"]):
                with self.subTest(extra=extra), self.assertRaises(SystemExit):
                    wrap.main(["--bundle", str(bundle), *extra])


class VerifyComposeStaticTests(unittest.TestCase):
    PATH = wrap.ROOT / "docs" / "development" / "issue91_r2_verify.compose.yaml"

    def test_project_ports_images_auth_and_volumes(self):
        lines = [line.split("#", 1)[0].rstrip() for line in self.PATH.read_text(encoding="utf-8").splitlines()]
        body = [line for line in lines if line.strip()]
        text = "\n".join(body)
        self.assertIn(f"name: {wrap.PROJECT}", body)
        ports = [line.strip() for line in body if line.strip().startswith("- \"")]
        self.assertEqual(ports, [f"- \"127.0.0.1:{wrap.SERVICES['qdrant']['port']}:6333\"",
                                 f"- \"127.0.0.1:{wrap.SERVICES['neo4j']['port']}:7687\""])
        self.assertIn(wrap.SERVICES["qdrant"]["image"], text)
        self.assertIn(wrap.SERVICES["neo4j"]["image"], text)
        self.assertRegex(text, r"QDRANT__SERVICE__API_KEY: \$\{" + wrap.QDRANT_KEY_ENV + r":\?[^}]+\}")
        self.assertRegex(text, r"NEO4J_AUTH: neo4j/\$\{" + wrap.NEO4J_PASSWORD_ENV + r":\?[^}]+\}")
        for forbidden in ("external", "container_name", "network_mode", "networks:", ":-", "7474",
                          "${QDRANT_API_KEY", "${NEO4J_PASSWORD"):      # 공유 변수 이름 직접 사용 금지
            self.assertNotIn(forbidden, text)
        mounts = [line.strip() for line in body if line.strip().startswith("- ") and ":/" in line]
        self.assertEqual(mounts, ["- qdrant_storage:/qdrant/storage", "- neo4j_data:/data", "- neo4j_logs:/logs"])
        expected_volumes = {f"{wrap.PROJECT}_{m.split(':')[0][2:]}" for m in mounts}
        self.assertEqual(expected_volumes, wrap.SERVICES["qdrant"]["volumes"] | wrap.SERVICES["neo4j"]["volumes"])
        self.assertFalse(set(ports) & {f"- \"127.0.0.1:{p}:6333\"" for p in wrap.DENIED_PORTS})


if __name__ == "__main__":
    unittest.main()
