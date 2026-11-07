"""
Qdrant 실험 컬렉션 스모크 테스트 — 실제 서버에 "읽기 전용"으로 접속한다.

실행 방법 (프로젝트 루트, .venv):
    set RUN_QDRANT_SMOKE=1                      (PowerShell: $env:RUN_QDRANT_SMOKE="1")
    .venv\\Scripts\\python.exe -m unittest tests/integration/test_qdrant_smoke.py -v

결과 구분
- RUN_QDRANT_SMOKE가 1이 아니면     → 모두 skip, 사유 "NOT_RUN"           (서버에 접속하지 않음)
- 서버·컬렉션이 아직 준비되지 않았으면 → 모두 skip, 사유 "DATA_NOT_READY: …" (실패가 아니라 미실행)
- 준비되어 있는데 값이 기대와 다르면   → FAIL                                (실제 문제)

이 테스트는 컬렉션을 만들거나, point를 쓰거나, 지우지 않는다.
.env의 비밀값(API 키 등)은 출력하지 않는다.
"""

import os
import sys
import unittest
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 실험 설정 (2026-11-05 2차 실험 기준). 실험 설정이 바뀌면 이 값과 기대 point 수를 함께 고친다.
EXPERIMENT = {
    "collection": "exp_b2_qwen3emb06b_v1",
    "vector_size": 1024,
    "distance": "Cosine",
    "chunking_version": "B2",
    "embedding_model": "qwen3-embedding:0.6b",
    "embedding_input_rule": "policy_section_body_v2",
    # 문서별 기대 point 수 = B2 청크 수 (1차 34개 + 2차 DOC_LH_001 73개 = 107개)
    "expected_points_per_document": {
        "DOC_NHUF_003": 14, "DOC_BOK_001": 7, "DOC_YC_003": 3, "DOC_GOV_006": 10, "DOC_LH_001": 73,
    },
}
EXPECTED_TOTAL_POINTS = sum(EXPERIMENT["expected_points_per_document"].values())   # 107
RUN_SMOKE = os.environ.get("RUN_QDRANT_SMOKE") == "1"


@unittest.skipUnless(RUN_SMOKE, "NOT_RUN: RUN_QDRANT_SMOKE=1 일 때만 실제 서버에 접속한다")
class TestQdrantExperimentCollectionSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        warnings.filterwarnings("ignore", message="Api key is used with an insecure connection.")
        from database import handoff_data as hd
        from database.qdrant import store

        cls.store = store
        cls.collection = EXPERIMENT["collection"]
        try:
            cls.client = store.get_client()
            names = [item.name for item in cls.client.get_collections().collections]
        except store.ConnectionSettingsError as error:
            raise unittest.SkipTest(f"DATA_NOT_READY: 접속 설정 없음 ({error})")
        except Exception as error:   # 서버가 꺼져 있음 등
            raise unittest.SkipTest(f"DATA_NOT_READY: Qdrant 서버에 접속할 수 없음 ({type(error).__name__})")
        if cls.collection not in names:
            raise unittest.SkipTest(f"DATA_NOT_READY: 컬렉션 {cls.collection} 없음")

        records, offset = [], None
        while True:   # 읽기 전용 전체 조회 (scroll)
            page, offset = cls.client.scroll(cls.collection, limit=256, offset=offset,
                                             with_payload=True, with_vectors=True)
            records.extend(page)
            if offset is None:
                break
        cls.records = records
        cls.documents = {d["document_id"]: d for d in hd.load_documents()}

    def test_collection_vector_config(self):
        params = self.client.get_collection(self.collection).config.params.vectors
        self.assertEqual(params.size, EXPERIMENT["vector_size"])
        self.assertEqual(str(params.distance), EXPERIMENT["distance"])

    def test_point_count_total_and_per_document(self):
        self.assertEqual(self.store.count_points(self.client, self.collection), EXPECTED_TOTAL_POINTS)
        for document_id, expected in EXPERIMENT["expected_points_per_document"].items():
            with self.subTest(document=document_id):
                self.assertEqual(self.store.count_points(self.client, self.collection, {"document_id": document_id}),
                                 expected)

    def test_point_ids_are_uuid5_of_chunk_id_and_unique(self):
        from database.chunking import make_point_id
        ids = [str(record.id) for record in self.records]
        self.assertEqual(len(ids), len(set(ids)))
        for record in self.records:
            self.assertEqual(str(record.id), make_point_id(record.payload["chunk_id"]))

    def test_payload_required_fields_and_experiment_settings(self):
        for record in self.records:
            payload = record.payload
            with self.subTest(chunk=payload.get("chunk_id")):
                self.assertEqual(set(self.store.REQUIRED_PAYLOAD_FIELDS) - set(payload), set())
                self.assertEqual(payload["chunking_version"], EXPERIMENT["chunking_version"])
                self.assertEqual(payload["embedding_model"], EXPERIMENT["embedding_model"])
                self.assertEqual(payload["embedding_input_rule"], EXPERIMENT["embedding_input_rule"])
                self.assertEqual(payload["embedding_dim"], EXPERIMENT["vector_size"])
                self.assertEqual(len(record.vector), EXPERIMENT["vector_size"])

    def test_payload_text_matches_original_and_hash(self):
        for record in self.records:
            payload = record.payload
            document = self.documents[payload["document_id"]]
            with self.subTest(chunk=payload["chunk_id"]):
                self.assertEqual(document["text"][payload["char_start"]:payload["char_end"]], payload["text"])
                self.assertEqual(payload["content_hash"], document["content_hash"])
                self.assertEqual(payload["policy_id"], document["policy_id"])

    def test_pdf_page_metadata_follows_page_markers(self):
        from database.chunking import find_page_markers
        pdf_records = [r for r in self.records if r.payload.get("source_type") == "pdf"]
        if not pdf_records:
            self.skipTest("DATA_NOT_READY: PDF point 없음")
        for record in pdf_records:
            payload = record.payload
            markers = find_page_markers(self.documents[payload["document_id"]]["text"])
            expected = [page for start, _end, page in markers if start <= payload["char_start"]]
            with self.subTest(chunk=payload["chunk_id"]):
                self.assertEqual(payload["page_start"], payload["page_end"])
                self.assertEqual(payload["page_start"], expected[-1] if expected else None)

    def test_search_return_format_with_stored_vector(self):
        # Ollama 없이 검색 형식을 확인한다: 저장된 point의 벡터로 검색하면 그 point가 1위여야 한다.
        target = self.records[0]
        hits = self.store.search_chunks(self.client, self.collection, list(target.vector), k=5)
        self.assertEqual(len(hits), 5)
        self.assertEqual(hits[0]["point_id"], str(target.id))
        for hit in hits:
            self.assertEqual(set(hit), {"rank", "score", "point_id", "chunk_id", "policy_id", "document_id", "payload"})
            self.assertIsInstance(hit["score"], float)
        self.assertEqual([hit["rank"] for hit in hits], [1, 2, 3, 4, 5])
        self.assertEqual(hits, sorted(hits, key=lambda hit: -hit["score"]))

    def test_document_id_filter(self):
        target = self.records[0]
        document_id = target.payload["document_id"]
        hits = self.store.search_chunks(self.client, self.collection, list(target.vector), k=10,
                                        filters={"document_id": document_id})
        self.assertTrue(hits)
        self.assertEqual({hit["document_id"] for hit in hits}, {document_id})

    def test_protected_collection_is_refused(self):
        with self.assertRaises(self.store.CollectionGuardError):
            self.store.count_points(self.client, "lifepolicy_chunks")


if __name__ == "__main__":
    unittest.main(verbosity=2)
