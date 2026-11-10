"""Issue #41 회귀: 실제 모델/DB 호출 없이 sample_only 벡터와 로컬 원문 사용."""

import json
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import MagicMock, patch

from database import retrieval_v140_eval as ev


class EmbeddingABTests(unittest.TestCase):
    def test_real_release_b2_inputs_and_ids_are_identical(self):
        with patch.object(ev.hd, "dataset_version", return_value="1.4.0-candidate"):
            docs = ev.hd.load_documents(verify_hash=True)
            metadata = ev.hd.load_payload_metadata()
            chunks = [ev.fi.build_config_chunks(name, docs)[0] for name in ev.AB_CONFIGS]
            self.assertEqual(len(chunks[0]), 1138)
            self.assertEqual(chunks[0], chunks[1])
            self.assertEqual(len({c["point_id"] for c in chunks[1]}), 1138)
            inputs = [[ev.em.build_embedding_text(c, ev.store.metadata_for_chunk(metadata, c),
                       ev.fi.EMBEDDING_INPUT_RULE) for c in batch] for batch in chunks]
            self.assertEqual(inputs[0], inputs[1])
            config = ev.fi.get_config("A_B2_4B_V140")
            self.assertEqual(ev.fi.config_model(config), ("qwen3-embedding:4b", 2560))
            self.assertEqual(config["collection"], "exp_b2_qwen3emb4b_v140_v1")
            self.assertEqual(config["expected_chunks"], 1138)

    def test_real_frozen_questions_transfer_and_denominator(self):
        cases, audit = ev.load_cases(ev.hd.load_documents(verify_hash=True), v0_only=True)
        self.assertEqual(len(cases), 55)
        self.assertEqual(sum(c["v0"]["answerable"] for c in cases), 52)
        self.assertEqual(audit["confirmed_human_reviewed_questions"], 0)
        self.assertTrue(all(c["exact_match"] for c in audit["v0_span_checks"]))

    def test_frozen_hash_and_counts_are_required(self):
        with patch.object(ev, "lf_hash", return_value="sample_only_mismatch"):
            with self.assertRaisesRegex(ValueError, "해시 불일치"):
                ev.load_cases([], v0_only=True)
        with patch.object(ev.rv, "load_jsonl", return_value=[]):
            with self.assertRaisesRegex(ValueError, "질문 수 불일치"):
                ev.load_cases([], v0_only=True)

    def test_collection_conflict_stops_preflight(self):
        plan = {"ready": False, "collection_state": {"state": "conflict", "reasons": ["sample_only"]}}
        with patch.object(ev.fi, "plan_full_index", return_value=plan):
            with self.assertRaisesRegex(ValueError, "컬렉션 불일치"):
                ev.validate_ab_collections(MagicMock(), [], {})

    def test_incomplete_collection_is_rejected(self):
        for state in ("absent", "partial"):
            with self.subTest(state=state), patch.object(ev.fi, "plan_full_index", return_value={
                    "ready": True, "collection_state": {"state": state}}):
                with self.assertRaisesRegex(ValueError, "컬렉션 불일치"):
                    ev.validate_ab_collections(MagicMock(), [], {})

    def test_actual_collection_dimension_and_payload_model_guards(self):
        from qdrant_client import QdrantClient, models
        chunk = {"point_id": ev.fi.chunking.make_point_id("sample_only"), "chunk_id": "sample_only",
                 "document_id": "sample_only_doc", "policy_id": "sample_only_policy", "char_start": 0,
                 "char_end": 1, "page_start": None, "page_end": None, "chunking_version": "B2", "text": "x"}
        hashes = {"sample_only_doc": "sample_only_hash"}
        for dimension, model in ((1024, "qwen3-embedding:4b"), (2560, "qwen3-embedding:0.6b")):
            with self.subTest(dimension=dimension, model=model), closing(QdrantClient(":memory:")) as client:
                name = "exp_sample_only_ab"
                client.create_collection(name, vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE))
                payload = ev.fi.expected_payload_identity(chunk, hashes["sample_only_doc"], model, dimension)
                client.upsert(name, [models.PointStruct(id=chunk["point_id"], vector=[1.0] * dimension,
                                                       payload={**payload, "text": "x"})])
                state = ev.fi.inspect_collection(client, name, [chunk], hashes, "qwen3-embedding:4b", 2560)
                self.assertEqual(state["state"], "conflict")
                self.assertTrue(state["reasons"])

    def test_changed_b2_input_is_rejected(self):
        with patch.object(ev.fi, "plan_full_index", return_value={"ready": True,
                "collection_state": {"state": "complete"}}), \
             patch.object(ev.fi, "build_config_chunks", side_effect=[([], {}), ([{"text": "sample_only"}], {})]), \
             patch.object(ev.store, "metadata_for_chunk", return_value={}), \
             patch.object(ev.em, "build_embedding_text", return_value="sample_only"):
            with self.assertRaisesRegex(ValueError, "B2 청크"):
                ev.validate_ab_collections(MagicMock(), [], {})

    def test_legacy_4b_config_rejects_release_dataset(self):
        with patch.object(ev.hd, "dataset_version", return_value="1.4.0-candidate"):
            with self.assertRaisesRegex(ev.fi.FullIndexError, "1.3.2 전용"):
                ev.fi.get_config("A_B2_4B")

    def run_mock(self, *, wrong_dim=False, wrong_model=False, dataset="1.4.0-candidate"):
        # 가상 결과는 제어 흐름·분모 검사만 하며 실제 성능으로 사용하지 않는다.
        cases = [{"id": f"sample_only_{i}", "query": f"sample question {i}",
                  "v0": {"question_id": str(i), "answerable": i < 52,
                         "gold": {"required": [{"evidence_id": "sample_only", "alternatives": [
                             {"document_id": "sample_only", "char_start": 0, "char_end": 1}]}]}}}
                 for i in range(55)]
        plans = {name: {"model": model, "dim": dim, "collection": name} for name, model, dim in
                 ((ev.AB_CONFIGS[0], "qwen3-embedding:0.6b", 1024),
                  (ev.AB_CONFIGS[1], "qwen3-embedding:4b", 2560))}
        calls = []

        def factory(*, model, expected_dim, batch_size, timeout):
            def embed(texts):
                calls.append((model, texts))
                return [[0.0] * (2560 if wrong_dim else expected_dim)]
            embed.embedding_model = "sample_only_wrong" if wrong_model else model
            embed.embedding_dim = expected_dim
            return embed

        client = MagicMock()
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            from database import ab_eval_state
            stack.enter_context(patch.object(ab_eval_state, "collect_context", return_value=({"sample_only": True}, {})))
            stack.enter_context(patch.object(ab_eval_state, "loaded_models", return_value=[]))
            for obj, name, value in ((ev.hd, "dataset_version", dataset), (ev.hd, "load_documents", []),
                                     (ev.hd, "verify_manifest_checksums", []), (ev.hd, "load_payload_metadata", {}),
                                     (ev, "load_cases", (cases, {})), (ev, "validate_ab_collections", plans),
                                     (ev.store, "get_client", client)):
                stack.enter_context(patch.object(obj, name, return_value=value))
            factory_mock = stack.enter_context(patch.object(ev.em, "make_ollama_embedder", side_effect=factory))
            search = stack.enter_context(patch.object(ev.store, "search_chunks", return_value=[]))
            stack.enter_context(patch.object(ev.hs, "_make_neo4j_driver", side_effect=AssertionError("Neo4j forbidden")))
            output = Path(folder) / "new"
            if wrong_dim or wrong_model or dataset != "1.4.0-candidate":
                with self.assertRaises((ValueError, ev.em.EmbeddingError)):
                    ev.run_model_ab(output)
                search.assert_not_called()
                self.assertEqual(output.exists(), dataset == "1.4.0-candidate")
                return
            report = ev.run_model_ab(output)
            self.assertEqual(json.loads((output / "summary.json").read_text(encoding="utf-8")), report)
            self.assertEqual(report["status"], "preliminary_unreviewed_gold")
            self.assertIsNone(report["query_template"])
            self.assertEqual(factory_mock.call_count, 2)
            self.assertEqual([c.kwargs["expected_dim"] for c in factory_mock.call_args_list], [1024, 2560])
            self.assertEqual(len(calls), 110)
            self.assertEqual([t for m, t in calls[:55]], [t for m, t in calls[55:]])
            self.assertTrue(all(not t[0].startswith("Instruct:") for _, t in calls))
            for call in search.call_args_list:
                name, vector = call.args[1:]
                self.assertEqual(len(vector), plans[name]["dim"])
                self.assertEqual(call.kwargs, {"k": 5, "filters": None})
            for result in report["results"].values():
                self.assertEqual(result["scored_questions"], 52)
                self.assertEqual(result["preliminary_recall_at_5"], 0)
                self.assertEqual(result["preliminary_mrr_at_5"], 0)
                for key in ("embedding", "search", "combined"):
                    self.assertEqual(result[key]["samples"], 55)
                    self.assertIn("total_seconds", result[key])
            client.close.assert_called_once()

    def test_model_vectors_instructions_timing_and_denominator(self):
        self.run_mock()

    def test_4b_vector_cannot_reach_06b_collection(self):
        self.run_mock(wrong_dim=True)

    def test_wrong_embedder_model_is_rejected(self):
        self.run_mock(wrong_model=True)

    def test_wrong_dataset_is_rejected_before_search(self):
        self.run_mock(dataset="1.3.2")


if __name__ == "__main__":
    unittest.main()
