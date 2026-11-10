"""Synthetic vectors only; no Ollama, Docker, or external DB calls."""
import copy
from contextlib import redirect_stdout
import io
import json
import math
import struct
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import uuid

from database import runpod_transfer as tr
from scripts import runpod_embed as w


class Client:
    def __init__(self, records): self.records = copy.deepcopy(records)
    def get_collection(self, name): return NS(config=NS(params=NS(vectors=NS(size=4, distance="Cosine"))))
    def count(self, *args, **kwargs): return NS(count=len(self.records))
    def scroll(self, *args, **kwargs): return ([NS(id=k, **v) for k, v in self.records.items()], None)
    def retrieve(self, *args, ids, **kwargs): return [NS(id=k, **self.records[k]) for k in ids if k in self.records]


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.folder = self.root / "package"; self.output = self.root / "output"
        self.chunks = [{"point_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "sample_only_" + str(i))),
            "chunk_id": "CHK_SAMPLE_" + str(i), "document_id": "DOC_SAMPLE", "policy_id": "POL_SAMPLE",
            "text": "sample input " + str(i)} for i in range(299)]
        self.metadata, self.hashes = {}, {"DOC_SAMPLE": "a" * 64}
        self.records = {c["point_id"]: {"vector": [0.5] * 4,
            "payload": self.payload(c, run_info={"index_run_id": "sample_original"})} for c in self.chunks[:297]}
        for record in self.records.values(): record["payload"]["embedding_dim"] = 4
        self.client = Client(self.records)
        self.anchor = self.root / "anchor.json"
        self.anchor.write_text(json.dumps({"partial_records_sha256": tr.er.digest(self.records)}))
        patches = [patch.object(w, "COUNT", 2), patch.object(w, "DIM", 4), patch.object(tr.er, "DIM", 4),
            patch.object(tr.er, "POINTS", 299), patch.object(tr.er, "ANCHOR", self.anchor),
            patch.object(tr, "source_data", return_value=(self.chunks, self.metadata, self.hashes)),
            patch.object(tr.fi, "manifest_sha256", return_value="sample_manifest"),
            patch.object(tr.fi, "chunk_fingerprint", return_value="sample_chunks"),
            patch.object(tr.store, "metadata_for_chunk", return_value={}),
            patch.object(tr.store, "build_point_payload", side_effect=self.payload),
            patch.object(tr.em, "build_embedding_text", side_effect=lambda c, m, rule: c["text"]),
            patch.object(w, "api", side_effect=self.api),
            patch.object(w.subprocess, "check_output", return_value="sample_only GPU inventory"),
            patch.object(tr.store, "upsert_points", side_effect=self.write)]
        for p in patches: p.start(); self.addCleanup(p.stop)
        self.calls = []; self.writes = []
        self.export = tr.export_package(self.client, self.folder)

    @staticmethod
    def payload(c, *, run_info, **kwargs):
        return {**{k: c[k] for k in ("chunk_id", "document_id", "policy_id", "text")},
            "content_hash": "a" * 64, "embedding_model": w.MODEL, "embedding_dim": w.DIM,
            "embedding_input_rule": w.RULE, "index_run_id": run_info["index_run_id"]}

    def api(self, path, body=None):
        if path == "tags": return {"models": [{"name": w.MODEL, "digest": w.MODEL_DIGEST}]}
        if path == "version": return {"version": "sample_only"}
        self.assertEqual(path, "embed"); self.assertFalse(body["truncate"])
        self.calls.extend(body["input"])
        return {"embeddings": [[0.5] * 4]}

    def write(self, client, collection, points):
        self.assertEqual(collection, tr.er.COLLECTION)
        for p in points:
            self.assertNotIn(p["id"], client.records)
            self.writes.append(p["id"])
            client.records[p["id"]] = {"payload": p["payload"], "vector": p["vector"]}
        return len(points)

    def generate(self): return w.generate(self.folder, self.output, request=self.api)

    def test_export_exact_missing_original_inputs_ids_no_vectors(self):
        m, rows = w.load_package(self.folder)
        self.assertEqual([r["point_id"] for r in rows], [c["point_id"] for c in self.chunks[297:]])
        self.assertEqual([r["input_text"] for r in rows], [c["text"] for c in self.chunks[297:]])
        self.assertEqual(len(m["existing_ids"]), 297)
        self.assertTrue(all("vector" not in r for r in rows))
        with self.assertRaises(ValueError): tr.export_package(self.client, self.folder)

    def test_model_digest_mismatch_before_embedding(self):
        with self.assertRaisesRegex(ValueError, "model digest"):
            w.generate(self.folder, self.output, request=lambda path: {"models": [{"name": w.MODEL, "digest": "wrong"}]})
        self.assertFalse(self.calls)

    def test_interrupt_resume_and_duplicate_embedding_prevention(self):
        n = 0
        def interrupted(path, body=None):
            nonlocal n
            if path == "embed":
                n += 1
                if n == 2: raise KeyboardInterrupt
            return self.api(path, body)
        with self.assertRaises(KeyboardInterrupt): w.generate(self.folder, self.output, request=interrupted)
        self.generate(); self.generate()
        self.assertEqual(self.calls, [c["text"] for c in self.chunks[297:]])

    def test_corrupted_saved_result_refuses_recompute(self):
        self.generate(); p = next((self.output / "records").glob("*.json")); data = json.loads(p.read_text())
        data["vector"][0] = 0.9; p.write_text(json.dumps(data))
        with self.assertRaises(ValueError): self.generate()
        self.assertEqual(len(self.calls), 2)

    def test_input_checksum_corruption_rejected(self):
        with (self.folder / "inputs.jsonl").open("a") as f: f.write("\n")
        with self.assertRaises(ValueError): self.generate()
        self.assertFalse(self.calls)

    def test_wrong_rule_dimension_and_nonfinite_rejected(self):
        for v in ([1.0], [float("nan")] * 4, [0.0] * 4):
            with self.assertRaises(ValueError): w.validate_vector(v)
        manifest, rows = w.load_package(self.folder)
        bad = {**manifest, "input_rule": "wrong"}
        w.atomic(self.folder / "manifest.json", bad)
        self.resign_package()
        with self.assertRaises(ValueError): w.load_package(self.folder)

    def resign_package(self):
        (self.folder / "SHA256SUMS").write_text("".join(w.file_sha(p) + "  " + p.name + "\n"
            for p in sorted(self.folder.iterdir()) if p.name != "SHA256SUMS"))

    def test_default_import_is_read_only_and_full_import_is_idempotent(self):
        self.generate()
        self.assertEqual(tr.import_results(self.client, self.folder, self.output)["to_add"], 2)
        self.assertFalse(self.writes)
        report = tr.import_results(self.client, self.folder, self.output, execute=True)
        self.assertEqual(report["points"], 299)
        self.assertEqual(tr.import_results(self.client, self.folder, self.output, execute=True)["added"], 0)
        self.assertEqual(len(self.writes), 2)
        self.assertEqual({k: self.client.records[k] for k in self.records}, self.records)

    def test_import_lost_response_recovers_from_db(self):
        self.generate()
        def uncertain(*args): self.write(*args); raise TimeoutError
        with patch.object(tr.store, "upsert_points", side_effect=uncertain):
            with self.assertRaises(TimeoutError): tr.import_results(self.client, self.folder, self.output, execute=True)
        self.assertEqual(tr.import_results(self.client, self.folder, self.output, execute=True)["added"], 1)
        self.assertEqual(len(self.writes), 2)

    def test_existing_imported_point_is_never_overwritten(self):
        self.generate(); tr.import_results(self.client, self.folder, self.output, execute=True)
        self.client.records[self.writes[0]]["vector"][0] = 0.9
        with self.assertRaises(ValueError): tr.import_results(self.client, self.folder, self.output, execute=True)
        self.assertEqual(len(self.writes), 2)

    def test_original_point_change_refuses_import(self):
        self.generate(); self.client.records[next(iter(self.records))]["vector"][0] = 0.9
        with self.assertRaisesRegex(ValueError, "Original 297"): tr.import_results(self.client, self.folder, self.output, execute=True)
        self.assertFalse(self.writes)

    def test_package_must_match_local_original_checkpoint(self):
        self.generate()
        self.anchor.write_text(json.dumps({"partial_records_sha256": "sample_wrong_anchor"}))
        with self.assertRaisesRegex(ValueError, "local original checkpoint"):
            tr.import_results(self.client, self.folder, self.output, execute=True)
        self.assertFalse(self.writes)

    def test_final_db_cli_blocked_before_client_creation(self):
        with patch("sys.argv", ["transfer", "import", "--url", "http://127.0.0.1:46336", "--package", "sample"]), patch.object(tr.er, "verify_container") as verify:
            with self.assertRaises(ValueError): tr.main()
        verify.assert_not_called()

    def test_full_result_and_post_import_validation(self):
        self.generate()
        def bad_write(client, name, points):
            self.write(client, name, points); client.records[points[0]["id"]]["vector"][0] = 0.1
        with patch.object(tr.store, "upsert_points", side_effect=bad_write):
            with self.assertRaises(ValueError): tr.import_results(self.client, self.folder, self.output, execute=True)

    def test_results_file_corruption_rejected(self):
        self.generate(); (self.output / "results.json").write_text("{}")
        with self.assertRaises(ValueError): tr.import_results(self.client, self.folder, self.output)

    def test_all_841_synthetic_results_import_and_full_1138_validation(self):
        self.chunks.extend({"point_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "sample_only_" + str(i))),
            "chunk_id": "CHK_SAMPLE_" + str(i), "document_id": "DOC_SAMPLE", "policy_id": "POL_SAMPLE",
            "text": "sample input " + str(i)} for i in range(299, 1138))
        folder = self.root / "full_sample_only"
        output = self.root / "full_sample_only_vectors"
        with patch.object(w, "COUNT", 841), patch.object(tr.er, "POINTS", 1138), redirect_stdout(io.StringIO()):
            tr.export_package(self.client, folder)
            w.generate(folder, output, request=self.api)
            self.assertEqual(len(self.calls), 841)
            self.assertEqual(tr.import_results(self.client, folder, output)["to_add"], 841)
            report = tr.import_results(self.client, folder, output, execute=True)
            self.assertEqual((report["added"], report["points"]), (841, 1138))
            self.assertEqual(tr.import_results(self.client, folder, output, execute=True)["added"], 0)
            self.assertEqual({k: self.client.records[k] for k in self.records}, self.records)

    def test_observed_rest_decimal_representation_matches_float32_bytes(self):
        stored = [-0.0004797283, 0.5, 0.5, 0.5]
        expected = [-0.00047972830361686647, 0.5, 0.5, 0.5]
        self.assertNotEqual(stored, expected)
        self.assertEqual(tr.float32_bytes(stored), tr.float32_bytes(expected))

    def test_one_float32_ulp_difference_fails_exact_comparison(self):
        value = -0.00047972830361686647
        bits = struct.unpack("<I", struct.pack("<f", value))[0]
        changed = struct.unpack("<f", struct.pack("<I", bits + 1))[0]
        self.assertNotEqual(tr.float32_bytes([value, 0.5, 0.5, 0.5]),
                            tr.float32_bytes([changed, 0.5, 0.5, 0.5]))

    def test_bytes_reject_dimension_nan_inf(self):
        for vector in ([1.0], [float("nan"), 1.0, 1.0, 1.0], [float("inf"), 1.0, 1.0, 1.0],
                       [-float("inf"), 1.0, 1.0, 1.0]):
            with self.subTest(vector=vector):
                with self.assertRaises(ValueError): tr.float32_bytes(vector)

    def test_payload_values_and_nested_types_are_strict(self):
        expected = {"dimension": 4, "nested": [True, {"rank": 1}]}
        self.assertTrue(tr.same_payload(copy.deepcopy(expected), expected))
        for changed in ({"dimension": 4.0, "nested": [True, {"rank": 1}]},
                        {"dimension": 4, "nested": [1, {"rank": 1}]},
                        {"dimension": 4, "nested": [True, {"rank": 1.0}]},
                        {"dimension": 5, "nested": [True, {"rank": 1}]}):
            self.assertFalse(tr.same_payload(changed, expected))

    def prepare_observed_rest_records(self):
        value = -0.00047972830361686647
        def request(path, body=None):
            if path == "embed": return {"embeddings": [[value, math.sqrt(1 - value * value), 0.0, 0.0]]}
            return self.api(path, body)
        w.generate(self.folder, self.output, request=request)
        tr.import_results(self.client, self.folder, self.output, execute=True)  # Fake DB only.
        for key in self.writes:
            self.assertEqual(self.client.records[key]["vector"][0], value)
            self.client.records[key]["vector"][0] = -0.0004797283

    def test_read_only_existing_import_accepts_rest_decimals_without_writes(self):
        self.prepare_observed_rest_records()
        with patch.object(tr.store, "upsert_points") as upsert:
            result = tr.import_results(self.client, self.folder, self.output)
        self.assertEqual((result["existing"], result["to_add"]), (299, 0))
        upsert.assert_not_called()
        self.assertEqual({k: self.client.records[k] for k in self.records}, self.records)

    def test_read_only_import_rejects_one_ulp_change(self):
        self.prepare_observed_rest_records()
        vector = self.client.records[self.writes[0]]["vector"]
        bits = struct.unpack("<I", struct.pack("<f", vector[0]))[0]
        vector[0] = struct.unpack("<f", struct.pack("<I", bits + 1))[0]
        with self.assertRaisesRegex(ValueError, "never overwrite"):
            tr.import_results(self.client, self.folder, self.output)

    def test_read_only_import_rejects_equal_numeric_different_payload_type(self):
        self.prepare_observed_rest_records()
        self.client.records[self.writes[0]]["payload"]["embedding_dim"] = 4.0
        with self.assertRaisesRegex(ValueError, "never overwrite"):
            tr.import_results(self.client, self.folder, self.output)


if __name__ == "__main__": unittest.main()
