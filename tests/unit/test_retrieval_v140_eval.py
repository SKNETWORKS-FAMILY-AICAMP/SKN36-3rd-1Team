"""실제 DB 없이 평가 분모·중복·출처 경계를 확인한다."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    def test_evaluation_uses_neo4j_environment_and_common_embedding_constants(self):
        cases = [{"id": "sample_only", "query": "sample question", "source": "sample_only", "targets": []}]
        session = MagicMock()
        session.search_policy_evidence.return_value = {"hits": []}
        session.search_policy_context.return_value = {"policies": [], "missing_policy_ids": [], "data_quality_summary": {}}
        env = {"NEO4J_URI": "bolt://sample-only.invalid:28765", "NEO4J_USER": "neo4j", "NEO4J_PASSWORD": "sample_only"}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, env, clear=True), \
             patch.object(evaluation.em, "load_env_file"), \
             patch.object(evaluation.hd, "dataset_version", return_value="1.4.0-candidate"), \
             patch.object(evaluation.hd, "load_documents", return_value=[]), \
             patch.object(evaluation, "load_cases", return_value=(cases, {})), \
             patch.object(evaluation.fi, "build_config_chunks", return_value=([], {})), \
             patch.object(evaluation, "region_cases", return_value=[]), \
             patch.object(evaluation, "lf_hash", return_value="sample_only_hash"), \
             patch.object(evaluation.subprocess, "check_output", return_value="sample_only_commit\n"), \
             patch.object(evaluation.em, "DEFAULT_EMBEDDING_MODEL", "sample_only_model"), \
             patch.object(evaluation.em, "DEFAULT_EMBEDDING_DIM", 7), \
             patch.object(evaluation.em, "make_ollama_embedder", return_value=lambda texts: [[0.] * 7 for _ in texts]) as make_embedder, \
             patch.object(evaluation.store, "get_client") as make_client, \
             patch.object(evaluation.hs, "HybridSearchSession", return_value=session) as make_session, \
             patch("neo4j.GraphDatabase.driver") as make_driver:
            output = Path(folder) / "new"
            evaluation.run(output)
            self.assertEqual(make_driver.call_args.args[0], env["NEO4J_URI"])
            make_driver.return_value.verify_connectivity.assert_called_once()
            make_embedder.assert_called_once_with(model="sample_only_model", expected_dim=7, batch_size=1)
            cached = make_session.call_args.kwargs["embedder"]
            self.assertEqual((cached.embedding_model, cached.embedding_dim), ("sample_only_model", 7))
            report = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual((report["model"], report["dimensions"]), ("sample_only_model", 7))
            make_driver.return_value.close.assert_called_once()
            make_client.return_value.close.assert_called_once()

    def test_evaluation_missing_neo4j_uri_fails_before_any_embedding(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True), \
             patch.object(evaluation.em, "load_env_file"), \
             patch.object(evaluation.hd, "dataset_version", return_value="1.4.0-candidate"), \
             patch.object(evaluation.hd, "load_documents", return_value=[]), \
             patch.object(evaluation, "load_cases", return_value=([], {})), \
             patch.object(evaluation, "region_cases", return_value=[]), \
             patch.object(evaluation.fi, "build_config_chunks", return_value=([], {})), \
             patch.object(evaluation.em, "make_ollama_embedder") as embed, \
             patch("neo4j.GraphDatabase.driver") as driver:
            with self.assertRaisesRegex(evaluation.hs.HybridSearchBackendError, "NEO4J_URI"):
                evaluation.run(Path(folder) / "new")
            driver.assert_not_called()
            embed.assert_not_called()

    def test_shared_neo4j_connection_keeps_explicit_environment_over_dotenv(self):
        with tempfile.TemporaryDirectory() as folder:
            env_path = Path(folder) / ".env"
            env_path.write_text("NEO4J_URI=bolt://dotenv.sample-only.invalid:17687\n"
                                "NEO4J_USER=neo4j\nNEO4J_PASSWORD=sample_only\n", encoding="utf-8")
            with patch.dict(os.environ, {"NEO4J_URI": "bolt://explicit.sample-only.invalid:29876"}, clear=True), \
                 patch.object(evaluation.em, "DEFAULT_ENV_PATH", env_path), \
                 patch("neo4j.GraphDatabase.driver") as make_driver:
                driver = evaluation.hs._make_neo4j_driver(5, 10)
                self.assertEqual(make_driver.call_args.args[0], "bolt://explicit.sample-only.invalid:29876")
                driver.close()


if __name__ == "__main__":
    unittest.main()
