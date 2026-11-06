"""
Retrieval 평가 실행기(database/retrieval_run.py) 단위 테스트 — Qdrant 메모리 모드, 실제 서버·Ollama 없이 실행

실행 방법 (프로젝트 루트에서):
    .venv\\Scripts\\python.exe -m unittest tests/unit/test_retrieval_run.py -v

검사 내용
- 고정 질문(retrieval_eval_v0) 해시 검사: 파일이 바뀌면 EvalIntegrityError
- 질문 임베딩은 한 번만, 같은 벡터를 모든 컬렉션에 재사용
- 검색 결과 기록에 청크 본문이 없고 위치·ID만 있음
- Recall@1/3/5, MRR@5(5위 밖 0점), 일반 질문 분모 제외, 복수 근거 질문은 모두 찾아야 hit
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import full_index as fi  # noqa: E402
from database import handoff_data as hd  # noqa: E402
from database import retrieval_eval as rv  # noqa: E402
from database import retrieval_run as rr  # noqa: E402
from database.qdrant import store  # noqa: E402


def setUpModule():
    import logging
    logging.getLogger().setLevel(logging.ERROR)


def _onehot(index: int) -> list[float]:
    vector = [0.0] * fi.EMBEDDING_DIM
    vector[index] = 1.0
    return vector


class TestFrozenSet(unittest.TestCase):
    def test_loads_real_frozen_set(self):
        questions, freeze = rr.load_frozen_questions()
        self.assertEqual((len(questions), sum(q["answerable"] for q in questions)), (55, 52))
        self.assertEqual(freeze["eval_set"], "retrieval_eval_v0")

    def test_tampered_question_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            for name in ("retrieval_eval_v0.freeze.json", "questions_candidates_v0.source.json",
                         "questions_candidates_v0.jsonl"):
                shutil.copy(rr.EVAL_DIR / name, target / name)
            rr.load_frozen_questions(target)                                   # 복사본은 통과
            rows = rv.load_jsonl(target / "questions_candidates_v0.jsonl")
            rows[0]["gold"]["required"][0]["char_end"] += 1                    # 정답 위치를 1자 바꿈
            rv.write_jsonl(target / "questions_candidates_v0.jsonl", rows)
            with self.assertRaises(rr.EvalIntegrityError):
                rr.load_frozen_questions(target)


class TestEvaluateCollection(unittest.TestCase):
    """실제 B3 청크 일부를 one-hot 벡터로 넣어, 질문 벡터가 어느 청크를 가리키는지 정해 둔다 (sample_only 벡터)."""

    @classmethod
    def setUpClass(cls):
        questions, _ = rr.load_frozen_questions()
        cls.by_id = {q["question_id"]: q for q in questions}
        documents = {d["document_id"]: d for d in hd.load_documents()}
        cls.chunks, _ = fi.build_config_chunks("B_B3", [documents["DOC_NHUF_003"], documents["DOC_LH_001"]])
        cls.client = store.get_client(location=":memory:")
        cls.collection = "exp_unit_retrieval_run"
        store.ensure_collection(cls.client, cls.collection, fi.EMBEDDING_DIM)
        points = [{"id": c["point_id"], "vector": _onehot(i), "payload": {
            "chunk_id": c["chunk_id"], "document_id": c["document_id"], "policy_id": c["policy_id"], "text": c["text"],
            "char_start": c["char_start"], "char_end": c["char_end"], "chunking_version": c["chunking_version"]}}
                  for i, c in enumerate(cls.chunks)]
        store.upsert_points(cls.client, cls.collection, points)

    def _index_of_chunk_containing(self, question_id: str, evidence_index: int = 0) -> int:
        gold = self.by_id[question_id]["gold"]["required"][evidence_index]
        return next(i for i, c in enumerate(self.chunks) if c["document_id"] == gold["document_id"]
                    and c["char_start"] <= gold["char_start"] and gold["char_end"] <= c["char_end"])

    def test_hit_at_rank_one_and_no_text_recorded(self):
        question = self.by_id["RQ001"]
        result = rr.evaluate_collection(self.client, self.collection, [question],
                                        [_onehot(self._index_of_chunk_containing("RQ001"))])
        row = result["questions"][0]
        self.assertEqual(row["scores"]["at_1"]["outcome"], "hit")
        self.assertEqual(row["scores"]["at_5"]["reciprocal_rank"], 1.0)
        self.assertEqual(len(row["hits"]), 5)
        self.assertNotIn("text", row["hits"][0])
        self.assertGreater(row["hits"][0]["text_chars"], 0)

    def test_multi_evidence_needs_all(self):
        # RQ019: 총자산·자동차 근거 2개. 한 근거 청크만 가리키는 벡터 → 같은 청크에 둘 다 있으면 hit, 아니면 partial
        question = self.by_id["RQ019"]
        first = self._index_of_chunk_containing("RQ019", 0)
        second = self._index_of_chunk_containing("RQ019", 1)
        row = rr.evaluate_collection(self.client, self.collection, [question], [_onehot(first)])["questions"][0]
        expected = "hit" if first == second else "partial"
        self.assertEqual(row["scores"]["at_1"]["outcome"], expected)
        self.assertEqual(row["scores"]["at_1"]["required_total"], 2)

    def test_aggregate_recall_cutoffs_mrr_and_general_excluded(self):
        questions = [self.by_id["RQ001"], self.by_id["RQ053"]]
        vectors = [_onehot(len(self.chunks) + 1), _onehot(0)]          # RQ001: 아무 청크와도 유사도 0 → 순위 임의
        result = rr.evaluate_collection(self.client, self.collection, questions, vectors)
        summary = rr.aggregate(result["questions"], self.by_id)
        self.assertEqual(summary["overall"]["questions"], 1)                 # 일반 질문 제외
        self.assertEqual(len(summary["general_questions"]), 1)
        rank = result["questions"][0]["scores"]["at_5"]["first_hit_rank"]
        self.assertEqual(summary["overall"]["mrr_at_5"], round(1 / rank, 4) if rank else 0.0)
        self.assertLessEqual(summary["overall"]["recall_at_1"], summary["overall"]["recall_at_3"])
        self.assertLessEqual(summary["overall"]["recall_at_3"], summary["overall"]["recall_at_5"])


class TestRunEmbedsOnce(unittest.TestCase):
    def test_questions_embedded_once_and_reused(self):
        client = store.get_client(location=":memory:")
        store.ensure_collection(client, "exp_unit_run_a", fi.EMBEDDING_DIM)
        store.upsert_points(client, "exp_unit_run_a", [{"id": fi.chunking.make_point_id("CHK_X_1"), "vector": _onehot(0),
                                                       "payload": {"chunk_id": "CHK_X_1", "document_id": "DOC_X",
                                                                   "policy_id": "POL_X", "text": "가상", "char_start": 0,
                                                                   "char_end": 2}}])
        calls = []

        def counting_embedder(texts):
            calls.append(len(texts))
            return [_onehot(1) for _ in texts]
        fake = {name: {"collection": "exp_unit_run_a", "expected_points": 1, "actual_points": 1, "state": "complete",
                       "chunks": []} for name in rr.CONFIGS}
        with mock.patch.object(rr, "check_collections", return_value=fake):
            output = rr.run(client, counting_embedder, documents=[])
        self.assertEqual(calls, [55])
        self.assertEqual(set(output["results"]), set(rr.CONFIGS))
        self.assertTrue(output["query"]["embedded_once"])
        self.assertIsNone(output["query"]["query_template"])
        json.dumps(output, ensure_ascii=False)                                 # 결과는 JSON으로 저장 가능


class TestModelsAndInstruction(unittest.TestCase):
    """모델별 1회 임베딩, 질의 지시문 적용, 알 수 없는 지시문 거부"""

    def setUp(self):
        self.client = store.get_client(location=":memory:")
        for name, dim in (("exp_unit_m06", fi.EMBEDDING_DIM), ("exp_unit_m4b", 2560)):
            store.ensure_collection(self.client, name, dim)
            vector = [0.0] * dim
            vector[0] = 1.0
            store.upsert_points(self.client, name, [{"id": fi.chunking.make_point_id(f"CHK_{name}"), "vector": vector,
                                                     "payload": {"chunk_id": f"CHK_{name}", "document_id": "DOC_X",
                                                                 "policy_id": "POL_X", "text": "가상", "char_start": 0,
                                                                 "char_end": 2}}])
        self.fake = {
            "A_B2": {"collection": "exp_unit_m06", "model": "m06", "dim": fi.EMBEDDING_DIM, "state": "complete", "chunks": []},
            "A_B2_4B": {"collection": "exp_unit_m4b", "model": "m4b", "dim": 2560, "state": "complete", "chunks": []},
        }
        self.calls = {"m06": [], "m4b": []}

    def _embedder(self, model, dim):
        def embed(texts):
            self.calls[model].append(list(texts))
            vector = [0.0] * dim
            vector[0] = 1.0
            return [vector for _ in texts]
        return embed

    def _run(self, template="none"):
        embedders = {"m06": self._embedder("m06", fi.EMBEDDING_DIM), "m4b": self._embedder("m4b", 2560)}
        with mock.patch.object(rr, "check_collections", return_value=self.fake):
            return rr.run(self.client, embedders, documents=[], configs=("A_B2", "A_B2_4B"),
                          query_template_name=template)

    def test_each_model_embeds_questions_once(self):
        output = self._run()
        self.assertEqual([len(call) for call in self.calls["m06"]], [55])
        self.assertEqual([len(call) for call in self.calls["m4b"]], [55])
        self.assertEqual(output["query"]["models"]["m4b"]["dim"], 2560)
        self.assertEqual(set(output["results"]), {"A_B2", "A_B2_4B"})

    def test_instruction_applied_only_when_selected(self):
        self._run("none")
        self.assertFalse(any(text.startswith("Instruct:") for text in self.calls["m4b"][0]))
        self.calls = {"m06": [], "m4b": []}
        output = self._run("instruct_v1")
        self.assertTrue(all(text.startswith("Instruct:") and text.endswith(q) for text, q in
                            zip(self.calls["m4b"][0], [q["question"] for q in rr.load_frozen_questions()[0]])))
        self.assertEqual(output["query"]["query_template_name"], "instruct_v1")
        with self.assertRaises(rr.EvalIntegrityError):
            self._run("unknown_template")


if __name__ == "__main__":
    unittest.main(verbosity=2)
