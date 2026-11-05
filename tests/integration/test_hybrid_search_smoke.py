"""
Hybrid 검색(Qdrant + Neo4j + Ollama) 스모크 테스트 — 실제 서버에 "읽기 전용"으로 접속한다.

실행 방법 (프로젝트 루트, .venv):
    set RUN_HYBRID_SMOKE=1                      (PowerShell: $env:RUN_HYBRID_SMOKE="1")
    .venv\\Scripts\\python.exe -m unittest tests/integration/test_hybrid_search_smoke.py -v

결과 구분
- RUN_HYBRID_SMOKE가 1이 아니면              → 모두 skip, 사유 "NOT_RUN" (서버에 접속하지 않음)
- 서버 접속 불가·데이터 미적재                → 모두 skip, 사유 "DATA_NOT_READY: …"
- 데이터가 있는데 연결·원문이 기대와 다르면   → FAIL

쓰기 없음: Qdrant는 count·scroll·query만, Neo4j는 읽기 전용 세션만 쓴다(쓰기 메서드는 막아 둔다).
테스트 전후 Qdrant point 수와 Neo4j 노드·관계 수가 같은지도 확인한다. 질문은 원문 근거로 만든 기존 스모크 질문이다(성능 평가 아님).
"""

import logging
import os
import sys
import unittest
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RUN_SMOKE = os.environ.get("RUN_HYBRID_SMOKE") == "1"
EXPECTED_POINTS = 107
# (질문, 기대 정책, 기대 문서, 원문 근거 문자열) — docs/development/experiments/exp1_load_search.py와 같은 질문
QUESTIONS = [
    ("신혼부부 전세자금 대출의 금리는 어떻게 되나요?", "POL_003", "DOC_NHUF_003", "연 1.9% ∼ 연 3.3%"),
    ("아이돌봄서비스 시간제 서비스는 몇 살 아동까지 이용할 수 있나요?", "POL_BOK_001", "DOC_BOK_001", "생후 3개월~12세 이하의 아동"),
    ("콘텐츠 기업이 인턴을 채용하면 1인당 인건비를 얼마 지원받나요?", "POL_YC_003", "DOC_YC_003", "1인당 월 108만원"),
]


@unittest.skipUnless(RUN_SMOKE, "NOT_RUN: RUN_HYBRID_SMOKE=1 일 때만 실제 서버에 접속한다")
class TestHybridSearchSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.getLogger("neo4j").setLevel(logging.ERROR)
        warnings.filterwarnings("ignore", message="Api key is used with an insecure connection.")
        from database import handoff_data as hd
        from database import hybrid_search as hs
        from database.qdrant import store

        cls.hs, cls.store = hs, store
        try:
            cls.client = store.get_client()
            if not cls.client.collection_exists(hs.DEFAULT_COLLECTION):
                raise unittest.SkipTest(f"DATA_NOT_READY: 컬렉션 없음 {hs.DEFAULT_COLLECTION}")
            cls.points_before = store.count_points(cls.client, hs.DEFAULT_COLLECTION)
        except unittest.SkipTest:
            raise
        except Exception as error:
            raise unittest.SkipTest(f"DATA_NOT_READY: Qdrant 접속 불가 ({type(error).__name__})")
        try:
            cls._runner_context = hs.open_neo4j_runner()
            runner = cls._runner_context.__enter__()
        except hs.HybridSearchBackendError as error:
            raise unittest.SkipTest(f"DATA_NOT_READY: Neo4j 접속 불가 ({error.backend})")

        class ReadOnlyRunner(type(runner)):
            def run_schema(self, statement):
                raise AssertionError("스모크 테스트에서 스키마 쓰기 시도")

            def write_batch(self, query, rows, expected):
                raise AssertionError("스모크 테스트에서 데이터 쓰기 시도")
        cls.runner = ReadOnlyRunner(runner.driver)
        cls.graph_before = cls.runner.read_rows(hs.lg.QUERY_TOTAL_COUNTS)[0]
        if cls.graph_before["nodes"] == 0:
            cls._runner_context.__exit__(None, None, None)
            raise unittest.SkipTest("DATA_NOT_READY: Neo4j가 비어 있음")
        cls.documents = {doc["document_id"]: doc for doc in hd.load_documents(verify_hash=True)}
        cls.results = {question: hs.search_policy_context(question, k=5, client=cls.client, runner=cls.runner)
                       for question, *_ in QUESTIONS}

    @classmethod
    def tearDownClass(cls):
        context = getattr(cls, "_runner_context", None)
        if context is not None:
            context.__exit__(None, None, None)

    def test_cross_db_ids_hashes_and_relationships_pass(self):
        report = self.hs.verify_cross_db(client=self.client, runner=self.runner)
        self.assertEqual(report["status"], self.hs.PASS, report["failures"][:5])
        self.assertEqual(report["qdrant_points"], EXPECTED_POINTS)
        for check, counts in report["summary"].items():
            with self.subTest(check=check):
                self.assertEqual(counts[self.hs.PASS], EXPECTED_POINTS, counts)

    def test_expected_policy_and_document_rank_first(self):
        for question, policy_id, document_id, _evidence in QUESTIONS:
            result = self.results[question]
            with self.subTest(question=question):
                top = result["policies"][0]
                self.assertEqual(top["policy_id"], policy_id)
                self.assertEqual(top["evidence"][0]["document_id"], document_id)
                self.assertEqual(top["graph_status"], "FOUND")
                self.assertEqual(result["warnings"], [])

    def test_evidence_text_matches_original_document(self):
        for question, _policy_id, _document_id, evidence in QUESTIONS:
            result = self.results[question]
            hits = [hit for policy in result["policies"] for hit in policy["evidence"]]
            with self.subTest(question=question):
                for hit in hits:
                    source = self.documents[hit["document_id"]]["text"]
                    self.assertEqual(source[hit["char_start"]:hit["char_end"]], hit["text"], hit["chunk_id"])
                self.assertIn(evidence, hits[0]["text"])

    def test_graph_context_links_evidence_document(self):
        for question, policy_id, document_id, _evidence in QUESTIONS:
            entry = self.results[question]["policies"][0]
            with self.subTest(question=question):
                self.assertEqual(entry["graph"]["policy"]["policy_id"], policy_id)
                self.assertIn(document_id, [doc["document_id"] for doc in entry["graph"]["documents"]])
                self.assertEqual(entry["evidence_document_check"][document_id], self.hs.PASS)
                eligibility = entry["graph"]["eligibility"]
                self.assertFalse(eligibility["evaluated"])
                self.assertTrue(eligibility["tree_matches_requires_condition"])
                self.assertEqual(eligibility["structure_issues"], [])

    def test_filters_restrict_results(self):
        result = self.hs.search_policy_evidence("상담 전화번호", {"document_id": "DOC_LH_001"}, k=5, client=self.client)
        self.assertEqual({hit["document_id"] for hit in result["hits"]}, {"DOC_LH_001"})
        result = self.hs.search_policy_evidence("상담 전화번호", {"policy_id": "POL_001"}, k=5, client=self.client)
        self.assertEqual({hit["policy_id"] for hit in result["hits"]}, {"POL_001"})

    def test_databases_unchanged(self):
        self.assertEqual(self.store.count_points(self.client, self.hs.DEFAULT_COLLECTION), self.points_before)
        self.assertEqual(self.points_before, EXPECTED_POINTS)
        self.assertEqual(self.runner.read_rows(self.hs.lg.QUERY_TOTAL_COUNTS)[0], self.graph_before)

    def test_session_reuses_connections_and_matches_function_results(self):
        with self.hs.HybridSearchSession() as session:
            results = [session.search_policy_context(question, k=5) for question, *_ in QUESTIONS]
            self.assertEqual(session.owned_resources, {"qdrant_client": True, "neo4j_driver": True})
            self.assertEqual(session.runner.query_timeout, self.hs.NEO4J_QUERY_TIMEOUT_SECONDS)
        self.assertTrue(session.closed)
        self.assertEqual(session.owned_resources, {"qdrant_client": False, "neo4j_driver": False})
        for (question, *_), result in zip(QUESTIONS, results):
            expected = self.results[question]
            with self.subTest(question=question):
                self.assertEqual([(p["policy_id"], p["best_rank"]) for p in result["policies"]],
                                 [(p["policy_id"], p["best_rank"]) for p in expected["policies"]])
                self.assertEqual([h["chunk_id"] for p in result["policies"] for h in p["evidence"]],
                                 [h["chunk_id"] for p in expected["policies"] for h in p["evidence"]])

    def test_data_quality_warnings_on_real_data(self):
        """경고는 표시만 한다: 순위·근거는 그대로이고, 자격 판정(evaluated)은 False."""
        gov = self.hs.search_policy_context("수산자원보호 직접지불금을 부정하게 받으면 어떤 벌칙이 있나요?", k=5,
                                            client=self.client, runner=self.runner)
        top = gov["policies"][0]
        self.assertEqual(top["policy_id"], "POL_GOV_006")
        codes = {warning["code"] for warning in top["data_quality"]["warnings"]}
        self.assertLessEqual({self.hs.QUALITY_HOMELESS_PROMOTED, self.hs.QUALITY_INCOME_ALL_BANDS,
                              self.hs.QUALITY_EXTRACTED_NOT_VERIFIED}, codes)
        self.assertFalse(top["graph"]["eligibility"]["evaluated"])
        unknown = self.results[QUESTIONS[0][0]]["policies"][0]                      # POL_003 (조건 UNKNOWN)
        self.assertIn(self.hs.QUALITY_UNKNOWN_CONDITION, {w["code"] for w in unknown["data_quality"]["warnings"]})

    def test_no_neo4j_notifications_need_review(self):
        classified = self.hs.lg.classify_notifications(self.runner.notifications)
        self.assertEqual(classified["needs_review"], [], classified["by_code"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
