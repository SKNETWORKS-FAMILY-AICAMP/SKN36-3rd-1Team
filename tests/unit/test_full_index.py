"""
전체 색인(database/full_index.py) 단위 테스트 — Qdrant 메모리 모드 + 가짜 임베딩, 서버·Ollama·LLM 없이 실행

실행 방법 (프로젝트 루트에서):
    .venv\\Scripts\\python.exe -m unittest tests/unit/test_full_index.py -v

검사 내용
- 구성 3종의 청크 수(1,081 / 1,117 / 1,141), point id = uuid5(chunk_id), 문서 246·정책 237 포함, C1 저장 응답 재현
- dry-run은 컬렉션을 만들지 않는다 / confirm_write 없이 쓰지 않는다 / 보호 컬렉션·알 수 없는 구성 거부
- 이어 하기(partial → complete), 재실행 시 추가 쓰기 0, 충돌(차원·예상 밖 point·payload 불일치) 시 쓰지 않고 중단
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import full_index as fi  # noqa: E402
from database import handoff_data as hd  # noqa: E402
from database.qdrant import store  # noqa: E402

SMALL_DOCUMENTS = ("DOC_NHUF_003", "DOC_YC_003", "DOC_GOV_048")


def setUpModule():
    # Qdrant 메모리 모드의 "payload index는 효과 없음" 경고만 숨긴다 (서버에서는 인덱스가 만들어진다)
    import logging
    logging.getLogger().setLevel(logging.ERROR)


def fake_embedder(texts):
    """가짜 임베딩 (sample_only): 글자 수로 만든 결정적 1024차원 벡터."""
    return [[((len(text) + index) % 7 + 1) / 10.0 for index in range(fi.EMBEDDING_DIM)] for text in texts]


class TestConfigs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = hd.load_documents()
        cls.metadata = hd.load_policy_metadata()
        cls.plans = {name: fi.plan_full_index(name, documents=cls.documents, policy_metadata=cls.metadata)
                     for name in fi.FULL_INDEX_CONFIGS}

    def test_expected_counts_and_ids(self):
        for name, plan in self.plans.items():
            with self.subTest(config=name):
                self.assertTrue(plan["ready"], plan)
                self.assertEqual(plan["chunks"], fi.FULL_INDEX_CONFIGS[name]["expected_chunks"])
                self.assertTrue(plan["unique_point_ids"])
                self.assertTrue(plan["point_id_is_uuid5_chunk_id"])
                self.assertEqual(plan["restore_mismatch"], 0)
                self.assertEqual((plan["documents"], plan["policies"]), (246, 237))
                self.assertEqual(plan["model"], "qwen3-embedding:0.6b")
                self.assertEqual((plan["dim"], plan["distance"], plan["embedding_input_rule"]),
                                 (1024, "Cosine", "policy_section_body_v2"))
                self.assertIsNone(plan["query_template"])
                self.assertIsNone(plan["collection_state"])          # client 없이 계획만

    def test_c1_reproduced_from_saved_responses(self):
        plan = self.plans["C_B3C1"]
        self.assertEqual(plan["versions"], {"B3": 1010, "C1": 131})
        self.assertEqual({k: v["accepted"] for k, v in plan["c1"].items()},
                         {"DOC_LH_001": 57, "DOC_NHUF_003": 23, "DOC_NHUF_005": 21})

    def test_collection_names_are_distinct_experiment_names(self):
        names = [config["collection"] for config in fi.FULL_INDEX_CONFIGS.values()]
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            store.assert_experiment_collection(name)
            self.assertNotIn(name, fi.PROTECTED_COLLECTIONS)
        self.assertEqual(len({plan["chunk_fingerprint"] for plan in self.plans.values()}), 3)

    def test_unknown_and_protected_configs_are_rejected(self):
        with self.assertRaises(fi.FullIndexError):
            fi.get_config("D_UNKNOWN")
        protected = {"collection": fi.PROTECTED_COLLECTIONS[0], "strategy": "B2", "expected_chunks": 107}
        with mock.patch.dict(fi.FULL_INDEX_CONFIGS, {"X_PROTECTED": protected}):
            with self.assertRaises(fi.FullIndexError):
                fi.get_config("X_PROTECTED")


class TestWriteSafety(unittest.TestCase):
    def setUp(self):
        self.client = store.get_client(location=":memory:")
        all_documents = {d["document_id"]: d for d in hd.load_documents()}
        self.documents = [all_documents[document_id] for document_id in SMALL_DOCUMENTS]
        self.metadata = hd.load_policy_metadata()
        self.chunks, _ = fi.build_config_chunks("B_B3", self.documents)
        self.collection = fi.FULL_INDEX_CONFIGS["B_B3"]["collection"]
        self.hashes = {d["document_id"]: d["content_hash"] for d in self.documents}

    def _execute(self, chunks=None, **kwargs):
        return fi.execute_full_index("B_B3", client=self.client, embedder=fake_embedder, documents=self.documents,
                                     policy_metadata=self.metadata, chunks=chunks or self.chunks, **kwargs)

    def test_dry_run_does_not_create_collection(self):
        state = fi.inspect_collection(self.client, self.collection, self.chunks, self.hashes)
        self.assertEqual(state["state"], "absent")
        self.assertFalse(self.client.collection_exists(self.collection))

    def test_write_requires_confirmation(self):
        with self.assertRaises(fi.FullIndexError):
            self._execute()
        self.assertFalse(self.client.collection_exists(self.collection))

    def test_full_write_verify_and_idempotent_rerun(self):
        result = self._execute(confirm_write=True, run_id="RUN_SAMPLE")
        self.assertEqual(result["state_before"]["state"], "absent")
        self.assertEqual(result["written"], len(self.chunks))
        self.assertTrue(result["verification"]["passed"], result["verification"])
        again = self._execute(confirm_write=True, run_id="RUN_SAMPLE_2")
        self.assertEqual(again["state_before"]["state"], "complete")
        self.assertEqual(again["written"], 0)
        self.assertEqual(store.count_points(self.client, self.collection), len(self.chunks))

    def test_resume_writes_only_missing_points(self):
        half = self.chunks[: len(self.chunks) // 2]
        self._execute(chunks=half, confirm_write=True)               # 중단된 상황 재현: 절반만 적재
        state = fi.inspect_collection(self.client, self.collection, self.chunks, self.hashes)
        self.assertEqual(state["state"], "partial")
        result = self._execute(confirm_write=True)
        self.assertEqual(result["written"], len(self.chunks) - len(half))
        self.assertTrue(result["verification"]["passed"])

    def test_conflicts_stop_without_writing(self):
        # 1) 차원이 다른 같은 이름 컬렉션
        store.ensure_collection(self.client, self.collection, 8)
        with self.assertRaises(fi.FullIndexError):
            self._execute(confirm_write=True)
        self.assertEqual(store.count_points(self.client, self.collection), 0)

    def test_unexpected_point_and_payload_mismatch_are_conflicts(self):
        self._execute(confirm_write=True)
        other_chunks, _ = fi.build_config_chunks("A_B2", self.documents)         # 다른 구성(B2) 청크
        state = fi.inspect_collection(self.client, self.collection, other_chunks, self.hashes)
        self.assertEqual(state["state"], "conflict")                            # 예상 밖 point(B3)가 있음
        changed = [dict(chunk) for chunk in self.chunks]
        changed[0]["char_end"] -= 1
        changed[0]["text"] = changed[0]["text"][:-1]
        state = fi.inspect_collection(self.client, self.collection, changed, self.hashes)
        self.assertEqual(state["state"], "conflict")
        self.assertIn("payload 불일치", " ".join(state["reasons"]))
        count = store.count_points(self.client, self.collection)
        with self.assertRaises(fi.FullIndexError):
            self._execute(chunks=changed, confirm_write=True)
        self.assertEqual(store.count_points(self.client, self.collection), count)


if __name__ == "__main__":
    unittest.main(verbosity=2)
