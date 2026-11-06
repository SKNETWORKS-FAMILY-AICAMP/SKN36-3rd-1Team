"""Issue #21 regression fixtures; all fabricated records are sample_only."""
import unittest
import copy
import json
from pathlib import Path
from unittest.mock import patch

from collectors.api.gov24_collector import structure_ja_conditions
from collectors.api.youthcenter_collector import normalize_youthcenter_item, map_zip_codes_to_regions
from scripts.build_clean_dataset import extract_lifecycles_and_attributes, infer_region_from_text
from scripts.build_clean_dataset import build_condition_edges, rebuild_condition_relationships, rebuild_policy_conditions
from scripts.build_handoff_package import build_condition_groups


def sample_policy(requirements):
    return {"policy_id": "POL_SAMPLE", "policy_name": "sample_only",
            "source_document_ids": ["DOC_SAMPLE"], "requirements": requirements,
            "verification_status": "extracted"}


def graph_for(policy):
    edges = build_condition_edges(policy)
    nodes = {e["target_id"]: {"name": e["target_name"]} for e in edges}
    return build_condition_groups([policy], edges, nodes)


class GenerationRegressionTests(unittest.TestCase):
    def test_household_not_applicable_is_preserved(self):
        result = structure_ja_conditions({"JA0410": "Y", "JA0412": "Y"})
        self.assertEqual(result["가구주거조건"]["해당사항없음"], "적용대상")

    def test_all_income_brackets_are_preserved(self):
        structured = structure_ja_conditions({f"JA020{i}": "Y" for i in range(1, 6)})
        _, attrs = extract_lifecycles_and_attributes(
            policy_name="sample_only", target_groups=[], eligibility_text="",
            requirements={"structured_support_conditions": structured},
        )
        self.assertEqual(set(attrs["income_level"]), {
            name for name, status in structured["소득구간조건"].items() if status == "적용대상"
        })

    def test_explicit_district_is_not_dropped(self):
        self.assertEqual(infer_region_from_text("서울특별시 강남구"), ["서울특별시 강남구"])

    def test_missing_region_is_unknown(self):
        self.assertEqual(map_zip_codes_to_regions(None), ["unknown"])

    def test_n_age_flag_does_not_mean_unrestricted(self):
        result = normalize_youthcenter_item({
            "plcyNo": "sample_only", "sprtTrgtAgeLmtYn": "N",
            "sprtTrgtMinAge": "19", "sprtTrgtMaxAge": "34",
        }, {})
        req = result["requirements"]
        self.assertIsNone(req["age_limit"])
        self.assertTrue(req["age_review_needed"])
        self.assertEqual((req["min_age"], req["max_age"]), (19, 34))

    def test_zero_age_raw_values_survive(self):
        result = normalize_youthcenter_item({"sprtTrgtAgeLmtYn": "N",
            "sprtTrgtMinAge": "0", "sprtTrgtMaxAge": "0"}, {})
        self.assertEqual(result["requirements"]["min_age_raw"], "0")
        self.assertIsNone(result["requirements"]["age_limit"])
        self.assertNotIn("제한없음", result["eligibility"].split(" / ")[0])

    def test_detailed_region_code_survives(self):
        result = normalize_youthcenter_item({"zipCd": "11680,99999"}, {})
        self.assertEqual(result["requirements"]["region_codes"], ["11680", "99999"])
        self.assertTrue(result["requirements"]["region_review_needed"])
        self.assertIn("unknown", result["regions"])

    def test_all_income_group_is_unknown(self):
        policy = sample_policy({"structured_support_conditions": structure_ja_conditions(
            {f"JA020{i}": "Y" for i in range(1, 6)})})
        groups, edges, _ = graph_for(policy)
        income = next(g for g in groups if g["group_id"].endswith("_INC"))
        self.assertEqual(income["logic"], "UNKNOWN")
        self.assertEqual(sum(e["source_id"] == income["group_id"] for e in edges), 5)

    def test_homeless_is_in_household_group(self):
        policy = sample_policy({"structured_support_conditions": structure_ja_conditions(
            {"JA0410": "Y", "JA0412": "Y", "JA0404": "Y"})})
        groups, edges, _ = graph_for(policy)
        edge = next(e for e in edges if e["relation"] == "CONTAINS_CONDITION"
                    and e["target_id"] == "COND_HH_무주택세대")
        self.assertTrue(edge["source_id"].endswith("_HH"))
        self.assertEqual(next(g for g in groups if g["is_root"])["logic"], "UNKNOWN")

    def test_unparsed_exclusion_is_unknown_and_preserved(self):
        for field in ("exclusion_criteria", "excluded_participants"):
            policy = sample_policy({field: "sample_only 제외 문장 전체"})
            groups, _, _ = graph_for(policy)
            self.assertNotIn("NOT", [g["logic"] for g in groups])
            self.assertTrue(any("sample_only 제외 문장 전체" in g["description"] for g in groups))

    def test_gov_employment_and_business_are_not_dropped(self):
        policy = sample_policy({"structured_support_conditions": structure_ja_conditions(
            {"JA0326": "Y", "JA0327": "Y", "JA1101": "Y", "JA0313": "Y"})})
        edges = build_condition_edges(policy)
        self.assertEqual(sum(e["target_id"].startswith("COND_JOB_") for e in edges), 4)
        self.assertTrue(all(e["evidence_document_id"] == "DOC_SAMPLE" for e in edges))

    def test_youth_age_and_income_are_not_dropped(self):
        policy = sample_policy({"age_limit_yn": "N", "min_age": 19, "max_age": 34,
            "income_condition_code": "0043002", "income_min_amount": "0",
            "income_max_amount": "123456", "income_etc_details": "sample_only"})
        edges = build_condition_edges(policy)
        self.assertIn("COND_AGE_19_34", [e["target_id"] for e in edges])
        income = next(e for e in edges if e["target_id"].startswith("COND_YC_INCOME_"))
        self.assertEqual(json.loads(income["raw_value"])["income_max_amount"], "123456")
        self.assertEqual(income["review_status"], "needs_review")
        self.assertEqual(graph_for(policy)[0][0]["logic"], "UNKNOWN")

    def test_missing_age_bound_is_not_invented(self):
        policy = sample_policy({"structured_support_conditions": structure_ja_conditions({"JA0110": 19})})
        self.assertEqual(build_condition_edges(policy)[0]["target_id"], "COND_AGE_19_UNKNOWN")

    def test_cleaned_raw_ja0410_is_recovered_without_mutation(self):
        policy = sample_policy({"support_conditions": {"JA0410": "Y"},
            "structured_support_conditions": {"가구주거조건": {"무주택세대": "적용대상"}}})
        snapshot = copy.deepcopy(policy)
        self.assertIn("COND_HH_해당사항없음", [e["target_id"] for e in build_condition_edges(policy)])
        self.assertEqual(policy, snapshot)

    def test_candidate_metadata_preserves_provenance(self):
        policy = sample_policy({"structured_support_conditions": structure_ja_conditions({"JA0201": "Y", "JA0202": "Y"})})
        policy.update(source_url="https://example.org/sample_only", content_hash="sample_only", retrieved_at="sample_only")
        snapshot = copy.deepcopy(policy)
        candidate = rebuild_policy_conditions([policy])[0]
        self.assertEqual(candidate["eligibility_review_status"], "needs_review")
        self.assertEqual(len(candidate["eligibility_attributes"]["income_level"]), 2)
        for key in ("policy_id", "source_document_ids", "source_url", "content_hash", "retrieved_at"):
            self.assertEqual(candidate[key], policy[key])
        self.assertEqual(policy, snapshot)

    def test_rebuild_preserves_other_edges_and_inputs(self):
        policy = sample_policy({"age_limit_yn": "N", "min_age": 19})
        edges = [{"source_id": "POL_OTHER", "relation": "REQUIRES_CONDITION", "target_id": "COND_OTHER"},
                 {"source_id": "POL_SAMPLE", "relation": "SUPPORTED_BY", "target_id": "DOC_SAMPLE"}]
        snapshot = copy.deepcopy((policy, edges))
        result = rebuild_condition_relationships([policy], edges)
        self.assertEqual(result[:2], edges)
        self.assertEqual((policy, edges), snapshot)
        self.assertEqual(rebuild_condition_relationships([policy], result), result)

    def test_import_does_not_write_files(self):
        import importlib
        import scripts.build_clean_dataset as clean
        import scripts.build_handoff_package as handoff
        with patch("builtins.open", side_effect=AssertionError("unexpected I/O")), \
             patch.object(Path, "mkdir", side_effect=AssertionError("unexpected mkdir")):
            importlib.reload(clean)
            importlib.reload(handoff)

    def test_existing_output_is_rejected_before_writes(self):
        from scripts import build_clean_dataset as clean, build_handoff_package as handoff
        with self.assertRaises(ValueError):
            clean.main(Path("data/processed"))
        with patch.object(handoff, "HANDOFF_DIR", Path("data/processed")):
            with self.assertRaises(FileExistsError):
                handoff.main()

    def test_current_processed_data_rebuild_integrity(self):
        root = Path(__file__).resolve().parents[2] / "data" / "processed"
        policies = [json.loads(line) for line in (root / "policies.jsonl").read_text(encoding="utf-8").splitlines()]
        edges = [json.loads(line) for line in (root / "relationships.jsonl").read_text(encoding="utf-8").splitlines()]
        snapshot = copy.deepcopy(policies)
        result = rebuild_condition_relationships(policies, edges)
        nodes = {e["target_id"]: {"name": e["target_name"]} for e in result if e["relation"] == "REQUIRES_CONDITION"}
        groups, graph, group_ids = build_condition_groups(policies, result, nodes)
        self.assertEqual(len([g for g in groups if g["is_root"]]), len(policies))
        self.assertEqual(len(group_ids), len(groups))
        self.assertNotIn("NOT", [g["logic"] for g in groups])
        for edge in graph:
            if edge["relation"] == "CONTAINS_GROUP":
                self.assertIn(edge["target_id"], group_ids)
            elif edge["relation"] == "CONTAINS_CONDITION":
                self.assertIn(edge["target_id"], nodes)
        gov11 = next(p for p in policies if p["policy_id"] == "POL_GOV_011")
        self.assertEqual(next(g for g in groups if g["group_id"] == "GRP_POL_GOV_011_ROOT")["logic"], "UNKNOWN")
        self.assertEqual(gov11, next(p for p in snapshot if p["policy_id"] == "POL_GOV_011"))
        self.assertEqual(policies, snapshot)


if __name__ == "__main__":
    unittest.main()
