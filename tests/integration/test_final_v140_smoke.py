"""최종 v1.4.0 전용 읽기 스모크. RUN_FINAL_V140_SMOKE=1로 명시 실행한다.

레거시 4B/instruct 프로필을 요구하지 않으며 실제 0.6B·Qdrant·Neo4j·checker를 연결한다.
정책 필터 검사는 배선/근거 연결 검사이며 검색 품질 평가가 아니다.

대상: Issue #105 R3D Verify 환경 (docs/development/ISSUE105_R3_RUNBOOK.md §5.11·§5.12)
- Qdrant http://127.0.0.1:56756, Neo4j bolt://127.0.0.1:56787, Ollama 127.0.0.1/localhost:11434
- 연결 전에 대상 주소·데이터셋 버전·manifest를 확인한다. 인증값은 읽어 출력하지 않는다.
- 읽기 전용: Neo4j 노드·관계 수와 Qdrant Point 수를 실행 전후로 비교한다 (테스트 실패 시에도 class cleanup에서 비교).

Issue #102: 실제 검색 결과(서비스 경로)의 정적 품질 플래그 적용과 #40 소득 UNKNOWN 부분 판정 동시 적용을 확인한다.
- 적용군: search_policy_context()가 돌려준 data_quality (정적 플래그 포함)
- 대조군: 같은 실제 그래프 문맥으로 assess_policy_data_quality(..., static_flags=[]) (그래프 계산 경고는 그대로)
- 사용자 프로필은 sample_only (Checker가 읽는 age·region·child_age_months만 사용)
"""

import os
import unittest

EXPECTED_DATASET_VERSION = "1.4.0-candidate"
EXPECTED_MANIFEST_SHA256 = "b31307223f7334ffb7588d55670318d07d36e3fc18a78f62e8c961fdad04f3da"
ALLOWED_QDRANT_URLS = {"http://127.0.0.1:56756"}
ALLOWED_NEO4J_URIS = {"bolt://127.0.0.1:56787"}
ALLOWED_OLLAMA_URLS = {"http://127.0.0.1:11434", "http://localhost:11434"}
# Runbook §5.11·§5.12 (R3D 최종 번들) + full_index.INDEX_CONFIGS["A_B2_V140"]
EXPECTED_TOTALS = {"nodes": 1147, "relationships": 2702}
EXPECTED_B2_POINTS = 1249
EXPECTED_REQUIRES_CONDITION = 211
B2_COLLECTION = "exp_b2_qwen3emb06b_v140_v1"
SEARCH_PROFILE = "b2_06b_v140"
EMBEDDING_MODEL = "qwen3-embedding:0.6b"
EMBEDDING_DIM = 1024

ORIGINAL_POLICIES = ("POL_005", "POL_GOV_006", "POL_GOV_011", "POL_009")
# Issue #102: 릴리스 JSONL·v3 정적 플래그로 확인한 대상 (2026-11-25)
#   POL_GOV_002: 루트 AND [AGE 0~65], 정적 S6 EMPLOYMENT_NOT_MODELED → employment
#   POL_GOV_009: 루트 AND [AGE 0~120], 정적 S10 MARRIAGE_CHILD_NOT_MODELED → children
#   POL_GOV_039: 루트 AND [AGE 0~120] + 소득 UNKNOWN 하위 그룹(#40 범위 축소 허용), 정적 S6·S10 → employment·children
#   POL_GOV_042: 루트 AND [AGE 0~17] + 소득 UNKNOWN 하위 그룹(#40 범위 축소 허용), 정적 S10 → children
STATIC_EXPECTED = {
    "POL_GOV_002": {"EMPLOYMENT_NOT_MODELED": ["employment"]},
    "POL_GOV_009": {"MARRIAGE_CHILD_NOT_MODELED": ["children"]},
    "POL_GOV_039": {"EMPLOYMENT_NOT_MODELED": ["employment"], "MARRIAGE_CHILD_NOT_MODELED": ["children"]},
    "POL_GOV_042": {"MARRIAGE_CHILD_NOT_MODELED": ["children"]},
}
PROFILE = {"age": 30, "region": "서울특별시", "child_age_months": 10}   # sample_only (Issue #102 사용자 스캔과 같은 값)


def _guard_targets():
    """연결 전에 대상·데이터를 확인한다. 값(특히 인증값)은 메시지에 넣지 않는다."""
    from database import embedding as em
    from database import full_index as fi
    from database import handoff_data as hd
    from database.qdrant import store

    if hd.dataset_version() != EXPECTED_DATASET_VERSION:
        raise ValueError(f"Final smoke requires {EXPECTED_DATASET_VERSION}")
    if fi.manifest_sha256() != EXPECTED_MANIFEST_SHA256:
        raise ValueError("manifest SHA-256이 R3D 기준과 다름")
    qdrant_url, _api_key = store.resolve_connection_settings()       # 연결하지 않음. 키는 쓰지 않음
    if qdrant_url.rstrip("/") not in ALLOWED_QDRANT_URLS:
        raise ValueError("QDRANT_URL이 R3D Verify 대상이 아님 (값은 출력하지 않음)")
    em.load_env_file()                                                # override=False: 실행기와 같은 값
    if os.environ.get("NEO4J_URI", "").strip() not in ALLOWED_NEO4J_URIS:
        raise ValueError("NEO4J_URI가 R3D Verify 대상이 아님 (값은 출력하지 않음)")
    if not (os.environ.get("NEO4J_USER") and os.environ.get("NEO4J_PASSWORD")):
        raise ValueError("NEO4J_USER 또는 NEO4J_PASSWORD 미설정 (값은 출력하지 않음)")
    if em.resolve_ollama_base_url() not in ALLOWED_OLLAMA_URLS:
        raise ValueError("OLLAMA_BASE_URL이 로컬 11434가 아님 (값은 출력하지 않음)")


@unittest.skipUnless(os.environ.get("RUN_FINAL_V140_SMOKE") == "1", "NOT_RUN: final v1.4.0 smoke opt-in required")
class FinalV140Smoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _guard_targets()
        from database import hybrid_search as hs
        from database.qdrant import store
        from backend.eligibility.checker import check_eligibility

        cls.hs, cls.checker = hs, staticmethod(check_eligibility)
        cls.client = store.get_client()
        cls.addClassCleanup(cls.client.close)
        cls.context = hs.open_neo4j_runner()
        cls.runner = cls.context.__enter__()
        cls.addClassCleanup(cls.context.__exit__, None, None, None)
        cls.before = cls.runner.read_rows(hs.lg.QUERY_TOTAL_COUNTS)[0]
        cls.collection = B2_COLLECTION
        cls.points_before = cls.client.count(cls.collection, exact=True).count
        # cleanup은 역순 실행 → 연결을 닫기 전에 읽기 전후 건수를 비교한다 (테스트·setUpClass 실패 시에도)
        cls.addClassCleanup(cls._assert_counts_unchanged)
        cls.session = hs.HybridSearchSession(client=cls.client, runner=cls.runner, profile=SEARCH_PROFILE)
        cls.addClassCleanup(cls.session.close)
        cls.results = {}
        for policy_id in ORIGINAL_POLICIES + tuple(STATIC_EXPECTED):
            policy = cls.runner.read_rows("MATCH (p:Policy {policy_id:$id}) RETURN p.policy_name AS name", {"id": policy_id})[0]
            cls.results[policy_id] = cls.session.search_policy_context(
                policy["name"] + " 신청 조건과 기간", filters={"policy_id": policy_id}, k=5)

    @classmethod
    def _assert_counts_unchanged(cls):
        after = cls.runner.read_rows(cls.hs.lg.QUERY_TOTAL_COUNTS)[0]
        points = cls.client.count(cls.collection, exact=True).count
        if after != cls.before or points != cls.points_before:
            raise AssertionError(f"읽기 전용 스모크 중 DB 건수 변경: Neo4j {cls.before}→{after}, "
                                 f"Qdrant {cls.points_before}→{points}")

    def entry(self, policy_id):
        result = self.results[policy_id]
        self.assertFalse(result["missing_policy_ids"])
        return next(p for p in result["policies"] if p["policy_id"] == policy_id)

    def test_final_counts(self):
        self.assertEqual(self.before, EXPECTED_TOTALS)
        self.assertEqual(self.points_before, EXPECTED_B2_POINTS)
        rows = self.runner.read_rows("MATCH ()-[r:REQUIRES_CONDITION]->() RETURN count(r) AS count")
        self.assertEqual(rows[0]["count"], EXPECTED_REQUIRES_CONDITION)

    def test_all_cross_db_links(self):
        report = self.session.verify_cross_db()
        self.assertEqual(report["collection"], B2_COLLECTION)
        self.assertEqual((report["qdrant_points"], report["qdrant_count"]), (EXPECTED_B2_POINTS, EXPECTED_B2_POINTS))
        self.assertTrue(report["all_points_read"])
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["failures"])
        # Point마다 7개 검사(CROSS_CHECKS) → 검사별 PASS 1,249 (Runbook §5.11: B2 PASS 8,743 = 7 × 1,249)
        self.assertEqual(set(report["summary"]), set(self.hs.CROSS_CHECKS))
        for check, counts in report["summary"].items():
            with self.subTest(check=check):
                self.assertEqual(counts, {"PASS": EXPECTED_B2_POINTS, "MISSING": 0, "MISMATCH": 0, "NOT_TESTED": 0})

    def test_real_hybrid_and_checker_for_four_policies(self):
        for policy_id in ORIGINAL_POLICIES:
            result = self.results[policy_id]
            with self.subTest(policy_id=policy_id):
                entry = self.entry(policy_id)
                self.assertTrue(entry["evidence"])
                self.assertEqual(result["search_profile"]["name"], SEARCH_PROFILE)
                self.assertEqual(result["search_profile"]["embedding_dim"], EMBEDDING_DIM)
                checked = self.checker({"age": 30, "region": "서울특별시"}, entry)  # sample_only profile
                self.assertEqual(checked["policy_id"], policy_id)
                self.assertIn(checked["reception_status"], ("before", "open", "closed", "unknown"))
                self.assertTrue(checked["conditions"])
                self.assertTrue(all(c["status"] in ("eligible", "ineligible", "needs_check") for c in checked["conditions"]))

    def test_search_profile_contract(self):
        profile = self.results["POL_GOV_002"]["search_profile"]
        self.assertEqual((profile["name"], profile["collection"], profile["embedding_model"],
                          profile["embedding_dim"], profile["collection_vector_size"]),
                         (SEARCH_PROFILE, B2_COLLECTION, EMBEDDING_MODEL, EMBEDDING_DIM, EMBEDDING_DIM))

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
        # 최신 릴리스에는 이 레거시 정적 플래그 묶음을 적용하지 않는다.
        # Issue #102: 대신 v1.4.0 전용 목록이 적용된다 (목록 파일 생성 후 실행).
        static = self.results["POL_GOV_006"]["static_quality_flags"]
        self.assertEqual((static["status"], static["file"], static["data_version"]),
                         ("applied", "neo4j_semantic_flags_v1_4_0.json", "1.4.0-candidate"))

    def test_gov011_corrected_age(self):
        rows = self.runner.read_rows(
            "MATCH (:Policy {policy_id:$id})-[:REQUIRES_CONDITION]->(c:EligibilityCondition) "
            "WHERE c.type='AGE' RETURN c.condition_id AS id", {"id": "POL_GOV_011"})
        self.assertEqual({r["id"] for r in rows}, {"COND_AGE_12_24"})

    def test_pol009_dates(self):
        policy = self.entry("POL_009")["graph"]["policy"]
        self.assertEqual((policy["application_start"], policy["application_end"]), ("2024-02-26", "2025-02-25"))
        self.assertIn("2026-03-30", policy["application_status_note"])

    def test_after_school_guide_is_citable(self):
        from backend.rag.context_builder import build_contexts

        result = self.session.search_policy_context(
            "2026년 방과후보육료는 몇 년생까지 받을 수 있나요? 출생일 기준을 알려주세요.", k=10)
        contexts = build_contexts(result["policies"])
        guide = next((row for row in contexts if row["document_id"] == "DOC_BOK_031_MOE_2026"), None)
        self.assertIsNotNone(guide, "교육부 출생일 표가 최종 인용 후보에 있어야 합니다.")
        self.assertEqual(guide["page_number"], 305)
        self.assertIn("ʼ19. 01. 01. ~ ʼ13. 12. 31.", guide["text"])
        self.assertTrue(all("'18. 01. 01. ~ '12. 12. 31." not in row["text"] for row in contexts))

    # --- Issue #102: 실제 검색 결과의 정적 플래그 ---------------------------------------

    def _arms(self, policy_id, profile=PROFILE):
        """(적용군 data_quality, 대조군 data_quality, 적용군 Checker, 대조군 Checker). 같은 실제 그래프·프로필."""
        entry = self.entry(policy_id)
        self.assertEqual(entry["graph_status"], "FOUND")
        treated = entry["data_quality"]

        control_warnings = [
            w for w in treated["warnings"]
            if w.get("source") != "static_flags"
        ]

        control = {
            **treated,
            "warnings": control_warnings,
            **self.hs._hold_summary(control_warnings),
        }
        control_entry = {**entry, "data_quality": control}
        return treated, control, self.checker(profile, entry), self.checker(profile, control_entry)

    def _root_row(self, checked, entry):
        from backend.eligibility.checker import _GROUP_REASONS

        trees = entry["graph"]["eligibility"]["condition_tree"]
        self.assertEqual(len(trees), 1)
        prefix = _GROUP_REASONS[trees[0]["logic"]]
        rows = [row for row in checked["conditions"] if row["field"] == "eligibility" and row["reason"].startswith(prefix)]
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def _rows(self, checked, field):
        return [row["status"] for row in checked["conditions"] if row["field"] == field]

    def _assert_static_flags_applied(self, policy_id, treated, control):
        from database import data_quality_flags as dqf

        self.assertEqual(self.results[policy_id]["static_quality_flags"]["status"], "applied")
        expected = STATIC_EXPECTED[policy_id]
        listed = {flag["code"]: flag["affects"] for flag in dqf.load_flags()["flags_by_policy"][policy_id] if "code" in flag}
        self.assertEqual(listed, expected)                                   # v3 플래그 파일과 고정 기대값 일치
        static = {w["code"]: w for w in treated["warnings"] if w.get("source") == "static_flags"}
        self.assertEqual({code: w["affects"] for code, w in static.items()}, expected)
        for code, warning in static.items():
            self.assertTrue(warning["blocks_auto_eligibility"], code)
            self.assertFalse(warning["official_source_checked"], code)
            self.assertTrue(set(warning["affects"]) <= set(treated["affected_attributes"]), code)
        self.assertTrue(treated["hold_auto_eligibility"])
        self.assertFalse([w for w in control["warnings"] if w.get("source") == "static_flags"])
        # 그래프 계산(동적) 경고는 적용군에도 그대로 있다
        self.assertEqual([w["code"] for w in control["warnings"]],
                         [w["code"] for w in treated["warnings"] if w.get("source") != "static_flags"])

    def test_issue102_static_flags_on_real_search_without_unknown(self):
        for policy_id in ("POL_GOV_002", "POL_GOV_009"):
            with self.subTest(policy_id=policy_id):
                entry = self.entry(policy_id)
                self.assertTrue(entry["evidence"])
                self.assertFalse(entry["graph"]["eligibility"].get("unknown_logic_groups"))
                treated, control, after, before = self._arms(policy_id)
                self._assert_static_flags_applied(policy_id, treated, control)
                affects = {a for values in STATIC_EXPECTED[policy_id].values() for a in values}
                self.assertFalse(affects & set(control["affected_attributes"]))
                # 대조군: 나이(실제 BETWEEN 조건, 30세 포함)와 루트 종합 결과가 적격 → 적용군: 루트만 확인 필요로 보호
                self.assertEqual(self._rows(before, "age"), ["eligible"])
                self.assertEqual(self._root_row(before, entry)["status"], "eligible")
                self.assertEqual(self._rows(after, "age"), ["eligible"])          # 영향 밖 개별 조건은 유지
                self.assertEqual(self._root_row(after, entry)["status"], "needs_check")

    def _assert_issue40_scope(self, policy_id, entry, treated, control):
        from database.unknown_group_scope import income_only_unknown_under_and, scoped_income_quality_attributes

        eligibility = entry["graph"]["eligibility"]
        self.assertTrue(eligibility.get("unknown_logic_groups"))
        self.assertTrue(income_only_unknown_under_and(eligibility))            # 실제 검색 그래프가 #40 요건에 해당
        expected_control_scope = {"income", "region"}

        self.assertEqual(
            scoped_income_quality_attributes(eligibility, control),
            expected_control_scope
        )

        affects = {
            a for values in STATIC_EXPECTED[policy_id].values()
            for a in values
        }

        self.assertEqual(
            scoped_income_quality_attributes(eligibility, treated),
            expected_control_scope | affects
        )

    def test_issue40_scoped_income_with_static_flags_no_certain_failure(self):
        # POL_GOV_039: 나이 0~120(30세 포함) + 소득 UNKNOWN → 개별 나이는 적격 유지, 소득·루트는 확인 필요
        policy_id = "POL_GOV_039"
        entry = self.entry(policy_id)
        treated, control, after, before = self._arms(policy_id)
        self._assert_static_flags_applied(policy_id, treated, control)
        self._assert_issue40_scope(policy_id, entry, treated, control)
        for checked in (before, after):
            self.assertEqual(self._rows(checked, "age"), ["eligible"])
            self.assertTrue(self._rows(checked, "income"))
            self.assertTrue(all(status == "needs_check" for status in self._rows(checked, "income")))
            self.assertEqual(self._root_row(checked, entry)["status"], "needs_check")   # 정보 부족 → 적격 확정 안 함

    def test_issue40_certain_failure_preserved_with_static_flags(self):
        # POL_GOV_042: 나이 0~17(실제 BETWEEN) + 소득 UNKNOWN + 정적 children 보호
        policy_id = "POL_GOV_042"
        entry = self.entry(policy_id)
        treated, control, after, before = self._arms(policy_id)           # 30세: 확정 미충족
        self._assert_static_flags_applied(policy_id, treated, control)
        self._assert_issue40_scope(policy_id, entry, treated, control)
        for checked in (before, after):
            self.assertEqual(self._rows(checked, "age"), ["ineligible"])
            self.assertEqual(self._root_row(checked, entry)["status"], "ineligible")
            self.assertTrue(self._rows(checked, "income"))
            self.assertTrue(all(status == "needs_check" for status in self._rows(checked, "income")))
        in_range = {**PROFILE, "age": 10}                                  # sample_only: 나이 충족
        _t, _c, after_ok, before_ok = self._arms(policy_id, in_range)
        for checked in (before_ok, after_ok):
            self.assertEqual(self._rows(checked, "age"), ["eligible"])
            self.assertEqual(self._root_row(checked, entry)["status"], "needs_check")  # 소득 미확인 → 적격 확정 안 함

    def test_z_counts_unchanged(self):
        self.assertEqual(self.runner.read_rows(self.hs.lg.QUERY_TOTAL_COUNTS)[0], self.before)
        self.assertEqual(self.client.count(self.collection, exact=True).count, self.points_before)


if __name__ == "__main__":
    unittest.main()
