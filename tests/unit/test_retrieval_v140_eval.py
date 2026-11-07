"""실제 DB 없이 평가 분모·중복·출처 경계를 확인한다."""

import unittest
from unittest.mock import patch

from database import retrieval_v140_eval as evaluation


class RetrievalV140EvalTests(unittest.TestCase):
    def test_duplicate_policy_chunks_do_not_inflate_reference_coverage(self):
        hits = [{"policy_id": "POL_A"}, {"policy_id": "POL_A"}, {"policy_id": "POL_B"}]
        diagnostic = evaluation.reference_diagnostic(["POL_A", "POL_C"], hits, 2)
        self.assertEqual(diagnostic["declared_target_coverage"], .5)
        self.assertEqual(diagnostic["first_declared_target_rank"], 1)

    def test_missing_reference_is_not_scored_as_false(self):
        self.assertIsNone(evaluation.reference_diagnostic([], [{"policy_id": "POL_A"}], 10))

    def test_cutoff_consumes_duplicate_chunks(self):
        hits = [{"policy_id": "POL_B"}] * 5 + [{"policy_id": "POL_A"}]
        self.assertFalse(evaluation.reference_diagnostic(["POL_A"], hits, 5)["any_declared_target"])
        self.assertEqual(evaluation.reference_diagnostic(["POL_A"], hits, 10)["first_declared_target_rank"], 6)

    def test_flatten_hybrid_restores_original_chunk_rank(self):
        result = {"policies": [{"evidence": [{"rank": 1}, {"rank": 3}]},
                               {"evidence": [{"rank": 2}]}], "unlinked_hits": [{"rank": 4}]}
        self.assertEqual([h["rank"] for h in evaluation.flatten_hits(result)], [1, 2, 3, 4])

    def test_p95_uses_nearest_rank_and_empty_is_not_zero_latency(self):
        result = evaluation.latency_summary(list(range(1, 21)))
        self.assertEqual(result["p95_ms"], 19000)
        self.assertIsNone(evaluation.latency_summary([])["mean_ms"])

    def test_no_human_gold_means_formal_metrics_unavailable_even_with_hits(self):
        diagnostic = evaluation.reference_diagnostic(["POL_A"], [{"policy_id": "POL_A"}], 10)
        record = {"profile": "b2_06b_v140", "mode": "vector", "seconds": .1,
                  "hits": [{"policy_id": "POL_A", "document_id": "DOC_A"}],
                  "reference_5": diagnostic, "reference_10": diagnostic}
        result = evaluation.summarize([record])["b2_06b_v140:vector"]
        self.assertIsNone(result["formal_hit_at_10"])
        self.assertIsNone(result["formal_recall"])
        self.assertIsNone(result["formal_mrr"])
        self.assertEqual(result["exploratory_at_10"]["questions_with_declared_target"], 1)

    def test_failed_queries_are_explicit_and_not_scored_as_success(self):
        result = evaluation.summarize([{"profile": "b2_06b_v140", "mode": "vector",
                                        "error": {"type": "Timeout"}}])["b2_06b_v140:vector"]
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["questions"], 1)
        self.assertEqual(result["exploratory_at_10"]["declared_reference_questions"], 0)

    def test_changed_frozen_questions_stop_transfer_before_search(self):
        with patch.object(evaluation, "lf_hash", return_value="mismatch"):
            with self.assertRaisesRegex(ValueError, "해시 불일치"):
                evaluation.load_cases([])


if __name__ == "__main__":
    unittest.main()
