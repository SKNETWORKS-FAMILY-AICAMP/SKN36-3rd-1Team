"""
Hybrid 검색 프로필·데이터 품질 경고 스모크 테스트 — 실제 Qdrant·Neo4j·Ollama에 "읽기 전용"으로 접속한다.

실행 방법 (프로젝트 루트, .venv):
    $env:RUN_HYBRID_SMOKE="1"; .venv\\Scripts\\python.exe -m unittest tests/integration/test_hybrid_profiles_smoke.py -v

결과 구분
- RUN_HYBRID_SMOKE가 1이 아니면 → 모두 skip "NOT_RUN" (서버에 접속하지 않음)
- 컬렉션·Neo4j가 준비되지 않았으면 → skip "DATA_NOT_READY: …"
검사 내용
- b2_06b_baseline(0.6B, 1024)·b2_4b_instruct(4B, 2560, instruct_v1) 실제 검색, 컬렉션 실제 차원
- 모델·컬렉션 불일치 → 검색 전 SearchProfileError, 같은 프로필 재검색 결과 동일
- UNKNOWN 판정 보류, 무주택·소득·지역·연령·취업·제외·빈 NOT 의심 경고 (모두 확정 아님, 공식 원문 미검증)
- 테스트 전후 Qdrant point 수와 Neo4j 노드·관계 수 동일
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
A, B = "b2_06b_baseline", "b2_4b_instruct"
GOV_006_QUESTION = "수산자원보호 직접지불금을 부정하게 받으면 어떤 벌칙이 있나요?"
POL_003_QUESTION = "신혼부부전용 전세자금 대출의 금리는 얼마인가요?"


@unittest.skipUnless(RUN_SMOKE, "NOT_RUN: RUN_HYBRID_SMOKE=1 일 때만 실제 서버에 접속한다")
class TestHybridProfilesSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.getLogger("neo4j").setLevel(logging.ERROR)
        warnings.filterwarnings("ignore", message="Api key is used with an insecure connection.")
        from database import hybrid_search as hs
        from database.qdrant import store
        cls.hs, cls.store = hs, store
        try:
            cls.client = store.get_client()
            cls.collections = {name: hs.resolve_search_profile(name)["collection"] for name in (A, B)}
            for collection in cls.collections.values():
                if not cls.client.collection_exists(collection):
                    raise unittest.SkipTest(f"DATA_NOT_READY: 컬렉션 없음 {collection}")
            cls.points_before = {c: store.count_points(cls.client, c) for c in cls.collections.values()}
        except unittest.SkipTest:
            raise
        except Exception as error:
            raise unittest.SkipTest(f"DATA_NOT_READY: Qdrant 접속 불가 ({type(error).__name__})")
        try:
            cls._runner_context = hs.open_neo4j_runner()
            cls.runner = cls._runner_context.__enter__()
        except hs.HybridSearchBackendError as error:
            raise unittest.SkipTest(f"DATA_NOT_READY: Neo4j 접속 불가 ({error.backend})")
        cls.graph_before = cls.runner.read_rows(hs.lg.QUERY_TOTAL_COUNTS)[0]
        cls.results = {(name, question): hs.search_policy_context(question, k=5, client=cls.client, runner=cls.runner,
                                                                  profile=name)
                       for name in (A, B) for question in (GOV_006_QUESTION, POL_003_QUESTION)}

    @classmethod
    def tearDownClass(cls):
        context = getattr(cls, "_runner_context", None)
        if context is not None:
            context.__exit__(None, None, None)

    def _policy(self, result, policy_id):
        found = [p for p in result["policies"] if p["policy_id"] == policy_id]
        self.assertTrue(found, f"{policy_id} 없음: {[p['policy_id'] for p in result['policies']]}")
        return found[0]

    def test_profiles_search_full_collections_with_real_dimensions(self):
        expected = {A: (1024, None), B: (2560, "instruct_v1")}
        for name, (dim, template) in expected.items():
            meta = self.results[(name, GOV_006_QUESTION)]["search_profile"]
            with self.subTest(profile=name):
                self.assertEqual((meta["mode"], meta["name"], meta["collection"]), ("profile", name, self.collections[name]))
                self.assertEqual((meta["embedding_dim"], meta["collection_vector_size"]), (dim, dim))
                self.assertEqual(meta["query_template_name"], template or "none")
                actual = self.client.get_collection(self.collections[name]).config.params.vectors.size
                self.assertEqual(actual, dim)
                self.assertEqual(self.results[(name, GOV_006_QUESTION)]["static_quality_flags"]["status"], "applied")

    def test_default_call_uses_full_baseline_not_legacy_107(self):
        result = self.hs.search_policy_evidence(GOV_006_QUESTION, k=5, client=self.client)
        self.assertEqual(result["collection"], "exp_b2_qwen3emb06b_full_v1")
        self.assertNotEqual(result["collection"], self.hs.LEGACY_EXPERIMENT_COLLECTION)

    def test_mismatch_errors_before_search(self):
        small = self.hs.em.make_ollama_embedder(model="qwen3-embedding:0.6b", expected_dim=1024)
        with self.assertRaises(self.hs.SearchProfileError):                     # 4B 프로필에 0.6B embedder
            self.hs.search_policy_evidence(GOV_006_QUESTION, client=self.client, profile=B, embedder=small)
        with self.assertRaises(self.hs.SearchProfileError):                     # 4B 컬렉션 직접 지정 + 기본 0.6B
            self.hs.search_policy_evidence(GOV_006_QUESTION, client=self.client, collection=self.collections[B])
        with self.assertRaises(self.hs.SearchProfileError):                     # 4B 프로필에서 지시문 누락 시도
            self.hs.search_policy_evidence(GOV_006_QUESTION, client=self.client, profile=B, query_template=None)

    def test_same_profile_is_reproducible(self):
        for name in (A, B):
            again = self.hs.search_policy_evidence(GOV_006_QUESTION, k=5, client=self.client, profile=name)
            first = self.results[(name, GOV_006_QUESTION)]
            with self.subTest(profile=name):
                # 처음 결과(정책별로 묶임)를 순위 순으로 펼쳐 다시 검색한 Top-5와 비교한다
                ranked = sorted((h["rank"], h["chunk_id"]) for p in first["policies"] for h in p["evidence"])
                self.assertEqual([c for _r, c in ranked], [h["chunk_id"] for h in again["hits"]])
                self.assertEqual(again["search_profile"], first["search_profile"])

    def test_unknown_condition_is_held_not_judged(self):
        for name in (A, B):
            entry = self._policy(self.results[(name, POL_003_QUESTION)], "POL_003")
            with self.subTest(profile=name):
                self.assertTrue(entry["data_quality"]["hold_auto_eligibility"])
                self.assertIn("UNKNOWN_CONDITION_GROUP", entry["data_quality"]["hold_reasons"])
                self.assertFalse(entry["graph"]["eligibility"]["evaluated"])
                unknown = [w for w in entry["data_quality"]["warnings"] if w["code"] == "UNKNOWN_CONDITION_GROUP"][0]
                self.assertEqual(unknown["handoff_checks"][0]["evidence_level"], "keyword_detected")

    def test_gov24_suspected_patterns_on_real_search(self):
        entry = self._policy(self.results[(A, GOV_006_QUESTION)], "POL_GOV_006")
        by_code = {w["code"]: w for w in entry["data_quality"]["warnings"]}
        for code in ("SUSPECTED_HOMELESS_REQUIRED_WITH_HOUSEHOLD_OR", "SUSPECTED_INCOME_ALL_BANDS_OR"):
            with self.subTest(code=code):
                self.assertEqual(by_code[code]["level"], "suspected")
                self.assertTrue(by_code[code]["handoff_checks"])                    # 인계 대조 결과가 붙음
                self.assertFalse(by_code[code]["official_source_checked"])
        self.assertFalse(entry["graph"]["eligibility"]["evaluated"])

    def test_graph_and_static_warnings_on_real_policies(self):
        context = self.hs.get_policy_graph_context(["POL_GOV_048", "POL_GOV_011", "POL_005", "POL_009"],
                                                   runner=self.runner)["policies"]
        flags = self.hs.dqf.load_flags()["flags_by_policy"]
        expected = {"POL_GOV_048": "SUSPECTED_REGION_GRANULARITY_LOST", "POL_GOV_011": "AGE_SOURCE_GRAPH_MISMATCH",
                    "POL_005": "EMPTY_NOT_GROUP", "POL_009": "EXCLUSION_NOT_MODELED"}
        for policy_id, code in expected.items():
            quality = self.hs.assess_policy_data_quality(context[policy_id], flags.get(policy_id))
            with self.subTest(policy=policy_id):
                self.assertIn(code, {w["code"] for w in quality["warnings"]})
                self.assertTrue(quality["hold_auto_eligibility"])
        quality = self.hs.assess_policy_data_quality(context["POL_005"], flags.get("POL_005"))
        self.assertIn("EMPLOYMENT_NOT_MODELED", {w["code"] for w in quality["warnings"]})

    def test_databases_unchanged(self):
        self.assertEqual({c: self.store.count_points(self.client, c) for c in self.collections.values()},
                         self.points_before)
        self.assertEqual(self.points_before, {"exp_b2_qwen3emb06b_full_v1": 1081, "exp_b2_qwen3emb4b_full_v1": 1081})
        self.assertEqual(self.runner.read_rows(self.hs.lg.QUERY_TOTAL_COUNTS)[0], self.graph_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
