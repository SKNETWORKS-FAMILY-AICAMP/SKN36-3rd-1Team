"""Sample-only records and fake embeddings; never contact Ollama or Docker."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import patch

from database import embedding_resume as er


class FakeClient:
    def __init__(self, records):
        self.records = copy.deepcopy(records)
        self.config = NS(size=er.DIM, distance="Cosine")

    def get_collection(self, name):
        return NS(config=NS(params=NS(vectors=self.config)))

    def count(self, *args, **kwargs):
        return NS(count=len(self.records))

    def scroll(self, *args, **kwargs):
        return ([NS(id=k, **v) for k, v in self.records.items()], None)

    def retrieve(self, *args, ids, **kwargs):
        return [NS(id=k, **self.records[k]) for k in ids if k in self.records]


class ResumeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "checkpoint.json"
        self.chunks = [{"point_id": "sample_a", "document_id": "sample_doc", "text": "sample a"},
                       {"point_id": "sample_b", "document_id": "sample_doc", "text": "sample b"}]
        self.records = {"sample_a": {"vector": [0.5] * er.DIM,
            "payload": self.payload(self.chunks[0], run_info={"index_run_id": "sample_run"})}}
        self.anchor = {"partial_4b": {"existing_points": 1}, "partial_records_sha256": er.digest(self.records)}
        self.client = FakeClient(self.records)
        self.calls = []
        patches = [patch.object(er, "POINTS", 2), patch.object(er.fi, "manifest_sha256", return_value="sample_manifest"),
                   patch.object(er.em, "build_embedding_text", side_effect=lambda c, m, rule: c["text"]),
                   patch.object(er.store, "metadata_for_chunk", return_value={}),
                   patch.object(er.store, "build_point_payload", side_effect=self.payload),
                   patch.object(er.store, "build_points", side_effect=self.points),
                   patch.object(er.store, "upsert_points", side_effect=self.write)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def payload(chunk, *, run_info, **kwargs):
        return {"text": chunk["text"], "content_hash": "a" * 64, "embedding_dim": er.DIM,
                "embedding_model": er.MODEL, "index_run_id": run_info["index_run_id"]}

    def points(self, chunks, vectors, **kwargs):
        return [(c["point_id"], {"payload": self.payload(c, **kwargs), "vector": v})
                for c, v in zip(chunks, vectors)]

    def write(self, client, name, points):
        self.assertEqual(name, er.COLLECTION)
        client.records.update(dict(points))
        return len(points)

    def embed(self, texts):
        self.calls.extend(texts)
        return [[0.25] * er.DIM for _ in texts]

    def run_resume(self, execute=True):
        return er.resume(self.client, self.chunks, {}, {"sample_doc": "a" * 64}, self.anchor,
                         self.path, execute=execute, embedder=self.embed, ram=lambda: 8 * 1024**3)

    def test_missing_only_and_repeat_no_embedding(self):
        self.run_resume()
        self.run_resume()
        self.assertEqual(self.calls, ["sample b"])
        self.assertEqual(self.client.records["sample_a"], self.records["sample_a"])

    def test_read_only_never_writes_checkpoint_or_embeds(self):
        report = self.run_resume(False)
        self.assertEqual(report["missing"], 1)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.calls, [])

    def test_failed_save_before_commit_retries_missing_only(self):
        with patch.object(er.store, "upsert_points", side_effect=TimeoutError):
            with self.assertRaises(TimeoutError): self.run_resume()
        self.assertEqual(len(self.client.records), 1)
        self.run_resume()
        self.assertEqual(self.calls, ["sample b", "sample b"])

    def test_committed_save_with_lost_response_does_not_reembed(self):
        def uncertain(*args):
            self.write(*args)
            raise TimeoutError
        with patch.object(er.store, "upsert_points", side_effect=uncertain):
            with self.assertRaises(TimeoutError): self.run_resume()
        self.run_resume()
        self.assertEqual(self.calls, ["sample b"])

    def test_interruption_after_save_reconciles_pending(self):
        def interrupted(*args):
            self.write(*args)
            raise KeyboardInterrupt
        with patch.object(er.store, "upsert_points", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt): self.run_resume()
        self.run_resume()
        self.assertEqual(self.calls, ["sample b"])

    def test_final_endpoint_and_baseline_collection_blocked(self):
        for url, name in [("http://127.0.0.1:46336", er.COLLECTION),
                          (er.URL, "exp_b2_qwen3emb06b_v140_v1"), (er.URL + "/", er.COLLECTION)]:
            with self.assertRaises(ValueError): er.require_target(url, name)
        er.require_target(er.URL)

    def test_initial_anchor_mismatch_rejected(self):
        self.anchor["partial_records_sha256"] = "wrong"
        with self.assertRaisesRegex(ValueError, "paused checkpoint"): self.run_resume()
        self.assertFalse(self.calls)

    def test_bad_dimension_payload_or_id_rejected_before_embedding(self):
        original = copy.deepcopy(self.client.records)
        for fault in ("dimension", "payload", "id", "nan"):
            with self.subTest(fault=fault):
                self.client.records = copy.deepcopy(original)
                if fault == "dimension": self.client.records["sample_a"]["vector"] = [1.0]
                if fault == "nan": self.client.records["sample_a"]["vector"][0] = float("nan")
                if fault == "payload": self.client.records["sample_a"]["payload"]["content_hash"] = "wrong"
                if fault == "id": self.client.records["unknown"] = self.client.records.pop("sample_a")
                with self.assertRaises(ValueError): self.run_resume()
        self.assertFalse(self.calls)

    def test_wrong_collection_dimension_rejected(self):
        self.client.config.size = 1024
        with self.assertRaisesRegex(ValueError, "dimension"): self.run_resume()

    def test_baseline_config_cannot_receive_4b(self):
        with patch.object(er.fi, "get_config", return_value={"collection": "exp_b2_qwen3emb06b_v140_v1"}):
            with self.assertRaisesRegex(ValueError, "Pinned 4B"): self.run_resume()
        self.assertFalse(self.calls)

    def test_incomplete_scan_is_rejected(self):
        with patch.object(self.client, "count", return_value=NS(count=2)):
            with self.assertRaisesRegex(ValueError, "Incomplete"): self.run_resume()

    def test_low_ram_before_request_stops_without_embedding(self):
        values = iter([8 * 1024**3, 1024**3])
        with self.assertRaisesRegex(ValueError, "2 GiB"):
            er.resume(self.client, self.chunks, {}, {"sample_doc": "a" * 64}, self.anchor,
                      self.path, execute=True, embedder=self.embed, ram=lambda: next(values))
        self.assertFalse(self.calls)

    def test_changed_acknowledged_record_and_missing_record_rejected(self):
        self.run_resume()
        self.client.records["sample_a"]["vector"][0] = 0.7
        with self.assertRaisesRegex(ValueError, "Acknowledged"): self.run_resume()
        del self.client.records["sample_a"]
        with self.assertRaisesRegex(ValueError, "Acknowledged"): self.run_resume()

    def test_unjournaled_extra_rejected(self):
        self.run_resume()
        state = json.loads(self.path.read_text())
        del state["acknowledged"]["sample_b"]
        self.path.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, "Unjournaled"): self.run_resume()

    def test_fingerprint_drift_rejected(self):
        self.run_resume()
        with patch.object(er.fi, "manifest_sha256", return_value="changed"):
            with self.assertRaisesRegex(ValueError, "dataset/input/model"): self.run_resume()

    def test_low_ram_refuses_before_embedding(self):
        with self.assertRaisesRegex(ValueError, "6 GiB"):
            er.resume(self.client, self.chunks, {}, {"sample_doc": "a" * 64}, self.anchor,
                      self.path, execute=True, embedder=self.embed, ram=lambda: 5 * 1024**3)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.calls)

    def test_existing_markers_never_touched(self):
        paths = [self.path.parent / n for n in ("resume-start.json", "batch-adjust-start.json", "paused-checkpoint.json")]
        for p in paths: p.write_bytes(b"sample_only_original")
        self.run_resume()
        self.assertTrue(all(p.read_bytes() == b"sample_only_original" for p in paths))

    def test_invalid_embedder_output_is_not_saved(self):
        with self.assertRaises(er.em.EmbeddingError):
            er.resume(self.client, self.chunks, {}, {"sample_doc": "a" * 64}, self.anchor,
                      self.path, execute=True, embedder=lambda texts: [[1.0] * 1024], ram=lambda: 8 * 1024**3)
        self.assertEqual(self.client.records, self.records)

    def test_os_lock_can_be_reacquired_after_interrupt(self):
        path = self.path.with_suffix(".lock")
        with self.assertRaises(KeyboardInterrupt):
            with er.writer_lock(path): raise KeyboardInterrupt
        with er.writer_lock(path):
            with self.assertRaises(OSError):
                with er.writer_lock(path): pass

    def test_failed_checkpoint_ack_after_commit_reconciles(self):
        save = er.save_state
        count = 0
        def failing_save(path, state):
            nonlocal count
            count += 1
            if count == 3: raise OSError("sample_only disk error")
            save(path, state)
        with patch.object(er, "save_state", side_effect=failing_save):
            with self.assertRaises(OSError): self.run_resume()
        self.run_resume()
        self.assertEqual(self.calls, ["sample b"])


if __name__ == "__main__":
    unittest.main()
