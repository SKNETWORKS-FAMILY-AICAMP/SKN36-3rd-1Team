"""Mock-only evaluation interruption, identity and timing regression tests."""
import copy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from database import ab_eval_state as st, retrieval_v140_eval as ev

REAL_CONTEXT = st.collect_context


class EvaluationResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / "sample_only_run"
        self.identity = {"sample_only": True, "models": "sample_digest", "freeze": "sample_freeze",
                         "top_k": 5, "code": "sample_code", "environment": "sample_gpu"}
        self.cases = [{"id": str(i), "query": "sample " + str(i),
            "v0": {"question_id": str(i), "answerable": i < 52,
                   "gold": {"required": [{"evidence_id": "sample", "alternatives": [
                       {"document_id": "sample", "char_start": 0, "char_end": 1}]}]}}} for i in range(55)]
        self.plans = {n: {"model": n, "dim": 2, "collection": n} for n in ev.AB_CONFIGS}
        self.client, self.search = MagicMock(), MagicMock(return_value=[])
        self.calls, self.timeouts = [], []
        self.fail_at, self.error = None, TimeoutError
        stack = ExitStack(); self.addCleanup(stack.close)
        for obj, name, value in [(ev.hd, "dataset_version", "1.4.0-candidate"), (ev.hd, "load_documents", []),
            (ev.hd, "verify_manifest_checksums", []), (ev.hd, "load_payload_metadata", {}),
            (ev, "load_cases", (self.cases, {})), (ev, "validate_ab_collections", self.plans),
            (ev.store, "get_client", self.client), (st, "loaded_models", [])]:
            stack.enter_context(patch.object(obj, name, return_value=value))
        stack.enter_context(patch.object(st, "collect_context", side_effect=lambda *args:
            (copy.deepcopy(self.identity), {"loaded_models": []})))
        stack.enter_context(patch.object(ev.em, "make_ollama_embedder", side_effect=self.factory))
        stack.enter_context(patch.object(ev.store, "search_chunks", self.search))

    def factory(self, *, model, expected_dim, batch_size, timeout):
        self.timeouts.append(timeout)
        def embed(texts):
            self.calls.append((model, texts[0]))
            if self.fail_at == len(self.calls): raise self.error("sample_only interruption")
            return [[0.5, 0.5]]
        embed.embedding_model, embed.embedding_dim = model, expected_dim
        return embed

    def run_eval(self, resume=False):
        return ev.run_model_ab(self.output, timeout_06b=123, timeout_4b=456, resume=resume)

    def test_timeout_resume_reuses_successes_and_original_timings(self):
        self.fail_at = 3
        with self.assertRaises(TimeoutError): self.run_eval()
        saved = list((self.output / "questions").glob("*.json")); self.assertEqual(len(saved), 2)
        first_bytes = {p: p.read_bytes() for p in saved}
        failure = json.loads(next((self.output / "runs").glob("*.json")).read_text())
        self.assertEqual((failure["phase"], failure["error_type"]), ("embedding", "TimeoutError"))
        self.fail_at = None
        report = self.run_eval(True)
        self.assertEqual(len(self.calls), 111)  # 110 successes, one failed request.
        self.assertEqual(report["reproducibility"]["reused_successful_questions"], 2)
        self.assertTrue(all(p.read_bytes() == value for p, value in first_bytes.items()))
        self.assertEqual(self.timeouts, [123, 123, 456])
        for value in report["results"].values():
            self.assertEqual(value["scored_questions"], 52)
            self.assertEqual(value["embedding"]["samples"], 55)
            self.assertEqual(value["preliminary_recall_at_5"], 0)

    def test_keyboard_interrupt_keeps_successful_questions(self):
        self.fail_at, self.error = 2, KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt): self.run_eval()
        self.fail_at = None
        self.assertEqual(self.run_eval(True)["reproducibility"]["reused_successful_questions"], 1)

    def test_search_timeout_is_not_saved_as_success(self):
        self.search.side_effect = TimeoutError
        with self.assertRaises(TimeoutError): self.run_eval()
        self.assertEqual(list((self.output / "questions").glob("*.json")), [])
        self.search.side_effect = None
        self.assertEqual(self.run_eval(True)["reproducibility"]["reused_successful_questions"], 0)

    def test_model_freeze_topk_code_environment_mismatch_before_embedding(self):
        self.fail_at = 2
        with self.assertRaises(TimeoutError): self.run_eval()
        for key in ("models", "freeze", "top_k", "code", "environment"):
            with self.subTest(key=key):
                old = self.identity[key]; self.identity[key] = "changed"
                count = len(self.calls)
                with self.assertRaisesRegex(ValueError, "identity mismatch"): self.run_eval(True)
                self.assertEqual(len(self.calls), count)
                self.identity[key] = old

    def test_saved_record_corruption_fails_without_new_embedding(self):
        self.fail_at = 2
        with self.assertRaises(TimeoutError): self.run_eval()
        p = next((self.output / "questions").glob("*.json")); data = json.loads(p.read_text())
        data["row"]["embedding_seconds"] += 1
        p.write_text(json.dumps(data))
        count = len(self.calls)
        with self.assertRaisesRegex(ValueError, "checksum"): self.run_eval(True)
        self.assertEqual(len(self.calls), count)

    def test_existing_output_requires_resume_and_bad_timeout_rejected(self):
        self.fail_at = 1
        with self.assertRaises(TimeoutError): self.run_eval()
        with self.assertRaises(ValueError): self.run_eval()
        for value in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError): ev.run_model_ab(self.output, timeout_4b=value, resume=True)

    def test_ollama_base_url_is_used_by_real_embedder_wrapper(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {"embeddings": [[0.5, 0.5]]}
        post = MagicMock(return_value=response)
        with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://127.0.0.1:11435"}):
            # The original factory is patched above; batch API is the actual shared path it invokes.
            ev.em.ollama_embed_batch(["sample"], model="sample_model", expected_dim=2, timeout=456, http_post=post)
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:11435/api/embed")
        self.assertEqual(post.call_args.kwargs["timeout"], 456)
        self.assertEqual(post.call_args.kwargs["json"]["input"], ["sample"])
        self.assertNotIn("Instruct", str(post.call_args.kwargs["json"]))

    def context_case(self):
        from scripts.runpod_embed import MODEL_DIGEST
        context = {"models": {"qwen3-embedding:0.6b": {"digest": "ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d"},
                              "qwen3-embedding:4b": {"digest": MODEL_DIGEST}},
            "collections": {"sample": "sample_full_collection_digest"}, "manifest_sha256": "sample_manifest",
            "freeze_sha256": "sample_freeze", "python": "sample_python", "platform": "sample_os",
            "git": {"head": "sample_head"}, "available_ram_bytes": 1, "captured_at": "sample_time"}
        response = MagicMock(); response.json.return_value = {"version": "sample_version"}
        return context, response

    def test_context_uses_completed_collections_and_ignores_volatile_ram(self):
        context, response = self.context_case()
        with patch.object(st.er, "evaluation_context", return_value=context) as capture, \
             patch.object(st.er, "resume", side_effect=AssertionError("No 297 checkpoint dependency")), \
             patch.object(ev.store, "resolve_connection_settings", return_value=(st.er.URL, "sample_only")), \
             patch.object(st.em, "resolve_ollama_base_url", return_value="http://127.0.0.1:11435"), \
             patch("requests.get", return_value=response):
            first, _ = REAL_CONTEXT(self.client, self.plans, {"sample": 123}, {"gpu": "sample_gpu", "ssh_tunnel": True})
            context["available_ram_bytes"] = 100
            context["captured_at"] = "new_time"
            second, _ = REAL_CONTEXT(self.client, self.plans, {"sample": 123}, {"gpu": "sample_gpu", "ssh_tunnel": True})
        self.assertEqual(first, second)
        self.assertEqual(first["ollama_url"], "http://127.0.0.1:11435")
        self.assertEqual(first["ollama_version"], "sample_version")
        self.assertEqual(capture.call_count, 2)

    def test_context_rejects_wrong_model_digest(self):
        context, response = self.context_case()
        context["models"]["qwen3-embedding:4b"]["digest"] = "wrong"
        with patch.object(st.er, "evaluation_context", return_value=context), \
             patch.object(ev.store, "resolve_connection_settings", return_value=(st.er.URL, "sample_only")), \
             patch("requests.get", return_value=response):
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                REAL_CONTEXT(self.client, self.plans, {}, None)

    def test_context_version_timeout_refuses_before_embedding(self):
        with patch.object(st.em, "resolve_ollama_base_url", return_value="http://127.0.0.1:11435"), \
             patch("requests.get", side_effect=TimeoutError):
            context, _ = self.context_case()
            with patch.object(st.er, "evaluation_context", return_value=context), \
                 patch.object(ev.store, "resolve_connection_settings", return_value=(st.er.URL, "sample_only")):
                with self.assertRaises(TimeoutError): REAL_CONTEXT(self.client, self.plans, {}, None)
        self.assertFalse(self.calls)


if __name__ == "__main__": unittest.main()
