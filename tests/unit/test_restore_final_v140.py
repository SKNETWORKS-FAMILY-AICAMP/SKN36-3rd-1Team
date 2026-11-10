"""sample_only fixtures: restore guards must fail before writes."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from scripts import restore_final_v140 as restore


class RestoreFinalGuards(unittest.TestCase):
    def record(self):
        return {"sample_only": {"vector": [1.0] * 1024,
                "payload": {"text": "sample_only", "category": "old", "life_stages": ["old"]}}}

    def test_existing_databases_are_rejected(self):
        for names, counts in [(["existing"], {"nodes": 0, "relationships": 0}),
                               ([], {"nodes": 1, "relationships": 0})]:
            with self.assertRaisesRegex(ValueError, "empty"):
                restore.require_empty(names, counts)

    def test_loopback_only_and_no_embedded_credentials(self):
        restore.require_local_url("http://127.0.0.1:46338")
        for value in ("https://example.org", "http://user:secret@localhost:6333"):
            with self.assertRaises(ValueError):
                restore.require_local_url(value)

    def test_metadata_only_plan_does_not_mutate_records(self):
        rows = self.record()
        original = copy.deepcopy(rows)
        changes, counts = restore.alignment_plan(rows, {"sample_only": {
            "text": "sample_only", "category": "new", "life_stages": ["new"]}})
        self.assertEqual(rows, original)
        self.assertEqual(len(changes), 1)
        self.assertEqual(counts, {"category": 1, "life_stages": 1})

    def test_already_current_points_are_not_updated(self):
        rows = self.record()
        changes, counts = restore.alignment_plan(rows, {"sample_only": rows["sample_only"]["payload"]})
        self.assertEqual((changes, counts), ([], {}))

    def test_protected_payload_and_id_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            restore.alignment_plan(self.record(), {"sample_only": {"text": "different"}})
        with self.assertRaises(ValueError):
            restore.alignment_plan(self.record(), {})

    def test_invalid_vectors_rejected(self):
        for vector in ([1.0] * 2560, [float("nan")] * 1024):
            rows = self.record()
            rows["sample_only"]["vector"] = vector
            with self.assertRaises(ValueError):
                restore.alignment_plan(rows, {"sample_only": rows["sample_only"]["payload"]})

    def test_vector_digest_is_unaffected_by_metadata(self):
        rows = self.record()
        before = restore.vector_digest(rows)
        protected = restore.protected_digest(rows)
        rows["sample_only"]["payload"]["category"] = "new"
        self.assertEqual(restore.vector_digest(rows), before)
        self.assertEqual(restore.protected_digest(rows), protected)
        rows["sample_only"]["payload"]["text"] = "changed"
        self.assertNotEqual(restore.protected_digest(rows), protected)

    def test_bundle_checksum_and_traversal_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "snapshot").write_bytes(b"sample_only")
            manifest = {"collection": restore.COLLECTION, "dimensions": 1024, "snapshot": "snapshot",
                        "snapshot_sha256": hashlib.sha256(b"sample_only").hexdigest()}
            (folder / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
            checksums = "\n".join(restore.file_sha(folder / name) + "  " + name for name in ("snapshot", "package.json")) + "\n"
            (folder / "SHA256SUMS").write_text(checksums, encoding="utf-8")
            restore.check_bundle(folder)
            (folder / "snapshot").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                restore.check_bundle(folder)
            (folder / "SHA256SUMS").write_text("0" * 64 + "  ../outside\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                restore.check_bundle(folder)


if __name__ == "__main__":
    unittest.main()
