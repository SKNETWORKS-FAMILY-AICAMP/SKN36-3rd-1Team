"""Issue #91 r1 B2 벡터 재사용 계획(scripts/seed_b2_from_r1_vectors.py) 테스트.

합성 데이터는 모두 sample_only 가상 값이다. 실제 덤프 테스트는 덤프(venv/, Git 제외)가 있을 때만 실행한다.
DB·Ollama에 접속하지 않는다.
"""
import copy
import hashlib
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import handoff_data as hd
from database.chunking import make_point_id
from database.qdrant import store
from scripts import restore_final_v140 as restore
from scripts import seed_b2_from_r1_vectors as seed

RUN = dict(seed.DUMP_RUN)
DIM = seed.DUMP_DIMENSIONS


def sample_chunk(chunk_id, text, *, section="sample_only 섹션", start=0, policy="POL_SAMPLE_1", document="DOC_SAMPLE_1"):
    return {"chunk_id": chunk_id, "point_id": make_point_id(chunk_id), "policy_id": policy, "document_id": document,
            "text": text, "section_title": section, "source_url": "https://example.org/sample_only",
            "char_start": start, "char_end": start + len(text), "page_start": None, "page_end": None,
            "chunking_version": "B2_sample_only"}


def sample_meta(name="sample_only 정책", category="sample_category"):
    return {"policy_name": name, "category": category, "region": "전국", "life_stages": ["sample_stage"],
            "target_groups": ["sample_group"], "eligibility_attributes": {}, "verification_status": "sample_only"}


def sample_vector(seed_value):
    return [math.sin(seed_value + index) / 32 for index in range(DIM)]


def dump_record(chunk, meta, content_hash, vector, run=RUN):
    return {"payload": store.build_point_payload(chunk, policy_meta=meta, content_hash=content_hash, run_info=run),
            "vector": vector}


class SampleData:
    """기준 덤프(old)와 최신 청크(new) — 동일 / 메타데이터 변경 / 핵심(해시) 변경 / 본문 변경 / 신규."""

    def __init__(self):
        old_hash, new_hash = "a" * 64, "b" * 64
        self.old_meta = {"POL_SAMPLE_1": sample_meta()}
        self.metadata = {"POL_SAMPLE_1": sample_meta(category="sample_category_new")}
        same = sample_chunk("CHK_B2_DOC_SAMPLE_1_C001", "sample_only 본문 하나")
        moved = sample_chunk("CHK_B2_DOC_SAMPLE_1_C002", "sample_only 본문 둘", start=20)
        edited = sample_chunk("CHK_B2_DOC_SAMPLE_1_C003", "sample_only 본문 셋", start=40)
        self.old_chunks = [same, moved, edited]
        moved_new = dict(moved, char_start=18, char_end=18 + len(moved["text"]))
        edited_new = dict(edited, text="sample_only 본문 셋 (수정)", char_end=40 + 20)
        added = sample_chunk("CHK_B2_DOC_SAMPLE_1_C004", "sample_only 신규 본문", start=70)
        self.chunks = [same, moved_new, edited_new, added]
        self.records = {chunk["point_id"]: dump_record(chunk, self.old_meta["POL_SAMPLE_1"], old_hash,
                                                       sample_vector(index))
                        for index, chunk in enumerate(self.old_chunks)}
        self.hashes = {"DOC_SAMPLE_1": new_hash}
        self.expected = {"chunks": 4, "reuse": 2, "embed": {edited["chunk_id"], added["chunk_id"]},
                         "core_refresh": {same["chunk_id"], moved["chunk_id"]}}


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.data = SampleData()

    def plan(self):
        return seed.classify(self.data.records, self.data.chunks, self.data.metadata, self.data.hashes)

    def test_reuse_and_embed_classification(self):
        plan = self.plan()
        reuse = {item["chunk_id"]: item for item in plan["reuse"]}
        self.assertEqual(set(reuse), {"CHK_B2_DOC_SAMPLE_1_C001", "CHK_B2_DOC_SAMPLE_1_C002"})
        self.assertEqual(reuse["CHK_B2_DOC_SAMPLE_1_C001"]["changed_fields"], ["category", "content_hash"])
        self.assertEqual(reuse["CHK_B2_DOC_SAMPLE_1_C002"]["changed_fields"],
                         ["category", "char_end", "char_start", "content_hash"])
        embed = {item["chunk_id"]: item for item in plan["embed"]}
        self.assertEqual(embed["CHK_B2_DOC_SAMPLE_1_C003"]["reason"], "embedding_input_changed")
        self.assertEqual(embed["CHK_B2_DOC_SAMPLE_1_C003"]["changed_parts"], ["text"])
        self.assertEqual(embed["CHK_B2_DOC_SAMPLE_1_C004"]["reason"], "new_point")
        self.assertEqual(plan["remove"], [])
        seed.validate_plan(plan, self.data.records, self.data.expected)

    def test_metadata_only_change_is_metadata_refresh(self):
        self.data.hashes = {"DOC_SAMPLE_1": "a" * 64}
        self.data.chunks = self.data.old_chunks[:1]
        item = self.plan()["reuse"][0]
        self.assertEqual((item["kind"], item["changed_fields"]), ("metadata_refresh", ["category"]))

    def test_reused_payload_is_latest_expected_payload_with_dump_run(self):
        item = next(i for i in self.plan()["reuse"] if i["chunk_id"] == "CHK_B2_DOC_SAMPLE_1_C002")
        chunk = self.data.chunks[1]
        expected = store.build_point_payload(chunk, policy_meta=self.data.metadata["POL_SAMPLE_1"],
                                             content_hash="b" * 64, run_info=RUN)
        self.assertEqual(item["payload"], expected)
        self.assertEqual(item["payload"]["index_run_id"], RUN["index_run_id"])
        self.assertEqual(item["kind"], "core_refresh")

    def test_reused_vectors_are_unchanged_and_inputs_not_mutated(self):
        before = copy.deepcopy((self.data.records, self.data.chunks, self.data.metadata))
        plan = self.plan()
        for item in plan["reuse"]:
            self.assertEqual(item["vector"], before[0][item["point_id"]]["vector"])
        self.assertEqual((self.data.records, self.data.chunks, self.data.metadata), before)

    def test_section_title_or_policy_name_change_requires_embedding(self):
        changed_section = [dict(self.data.old_chunks[0], section_title="sample_only 다른 섹션")]
        plan = seed.classify(self.data.records, changed_section, self.data.old_meta, self.data.hashes)
        self.assertEqual(plan["embed"][0]["changed_parts"], ["section_title"])
        renamed = {"POL_SAMPLE_1": sample_meta(name="sample_only 바뀐 정책명")}
        plan = seed.classify(self.data.records, self.data.old_chunks[:1], renamed, self.data.hashes)
        self.assertEqual(plan["embed"][0]["changed_parts"], ["policy_name"])

    def test_dump_point_missing_from_latest_chunks_is_reported_and_rejected(self):
        plan = seed.classify(self.data.records, self.data.chunks[:1], self.data.metadata, self.data.hashes)
        self.assertEqual(len(plan["remove"]), 2)
        with self.assertRaisesRegex(seed.SeedPlanError, "제거 필요"):
            seed.validate_plan(plan, self.data.records, self.data.expected)

    def test_validate_rejects_unexpected_classification_or_changed_vector(self):
        plan = self.plan()
        with self.assertRaisesRegex(seed.SeedPlanError, "새 임베딩 대상"):
            seed.validate_plan(plan, self.data.records, dict(self.data.expected, embed={"CHK_B2_DOC_SAMPLE_1_C004"}))
        with self.assertRaisesRegex(seed.SeedPlanError, "분류 건수"):
            seed.validate_plan(plan, self.data.records, dict(self.data.expected, reuse=3))
        with self.assertRaisesRegex(seed.SeedPlanError, "핵심 payload"):
            seed.validate_plan(plan, self.data.records, dict(self.data.expected, core_refresh=set()))
        plan["reuse"][0]["vector"] = list(plan["reuse"][0]["vector"])
        plan["reuse"][0]["vector"][0] += 1e-7
        with self.assertRaisesRegex(seed.SeedPlanError, "재사용 벡터가 덤프와 다릅니다"):
            seed.validate_plan(plan, self.data.records, self.data.expected)

    def test_point_id_collision_with_other_chunk_is_rejected(self):
        other = dict(self.data.chunks[0], chunk_id="CHK_B2_DOC_SAMPLE_1_C009")
        with self.assertRaisesRegex(seed.SeedPlanError, "Point ID 규칙"):
            seed.classify(self.data.records, [other], self.data.metadata, self.data.hashes)


class DumpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.rows = [{"id": make_point_id(f"CHK_B2_DOC_SAMPLE_1_C00{i}"),
                      "vector": sample_vector(i),
                      "payload": {"point_id": make_point_id(f"CHK_B2_DOC_SAMPLE_1_C00{i}"), **RUN}}
                     for i in range(3)]

    def write(self, rows):
        path = Path(self.temp.name) / "sample_only_dump.json"
        path.write_text(json.dumps(rows), encoding="utf-8")
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def load(self, rows, **kwargs):
        path, sha = self.write(rows)
        return seed.load_dump(path, expected_sha256=kwargs.pop("sha", sha), expected_points=kwargs.pop("points", 3))

    def test_valid_dump_loads_without_modifying_file(self):
        path, sha = self.write(self.rows)
        records = seed.load_dump(path, expected_sha256=sha, expected_points=3)
        self.assertEqual(len(records), 3)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), sha)

    def test_sha256_mismatch_is_rejected(self):
        with self.assertRaisesRegex(seed.SeedPlanError, "SHA-256 불일치"):
            self.load(self.rows, sha="0" * 64)

    def test_duplicate_and_missing_point_ids_are_rejected(self):
        with self.assertRaisesRegex(seed.SeedPlanError, "중복 Point ID"):
            self.load(self.rows + [copy.deepcopy(self.rows[0])], points=4)
        with self.assertRaisesRegex(seed.SeedPlanError, "Point 수 2"):
            self.load(self.rows[:2])

    def test_invalid_id_or_payload_point_id_is_rejected(self):
        rows = copy.deepcopy(self.rows)
        rows[0]["id"] = "sample_only"
        with self.assertRaisesRegex(seed.SeedPlanError, "Point ID 형식"):
            self.load(rows)
        rows = copy.deepcopy(self.rows)
        rows[0]["payload"]["point_id"] = rows[1]["id"]
        with self.assertRaisesRegex(seed.SeedPlanError, "payload.point_id"):
            self.load(rows)

    def test_vector_dimension_and_finite_values_are_enforced(self):
        for bad in (sample_vector(0)[:-1], sample_vector(0)[:-1] + [float("nan")],
                    sample_vector(0)[:-1] + [float("inf")], sample_vector(0)[:-1] + [True], None):
            rows = copy.deepcopy(self.rows)
            rows[0]["vector"] = bad
            with self.subTest(bad=type(bad).__name__ if bad is None else bad[-1]):
                with self.assertRaisesRegex(seed.SeedPlanError, "벡터 차원·유한값"):
                    self.load(rows)

    def test_vector_digest_mismatch_is_rejected(self):
        records = self.load(self.rows)
        self.assertEqual(seed.check_vector_digest(records, restore.vector_digest(records)),
                         restore.vector_digest(records))
        with self.assertRaisesRegex(seed.SeedPlanError, "vector_digest 불일치"):
            seed.check_vector_digest(records, "0" * 64)

    def test_dump_run_model_rule_and_run_id_are_enforced(self):
        records = self.load(self.rows)
        seed.check_dump_run(records)
        for field, value in (("embedding_model", "sample_only-model"), ("embedding_input_rule", "body_only"),
                             ("embedding_dim", 2560), ("index_run_id", "RUN_SAMPLE_ONLY")):
            broken = copy.deepcopy(records)
            next(iter(broken.values()))["payload"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(seed.SeedPlanError, "실행 정보가 다른 Point 1개"):
                seed.check_dump_run(broken)
        with self.assertRaisesRegex(seed.SeedPlanError, "현재 A_B2_V140 구성과 다릅니다"):
            seed.check_dump_run(records, dict(RUN, embedding_input_rule="policy_section_body"))

    def test_r1_record_digest_is_checked(self):
        seed.check_r1_record()
        record = json.loads(seed.R1_RECORD.read_text(encoding="utf-8"))
        record["verify_only"]["vector_digest"] = "0" * 64
        path = Path(self.temp.name) / "sample_only_record.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(seed.SeedPlanError, "verify_only"):
            seed.check_r1_record(path)


class ReleaseGuardTests(unittest.TestCase):
    def test_wrong_dataset_version_is_rejected(self):
        with patch.object(hd, "dataset_version", return_value="1.3.2"):
            with self.assertRaisesRegex(seed.SeedPlanError, "데이터셋 버전"):
                seed.load_release()

    def test_wrong_manifest_is_rejected(self):
        with patch.object(hd, "dataset_version", return_value=seed.DATASET_VERSION), \
                patch.object(seed.fi, "manifest_sha256", return_value="0" * 64):
            with self.assertRaisesRegex(seed.SeedPlanError, "manifest SHA-256"):
                seed.load_release()

    def test_script_has_no_database_write_or_embedding_path(self):
        source = Path(seed.__file__).read_text(encoding="utf-8")
        for call in ("upsert", "set_payload", "delete", "create_collection", "ensure_collection", "get_client",
                     "QdrantClient", "GraphDatabase", "make_ollama_embedder", "execute_full_index", "requests."):
            self.assertNotIn(call + "(", source.replace("store.REQUIRED_PAYLOAD_FIELDS", ""), call)


@unittest.skipUnless(seed.DEFAULT_DUMP.is_file(), "NOT_RUN: 기존 B2 덤프(venv/issue31_20261109)가 이 PC에 없음")
class RealDumpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if hd.dataset_version() != seed.DATASET_VERSION:
            raise unittest.SkipTest("NOT_RUN: v1.4.0-candidate 데이터셋이 아님")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from r2_release_gate import SKIP_REASON, r2_release_is_current
        if not r2_release_is_current():            # #105: r2 기대값은 r2 릴리스에만 적용
            raise unittest.SkipTest(SKIP_REASON)

    def test_real_dump_plan_without_db_or_embedding(self):
        before = restore.file_sha(seed.DEFAULT_DUMP)
        blocked = AssertionError("DB·임베딩 호출 금지")
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(store, "get_client", side_effect=blocked), \
                patch.object(store, "upsert_points", side_effect=blocked), \
                patch.object(store, "ensure_collection", side_effect=blocked), \
                patch.object(seed.em, "make_ollama_embedder", side_effect=blocked), \
                patch("requests.post", side_effect=blocked), \
                patch("qdrant_client.QdrantClient", side_effect=blocked):
            report = Path(folder) / "plan.json"
            self.assertEqual(seed.main(["--report", str(report)]), 0)
            result = json.loads(report.read_text(encoding="utf-8"))
            with self.assertRaises(SystemExit):
                seed.main(["--report", str(report)])        # 기존 보고서 덮어쓰기 거부
        self.assertEqual(restore.file_sha(seed.DEFAULT_DUMP), before)
        self.assertEqual((result["status"], result["db_writes"], result["embedding_calls"]), ("DRY_RUN_PASS", 0, 0))
        self.assertEqual(result["dump"]["vector_digest"], seed.R1_VECTOR_DIGEST)
        self.assertEqual(result["counts"]["reuse"], 1135)
        self.assertEqual(result["counts"]["reuse_by_kind"],
                         {"core_refresh": 2, "identical": 1000, "metadata_refresh": 133})
        self.assertEqual({item["chunk_id"] for item in result["embed"]}, seed.EXPECTED_EMBED_CHUNK_IDS)
        self.assertIn({"point_id": "7fa02bbf-4c6c-5d8f-b314-6f94d9c34b14",
                       "chunk_id": "CHK_B2_DOC_BOK_031_MOE_2026_C001", "reason": "new_point"}, result["embed"])
        self.assertEqual({item["chunk_id"] for item in result["core_refresh"]}, seed.EXPECTED_CORE_REFRESH_CHUNK_IDS)
        self.assertEqual(result["remove"], [])


if __name__ == "__main__":
    unittest.main()
