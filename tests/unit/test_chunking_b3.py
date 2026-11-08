"""
B3 청킹 후보(R2 고아 소제목·R5 HTML 제목 미인식 개선) 단위 테스트 — DB·LLM 없이 실행

실행 방법 (프로젝트 루트에서):
    .venv\\Scripts\\python.exe -m unittest tests/unit/test_chunking_b3.py -v

구성
1) 가상 예제(sample_only): 소제목 앞에서 자르기, 수치 조건 제목 제외
2) 실제 문서 246건: 원문 복원·누락·중복·페이지·ID, B2 대비 고아 소제목·제목 경계, B3 출력 해시
주의: 검색 정확도(B2보다 좋은가)는 검사하지 않는다. 후속 Retrieval 평가에서 측정한다.
"""

import hashlib
import json
import sys
import unittest
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import chunking as ck  # noqa: E402
from database import handoff_data as hd  # noqa: E402

# B3 출력 해시 (2026-11-06 기록). B3 규칙을 일부러 바꿀 때만 갱신한다.
B3_OUTPUT_SHA256 = ("5b400f5933f153f60f3df206d3010d1bcee0d0927e9afe23e01c5d6254dc3f8e",
                    "87b259d9e7e9d2093b1ce47e905dc4db2e55d98a7baec81dadac403c2c3112e8")
# 2026-11-06 정밀 점검에서 B2 고아 소제목으로 센 22건 (측정 기준 고정용)
B2_ORPHAN_COUNT = 22
# R5 대상: B1/B2에서 길이 기반 분할(length_fallback)이던 HTML 문서 중 구조 인식 제목이 2개 이상인 문서
R5_DOCUMENTS = ("DOC_NHUF_004", "DOC_NHUF_006", "DOC_NHUF_009", "DOC_NHUF_010", "DOC_WORK24_001")


def _output_sha256(chunks: list, reports: list) -> tuple[str, str]:
    return tuple(hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                 for value in (chunks, reports))


# ---------------------------------------------------------------------------
# 1) 가상 예제 (sample_only: 실제 정책 내용이 아님)
# ---------------------------------------------------------------------------
class TestOrphanLabelSample(unittest.TestCase):
    TEXT = ("가상 본문 문장입니다. " * 6 + "\n") * 3 + "자동차\n- 가상 기준 1천만원 이하\n" + ("가상 본문 문장입니다. " * 6 + "\n") * 3

    def _pieces(self, avoid: bool):
        return ck.split_long_span(self.TEXT, 0, len(self.TEXT), chunk_size=260, chunk_overlap=20,
                                  avoid_orphan_label=avoid)

    def test_label_line_detection(self):
        for line in ("자동차", "ㅇ (소득)", "대출금리", "2. 개인정보 수집·이용 안내", "■ 입주자격"):
            with self.subTest(line=line):
                self.assertTrue(ck._is_orphan_label_line(line))
        for line in ("가상 문장입니다.", "총자산 3억원 이하", "이 줄은 소제목이라고 보기에는 너무 길게 이어지는 문장"):
            with self.subTest(line=line):
                self.assertFalse(ck._is_orphan_label_line(line))

    def test_b1_behaviour_leaves_label_at_end_but_b3_moves_it(self):
        def ends_with_label(pieces):
            return any(self.TEXT[s:e].splitlines()[-1].strip() == "자동차" for s, e, _o in pieces)
        self.assertTrue(ends_with_label(self._pieces(False)), "예제가 B1 방식에서 고아 소제목을 만들어야 비교가 된다")
        b3 = self._pieces(True)
        self.assertFalse(ends_with_label(b3))
        label_piece = [s for s, e, _o in b3 if "자동차" in self.TEXT[s:e]]
        self.assertTrue(any("가상 기준 1천만원" in self.TEXT[s:e] for s, e, _o in b3 if "자동차" in self.TEXT[s:e]))
        self.assertTrue(label_piece)

    def test_b3_split_keeps_every_character(self):
        covered = [False] * len(self.TEXT)
        for start, end, _overlap in self._pieces(True):
            self.assertLessEqual(end - start, 260)
            for index in range(start, end):
                covered[index] = True
        self.assertEqual([i for i, ch in enumerate(self.TEXT) if not covered[i] and not ch.isspace()], [])


class TestHtmlHeadingsSample(unittest.TestCase):
    TEXT = ("가상 정책 안내 화면\n"
            "대출대상\n가상 조건을 모두 충족하는 가구\n추가 설명 줄\n"
            "■ 연 2.5% 금리 우대\n가상 우대 설명\n"
            "대출금리\n가상 금리 설명 줄\n추가 설명 줄\n")

    def test_vocabulary_headings_are_added_and_numeric_titles_are_rejected(self):
        starts = ck._heading_starts_html_b3(self.TEXT)
        titles = [title for _p, title, _l in starts]
        self.assertIn("대출대상", titles)
        self.assertIn("대출금리", titles)
        self.assertNotIn("■ 연 2.5% 금리 우대", titles)
        self.assertEqual(ck._heading_starts_html(self.TEXT), [])          # B1 규칙은 이 제목들을 못 찾는다(R5)
        for position, _title, _level in starts:
            self.assertEqual(ck._line_start(self.TEXT, position), position)

    def test_single_heading_is_not_used(self):
        text = "가상 안내\n대출대상\n가상 조건 줄\n추가 줄\n"
        self.assertEqual(ck._heading_starts_html_b3(text), [])


# ---------------------------------------------------------------------------
# 2) 실제 문서 246건
# ---------------------------------------------------------------------------
class TestB3RealDocuments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = hd.load_documents()
        cls.by_id = {document["document_id"]: document for document in cls.documents}
        cls.b2, _ = ck.chunk_documents(cls.documents, strategy="B2")
        cls.b3, cls.reports = ck.chunk_documents(cls.documents, strategy="B3")
        cls.report_by_id = {report["document_id"]: report for report in cls.reports}
        cls.b3_by_doc = defaultdict(list)
        for chunk in cls.b3:
            cls.b3_by_doc[chunk["document_id"]].append(chunk)
        cls.b2_by_doc = defaultdict(list)
        for chunk in cls.b2:
            cls.b2_by_doc[chunk["document_id"]].append(chunk)

    def test_output_hash_is_fixed(self):
        self.assertEqual(_output_sha256(self.b3, self.reports), B3_OUTPUT_SHA256)

    def test_every_document_and_text_is_original(self):
        self.assertEqual(set(self.b3_by_doc), set(self.by_id))
        for chunk in self.b3:
            document = self.by_id[chunk["document_id"]]
            self.assertEqual(chunk["text"], document["text"][chunk["char_start"]:chunk["char_end"]], chunk["chunk_id"])
            self.assertLessEqual(len(chunk["text"]), ck.CHUNK_SIZE)
            self.assertTrue(chunk["text"].strip())

    def test_no_missing_or_unintended_overlap(self):
        for document_id, document in self.by_id.items():
            result = ck.verify_coverage(document, self.b3_by_doc[document_id],
                                        self.report_by_id[document_id]["excluded_spans"])
            with self.subTest(document=document_id):
                self.assertEqual(result["missing_chars"], 0)
                self.assertEqual(result["unintended_overlap_chars"], 0)
                self.assertEqual(result["excluded_in_chunks"], 0)

    def test_ids_are_unique_and_versioned(self):
        chunk_ids = [chunk["chunk_id"] for chunk in self.b3]
        self.assertEqual(len(chunk_ids), len(set(chunk_ids)))
        self.assertEqual(len({chunk["point_id"] for chunk in self.b3}), len(chunk_ids))
        self.assertTrue(all(chunk_id.startswith("CHK_B3_") for chunk_id in chunk_ids))
        self.assertTrue(all(chunk["chunking_version"] == "B3" for chunk in self.b3))
        self.assertFalse(set(chunk_ids) & {chunk["chunk_id"] for chunk in self.b2})

    def test_pdf_pages_match_markers(self):
        for document in self.documents:
            if document["source_type"] != "pdf":
                continue
            markers = ck.find_page_markers(document["text"])
            for chunk in self.b3_by_doc[document["document_id"]]:
                with self.subTest(chunk=chunk["chunk_id"]):
                    prior = [page for start, _end, page in markers if start <= chunk["char_start"]]
                    self.assertTrue(prior)
                    self.assertEqual(chunk["page_start"], prior[-1])
                    self.assertEqual(chunk["page_start"], chunk["page_end"])
                    self.assertFalse(any(chunk["char_start"] < start < chunk["char_end"] for start, _e, _p in markers))

    def test_orphan_labels_reduced(self):
        self.assertEqual(len(ck.find_orphan_label_chunks(self.b2)), B2_ORPHAN_COUNT)
        b3_orphans = ck.find_orphan_label_chunks(self.b3)
        self.assertLessEqual(len(b3_orphans), 3, b3_orphans)
        for document_id in ("DOC_NHUF_003", "DOC_NHUF_005"):
            self.assertEqual([item for item in b3_orphans if item["document_id"] == document_id], [])

    def test_r5_documents_use_headings(self):
        for document_id in R5_DOCUMENTS:
            report = self.report_by_id[document_id]
            with self.subTest(document=document_id):
                self.assertEqual(report["boundary_method"], "html_heading")
                self.assertGreater(report["b3_text_headings_added"], 0)
                b2_align = ck.heading_alignment(self.by_id[document_id], self.b2_by_doc[document_id])
                b3_align = ck.heading_alignment(self.by_id[document_id], self.b3_by_doc[document_id])
                self.assertGreater(b3_align["at_chunk_start"], b2_align["at_chunk_start"])
                self.assertGreaterEqual(b3_align["with_body"], b2_align["with_body"])
        self.assertEqual(len(self.b3_by_doc["DOC_LH_003"]), 1)      # 제목 1개짜리 짧은 문서는 그대로 1청크

    def test_api_and_pdf_heading_rules_unchanged(self):
        for document in self.documents:
            if document["source_type"] in ("api", "pdf"):
                with self.subTest(document=document["document_id"]):
                    self.assertEqual(self.report_by_id[document["document_id"]]["b3_text_headings_added"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
