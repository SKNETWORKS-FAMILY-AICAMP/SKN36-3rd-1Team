"""Issue #21 최종 후보를 Issue #5 로더·색인 계획에 연결하는 검사 (DB·임베딩 없음).

경로 상수는 import 시 정해지므로, 후보 데이터셋 검사는 환경 변수를 준 별도 프로세스에서 실행한다.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from database import full_index as fi
from database import handoff_data as hd

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = "data/candidates/issue21-v1.4.0-rag"


def run_with_dataset(code, dataset=CANDIDATE):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    if dataset:
        env[hd.DATASET_DIR_ENV] = dataset
    else:
        env.pop(hd.DATASET_DIR_ENV, None)
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                            encoding="utf-8")
    if result.returncode != 0:
        raise AssertionError(result.stderr[-2000:])
    return json.loads(result.stdout.strip().splitlines()[-1])


class DefaultDatasetUnchangedTests(unittest.TestCase):
    def test_default_paths_and_counts_are_v132(self):
        if os.environ.get(hd.DATASET_DIR_ENV):
            self.skipTest("다른 데이터셋이 설정된 환경")
        self.assertEqual(hd.HANDOFF_DIR, ROOT / "data" / "handoff")
        self.assertEqual(hd.DOCUMENTS_PATH, ROOT / "data" / "processed" / "documents.jsonl")
        self.assertEqual((hd.EXPECTED_CHUNK_COUNT, hd.EXPECTED_NODE_COUNT, hd.EXPECTED_RELATIONSHIP_COUNT),
                         (557, 1034, 3615))
        self.assertEqual(hd.dataset_version(), "1.3.2")
        self.assertFalse(hd.POLICY_METADATA_PATH.is_file())

    def test_issue21_configs_are_rejected_on_v132(self):
        with self.assertRaises(fi.FullIndexError):
            fi.get_config("A_B2_I21")
        self.assertEqual(fi.get_config("A_B2")["collection"], "exp_b2_qwen3emb06b_full_v1")
        self.assertEqual(fi.available_configs(), ("A_B2", "B_B3", "C_B3C1", "A_B2_4B"))

    def test_v132_search_default_and_flags_are_unchanged(self):
        from database import data_quality_flags as dqf
        from database import hybrid_search as hs
        self.assertEqual(hs.default_profile(), hs.DEFAULT_PROFILE)
        self.assertEqual(hs.resolve_search_settings()["collection"], "exp_b2_qwen3emb06b_full_v1")
        self.assertEqual(dqf.load_flags()["status"], "applied")
        with self.assertRaises(fi.FullIndexError):
            hs.resolve_search_profile("b2_06b_i21")


class ManifestEquivalenceTests(unittest.TestCase):
    def setUp(self):
        record = json.loads(fi.MANIFEST_EQUIVALENCE_PATH.read_text(encoding="utf-8"))["equivalences"][0]
        self.old, self.new = record["from_manifest_sha256"], record["to_manifest_sha256"]

    def test_only_recorded_change_with_identical_data_is_accepted(self):
        self.assertTrue(fi.manifest_equivalent(self.new, self.new))
        self.assertTrue(fi.manifest_equivalent(self.old, self.new))
        self.assertFalse(fi.manifest_equivalent("0" * 64, self.new))
        self.assertFalse(fi.manifest_equivalent(self.new, self.old))

    def test_changed_data_file_breaks_equivalence(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "handoff"
            shutil.copytree(ROOT / "data" / "handoff", copy)
            target = copy / "qdrant" / "payload_chunks.jsonl"
            target.write_bytes(target.read_bytes() + b"\n")
            original = hd.HANDOFF_DIR
            try:
                hd.HANDOFF_DIR = copy
                self.assertFalse(fi.manifest_equivalent(self.old, self.new))
            finally:
                hd.HANDOFF_DIR = original


@unittest.skipUnless((ROOT / CANDIDATE / "manifest.json").is_file(), "Issue #21 후보 데이터 없음")
class CandidateDatasetTests(unittest.TestCase):
    def test_loader_inputs_counts_and_hashes(self):
        out = run_with_dataset("""
import json
from database import handoff_data as hd
from database.neo4j import load_graph as lg
docs = hd.load_documents(verify_hash=True)
meta = hd.load_policy_metadata()
chunks = hd.load_payload_chunks()
graph = lg.read_graph_input()
report = lg.validate_graph_input(graph)
rels = graph.relationships
print(json.dumps({
  "version": hd.dataset_version(), "docs": len(docs), "meta": len(meta), "chunks": len(chunks),
  "expected": [hd.EXPECTED_DOCUMENT_COUNT, hd.EXPECTED_CHUNK_COUNT, hd.EXPECTED_NODE_COUNT, hd.EXPECTED_RELATIONSHIP_COUNT],
  "manifest_bad": [r["file"] for r in hd.verify_manifest_checksums() if not r["matched"]],
  "graph_ok": report.ok, "nodes": sum(len(v) for v in graph.nodes_by_label.values()), "rels": len(rels),
  "chunk_refs_ok": all(c["document_id"] in {d["document_id"] for d in docs} and c["policy_id"] in meta for c in chunks),
  "raw_value": sum("raw_value" in r for r in rels),
  "raw_value_kept": all(lg._relationship_row(r)["properties"].get("raw_value") == r["raw_value"] for r in rels if "raw_value" in r),
  "logic": sorted({g["logic"] for g in graph.nodes_by_label["ConditionGroup"]}),
  "needs_review_policies": sum(p.get("eligibility_review_status") == "needs_review" for p in graph.nodes_by_label["Policy"]),
  "region_detail": sum("region_detail" in m for m in meta.values()),
}, ensure_ascii=False))
""")
        self.assertEqual(out["version"], "issue21-v1.4.0-review")
        self.assertEqual((out["docs"], out["meta"], out["chunks"]), (261, 246, 599))
        self.assertEqual(out["expected"], [261, 599, 1185, 4427])
        self.assertEqual(out["manifest_bad"], [])
        self.assertTrue(out["graph_ok"])
        self.assertEqual((out["nodes"], out["rels"]), (1185, 4427))
        self.assertTrue(out["chunk_refs_ok"])
        self.assertEqual(out["raw_value"], 14)
        self.assertTrue(out["raw_value_kept"])
        self.assertEqual(out["logic"], ["AND", "OR", "UNKNOWN"])
        self.assertEqual(out["needs_review_policies"], 246)
        self.assertEqual(out["region_detail"], 22)

    def test_index_plans_are_ready_and_v132_configs_are_rejected(self):
        out = run_with_dataset("""
import json
from database import handoff_data as hd, full_index as fi
docs = hd.load_documents(verify_hash=True)
meta = hd.load_policy_metadata()
plans = {}
for name in ("A_B2_I21", "B_B3_I21"):
    p = fi.plan_full_index(name, documents=docs, policy_metadata=meta, client=None)
    plans[name] = {k: p[k] for k in ("collection", "chunks", "chunk_count_matches", "unique_point_ids",
                                     "point_id_is_uuid5_chunk_id", "restore_mismatch", "missing_documents",
                                     "missing_policies", "ready")}
try:
    fi.get_config("A_B2"); rejected = False
except fi.FullIndexError:
    rejected = True
from database import hybrid_search as hs, data_quality_flags as dqf
settings = hs.resolve_search_settings()
print(json.dumps({"plans": plans, "v132_rejected": rejected, "default_profile": hs.default_profile(),
                  "default_collection": settings["collection"], "flags": dqf.load_flags()["status"],
                  "b3_profile": hs.resolve_search_profile("b3_06b_i21")["collection"]}, ensure_ascii=False))
""")
        self.assertTrue(out["v132_rejected"])
        self.assertEqual((out["default_profile"], out["default_collection"]), ("b2_06b_i21", "exp_b2_qwen3emb06b_i21_v1"))
        self.assertEqual(out["b3_profile"], "exp_b3_qwen3emb06b_i21_v1")
        # v1.3.2 그래프로 만든 정적 플래그는 다른 데이터셋에 적용하지 않는다 (stale이 아니라 적용 대상 아님)
        self.assertEqual(out["flags"], "not_applicable")
        for name, chunks in (("A_B2_I21", 1138), ("B_B3_I21", 1220)):
            plan = out["plans"][name]
            with self.subTest(name=name):
                self.assertEqual(plan["chunks"], chunks)
                self.assertTrue(plan["ready"] and plan["chunk_count_matches"] and plan["unique_point_ids"])
                self.assertEqual((plan["restore_mismatch"], plan["missing_documents"], plan["missing_policies"]), (0, [], []))
                self.assertTrue(plan["collection"].endswith("_i21_v1"))

    def test_payload_carries_optional_policy_fields(self):
        from database.qdrant import store
        chunk = {"chunk_id": "DOC_SAMPLE_C01", "policy_id": "POL_SAMPLE", "document_id": "DOC_SAMPLE", "text": "sample_only",
                 "source_url": "https://example.org", "chunking_version": "sample_only"}
        meta = {f: "sample_only" for f in hd.POLICY_METADATA_FIELDS}
        run = {f: "sample_only" for f in store.RUN_INFO_FIELDS}
        plain = store.build_point_payload(chunk, policy_meta=meta, content_hash="hash", run_info=run)
        self.assertNotIn("region_detail", plain)
        extended = store.build_point_payload(chunk, policy_meta=dict(meta, region_detail="충청남도 천안시",
                                             eligibility_review_status="needs_review"), content_hash="hash", run_info=run)
        self.assertEqual(extended["region_detail"], "충청남도 천안시")
        self.assertEqual(extended["eligibility_review_status"], "needs_review")


class RegionDetailWarningTests(unittest.TestCase):
    def test_region_detail_replaces_lost_granularity_warning(self):
        from database import hybrid_search as hs
        trees = [{"group_id": "GRP_SAMPLE", "logic": "AND", "conditions": [], "groups": []}]
        base = {"policy": {"organization": "충청남도 천안시"}, "regions": [{"name": "충청남도"}]}
        warnings = []
        hs._graph_quality_patterns(base, trees, warnings)
        self.assertIn(hs.QUALITY_REGION_GRANULARITY, {w["code"] for w in warnings})
        warnings = []
        detailed = dict(base, policy={"organization": "충청남도 천안시", "region_detail": "충청남도 천안시"})
        hs._graph_quality_patterns(detailed, trees, warnings)
        codes = {w["code"] for w in warnings}
        self.assertIn(hs.QUALITY_REGION_DETAIL, codes)
        self.assertNotIn(hs.QUALITY_REGION_GRANULARITY, codes)
        self.assertTrue(hs.QUALITY_CODE_META[hs.QUALITY_REGION_DETAIL]["blocks_auto_eligibility"])


if __name__ == "__main__":
    unittest.main()
