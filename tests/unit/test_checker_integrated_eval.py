"""#82 평가 CLI의 비용 중단과 실제 Pipeline 배선을 외부 호출 없이 검증한다."""
import contextlib
import csv
import importlib.util
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from urllib.parse import quote, quote_plus
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "checker_integrated_eval", ROOT / "tests/evaluation/llm_comparison/compare_checker_integrated.py"
)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def sample_row(cost=0.1, status="answered"):
    row = {key: "" for key in evaluation.CSV_FIELDS}
    row.update(question_id="SAMPLE_ONLY", model=evaluation.MODELS[0], status=status,
               elapsed_seconds=0, estimated_cost_usd=cost, num_policy_checks=1,
               num_needs_check_conditions=1, answer="가상 원문 [1]", citations=[],
               notice=None, ask_error="", sdk_calls=1, input_trace={})
    row.pop("redacted")
    return row


class EvaluationCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.enterContext(patch.object(evaluation, "OUT_ROOT", self.output))
        self.enterContext(patch.object(evaluation, "run_metadata", return_value={"sample_only": True}))
        self.enterContext(patch.dict(os.environ, {"OPENAI_API_KEY": "sample_only"}))
        self.stdout = self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def run_cli(self, args, **kwargs):
        with patch.object(evaluation.sys, "argv", ["evaluation", *args]), \
                patch.object(evaluation, "run_one", **kwargs) as run:
            evaluation.main()
        return run

    def saved(self):
        folder, = self.output.iterdir()
        rows = [json.loads(line) for line in (folder / "comparison.jsonl").read_text(encoding="utf-8").splitlines()]
        metadata = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        return folder, rows, metadata

    def test_invalid_budgets_fail_before_loading_inputs_or_creating_files(self):
        for value in ("nan", "inf", "-inf", "0", "-1", "invalid"):
            with self.subTest(value=value), patch.object(evaluation, "load_questions") as load, \
                    patch.object(evaluation, "run_one") as run, \
                    patch.object(evaluation.sys, "argv", ["evaluation", "--execute", "--budget", value]), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    evaluation.main()
                self.assertEqual(error.exception.code, 2)
                load.assert_not_called()
                run.assert_not_called()
                self.assertEqual(list(self.output.iterdir()), [])

    def test_dry_run_never_initializes_execution_metadata_or_calls_models(self):
        with patch.object(evaluation, "run_metadata") as metadata:
            run = self.run_cli([], return_value=sample_row())
        run.assert_not_called()
        metadata.assert_not_called()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_valid_budget_completes_and_files_round_trip(self):
        run = self.run_cli(["--execute", "--budget", "1"], side_effect=lambda *args, **kwargs: sample_row(0.01))
        folder, rows, metadata = self.saved()
        self.assertEqual(run.call_count, 15)
        self.assertEqual(len(rows), 15)
        self.assertEqual(metadata["status"], "completed")
        self.assertAlmostEqual(metadata["known_cost_usd"], 0.15)
        with (folder / "comparison.csv").open(encoding="utf-8-sig", newline="") as stream:
            csv_rows = list(csv.DictReader(stream))
        self.assertEqual(len(csv_rows), len(rows))
        self.assertEqual(json.loads(csv_rows[0]["input_trace"]), rows[0]["input_trace"])
        self.assertFalse(rows[0]["redacted"])

    def test_threshold_stops_before_next_call_and_records_possible_overshoot(self):
        for budget, costs, expected in (("0.2", [0.1, 0.1], 2), ("0.3", [0.31], 1)):
            with self.subTest(budget=budget):
                run = self.run_cli(["--execute", "--budget", budget],
                                   side_effect=[sample_row(cost) for cost in costs])
                folders = sorted(self.output.iterdir())
                metadata = json.loads((folders[-1] / "run.json").read_text(encoding="utf-8"))
                self.assertEqual(run.call_count, expected)
                self.assertEqual(metadata["status"], "budget_reached")
                self.assertAlmostEqual(metadata["known_cost_usd"], sum(costs))

    def test_unknown_cost_stops_without_printing_zero_cost(self):
        run = self.run_cli(["--execute"], return_value=sample_row(None))
        _, rows, metadata = self.saved()
        self.assertEqual(run.call_count, 1)
        self.assertIsNone(rows[0]["estimated_cost_usd"])
        self.assertEqual(metadata["status"], "cost_unknown")
        self.assertIn("cost=확인 불가", self.stdout.getvalue())
        self.assertNotIn("cost=$0.000000", self.stdout.getvalue())

    def test_failed_request_with_known_cost_is_recorded_and_evaluation_continues(self):
        run = self.run_cli(["--execute"], return_value=sample_row(0.01, "error"))
        _, rows, metadata = self.saved()
        self.assertEqual(run.call_count, 15)
        self.assertEqual(len(rows), 15)
        self.assertEqual(metadata["status"], "completed_with_errors")

    def test_interrupt_keeps_previous_request_and_incomplete_status(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_cli(["--execute"], side_effect=[sample_row(0.01), KeyboardInterrupt()])
        _, rows, metadata = self.saved()
        self.assertEqual(len(rows), 1)
        self.assertEqual(metadata["status"], "interrupted")

    def test_secrets_are_masked_in_record_and_source_row_is_preserved(self):
        row = sample_row()
        secret = "sample-secret+/value"
        row["input_trace"] = {"contexts": [{"text": secret, "api_key": "private fixture",
                                           "encoded": quote(secret, safe=""),
                                           "query": quote_plus(secret)}]}
        with patch.dict(os.environ, {"OPENAI_API_KEY": secret}):
            self.run_cli(["--execute", "--budget", "0.05"], return_value=row)
        folder, rows, _ = self.saved()
        self.assertTrue(rows[0]["redacted"])
        self.assertNotIn(secret, (folder / "comparison.jsonl").read_text(encoding="utf-8"))
        for path in folder.iterdir():
            text = path.read_text(encoding="utf-8-sig")
            for value in (secret, quote(secret, safe=""), quote_plus(secret)):
                self.assertNotIn(value, text)
        self.assertEqual(rows[0]["input_trace"]["contexts"][0]["api_key"], "***")
        self.assertEqual(row["input_trace"]["contexts"][0]["text"], secret)

    def test_partial_file_publication_preserves_rows_and_marks_save_failure(self):
        original_replace = Path.replace

        def fail_csv_replace(path, target):
            if path.name == "comparison.csv.tmp":
                raise PermissionError("sample_only storage failure")
            return original_replace(path, target)

        with patch.object(Path, "replace", fail_csv_replace), self.assertRaises(PermissionError):
            self.run_cli(["--execute"], return_value=sample_row(0.01))
        folder, rows, metadata = self.saved()
        recovery = json.loads((folder / "recovery.json").read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(recovery["rows"]), 1)
        self.assertEqual(metadata["status"], "save_failed")
        self.assertFalse(metadata["results_consistent"])
        self.assertEqual(metadata["save_error"], "PermissionError")
        self.assertNotIn("저장 완료", self.stdout.getvalue())

    def test_contact_models_in_generation_trace_are_serializable(self):
        from backend.api.schemas import Contact
        row = sample_row()
        row["input_trace"] = {"generator": {"policy_checks": [{"contacts": [
            Contact(agency="가상 기관", phone="000-0000")
        ]}]}}
        self.run_cli(["--execute", "--budget", "0.05"], return_value=row)
        _, rows, _ = self.saved()
        contact = rows[0]["input_trace"]["generator"]["policy_checks"][0]["contacts"][0]
        self.assertEqual(contact["agency"], "가상 기관")
        self.assertEqual(contact["phone"], "000-0000")
        self.assertFalse(rows[0]["redacted"])

    def test_resume_skips_saved_request_and_includes_its_cost(self):
        row = sample_row(0.1)
        row["question_id"] = evaluation.QUESTION_IDS[0]
        self.run_cli(["--execute", "--budget", "0.05"], return_value=row)
        folder, _, _ = self.saved()
        run = self.run_cli(["--execute", "--resume-dir", str(folder), "--budget", "0.15"],
                           return_value=sample_row(0.1))
        _, rows, metadata = self.saved()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], evaluation.MODELS[1])
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(metadata["known_cost_usd"], 0.2)
        self.assertEqual(metadata["status"], "budget_reached")

    def test_resume_rejects_csv_that_no_longer_matches_jsonl(self):
        row = sample_row(0.1)
        row["question_id"] = evaluation.QUESTION_IDS[0]
        self.run_cli(["--execute", "--budget", "0.05"], return_value=row)
        folder, _, _ = self.saved()
        csv_path = folder / "comparison.csv"
        with csv_path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fields, records = reader.fieldnames, list(reader)
        records[0]["answer"] = "sample_only stale generation"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
        before = (folder / "run.json").read_bytes()
        with patch.object(evaluation, "run_one", return_value=sample_row(0.1)) as run, \
                patch.object(evaluation.sys, "argv", ["evaluation", "--execute", "--resume-dir", str(folder)]), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            evaluation.main()
        run.assert_not_called()
        self.assertEqual((folder / "run.json").read_bytes(), before)

    def test_resume_blocks_unrecorded_sdk_attempt(self):
        row = sample_row(0.1)
        row["question_id"] = evaluation.QUESTION_IDS[0]
        self.run_cli(["--execute", "--budget", "0.05"], return_value=row)
        folder, _, _ = self.saved()
        evaluation.atomic(folder / "request-01.json", {
            "question_id": evaluation.QUESTION_IDS[0], "model": evaluation.MODELS[1],
            "stage": "model_received", "sdk_calls": 1,
        })
        with patch.object(evaluation, "run_one") as run, self.assertRaises(SystemExit), \
                contextlib.redirect_stderr(io.StringIO()):
            with patch.object(evaluation.sys, "argv", ["evaluation", "--execute", "--resume-dir", str(folder)]):
                evaluation.main()
        run.assert_not_called()

    def test_resume_preserves_unknown_cost_stop(self):
        row = sample_row(None)
        row["question_id"] = evaluation.QUESTION_IDS[0]
        self.run_cli(["--execute"], return_value=row)
        folder, _, _ = self.saved()
        run = self.run_cli(["--execute", "--resume-dir", str(folder)])
        run.assert_not_called()
        _, rows, metadata = self.saved()
        self.assertEqual(metadata["status"], "cost_unknown")
        self.assertFalse(metadata["cost_total_complete"])
        self.assertEqual(len(rows), 1)

    def test_interrupt_after_sdk_attempt_marks_cost_incomplete_and_blocks_resume(self):
        def interrupt(model, question_id, question, *, checkpoint):
            checkpoint({"model": model, "question_id": question_id,
                        "stage": "before_sdk", "sdk_calls": 1, "usage": {}, "input_trace": {}})
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_cli(["--execute"], side_effect=interrupt)
        folder, rows, metadata = self.saved()
        self.assertEqual(rows, [])
        self.assertEqual(metadata["status"], "interrupted")
        self.assertFalse(metadata["cost_total_complete"])
        self.assertEqual(len(metadata["unrecorded_sdk_attempts"]), 1)
        with patch.object(evaluation, "run_one") as run, \
                patch.object(evaluation.sys, "argv", ["evaluation", "--execute", "--resume-dir", str(folder)]), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            evaluation.main()
        run.assert_not_called()

    def test_changed_execution_inputs_block_resume(self):
        row = sample_row(0.1)
        row["question_id"] = evaluation.QUESTION_IDS[0]
        self.run_cli(["--execute", "--budget", "0.05"], return_value=row)
        folder, _, metadata = self.saved()
        metadata["files_sha256"] = {"sample_only.py": "different"}
        evaluation.atomic(folder / "run.json", metadata)
        with patch.object(evaluation, "run_one") as run, \
                patch.object(evaluation.sys, "argv", ["evaluation", "--execute", "--resume-dir", str(folder)]), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            evaluation.main()
        run.assert_not_called()

    def test_checkpoint_secret_masking_also_covers_atomic_temporary_file(self):
        secret = "sample-secret+/value"
        real_replace = os.replace
        def reject(path, target):
            if Path(target).name == "sample-checkpoint.json":
                raise PermissionError("sample_only")
            real_replace(path, target)
        with patch.dict(os.environ, {"OPENAI_API_KEY": secret}), patch.object(os, "replace", reject):
            with self.assertRaises(PermissionError):
                evaluation.atomic(self.output / "sample-checkpoint.json", evaluation.storage_record({
                    "trace": {"raw": secret, "encoded": quote(secret, safe=""),
                              "query": quote_plus(secret), "contacts": []},
                }))
        text = (self.output / "sample-checkpoint.json.tmp").read_text(encoding="utf-8")
        self.assertTrue(json.loads(text)["redacted"])
        for value in (secret, quote(secret, safe=""), quote_plus(secret)):
            self.assertNotIn(value, text)

    def test_failed_recovery_keeps_save_failed_metadata(self):
        real_atomic = evaluation.atomic
        def fail_recovery(path, value):
            if path.name == "recovery.json":
                raise OSError("sample_only")
            real_atomic(path, value)
        with patch.object(evaluation, "save_results", side_effect=PermissionError("sample_only")), \
                patch.object(evaluation, "atomic", side_effect=fail_recovery), self.assertRaises(OSError):
            self.run_cli(["--execute"], return_value=sample_row(0.01))
        folder, = self.output.iterdir()
        metadata = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["status"], "save_failed")
        self.assertFalse(metadata["results_consistent"])
        self.assertFalse((folder / "comparison.jsonl").exists())
        self.assertNotIn("저장 완료", self.stdout.getvalue())


class EvaluationPipelineTests(unittest.TestCase):
    def test_actual_pipeline_checker_generator_record_input_usage_and_close_session(self):
        from openai.resources.chat.completions import Completions
        from database import hybrid_search

        policy = {
            "policy_id": "SAMPLE_ONLY", "policy_name": "가상 정책", "best_rank": 1,
            "graph": {"policy": {}, "eligibility": {}}, "graph_status": "FOUND", "data_quality": {},
            "evidence": [{"rank": 1, "chunk_id": "SAMPLE_C", "document_id": "SAMPLE_D",
                          "text": "가상 공고 원문", "source_url": "https://example.org/notice"}],
        }
        from unittest.mock import MagicMock
        session = MagicMock()
        session.__enter__.return_value = session
        session.search_policy_context.return_value = {"policies": [policy]}
        response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20,
                                  prompt_tokens_details=None, completion_tokens_details=None),
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps({
                "status": "answered", "answer": "가상 공고 원문 [1]", "used_source_ids": [1],
                "follow_up_questions": [],
            })))],
        )
        usage = response.usage
        for token_usage, finish, status in ((usage, "stop", "answered"),
                                            (None, "stop", "answered"), (usage, "length", "error")):
            with self.subTest(usage=token_usage is not None, finish=finish):
                checkpoint_dir = Path(self.enterContext(TemporaryDirectory()))
                session.reset_mock()
                response.usage = token_usage
                response.choices[0].finish_reason = finish
                with patch.dict(os.environ, {"OPENAI_API_KEY": "sample_only"}), \
                        patch.object(hybrid_search, "HybridSearchSession", return_value=session), \
                        patch.object(Completions, "create", return_value=response) as sdk:
                    row = evaluation.run_one("gpt-5.4-mini", "SAMPLE_ONLY", "가상 정책", checkpoint=lambda record: evaluation.atomic(
                        checkpoint_dir / (record["stage"] + ".json"), evaluation.storage_record(record)))
                received = json.loads((checkpoint_dir / "model_received.json").read_text(encoding="utf-8"))
                self.assertEqual(received["sdk_calls"], 1)
                self.assertEqual(received["input_trace"]["model_output"][0]["finish_reason"], finish)
                self.assertEqual(received["usage"].get("prompt_tokens"), 100 if token_usage is not None else None)
                self.assertEqual(row["status"], status)
                self.assertEqual(row["sdk_calls"], 1)
                sdk.assert_called_once()
                session.search_policy_context.assert_called_once()
                session.__exit__.assert_called_once()
                if token_usage is None:
                    self.assertIsNone(row["estimated_cost_usd"])
                else:
                    self.assertEqual((row["prompt_tokens"], row["completion_tokens"]), (100, 20))
                    self.assertGreater(row["estimated_cost_usd"], 0)
                trace = row["input_trace"]
                self.assertEqual(trace["model_output"][0]["finish_reason"], finish)
                self.assertEqual(trace["search"]["result"]["policies"][0], policy)
                self.assertEqual(trace["generator"]["contexts"][0]["chunk_id"], "SAMPLE_C")
                self.assertEqual(trace["generator"]["policy_checks"][0]["policy_id"], "SAMPLE_ONLY")
                self.assertTrue(trace["generator"]["policy_checks"][0]["conditions"])
                if status == "answered":
                    self.assertEqual(trace["response"]["answer"], row["answer"])
                    self.assertEqual(trace["response"]["citations"], row["citations"])
                    self.assertEqual(trace["response"]["policy_checks"][0]["policy_id"], "SAMPLE_ONLY")
                else:
                    self.assertNotIn("response", trace)
                    self.assertEqual(trace["error"]["code"], row["ask_error"])
                with TemporaryDirectory() as folder:
                    evaluation.save_results([row], Path(folder))
                    saved = json.loads((Path(folder) / "comparison.jsonl").read_text(encoding="utf-8"))
                self.assertEqual(saved["input_trace"], trace)
                self.assertEqual(saved["citations"], row["citations"])

        session.reset_mock()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sample_only"}), \
                patch.object(hybrid_search, "HybridSearchSession", return_value=session), \
                patch.object(Completions, "create", side_effect=RuntimeError("sample_only")) as sdk:
            row = evaluation.run_one("gpt-5.4-mini", "SAMPLE_ONLY", "가상 정책")
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["sdk_calls"], 1)
        self.assertIsNone(row["estimated_cost_usd"])
        sdk.assert_called_once()
        session.search_policy_context.assert_called_once()
        session.__exit__.assert_called_once()

        response.usage = usage
        response.choices[0].finish_reason = "stop"
        for failure_stage, expected_calls in (("generator_input", 0), ("pipeline_complete", 1)):
            with self.subTest(checkpoint_failure=failure_stage), TemporaryDirectory() as folder:
                checkpoint_dir = Path(folder)
                def checkpoint(record):
                    if record["stage"] == failure_stage:
                        raise OSError("sample_only")
                    evaluation.atomic(checkpoint_dir / (record["stage"] + ".json"), evaluation.storage_record(record))
                with patch.dict(os.environ, {"OPENAI_API_KEY": "sample_only"}), \
                        patch.object(hybrid_search, "HybridSearchSession", return_value=session), \
                        patch.object(Completions, "create", return_value=response) as sdk, \
                        self.assertRaises(evaluation.CheckpointError):
                    evaluation.run_one("gpt-5.4-mini", "SAMPLE_ONLY", "가상 정책", checkpoint=checkpoint)
                self.assertEqual(sdk.call_count, expected_calls)
                if expected_calls:
                    saved = json.loads((checkpoint_dir / "model_received.json").read_text(encoding="utf-8"))
                    self.assertEqual(saved["usage"]["prompt_tokens"], 100)
                    self.assertTrue(saved["input_trace"]["model_output"][0]["content"])

    def test_search_failure_closes_session_and_has_zero_llm_cost(self):
        from database import hybrid_search
        from unittest.mock import MagicMock
        session = MagicMock()
        session.__enter__.return_value = session
        session.search_policy_context.side_effect = RuntimeError("sample secret must not be recorded")
        with patch.object(hybrid_search, "HybridSearchSession", return_value=session):
            row = evaluation.run_one("gpt-5.4-mini", "SAMPLE_ONLY", "가상 정책")
        session.__exit__.assert_called_once()
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["sdk_calls"], 0)
        self.assertEqual(row["estimated_cost_usd"], 0)
        self.assertNotIn("sample secret", row["ask_error"])


if __name__ == "__main__":
    unittest.main()
