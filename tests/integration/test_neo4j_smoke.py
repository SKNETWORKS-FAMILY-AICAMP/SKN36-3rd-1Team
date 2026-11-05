"""
Neo4j 적재 결과 스모크 테스트 — 실제 서버에 "읽기 전용"으로 접속한다.

실행 방법 (프로젝트 루트, .venv):
    set RUN_NEO4J_SMOKE=1                      (PowerShell: $env:RUN_NEO4J_SMOKE="1")
    .venv\\Scripts\\python.exe -m unittest tests/integration/test_neo4j_smoke.py -v

결과 구분
- RUN_NEO4J_SMOKE가 1이 아니면          → 모두 skip, 사유 "NOT_RUN"           (서버에 접속하지 않음)
- 서버 접속 불가·DB가 비어 있으면       → 모두 skip, 사유 "DATA_NOT_READY: …" (실패가 아니라 미실행)
- 데이터가 있는데 원본과 다르면         → FAIL                                (실제 문제)

이 테스트는 노드·관계·스키마를 만들거나 바꾸거나 지우지 않는다.
- 모든 조회는 Neo4jDriverRunner의 읽기 전용 세션(default_access_mode="READ")으로 한다.
- 쓰기 메서드(run_schema·write_batch)는 호출되면 바로 실패하도록 막아 둔다.
.env의 비밀값(비밀번호 등)은 출력하지 않는다.
"""

import logging
import os
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXPECTED_SERVER_SERIES = "5.26."      # docker-compose.yml의 neo4j:5.26-community
RUN_SMOKE = os.environ.get("RUN_NEO4J_SMOKE") == "1"


def _read_only_runner_class(base):
    class ReadOnlyRunner(base):
        """읽기만 허용하는 실행기. 쓰기 메서드를 부르면 테스트가 실패한다."""

        def run_schema(self, statement):
            raise AssertionError("스모크 테스트에서 스키마 쓰기 시도")

        def write_batch(self, query, rows, expected):
            raise AssertionError("스모크 테스트에서 데이터 쓰기 시도")
    return ReadOnlyRunner


@unittest.skipUnless(RUN_SMOKE, "NOT_RUN: RUN_NEO4J_SMOKE=1 일 때만 실제 서버에 접속한다")
class TestNeo4jLoadedGraphSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 드라이버가 서버 알림을 경고 로그로 화면에 쏟아내지 않게 한다. 알림 자체는 끄지 않고 runner.notifications로 모은다.
        logging.getLogger("neo4j").setLevel(logging.ERROR)
        from neo4j import GraphDatabase

        from database import embedding as em
        from database.neo4j import load_graph as lg

        cls.lg = lg
        em.load_env_file()   # .env (기존 환경 변수는 덮어쓰지 않음)
        uri, user, password = (os.environ.get(name) for name in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"))
        if not (uri and user and password):
            raise unittest.SkipTest("DATA_NOT_READY: NEO4J_URI·NEO4J_USER·NEO4J_PASSWORD 중 일부가 없음")
        try:
            cls.driver = GraphDatabase.driver(uri, auth=(user, password))
            cls.driver.verify_connectivity()
            cls.server_agent = cls.driver.get_server_info().agent
        except Exception as error:   # 서버 꺼짐·인증 실패 등 (메시지에 비밀값을 넣지 않는다)
            raise unittest.SkipTest(f"DATA_NOT_READY: Neo4j 서버에 접속할 수 없음 ({type(error).__name__})")
        cls.runner = _read_only_runner_class(lg.Neo4jDriverRunner)(cls.driver)
        cls.graph = lg.read_graph_input()
        totals = cls.runner.read_rows(lg.QUERY_TOTAL_COUNTS)[0]
        if totals["nodes"] == 0 and totals["relationships"] == 0:
            cls.driver.close()
            raise unittest.SkipTest("DATA_NOT_READY: DB가 비어 있음 (적재 전)")
        cls.totals = totals

    @classmethod
    def tearDownClass(cls):
        driver = getattr(cls, "driver", None)
        if driver is not None:
            driver.close()

    def test_server_version_is_5_26_series(self):
        self.assertTrue(self.server_agent.startswith("Neo4j/" + EXPECTED_SERVER_SERIES), self.server_agent)

    def test_total_node_and_relationship_counts(self):
        self.assertEqual(self.totals["nodes"], sum(len(rows) for rows in self.graph.nodes_by_label.values()))
        self.assertEqual(self.totals["relationships"], len(self.graph.relationships))
        self.assertEqual(self.totals["nodes"], 1034)          # 인계 기준값 (파일 기준 계산과 같아야 함)
        self.assertEqual(self.totals["relationships"], 3615)

    def test_counts_by_label_and_type_match_input(self):
        self.assertEqual(self.lg.snapshot_counts(self.runner), self.lg.expected_counts(self.graph))

    def test_schema_constraints_and_indexes(self):
        report = self.lg.verify_schema(self.runner, phase="post")
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(len(report["constraints"]["matched"]), 9)
        self.assertEqual(len(report["indexes"]["matched"]), 4)

    def test_node_id_sets_match_input(self):
        report = self.lg.verify_node_ids(self.graph, self.runner)
        self.assertTrue(report["ok"], {key: str(value)[:300] for key, value in report["problems"].items()})
        self.assertEqual(report["actual_by_label"], report["expected_by_label"])

    def test_relationship_endpoint_sets_match_input(self):
        report = self.lg.verify_relationship_endpoints(self.graph, self.runner)
        self.assertTrue(report["ok"], {key: str(value)[:300] for key, value in report["problems"].items()})
        self.assertEqual(report["actual_by_type"], report["expected_by_type"])

    def test_node_properties_match_input(self):
        report = self.lg.verify_node_properties(self.graph, self.runner)
        self.assertTrue(report["ok"], {"counts": report["counts"], "unmatched": report["unmatched"],
                                       "examples": str(report.get("examples"))[:500]})
        self.assertEqual(report["compared_nodes"], 1034)

    def test_relationship_properties_match_input(self):
        report = self.lg.verify_relationship_properties(self.graph, self.runner)
        self.assertTrue(report["ok"], {"counts": report["counts"], "unmatched": report["unmatched"],
                                       "not_loaded": report["input_fields_not_loaded"]})
        self.assertEqual(report["compared_relationships"], 3615)

    def test_no_notifications_need_review(self):
        self.lg.verify_loaded_graph(self.graph, self.runner)
        classified = self.lg.classify_notifications(self.runner.notifications)
        self.assertEqual(classified["needs_review"], [], classified["by_code"])

    def test_reads_use_read_only_sessions(self):
        session = self.runner._session(self.lg.READ_ACCESS_MODE)
        try:
            self.assertEqual(session._config.default_access_mode, "READ")
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
