"""sample_only fixtures: restore guards must fail before writes."""
import copy
import hashlib
import json
import os
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
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
            manifest = {"collection": restore.COLLECTION, "dimensions": restore.SNAPSHOT_DIMENSIONS,
                        "points": restore.SNAPSHOT_POINTS, "snapshot": "snapshot",
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



class CrossDbAndHashGuards(unittest.TestCase):
    def test_exact_pass_and_empty_failures(self):
        restore.require_cross_db_pass({"status": "PASS", "failures": []})

    def test_cross_db_abnormal_status_or_failures_fail(self):
        for status, failures in (("MISMATCH", []), ("PASS", [{"sample_only": True}]),
                                 ("NOT_TESTED", []), ("UNKNOWN", []), (None, []), ("PASS", None)):
            with self.subTest(status=status, failures=failures):
                result = {"failures": failures, "qdrant_points": 7, "qdrant_count": restore.SNAPSHOT_POINTS}
                if status is not None:
                    result["status"] = status
                with self.assertRaisesRegex(ValueError, r"status=.*checked=7, total=1138"):
                    restore.require_cross_db_pass(result)

    def test_untrusted_status_is_not_echoed(self):
        with self.assertRaises(ValueError) as raised:
            restore.require_cross_db_pass({"status": "sample_only_sensitive_value", "failures": []})
        self.assertIn("status=UNKNOWN", str(raised.exception))
        self.assertNotIn("sample_only_sensitive_value", str(raised.exception))

    def test_real_cross_db_partial_read_can_have_empty_failures(self):
        from database import hybrid_search as hs
        row = {"policy_id": "POL_SAMPLE_ONLY", "document_id": "DOC_SAMPLE_ONLY", "chunk_id": "sample_only"}
        with patch.object(hs, "read_payload_keys", return_value=[row]), \
             patch.object(hs.store, "count_points", return_value=2), \
             patch.object(hs, "_read", side_effect=[
                 [{"policy_id": row["policy_id"], "exists": True}],
                 [{"document_id": row["document_id"]}]]), \
             patch.object(hs, "_check_chunk", return_value={k: "PASS" for k in hs.CROSS_CHECKS}):
            result = hs.verify_cross_db(client=object(), runner=object(), collection="exp_sample_only")
        self.assertEqual((result["status"], result["failures"]), ("MISMATCH", []))
        self.assertFalse(result["all_points_read"])
        with self.assertRaisesRegex(ValueError, "checked=1, total=2"):
            restore.require_cross_db_pass(result)

    def test_hash_mapping_preserves_values(self):
        value = hashlib.sha256(b"sample_only").hexdigest()
        self.assertEqual(restore.build_document_hashes([{"document_id": "DOC_SAMPLE", "content_hash": value}]),
                         {"DOC_SAMPLE": value})

    def test_duplicate_document_even_same_hash_rejected(self):
        document = {"document_id": "DOC_SAMPLE", "content_hash": "a" * 64}
        with self.assertRaisesRegex(ValueError, "Duplicate document_id"):
            restore.build_document_hashes([document, dict(document)])

    def test_invalid_document_and_hash_rejected(self):
        for value in (None, "", "a" * 63, "g" * 64, "A" * 64, 123):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Invalid SHA-256"):
                restore.build_document_hashes([{"document_id": "DOC_SAMPLE", "content_hash": value}])
        for document_id in (None, "", " ", 1):
            with self.subTest(document_id=document_id), self.assertRaisesRegex(ValueError, "Invalid document_id"):
                restore.build_document_hashes([{"document_id": document_id, "content_hash": "a" * 64}])

    def test_snapshot_criteria_and_package_points_are_fixed(self):
        self.assertEqual((restore.SNAPSHOT_DIMENSIONS, restore.SNAPSHOT_POINTS), (1024, 1138))
        self.assertEqual(restore.EXPECTED, (246, 261, 599, 1138, 1004, 2431, 136))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "snapshot").write_bytes(b"sample_only")
            for updates in ({"dimensions": 2560}, {"points": 1137}, {"points": None}, {}):
                manifest = {"collection": restore.COLLECTION, "dimensions": restore.SNAPSHOT_DIMENSIONS,
                            "points": restore.SNAPSHOT_POINTS, "snapshot": "snapshot",
                            "snapshot_sha256": restore.file_sha(folder / "snapshot")}
                manifest.update(updates)
                if not updates:
                    manifest.pop("points")
                (folder / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
                (folder / "SHA256SUMS").write_text(
                    "".join(restore.file_sha(folder / n) + "  " + n + "\n"
                            for n in ("package.json", "snapshot")), encoding="utf-8")
                with self.subTest(updates=updates), self.assertRaisesRegex(ValueError, "dimension/count"):
                    restore.check_bundle(folder)


class RestoreExecutionPaths(unittest.TestCase):
    """sample_only full-size fixtures keep production count guards enabled."""

    def setup_case(self, stack, *, verify_only=False, changes=0, after_fault=None,
                   links_override=None, graph_fault=None, missing_document=False):
        from database import handoff_data as hd, full_index as fi, hybrid_search as hs
        from database.neo4j import load_graph as lg
        from database.qdrant import store
        documents = [{"document_id": f"DOC_SAMPLE_{i}", "policy_id": f"POL_SAMPLE_{i % 246}",
                      "content_hash": hashlib.sha256(f"sample_only_{i}".encode()).hexdigest()}
                     for i in range(261)]
        chunks = [{"point_id": f"sample_only_{i}", "document_id": documents[i % 261]["document_id"]}
                  for i in range(restore.SNAPSHOT_POINTS)]
        hashes = {d["document_id"]: d["content_hash"] for d in documents}
        expected = {c["point_id"]: {"text": "sample_only", "category": "sample_new",
                    "life_stages": ["sample_new"], "content_hash": hashes[c["document_id"]],
                    "embedding_model": restore.MODEL, "embedding_dim": restore.SNAPSHOT_DIMENSIONS,
                    "embedding_input_rule": fi.EMBEDDING_INPUT_RULE, "index_run_id": "sample_only"}
                    for c in chunks}
        vector = [1.0] * restore.SNAPSHOT_DIMENSIONS
        before = {key: {"payload": dict(payload), "vector": vector} for key, payload in expected.items()}
        after = {key: {"payload": dict(payload), "vector": vector} for key, payload in expected.items()}
        for i in range(changes):
            before[f"sample_only_{i}"]["payload"]["category"] = "sample_old"
        if after_fault == "metadata":
            after["sample_only_0"]["payload"]["category"] = "sample_old"
        elif after_fault == "protected":
            after["sample_only_0"]["payload"]["text"] = "changed_sample_only"
        elif after_fault == "vector":
            after["sample_only_0"]["vector"] = [-1.0] + vector[1:]
        if missing_document:
            chunks[0]["document_id"] = "DOC_SAMPLE_MISSING"
        snapshot = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "sample.snapshot"
        snapshot.write_bytes(b"sample_only")
        package = {"manifest_lf_sha256": "sample_manifest", "snapshot_sha256": "sample_hash",
                   "vector_digest": restore.vector_digest(before)}
        graph = object()
        validation = SimpleNamespace(ok=True, node_counts={"sample": 1004},
                                     relationship_counts={"REQUIRES_CONDITION": 136, "sample": 2295})
        client, driver, runner = MagicMock(), MagicMock(), MagicMock()
        client.get_collections.return_value.collections = []
        client.get_collection.return_value = SimpleNamespace(
            points_count=restore.SNAPSHOT_POINTS, config=SimpleNamespace(params=SimpleNamespace(
                vectors=SimpleNamespace(size=restore.SNAPSHOT_DIMENSIONS, distance="Cosine"))))
        runner.read_rows.return_value = {"unused": True}  # replaced by contract-shaped rows below
        runner.read_rows.side_effect = ([{"nodes": 1004, "relationships": 2431}],) if verify_only else (
            [{"nodes": 0, "relationships": 0}], [{"nodes": 1004, "relationships": 2431}])
        verification = {"ok": True, "node_properties": {"ok": True}, "relationship_properties": {"ok": True}}
        load_result = {"ok": True, "verification": verification}
        direct_result = verification
        if graph_fault == "load_top":
            load_result = {"ok": False, "verification": verification}
        elif graph_fault == "load_nested":
            load_result = {"ok": True, "verification": {"ok": False}}
        elif graph_fault == "load_missing":
            load_result = {"ok": True}
        elif graph_fault == "direct":
            direct_result = {"ok": False}
        links = {"status": "PASS", "failures": [], "qdrant_points": restore.SNAPSHOT_POINTS,
                 "qdrant_count": restore.SNAPSHOT_POINTS, "all_points_read": True, "summary": {}}
        if links_override:
            links.update(links_override)
            if links_override.get("status") is None:
                links.pop("status", None)

        def replace(obj, name, **kwargs):
            return stack.enter_context(patch.object(obj, name, **kwargs))

        stack.enter_context(patch.dict(os.environ, {
            "QDRANT_URL": "http://127.0.0.1:1", "QDRANT_API_KEY": "sample_only",
            "NEO4J_URI": "bolt://127.0.0.1:1", "NEO4J_USER": "sample_only",
            "NEO4J_PASSWORD": "sample_only"}))
        replace(restore, "check_bundle", return_value=(package, snapshot))
        scan = replace(restore, "scan", side_effect=[before, after])
        replace(hd, "verify_manifest_checksums", return_value=[])
        replace(hd, "load_documents", return_value=documents)
        replace(hd, "load_payload_metadata", return_value={})
        replace(hd, "load_payload_chunks", return_value=[None] * 599)
        replace(fi, "manifest_sha256", return_value="sample_manifest")
        replace(fi, "build_config_chunks", return_value=(chunks, {}))
        replace(lg, "read_graph_input", return_value=graph)
        replace(lg, "validate_graph_input", return_value=validation)
        replace(lg, "Neo4jDriverRunner", return_value=runner)
        load = replace(lg, "load_graph", return_value=load_result)
        verify = replace(lg, "verify_loaded_graph", return_value=direct_result)
        cross = replace(hs, "verify_cross_db", return_value=links)
        replace(store, "metadata_for_chunk", return_value={})

        def build(chunk, **kwargs):
            self.assertEqual(kwargs["content_hash"], hashes[chunk["document_id"]])
            self.assertEqual(kwargs["run_info"]["embedding_dim"], restore.SNAPSHOT_DIMENSIONS)
            return dict(expected[chunk["point_id"]])

        builder = replace(store, "build_point_payload", side_effect=build)
        connection = stack.enter_context(patch("neo4j.GraphDatabase.driver", return_value=driver))
        stack.enter_context(patch("qdrant_client.QdrantClient", return_value=client))
        post = stack.enter_context(patch("requests.post"))
        args = SimpleNamespace(repo=Path.cwd(), bundle=Path("sample_only"), apply=not verify_only,
                               verify_only=verify_only)
        return SimpleNamespace(args=args, scan=scan, client=client, driver=driver, load=load,
                               verify=verify, cross=cross, post=post, builder=builder, connection=connection)

    def test_no_changes_scans_once_and_cross_checks_both_modes(self):
        for verify_only in (False, True):
            with self.subTest(verify_only=verify_only), ExitStack() as stack:
                case = self.setup_case(stack, verify_only=verify_only)
                result = restore.restore(case.args)
                self.assertEqual(result["status"], "PASS")
                self.assertTrue(result["full_graph_properties_verified"])
                self.assertEqual(result["cross_db"]["status"], "PASS")
                self.assertEqual(result["cross_db"]["qdrant_count"], restore.SNAPSHOT_POINTS)
                self.assertEqual(case.scan.call_count, 1)
                case.client.set_payload.assert_not_called()
                case.cross.assert_called_once()
                self.assertEqual(case.builder.call_count, restore.SNAPSHOT_POINTS)
                if verify_only:
                    case.verify.assert_called_once()
                    case.load.assert_not_called()
                    case.post.assert_not_called()
                    self.assertEqual(result["graph_verification_source"], "verify_loaded_graph")
                else:
                    case.load.assert_called_once()
                    self.assertIs(case.load.call_args.kwargs["verify"], True)
                    case.verify.assert_not_called()
                    case.post.assert_called_once()
                    self.assertEqual(result["graph_verification_source"], "load_graph.verification")
                case.client.close.assert_called_once()
                case.driver.close.assert_called_once()

    def test_changed_payload_scans_twice_and_cross_checks(self):
        with ExitStack() as stack:
            case = self.setup_case(stack, changes=3)
            result = restore.restore(case.args)
            self.assertEqual(result["metadata_points_updated"], 3)
            self.assertEqual(case.scan.call_count, 2)
            self.assertEqual(case.client.set_payload.call_count, 3)
            case.cross.assert_called_once()

    def test_modified_payload_and_vectors_must_match_after_write(self):
        for fault in ("metadata", "protected", "vector"):
            with self.subTest(fault=fault), ExitStack() as stack:
                case = self.setup_case(stack, changes=1, after_fault=fault)
                with self.assertRaises(ValueError):
                    restore.restore(case.args)
                self.assertEqual(case.scan.call_count, 2)
                case.load.assert_not_called()
                case.cross.assert_not_called()

    def test_verify_only_refuses_pending_changes(self):
        with ExitStack() as stack:
            case = self.setup_case(stack, verify_only=True, changes=1)
            with self.assertRaisesRegex(ValueError, "verify-only refuses"):
                restore.restore(case.args)
            self.assertEqual(case.scan.call_count, 1)
            case.client.set_payload.assert_not_called()
            case.cross.assert_not_called()

    def test_loader_full_verification_failure_never_returns_pass(self):
        for fault in ("load_top", "load_nested", "load_missing", "direct"):
            with self.subTest(fault=fault), ExitStack() as stack:
                case = self.setup_case(stack, verify_only=fault == "direct", graph_fault=fault)
                with self.assertRaisesRegex(ValueError, "Neo4j"):
                    restore.restore(case.args)
                case.cross.assert_not_called()

    def test_invalid_cross_db_status_never_returns_pass_in_either_mode(self):
        for verify_only in (False, True):
            for status in ("MISMATCH", "NOT_TESTED", "UNKNOWN", None):
                with self.subTest(verify_only=verify_only, status=status), ExitStack() as stack:
                    case = self.setup_case(stack, verify_only=verify_only, links_override={"status": status})
                    with self.assertRaisesRegex(ValueError, "Cross-DB validation failed"):
                        restore.restore(case.args)
                    case.cross.assert_called_once()

    def test_cross_db_pass_with_failures_never_returns_pass(self):
        with ExitStack() as stack:
            case = self.setup_case(stack, links_override={"failures": [{"sample_only": True}]})
            with self.assertRaises(ValueError):
                restore.restore(case.args)

    def test_missing_chunk_document_fails_before_connect_or_write(self):
        with ExitStack() as stack:
            case = self.setup_case(stack, missing_document=True)
            with self.assertRaisesRegex(ValueError, "DOC_SAMPLE_MISSING"):
                restore.restore(case.args)
            case.connection.assert_not_called()
            case.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
