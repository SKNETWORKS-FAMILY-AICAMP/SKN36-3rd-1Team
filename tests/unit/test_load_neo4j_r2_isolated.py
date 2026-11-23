"""Issue #91 Neo4j 격리 적재 경로(scripts/load_neo4j_r2_isolated.py) 테스트.

실제 Neo4j·Qdrant·Docker에 접속하지 않는다. 기존 test_neo4j_loader.FakeGraphRunner(메모리 그래프)와
test_update_b2_from_snapshot.FakeQdrant를 재사용한다. 그래프·청크는 실제 v1.4.0-candidate 파일을 읽는다.
가짜 실행기는 로더 쿼리 모양만 흉내 내며 실제 Neo4j의 트랜잭션 동작은 NOT_TESTED이다.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_neo4j_loader import FakeGraphRunner  # noqa: E402
from test_update_b2_from_snapshot import FakeQdrant  # noqa: E402

from database import handoff_data as hd, hybrid_search as hs  # noqa: E402
from database.neo4j import load_graph as lg  # noqa: E402
from database.qdrant import store  # noqa: E402
from scripts import load_neo4j_r2_isolated as r2  # noqa: E402
from scripts import restore_final_v140 as restore  # noqa: E402
from scripts import seed_b2_from_r1_vectors as seed  # noqa: E402
from scripts import update_b2_from_snapshot as qdrant_r2  # noqa: E402

URL = "bolt://127.0.0.1:56487"
ENV = {r2.PASSWORD_ENV: "sample_only_password"}


class FakeNeo4j(FakeGraphRunner):
    """FakeGraphRunner + dbms.components + 교차 검증 쿼리 (sample_only)."""

    def __init__(self, version=r2.EXPECTED_NEO4J_VERSION, edition="community", **kwargs):
        super().__init__(**kwargs)
        self.version, self.edition, self.notifications = version, edition, []

    def writes(self):
        return [call for call in self.calls if call[0] in ("write", "schema")]

    def read_rows(self, query, parameters=None):
        if query == r2.QUERY_COMPONENTS:
            self.calls.append(("read", query))
            return [{"name": "Neo4j Kernel", "versions": [self.version], "edition": self.edition}]
        if query == hs.CROSS_POLICY_QUERY:
            return [{"policy_id": p, "exists": ("Policy", p) in self.nodes} for p in parameters["policy_ids"]]
        if query == hs.CROSS_DOCUMENT_QUERY:
            rows = []
            for document_id in parameters["document_ids"]:
                node = self.nodes.get(("Document", document_id))
                supported = sorted(source for relation, (_sl, source), (_tl, target) in self.relationships
                                   if relation == "SUPPORTED_BY" and target == document_id)
                rows.append({"document_id": document_id, "exists": node is not None,
                             "content_hash": (node or {}).get("content_hash"),
                             "policy_id": (node or {}).get("policy_id"), "supported_by": supported})
            return rows
        return super().read_rows(query, parameters)


def fingerprint(runner, ref):
    return r2.dry_run(runner, ref)["fingerprint"]


class Reference:
    value = None

    @classmethod
    def get(cls):
        if cls.value is None:
            cls.value = r2.load_reference()
        return cls.value


class GuardTests(unittest.TestCase):
    def test_only_isolated_bolt_url_is_allowed(self):
        self.assertEqual(r2.require_isolated_bolt_url(URL), URL)
        for url in ("bolt://127.0.0.1:7687", "bolt://127.0.0.1:17687", "bolt://127.0.0.1:57687",
                    "bolt://127.0.0.1:56488", "neo4j://127.0.0.1:56487", "http://127.0.0.1:56487",
                    "bolt://10.0.0.5:56487", "bolt://neo4j:pw@127.0.0.1:56487", "bolt://127.0.0.1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                r2.require_isolated_bolt_url(url)

    def test_password_comes_only_from_dedicated_variable(self):
        self.assertEqual(r2.read_password(ENV), "sample_only_password")
        with self.assertRaisesRegex(r2.Neo4jR2Error, r2.PASSWORD_ENV):
            r2.read_password({"NEO4J_PASSWORD": "shared_sample_only", "NEO4J_URI": "bolt://127.0.0.1:7687"})

    def test_driver_uses_argument_url_and_ignores_env_and_dotenv(self):
        created = []

        def fake_driver(url, auth, **kwargs):
            driver = SimpleNamespace(url=url, auth=auth, closed=False, verify_connectivity=lambda: None)
            driver.close = lambda: setattr(driver, "closed", True)
            created.append(driver)
            return driver

        blocked = AssertionError(".env·공유 설정 사용 금지")
        with patch("neo4j.GraphDatabase.driver", side_effect=fake_driver), \
                patch.dict("os.environ", {"NEO4J_URI": "bolt://127.0.0.1:7687", "NEO4J_PASSWORD": "shared"}), \
                patch.object(hs.em, "load_env_file", side_effect=blocked), \
                patch.object(store, "load_project_env", side_effect=blocked):
            driver = r2.make_isolated_driver(URL, ENV)
        self.assertEqual((driver.url, driver.auth), (URL, ("neo4j", "sample_only_password")))

    def test_failed_connectivity_closes_driver(self):
        state = {}

        def fake_driver(url, auth, **kwargs):
            def fail():
                raise RuntimeError("sample_only unavailable")
            return SimpleNamespace(verify_connectivity=fail, close=lambda: state.setdefault("closed", True))

        with patch("neo4j.GraphDatabase.driver", side_effect=fake_driver), self.assertRaises(RuntimeError):
            r2.make_isolated_driver(URL, ENV)
        self.assertTrue(state["closed"])

    def test_auth_must_reject_wrong_password(self):
        from neo4j.exceptions import AuthError
        seen = []

        def driver_factory(raise_auth):
            def factory(url, auth, **kwargs):
                seen.append(auth)

                def verify():
                    if raise_auth:
                        raise AuthError("sample_only")
                return SimpleNamespace(verify_connectivity=verify, close=lambda: None)
            return factory

        with patch("neo4j.GraphDatabase.driver", side_effect=driver_factory(True)):
            self.assertTrue(r2.check_auth_enforced(URL))
        self.assertNotEqual(seen[0][1], ENV[r2.PASSWORD_ENV])          # 무작위 틀린 비밀번호
        with patch("neo4j.GraphDatabase.driver", side_effect=driver_factory(False)):
            with self.assertRaisesRegex(r2.Neo4jR2Error, "인증"):
                r2.check_auth_enforced(URL)

    def test_server_version_and_edition_are_pinned(self):
        self.assertEqual(r2.server_info(FakeNeo4j())["version"], "5.26.32")
        for runner in (FakeNeo4j(version="5.26.0"), FakeNeo4j(edition="enterprise")):
            with self.subTest(version=runner.version, edition=runner.edition), self.assertRaises(r2.Neo4jR2Error):
                r2.server_info(runner)


class ReferenceTests(unittest.TestCase):
    def test_latest_release_counts_match_pinned_expectation(self):
        ref = Reference.get()
        self.assertEqual(ref["counts"], (246, 270, 665, 1139, 1135, 2676, 211))
        self.assertEqual(ref["counts"], tuple(restore.EXPECTED))
        self.assertTrue(ref["validation"]["ok"])
        self.assertEqual(ref["manifest_lf_sha256"], seed.MANIFEST_LF_SHA256)

    def test_manifest_or_count_mismatch_stops(self):
        with patch.object(seed.fi, "manifest_sha256", return_value="0" * 64):
            with self.assertRaisesRegex(seed.SeedPlanError, "manifest"):
                r2.load_reference()
        with self.assertRaisesRegex(r2.Neo4jR2Error, "건수"):
            r2.load_reference(expected=(246, 262, 600, 1139, 1125, 2667, 211))

    def test_invalid_graph_input_stops(self):
        graph = lg.read_graph_input()
        broken = lg.GraphInput(graph.nodes_by_label, graph.relationships + [dict(graph.relationships[0])],
                               graph.source_dir)
        with patch.object(lg, "read_graph_input", return_value=broken):
            with self.assertRaisesRegex(r2.Neo4jR2Error, "duplicate_relationship"):
                r2.load_reference()


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.ref = Reference.get()
        self.runner = FakeNeo4j()

    def test_dry_run_on_empty_db_has_no_writes(self):
        output = r2.dry_run(self.runner, self.ref)
        self.assertEqual((output["state"], output["totals"], output["db_writes"]), ("empty", {"nodes": 0, "relationships": 0}, 0))
        self.assertEqual(output["expected_totals"], {"nodes": 1135, "relationships": 2676})
        self.assertRegex(output["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(self.runner.writes(), [])

    def test_write_requires_confirmation_and_matching_fingerprint(self):
        with self.assertRaisesRegex(r2.Neo4jR2Error, "confirm_write"):
            r2.load(self.runner, self.ref, expected_fingerprint=fingerprint(self.runner, self.ref))
        with self.assertRaisesRegex(r2.Neo4jR2Error, "fingerprint"):
            r2.load(self.runner, self.ref, True)
        with self.assertRaisesRegex(r2.Neo4jR2Error, "fingerprint"):
            r2.load(self.runner, self.ref, True, "0" * 64)
        self.assertEqual(self.runner.writes(), [])

    def test_load_writes_full_graph_into_the_verified_runner(self):
        result = r2.load(self.runner, self.ref, True, fingerprint(self.runner, self.ref))
        self.assertEqual(result["status"], "LOADED")
        self.assertEqual(result["totals"], {"nodes": 1135, "relationships": 2676})
        self.assertTrue(result["verification_ok"])
        self.assertEqual((len(self.runner.nodes), len(self.runner.relationships)), (1135, 2676))
        self.assertEqual(sum(1 for r in self.runner.relationships if r[0] == "REQUIRES_CONDITION"), 211)
        self.assertEqual(r2.verify(self.runner, self.ref)["ok"], True)
        complete = r2.dry_run(self.runner, self.ref)
        self.assertEqual(complete["state"], "complete")
        writes = len(self.runner.writes())
        with self.assertRaisesRegex(r2.Neo4jR2Error, "complete"):       # 완료 후 다시 적재하지 않음
            r2.load(self.runner, self.ref, True, complete["fingerprint"])
        self.assertEqual(len(self.runner.writes()), writes)

    def test_existing_foreign_data_is_never_written(self):
        self.runner.nodes[("Policy", "POL_SAMPLE_ONLY_FOREIGN")] = {"policy_id": "POL_SAMPLE_ONLY_FOREIGN"}
        output = r2.dry_run(self.runner, self.ref)
        self.assertEqual(output["state"], "foreign_data")
        with self.assertRaisesRegex(r2.Neo4jR2Error, "foreign_data"):
            r2.load(self.runner, self.ref, True, output["fingerprint"])
        with self.assertRaisesRegex(r2.Neo4jR2Error, "foreign_data"):
            r2.load(self.runner, self.ref, True, output["fingerprint"], resume=True)
        self.assertEqual(self.runner.writes(), [])

    def test_wrong_server_version_is_never_written(self):
        runner = FakeNeo4j(version="5.26.0")
        with self.assertRaises(r2.Neo4jR2Error):
            r2.load(runner, self.ref, True, "0" * 64)
        self.assertEqual(runner.writes(), [])

    def test_partial_failure_is_preserved_and_needs_explicit_resume(self):
        runner = FakeNeo4j(fail_on_call=3)
        first = fingerprint(runner, self.ref)
        with self.assertRaises(lg.GraphLoadError):
            r2.load(runner, self.ref, True, first)
        partial = r2.dry_run(runner, self.ref)
        self.assertEqual(partial["state"], "partial")
        kept = (dict(runner.nodes), set(runner.relationships))
        writes = len(runner.writes())
        with self.assertRaisesRegex(r2.Neo4jR2Error, "partial"):          # 일반 적재로는 이어 쓰지 않음
            r2.load(runner, self.ref, True, partial["fingerprint"])
        with self.assertRaisesRegex(r2.Neo4jR2Error, "fingerprint"):      # 실패 전 fingerprint로는 재개 불가
            r2.load(runner, self.ref, True, first, resume=True)
        self.assertEqual((dict(runner.nodes), set(runner.relationships), len(runner.writes())), (*kept, writes))
        with self.assertRaisesRegex(r2.Neo4jR2Error, "empty"):             # 빈 DB에는 resume 불가
            r2.load(FakeNeo4j(), self.ref, True, fingerprint(FakeNeo4j(), self.ref), resume=True)
        result = r2.load(runner, self.ref, True, partial["fingerprint"], resume=True)
        self.assertEqual((result["status"], result["totals"]), ("LOADED", {"nodes": 1135, "relationships": 2676}))


class CrossDbTests(unittest.TestCase):
    """실제 B2 청크 1,139개 payload를 담은 FakeQdrant + 적재된 FakeNeo4j로 교차 검증 (벡터는 sample_only)."""

    @classmethod
    def setUpClass(cls):
        cls.ref = Reference.get()
        metadata = hd.load_payload_metadata()
        hashes = {d["document_id"]: d["content_hash"] for d in cls.ref["documents"]}
        points = {}
        for index, chunk in enumerate(cls.ref["chunks"]):
            payload = store.build_point_payload(chunk, policy_meta=store.metadata_for_chunk(metadata, chunk),
                                                content_hash=hashes[chunk["document_id"]], run_info=seed.DUMP_RUN)
            points[chunk["point_id"]] = {"payload": payload, "vector": [float(index % 7) / 7.0 + 0.01] * 1024}
        cls.points = points
        cls.digest = restore.vector_digest(points)

    def setUp(self):
        self.qdrant = FakeQdrant(self.points)
        self.neo4j = FakeNeo4j()
        r2.load(self.neo4j, self.ref, True, fingerprint(self.neo4j, self.ref))

    def test_cross_check_passes_read_only(self):
        neo4j_writes = len(self.neo4j.writes())
        result = r2.cross_check(self.qdrant, self.neo4j, self.ref, vector_digest=self.digest)
        self.assertEqual((result["status"], result["qdrant_points"], result["db_writes"]), ("PASS", 1139, 0))
        self.assertEqual(result["cross_db"]["status"], "PASS")
        self.assertEqual(result["cross_db"]["failures"], [])
        self.assertTrue(result["full_index_verification"]["passed"])
        self.assertTrue(result["qdrant_unchanged"])
        self.assertEqual(self.qdrant.writes, [])
        self.assertEqual(len(self.neo4j.writes()), neo4j_writes)

    def test_cross_check_refuses_wrong_qdrant_or_unloaded_neo4j(self):
        with self.assertRaisesRegex(r2.Neo4jR2Error, "digest"):
            r2.cross_check(self.qdrant, self.neo4j, self.ref, vector_digest="0" * 64)
        shared = FakeQdrant(self.points, extra_collections=("exp_b2_qwen3emb06b_v1",))
        with self.assertRaises(qdrant_r2.UpdateError):
            r2.cross_check(shared, self.neo4j, self.ref, vector_digest=self.digest)
        with self.assertRaisesRegex(r2.Neo4jR2Error, "일치하지 않아"):
            r2.cross_check(self.qdrant, FakeNeo4j(), self.ref, vector_digest=self.digest)
        self.assertEqual((self.qdrant.writes, shared.writes), ([], []))

    def test_cross_check_detects_hash_mismatch(self):
        # Neo4j 쪽 변경은 verify_loaded_graph가 먼저 막는다.
        key = ("Document", self.ref["chunks"][0]["document_id"])
        original = self.neo4j.nodes[key]
        self.neo4j.nodes[key] = dict(original, content_hash="0" * 64)
        with self.assertRaisesRegex(r2.Neo4jR2Error, "일치하지 않아"):
            r2.cross_check(self.qdrant, self.neo4j, self.ref, vector_digest=self.digest)
        self.neo4j.nodes[key] = original
        # Qdrant payload 해시가 Neo4j와 다르면 verify_cross_db가 MISMATCH로 잡는다 (벡터는 그대로).
        point_id = self.ref["chunks"][0]["point_id"]
        self.qdrant.collections[qdrant_r2.COLLECTION][point_id]["payload"]["content_hash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, r"Cross-DB validation failed: status=MISMATCH.*failures=1"):
            r2.cross_check(self.qdrant, self.neo4j, self.ref, vector_digest=self.digest)
        self.assertEqual(self.qdrant.writes, [])


class CliTests(unittest.TestCase):
    def test_write_modes_require_flags(self):
        for extra in (["--load"], ["--load", "--confirm-write"], ["--resume", "--expect-fingerprint", "x"],
                      ["--cross-db"], ["--load", "--verify", "--confirm-write", "--expect-fingerprint", "x"]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                r2.main(["--neo4j-url", URL] + extra)

    def test_bad_url_fails_before_any_connection(self):
        with patch("neo4j.GraphDatabase.driver", side_effect=AssertionError("접속 금지")):
            for url in ("bolt://127.0.0.1:7687", "neo4j://127.0.0.1:56487"):
                with self.subTest(url=url):
                    self.assertEqual(r2.main(["--neo4j-url", url]), 1)

    def test_dry_run_cli_uses_one_verified_driver_for_the_runner(self):
        from neo4j.exceptions import AuthError
        good = SimpleNamespace(verify_connectivity=lambda: None, close=lambda: None)

        def factory(url, auth, **kwargs):
            assert url == URL
            if auth != ("neo4j", ENV[r2.PASSWORD_ENV]):
                def deny():
                    raise AuthError("sample_only")
                return SimpleNamespace(verify_connectivity=deny, close=lambda: None)
            return good

        made = []

        def runner_factory(driver, database=None):
            made.append((driver, database))
            return FakeNeo4j()

        with tempfile.TemporaryDirectory() as folder, \
                patch.dict("os.environ", {**ENV, "NEO4J_URI": "bolt://127.0.0.1:7687"}), \
                patch("neo4j.GraphDatabase.driver", side_effect=factory), \
                patch.object(lg, "Neo4jDriverRunner", side_effect=runner_factory):
            report = Path(folder) / "dry.json"
            self.assertEqual(r2.main(["--neo4j-url", URL, "--report", str(report)]), 0)
            output = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(made, [(good, "neo4j")])
        self.assertEqual((output["state"], output["auth_enforced"], output["db_writes"]), ("empty", True, 0))
        self.assertNotIn(ENV[r2.PASSWORD_ENV], json.dumps(output))


class ComposeStaticTests(unittest.TestCase):
    """docs/development/issue91_r2_neo4j.compose.yaml 정적 검사 + #91 Qdrant Compose와 분리 확인."""

    FOLDER = Path(r2.ROOT) / "docs" / "development"
    DIGEST = "sha256:42ef3ebfd671ebd01fdebb6dcbe80cf7b45eef2aa3d346ef4132f7ef31e67d2f"

    def body(self, name):
        lines = [line.split("#", 1)[0].rstrip() for line in (self.FOLDER / name).read_text(encoding="utf-8").splitlines()]
        return [line for line in lines if line.strip()]

    def test_project_image_ports_memory_and_auth(self):
        body = self.body("issue91_r2_neo4j.compose.yaml")
        self.assertIn("name: issue91-r2-neo4j-20261119", body)
        self.assertIn(f"    image: neo4j:5.26-community@{self.DIGEST}", body)
        ports = [line.strip() for line in body if line.strip().startswith("- \"")]
        self.assertEqual(ports, [f"- \"127.0.0.1:{r2.ISOLATED_BOLT_PORT}:7687\""])      # HTTP 7474 미노출
        auth = [line for line in body if "NEO4J_AUTH" in line]
        self.assertEqual(len(auth), 1)
        self.assertRegex(auth[0], r"neo4j/\$\{" + r2.PASSWORD_ENV + r":\?[^}]+\}$")
        self.assertNotIn(":-", auth[0])
        for line in ("    mem_limit: 1280m", "    cpus: 2", "      NEO4J_server_memory_heap_max__size: 256m"):
            self.assertIn(line, body)
        self.assertIn(self.DIGEST, (self.FOLDER / "issue43_restore.compose.yaml").read_text(encoding="utf-8"))

    def test_only_new_project_volumes_and_no_shared_resources(self):
        body = self.body("issue91_r2_neo4j.compose.yaml")
        text = "\n".join(body)
        for forbidden in ("external", "container_name", "network_mode", "networks:", "7474", "qdrant"):
            self.assertNotIn(forbidden, text)
        mounts = [line.strip() for line in body if line.strip().startswith("- ") and ":/" in line]
        self.assertEqual(mounts, ["- neo4j_data:/data", "- neo4j_logs:/logs"])
        self.assertEqual(body[-3:], ["volumes:", "  neo4j_data:", "  neo4j_logs:"])
        self.assertFalse((self.FOLDER / ".env").exists())

    def test_qdrant_compose_is_separate_and_unchanged(self):
        qdrant = "\n".join(self.body("issue91_r2_qdrant.compose.yaml"))
        self.assertNotIn("neo4j", qdrant)
        self.assertIn("name: issue91-r2-qdrant-20261119", qdrant)
        self.assertIn(f"127.0.0.1:{qdrant_r2.ISOLATED_QDRANT_PORT}:6333", qdrant)
        self.assertNotEqual(r2.ISOLATED_BOLT_PORT, qdrant_r2.ISOLATED_QDRANT_PORT)


if __name__ == "__main__":
    unittest.main()
