"""루트 UNKNOWN이 확인된 하위 조건을 지우지 않는지 3값 논리로 검사한다.

docs/data/db_schema.md §3값 논리: AND는 하나라도 FALSE면 FALSE, 그 외 UNKNOWN.
검토 대기 조건은 UNKNOWN 그룹 안에만 두어 FALSE를 만들 수 없어야 한다.
가상 정책은 모두 sample_only이며 실제 정책의 자격을 확정하지 않는다.
"""
import copy
import itertools
import unittest

from collectors.api.gov24_collector import structure_ja_conditions
from scripts.build_clean_dataset import (
    build_condition_edges, get_structured_conditions, rebuild_condition_relationships, rebuild_policy_conditions,
)
from scripts.build_handoff_package import build_condition_groups, build_condition_nodes
from scripts.rebuild_eligibility_candidate import ROOT, hydrate

KNOWN = "extracted"
PENDING = "needs_review"


def edge(cid, status, **extra):
    return {"source_id": "POL_SAMPLE", "relation": "REQUIRES_CONDITION", "target_id": cid,
            "target_name": "sample_only", "evidence_document_id": "DOC_SAMPLE", "review_status": status, **extra}


def graph(edges, requirements=None):
    policy = {"policy_id": "POL_SAMPLE", "policy_name": "sample_only", "verification_status": KNOWN,
              "source_document_ids": ["DOC_SAMPLE"], "requirements": requirements or {}}
    snapshot = copy.deepcopy((policy, edges))
    groups, out, _ = build_condition_groups([policy], edges, build_condition_nodes(edges))
    assert (policy, edges) == snapshot
    return groups, out


def evaluate(groups, edges, truth, pid="POL_SAMPLE"):
    """db_schema 3값 논리 그대로. UNKNOWN 그룹은 자식과 무관하게 UNKNOWN."""
    logic = {g["group_id"]: g["logic"] for g in groups}
    children = {}
    for e in edges:
        if e["relation"] in ("CONTAINS_GROUP", "CONTAINS_CONDITION"):
            children.setdefault(e["source_id"], []).append(e)

    def value(gid):
        if logic[gid] == "UNKNOWN":
            return None
        vals = [value(e["target_id"]) if e["relation"] == "CONTAINS_GROUP" else truth[e["target_id"]]
                for e in children.get(gid, [])]
        if logic[gid] == "AND":
            return False if False in vals else (True if vals and all(vals) else None)
        if logic[gid] == "OR":
            return True if True in vals else (False if vals and all(v is False for v in vals) else None)
        raise AssertionError(logic[gid])

    root = next(e["target_id"] for e in edges if e["relation"] == "HAS_ROOT_CONDITION_GROUP" and e["source_id"] == pid)
    return value(root)


def tree_condition_ids(edges):
    return {e["target_id"] for e in edges if e["relation"] == "CONTAINS_CONDITION"}


def mixed_sample():
    return [edge("COND_AGE_19_34", KNOWN), edge("COND_EDU_SAMPLE_A", KNOWN), edge("COND_EDU_SAMPLE_B", KNOWN),
            edge("COND_YC_INCOME_SAMPLE", PENDING, raw_value="sample_only", condition_type="OTHER")]


class RootSemanticsTests(unittest.TestCase):
    def test_known_conditions_stay_evaluable_under_pending_income(self):
        groups, edges = graph(mixed_sample())
        logic = {g["group_id"]: g["logic"] for g in groups}
        self.assertEqual(logic["GRP_POL_SAMPLE_ROOT"], "AND")
        self.assertEqual(logic["GRP_POL_SAMPLE_OR_EDU"], "OR")
        self.assertEqual(logic["GRP_POL_SAMPLE_UNKNOWN_REVIEW"], "UNKNOWN")
        pending = [e for e in edges if e["source_id"] == "GRP_POL_SAMPLE_UNKNOWN_REVIEW"]
        self.assertEqual([e["target_id"] for e in pending], ["COND_YC_INCOME_SAMPLE"])
        self.assertEqual(pending[0]["review_status"], PENDING)
        self.assertEqual(tree_condition_ids(edges), {e["target_id"] for e in mixed_sample()})

    def test_pending_condition_can_never_produce_false(self):
        groups, edges = graph(mixed_sample())
        ids = sorted(tree_condition_ids(edges))
        for values in itertools.product((True, False), repeat=len(ids)):
            truth = dict(zip(ids, values))
            with self.subTest(truth=truth):
                result = evaluate(groups, edges, truth)
                known_false = not truth["COND_AGE_19_34"] or not (truth["COND_EDU_SAMPLE_A"] or truth["COND_EDU_SAMPLE_B"])
                # 확정 조건 불충족이면 FALSE, 그 외에는 검토 조건 값과 무관하게 UNKNOWN.
                self.assertIs(result, False if known_false else None)

    def test_all_pending_policy_keeps_unknown_root_without_and_claim(self):
        structured = structure_ja_conditions({"JA0110": 19, "JA0111": 34, "JA0201": "Y", "JA0202": "Y", "JA0314": "Y"})
        policy = {"policy_id": "POL_SAMPLE", "source_document_ids": ["DOC_SAMPLE"], "verification_status": KNOWN,
                  "requirements": {"structured_support_conditions": structured}}
        es = build_condition_edges(policy)
        groups, edges = graph(es, policy["requirements"])
        self.assertEqual(next(g for g in groups if g["is_root"])["logic"], "UNKNOWN")
        self.assertNotIn("GRP_POL_SAMPLE_UNKNOWN_REVIEW", {g["group_id"] for g in groups})
        self.assertEqual(tree_condition_ids(edges), {e["target_id"] for e in es})

    def test_single_uncertain_ja_item_is_not_a_hard_and_member(self):
        es = [edge("COND_EDU_SAMPLE_A", KNOWN), edge("COND_JOB_어업인", PENDING)]
        requirements = {"structured_support_conditions": structure_ja_conditions({"JA0314": "Y"})}
        groups, edges = graph(es, requirements)
        self.assertEqual(next(g for g in groups if g["is_root"])["logic"], "AND")
        job = next(e for e in edges if e["target_id"] == "COND_JOB_어업인" and e["relation"] == "CONTAINS_CONDITION")
        self.assertEqual(job["source_id"], "GRP_POL_SAMPLE_UNKNOWN_REVIEW")
        self.assertIsNone(evaluate(groups, edges, {"COND_EDU_SAMPLE_A": True, "COND_JOB_어업인": False}))

    def test_exclusion_only_policy_has_no_and_without_evaluable_child(self):
        groups, edges = graph([], {"exclusion_criteria": ["sample_only 제외 문장"]})
        root = next(g for g in groups if g["is_root"])
        self.assertEqual(root["logic"], "UNKNOWN")
        self.assertTrue(any(g["group_id"].endswith("_UNKNOWN_EXCL") for g in groups))

    def test_no_and_group_is_empty_or_holds_pending_atoms_directly(self):
        policies = rebuild_policy_conditions(hydrate(ROOT / "data/releases/v1.4.0-candidate"))
        flat = rebuild_condition_relationships(policies, [])
        groups, edges, _ = build_condition_groups(policies, flat, build_condition_nodes(flat))
        review = {(e["source_id"], e["target_id"]): e["review_status"] for e in flat if e["relation"] == "REQUIRES_CONDITION"}
        owner = {g["group_id"]: g["policy_id"] for g in groups}
        for g in (g for g in groups if g["logic"] in ("AND", "OR")):
            members = [e for e in edges if e["source_id"] == g["group_id"]]
            with self.subTest(group=g["group_id"]):
                self.assertTrue(members)
                for e in members:
                    if e["relation"] == "CONTAINS_CONDITION":
                        self.assertNotEqual(review[(owner[g["group_id"]], e["target_id"])], PENDING)
        for p in policies:
            required = {cid for (pid, cid) in review if pid == p["policy_id"]}
            gids = {g["group_id"] for g in groups if g["policy_id"] == p["policy_id"]}
            in_tree = {e["target_id"] for e in edges if e["source_id"] in gids and e["relation"] == "CONTAINS_CONDITION"}
            self.assertEqual(in_tree, required, p["policy_id"])

    def test_real_yc002_known_or_groups_are_evaluable(self):
        policies = rebuild_policy_conditions(hydrate(ROOT / "data/releases/v1.4.0-candidate"))
        policy = next(p for p in policies if p["policy_id"] == "POL_YC_002")
        es = rebuild_condition_relationships([policy], [])
        groups, edges, _ = build_condition_groups([policy], es, build_condition_nodes(es))
        logic = {g["group_id"]: g["logic"] for g in groups}
        self.assertEqual(logic["GRP_POL_YC_002_ROOT"], "AND")
        self.assertEqual(logic["GRP_POL_YC_002_OR_EDU"], "OR")
        self.assertEqual(logic["GRP_POL_YC_002_OR_SP"], "OR")
        pending = {e["target_id"] for e in edges if e["source_id"] == "GRP_POL_YC_002_UNKNOWN_REVIEW"}
        # 소득 0043001은 공식 코드표상 '무관'이라 조건 관계가 없다. 연령(N, 원문 범위 없음)만 보류.
        self.assertEqual({c.split("_")[1] for c in pending}, {"AGE"})
        self.assertFalse(any(e["target_id"].startswith("COND_YC_INCOME_") for e in es))
        self.assertIn("0043001", policy["requirements"]["income_condition_basis"])
        # 학력 조건이 하나도 없으면 원 OR 구조대로 FALSE. 연령·소득은 판정에 쓰지 않는다.
        truth = {e["target_id"]: e["target_id"].startswith(("COND_SP_", "COND_AGE_", "COND_YC_")) for e in es}
        self.assertIs(evaluate(groups, edges, truth, "POL_YC_002"), False)
        truth.update({cid: True for cid in truth if cid.startswith("COND_EDU_")})
        self.assertIsNone(evaluate(groups, edges, truth, "POL_YC_002"))


class RealCandidatePartialEvaluationTests(unittest.TestCase):
    """v1.4.0 원본 후보(Git 추적)에서 재구성한 실제 정책 전체를 3값 논리로 검사한다."""

    @classmethod
    def setUpClass(cls):
        cls.policies = rebuild_policy_conditions(hydrate(ROOT / "data/releases/v1.4.0-candidate"))
        flat = rebuild_condition_relationships(cls.policies, [])
        cls.groups, cls.edges, _ = build_condition_groups(cls.policies, flat, build_condition_nodes(flat))
        cls.review = {(e["source_id"], e["target_id"]): e["review_status"] for e in flat
                      if e["relation"] == "REQUIRES_CONDITION"}

    def atoms(self, pid):
        return {cid: status for (p, cid), status in self.review.items() if p == pid}

    def test_partial_evaluation_policy_set(self):
        roots = {g["policy_id"]: g["logic"] for g in self.groups if g["is_root"]}
        and_roots = sorted(pid for pid, logic in roots.items() if logic == "AND")
        self.assertEqual(len(roots), 246)
        # 온통청년 25 + 정부24 연령 원문 일치 3 + K-패스 공식 검토 1 + 서울 청년수당 1
        self.assertEqual(len(and_roots), 30)
        unknown_children = {g["policy_id"] for g in self.groups if not g["is_root"] and g["logic"] == "UNKNOWN"}
        # 어떤 AND 루트도 UNKNOWN 하위 그룹 없이 TRUE로 판정될 수 없다.
        self.assertEqual([pid for pid in and_roots if pid not in unknown_children], [])

    def test_pending_atoms_never_decide_and_known_false_decides(self):
        for pid in sorted(g["policy_id"] for g in self.groups if g["is_root"] and g["logic"] == "AND"):
            atoms = self.atoms(pid)
            known = [c for c, s in atoms.items() if s != PENDING]
            pending = [c for c, s in atoms.items() if s == PENDING]
            with self.subTest(pid=pid):
                groups = [g for g in self.groups if g["policy_id"] == pid]
                self.assertTrue(known)
                self.assertTrue(any(g["logic"] == "UNKNOWN" and not g["is_root"] for g in groups))
                for value in (True, False):
                    # 확정 조건이 모두 충족돼도 검토 조건 값과 무관하게 UNKNOWN (TRUE로 확정되지 않음)
                    truth = {c: True for c in known} | {c: value for c in pending}
                    self.assertIsNone(evaluate(groups, self.edges, truth, pid))
                    # 확정 조건이 모두 불충족이면 검토 조건 값과 무관하게 FALSE
                    truth = {c: False for c in known} | {c: value for c in pending}
                    self.assertIs(evaluate(groups, self.edges, truth, pid), False)

    def test_unknown_policies_never_evaluate_to_true_or_false(self):
        for pid in sorted(g["policy_id"] for g in self.groups if g["is_root"] and g["logic"] == "UNKNOWN"):
            groups = [g for g in self.groups if g["policy_id"] == pid]
            atoms = self.atoms(pid)
            with self.subTest(pid=pid):
                self.assertTrue(all(s == PENDING for s in atoms.values()))
                for value in (True, False):
                    self.assertIsNone(evaluate(groups, self.edges, {c: value for c in atoms}, pid))


class SourceCodePreservationTests(unittest.TestCase):
    def test_ja0322_special_not_applicable_is_preserved(self):
        self.assertEqual(structure_ja_conditions({"JA0322": "Y"})["생애주기특수조건"]["해당사항없음"], "적용대상")

    def test_cleaned_raw_ja0322_is_recovered_without_mutation(self):
        requirements = {"support_conditions": {"JA0322": "Y"},
                        "structured_support_conditions": {"생애주기특수조건": {"장애인": "적용대상"}}}
        snapshot = copy.deepcopy(requirements)
        structured = get_structured_conditions(requirements)
        self.assertEqual(structured["생애주기특수조건"], {"장애인": "적용대상", "해당사항없음": "적용대상"})
        self.assertEqual(requirements, snapshot)


if __name__ == "__main__":
    unittest.main()


class EvidenceRuleTests(unittest.TestCase):
    """공식 근거로만 조건을 확정하는 규칙 (sample_only 가상 정책 포함)."""

    def test_age_text_evidence_requires_single_identical_range(self):
        from scripts.build_clean_dataset import age_text_evidence
        self.assertIn("19~34세", age_text_evidence(19, 34, [("원문", "만 19~34세 청년")]))
        self.assertIsNone(age_text_evidence(19, 34, [("원문", "만 19~34세, 단 만 12세 ~ 24세 우선")]))
        self.assertIsNone(age_text_evidence(13, 18, [("원문", "만 12세 ~ 만 24세")]))
        self.assertIsNone(age_text_evidence(19, 34, [("원문", "청년 누구나")]))
        self.assertIsNone(age_text_evidence(None, 34, [("원문", "만 19~34세")]))

    def test_generated_youth_eligibility_is_not_age_evidence(self):
        p = {"policy_id": "POL_SAMPLE", "verification_status": KNOWN, "eligibility": "연령: 만 19~34세 / 혼인: 제한없음",
             "requirements": {"age_limit_yn": "N", "min_age": 19, "max_age": 34, "source_free_text": {}}}
        self.assertNotIn("age_text_evidence", rebuild_policy_conditions([p])[0]["requirements"])
        p["requirements"]["source_free_text"] = {"plcySprtCn": "만 19~34세 청년에게 지원"}
        rebuilt = rebuild_policy_conditions([p])[0]
        self.assertIn("plcySprtCn", rebuilt["requirements"]["age_text_evidence"])
        age = next(e for e in build_condition_edges(rebuilt) if e["target_id"] == "COND_AGE_19_34")
        self.assertNotEqual(age["review_status"], PENDING)

    def test_official_income_unrestricted_code_only_without_other_text(self):
        base = {"policy_id": "POL_SAMPLE", "requirements": {"income_condition_code": "0043001",
                "income_condition_label": "무관", "income_min_amount": "0", "income_max_amount": "0",
                "income_etc_details": ""}}
        rebuilt = rebuild_policy_conditions([base])[0]
        self.assertFalse(rebuilt["requirements"]["income_review_needed"])
        self.assertFalse(any(e["target_id"].startswith("COND_YC_INCOME_") for e in build_condition_edges(rebuilt)))
        base["requirements"]["income_etc_details"] = "sample_only 중위소득 100% 이하"
        rebuilt = rebuild_policy_conditions([base])[0]
        self.assertTrue(rebuilt["requirements"]["income_review_needed"])
        self.assertTrue(any(e["target_id"].startswith("COND_YC_INCOME_") for e in build_condition_edges(rebuilt)))

    def test_unstructured_text_blocks_true_but_not_false(self):
        es = [edge("COND_AGE_19_34", KNOWN)]
        groups, edges = graph(es, {"additional_qualification": "sample_only 추가 자격"})
        self.assertEqual(next(g for g in groups if g["is_root"])["logic"], "AND")
        self.assertIsNone(evaluate(groups, edges, {"COND_AGE_19_34": True}))
        self.assertIs(evaluate(groups, edges, {"COND_AGE_19_34": False}), False)

    def test_age_review_without_known_age_blocks_true(self):
        es = [edge("COND_JOB_SAMPLE", KNOWN)]
        groups, edges = graph(es, {"age_limit_yn": "Y", "age_review_needed": True})
        self.assertTrue(any(g["group_id"].endswith("_UNKNOWN_UNSTRUCTURED") for g in groups))
        self.assertIsNone(evaluate(groups, edges, {"COND_JOB_SAMPLE": True}))

    def test_official_age_review_file_decisions(self):
        policies = {p["policy_id"]: p for p in rebuild_policy_conditions(hydrate(ROOT / "data/releases/v1.4.0-candidate"))}
        self.assertIn("공식 원문 검토", policies["POL_STG_MOLIT_001"]["requirements"]["age_text_evidence"])
        self.assertNotIn("age_text_evidence", policies["POL_GOV_011"]["requirements"])


class RegionContractTests(unittest.TestCase):
    def test_region_detail_keeps_province_relation_and_never_null(self):
        policies = rebuild_policy_conditions(hydrate(ROOT / "data/releases/v1.4.0-candidate"))
        detailed = [p for p in policies if "region_detail" in p]
        self.assertEqual(len(detailed), 22)
        for p in detailed:
            with self.subTest(pid=p["policy_id"]):
                self.assertTrue(p["region_detail"].startswith(p["region"] + " "))
                self.assertEqual(p["regions"], [p["region"]])
        self.assertTrue(all(p.get("region_detail", "x") is not None for p in policies))
        edges = rebuild_condition_relationships(policies, [])
        self.assertFalse(any(e["relation"] == "AVAILABLE_IN" for e in edges))


class ReferenceEvaluatorTests(unittest.TestCase):
    def test_atom_rules(self):
        from scripts.eligibility_3vl import evaluate_atom, TRUE, FALSE, UNKNOWN
        age = {"type": "AGE", "operator": "BETWEEN", "value": "19~34"}
        self.assertEqual(evaluate_atom(age, {"age": 29}), TRUE)
        self.assertEqual(evaluate_atom(age, {"age": 40}), FALSE)
        self.assertEqual(evaluate_atom(age, {}), UNKNOWN)
        job = {"type": "EMPLOYMENT", "operator": "IN", "value": "미취업자"}
        self.assertEqual(evaluate_atom(job, {"employment": ["미취업자"]}), TRUE)
        self.assertEqual(evaluate_atom(job, {"employment": ["재직자"]}), FALSE)
        self.assertEqual(evaluate_atom({"type": "OTHER", "operator": "UNKNOWN", "value": "{}"}, {"age": 1}), UNKNOWN)
