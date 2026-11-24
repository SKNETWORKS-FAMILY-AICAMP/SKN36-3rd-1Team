"""
Issue #81 A안: 사람이 검토한 노이즈 청크 27건만 적재 대상에서 뺀다 (DB·임베딩·네트워크 없음).

실행 (프로젝트 루트에서):
    python -m pytest tests/unit/test_noise_chunk_exclusion.py -v

무엇을 확인하는가?
1) 데이터 제외: v1.4.0 600 → 573, v1.3.2 557 → 530, 제외 ID = 검토 목록 27건, 정상 청크 불변
2) 안전성: 본문 변경·누락·중복·검토하지 않은 데이터셋이면 조용히 빼지 않고 멈춘다, 원본 파일 불변
3) 회귀: A 전략 적재 경로 적용·재실행 시 되살아나지 않음, B2 1,139개·검색 프로필·복원 600 계약 유지
두 데이터셋 파일을 경로로 직접 읽으므로 LIFEPOLICY_DATASET_DIR 설정과 무관하게 같은 결과가 나온다.
"""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = Path(__file__).resolve().parent          # test_vector_store의 가짜 임베딩·실행 정보 재사용
for path in (PROJECT_ROOT, TESTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from database import handoff_data as hd  # noqa: E402
from database import vector_index as vi  # noqa: E402
from database.chunking import make_point_id  # noqa: E402

V140_DIR = hd.DEFAULT_HANDOFF_DIR            # data/releases/v1.4.0-candidate (승인 릴리스)
V132_DIR = hd.LEGACY_HANDOFF_DIR             # data/handoff (v1.3.2)
DATASETS = {
    hd.RELEASE_V140_VERSION: (V140_DIR, 663, 636),
    "1.3.2": (V132_DIR, 557, 530),
}
NOISE_IDS = sorted(hd.REVIEWED_NOISE_CHUNKS)
QDRANT_CLIENT_AVAILABLE = importlib.util.find_spec("qdrant_client") is not None


def release_chunks(directory: Path) -> list[dict]:
    return hd.read_jsonl(directory / "qdrant" / "payload_chunks.jsonl")


def directory_digest(directory: Path) -> dict[str, str]:
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob("*")) if path.is_file()}


class TestReviewedListDefinition(unittest.TestCase):
    def test_hash_table_covers_exactly_the_reviewed_list(self):
        self.assertEqual(len(hd.REVIEWED_NOISE_CHUNKS), 27)
        self.assertEqual(set(hd.REVIEWED_NOISE_TEXT_SHA256), set(hd.REVIEWED_NOISE_CHUNKS))

    def test_short_threshold_is_only_for_candidates(self):
        # 새 교육부 표는 짧아 후보에 잡히지만 날짜 근거이므로 적재에 남는다.
        self.assertEqual(hd.SHORT_CHUNK_THRESHOLD, 120)
        for version, (directory, _raw, _selected) in DATASETS.items():
            with self.subTest(version=version):
                chunks = release_chunks(directory)
                expected = set(NOISE_IDS)
                if version == hd.RELEASE_V140_VERSION:
                    # 서울 청년수당 소개 탭의 문의 전화 2줄도 짧지만 연락처라 적재에 남는다(#104 검토).
                    kept_ids = ("DOC_BOK_031_MOE_2026_P305_C01", "DOC_STG_SEOUL_001_INTRO_2026_C04")
                    expected.update(kept_ids)
                    selected, _ = hd.select_chunks_for_embedding(chunks, version=version)
                    for kept_id in kept_ids:
                        self.assertIn(kept_id, {chunk["chunk_id"] for chunk in selected})
                self.assertEqual(set(hd.find_short_chunk_candidates(chunks)), expected)


class TestDataExclusion(unittest.TestCase):
    """1~7: 데이터셋별 원본·적재 대상 건수와 정상 청크 불변."""

    def test_raw_and_selected_counts_per_dataset(self):
        for version, (directory, raw_count, selected_count) in DATASETS.items():
            chunks = release_chunks(directory)
            selected, excluded = hd.select_chunks_for_embedding(chunks, version=version)
            with self.subTest(version=version):
                self.assertEqual(len(chunks), raw_count)                 # 원본 그대로
                self.assertEqual(len(selected), selected_count)
                self.assertEqual(sorted(c["chunk_id"] for c in excluded), NOISE_IDS)
                self.assertEqual(len(release_chunks(directory)), raw_count)   # 파일 원본 건수 유지

    def test_normal_chunks_keep_order_id_text_and_point_id(self):
        for version, (directory, _raw, _selected) in DATASETS.items():
            chunks = release_chunks(directory)
            before = json.dumps(chunks, ensure_ascii=False, sort_keys=True)
            selected, _excluded = hd.select_chunks_for_embedding(chunks, version=version)
            expected = [chunk for chunk in chunks if chunk["chunk_id"] not in hd.REVIEWED_NOISE_CHUNKS]
            with self.subTest(version=version):
                self.assertEqual(selected, expected)                     # 순서·ID·본문·메타데이터 그대로
                self.assertTrue(all(c["point_id"] == make_point_id(c["chunk_id"]) for c in selected))
                self.assertEqual(json.dumps(chunks, ensure_ascii=False, sort_keys=True), before)  # 입력 불변

    def test_unreviewed_short_chunk_is_kept(self):
        chunks = release_chunks(V140_DIR)
        sample = {**chunks[0], "chunk_id": "DOC_SAMPLE_ONLY_001_C01", "text": "짧은 정상 문장 (sample_only)"}
        selected, excluded = hd.select_chunks_for_embedding(chunks + [sample], version=hd.RELEASE_V140_VERSION)
        self.assertIn("DOC_SAMPLE_ONLY_001_C01", hd.find_short_chunk_candidates(chunks + [sample]))
        self.assertIn(sample, selected)                                  # 짧아도 검토하지 않았으면 남긴다
        self.assertEqual(len(excluded), 27)

    def test_exclude_noise_false_returns_everything(self):
        chunks = release_chunks(V140_DIR)
        selected, excluded = hd.select_chunks_for_embedding(chunks, exclude_noise=False, version="unknown-dataset")
        self.assertEqual((selected, excluded), (chunks, []))


class TestSafetyGuards(unittest.TestCase):
    """8~12: 검토 당시와 다른 데이터에는 과거 검토 결과를 조용히 적용하지 않는다."""

    def setUp(self):
        self.chunks = release_chunks(V140_DIR)
        self.version = hd.RELEASE_V140_VERSION

    def test_changed_reviewed_text_is_rejected(self):
        index = next(i for i, c in enumerate(self.chunks) if c["chunk_id"] == "DOC_YC_002_C03")
        changed = list(self.chunks)
        changed[index] = {**changed[index], "text": changed[index]["text"] + " 자격 조건 추가 (sample_only)"}
        with self.assertRaisesRegex(hd.ReviewedNoiseError, "DOC_YC_002_C03"):
            hd.select_chunks_for_embedding(changed, version=self.version)

    def test_missing_reviewed_chunk_is_rejected(self):
        partial = [c for c in self.chunks if c["chunk_id"] != "DOC_NHUF_001_C02"]
        with self.assertRaisesRegex(hd.ReviewedNoiseError, "DOC_NHUF_001_C02"):
            hd.select_chunks_for_embedding(partial, version=self.version)

    def test_duplicated_chunk_id_is_rejected(self):
        for duplicated_id in ("DOC_BOK_015_C03", self.chunks[0]["chunk_id"]):     # 검토 대상·정상 청크 각각
            duplicate = next(c for c in self.chunks if c["chunk_id"] == duplicated_id)
            with self.subTest(chunk_id=duplicated_id):
                with self.assertRaisesRegex(hd.ReviewedNoiseError, "중복"):
                    hd.select_chunks_for_embedding(self.chunks + [dict(duplicate)], version=self.version)

    def test_repeated_calls_give_same_result(self):
        first = hd.select_chunks_for_embedding(self.chunks, version=self.version)
        second = hd.select_chunks_for_embedding(self.chunks, version=self.version)
        self.assertEqual(first, second)

    def test_unreviewed_dataset_is_not_trusted(self):
        for version in ("issue21-v1.4.0-review", "1.5.0", ""):
            with self.subTest(version=version):
                with self.assertRaisesRegex(hd.ReviewedNoiseError, "확인하지 않은 데이터셋"):
                    hd.select_chunks_for_embedding(self.chunks, version=version)

    def test_default_version_is_current_dataset(self):
        # version을 생략하면 현재 데이터셋(manifest_version)을 쓴다. 현재 데이터셋의 원본으로 573/530이 나온다.
        selected, excluded = hd.select_chunks_for_embedding(hd.load_payload_chunks())
        self.assertEqual(len(selected), hd.EXPECTED_CHUNK_COUNT - 27)
        self.assertEqual(len(excluded), 27)

    def test_release_files_and_manifest_unchanged(self):
        before = {name: directory_digest(directory) for name, (directory, _r, _s) in DATASETS.items()}
        for version, (directory, _raw, _selected) in DATASETS.items():
            hd.select_chunks_for_embedding(release_chunks(directory), version=version)
        vi.prepare_chunks(hd.load_documents(), "A", hd.load_payload_chunks())
        after = {name: directory_digest(directory) for name, (directory, _r, _s) in DATASETS.items()}
        self.assertEqual(before, after)
        self.assertEqual([r for r in hd.verify_manifest_checksums() if not r["matched"]], [])


class TestAStrategyPath(unittest.TestCase):
    """13: A 전략(vector_index) 적재 경로에 적용."""

    @classmethod
    def setUpClass(cls):
        cls.documents = hd.load_documents()
        cls.payload_chunks = hd.load_payload_chunks()

    def test_prepare_chunks_a_excludes_reviewed_noise_only(self):
        chunks = vi.prepare_chunks(self.documents, "A", self.payload_chunks)
        ids = [c["chunk_id"] for c in chunks]
        self.assertFalse(set(ids) & set(hd.REVIEWED_NOISE_CHUNKS))
        self.assertEqual(len(chunks), hd.EXPECTED_CHUNK_COUNT - 27)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(c["point_id"] == make_point_id(c["chunk_id"]) for c in chunks))

    def test_prepare_chunks_a_rejects_partial_source(self):
        partial = [c for c in self.payload_chunks if c["chunk_id"] != "DOC_BOK_015_C03"]
        with self.assertRaises(hd.ReviewedNoiseError):
            vi.prepare_chunks(self.documents, "A", partial)

    def test_other_strategies_ignore_release_chunks(self):
        # B1/B2/B3는 문서 본문을 직접 청킹한다. 노이즈 제외와 무관하고 검토 ID(DOC_…_Cnn 형식)가 나오지 않는다.
        noise_documents = vi.select_documents(self.documents, ["DOC_BOK_015", "DOC_NHUF_001"])
        for strategy in ("B1", "B2", "B3"):
            chunks = vi.prepare_chunks(noise_documents, strategy, self.payload_chunks)
            with self.subTest(strategy=strategy):
                self.assertTrue(chunks)
                self.assertFalse({c["chunk_id"] for c in chunks} & set(hd.REVIEWED_NOISE_CHUNKS))

    @unittest.skipUnless(QDRANT_CLIENT_AVAILABLE, "qdrant-client 미설치 → NOT_TESTED")
    def test_rerun_does_not_bring_back_excluded_points(self):
        # Qdrant 메모리 모드(프로세스 안, 네트워크 없음) + 가짜 임베딩. 같은 적재를 두 번 해도 27건의 Point는 없다.
        from database.qdrant import store
        from test_vector_store import TEST_COLLECTION, fake_embedder, fake_run_info

        client = store.get_client(location=":memory:")
        document_ids = ["DOC_BOK_015", "DOC_NHUF_001", "DOC_YC_002"]
        report = vi.run_minimal_index(document_ids, strategy="A", collection=TEST_COLLECTION, client=client,
                                      embedder=fake_embedder(), run_info=fake_run_info(),
                                      documents=self.documents, payload_chunks=self.payload_chunks)
        stored = store.list_point_ids(client, TEST_COLLECTION, {"document_id": document_ids})
        excluded_points = {make_point_id(chunk_id) for chunk_id in hd.REVIEWED_NOISE_CHUNKS}
        expected = [c for c in self.payload_chunks
                    if c["document_id"] in document_ids and c["chunk_id"] not in hd.REVIEWED_NOISE_CHUNKS]
        self.assertFalse(set(stored) & excluded_points)
        self.assertEqual(report["points_after_first"], len(expected))
        self.assertEqual(report["points_after_second"], len(expected))
        self.assertTrue(report["id_set_unchanged"])
        self.assertFalse(report["duplicates_found"])


V140_SERVICE_CHECK = r"""
import json
import database.handoff_data as hd
from database import full_index as fi
from database import hybrid_search as hs
docs = hd.load_documents(verify_hash=True)
b2, _info = fi.build_config_chunks("A_B2_V140", docs)
profile = hs.SEARCH_PROFILES[hs.default_profile()]
print(json.dumps({"version": hd.dataset_version(), "b2": len(b2),
                  "b2_has_reviewed_ids": bool({c["chunk_id"] for c in b2} & set(hd.REVIEWED_NOISE_CHUNKS)),
                  "default_profile": hs.default_profile(), "index_config": profile["index_config"],
                  "collection": fi.FULL_INDEX_CONFIGS[profile["index_config"]]["collection"],
                  "raw_chunks": len(hd.load_payload_chunks())}))
"""


class TestServicePathsUnchanged(unittest.TestCase):
    """14·15·18: 서비스 B2(1,139), 검색 프로필, 복원 검증 600 계약은 그대로 (v1.4.0 하위 프로세스)."""

    @classmethod
    def setUpClass(cls):
        env = {**os.environ, hd.DATASET_DIR_ENV: str(V140_DIR), "PYTHONIOENCODING": "utf-8",
               "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run([sys.executable, "-c", V140_SERVICE_CHECK], cwd=PROJECT_ROOT, env=env,
                                capture_output=True, text=True, encoding="utf-8", timeout=600)
        if result.returncode != 0:
            raise AssertionError(result.stderr[-3000:])
        cls.out = json.loads(result.stdout.strip().splitlines()[-1])

    def test_b2_service_chunks_unchanged(self):
        self.assertEqual(self.out["version"], hd.RELEASE_V140_VERSION)
        self.assertEqual(self.out["b2"], 1233)
        self.assertFalse(self.out["b2_has_reviewed_ids"])

    def test_search_profile_unchanged(self):
        self.assertEqual((self.out["default_profile"], self.out["index_config"], self.out["collection"]),
                         ("b2_06b_v140", "A_B2_V140", "exp_b2_qwen3emb06b_v140_v1"))

    def test_restore_contract_keeps_600_raw_chunks(self):
        spec = importlib.util.spec_from_file_location("restore_final_v140",
                                                      PROJECT_ROOT / "scripts" / "restore_final_v140.py")
        restore = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(restore)
        self.assertEqual(restore.EXPECTED[2], 663)
        self.assertEqual(self.out["raw_chunks"], restore.EXPECTED[2])


if __name__ == "__main__":
    unittest.main()
