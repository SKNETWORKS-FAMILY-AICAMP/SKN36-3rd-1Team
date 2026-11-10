"""최종 v1.4.0 전용 읽기 스모크. RUN_FINAL_V140_SMOKE=1로 명시 실행한다.

레거시 4B/instruct 프로필을 요구하지 않으며 실제 0.6B·Qdrant·Neo4j·checker를 연결한다.
정책 필터 검사는 배선/근거 연결 검사이며 검색 품질 평가가 아니다.
"""

import os
import unittest


@unittest.skipUnless(os.environ.get("RUN_FINAL_V140_SMOKE") == "1", "NOT_RUN: final v1.4.0 smoke opt-in required")
class FinalV140Smoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from database import handoff_data as hd, hybrid_search as hs
        from database.qdrant import store
        from backend.eligibility.checker import check_eligibility

        if hd.dataset_version() != "1.4.0-candidate":
            raise ValueError("Final smoke requires 1.4.0-candidate")
        cls.hs, cls.checker = hs, staticmethod(check_eligibility)
        cls.client = store.get_client()
        cls.addClassCleanup(cls.client.close)
        cls.context = hs.open_neo4j_runner()
        cls.runner = cls.context.__enter__()
        cls.addClassCleanup(cls.context.__exit__, None, None, None)
        cls.before = cls.runner.read_rows(hs.lg.QUERY_TOTAL_COUNTS)[0]
        cls.collection = "exp_b2_qwen3emb06b_v140_v1"
        cls.points_before = cls.client.count(cls.collection, exact=True).count
        cls.session = hs.HybridSearchSession(client=cls.client, runner=cls.runner, profile="b2_06b_v140")
        cls.addClassCleanup(cls.session.close)
        cls.results = {}
        for policy_id in ("POL_005", "POL_GOV_006", "POL_GOV_011", "POL_009"):
            policy = cls.runner.read_rows("MATCH (p:Policy {policy_id:$id}) RETURN p.policy_name AS name", {"id": policy_id})[0]
            cls.results[policy_id] = cls.session.search_policy_context(
                policy["name"] + " 신청 조건과 기간", filters={"policy_id": policy_id}, k=5)

    def entry(self, policy_id):
        result = self.results[policy_id]
        self.assertFalse(result["missing_policy_ids"])
        return next(p for p in result["policies"] if p["policy_id"] == policy_id)

    def test_final_counts(self):
        self.assertEqual(self.before, {"nodes": 1004, "relationships": 2431})
        self.assertEqual(self.points_before, 1138)
        rows = self.runner.read_rows("MATCH ()-[r:REQUIRES_CONDITION]->() RETURN count(r) AS count")
        self.assertEqual(rows[0]["count"], 136)

    def test_all_cross_db_links(self):
        report = self.session.verify_cross_db()
        self.assertEqual(report["qdrant_points"], 1138)
        self.assertFalse(report["failures"])
        for counts in report["summary"].values():
            self.assertEqual(counts["PASS"], 1138)

    def test_real_hybrid_and_checker_for_four_policies(self):
        for policy_id, result in self.results.items():
            with self.subTest(policy_id=policy_id):
                entry = self.entry(policy_id)
                self.assertTrue(entry["evidence"])
                self.assertEqual(result["search_profile"]["name"], "b2_06b_v140")
                self.assertEqual(result["search_profile"]["embedding_dim"], 1024)
                checked = self.checker({"age": 30, "region": "서울특별시"}, entry)  # sample_only profile
                self.assertEqual(checked["policy_id"], policy_id)
                self.assertIn(checked["reception_status"], ("before", "open", "closed", "unknown"))
                self.assertTrue(checked["conditions"])
                self.assertTrue(all(c["status"] in ("eligible", "ineligible", "needs_check") for c in checked["conditions"]))

    def test_pol005_exclusion_tree(self):
        rows = self.runner.read_rows(
            "MATCH (:ConditionGroup {group_id:$id})-[:CONTAINS_GROUP]->(n:ConditionGroup) "
            "RETURN coalesce(n.group_id,n.condition_id) AS id", {"id": "GRP_POL_005_NOT_EXCL"})
        self.assertEqual({r["id"] for r in rows}, {"GRP_POL_005_OR_EXCL"})
        members = self.runner.read_rows(
            "MATCH (:ConditionGroup {group_id:$id})-[:CONTAINS_CONDITION]->(n:EligibilityCondition) "
            "RETURN n.condition_id AS id", {"id": "GRP_POL_005_OR_EXCL"})
        self.assertEqual(len(members), 13)
        self.assertIn("COND_AGE_75_120", {r["id"] for r in members})

    def test_gov006_corrected_data_not_legacy_static_flags(self):
        entry = self.entry("POL_GOV_006")
        policy = entry["graph"]["policy"]
        self.assertFalse(policy.get("application_start"))
        self.assertFalse(policy.get("application_end"))
        self.assertTrue(policy["application_status_note"])
        # test_hybrid_profiles_smoke.py:122~124는 동결 레거시 정적 경고 기대값이다.
        # 최신 릴리스에는 이 정적 플래그 묶음을 적용하지 않는다.
        self.assertEqual(self.results["POL_GOV_006"]["static_quality_flags"]["status"], "not_applicable")

    def test_gov011_corrected_age(self):
        rows = self.runner.read_rows(
            "MATCH (:Policy {policy_id:$id})-[:REQUIRES_CONDITION]->(c:EligibilityCondition) "
            "WHERE c.type='AGE' RETURN c.condition_id AS id", {"id": "POL_GOV_011"})
        self.assertEqual({r["id"] for r in rows}, {"COND_AGE_12_24"})

    def test_pol009_dates(self):
        policy = self.entry("POL_009")["graph"]["policy"]
        self.assertEqual((policy["application_start"], policy["application_end"]), ("2024-02-26", "2025-02-25"))
        self.assertIn("2026-03-30", policy["application_status_note"])

    def test_z_counts_unchanged(self):
        self.assertEqual(self.runner.read_rows(self.hs.lg.QUERY_TOTAL_COUNTS)[0], self.before)
        self.assertEqual(self.client.count(self.collection, exact=True).count, self.points_before)


if __name__ == "__main__":
    unittest.main()
