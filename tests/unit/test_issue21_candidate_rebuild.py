"""별도 후보의 보존 경계와 실데이터 기반 비교 지표 회귀 검사."""
import copy
import unittest
from pathlib import Path

from scripts.rebuild_eligibility_candidate import (
    ROOT, age_conflicts, document_records, generator_provenance, hydrate, lf_bytes, policy_metadata, metrics, output_path, rebuild_chunk_metadata, rows,
)
import subprocess


class CandidateRebuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = ROOT / "data/releases/v1.4.0-candidate"
        cls.policies = hydrate(cls.package)

    def test_existing_release_and_candidate_root_are_rejected(self):
        for path in (self.package, ROOT / "data/candidates", ROOT / "data/processed"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                output_path(path)

    def test_reviewed_urls_and_policy_ids_survive_hydration(self):
        original = rows(self.package / "neo4j/nodes_policy.jsonl")
        self.assertEqual(len(self.policies), 246)
        self.assertEqual([(p["policy_id"], p["official_apply_url"]) for p in original],
                         [(p["policy_id"], p["official_apply_url"]) for p in self.policies])

    def test_conflicting_source_id_does_not_gain_inferred_conditions(self):
        p = next(p for p in self.policies if p["policy_id"] == "POL_STG_SEOUL_001")
        # official_id는 바꾸지 않는다. 문서의 원천 항목과 신청기간·지원내용이 정확히 같을 때만 조건 근거로 쓴다.
        self.assertEqual(p["official_id"], "20260317005400211516")
        identity = p["requirements"]["source_identity"]
        self.assertEqual(identity["matched_source_id"], "20261018005400213994")
        self.assertEqual(identity["document_id"], "DOC_STG_SEOUL_001_YC")
        self.assertEqual(p["requirements"]["age_limit_yn"], "N")
        # 내용이 다른 정책이면 연결하지 않는다.
        node = next(n for n in rows(self.package / "neo4j/nodes_policy.jsonl") if n["policy_id"] == "POL_STG_SEOUL_001")
        self.assertEqual(node["application_period"], "20260527 ~ 20260529")

    def test_real_youth_income_code_is_preserved(self):
        p = next(p for p in self.policies if p["policy_id"] == "POL_YC_002")
        self.assertEqual(p["requirements"]["income_condition_code"], "0043001")
        self.assertEqual(p["requirements"]["age_limit_yn"], "N")

    def test_gov011_age_conflict_is_reported_without_selection(self):
        snapshot = copy.deepcopy(self.policies)
        conflicts = age_conflicts(self.policies)
        p = next(p for p in conflicts if p["policy_id"] == "POL_GOV_011")
        self.assertEqual(p["structured"], [13, 18])
        self.assertIn((12, 24), p["text_ranges"])
        self.assertEqual(self.policies, snapshot)

    def test_original_homeless_mandatory_and_income_or_are_measured(self):
        m = metrics(self.policies, rows(self.package / "neo4j/nodes_condition_group.jsonl"),
                    rows(self.package / "neo4j/relationships.jsonl"),
                    rows(self.package / "neo4j/nodes_eligibility_condition.jsonl"))
        self.assertEqual(m["income_five_band_OR_policies"], 45)
        self.assertGreater(m["homeless_mandatory_AND_policies"], 0)
        self.assertEqual(m["empty_NOT_groups"], 1)

    def test_chunk_law_metadata_is_not_replaced_by_policy_attributes(self):
        chunks = rows(self.package / "qdrant/payload_chunks.jsonl")
        original = next(c for c in chunks if c["eligibility_attributes"].get("law_article"))
        snapshot = copy.deepcopy(original)
        policy = {"region": original["region"], "eligibility_review_status": "needs_review",
                  "eligibility_attributes": {"income_level": ["sample_only"], "unrelated": "must_not_leak"}}
        updated = rebuild_chunk_metadata(original, policy)
        self.assertEqual(updated["eligibility_attributes"]["law_article"], original["eligibility_attributes"]["law_article"])
        self.assertNotIn("unrelated", updated["eligibility_attributes"])
        self.assertEqual(updated["text"], original["text"])
        self.assertEqual(original, snapshot)


    def test_provenance_uses_real_head_and_flags_uncommitted_generators(self):
        head, generators = generator_provenance()
        actual = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, actual)
        for name, meta in generators.items():
            with self.subTest(name=name):
                lf = lf_bytes((ROOT / name).read_bytes())
                committed = subprocess.run(["git", "show", f"HEAD:{name}"], cwd=ROOT, capture_output=True)
                self.assertEqual(meta["matches_head"], committed.returncode == 0 and lf_bytes(committed.stdout) == lf)


    def test_document_bodies_are_rebuilt_from_tracked_sources_with_matching_hash(self):
        import hashlib
        documents = rows(self.package / "neo4j/nodes_document.jsonl")
        records = document_records(documents)
        self.assertEqual([{k: v for k, v in r.items() if k != "text"} for r in records], documents)
        self.assertTrue(all(hashlib.sha256(r["text"].encode("utf-8")).hexdigest() == r["content_hash"] for r in records))

    def test_policy_metadata_is_one_consistent_row_per_policy(self):
        nodes = rows(self.package / "neo4j/nodes_policy.jsonl")
        lookup = {p["policy_id"]: p for p in self.policies}
        meta = policy_metadata([dict(n, eligibility_review_status="needs_review") for n in nodes], lookup)
        self.assertEqual([m["policy_id"] for m in meta], [n["policy_id"] for n in nodes])
        for field in ("policy_name", "category", "region", "life_stages", "target_groups",
                      "eligibility_attributes", "verification_status"):
            self.assertTrue(all(field in m for m in meta), field)
        self.assertFalse(any(m.get("region_detail", "x") is None for m in meta))


if __name__ == "__main__":
    unittest.main()
