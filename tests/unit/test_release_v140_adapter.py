"""
승인된 v1.4.0 릴리스(data/releases/v1.4.0-candidate) 입력 어댑터 테스트. DB·임베딩 없음.

- 가상 패키지: 누락·중복·해시 불일치·문서 내 메타데이터 불일치에서 멈추는지, 문서별 값을 합치지 않는지
- 실제 릴리스(하위 프로세스, LIFEPOLICY_DATASET_DIR): 문서 261·해시 일치, 문서별 메타데이터, B2/B3 청크,
  Qdrant payload 필수 필드, Neo4j 1,030노드/2,921관계 입력 검증, 원본 파일 불변
- 명시적 v1.3.2 선택 시 호환 동작 회귀
공식 정책 내용의 정확성은 검증하지 않는다 (저장된 원천과의 일치만 본다).
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from database import release_adapter as ra  # noqa: E402

RELEASE = ROOT / "data" / "releases" / "v1.4.0-candidate"
META = {"policy_name": "sample_only", "category": "sample_only", "region": "전국", "life_stages": ["청년"],
        "target_groups": ["청년"], "eligibility_attributes": {"income_level": []}, "verification_status": "extracted"}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def tree_digest(directory: Path) -> dict[str, str]:
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file()}


class SyntheticPackageTests(unittest.TestCase):
    """가상 sample_only 패키지로 어댑터의 중단 조건을 확인한다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.package = self.root / "data" / "releases" / "sample"
        law = "제1조 sample_only 조문"
        (self.root / "data" / "staging" / "official").mkdir(parents=True)
        (self.root / "data" / "staging" / "official" / "law.md").write_text("> 출처 메모\n" + law, encoding="utf-8")
        write_jsonl(self.root / "data" / "processed" / "documents.jsonl",
                    [{"document_id": "DOC_A", "policy_id": "POL_A", "text": "본문 A", "content_hash": sha("본문 A")}])
        self.nodes = [
            {"document_id": "DOC_A", "policy_id": "POL_A", "source_type": "api", "content_hash": sha("본문 A")},
            {"document_id": "DOC_B", "policy_id": "POL_A", "source_type": "law_text", "doc_type": "law",
             "local_raw_path": "data/staging/official/law.md", "content_hash": sha(law)},
        ]
        self.chunks = [
            {"chunk_id": "C_A1", "document_id": "DOC_A", "policy_id": "POL_A", **META},
            {"chunk_id": "C_A2", "document_id": "DOC_A", "policy_id": "POL_A", **META},
            {"chunk_id": "C_B1", "document_id": "DOC_B", "policy_id": "POL_A",
             **{**META, "verification_status": "verified_official_text", "life_stages": []}},
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, nodes=None, chunks=None):
        write_jsonl(self.package / "neo4j" / "nodes_document.jsonl", self.nodes if nodes is None else nodes)
        write_jsonl(self.package / "qdrant" / "payload_chunks.jsonl", self.chunks if chunks is None else chunks)

    def test_documents_assembled_in_node_order_with_rule(self):
        self.write()
        self.assertTrue(ra.is_release_package(self.package))
        docs = ra.load_release_documents(self.package, self.root)
        self.assertEqual([d["document_id"] for d in docs], ["DOC_A", "DOC_B"])
        self.assertEqual(docs[1]["text"], "제1조 sample_only 조문")         # '>' 출처 메모 줄 제외
        self.assertTrue(all(sha(d["text"]) == d["content_hash"] for d in docs))

    def test_missing_body_stops(self):
        self.write(nodes=self.nodes + [{"document_id": "DOC_C", "policy_id": "POL_A", "source_type": "pdf",
                                        "content_hash": sha("x")}])
        with self.assertRaisesRegex(ra.ReleaseAdapterError, "본문을 찾지 못한 문서 1건.*DOC_C"):
            ra.load_release_documents(self.package, self.root)

    def test_duplicate_document_id_stops(self):
        self.write(nodes=self.nodes + [self.nodes[0]])
        with self.assertRaisesRegex(ra.ReleaseAdapterError, "document_id 중복: DOC_A"):
            ra.load_release_documents(self.package, self.root)

    def test_hash_mismatch_stops(self):
        self.write(nodes=[{**self.nodes[0], "content_hash": sha("다른 본문")}, self.nodes[1]])
        with self.assertRaisesRegex(ra.ReleaseAdapterError, "content_hash와 본문이 다른 문서 1건.*DOC_A"):
            ra.load_release_documents(self.package, self.root)

    def test_metadata_is_per_document_not_merged_by_policy(self):
        self.write()
        meta = ra.load_release_document_metadata(self.package)
        self.assertEqual(meta["DOC_A"]["verification_status"], "extracted")
        self.assertEqual(meta["DOC_B"]["verification_status"], "verified_official_text")   # 같은 정책, 다른 문서 값 유지
        self.assertEqual(meta["DOC_B"]["life_stages"], [])

    def test_metadata_inconsistency_within_document_stops(self):
        bad = self.chunks[:1] + [{**self.chunks[1], "region": "서울특별시"}] + self.chunks[2:]
        self.write(chunks=bad)
        with self.assertRaisesRegex(ra.ReleaseAdapterError, "같은 문서의 청크 메타데이터 불일치: DOC_A"):
            ra.load_release_document_metadata(self.package)

    def test_document_without_chunks_and_policy_mismatch_stop(self):
        self.write(chunks=self.chunks[:2])
        with self.assertRaisesRegex(ra.ReleaseAdapterError, "청크 메타데이터가 없는 문서 1건.*DOC_B"):
            ra.load_release_document_metadata(self.package)
        self.write(chunks=self.chunks[:2] + [{**self.chunks[2], "policy_id": "POL_X"}])
        with self.assertRaisesRegex(ra.ReleaseAdapterError, "policy_id가 다름"):
            ra.load_release_document_metadata(self.package)

    def test_path_outside_root_is_rejected(self):
        self.write(nodes=[self.nodes[0], {**self.nodes[1], "local_raw_path": "../../etc/law.md"}])
        with self.assertRaisesRegex(ra.ReleaseAdapterError, "저장소 밖"):
            ra.load_release_documents(self.package, self.root)


RELEASE_CHECK = r"""
import json, hashlib
from collections import Counter
import database.handoff_data as hd
from database import full_index, chunking
from database.qdrant import store
from database.neo4j import load_graph as lg
out = {"is_release": hd.IS_RELEASE_PACKAGE, "version": hd.dataset_version(),
       "expected": [hd.EXPECTED_POLICY_COUNT, hd.EXPECTED_DOCUMENT_COUNT, hd.EXPECTED_CHUNK_COUNT,
                    hd.EXPECTED_NODE_COUNT, hd.EXPECTED_RELATIONSHIP_COUNT],
       "manifest_bad": [r["file"] for r in hd.verify_manifest_checksums() if not r["matched"]]}
docs = hd.load_documents(verify_hash=True)
meta = hd.load_payload_metadata()
source = hd.load_payload_chunks()
nodes = {d["document_id"]: d for d in hd.load_neo4j_nodes()["Document"]}
out["documents"] = len(docs); out["unique_documents"] = len({d["document_id"] for d in docs})
out["documents_equal_nodes"] = [d["document_id"] for d in docs] == list(nodes)
out["hash_mismatch"] = sum(hashlib.sha256(d["text"].encode()).hexdigest() != d["content_hash"] for d in docs)
out["metadata_documents"] = len(meta)
out["metadata_keyed_by_document"] = set(meta) == set(nodes)
out["metadata_preserved"] = all(meta[c["document_id"]][f] == c[f] for c in source for f in hd.POLICY_METADATA_FIELDS)
out["policy_level_conflicts_kept"] = sorted({c["policy_id"] for c in source}
    - {p for p in {c["policy_id"] for c in source}
       if len({json.dumps(meta[d], sort_keys=True, ensure_ascii=False) for d in nodes if nodes[d]["policy_id"] == p}) == 1})
policies = {p["policy_id"] for p in hd.load_neo4j_nodes()["Policy"]}
run = {"embedding_model": "dry-run", "embedding_dim": 0, "embedding_input_rule": "dry-run", "index_run_id": "dry-run"}
hashes = {d["document_id"]: d["content_hash"] for d in docs}
from database import release_adapter, embedding as em
out["metadata_type_is_document"] = isinstance(meta, release_adapter.DocumentMetadata)
# 임베딩 입력 경로와 payload 경로가 같은 문서 메타데이터를 쓰는지 (호출 인자를 기록)
seen_embed = {}
original = em.build_embedding_text
def recording(chunk, policy_meta, rule):
    seen_embed[chunk["chunk_id"]] = policy_meta
    return original(chunk, policy_meta, rule)
em.build_embedding_text = recording
try:
    stray = {"chunk_id": "X", "document_id": "DOC_NOT_IN_RELEASE", "policy_id": next(iter(policies))}
    store.metadata_for_chunk(meta, stray); out["unknown_document_raises"] = False
except ValueError:
    out["unknown_document_raises"] = True
for name in ("A_B2_V140", "B_B3_V140"):
    plan = full_index.plan_full_index(name, documents=docs, policy_metadata=meta, client=None)
    chunks, _ = full_index.build_config_chunks(name, docs)
    missing_fields = set(); wrong_meta = 0
    for c in chunks:
        p = store.build_point_payload(c, policy_meta=store.metadata_for_chunk(meta, c),
                                      content_hash=hashes[c["document_id"]], run_info=run)
        missing_fields |= {f for f in store.REQUIRED_PAYLOAD_FIELDS if f not in p}
        wrong_meta += any(p[f] != meta[c["document_id"]][f] for f in hd.POLICY_METADATA_FIELDS)
    points = store.build_points(chunks, [[0.0]] * len(chunks), policy_metadata=meta, content_hashes=hashes,
                                run_info={**run, "embedding_dim": 1})
    embed_vs_doc = sum(seen_embed[c["chunk_id"]] is not meta[c["document_id"]] for c in chunks)
    payload_vs_doc = sum(any(pt["payload"][f] != meta[c["document_id"]][f] for f in hd.POLICY_METADATA_FIELDS)
                         for c, pt in zip(chunks, points))
    out[name + "_paths"] = {"embedding_input_not_document_meta": embed_vs_doc, "payload_not_document_meta": payload_vs_doc}
    out[name] = {"chunks": plan["chunks"], "expected": plan["expected_chunks"], "ready": plan["ready"],
                 "unique_point_ids": plan["unique_point_ids"], "unique_chunk_ids": len({c["chunk_id"] for c in chunks}) == len(chunks),
                 "restore_mismatch": plan["restore_mismatch"], "missing_documents": plan["missing_documents"],
                 "missing_policies": plan["missing_policies"], "policies_covered": len({c["policy_id"] for c in chunks}),
                 "policies_total": len(policies), "missing_payload_fields": sorted(missing_fields), "wrong_payload_metadata": wrong_meta}
graph = lg.read_graph_input(hd.NEO4J_HANDOFF_DIR)
report = lg.validate_graph_input(graph)
out["graph_ok"] = report.ok
out["graph_nodes"] = sum(len(v) for v in hd.load_neo4j_nodes().values())
out["graph_relationships"] = len(hd.load_neo4j_relationships())
out["configs"] = list(full_index.available_configs())
print(json.dumps(out, ensure_ascii=False))
"""


class ApprovedReleaseTests(unittest.TestCase):
    """실제 승인 릴리스를 LIFEPOLICY_DATASET_DIR로 선택해 하위 프로세스에서 검사한다 (DB·임베딩 없음)."""

    @classmethod
    def setUpClass(cls):
        assert RELEASE.is_dir(), "승인 릴리스 data/releases/v1.4.0-candidate가 저장소에 있어야 한다"
        cls.before = tree_digest(RELEASE)
        env = {**os.environ, "LIFEPOLICY_DATASET_DIR": "data/releases/v1.4.0-candidate", "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run([sys.executable, "-c", RELEASE_CHECK], cwd=ROOT, env=env, capture_output=True,
                                text=True, encoding="utf-8", timeout=900)
        if result.returncode != 0:
            raise AssertionError(result.stderr[-3000:])
        cls.out = json.loads(result.stdout.strip().splitlines()[-1])
        cls.after = tree_digest(RELEASE)

    def test_release_files_unchanged_and_manifest_matches(self):
        self.assertEqual(self.before, self.after)
        self.assertEqual(len(self.before), 21)
        self.assertEqual(self.out["manifest_bad"], [])

    def test_release_selected_with_manifest_counts(self):
        self.assertTrue(self.out["is_release"])
        self.assertEqual(self.out["version"], "1.4.0-candidate")
        self.assertEqual(self.out["expected"], [246, 261, 599, 1030, 2921])
        self.assertEqual(self.out["configs"], ["A_B2_V140", "B_B3_V140"])

    def test_all_documents_linked_with_matching_hash(self):
        self.assertEqual((self.out["documents"], self.out["unique_documents"]), (261, 261))
        self.assertTrue(self.out["documents_equal_nodes"])
        self.assertEqual(self.out["hash_mismatch"], 0)

    def test_metadata_per_document_preserved(self):
        self.assertEqual(self.out["metadata_documents"], 261)
        self.assertTrue(self.out["metadata_keyed_by_document"])
        self.assertTrue(self.out["metadata_preserved"])
        # 정책 단위로 합치면 충돌하는 5개 정책: 문서별 값이 그대로 남아 있어야 한다
        self.assertEqual(self.out["policy_level_conflicts_kept"],
                         ["POL_001", "POL_STG_EX_001", "POL_STG_ICN_001", "POL_STG_MOIS_001", "POL_STG_SEOUL_001"])

    def test_b2_b3_chunks_and_payloads(self):
        for name, expected in (("A_B2_V140", 1138), ("B_B3_V140", 1220)):
            plan = self.out[name]
            with self.subTest(config=name):
                self.assertEqual((plan["chunks"], plan["expected"]), (expected, expected))
                self.assertTrue(plan["ready"])
                self.assertTrue(plan["unique_point_ids"] and plan["unique_chunk_ids"])
                self.assertEqual(plan["restore_mismatch"], 0)
                self.assertEqual((plan["missing_documents"], plan["missing_policies"]), ([], []))
                self.assertEqual(plan["policies_covered"], plan["policies_total"])
                self.assertEqual(plan["policies_total"], 246)
                self.assertEqual(plan["missing_payload_fields"], [])
                self.assertEqual(plan["wrong_payload_metadata"], 0)

    def test_document_metadata_strict_on_all_paths(self):
        self.assertTrue(self.out["metadata_type_is_document"])
        self.assertTrue(self.out["unknown_document_raises"])           # 정책 값으로 대체하지 않고 오류
        for name in ("A_B2_V140", "B_B3_V140"):
            with self.subTest(config=name):
                self.assertEqual(self.out[name + "_paths"],
                                 {"embedding_input_not_document_meta": 0, "payload_not_document_meta": 0})

    def test_neo4j_graph_input(self):
        self.assertTrue(self.out["graph_ok"])
        self.assertEqual((self.out["graph_nodes"], self.out["graph_relationships"]), (1030, 2921))


class DefaultDatasetRegressionTests(unittest.TestCase):
    """data/handoff를 명시한 v1.3.2 호환 동작."""

    def test_explicit_legacy_is_not_release_and_metadata_is_policy_keyed(self):
        import database.handoff_data as hd
        from database.qdrant import store
        self.assertFalse(hd.IS_RELEASE_PACKAGE)
        self.assertEqual(hd.dataset_version(), "1.3.2")
        self.assertEqual(hd.load_payload_metadata(), hd.load_policy_metadata())
        chunk = hd.load_payload_chunks()[0]
        meta = hd.load_policy_metadata()
        self.assertIs(store.metadata_for_chunk(meta, chunk), meta[chunk["policy_id"]])

    def test_lookup_mode_is_explicit_and_never_falls_back(self):
        """문서별 메타데이터(DocumentMetadata)는 document_id로만, 정책별(dict)은 policy_id로만 찾는다."""
        from database.qdrant import store
        chunk = {"chunk_id": "C1", "document_id": "DOC_A", "policy_id": "POL_A"}
        # 기존(v1.3.2) 정책 단위 dict: policy_id로만
        self.assertEqual(store.metadata_for_chunk({"POL_A": {"v": 2}}, chunk), {"v": 2})
        with self.assertRaisesRegex(ValueError, "정책 메타데이터 없음: POL_A"):
            store.metadata_for_chunk({"DOC_A": {"v": 1}}, chunk)          # dict에서 문서 키로 대체하지 않음
        # 승인 릴리스 문서 단위: document_id로만. 같은 정책 키가 섞여 있어도 정책 값으로 조용히 대체하지 않음
        doc_meta = ra.DocumentMetadata({"DOC_A": {"v": 1}})
        self.assertEqual(store.metadata_for_chunk(doc_meta, chunk), {"v": 1})
        confused = ra.DocumentMetadata({"DOC_B": {"v": 3}, "POL_A": {"v": 2}})
        with self.assertRaisesRegex(ValueError, "문서 메타데이터 없음: DOC_A"):
            store.metadata_for_chunk(confused, chunk)
        # 임베딩 입력(vector_index)처럼 없으면 None을 허용하는 경로도 같은 규칙
        self.assertIsNone(store.metadata_for_chunk(confused, chunk, required=False))
        self.assertIsNone(store.metadata_for_chunk({"DOC_A": {"v": 1}}, chunk, required=False))

    def test_build_points_uses_same_strict_lookup(self):
        from database.qdrant import store
        chunk = {"chunk_id": "C1", "point_id": "p1", "document_id": "DOC_A", "policy_id": "POL_A", "text": "t",
                 "source_url": "u", "chunking_version": "v"}
        run = {"embedding_model": "m", "embedding_dim": 1, "embedding_input_rule": "r", "index_run_id": "i"}
        with self.assertRaisesRegex(ValueError, "문서 메타데이터 없음: DOC_A"):
            store.build_points([chunk], [[0.0]], policy_metadata=ra.DocumentMetadata({"POL_A": META}),
                               content_hashes={"DOC_A": "h"}, run_info=run)
        points = store.build_points([chunk], [[0.0]], policy_metadata=ra.DocumentMetadata({"DOC_A": META}),
                                    content_hashes={"DOC_A": "h"}, run_info=run)
        self.assertEqual(points[0]["payload"]["verification_status"], "extracted")


if __name__ == "__main__":
    unittest.main()
