"""Issue #105 r3 독립 복원 보호 래퍼(scripts/restore_r3_isolated.py) 테스트.

FakeDocker·VerifyQdrant·VerifyNeo4j(r2 래퍼 테스트 더블)와 sample_only 번들만 쓴다. Docker·DB 접속 없음.
"""
import copy
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from r3_fixtures import final_sample_pins, pending_pins, stage1_pins  # noqa: E402
from test_restore_final_v140_r3 import Bundle  # noqa: E402
from test_restore_r2_isolated import FakeDocker, VerifyNeo4j, VerifyQdrant  # noqa: E402

from database.neo4j import load_graph as lg  # noqa: E402
from scripts import restore_final_v140_r3 as restore_r3  # noqa: E402
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


def make_ctx(docker=None, qdrant=None, runner=None):
    from neo4j.exceptions import AuthError
    qdrant = qdrant if qdrant is not None else VerifyQdrant()

    def neo4j_factory(password):
        if password != VALUES["neo4j"]:
            raise AuthError("sample_only")
        return SimpleNamespace(close=lambda: None)

    return wrap.Context(dict(VALUES), docker=docker or good_docker(), qdrant_factory=lambda values: qdrant,
                        neo4j_factory=neo4j_factory, runner_factory=lambda driver: runner or VerifyNeo4j(),
                        http_get=lambda url, timeout: SimpleNamespace(status_code=401), graph=GRAPH)


B2, B3 = wrap.CANONICAL["b2"], wrap.CANONICAL["b3"]
TB2, TB3 = restore_r3.temp_collection_name(B2, "a" * 32), restore_r3.temp_collection_name(B3, "b" * 32)


class AliasQdrant(VerifyQdrant):
    """alias 매핑까지 돌려주는 VerifyQdrant (r2 테스트 더블은 alias 대상을 "x"로만 준다)."""

    def __init__(self, collections, aliases):
        super().__init__({})
        self.collections = {name: {} for name in collections}
        self.alias_map = dict(aliases)

    def get_aliases(self):
        return SimpleNamespace(aliases=[SimpleNamespace(alias_name=a, collection_name=c)
                                        for a, c in self.alias_map.items()])


def published_qdrant():
    return AliasQdrant([TB2, TB3], {B2: TB2, B3: TB3})


def loaded_runner():
    runner = VerifyNeo4j()
    lg.load_graph(GRAPH, runner, batch_size=500, verify=False)        # sample 그래프를 fake Neo4j에만 적재
    return runner


class FakeRestore:
    def __init__(self, apply_status="PASS", verify_status="PASS"):
        self.calls, self.apply_status, self.verify_status = [], apply_status, verify_status

    def __call__(self, args, settings=None, pins=None, **kwargs):
        self.calls.append({"apply": args.apply, "verify_only": args.verify_only, "settings": dict(settings),
                           "pins": pins, "kwargs": dict(kwargs)})
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

    def test_secrets_with_whitespace_or_control_characters_are_refused_without_echoing_them(self):
        """#111 P1-D M2: 공백·제어 문자가 섞인 인증값은 거부하고, 오류에는 변수 이름만 싣는다."""
        for label, bad in (("leading_space", " sample_only_key_a"), ("trailing_space", "sample_only_key_a "),
                           ("inner_space", "sample_only key_a"), ("newline", "sample_only_key_a\n"),
                           ("crlf", "sample_only\r\nkey_a"), ("tab", "sample_only\tkey_a"), ("nul", "sample_only\x00a"),
                           ("del", "sample_only\x7fa"), ("nbsp", "sample_only a")):
            for env_name in (wrap.QDRANT_KEY_ENV, wrap.NEO4J_PASSWORD_ENV):
                with self.subTest(value=label, env=env_name):
                    with self.assertRaises(wrap.IsolationError) as caught:
                        wrap.read_secrets({**ENV, env_name: bad})
                    self.assertIn("공백·제어 문자", str(caught.exception))
                    self.assertIn(env_name, str(caught.exception))
                    self.assertFalse(bad.strip() in str(caught.exception))
        normal = {wrap.QDRANT_KEY_ENV: "Sample_Only-key.0123456789~", wrap.NEO4J_PASSWORD_ENV: "sample_only_PW_9876"}
        self.assertEqual(wrap.read_secrets(normal), {"qdrant": normal[wrap.QDRANT_KEY_ENV],
                                                      "neo4j": normal[wrap.NEO4J_PASSWORD_ENV]})

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
        if mode == "apply":                       # P1-C: 테스트 잠금은 임시 폴더에만 만든다 (%LOCALAPPDATA% 사용 안 함)
            kwargs.setdefault("lock_path", Path(self.tmp.name) / "locks" / "run.lock")
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

    def test_apply_passes_armed_write_guard_only_to_the_apply_call(self):
        """#111 P1-A: 래퍼가 검사를 통과한 뒤 만든 허가 객체를 apply 호출에만 넘긴다. preflight·verify-only에는 없다."""
        fake = FakeRestore()
        ctx = make_ctx()
        current = ctx.snapshot()[2]
        self.run_mode("apply", ctx=ctx, restore_fn=fake, confirm_write=True, expected_fingerprint=current)
        apply_call, verify_call = fake.calls
        guard = apply_call["kwargs"]["write_guard"]
        self.assertIsInstance(guard, wrap.IsolatedWriteGuard)
        restore_r3.require_write_guard(guard)                     # 본체 진입 검증을 통과하는 허가
        self.assertEqual(verify_call["kwargs"], {})
        # P1-C: guard의 lock_checker는 run()이 잡은 실제 RunLock이다. run()이 끝나면 잠금이 해제되어 같은 guard는 거부된다
        with self.assertRaisesRegex(wrap.IsolationError, "실행 잠금을 보유하고 있지 않습니다"):
            guard.check("before_qdrant_upload:b2", restore_r3.stage_expectation({}, {}, [], first=True))
        preflight = FakeRestore()
        self.run_mode("preflight", restore_fn=preflight)
        self.assertEqual(preflight.calls[0]["kwargs"], {})
        refused = FakeRestore()
        with self.assertRaisesRegex(wrap.IsolationError, "fingerprint"):   # 검사 실패 시 허가 객체도 만들지 않는다
            self.run_mode("apply", restore_fn=refused, confirm_write=True, expected_fingerprint="0" * 64)
        self.assertEqual(refused.calls, [])

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

        def swap(args, settings=None, pins=None, write_guard=None):
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
        both.collections = {name: {} for name in wrap.EXPECTED_COLLECTIONS}       # 옛 방식(정식 이름 직접 복원)
        state = make_ctx(qdrant=both).snapshot()[1]
        self.assertEqual(state["qdrant"]["state"], "not_published")
        with self.assertRaisesRegex(wrap.IsolationError, "complete"):
            self.run_mode("verify-only", ctx=make_ctx(qdrant=both), restore_fn=FakeRestore())
        aliased = published_qdrant()
        with self.assertRaisesRegex(wrap.IsolationError, "비어 있지 않습니다"):    # published DB에는 다시 쓰지 않음
            self.run_mode("preflight", ctx=make_ctx(qdrant=aliased), restore_fn=FakeRestore())

    def test_verify_only_is_refused_outside_the_published_state(self):
        cases = {
            "direct_public_collections": AliasQdrant([B2, B3], {}),
            "temps_without_aliases": AliasQdrant([TB2, TB3], {}),
            "partial_publish": AliasQdrant([TB2, TB3], {B2: TB2}),
            "swapped_aliases": AliasQdrant([TB2, TB3], {B2: TB3, B3: TB2}),
            "extra_alias": AliasQdrant([TB2, TB3], {B2: TB2, B3: TB3, "sample_only_alias": TB2}),
            "extra_collection": AliasQdrant([TB2, TB3, "exp_other_sample_only"], {B2: TB2, B3: TB3}),
            "public_collection_and_alias": AliasQdrant([TB2, TB3, B3], {B2: TB2, B3: TB3}),
        }
        for name, qdrant in cases.items():
            with self.subTest(state=name):
                fake = FakeRestore()
                ctx = make_ctx(qdrant=qdrant, runner=loaded_runner())
                self.assertEqual(ctx.snapshot()[1]["qdrant"]["state"], "not_published")
                with self.assertRaisesRegex(wrap.IsolationError, "complete"):
                    self.run_mode("verify-only", ctx=ctx, restore_fn=fake)
                self.assertEqual(fake.calls, [])
        fake = FakeRestore()                                            # Qdrant는 published여도 Neo4j가 비면 거부
        with self.assertRaisesRegex(wrap.IsolationError, "complete"):
            self.run_mode("verify-only", ctx=make_ctx(qdrant=published_qdrant()), restore_fn=fake)
        self.assertEqual(fake.calls, [])

    def test_verify_only_passes_in_the_published_state(self):
        fake = FakeRestore()
        ctx = make_ctx(qdrant=published_qdrant(), runner=loaded_runner())
        result = self.run_mode("verify-only", ctx=ctx, restore_fn=fake)
        self.assertEqual((result["servers"]["state"], result["db_writes"]), ("complete", 0))
        self.assertEqual(result["servers"]["qdrant"]["state"], "published")
        self.assertEqual(result["servers"]["qdrant"]["aliases"], {B2: TB2, B3: TB3})
        self.assertEqual([(c["apply"], c["verify_only"]) for c in fake.calls], [(False, True)])

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

        def partial(args, settings=None, pins=None, write_guard=None):
            calls.append((args.apply, args.verify_only))
            raise ValueError("Snapshot upload failed: HTTP 500 (exp_b3_qwen3emb06b_v140_v1)")
        with self.assertRaisesRegex(ValueError, "Snapshot upload failed"):
            self.run_mode("apply", ctx=ctx, restore_fn=partial, confirm_write=True, expected_fingerprint=current)
        self.assertEqual(calls, [(True, False)])                       # verify-only로 넘어가지 않는다


def first_stage():
    """B2 업로드 직전 기대 상태 (빈 서버·preflight fingerprint 재확인)."""
    return restore_r3.stage_expectation({}, {}, [], first=True)


class IsolatedWriteGuardTests(unittest.TestCase):
    """#111 P1-B: IsolatedWriteGuard.check 단독 검증. Context는 실제 클래스(FakeDocker·VerifyQdrant·VerifyNeo4j)."""

    def make_guard(self, ctx=None, *, lock_checker=lambda: True, fingerprint="current", run_id="d" * 32):
        ctx = ctx or make_ctx()
        identity, _, current = ctx.snapshot()
        guard = wrap.IsolatedWriteGuard(run_id, ctx=ctx, identity=identity,
                                        preflight_fingerprint=current if fingerprint == "current" else fingerprint,
                                        lock_checker=lock_checker)
        return ctx, guard

    def test_valid_state_passes_and_reads_fresh_state(self):
        _, guard = self.make_guard()
        guard.check("before_qdrant_upload:b2", first_stage())                 # 예외 없음

    def test_container_id_or_start_time_change_is_refused(self):
        for field, value in (("StartedAt", "2026-11-22T01:00:00Z"), ("Id", None)):
            for cid in ("q" * 64, "n" * 64):
                with self.subTest(container=cid[0], field=field):
                    docker = good_docker()
                    ctx, guard = self.make_guard(make_ctx(docker=docker))
                    if field == "StartedAt":
                        docker.containers[cid]["State"]["StartedAt"] = value
                    else:                                                    # 같은 포트에 새 컨테이너 (ID 변경)
                        new = "r" * 64
                        docker.containers[new] = {**docker.containers.pop(cid), "Id": new}
                    with self.assertRaisesRegex(wrap.IsolationError, "컨테이너 식별"):
                        guard.check("before_qdrant_upload:b3", restore_r3.stage_expectation({}, {}, []))

    def test_preflight_fingerprint_is_rechecked_before_the_first_write(self):
        _, guard = self.make_guard(fingerprint="0" * 64)
        with self.assertRaisesRegex(wrap.IsolationError, "fingerprint"):
            guard.check("before_qdrant_upload:b2", first_stage())
        missing = {k: v for k, v in first_stage().items() if k != "require_preflight_fingerprint"}
        with self.assertRaisesRegex(wrap.IsolationError, "fingerprint"):   # 표시가 없으면 재확인하는 쪽(fail-closed)
            guard.check("before_qdrant_upload:b2", missing)

    def test_qdrant_and_neo4j_state_mismatch_is_refused(self):
        cases = {
            "external_collection": (VerifyQdrant(), lambda q, r: q.collections.update({"exp_other_sample_only": {}}),
                                    "collections"),
            "canonical_collection": (VerifyQdrant(), lambda q, r: q.collections.update({B2: {}}), "collections"),
            "alias": (VerifyQdrant(), lambda q, r: q.aliases.append(B2), "aliases"),
            "neo4j_nodes": (VerifyQdrant(), lambda q, r: lg.load_graph(GRAPH, r, batch_size=500, verify=False),
                            "neo4j_totals"),
        }
        for name, (qdrant, change, field) in cases.items():
            with self.subTest(case=name):
                runner = VerifyNeo4j()
                ctx, guard = self.make_guard(make_ctx(qdrant=qdrant, runner=runner), fingerprint=None)
                change(qdrant, runner)
                # 첫 단계가 아닌 기대값(fingerprint 재확인 없음)으로 서버 상태 비교만 본다
                with self.assertRaisesRegex(wrap.IsolationError, field):
                    guard.check("before_qdrant_upload:b3", restore_r3.stage_expectation({}, {}, []))

    def test_missing_collection_or_incomplete_expectation_is_refused(self):
        _, guard = self.make_guard()
        expected = restore_r3.stage_expectation({"b2": TB2}, {"b2": {"points": 1, "vector_digest": "0" * 64}}, ["b2"])
        # 기대 컬렉션이 서버에 없음 → 조회 실패를 명확한 IsolationError로 (원래 예외 종류는 메시지에만)
        with self.assertRaisesRegex(wrap.IsolationError, r"서버 상태 조회 실패\(\w+\) → 쓰지 않음 \(before_qdrant_upload:b3\)"):
            guard.check("before_qdrant_upload:b3", expected)
        for field in wrap.GUARD_FIELDS:
            with self.subTest(missing=field), self.assertRaisesRegex(wrap.IsolationError, "불완전"):
                guard.check("before_qdrant_upload:b3", {k: v for k, v in first_stage().items() if k != field})
        for bad in (None, [], "x"):
            with self.subTest(expected=bad), self.assertRaisesRegex(wrap.IsolationError, "불완전"):
                guard.check("before_qdrant_upload:b3", bad)

    def test_armed_and_run_id_are_revalidated_on_every_check(self):
        for label, attribute, value, error, message in (
                ("disarmed", "armed", False, ValueError, "not armed"),
                ("truthy_armed", "armed", 1, ValueError, "not armed"),
                ("other_valid_run_id", "run_id", "f" * 32, wrap.IsolationError, "run_id가 발급"),
                ("invalid_run_id", "run_id", "d" * 32 + "\n", ValueError, "32 lowercase hex")):
            with self.subTest(case=label):
                ctx, guard = self.make_guard()
                guard.check("before_qdrant_upload:b2", first_stage())         # 처음엔 통과
                setattr(guard, attribute, value)
                calls = []
                ctx.qdrant_factory = lambda values: calls.append("qdrant")
                with self.assertRaisesRegex(error, message):
                    guard.check("before_qdrant_upload:b2", first_stage())
                self.assertEqual(calls, [])                                    # 서버 조회 전에 거부

    def test_lock_checker_missing_false_raising_or_not_true_is_refused_first(self):
        def boom():
            raise OSError("sample_only")
        for label, checker in (("missing", None), ("not_callable", True), ("false", lambda: False),
                               ("raises", boom), ("one", lambda: 1), ("none", lambda: None), ("text", lambda: "yes")):
            with self.subTest(lock_checker=label):
                docker = good_docker()
                ctx, guard = self.make_guard(make_ctx(docker=docker), lock_checker=checker)
                before = len(docker.calls) if hasattr(docker, "calls") else None
                touched = []
                ctx.identity = lambda: touched.append("identity")
                ctx.qdrant_factory = lambda values: touched.append("qdrant")
                with self.assertRaisesRegex(wrap.IsolationError, "잠금"):
                    guard.check("before_qdrant_upload:b2", first_stage())
                self.assertEqual(touched, [])                                  # Docker·DB 조회 전에 거부
                if before is not None:
                    self.assertEqual(len(docker.calls), before)
        seen = []
        _, guard = self.make_guard(lock_checker=lambda: seen.append("lock") or True)
        guard.check("before_qdrant_upload:b2", first_stage())
        self.assertEqual(seen, ["lock"])                                       # check마다 잠금 확인 1회

    def test_alignment_changes_outside_allowed_fields_or_inconsistent_are_refused(self):
        qdrant = VerifyQdrant({"sample_only_point": {"payload": {"category": "a", "region": "r"}, "vector": [0.0]}})
        name = next(iter(qdrant.collections))
        _, guard = self.make_guard(make_ctx(qdrant=qdrant), fingerprint=None)

        def aligning(changes, digests=None):
            expected = restore_r3.stage_expectation({}, {}, [])
            expected.update(collections=[name], alignment_changes={name: changes},
                            projected_payload_digests=digests if digests is not None else {name: "0" * 64})
            return expected
        for label, changes in (("other_field", [["sample_only_point", {"region": "x"}]]),
                               ("mixed_fields", [["sample_only_point", {"category": "b", "region": "x"}]]),
                               ("missing_point", [["sample_only_missing", {"category": "b"}]])):
            with self.subTest(changes=label), self.assertRaisesRegex(wrap.IsolationError, "허용 범위"):
                guard.check("before_payload_alignment:b2", aligning(changes))
        with self.assertRaisesRegex(wrap.IsolationError, "불완전"):               # 변경 목록과 기대 digest 짝이 다름
            guard.check("before_payload_alignment:b2", aligning([], digests={}))
        allowed = [["sample_only_point", {"category": "b"}]]
        with self.assertRaisesRegex(wrap.IsolationError, r"\['projected_payload_digests'\]"):
            guard.check("before_payload_alignment:b2", aligning(allowed))        # 허용 필드여도 결과 digest가 다르면 거부
        projected = restore_r3.project_alignment(restore_r3.scan(qdrant, name), allowed)
        guard.check("before_payload_alignment:b2",
                    aligning(allowed, {name: restore_r3.payload_digest(wrap.restore, projected)}))

    def counting_ctx(self, log, *, fail=None):
        """연결 생성·종료를 log에 기록하는 실제 Context. fail: 'neo4j_open'·'neo4j_read'·'qdrant_scan' 중 하나를
        켜면(fail["on"]=True) check 중 그 지점에서 예외를 낸다."""
        from neo4j.exceptions import AuthError
        qdrant, runner = VerifyQdrant(), VerifyNeo4j()
        fail = fail if fail is not None else {}

        class Handle:
            def __init__(self):
                log.append("qdrant_open")

            def __getattr__(self, item):
                if item == "scroll" and fail.get("on") and fail.get("at") == "qdrant_scan":
                    raise AttributeError("sample_only scan failure")
                return getattr(qdrant, item)

            def close(self):
                log.append("qdrant_close")

        def neo4j_factory(password):
            if password != VALUES["neo4j"]:
                raise AuthError("sample_only")
            if fail.get("on") and fail.get("at") == "neo4j_open":
                raise RuntimeError("sample_only neo4j unavailable")
            log.append("neo4j_open")
            return SimpleNamespace(close=lambda: log.append("neo4j_close"))

        def runner_factory(driver):
            if fail.get("on") and fail.get("at") == "neo4j_read":
                return SimpleNamespace(read_rows=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sample_only")))
            return runner

        return wrap.Context(dict(VALUES), docker=good_docker(), qdrant_factory=lambda values: Handle(),
                            neo4j_factory=neo4j_factory, runner_factory=runner_factory,
                            http_get=lambda url, timeout: SimpleNamespace(status_code=401), graph=GRAPH)

    def test_each_check_opens_new_connections_and_closes_them(self):
        log = []
        _, guard = self.make_guard(self.counting_ctx(log))
        log.clear()
        stage = restore_r3.stage_expectation({}, {}, [])
        guard.check("before_qdrant_upload:b3", stage)
        guard.check("before_neo4j_load", stage)
        self.assertEqual(log, ["qdrant_open", "neo4j_open", "neo4j_close", "qdrant_close"] * 2)   # 단계마다 새 연결
        log.clear()
        guard.check("before_qdrant_upload:b2", first_stage())          # 첫 단계: fingerprint 재확인 연결도 닫힘
        self.assertEqual(log.count("qdrant_open"), log.count("qdrant_close"))
        self.assertEqual(log.count("neo4j_open"), log.count("neo4j_close"))
        self.assertEqual(log.count("qdrant_open"), 2)

    def test_connections_are_closed_when_reading_fails(self):
        cases = {"neo4j_open": ["qdrant_open", "qdrant_close"],
                 "neo4j_read": ["qdrant_open", "neo4j_open", "neo4j_close", "qdrant_close"],
                 "qdrant_scan": ["qdrant_open", "neo4j_open", "neo4j_close", "qdrant_close"]}
        expected = restore_r3.stage_expectation({"b2": wrap.EXPECTED_COLLECTIONS[0]},
                                                {"b2": {"points": 0, "vector_digest": "0" * 64}}, ["b2"])
        for point, opened_closed in cases.items():
            with self.subTest(fail=point):
                log, fail = [], {"at": point}
                _, guard = self.make_guard(self.counting_ctx(log, fail=fail))
                log.clear()
                fail["on"] = True
                with self.assertRaisesRegex(wrap.IsolationError, r"서버 상태 조회 실패\(\w+\)"):
                    guard.check("before_qdrant_upload:b3", expected)
                self.assertEqual(log, opened_closed)

    def test_guard_without_context_is_refused(self):
        guard = wrap.IsolatedWriteGuard("d" * 32, lock_checker=lambda: True)
        with self.assertRaisesRegex(wrap.IsolationError, "컨텍스트"):
            guard.check("before_qdrant_upload:b2", first_stage())

    def test_run_connects_the_real_run_lock_to_the_guard(self):
        """P1-C (P1-B의 '잠금 미연결 = 거부' 테스트를 대체): run()의 apply guard는 실제 RunLock.is_held를 쓴다.
        run()에는 lock_checker를 바꿔 끼우는 인자가 없다(테스트·실제 main 모두 실제 잠금)."""
        import inspect
        self.assertNotIn("lock_checker", inspect.signature(wrap.run).parameters)
        ctx = make_ctx()
        current = ctx.snapshot()[2]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bundle = Bundle(Path(tmp.name))
        lock_path = Path(tmp.name) / "locks" / "run.lock"
        stages = []

        def restore_fn(args, settings=None, pins=None, write_guard=None):
            if args.apply:
                self.assertIs(write_guard._lock_checker(), True)               # 보유 중
                write_guard.check("before_qdrant_upload:b2", first_stage())
                stages.append("checked")
            return {"status": "PASS"}
        result = wrap.run("apply", wrap.ROOT, bundle.folder, ctx, confirm_write=True, expected_fingerprint=current,
                          restore_fn=restore_fn, pins=bundle.pins, lock_path=lock_path)
        self.assertEqual((result["restore_apply"]["status"], stages), ("PASS", ["checked"]))
        self.assertEqual(result["run_lock"]["owner"], wrap.read_lock_owner(lock_path))  # 보고서 = 파일 소유자 정보


CHILD_LOCK_SCRIPT = r"""
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts import restore_r3_isolated as w
lock = w.RunLock(Path(sys.argv[2]), {"run_id": "e" * 32, "pid": os.getpid(), "role": "sample_only_child"})
try:
    lock.acquire()
except w.RunLockError:
    print("REFUSED", flush=True)
    sys.exit(3)
print("LOCKED", flush=True)
mode = sys.argv[3]
if mode == "release":
    lock.release()
    print("RELEASED", flush=True)
elif mode == "exit_without_release":
    os._exit(0)
else:
    sys.stdin.readline()                     # hold: 부모가 stdin을 닫거나 이 자식만 강제 종료할 때까지 보유
"""


class RunLockTests(unittest.TestCase):
    """#111 P1-C (T-C1~C8): 실제 OS 파일 잠금. 잠금 파일은 테스트 임시 폴더에만 만들고, 강제 종료는 이 테스트가
    직접 띄운 자식 Python 프로세스에만 한다 (Docker·DB·다른 프로세스 접근 없음)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "locks" / "issue105-r3-verify-sample_only.lock"

    def lock(self, run_id="d" * 32, path=None):
        lock = wrap.RunLock(path or self.path, {"run_id": run_id, "pid": os.getpid(), "role": "sample_only_parent"})
        self.addCleanup(lock.release)
        return lock

    def make_bundle(self):
        folder = Path(self.tmp.name) / "bundle"
        if not folder.exists():
            folder.mkdir()
            self.bundle_obj = Bundle(folder)
        return self.bundle_obj

    def child(self, mode="hold", expect="LOCKED"):
        """잠금을 잡는 자식 Python. Windows venv의 python.exe는 실제 인터프리터를 다시 띄우는 실행기라 Popen.pid가
        잠금 보유 프로세스가 아니다 → 기본 인터프리터(sys._base_executable)를 직접 띄워 pid = 보유자로 맞춘다
        (c2에서 소유자 정보의 pid = proc.pid로 확인)."""
        import subprocess
        executable = getattr(sys, "_base_executable", None) or sys.executable
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p and os.path.isdir(p))}
        proc = subprocess.Popen([executable, "-c", CHILD_LOCK_SCRIPT, str(wrap.ROOT), str(self.path), mode],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=env)

        def stop():
            if proc.poll() is None:
                proc.kill()                                   # 이 테스트가 만든 자식만 종료
            proc.wait(timeout=30)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()
        self.addCleanup(stop)
        line = proc.stdout.readline().strip()
        self.assertEqual(line, expect, proc.stderr.read() if not line else line)
        return proc

    def assert_os_lock_held(self):
        """등록부와 무관하게 OS 잠금 자체가 잡혀 있는지: 새 핸들로 0번 바이트 잠금이 실패해야 한다."""
        fd = os.open(self.path, os.O_RDWR | getattr(os, "O_BINARY", 0))
        try:
            with self.assertRaises(OSError):
                wrap._os_lock(fd)
        finally:
            os.close(fd)

    def acquire_within(self, seconds):
        import time
        deadline, attempts = time.monotonic() + seconds, 0
        while True:
            attempts += 1
            lock = wrap.RunLock(self.path, {"run_id": "f" * 32})
            try:
                lock.acquire()
                self.addCleanup(lock.release)
                return lock, attempts
            except wrap.RunLockError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)                                 # 테스트만 짧은 폴링 (래퍼 자체는 대기·재시도 없음)

    def test_default_path_is_fixed_per_project_and_port(self):
        if os.name == "nt":
            environ = {"LOCALAPPDATA": r"C:\sample_only\AppData\Local"}
            self.assertEqual(wrap.default_lock_path(environ=environ),
                             Path(environ["LOCALAPPDATA"]) / "issue105-r3" / "locks"
                             / f"{wrap.PROJECT}-{wrap.ENV['qdrant_port']}.lock")
            self.assertEqual(wrap.default_lock_path(environ=environ), wrap.default_lock_path(environ=dict(environ)))
            self.assertNotEqual(wrap.default_lock_path(qdrant_port=1, environ=environ),
                                wrap.default_lock_path(environ=environ))
            with self.assertRaisesRegex(wrap.IsolationError, "LOCALAPPDATA"):
                wrap.default_lock_path(environ={})
        else:
            environ = {"XDG_STATE_HOME": "/sample_only/state"}
            self.assertEqual(wrap.default_lock_path(environ=environ),
                             Path("/sample_only/state/issue105-r3/locks") / f"{wrap.PROJECT}-{wrap.ENV['qdrant_port']}.lock")

    def test_c1_same_process_reentry_is_refused(self):
        first = self.lock().acquire()
        for spelling in (self.path, Path(str(self.path).upper()) if os.name == "nt" else self.path,
                         Path(self.tmp.name) / "locks" / ".." / "locks" / self.path.name):
            with self.subTest(path=str(spelling)), self.assertRaisesRegex(wrap.RunLockError, "재진입"):
                wrap.RunLock(spelling, {"run_id": "f" * 32}).acquire()
        self.assert_os_lock_held()                                    # 등록부만이 아니라 OS 잠금도 보유 중
        self.assertIs(first.is_held(), True)
        self.assertEqual(wrap.read_lock_owner(self.path)["run_id"], "d" * 32)   # 거부된 쪽은 소유자 정보를 바꾸지 않음

    def test_c2_two_processes_conflict_and_the_loser_reports_the_owner(self):
        import time
        proc = self.child("hold")
        started = time.monotonic()
        with self.assertRaises(wrap.RunLockError) as caught:
            self.lock().acquire()
        self.assertLess(time.monotonic() - started, 2.0)               # 비차단: 기다리지 않고 바로 거부
        message = str(caught.exception)
        self.assertIn(f'"pid": {proc.pid}', message)
        self.assertIn('"run_id": "' + "e" * 32 + '"', message)
        self.assertIn("대기·삭제·강제 해제 없음", message)
        self.assertTrue(self.path.exists())                           # 파일 삭제 없음
        self.assertIsNone(proc.poll())                                # 보유자는 그대로 실행 중 (강제 해제 없음)
        proc.stdin.close()                                            # 보유자 정상 종료
        proc.wait(timeout=30)
        parent = self.lock().acquire()                                # 반대 방향: 부모가 보유하면 자식이 거부
        self.child("hold", expect="REFUSED")
        self.assertIs(parent.is_held(), True)

    def test_c3_reacquire_after_normal_release(self):
        proc = self.child("release")
        self.assertEqual(proc.stdout.readline().strip(), "RELEASED")
        proc.stdin.close()
        self.assertEqual(proc.wait(timeout=30), 0)
        lock = self.lock().acquire()                                  # 다른 프로세스가 해제한 뒤
        lock.release()
        self.assertIs(lock.is_held(), False)
        again = self.lock().acquire()                                 # 같은 프로세스 해제 뒤
        self.assertIs(again.is_held(), True)

    def test_c4_reacquire_after_the_holder_is_killed_or_exits_without_release(self):
        proc = self.child("hold")
        with self.assertRaises(wrap.RunLockError):
            self.lock().acquire()
        proc.kill()                                                   # TerminateProcess / SIGKILL (자식만)
        proc.wait(timeout=30)
        lock, _ = self.acquire_within(10)
        self.assertIs(lock.is_held(), True)
        lock.release()
        proc = self.child("exit_without_release")                     # release 없이 os._exit
        proc.wait(timeout=30)
        lock, _ = self.acquire_within(10)
        self.assertIs(lock.is_held(), True)

    def test_c5_leftover_lock_file_without_os_lock_is_acquired(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"\x00" + json.dumps({"run_id": "0" * 32, "pid": 1}).encode())   # 이전 실행의 흔적
        self.assertEqual(wrap.read_lock_owner(self.path)["run_id"], "0" * 32)  # 파일·소유자 정보가 있어도
        lock = self.lock().acquire()                                        # OS 잠금이 없으면 획득
        self.assertEqual(wrap.read_lock_owner(self.path)["run_id"], "d" * 32)  # 소유자 정보는 새 값으로
        lock.release()
        self.assertTrue(self.path.exists())                                  # 해제해도 파일은 남김
        self.assertEqual(wrap.read_lock_owner(self.path)["run_id"], "d" * 32)  # 남은 소유자 정보 ≠ 보유
        self.path.write_bytes(b"not json")
        self.assertIsNone(wrap.read_lock_owner(self.path))
        self.lock().acquire()                                                # 깨진 파일도 OS 잠금이 없으면 획득

    def test_c6_exception_releases_the_lock(self):
        with self.assertRaises(RuntimeError):
            with wrap.RunLock(self.path, {"run_id": "d" * 32}) as lock:
                self.assertIs(lock.is_held(), True)
                raise RuntimeError("sample_only failure")
        self.assertIs(lock.is_held(), False)
        self.lock().acquire()                                                 # 다음 실행 가능

    def test_c6_run_apply_releases_the_lock_on_restore_or_snapshot_failure(self):
        bundle = self.make_bundle()
        ctx = make_ctx()
        current = ctx.snapshot()[2]
        seen = []

        def failing_restore(args, settings=None, pins=None, write_guard=None):
            seen.append(write_guard._lock_checker())
            raise ValueError("Snapshot upload failed: sample_only")
        with self.assertRaisesRegex(ValueError, "sample_only"):
            wrap.run("apply", wrap.ROOT, bundle.folder, ctx, confirm_write=True, expected_fingerprint=current,
                     restore_fn=failing_restore, pins=bundle.pins, lock_path=self.path)
        self.assertEqual(seen, [True])                                         # 실행 중에는 보유
        self.lock().acquire().release()                                        # 끝난 뒤 해제됨
        broken = make_ctx()
        broken.snapshot = lambda: (_ for _ in ()).throw(RuntimeError("sample_only snapshot failure"))
        with self.assertRaisesRegex(RuntimeError, "snapshot failure"):
            wrap.run("apply", wrap.ROOT, bundle.folder, broken, confirm_write=True, expected_fingerprint=current,
                     restore_fn=failing_restore, pins=bundle.pins, lock_path=self.path)
        self.lock().acquire()

    def counting_ctx(self, calls):
        from neo4j.exceptions import AuthError

        def qdrant_factory(values):
            calls.append("qdrant_client")
            return VerifyQdrant()

        def neo4j_factory(password):
            calls.append("neo4j_driver")
            if password != VALUES["neo4j"]:
                raise AuthError("sample_only")
            return SimpleNamespace(close=lambda: None)

        def http_get(url, timeout):
            calls.append("qdrant_http")
            return SimpleNamespace(status_code=401)
        return wrap.Context(dict(VALUES), docker=good_docker(), qdrant_factory=qdrant_factory,
                            neo4j_factory=neo4j_factory, runner_factory=lambda d: VerifyNeo4j(), http_get=http_get,
                            graph=GRAPH)

    def test_c7_lock_failure_means_zero_db_connections_and_zero_writes(self):
        bundle = self.make_bundle()
        for holder in ("same_process", "other_process"):
            with self.subTest(holder=holder):
                held = self.lock().acquire() if holder == "same_process" else None
                proc = self.child("hold") if holder == "other_process" else None
                calls, restored = [], []
                with self.assertRaises(wrap.RunLockError):
                    wrap.run("apply", wrap.ROOT, bundle.folder, self.counting_ctx(calls), confirm_write=True,
                             expected_fingerprint="0" * 64, restore_fn=lambda *a, **k: restored.append(1),
                             pins=bundle.pins, lock_path=self.path)
                self.assertEqual((calls, restored), ([], []))                # Qdrant·Neo4j 접속 0, 복원(쓰기) 0
                if held:
                    held.release()
                if proc:
                    proc.stdin.close()
                    proc.wait(timeout=30)

    def test_c7_cli_reports_the_lock_owner_without_secrets(self):
        proc = self.child("hold")
        with tempfile.TemporaryDirectory() as folder, \
                unittest.mock.patch.dict(os.environ, ENV), \
                unittest.mock.patch.object(wrap, "default_lock_path", return_value=self.path):
            report = Path(folder) / "r.json"
            calls = []                     # 번들 검사는 이 테스트의 관심 밖이라 고정값으로 바꿔 끼운다 (파일 접근만 하는 함수)
            with unittest.mock.patch.object(wrap, "check_source_and_bundle", return_value={"bundle": "sample_only"}):
                code = wrap.main(["--bundle", folder, "--apply", "--confirm-write", "--expect-fingerprint",
                                  "0" * 64, "--report", str(report)], ctx_factory=lambda values: self.counting_ctx(calls))
            output = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual((code, output["status"], output["error_type"]), (1, "FAILED", "RunLockError"))
        self.assertIn(f'"pid": {proc.pid}', output["reason"])
        self.assertEqual(calls, [])
        for secret in VALUES.values():
            self.assertNotIn(secret, json.dumps(output))

    def test_c8_real_run_lock_is_the_guard_lock_checker(self):
        lock = self.lock().acquire()
        ctx = make_ctx()
        identity, _, current = ctx.snapshot()
        guard = wrap.IsolatedWriteGuard("d" * 32, ctx=ctx, identity=identity, preflight_fingerprint=current,
                                        lock_checker=lock.is_held)
        guard.check("before_qdrant_upload:b2", first_stage())                # 보유 중 → 통과
        lock.release()
        self.assertIs(lock.is_held(), False)                                   # 해제 뒤 False
        observed = []
        ctx.qdrant_factory = lambda values: observed.append("qdrant")
        with self.assertRaisesRegex(wrap.IsolationError, "실행 잠금을 보유하고 있지 않습니다"):
            guard.check("before_qdrant_upload:b2", first_stage())
        self.assertEqual(observed, [])                                         # 서버 조회 전에 거부
        never = wrap.RunLock(self.path, {"run_id": "f" * 32})                  # 얻지 못한(얻지 않은) 잠금도 False
        self.assertIs(never.is_held(), False)
        broken = self.lock().acquire()                                          # 확인 중 오류 → 거부
        guard = wrap.IsolatedWriteGuard("d" * 32, ctx=ctx, identity=identity, preflight_fingerprint=current,
                                        lock_checker=broken.is_held)
        with unittest.mock.patch.object(wrap.os, "fstat", side_effect=OSError("sample_only")):
            with self.assertRaisesRegex(wrap.IsolationError, r"실행 잠금 확인 중 오류\(OSError\)"):
                guard.check("before_qdrant_upload:b2", first_stage())

    def test_c8_lock_held_by_another_process_is_not_held_by_this_guard(self):
        proc = self.child("hold")
        mine = wrap.RunLock(self.path, {"run_id": "d" * 32})
        with self.assertRaises(wrap.RunLockError):
            mine.acquire()
        self.assertIs(mine.is_held(), False)                                   # 파일·다른 보유자가 있어도 내 것은 아님
        self.assertIsNone(proc.poll())

    def test_container_change_between_lock_and_db_connection_is_refused(self):
        bundle = self.make_bundle()
        docker = good_docker()
        ctx = make_ctx(docker=docker)
        current = ctx.snapshot()[2]
        original = ctx.identity

        def identity_then_swap():                       # 잠금용 Docker 조회 직후 컨테이너가 바뀐 상황
            result = original()
            docker.containers["q" * 64]["State"]["StartedAt"] = "2026-11-22T02:00:00Z"
            ctx.identity = original
            return result
        ctx.identity = identity_then_swap
        restored = []
        with self.assertRaisesRegex(wrap.IsolationError, "잠금을 얻은 뒤 대상 컨테이너가 바뀌었습니다"):
            wrap.run("apply", wrap.ROOT, bundle.folder, ctx, confirm_write=True, expected_fingerprint=current,
                     restore_fn=lambda *a, **k: restored.append(1), pins=bundle.pins, lock_path=self.path)
        self.assertEqual(restored, [])
        self.lock().acquire()                                                  # 거부 뒤에도 해제됨

    def test_preflight_and_verify_only_do_not_take_the_lock(self):
        held = self.lock().acquire()
        bundle = self.make_bundle()
        result = wrap.run("preflight", wrap.ROOT, bundle.folder, make_ctx(), restore_fn=FakeRestore(),
                          pins=bundle.pins, lock_path=self.path)
        self.assertEqual(result["db_writes"], 0)
        verified = wrap.run("verify-only", wrap.ROOT, bundle.folder,
                            make_ctx(qdrant=published_qdrant(), runner=loaded_runner()),
                            restore_fn=FakeRestore(), pins=bundle.pins, lock_path=self.path)
        self.assertEqual(verified["db_writes"], 0)
        self.assertIs(held.is_held(), True)

    def test_owner_information_and_report(self):
        bundle = self.make_bundle()
        ctx = make_ctx()
        current = ctx.snapshot()[2]
        owners = []

        def restore_fn(args, settings=None, pins=None, write_guard=None):
            if args.apply:
                owners.append((wrap.read_lock_owner(self.path), write_guard.run_id))
            return {"status": "PASS"}
        result = wrap.run("apply", wrap.ROOT, bundle.folder, ctx, confirm_write=True, expected_fingerprint=current,
                          restore_fn=restore_fn, pins=bundle.pins, lock_path=self.path)
        (owner, run_id), = owners
        self.assertEqual(set(owner), {"run_id", "pid", "started_at", "project", "qdrant_port", "containers"})
        self.assertEqual((owner["run_id"], owner["pid"], owner["project"], owner["qdrant_port"]),
                         (run_id, os.getpid(), wrap.PROJECT, wrap.ENV["qdrant_port"]))
        self.assertEqual(owner["containers"], {"neo4j": "n" * 64, "qdrant": "q" * 64})
        self.assertEqual(result["run_lock"], {"lock_file": self.path.name, "owner": owner})
        self.assertTrue(self.path.exists())                                    # 끝난 뒤에도 파일은 남김
        self.lock().acquire()                                                  # 해제됨


def failing_restore(error, progress, seen, simulate=None):
    """apply에서 error를 던지는 restore 대역. simulate()로 실패 전까지의 쓰기 결과(fake DB 상태)를 만들고,
    본체처럼 진행 기록을 붙이고, 받은 guard·잠금 상태를 seen에 남긴다."""
    def restore_fn(args, settings=None, pins=None, write_guard=None):
        if args.apply:
            seen.append({"run_id": write_guard.run_id, "lock_held": write_guard._lock_checker()})
            if simulate:
                simulate()
            error.restore_progress = progress
            raise error
        return {"status": "PASS"}
    return restore_fn


PUBLISH_PROGRESS = {"stage": "alias_publish", "physical_collections": {"b2": TB2, "b3": TB3},
                    "uploads_completed": ["b2", "b3"], "payload_alignment_writes": {}, "neo4j_load_returned": True}


class FailureReportTests(unittest.TestCase):
    """#111 P1-D (T-D2·D3): run()이 apply 실패 직후 잠금을 보유한 채 상태를 재조회해 보고서 항목을 붙이는지,
    main()의 FAILED 보고서에 run_id·실제 매핑·분류가 들어가고 인증값은 없는지, 자동 삭제·재시도가 없는지."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name) / "bundle"
        folder.mkdir()
        self.b = Bundle(folder)
        self.lock_path = Path(self.tmp.name) / "locks" / "run.lock"

    def run_apply(self, ctx, restore_fn):
        current = ctx.snapshot()[2]
        return wrap.run("apply", wrap.ROOT, self.b.folder, ctx, confirm_write=True, expected_fingerprint=current,
                        restore_fn=restore_fn, pins=self.b.pins, lock_path=self.lock_path)

    def published_b2_only_ctx(self):
        """시작은 빈 서버. simulate()가 'B2 공개 뒤 B3 공개 실패' 상태(임시 2·외부 정식 B3 컬렉션·B2 alias·그래프)를 만든다."""
        qdrant, runner = AliasQdrant([], {}), VerifyNeo4j()

        def simulate():
            qdrant.collections.update({TB2: {}, TB3: {}, B3: {}})
            qdrant.alias_map[B2] = TB2
            lg.load_graph(GRAPH, runner, batch_size=500, verify=False)
        return qdrant, make_ctx(qdrant=qdrant, runner=runner), simulate

    def test_run_attaches_the_reinspected_state_while_holding_the_lock(self):
        qdrant, ctx, simulate = self.published_b2_only_ctx()
        seen, inspected_with_lock = [], []
        original = wrap.inspect_failure_state

        def spy(*args, **kwargs):
            inspected_with_lock.append(wrap.read_lock_owner(self.lock_path) is not None and self.lock_held())
            return original(*args, **kwargs)
        error = restore_r3.PublishError("Alias publish failed (sample_only)", partial_publish=True, aliases={B2: TB2})
        with unittest.mock.patch.object(wrap, "inspect_failure_state", side_effect=spy), \
                self.assertRaises(restore_r3.PublishError) as caught:
            self.run_apply(ctx, failing_restore(error, PUBLISH_PROGRESS, seen, simulate))
        report = caught.exception.failure_report
        self.assertEqual(report["run_id"], seen[0]["run_id"])
        self.assertEqual(inspected_with_lock, [True])                       # 재조회는 잠금 보유 중
        self.assertEqual([s["lock_held"] for s in seen], [True])           # 복원 중 guard의 잠금 확인도 보유 (M4)
        self.assertEqual((report["failed_stage"], report["partial_publish"], report["publish_state"]),
                         ("restore:alias_publish", True, "partial"))
        self.assertEqual(report["aliases"], {B2: TB2})
        self.assertEqual(report["collection_classification"]["collections"],
                         {B3: "canonical_name", TB2: "ours_temp", TB3: "ours_temp"})
        self.assertEqual(report["neo4j_data_present"], True)
        self.assertEqual((sorted(qdrant.collections), dict(qdrant.alias_map)),         # 롤백·삭제 없음
                         (sorted([B3, TB2, TB3]), {B2: TB2}))
        self.assertEqual(qdrant.writes, [])

    def lock_held(self):
        probe = wrap.RunLock(self.lock_path, {"run_id": "f" * 32})
        try:
            probe.acquire()
        except wrap.RunLockError:
            return True
        probe.release()
        return False

    def test_cli_failed_report_has_run_id_mapping_and_classification_without_secrets(self):
        qdrant, ctx, simulate = self.published_b2_only_ctx()
        current = ctx.snapshot()[2]
        seen, calls = [], []
        # M4: 테스트 전용 인증값을 메시지에 직접 넣어 reason 가림을 실제로 시험한다
        error = restore_r3.PublishError(f"Alias publish failed (sample_only {VALUES['neo4j']} {VALUES['qdrant']})",
                                        partial_publish=True, aliases={B2: TB2})
        fake = failing_restore(error, PUBLISH_PROGRESS, seen, simulate)

        def counted(*args, **kwargs):
            calls.append(args[0].apply)
            return fake(*args, **kwargs)
        with tempfile.TemporaryDirectory() as folder, unittest.mock.patch.dict(os.environ, ENV), \
                unittest.mock.patch.object(wrap, "default_lock_path", return_value=self.lock_path), \
                unittest.mock.patch.object(wrap, "check_source_and_bundle", return_value={"bundle": "sample_only"}), \
                unittest.mock.patch.object(restore_r3, "restore", side_effect=counted):
            # main은 실제 R3_PINS로 번들을 검사하므로 sample 번들 대신 파일 검사만 고정값으로 바꾼다 (P1-C CLI 테스트와 같음)
            report = Path(folder) / "r.json"
            code = wrap.main(["--bundle", str(self.b.folder), "--apply", "--confirm-write", "--expect-fingerprint",
                              current, "--report", str(report)], ctx_factory=lambda values: ctx)
            output = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual((code, output["status"], output["error_type"]), (1, "FAILED", "PublishError"))
        self.assertEqual(output["run_id"], seen[0]["run_id"])
        self.assertRegex(output["run_id"], r"^[0-9a-f]{32}$")
        self.assertEqual((output["partial_publish"], output["aliases"]), (True, {B2: TB2}))
        self.assertEqual((output["partial_publish_at_failure"], output["aliases_at_failure"]), (True, {B2: TB2}))
        self.assertEqual(output["alias_classification"][B2]["class"], "ours")
        self.assertEqual(output["alias_classification"][B3]["class"], "absent")
        self.assertEqual(output["physical_collections"], {"b2": TB2, "b3": TB3})
        self.assertEqual(output["neo4j_totals"]["nodes"] > 0, True)
        self.assertEqual(calls, [True])                                       # 자동 재시도·후속 verify-only 없음
        self.assertEqual([s["lock_held"] for s in seen], [True])
        self.assertEqual(output["reason"], "Alias publish failed (sample_only [REDACTED] [REDACTED])")
        for secret in VALUES.values():
            self.assertNotIn(secret, json.dumps(output))

    def test_failure_after_apply_uses_the_apply_physical_names(self):
        qdrant, runner = AliasQdrant([], {}), VerifyNeo4j()
        ctx = make_ctx(qdrant=qdrant, runner=runner)

        def restore_fn(args, settings=None, pins=None, write_guard=None):
            if args.apply:                                     # 공개까지 끝난 상태를 만든 뒤 PASS
                qdrant.collections.update({TB2: {}, TB3: {}})
                qdrant.alias_map.update({B2: TB2, B3: TB3})
                lg.load_graph(GRAPH, runner, batch_size=500, verify=False)
                return {"status": "PASS", "physical_collections": {"b2": TB2, "b3": TB3}}
            error = ValueError("Cross-DB validation failed: sample_only")
            error.restore_progress = {"stage": "collection_verification:b2", "physical_collections": None}
            raise error
        with self.assertRaisesRegex(ValueError, "Cross-DB") as caught:
            self.run_apply(ctx, restore_fn)
        report = caught.exception.failure_report
        self.assertEqual((report["failed_stage"], report["physical_collections"]),
                         ("post_apply_verify_only", {"b2": TB2, "b3": TB3}))
        self.assertEqual((report["partial_publish"], report["publish_state"]), (False, "complete"))

    def test_unreachable_servers_are_reported_as_unavailable_not_empty(self):
        qdrant, ctx, _ = self.published_b2_only_ctx()
        error = ValueError("Neo4j restore verification failed")
        seen = []
        restore_fn = failing_restore(error, dict(PUBLISH_PROGRESS, stage="neo4j_load"), seen)
        original_snapshot = ctx.snapshot
        current = original_snapshot()[2]

        def broken_qdrant(values):
            raise ConnectionError(VALUES["qdrant"])

        def apply_then_break(args, settings=None, pins=None, write_guard=None):
            ctx.qdrant_factory = broken_qdrant                                 # 실패 뒤 재조회 시점에 접속 불가
            ctx.neo4j_factory = lambda password: (_ for _ in ()).throw(OSError(VALUES["neo4j"]))
            return restore_fn(args, settings, pins, write_guard)
        with self.assertRaises(ValueError) as caught:
            wrap.run("apply", wrap.ROOT, self.b.folder, ctx, confirm_write=True, expected_fingerprint=current,
                     restore_fn=apply_then_break, pins=self.b.pins, lock_path=self.lock_path)
        report = caught.exception.failure_report
        self.assertEqual(report["unavailable"], {"qdrant_connection": "ConnectionError", "neo4j_connection": "OSError"})
        self.assertEqual((report["aliases"], report["alias_classification"], report["collection_classification"],
                          report["partial_publish"], report["publish_state"], report["neo4j_totals"],
                          report["neo4j_data_present"]), (None, None, None, None, "unknown", None, None))
        for secret in VALUES.values():
            self.assertNotIn(secret, json.dumps(report))

    def test_neo4j_count_query_failure_is_unknown_not_zero(self):
        """Neo4j 접속은 되지만 건수 조회가 실패: 0/0(비어 있음)으로 쓰지 않고 모름(None)으로, driver는 닫는다."""
        closed = []
        ctx = make_ctx(qdrant=AliasQdrant([TB2], {}))
        ctx.neo4j_factory = lambda password: SimpleNamespace(close=lambda: closed.append("neo4j"))
        ctx.runner_factory = lambda driver: SimpleNamespace(
            read_rows=lambda *a, **k: (_ for _ in ()).throw(RuntimeError(VALUES["neo4j"])))
        report = wrap.inspect_failure_state(ctx, "d" * 32, {"stage": "neo4j_load",
                                                            "physical_collections": {"b2": TB2, "b3": TB3}},
                                            "restore:neo4j_load")
        self.assertEqual((report["neo4j_totals"], report["neo4j_data_present"]), (None, None))
        self.assertEqual(report["unavailable"], {"neo4j_totals": "RuntimeError"})
        self.assertEqual(closed, ["neo4j"])
        self.assertEqual(report["collection_classification"], {"collections": {TB2: "ours_temp"}, "ours_absent": [TB3]})
        self.assertNotIn(VALUES["neo4j"], json.dumps(report))

    def test_m1_missing_stage_in_progress_keeps_the_original_error(self):
        for label, progress in (("no_stage", {"physical_collections": {"b2": TB2, "b3": TB3}}),
                                ("not_dict", "sample_only_not_a_dict"), ("stage_none", {"stage": None})):
            with self.subTest(progress=label):
                _, ctx, simulate = self.published_b2_only_ctx()                # subTest마다 새 빈 서버
                original = ValueError("Neo4j restore verification failed (sample_only)")
                with self.assertRaises(ValueError) as caught:
                    self.run_apply(ctx, failing_restore(original, progress, [], simulate))
                self.assertIs(caught.exception, original)                      # 최초 예외 그대로
                report = caught.exception.failure_report
                self.assertEqual(report["failed_stage"], "restore_apply")      # 단계 기록이 없으면 래퍼 단계
                self.assertNotIn("inspection_error", report)
                self.assertEqual(report["alias_classification"][B2]["class"],
                                 "ours" if label == "no_stage" else "foreign_target")   # 임시 이름을 모르면 ours 아님

    def test_m1_inspection_failure_keeps_the_original_error_and_records_only_the_type(self):
        qdrant, ctx, simulate = self.published_b2_only_ctx()
        original = restore_r3.PublishError("Alias publish failed (sample_only)", partial_publish=True,
                                           aliases={B2: TB2})
        boom = RuntimeError(f"sample_only inspection failure {VALUES['qdrant']}")
        with unittest.mock.patch.object(wrap, "inspect_failure_state", side_effect=boom), \
                self.assertRaises(restore_r3.PublishError) as caught:
            self.run_apply(ctx, failing_restore(original, PUBLISH_PROGRESS, [], simulate))
        self.assertIs(caught.exception, original)
        report = caught.exception.failure_report
        self.assertEqual((report["inspection_error"], report["failed_stage"], report["partial_publish"],
                          report["publish_state"], report["aliases"]),
                         ("RuntimeError", "restore:alias_publish", None, "unknown", None))   # 모름 → 정상·비어 있음 아님
        self.assertFalse(VALUES["qdrant"] in json.dumps(report))
        self.assertEqual((sorted(qdrant.collections), dict(qdrant.alias_map)),                # 롤백·삭제 없음
                         (sorted([B3, TB2, TB3]), {B2: TB2}))
        fields = wrap.failure_fields(caught.exception)                        # main 보고서: 실패 시점 값도 함께
        self.assertEqual((fields["partial_publish_at_failure"], fields["aliases_at_failure"], fields["partial_publish"]),
                         (True, {B2: TB2}, None))

    def test_m2_reason_hides_known_secrets_in_every_form(self):
        key, password = VALUES["qdrant"], VALUES["neo4j"]
        shared = "sample_only_shared_secret_value"
        environ = {**ENV, "QDRANT_API_KEY": shared}
        secrets_ = wrap.known_secret_values(environ)
        self.assertEqual(sorted(secrets_), sorted([key, password, shared]))
        message = (f"Header part ({key!r}) from ('api-key', {key!r}) must be valid; "
                   f"bytes {key.encode()!r}; neo4j auth={password}; shared={shared}; ending")
        reason = wrap.safe_reason(ValueError(message), secrets_)
        for value in (key, password, shared):
            self.assertFalse(value in reason)
        self.assertTrue(reason.startswith("Header part ('[REDACTED]') from ('api-key', '[REDACTED]')"))
        self.assertTrue(reason.endswith("shared=[REDACTED]; ending"))                     # 원인 문장은 유지
        self.assertEqual(wrap.safe_reason(ValueError("plain sample_only cause"), secrets_), "plain sample_only cause")
        unsafe = wrap.safe_reason(ValueError("xx sample_only REDACT xx"), ["REDACT"])       # 가린 뒤에도 남는 경우
        self.assertEqual(unsafe, "ValueError: 메시지에 인증값이 포함되어 원문을 기록하지 않음")
        # 이스케이프되는 문자(역슬래시·비ASCII)가 든 값: repr·bytes repr 모양에서도 가려야 한다
        odd = "sample_only\\key_é"
        escaped = (f"raw={odd}; str_repr={odd!r}; bytes_repr={odd.encode()!r}; end")
        reason = wrap.safe_reason(ValueError(escaped), [odd])
        self.assertEqual(reason, "raw=[REDACTED]; str_repr='[REDACTED]'; bytes_repr=b'[REDACTED]'; end")
        for form in (odd, repr(odd)[1:-1], repr(odd.encode())[2:-1]):
            self.assertFalse(form in reason)
        # 가린 표식 경계에서 이스케이프 모양이 다시 생기는 경우(전수 탐색으로 찾은 예): 원문 대신 고정 설명
        boundary = "]\\["                                                   # 값: ] \ [
        rebuilt = wrap.safe_reason(ValueError("]\\\\[\\\\]\\\\["), [boundary])  # 메시지: ]\\[\\]\\[
        self.assertEqual(rebuilt, "ValueError: 메시지에 인증값이 포함되어 원문을 기록하지 않음")

    def test_m2_cli_reason_is_redacted_for_external_value_errors(self):
        qdrant, ctx, simulate = self.published_b2_only_ctx()
        current = ctx.snapshot()[2]
        external = ValueError(f"Invalid header value {VALUES['qdrant'].encode()!r} (sample_only external library)")
        with tempfile.TemporaryDirectory() as folder, unittest.mock.patch.dict(os.environ, ENV), \
                unittest.mock.patch.object(wrap, "default_lock_path", return_value=self.lock_path), \
                unittest.mock.patch.object(wrap, "check_source_and_bundle", return_value={"bundle": "sample_only"}), \
                unittest.mock.patch.object(restore_r3, "restore",
                                           side_effect=failing_restore(external, PUBLISH_PROGRESS, [], simulate)):
            report = Path(folder) / "r.json"
            wrap.main(["--bundle", str(self.b.folder), "--apply", "--confirm-write", "--expect-fingerprint",
                       current, "--report", str(report)], ctx_factory=lambda values: ctx)
            text = report.read_text(encoding="utf-8")
        for value in VALUES.values():
            self.assertFalse(value in text)
        self.assertEqual(json.loads(text)["reason"],
                         "Invalid header value b'[REDACTED]' (sample_only external library)")

    def test_e11_cli_failed_report_carries_the_payload_index_details(self):
        """#111 P1-E: 인덱스 검증 실패 → FAILED 보고서에 payload_index_report(필드별 결과·누락·잘못된 타입) + P1-D 재조회."""
        qdrant, ctx, simulate = self.published_b2_only_ctx()
        current = ctx.snapshot()[2]
        index_report = {"stage": "before_alias_publish", "failed": ["b2"],
                        "collections": {"b2": {"key": "b2", "collection": TB2, "status": "FAILED",
                                               "missing_fields": ["region"], "invalid_types": {"category": "text"}}}}
        error = restore_r3.PayloadIndexError(f"Payload index verification failed before_alias_publish (sample_only "
                                             f"{VALUES['qdrant']})", report=index_report)
        progress = dict(PUBLISH_PROGRESS)
        with tempfile.TemporaryDirectory() as folder, unittest.mock.patch.dict(os.environ, ENV), \
                unittest.mock.patch.object(wrap, "default_lock_path", return_value=self.lock_path), \
                unittest.mock.patch.object(wrap, "check_source_and_bundle", return_value={"bundle": "sample_only"}), \
                unittest.mock.patch.object(restore_r3, "restore",
                                           side_effect=failing_restore(error, progress, [], simulate)):
            report = Path(folder) / "r.json"
            wrap.main(["--bundle", str(self.b.folder), "--apply", "--confirm-write", "--expect-fingerprint",
                       current, "--report", str(report)], ctx_factory=lambda values: ctx)
            text = report.read_text(encoding="utf-8")
        output = json.loads(text)
        self.assertEqual((output["status"], output["error_type"]), ("FAILED", "PayloadIndexError"))
        self.assertEqual(output["payload_index_report"], index_report)
        self.assertEqual((output["failed_stage"], output["run_id"] is not None), ("restore:alias_publish", True))
        self.assertTrue(output["reason"].endswith("(sample_only [REDACTED])"))
        for value in VALUES.values():
            self.assertFalse(value in text)

    def test_apply_on_an_already_written_target_is_refused_without_restore_or_db_writes(self):
        """이미 쓰기가 있었던(빈 서버 아님) 대상에 래퍼 apply: fingerprint가 현재 상태와 같아도 빈 서버 검사로 거부.
        복원 호출 0, 서버 쓰기·alias 변경 0, 재조회 보고서 없음, 잠금은 해제."""
        cases = {
            "published_and_loaded": (lambda: AliasQdrant([TB2, TB3], {B2: TB2, B3: TB3}), loaded_runner),
            "temp_collection_only": (lambda: AliasQdrant([TB2], {}), VerifyNeo4j),
            "graph_only": (lambda: AliasQdrant([], {}), loaded_runner),
        }
        for label, (make_qdrant, make_runner) in cases.items():
            with self.subTest(state=label):
                qdrant = make_qdrant()
                ctx = make_ctx(qdrant=qdrant, runner=make_runner())
                identity, servers, current = ctx.snapshot()
                self.assertNotEqual(servers["state"], "empty")
                before = (sorted(qdrant.collections), dict(qdrant.alias_map))
                restored = []
                with self.assertRaisesRegex(wrap.IsolationError, "비어 있지 않습니다") as caught:
                    wrap.run("apply", wrap.ROOT, self.b.folder, ctx, confirm_write=True, expected_fingerprint=current,
                             restore_fn=lambda *a, **k: restored.append(1), pins=self.b.pins, lock_path=self.lock_path)
                self.assertEqual(restored, [])                                       # 복원(쓰기) 호출 0
                self.assertEqual((qdrant.writes, (sorted(qdrant.collections), dict(qdrant.alias_map))), ([], before))
                self.assertFalse(hasattr(caught.exception, "failure_report"))        # 이번 실행은 쓰지 않음
                probe = wrap.RunLock(self.lock_path, {"run_id": "f" * 32})            # 잠금 해제됨
                probe.acquire()
                probe.release()

    def test_cli_generic_exception_after_writes_is_failed_with_diagnostics(self):
        """쓰기 시작 뒤 ValueError가 아닌 일반 예외: CLI는 FAILED·종료 코드 1이고 재조회 진단 정보를 잃지 않는다.
        (일반 예외 경로는 원본 메시지를 reason에 싣지 않는다 — 인증값 노출 방지)"""
        qdrant, ctx, simulate = self.published_b2_only_ctx()
        current = ctx.snapshot()[2]
        seen, calls = [], []
        error = RuntimeError(f"sample_only driver crash {VALUES['neo4j']}")
        fake = failing_restore(error, dict(PUBLISH_PROGRESS, stage="neo4j_load"), seen, simulate)

        def counted(*args, **kwargs):
            calls.append(args[0].apply)
            return fake(*args, **kwargs)
        with tempfile.TemporaryDirectory() as folder, unittest.mock.patch.dict(os.environ, ENV), \
                unittest.mock.patch.object(wrap, "default_lock_path", return_value=self.lock_path), \
                unittest.mock.patch.object(wrap, "check_source_and_bundle", return_value={"bundle": "sample_only"}), \
                unittest.mock.patch.object(restore_r3, "restore", side_effect=counted):
            report = Path(folder) / "r.json"
            code = wrap.main(["--bundle", str(self.b.folder), "--apply", "--confirm-write", "--expect-fingerprint",
                              current, "--report", str(report)], ctx_factory=lambda values: ctx)
            text = report.read_text(encoding="utf-8")
        output = json.loads(text)
        self.assertEqual((code, output["status"], output["mode"], output["error_type"]), (1, "FAILED", "apply", "RuntimeError"))
        self.assertNotEqual(output["status"], "PASS")
        self.assertEqual((output["run_id"], output["failed_stage"]), (seen[0]["run_id"], "restore:neo4j_load"))
        self.assertEqual((output["aliases"], output["partial_publish"], output["publish_state"]), ({B2: TB2}, True, "partial"))
        self.assertEqual(output["alias_classification"][B2]["class"], "ours")
        self.assertEqual(output["collection_classification"]["collections"],
                         {B3: "canonical_name", TB2: "ours_temp", TB3: "ours_temp"})
        self.assertEqual((output["neo4j_data_present"], output["unavailable"]), (True, {}))
        self.assertEqual(output["load_progress"]["uploads_completed"], ["b2", "b3"])
        self.assertIn("action", output)
        self.assertNotIn("reason", output)                                           # 일반 예외 메시지는 싣지 않음
        self.assertEqual(calls, [True])                                              # 재시도·사후 verify-only 없음
        self.assertEqual((sorted(qdrant.collections), dict(qdrant.alias_map)), (sorted([B3, TB2, TB3]), {B2: TB2}))
        for value in VALUES.values():
            self.assertFalse(value in text)

    def test_failures_before_any_write_have_no_failure_report(self):
        ctx = make_ctx()
        with self.assertRaisesRegex(wrap.IsolationError, "fingerprint") as caught:
            wrap.run("apply", wrap.ROOT, self.b.folder, ctx, confirm_write=True, expected_fingerprint="0" * 64,
                     restore_fn=FakeRestore(), pins=self.b.pins, lock_path=self.lock_path)
        self.assertFalse(hasattr(caught.exception, "failure_report"))         # 쓰기 전 거부: 재조회 대상 없음

    def test_classification_rules(self):
        physical = {"b2": TB2, "b3": TB3}
        cases = {
            "both_ours": ({B2: TB2, B3: TB3}, {B2: "ours", B3: "ours"}),
            "b2_only": ({B2: TB2}, {B2: "ours", B3: "absent"}),
            "b3_only": ({B3: TB3}, {B2: "absent", B3: "ours"}),
            "none": ({}, {B2: "absent", B3: "absent"}),
            "swapped": ({B2: TB3, B3: TB2}, {B2: "foreign_target", B3: "foreign_target"}),
            "foreign": ({B2: "exp_other_sample_only", B3: TB3}, {B2: "foreign_target", B3: "ours"}),
        }
        for name, (aliases, wanted) in cases.items():
            with self.subTest(case=name):
                result = wrap.classify_aliases(aliases, physical)
                self.assertEqual({k: v["class"] for k, v in result.items()}, wanted)
        self.assertEqual(wrap.classify_aliases({B2: TB2}, {})[B2]["class"], "foreign_target")   # 이름을 모르면 ours 아님
        other_temp = restore_r3.temp_collection_name(B2, "c" * 32)
        self.assertEqual(wrap.classify_collections([TB2, B2, other_temp, "x_sample_only"], physical),
                         {"collections": {B2: "canonical_name", TB2: "ours_temp", other_temp: "other_temp",
                                          "x_sample_only": "unexpected"}, "ours_absent": [TB3]})


class ContextFailureTests(unittest.TestCase):
    """#111 C-1·C-2: Context.snapshot()의 연결 정리, Context.identity()의 Docker 조회 실패 변환 (fake만, 접속 없음)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lock_path = Path(self.tmp.name) / "locks" / "run.lock"

    def counting_ctx(self, log, *, docker=None, neo4j_fails=False, client_close_fails=False):
        from neo4j.exceptions import AuthError
        qdrant = VerifyQdrant()

        class Handle:
            def __init__(self):
                log.append("qdrant_open")

            def __getattr__(self, item):
                return getattr(qdrant, item)

            def close(self):
                log.append("qdrant_close")
                if client_close_fails:
                    raise OSError("sample_only close failure")

        def neo4j_factory(password):
            if password != VALUES["neo4j"]:
                raise AuthError("sample_only")                       # check_neo4j_auth(틀린 비밀번호)는 거부되어야 함
            if neo4j_fails:
                log.append("neo4j_open_failed")
                raise RuntimeError("sample_only neo4j unavailable")
            log.append("neo4j_open")
            return SimpleNamespace(close=lambda: log.append("neo4j_close"))

        return wrap.Context(dict(VALUES), docker=docker or good_docker(), qdrant_factory=lambda values: Handle(),
                            neo4j_factory=neo4j_factory, runner_factory=lambda driver: VerifyNeo4j(),
                            http_get=lambda url, timeout: SimpleNamespace(status_code=401), graph=GRAPH)

    # C-1
    def test_c1_driver_failure_after_qdrant_client_closes_the_client_once(self):
        log = []
        with self.assertRaisesRegex(RuntimeError, "neo4j unavailable"):           # 예외는 숨기지 않음
            self.counting_ctx(log, neo4j_fails=True).snapshot()
        self.assertEqual(log, ["qdrant_open", "neo4j_open_failed", "qdrant_close"])
        self.assertEqual(log.count("qdrant_close"), 1)

    def test_c1_normal_snapshot_closes_both_once_and_returns_the_same_shape(self):
        log = []
        identity, servers, current = self.counting_ctx(log).snapshot()
        self.assertEqual(log, ["qdrant_open", "neo4j_open", "qdrant_close", "neo4j_close"])
        self.assertEqual((set(identity), servers["state"], len(current)), ({"qdrant", "neo4j"}, "empty", 64))
        self.assertEqual(current, make_ctx().snapshot()[2])                      # 같은 상태 → 같은 fingerprint

    def test_c1_client_close_failure_still_closes_the_driver(self):
        log = []
        with self.assertRaisesRegex(OSError, "close failure"):
            self.counting_ctx(log, client_close_fails=True).snapshot()
        self.assertEqual(log, ["qdrant_open", "neo4j_open", "qdrant_close", "neo4j_close"])

    def test_c1_driver_failure_while_holding_the_run_lock_releases_the_lock(self):
        folder = Path(self.tmp.name) / "bundle"
        folder.mkdir()
        bundle = Bundle(folder)
        log, restored = [], []
        ctx = self.counting_ctx(log, neo4j_fails=True)
        with self.assertRaisesRegex(RuntimeError, "neo4j unavailable"):
            wrap.run("apply", wrap.ROOT, bundle.folder, ctx, confirm_write=True, expected_fingerprint="0" * 64,
                     restore_fn=lambda *a, **k: restored.append(1), pins=bundle.pins, lock_path=self.lock_path)
        self.assertEqual((log.count("qdrant_open"), log.count("qdrant_close"), restored), (1, 1, []))
        probe = wrap.RunLock(self.lock_path, {"run_id": "f" * 32})                 # 잠금이 해제되어 다시 얻을 수 있음
        probe.acquire()
        probe.release()

    # C-2
    def failing_docker(self, error):
        def docker(args):
            raise error
        return docker

    def test_c2_docker_execution_failures_become_isolation_errors_without_the_original_message(self):
        secret_text = f"sample_only {VALUES['qdrant']} C:\\sample_only\\docker.exe"
        for label, error in (("not_found", FileNotFoundError(2, secret_text, "docker")),
                             ("permission", PermissionError(13, secret_text)), ("os_error", OSError(5, secret_text))):
            with self.subTest(error=label):
                ctx = make_ctx(docker=self.failing_docker(error))
                with self.assertRaises(wrap.IsolationError) as caught:
                    ctx.identity()
                message = str(caught.exception)
                self.assertTrue(message.startswith(f"Docker 상태 조회 실패({type(error).__name__})"))
                self.assertFalse(VALUES["qdrant"] in message or "docker.exe" in message or "sample_only" in message)
                self.assertIsNone(caught.exception.__cause__)                    # 원본 예외를 cause로 싣지 않음

    def test_c2_existing_isolation_errors_are_unchanged(self):
        original = wrap.IsolationError("docker ps 실패 (exit 1)")                    # run_docker의 기존 실패 모양
        with self.assertRaises(wrap.IsolationError) as caught:
            make_ctx(docker=self.failing_docker(original)).identity()
        self.assertIs(caught.exception, original)
        cases = {"r2_project": [container("q" * 64, "qdrant", project="issue91-r2-verify-20261119"),
                                container("n" * 64, "neo4j")]}                   # 소유 불일치도 기존 메시지 그대로
        with self.assertRaises(wrap.IsolationError) as caught:
            make_ctx(docker=FakeDocker(cases["r2_project"])).identity()
        self.assertNotIn("Docker 상태 조회 실패", str(caught.exception))
        self.assertEqual(set(make_ctx().identity()), {"qdrant", "neo4j"})            # 성공 경로 유지

    def test_c2_cli_failed_report_records_the_reason_and_touches_no_db(self):
        for mode_args in ([], ["--apply", "--confirm-write", "--expect-fingerprint", "0" * 64]):
            with self.subTest(mode=mode_args[:1] or ["preflight"]):
                log = []
                ctx = self.counting_ctx(log, docker=self.failing_docker(FileNotFoundError(2, VALUES["neo4j"], "docker")))
                restored = []
                with tempfile.TemporaryDirectory() as folder, unittest.mock.patch.dict(os.environ, ENV), \
                        unittest.mock.patch.object(wrap, "default_lock_path", return_value=self.lock_path), \
                        unittest.mock.patch.object(wrap, "check_source_and_bundle", return_value={"bundle": "sample_only"}), \
                        unittest.mock.patch.object(restore_r3, "restore", side_effect=lambda *a, **k: restored.append(1)):
                    report = Path(folder) / "r.json"
                    code = wrap.main(["--bundle", folder, *mode_args, "--report", str(report)],
                                     ctx_factory=lambda values: ctx)
                    text = report.read_text(encoding="utf-8")
                output = json.loads(text)
                self.assertEqual((code, output["status"], output["error_type"]), (1, "FAILED", "IsolationError"))
                self.assertTrue(output["reason"].startswith("Docker 상태 조회 실패(FileNotFoundError)"))
                self.assertEqual((log, restored), ([], []))                       # Qdrant·Neo4j 접속 0, 복원(쓰기) 0
                self.assertFalse(self.lock_path.exists())                          # apply도 잠금 전에 멈춤
                for value in VALUES.values():
                    self.assertFalse(value in text)


class CliTests(unittest.TestCase):
    def test_apply_flags_and_report_overwrite(self):
        with self.assertRaises(SystemExit):
            wrap.main(["--bundle", ".", "--apply"])
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "r.json"
            report.write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit):
                wrap.main(["--bundle", folder, "--report", str(report)])

    def test_partial_publish_is_reported_as_failed(self):
        error = restore_r3.PublishError("Alias publish failed (sample_only)", partial_publish=True, aliases={B2: TB2})
        with tempfile.TemporaryDirectory() as folder, \
                unittest.mock.patch.dict(os.environ, ENV), unittest.mock.patch.object(wrap, "run", side_effect=error):
            report = Path(folder) / "r.json"
            self.assertEqual(wrap.main(["--bundle", folder, "--apply", "--confirm-write", "--expect-fingerprint",
                                        "0" * 64, "--report", str(report)], ctx_factory=lambda values: None), 1)
            output = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual((output["status"], output["error_type"]), ("FAILED", "PublishError"))
        self.assertEqual((output["partial_publish"], output["aliases"]), (True, {B2: TB2}))
        self.assertNotIn(VALUES["qdrant"], json.dumps(output))

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
