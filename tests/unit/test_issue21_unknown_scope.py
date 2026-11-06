"""검토 상태가 다른 확정 조건의 논리까지 덮어쓰지 않는지 검사한다."""
import copy
import unittest

from scripts.build_handoff_package import build_condition_groups, build_condition_nodes
from scripts.build_clean_dataset import build_condition_edges, rebuild_policy_conditions, rebuild_condition_relationships
from scripts.rebuild_eligibility_candidate import ROOT, hydrate, with_policy_review_status


def sample_graph(requirements, pending_income=False, region_review=False):
    policy = {"policy_id": "POL_SAMPLE", "policy_name": "sample_only", "verification_status": "extracted",
              "source_document_ids": ["DOC_SAMPLE"], "requirements": copy.deepcopy(requirements)}
    if region_review:
        policy["requirements"]["region_review_needed"] = True
    edges = [{"source_id": "POL_SAMPLE", "target_id": cid, "relation": "REQUIRES_CONDITION",
              "target_name": "sample_only", "review_status": "extracted"}
             for cid in ("COND_AGE_19_34", "COND_EDU_SAMPLE_A", "COND_EDU_SAMPLE_B")]
    if pending_income:
        edges.append({"source_id": "POL_SAMPLE", "target_id": "COND_YC_INCOME_SAMPLE",
                      "relation": "REQUIRES_CONDITION", "target_name": "sample_only",
                      "raw_value": "sample_only", "condition_type": "OTHER", "review_status": "needs_review"})
    snapshot = copy.deepcopy((policy, edges))
    result = build_condition_groups([policy], edges, build_condition_nodes(edges))
    assert (policy, edges) == snapshot
    return result


class UnknownScopeTests(unittest.TestCase):
    def test_clear_policy_is_not_defaulted_to_needs_review(self):
        p = {"policy_id": "POL_SAMPLE", "policy_name": "sample_only", "verification_status": "extracted"}
        groups, _, _ = sample_graph({})
        result = with_policy_review_status([p], groups)[0]
        self.assertEqual(result["eligibility_review_status"], "extracted")
        self.assertNotIn("eligibility_review_status", p)
        pending = dict(p, eligibility_review_status="needs_review")
        self.assertEqual(with_policy_review_status([pending], groups)[0]["eligibility_review_status"], "needs_review")

    def test_unknown_policy_still_requires_review(self):
        p = {"policy_id": "POL_SAMPLE", "verification_status": "extracted"}
        groups, _, _ = sample_graph({}, pending_income=True)
        self.assertEqual(with_policy_review_status([p], groups)[0]["eligibility_review_status"], "needs_review")

    def test_explicit_y_age_is_not_tainted_by_pending_income(self):
        p = {"policy_id": "POL_SAMPLE", "policy_name": "sample_only", "verification_status": "extracted",
             "requirements": {"age_limit_yn": "Y", "age_review_needed": False, "min_age": 19, "max_age": 34,
                              "income_condition_code": "sample_only"}}
        edges = build_condition_edges(p)
        age = next(e for e in edges if e["target_id"].startswith("COND_AGE_"))
        income = next(e for e in edges if e["target_id"].startswith("COND_YC_INCOME_"))
        self.assertEqual(age["review_status"], "extracted")
        self.assertEqual(income["review_status"], "needs_review")

    def test_ambiguous_or_invalid_age_stays_pending(self):
        for flag, low, high, review in (("N", 19, 34, False), ("Y", 19, None, False),
                                        ("Y", 34, 19, False), ("Y", 19, 34, True), ("", 19, 34, False)):
            with self.subTest(flag=flag, low=low, high=high, review=review):
                p = {"policy_id": "POL_SAMPLE", "requirements": {"age_limit_yn": flag,
                     "min_age": low, "max_age": high, "age_review_needed": review}}
                age = next(e for e in build_condition_edges(p) if e["target_id"].startswith("COND_AGE_"))
                self.assertEqual(age["review_status"], "needs_review")

    def test_pending_income_does_not_erase_known_education_or(self):
        groups, edges, _ = sample_graph({}, pending_income=True)
        logic = {g["group_id"]: g["logic"] for g in groups}
        # 루트 UNKNOWN은 확인된 연령/학력의 FALSE 판정까지 지웠다. 검토 조건만 UNKNOWN 그룹으로 격리한다.
        self.assertEqual(logic["GRP_POL_SAMPLE_ROOT"], "AND")
        self.assertEqual(logic["GRP_POL_SAMPLE_OR_EDU"], "OR")
        self.assertEqual(logic["GRP_POL_SAMPLE_UNKNOWN_REVIEW"], "UNKNOWN")
        education = [e for e in edges if e["source_id"] == "GRP_POL_SAMPLE_OR_EDU"]
        self.assertTrue(education)
        self.assertTrue(all(e["review_status"] == "extracted" for e in education))
        income = next(e for e in edges if e["source_id"] == "GRP_POL_SAMPLE_UNKNOWN_REVIEW" and e["target_id"].startswith("COND_YC_INCOME"))
        self.assertEqual(income["review_status"], "needs_review")
        self.assertFalse(any(e["source_id"] == "GRP_POL_SAMPLE_ROOT" and e["target_id"].startswith("COND_YC_INCOME") for e in edges))

    def test_region_review_does_not_erase_known_condition_logic(self):
        groups, edges, _ = sample_graph({}, region_review=True)
        # AND/OR는 유지되고, 지역 검토는 별도 UNKNOWN 그룹으로만 남아 TRUE 확정을 막는다.
        self.assertEqual([g["logic"] for g in groups], ["AND", "OR", "UNKNOWN"])
        self.assertTrue(groups[2]["group_id"].endswith("_UNKNOWN_UNSTRUCTURED"))
        root = next(e for e in edges if e["relation"] == "HAS_ROOT_CONDITION_GROUP")
        self.assertEqual(root["review_status"], "needs_review")

    def test_structured_object_alone_is_not_uncertainty(self):
        groups, _, _ = sample_graph({"structured_support_conditions": {
            "연령조건": {"대상연령_시작": 19, "대상연령_종료": 34}}})
        self.assertEqual([g["logic"] for g in groups], ["AND", "OR"])

    def test_pending_education_is_still_unknown(self):
        p = {"policy_id": "POL_SAMPLE", "policy_name": "sample_only", "requirements": {}}
        es = [{"source_id": "POL_SAMPLE", "relation": "REQUIRES_CONDITION", "target_id": cid,
               "target_name": "sample_only", "review_status": "needs_review"}
              for cid in ("COND_EDU_SAMPLE_A", "COND_EDU_SAMPLE_B")]
        groups, _, _ = build_condition_groups([p], es, build_condition_nodes(es))
        self.assertEqual([g["logic"] for g in groups], ["UNKNOWN", "UNKNOWN"])

    def test_missing_conditions_stay_unknown_without_unrestricted_claim(self):
        p = {"policy_id": "POL_SAMPLE", "policy_name": "sample_only", "requirements": {}}
        groups, edges, _ = build_condition_groups([p], [], {})
        self.assertEqual(groups[0]["logic"], "UNKNOWN")
        self.assertNotIn("전 국민 개방형", groups[0]["description"])
        self.assertEqual(edges[0]["review_status"], "needs_review")

    def test_real_yc002_keeps_known_or_and_uncertain_income_age(self):
        policies = rebuild_policy_conditions(hydrate(ROOT / "data/releases/v1.4.0-candidate"))
        policy = next(p for p in policies if p["policy_id"] == "POL_YC_002")
        es = rebuild_condition_relationships([policy], [])
        groups, _, _ = build_condition_groups([policy], es, build_condition_nodes(es))
        logic = {g["group_id"]: g["logic"] for g in groups}
        self.assertEqual(logic["GRP_POL_YC_002_ROOT"], "AND")
        self.assertEqual(logic["GRP_POL_YC_002_OR_EDU"], "OR")
        self.assertEqual(logic["GRP_POL_YC_002_OR_SP"], "OR")
        self.assertEqual(logic["GRP_POL_YC_002_UNKNOWN_EXCL"], "UNKNOWN")
        self.assertEqual(logic["GRP_POL_YC_002_UNKNOWN_REVIEW"], "UNKNOWN")
        self.assertIsNone(policy["requirements"]["age_limit"])
        self.assertTrue(all(e["review_status"] == "needs_review" for e in es if e["target_id"].startswith(("COND_AGE_", "COND_YC_INCOME_"))))


if __name__ == "__main__":
    unittest.main()
