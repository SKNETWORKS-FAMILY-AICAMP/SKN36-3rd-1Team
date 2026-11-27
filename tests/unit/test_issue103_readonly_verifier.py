"""Verifier safety and provenance tests. No real credentials, DB or LLM calls."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import warnings
from unittest.mock import Mock, patch

PATH = Path(__file__).resolve().parents[1] / "manual/verify_issue103_r3d_readonly.py"
SPEC = importlib.util.spec_from_file_location("issue103_readonly_verifier", PATH)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class ReadonlyVerifierTests(unittest.TestCase):
    def setUp(self):
        self.logging_disabled = logging.root.manager.disable
        self.warning_filters = warnings.filters[:]

    def tearDown(self):
        # The CLI intentionally suppresses SDK logs; in-process tests must restore global state.
        logging.disable(self.logging_disabled)
        warnings.filters[:] = self.warning_filters

    def run_fake_api_cases(self, budget):
        """Exercise the real API/Generator recorder, replacing only search transport and SDK output."""
        from copy import deepcopy
        import sys

        sys.path.insert(0, str(PATH.parents[1] / "integration"))
        from test_issue103_guidance import release_policy

        policies = {pid: release_policy(pid) for pid in
                    {verifier.SEJONG, *verifier.LOANS, *(p for p, _, _ in verifier.FAQS)}}
        payloads = {hit["point_id"]: hit for item in policies.values() for hit in item["evidence"]}

        class Session:
            collection = "sample_only"

            def __init__(self, **kwargs):
                pass

            def search_policy_context(self, question, filters=None, k=10):
                if "와 세종시 위원회 청년참여 확대의" in question:
                    items = [deepcopy(policies[pid]) for pid in ("POL_STG_SEOUL_001", verifier.SEJONG)]
                    for rank, item in enumerate(items, 1):
                        item["best_rank"] = rank
                        item["evidence"] = item["evidence"][:1]
                        item["evidence"][0]["rank"] = rank
                    return {"policies": items}
                pid = (filters or {}).get("policy_id", verifier.KPASS)
                item = deepcopy(policies[pid])
                faq = next((doc for policy, doc, query in verifier.FAQS if policy == pid and question == query), None)
                if faq:
                    item["evidence"] = [hit for hit in item["evidence"] if hit["document_id"] == faq]
                item["evidence"] = item["evidence"][:1]
                return {"policies": [item]}

            def close(self):
                pass

        client = Mock()
        client.retrieve.side_effect = lambda collection, ids, **kw: [SimpleNamespace(id=pid, payload=payloads[pid]) for pid in ids]
        report = {"actual_llm_calls": 0}
        args = SimpleNamespace(llm=True, max_llm_calls=budget)
        sdk_result = json.dumps({"status": "answered", "answer": "sample_only [1]", "used_source_ids": [1], "follow_up_questions": []})
        with patch("database.hybrid_search.HybridSearchSession", Session), patch("backend.llm.generator._call_openai", return_value=sdk_result) as sdk:
            verifier.run_api_checks(report, client, Mock(), args)
            self.assertEqual(sdk.call_count, min(budget, 11))
        return report

    def test_full_eleven_case_recording_chain_with_fake_transports(self):
        report = self.run_fake_api_cases(11)
        self.assertEqual(report["actual_llm_calls"], 11)
        self.assertEqual(len(report["llm_cases"]), 11)
        for row in report["llm_cases"]:
            with self.subTest(kind=row["kind"], policy=row["selected_policy_id"]):
                self.assertEqual(row["status"], "PASS")
                self.assertIn("llm_raw_response", row)
                self.assertIn("generator_input", row)
                self.assertIn("pipeline_output", row)
                self.assertEqual(row["semantic_review"], "BLOCKED")

    def test_call_budget_never_exceeded_and_not_reported_as_product_failure(self):
        report = self.run_fake_api_cases(2)
        self.assertEqual(report["actual_llm_calls"], 2)
        self.assertEqual(sum(row["status"] == "BLOCKED" for row in report["llm_cases"]), 9)

    def test_qdrant_mutations_are_denied_before_reaching_sdk(self):
        sdk = Mock()
        client = verifier.ReadOnlyQdrant(sdk)
        for name in ("upsert", "delete", "create_collection", "delete_collection", "set_payload",
                     "create_payload_index", "update_collection_aliases", "recover_snapshot"):
            with self.subTest(name=name), self.assertRaises(PermissionError):
                getattr(client, name)
        self.assertEqual(sdk.mock_calls, [])

    def test_all_required_query_methods_are_forwarded(self):
        sdk = Mock()
        client = verifier.ReadOnlyQdrant(sdk)
        for name in verifier.ReadOnlyQdrant.allowed:
            self.assertIs(getattr(client, name), getattr(sdk, name))

    def test_redaction_handles_nested_keys_values_and_url_encoding(self):
        value = "sample_only /+ secret"
        encoded = verifier.quote(value, safe="")
        result = verifier.redact({value: [{"text": value + encoded}]}, [value])
        output = json.dumps(result)
        self.assertNotIn(value, output)
        self.assertNotIn(encoded, output)
        self.assertIn("REDACTED", output)

    def test_exception_details_do_not_escape(self):
        self.assertEqual(verifier.classify_error(ValueError("sample_only secret")),
                         {"error_type": "ValueError", "backend": None, "kind": None})

    def test_http_error_records_only_numeric_status_not_body_or_headers(self):
        error = ValueError("sample_only credential in response message")
        error.status_code = 401
        error.content = b"sample_only secret body"
        error.headers = {"Authorization": "sample_only secret header"}
        result = verifier.classify_error(error)
        self.assertEqual(result["http_status"], 401)
        self.assertEqual(result["kind"], "auth")
        self.assertNotIn("sample_only", json.dumps(result))

    def test_connections_only_cannot_incur_llm_calls(self):
        with TemporaryDirectory() as directory, patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            verifier.main(["--connections-only", "--llm", "--out", str(Path(directory) / "report.json")])

    def test_connections_only_authenticates_both_without_baseline_or_llm(self):
        fake = {name: "sample_only_connection_secret_" + str(i) for i, name in enumerate(verifier.REQUIRED)}
        git_output = lambda *a, **kw: "sample_only_sha" if kw.get("text") else b"sample_only_diff"
        client, driver = Mock(), Mock()
        with TemporaryDirectory() as directory, patch.dict(os.environ, fake), redirect_stdout(io.StringIO()), \
                patch.object(verifier.subprocess, "check_output", side_effect=git_output), \
                patch("qdrant_client.QdrantClient", return_value=client) as factory, \
                patch("neo4j.GraphDatabase.driver", return_value=driver), \
                patch.object(verifier, "database_checks", side_effect=AssertionError("baseline must not run")), \
                patch.object(verifier, "run_api_checks", side_effect=AssertionError("LLM must not run")):
            path = Path(directory) / "connection.json"
            code = verifier.main(["--connections-only", "--out", str(path)])
            text = path.read_text(encoding="utf-8")
            report = json.loads(text)
        self.assertEqual(code, 0)
        self.assertEqual(report["mode"], "CONNECTIONS_ONLY")
        self.assertEqual(report["automated_status"], "PASS")
        self.assertEqual(report["actual_llm_calls"], 0)
        self.assertEqual(len(report["checks"]), 2)
        self.assertFalse(factory.call_args.kwargs["trust_env"])
        for value in fake.values():
            self.assertNotIn(value, text)

    def test_missing_environment_preflight_writes_only_presence_and_no_calls(self):
        empty = {name: "" for name in (*verifier.REQUIRED, "OPENAI_API_KEY")}
        with TemporaryDirectory() as directory, patch.dict(os.environ, empty), redirect_stdout(io.StringIO()):
            path = Path(directory) / "report.json"
            code = verifier.main(["--preflight", "--llm", "--out", str(path)])
            report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(code, 2)
        self.assertEqual(report["mode"], "PREFLIGHT")
        self.assertEqual(report["actual_llm_calls"], 0)
        self.assertEqual(report["db_writes"], 0)
        self.assertEqual(report["preflight"]["missing_env_names"], [*verifier.REQUIRED, "OPENAI_API_KEY"])
        self.assertNotIn("neo4j", report)

    def test_existing_output_is_preserved(self):
        with TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            path = Path(directory) / "exists.json"
            path.write_text("sample_only old result", encoding="utf-8")
            with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                verifier.main(["--preflight", "--out", str(path)])
            self.assertEqual(path.read_text(encoding="utf-8"), "sample_only old result")

    def test_ready_preflight_returns_zero_without_sending_fake_credentials(self):
        fake = {name: "sample_only_secret_" + str(i) for i, name in enumerate((*verifier.REQUIRED, "OPENAI_API_KEY"))}
        git_output = lambda *a, **kw: "sample_only_sha" if kw.get("text") else b"sample_only_diff"
        with TemporaryDirectory() as directory, patch.dict(os.environ, fake), redirect_stdout(io.StringIO()), patch.object(verifier.subprocess, "check_output", side_effect=git_output):
            path = Path(directory) / "ready.json"
            code = verifier.main(["--preflight", "--llm", "--out", str(path)])
            text = path.read_text(encoding="utf-8")
            report = json.loads(text)
        self.assertEqual(code, 0)
        self.assertEqual(report["automated_status"], "PASS")
        self.assertEqual(report["actual_llm_calls"], 0)
        self.assertNotIn("qdrant", report)
        for value in fake.values():
            self.assertNotIn(value, text)

    def test_trace_uses_actual_B2_point_and_document_not_release_id(self):
        context = {"policy_id": "POL_SAMPLE", "document_id": "DOC_SAMPLE", "chunk_id": "CHK_B2_DOC_SAMPLE_C002",
                   "text": "sample_only evidence", "url": "https://example.org/faq", "source_id": 1}
        hit = {"point_id": "sample-point", "chunk_id": context["chunk_id"]}
        row = {"search_results": [{"policies": [{"policy_id": "POL_SAMPLE", "evidence": [hit]}]}],
               "generator_input": {"contexts": [context]},
               "fastapi_response": {"answer": "sample_only [1]", "citations": [{"document_id": "DOC_SAMPLE", "url": context["url"]}]}}
        payload = {**context, "text": " sample_only evidence \n", "source_url": context["url"]}
        sdk = Mock()
        sdk.retrieve.return_value = [SimpleNamespace(id="sample-point", payload=payload)]
        self.assertEqual(verifier.validate_trace(row, sdk, "sample_collection")["status"], "PASS")
        payload["policy_id"] = "POL_OTHER"
        result = verifier.validate_trace(row, sdk, "sample_collection")
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("adopted_db_payload_mismatch:policy_id", result["problems"])


if __name__ == "__main__":
    unittest.main()
