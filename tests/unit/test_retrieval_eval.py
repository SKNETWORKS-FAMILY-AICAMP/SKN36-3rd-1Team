"""
Retrieval 평가(database/retrieval_eval.py) 단위 테스트 — DB·임베딩 없이 실행

실행 방법 (프로젝트 루트에서):
    .venv\\Scripts\\python.exe -m unittest tests/unit/test_retrieval_eval.py -v

검사 내용
- 근거 위치 찾기(글자 그대로 / 공백 차이만), 없는 근거·모호한 위치·같은 정책 다른 문서·다른 정책 우연 일치
- 채점: 근거 전체 포함만 적중(1자 겹침 불인정), 분산 근거(split_only), 복수 근거(partial), 문서만 일치(doc_only),
  일반 질문(not_scored), MRR·첫 정답 순위
- 실제 후보 파일: 55개 모두 원문 검증, 원본(source)과 빌드 결과 일치, 세 구성 모두 도달 가능
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import handoff_data as hd  # noqa: E402
from database import retrieval_eval as rv  # noqa: E402

EVAL_DIR = PROJECT_ROOT / "docs" / "development" / "retrieval_eval"
SOURCE = EVAL_DIR / "questions_candidates_v0.source.json"
BUILT = EVAL_DIR / "questions_candidates_v0.jsonl"

# 가상 문서 (sample_only)
DOCS = [
    {"document_id": "DOC_S_1", "policy_id": "POL_S_1", "text": "가상 정책 안내\n지원금액: 월 10만원\n신청기간: 상시\n지원금액: 월 10만원\n"},
    {"document_id": "DOC_S_2", "policy_id": "POL_S_1", "text": "같은 정책의 다른 화면\n지원금액: 월 10만원\n"},
    {"document_id": "DOC_S_3", "policy_id": "POL_S_3", "text": "다른 정책\n신청기간: 상시\n소득 기준:\n중위소득 50% 이하\n"},
]


def _question(gold, answerable=True, qid="Q1", qtype="numeric_table"):
    return rv.build_question_candidates([{"question_id": qid, "question": "가상 질문", "question_type": qtype,
                                          "answerable": answerable, "gold": gold}], DOCS)[0]


def _hit(rank, document_id, start, end, policy_id="POL_S_1"):
    return {"rank": rank, "document_id": document_id, "char_start": start, "char_end": end, "policy_id": policy_id}


class TestBuild(unittest.TestCase):
    def test_exact_and_whitespace_matches(self):
        self.assertEqual(rv.find_evidence_spans("가 나\n다", "가 나\n다")[1], "exact")
        spans, mode = rv.find_evidence_spans("소득 기준:\n중위소득", "소득 기준: 중위소득")
        self.assertEqual(mode, "whitespace")
        self.assertEqual(spans, [(0, len("소득 기준:\n중위소득"))])
        self.assertEqual(rv.find_evidence_spans("가나다", "라마")[0], [])

    def test_not_found_and_ambiguous_need_review(self):
        missing = _question([{"document_id": "DOC_S_1", "evidence_text": "월 20만원"}])
        self.assertEqual(missing["verification"], "needs_review")
        self.assertEqual(missing["gold"]["required"][0]["status"], rv.STATUS_NOT_FOUND)
        ambiguous = _question([{"document_id": "DOC_S_1", "evidence_text": "지원금액: 월 10만원"}])
        self.assertEqual(ambiguous["gold"]["required"][0]["status"], rv.STATUS_AMBIGUOUS)
        self.assertEqual(ambiguous["verification"], "needs_review")

    def test_occurrence_selection_and_any_occurrence(self):
        second = _question([{"document_id": "DOC_S_1", "evidence_text": "지원금액: 월 10만원", "occurrence": 2,
                             "accept_same_policy_documents": False}])
        gold = second["gold"]["required"][0]
        self.assertEqual(gold["char_start"], DOCS[0]["text"].rindex("지원금액"))
        self.assertEqual(len(gold["alternatives"]), 1)
        anywhere = _question([{"document_id": "DOC_S_1", "evidence_text": "지원금액: 월 10만원",
                               "accept_any_occurrence": True}])
        documents = sorted(alt["document_id"] for alt in anywhere["gold"]["required"][0]["alternatives"])
        self.assertEqual(documents, ["DOC_S_1", "DOC_S_1", "DOC_S_2"])            # 같은 정책 다른 문서 포함

    def test_other_policy_coincidence_is_not_gold(self):
        question = _question([{"document_id": "DOC_S_1", "evidence_text": "신청기간: 상시"}], qtype="application")
        gold = question["gold"]["required"][0]
        self.assertEqual(gold["other_documents_with_same_text"], ["DOC_S_3"])
        self.assertEqual({alt["document_id"] for alt in gold["alternatives"]}, {"DOC_S_1"})

    def test_duplicate_id_and_unknown_type_rejected(self):
        item = {"question_id": "Q1", "question": "가", "question_type": "numeric_table", "gold": []}
        with self.assertRaises(ValueError):
            rv.build_question_candidates([item, item], DOCS)
        with self.assertRaises(ValueError):
            rv.build_question_candidates([{**item, "question_type": "unknown"}], DOCS)


class TestScoring(unittest.TestCase):
    def setUp(self):
        self.question = _question([{"document_id": "DOC_S_3", "evidence_text": "소득 기준: 중위소득 50% 이하"}],
                                  qtype="age_income_asset")
        gold = self.question["gold"]["required"][0]
        self.start, self.end = gold["char_start"], gold["char_end"]

    def test_full_containment_is_required(self):
        result = rv.score_question(self.question, [_hit(1, "DOC_S_1", 0, 5), _hit(2, "DOC_S_3", 0, self.end)])
        self.assertEqual((result["outcome"], result["first_hit_rank"], result["reciprocal_rank"]), ("hit", 2, 0.5))
        overlap = rv.score_question(self.question, [_hit(1, "DOC_S_3", self.end - 1, self.end + 5)])
        self.assertEqual(overlap["outcome"], "doc_only")                      # 1자 겹침은 적중이 아님

    def test_split_evidence_is_separated(self):
        middle = (self.start + self.end) // 2
        result = rv.score_question(self.question, [_hit(1, "DOC_S_3", 0, middle), _hit(2, "DOC_S_3", middle, 999)])
        self.assertEqual(result["outcome"], "split_only")
        self.assertEqual(result["reciprocal_rank"], 0.0)

    def test_miss_doc_only_and_top_k_limit(self):
        self.assertEqual(rv.score_question(self.question, [_hit(1, "DOC_S_1", 0, 10)])["outcome"], "miss")
        late = [_hit(rank, "DOC_S_1", 0, 1) for rank in range(1, 6)] + [_hit(6, "DOC_S_3", 0, self.end)]
        self.assertEqual(rv.score_question(self.question, late, k=5)["outcome"], "miss")
        self.assertEqual(rv.score_question(self.question, late, k=6)["first_hit_rank"], 6)

    def test_multi_evidence_needs_all_and_rank_is_last_found(self):
        text = DOCS[0]["text"]
        question = _question([{"document_id": "DOC_S_1", "evidence_text": "신청기간: 상시"},
                              {"document_id": "DOC_S_1", "evidence_text": "가상 정책 안내"}], qtype="application")
        first_line = (0, text.index("\n"))
        period = (text.index("신청기간"), text.index("신청기간") + len("신청기간: 상시"))
        partial = rv.score_question(question, [_hit(1, "DOC_S_1", *first_line)])
        self.assertEqual(partial["outcome"], "partial")
        full = rv.score_question(question, [_hit(1, "DOC_S_1", *first_line), _hit(3, "DOC_S_1", *period)])
        self.assertEqual((full["outcome"], full["first_hit_rank"]), ("hit", 3))

    def test_same_policy_alternative_document_counts(self):
        question = _question([{"document_id": "DOC_S_1", "evidence_text": "지원금액: 월 10만원", "occurrence": 1}])
        other = DOCS[1]["text"]
        start = other.index("지원금액")
        result = rv.score_question(question, [_hit(1, "DOC_S_2", start, len(other))])
        self.assertEqual(result["outcome"], "hit")

    def test_general_questions_are_not_scored_and_summary(self):
        general = _question([], answerable=False, qid="Q2", qtype="general_no_single_answer")
        scores = [rv.score_question(general, [_hit(1, "DOC_S_1", 0, 3), _hit(2, "DOC_S_3", 0, 3, "POL_S_3")]),
                  rv.score_question(self.question, [_hit(1, "DOC_S_3", 0, self.end)]),
                  rv.score_question(self.question, [])]
        self.assertEqual(scores[0]["outcome"], "not_scored")
        self.assertEqual(scores[0]["distinct_policies"], 2)
        summary = rv.summarize(scores)
        self.assertEqual((summary["scored_questions"], summary["not_scored"]), (2, 1))
        self.assertEqual((summary["recall_at_5"], summary["mrr"]), (0.5, 0.5))

    def test_attainability(self):
        chunks = [{"document_id": "DOC_S_3", "char_start": 0, "char_end": self.start + 2}]
        self.assertEqual(rv.attainability([self.question], chunks), {"Q1": ["Q1-E1"]})
        chunks.append({"document_id": "DOC_S_3", "char_start": self.start, "char_end": self.end})
        self.assertEqual(rv.attainability([self.question], chunks), {})


class TestFrozenV0(unittest.TestCase):
    """retrieval_eval_v0 고정 기록과 실제 파일이 같은지 (질문·정답이 몰래 바뀌지 않았는지)."""

    def test_freeze_hashes_match_files_and_manifest(self):
        import hashlib
        from database import full_index
        record = json.loads((EVAL_DIR / "retrieval_eval_v0.freeze.json").read_text(encoding="utf-8"))
        self.assertEqual(record["eval_set"], "retrieval_eval_v0")
        for name, expected in record["files"].items():
            data = (EVAL_DIR / name).read_bytes().replace(b"\r\n", b"\n")
            with self.subTest(file=name):
                self.assertEqual(hashlib.sha256(data).hexdigest(), expected)
        self.assertEqual(record["manifest_sha256"], full_index.manifest_sha256())
        self.assertEqual((record["questions"], record["scored_questions"], record["verified"]), (55, 52, 55))


class TestCandidateFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = json.loads(SOURCE.read_text(encoding="utf-8"))
        cls.documents = {d["document_id"]: d for d in hd.load_documents()}
        cls.built = rv.load_jsonl(BUILT)

    def test_built_file_matches_source(self):
        rebuilt = rv.build_question_candidates(self.source["items"], list(self.documents.values()))
        for row in rebuilt:
            row.update({"source_version": self.source["version"], "data_version": self.source["data_version"]})
        self.assertEqual(rebuilt, self.built)

    def test_all_candidates_verified_against_original_text(self):
        self.assertGreaterEqual(len(self.built), 30)
        self.assertEqual(set(q["question_type"] for q in self.built), set(rv.QUESTION_TYPES))
        for question in self.built:
            with self.subTest(question=question["question_id"]):
                self.assertEqual(question["status"], "candidate")
                self.assertEqual(question["verification"], "verified")
                for gold in question["gold"]["required"]:
                    text = self.documents[gold["document_id"]]["text"]
                    self.assertEqual(text[gold["char_start"]:gold["char_end"]], gold["evidence_text"])
                    for alt in gold["alternatives"]:
                        self.assertEqual(self.documents[alt["document_id"]]["policy_id"], gold["policy_id"])
                if not question["answerable"]:
                    self.assertEqual(question["gold"]["required"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
