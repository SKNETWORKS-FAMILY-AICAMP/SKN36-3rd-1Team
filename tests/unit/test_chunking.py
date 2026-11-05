"""
구조 기반 청킹(B안) 단위 테스트 — DB 없이 실행

실행 방법 (프로젝트 루트에서):
    python -m unittest tests/unit/test_chunking.py -v

구성
1) 가상 예제(sample_only) 테스트: 함수 하나하나가 의도대로 자르는지 작은 문자열로 확인
2) 실제 문서 246건 테스트: 원문 위치·누락·중복·ID·출처·페이지가 맞는지 전수 확인

주의: 이 테스트는 "B안이 A안보다 좋다"를 검사하지 않는다. 성능 비교는 검색 실험에서 측정한다.
"""

import re
import sys
import unittest
import uuid
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import chunking as ck  # noqa: E402
from database import handoff_data as hd  # noqa: E402

ALLOWED_EXCLUDED_REASONS = {"page_marker", "metadata_field:출처 URL", "metadata_field:최종수정일시"}
ALLOWED_METHODS = {"api_field", "pdf_heading", "html_heading", "length_fallback"}
CHUNK_ID_CONTRACT = re.compile(r"^CHK_[A-Za-z0-9_]+$")


# ---------------------------------------------------------------------------
# 1) 가상 예제 테스트 (sample_only: 실제 정책 내용이 아님)
# ---------------------------------------------------------------------------
class TestSplitLongSpan(unittest.TestCase):
    def test_short_span_becomes_one_trimmed_piece(self):
        text = "  짧은 문장입니다.  "
        pieces = ck.split_long_span(text, 0, len(text), chunk_size=100, chunk_overlap=10)
        self.assertEqual(len(pieces), 1)
        start, end, overlap = pieces[0]
        self.assertEqual(text[start:end], "짧은 문장입니다.")
        self.assertEqual(overlap, 0)

    def test_long_span_respects_size_offsets_and_overlap(self):
        lines = [f"{i}. 가상 조건 항목 {i}번은 예시 문장입니다." for i in range(1, 30)]
        text = "\n".join(lines)
        pieces = ck.split_long_span(text, 0, len(text), chunk_size=120, chunk_overlap=20)
        self.assertGreater(len(pieces), 1)
        covered = [False] * len(text)
        for start, end, overlap in pieces:
            self.assertLessEqual(end - start, 120)
            self.assertLessEqual(overlap, 20)
            self.assertFalse(text[start].isspace() or text[end - 1].isspace())
            for index in range(start, end):
                covered[index] = True
        missing = [i for i, ch in enumerate(text) if not covered[i] and not ch.isspace()]
        self.assertEqual(missing, [])

    def test_cut_prefers_item_start(self):
        # 항목 시작 줄("2. ...") 앞에서 잘려야 한다. 그래서 두 번째 조각은 항목 기호부터 시작하거나
        # overlap 때문에 앞 항목 끝부분부터 시작한다. 어느 쪽이든 첫 조각은 항목 경계에서 끝난다.
        text = "1. 첫째 항목 " + "가" * 60 + "\n2. 둘째 항목 " + "나" * 60
        pieces = ck.split_long_span(text, 0, len(text), chunk_size=90, chunk_overlap=0)
        first_start, first_end, _ = pieces[0]
        self.assertTrue(text[first_start:first_end].endswith("가"))
        self.assertTrue(text[pieces[1][0]:].startswith("2. 둘째"))

    def test_carriage_return_counts_as_line_break(self):
        text = "가" * 50 + "\r" + "나" * 50
        pieces = ck.split_long_span(text, 0, len(text), chunk_size=70, chunk_overlap=0)
        self.assertEqual(text[pieces[0][0]:pieces[0][1]], "가" * 50)


class TestApiSegments(unittest.TestCase):
    SAMPLE = (
        "[서비스명] 가상 서비스(sample_only)\n"
        "[지원대상] 가상 대상\n"
        "[우대형] 가상 우대 조건\n"
        "[선정기준] 가상 기준\n"
        "[지원내용] 가상 지원\n"
        "[출처 URL] https://example.org/sample\n"
        "[문의처] 000-0000\n"
    )

    def test_groups_and_metadata_exclusion(self):
        segments, excluded = ck._api_segments(self.SAMPLE)
        groups = [segment["group"] for segment in segments]
        self.assertEqual(groups, [ck.GROUP_BASIC, ck.GROUP_ELIGIBILITY, ck.GROUP_BENEFIT, ck.GROUP_CONTACT])
        # [우대형]은 표에 없으므로 앞의 [지원대상] 그룹을 이어받아 [선정기준]과 한 구간이 된다.
        self.assertIn("[우대형]", segments[1]["title"])
        self.assertIn("[선정기준]", segments[1]["title"])
        self.assertEqual([span["reason"] for span in excluded], ["metadata_field:출처 URL"])

    def test_chunk_document_excludes_metadata_text(self):
        document = {"document_id": "DOC_SAMPLE_001", "policy_id": "POL_SAMPLE_001", "source_type": "api",
                    "source_url": "https://example.org/sample", "text": self.SAMPLE}
        chunks, report = ck.chunk_document(document)
        self.assertEqual(report["boundary_method"], "api_field")
        self.assertFalse(any("[출처 URL]" in chunk["text"] for chunk in chunks))
        coverage = ck.verify_coverage(document, chunks, report["excluded_spans"])
        self.assertEqual(coverage["missing_chars"], 0)
        self.assertEqual(coverage["unintended_overlap_chars"], 0)


class TestHeadingSegments(unittest.TestCase):
    def test_html_numbered_list_splits_only_on_restart(self):
        text = "안내\n0. (가) 조건\n1. (나) 조건\n2. (다) 조건\n□ 가상 소제목\n1. 새 목록 첫 항목\n2. 새 목록 둘째"
        starts = ck._heading_starts_html(text)
        titles = [title for _pos, title, _level in starts]
        self.assertEqual(titles, ["0. (가) 조건", "□ 가상 소제목", "1. 새 목록 첫 항목"])

    def test_pdf_chapter_path_page_and_short_piece_merge(self):
        text = (
            "--- [Page 1] ---\n가상 공고 머리말 문장입니다. 충분히 긴 머리말을 둡니다.\n"
            "--- [Page 2] ---\n- 2 -\n1 공급개요\n■ 공급대상\n가상 공급 내용 문장입니다.\n"
            "--- [Page 3] ---\n가상 공급 내용이 다음 쪽으로 이어집니다.\n"
        )
        document = {"document_id": "DOC_SAMPLE_PDF", "policy_id": "POL_SAMPLE_001", "source_type": "pdf",
                    "source_url": "https://example.org/sample.pdf", "text": text}
        chunks, report = ck.chunk_document(document)
        self.assertEqual(report["boundary_method"], "pdf_heading")
        self.assertEqual([chunk["page_start"] for chunk in chunks], [1, 2, 3])
        # "- 2 -" 쪽번호 줄과 "1 공급개요" 장 제목 줄은 둘 다 30자 미만이라 따로 청크가 되지 않고,
        # 바로 뒤 "■ 공급대상" 구간 앞에 차례로 붙는다. 제목은 "장 > 절" 경로가 된다.
        self.assertTrue(chunks[1]["text"].startswith("- 2 -\n1 공급개요\n■ 공급대상"))
        self.assertEqual(chunks[1]["section_title"], "1 공급개요 > ■ 공급대상")
        self.assertEqual(chunks[2]["section_title"], "1 공급개요 > ■ 공급대상 (이어짐)")
        self.assertFalse(any("--- [Page" in chunk["text"] for chunk in chunks))
        coverage = ck.verify_coverage(document, chunks, report["excluded_spans"])
        self.assertEqual(coverage["missing_chars"], 0)

    def test_fallback_records_reason(self):
        document = {"document_id": "DOC_SAMPLE_HTML", "policy_id": "POL_SAMPLE_001", "source_type": "html",
                    "source_url": "https://example.org/sample", "title": "가상 문서", "text": "제목 없는 가상 본문입니다."}
        chunks, report = ck.chunk_document(document)
        self.assertEqual(report["boundary_method"], "length_fallback")
        self.assertTrue(report["fallback_reason"])
        self.assertEqual(chunks[0]["section_title"], "가상 문서")


# ---------------------------------------------------------------------------
# 2) 실제 문서 246건 테스트
# ---------------------------------------------------------------------------
class TestRealDocuments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = hd.load_documents()  # content_hash 검증 포함
        cls.documents_by_id = {doc["document_id"]: doc for doc in cls.documents}
        cls.chunks, cls.reports = ck.chunk_documents(cls.documents)
        cls.chunks_by_document = defaultdict(list)
        for chunk in cls.chunks:
            cls.chunks_by_document[chunk["document_id"]].append(chunk)

    def test_document_count_and_hash(self):
        self.assertEqual(len(self.documents), hd.EXPECTED_DOCUMENT_COUNT)

    def test_every_document_has_chunks_and_known_method(self):
        self.assertEqual(set(self.chunks_by_document), set(self.documents_by_id))
        for report in self.reports:
            with self.subTest(document=report["document_id"]):
                self.assertIn(report["boundary_method"], ALLOWED_METHODS)
                if report["boundary_method"] == "length_fallback":
                    self.assertTrue(report["fallback_reason"])

    def test_all_api_documents_use_field_boundaries(self):
        api_reports = [r for r in self.reports if r["source_type"] == "api"]
        self.assertEqual(len(api_reports), 232)
        self.assertTrue(all(r["boundary_method"] == "api_field" for r in api_reports))

    def test_text_equals_original_slice(self):
        for chunk in self.chunks:
            original = self.documents_by_id[chunk["document_id"]]["text"]
            with self.subTest(chunk=chunk["chunk_id"]):
                self.assertEqual(chunk["text"], original[chunk["char_start"]:chunk["char_end"]])
                self.assertTrue(chunk["text"])
                self.assertEqual(chunk["text"], chunk["text"].strip())
                self.assertLessEqual(len(chunk["text"]), ck.CHUNK_SIZE)

    def test_no_missing_text_and_no_unintended_overlap(self):
        for report in self.reports:
            document = self.documents_by_id[report["document_id"]]
            coverage = ck.verify_coverage(document, self.chunks_by_document[report["document_id"]],
                                          report["excluded_spans"])
            with self.subTest(document=report["document_id"]):
                self.assertEqual(coverage["missing_chars"], 0, coverage["missing_sample"])
                self.assertEqual(coverage["unintended_overlap_chars"], 0)
                self.assertEqual(coverage["excluded_in_chunks"], 0)

    def test_intentional_overlap_only_inside_same_section(self):
        for document_id, chunks in self.chunks_by_document.items():
            for previous, current in zip(chunks, chunks[1:]):
                if current["overlap_prev_chars"] == 0:
                    continue
                with self.subTest(chunk=current["chunk_id"]):
                    self.assertLessEqual(current["overlap_prev_chars"], ck.CHUNK_OVERLAP)
                    self.assertEqual(previous["section_title"], current["section_title"])
                    self.assertEqual(previous["char_end"] - current["char_start"], current["overlap_prev_chars"])

    def test_excluded_spans_have_allowed_reasons(self):
        reasons = {span["reason"] for report in self.reports for span in report["excluded_spans"]}
        self.assertLessEqual(reasons, ALLOWED_EXCLUDED_REASONS)
        self.assertFalse(any("[출처 URL]" in c["text"] or "[최종수정일시]" in c["text"] for c in self.chunks))

    def test_ids_unique_and_follow_contract(self):
        chunk_ids = [chunk["chunk_id"] for chunk in self.chunks]
        self.assertEqual(len(set(chunk_ids)), len(chunk_ids))
        self.assertTrue(all(CHUNK_ID_CONTRACT.match(cid) for cid in chunk_ids))
        self.assertTrue(all(c["point_id"] == str(uuid.uuid5(uuid.NAMESPACE_DNS, c["chunk_id"])) for c in self.chunks))
        # 기존 557건(A안)의 ID·Point ID와 겹치지 않아야 같은 컬렉션에 섞여도 덮어쓰지 않는다.
        old_chunks = hd.load_payload_chunks()
        self.assertFalse(set(chunk_ids) & {c["chunk_id"] for c in old_chunks})
        self.assertFalse({c["point_id"] for c in self.chunks} & {c["point_id"] for c in old_chunks})

    def test_chunk_sequence_contiguous_per_document(self):
        # 앞·뒤 청크(인접 문맥)를 chunk_seq ± 1로 찾을 수 있어야 한다.
        for document_id, chunks in self.chunks_by_document.items():
            with self.subTest(document=document_id):
                self.assertEqual([c["chunk_seq"] for c in chunks], list(range(1, len(chunks) + 1)))

    def test_policy_document_and_source_preserved(self):
        graph_policy_ids = {p["policy_id"] for p in hd.load_neo4j_nodes()["Policy"]}
        for chunk in self.chunks:
            document = self.documents_by_id[chunk["document_id"]]
            with self.subTest(chunk=chunk["chunk_id"]):
                self.assertEqual(chunk["policy_id"], document["policy_id"])
                self.assertEqual(chunk["source_url"], document["source_url"])
                self.assertEqual(chunk["source_type"], document["source_type"])
                self.assertIn(chunk["policy_id"], graph_policy_ids)  # Neo4j와 연결 가능

    def test_pdf_pages_follow_page_markers(self):
        for chunk in self.chunks:
            with self.subTest(chunk=chunk["chunk_id"]):
                if chunk["source_type"] != "pdf":
                    self.assertIsNone(chunk["page_start"])
                    continue
                markers = ck.find_page_markers(self.documents_by_id[chunk["document_id"]]["text"])
                expected_page = max(page for start, _end, page in markers if start <= chunk["char_start"])
                self.assertEqual(chunk["page_start"], expected_page)
                self.assertEqual(chunk["page_end"], expected_page)
                self.assertNotIn("--- [Page", chunk["text"])

    def test_api_chunk_does_not_mix_groups(self):
        for chunk in self.chunks:
            if chunk["boundary_method"] != "api_field":
                continue
            names = ck.API_FIELD_PATTERN.findall(chunk["text"])
            groups = {ck.API_FIELD_GROUPS[name] for name in names if name in ck.API_FIELD_GROUPS}
            with self.subTest(chunk=chunk["chunk_id"]):
                self.assertLessEqual(groups, {chunk["section_group"]})

    def test_deterministic(self):
        again, _ = ck.chunk_documents(self.documents)
        self.assertEqual(again, self.chunks)

    def test_old_chunks_can_be_located_for_comparison(self):
        # A안 비교를 위해 기존 557건의 원문 위치를 모두 찾을 수 있어야 한다.
        old_by_document = defaultdict(list)
        for chunk in hd.load_payload_chunks():
            old_by_document[chunk["document_id"]].append(chunk)
        for document_id, chunks in old_by_document.items():
            chunks.sort(key=lambda c: c["chunk_id"])
            positions = ck.locate_chunks_in_document(self.documents_by_id[document_id]["text"],
                                                     [c["text"] for c in chunks])
            with self.subTest(document=document_id):
                self.assertNotIn(None, positions)


# ---------------------------------------------------------------------------
# 3) B2 (짧은 청크 병합) — 가상 예제
# ---------------------------------------------------------------------------
def _sample_api_document(text: str) -> dict:
    return {"document_id": "DOC_SAMPLE_B2", "policy_id": "POL_SAMPLE_001", "source_type": "api",
            "source_url": "https://example.org/sample", "text": text}


class TestMergeShortChunksSample(unittest.TestCase):
    def _b2(self, text):
        document = _sample_api_document(text)
        b1_chunks, report = ck.chunk_document(document)
        return document, b1_chunks, ck.merge_short_chunks(document, b1_chunks), report

    def test_apply_and_contact_are_merged(self):
        text = ("[지원내용] " + "가" * 200 + "\n"
                "[구비서류] 가상 신청서\n"
                "[문의처] 000-0000\n")
        _doc, b1, b2, _ = self._b2(text)
        self.assertEqual(len(b1), 3)
        self.assertEqual(len(b2), 2)
        self.assertEqual(b2[1]["section_groups"], [ck.GROUP_APPLY, ck.GROUP_CONTACT])
        self.assertEqual(b2[1]["merged_from"], [b1[1]["chunk_id"], b1[2]["chunk_id"]])

    def test_benefit_between_eligibility_and_apply_is_not_merged(self):
        # 지원내용(짧음) 앞은 자격, 뒤는 신청 → 둘 다 허용되지 않은 조합이므로 그대로 둔다.
        text = ("[지원대상] " + "가" * 200 + "\n"
                "[지원내용] 가상 지원 10만원\n"
                "[신청기간 및 신청방법] " + "나" * 200 + "\n")
        _doc, b1, b2, _ = self._b2(text)
        self.assertEqual(len(b2), len(b1))
        self.assertEqual([c["section_groups"] for c in b2],
                         [[ck.GROUP_ELIGIBILITY], [ck.GROUP_BENEFIT], [ck.GROUP_APPLY]])

    def test_basic_and_eligibility_are_merged(self):
        text = "[서비스명] 가상 서비스\n[지원대상] 가상 대상자\n[지원내용] " + "다" * 200 + "\n"
        _doc, _b1, b2, _ = self._b2(text)
        self.assertEqual(b2[0]["section_groups"], [ck.GROUP_BASIC, ck.GROUP_ELIGIBILITY])
        self.assertEqual(b2[1]["section_groups"], [ck.GROUP_BENEFIT])

    def test_no_merge_across_excluded_span_or_over_max(self):
        # [출처 URL](제외 구간)이 사이에 있으면 합치지 않는다.
        text = "[구비서류] 가상 신청서\n[출처 URL] https://example.org/sample\n[문의처] 000-0000\n"
        _doc, b1, b2, _ = self._b2(text)
        self.assertEqual(len(b2), len(b1))
        # 합친 길이가 max_chars를 넘으면 합치지 않는다.
        document = _sample_api_document("[구비서류] 가상 신청서\n[문의처] 000-0000\n")
        b1_chunks, _ = ck.chunk_document(document)
        self.assertEqual(len(ck.merge_short_chunks(document, b1_chunks, max_chars=15)), 2)

    def test_pdf_merge_requires_same_page(self):
        text = ("--- [Page 1] ---\n1 가상 장\n■ 가상 절 A\n" + "가" * 80 + "\n"
                "--- [Page 2] ---\n가상 이어지는 짧은 문장입니다. 짧아요.\n■ 가상 절 B\n" + "나" * 300 + "\n")
        document = {"document_id": "DOC_SAMPLE_PDF2", "policy_id": "POL_SAMPLE_001", "source_type": "pdf",
                    "source_url": "https://example.org/sample.pdf", "text": text}
        b1_chunks, _ = ck.chunk_document(document)
        b2_chunks = ck.merge_short_chunks(document, b1_chunks)
        for chunk in b2_chunks:
            self.assertEqual(chunk["page_start"], chunk["page_end"])
            self.assertNotIn("--- [Page", chunk["text"])

    def test_merged_text_is_original_slice(self):
        text = "[구비서류] 가상 신청서\n\n[문의처] 000-0000\n"
        document, _b1, b2, _ = self._b2(text)
        self.assertEqual(len(b2), 1)
        self.assertEqual(b2[0]["text"], text[b2[0]["char_start"]:b2[0]["char_end"]])
        self.assertTrue(b2[0]["chunk_id"].startswith("CHK_B2_"))

    def test_unknown_strategy_is_rejected(self):
        with self.assertRaises(ValueError):
            ck.chunk_documents([], strategy="B3")


# ---------------------------------------------------------------------------
# 4) B2 — 실제 문서 246건
# ---------------------------------------------------------------------------
class TestB2RealDocuments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = hd.load_documents()
        cls.documents_by_id = {doc["document_id"]: doc for doc in cls.documents}
        cls.b1_chunks, cls.b1_reports = ck.chunk_documents(cls.documents)
        cls.b2_chunks, cls.b2_reports = ck.chunk_documents(cls.documents, strategy="B2")
        cls.b1_by_document = defaultdict(list)
        cls.b2_by_document = defaultdict(list)
        for chunk in cls.b1_chunks:
            cls.b1_by_document[chunk["document_id"]].append(chunk)
        for chunk in cls.b2_chunks:
            cls.b2_by_document[chunk["document_id"]].append(chunk)

    def test_b1_default_is_unchanged(self):
        explicit_b1, explicit_reports = ck.chunk_documents(self.documents, strategy="B1")
        self.assertEqual(explicit_b1, self.b1_chunks)
        self.assertEqual(explicit_reports, self.b1_reports)
        self.assertTrue(all(c["chunking_version"] == "B1" and "merged_from" not in c for c in self.b1_chunks))

    def test_b2_text_is_original_slice_and_within_size(self):
        for chunk in self.b2_chunks:
            original = self.documents_by_id[chunk["document_id"]]["text"]
            with self.subTest(chunk=chunk["chunk_id"]):
                self.assertEqual(chunk["text"], original[chunk["char_start"]:chunk["char_end"]])
                self.assertLessEqual(len(chunk["text"]), ck.CHUNK_SIZE)
                self.assertEqual(chunk["chunking_version"], "B2")

    def test_b2_no_missing_and_no_unintended_overlap(self):
        for report in self.b2_reports:
            document = self.documents_by_id[report["document_id"]]
            coverage = ck.verify_coverage(document, self.b2_by_document[report["document_id"]],
                                          report["excluded_spans"])
            with self.subTest(document=report["document_id"]):
                self.assertEqual(coverage["missing_chars"], 0)
                self.assertEqual(coverage["unintended_overlap_chars"], 0)
                self.assertEqual(coverage["excluded_in_chunks"], 0)

    def test_b2_merged_from_covers_every_b1_chunk_once_in_order(self):
        for document_id, b2_chunks in self.b2_by_document.items():
            flattened = [cid for chunk in b2_chunks for cid in chunk["merged_from"]]
            with self.subTest(document=document_id):
                self.assertEqual(flattened, [c["chunk_id"] for c in self.b1_by_document[document_id]])

    def test_b2_only_allowed_group_combinations(self):
        for chunk in self.b2_chunks:
            groups = chunk["section_groups"]
            with self.subTest(chunk=chunk["chunk_id"]):
                for i, first in enumerate(groups):
                    for second in groups[i + 1:]:
                        self.assertIn(frozenset({first, second}), ck.MERGE_PARTNER_GROUPS)

    def test_b2_ids_unique_and_separate_from_b1_and_a(self):
        b2_ids = [c["chunk_id"] for c in self.b2_chunks]
        self.assertEqual(len(set(b2_ids)), len(b2_ids))
        self.assertTrue(all(CHUNK_ID_CONTRACT.match(cid) and cid.startswith("CHK_B2_") for cid in b2_ids))
        self.assertTrue(all(c["point_id"] == str(uuid.uuid5(uuid.NAMESPACE_DNS, c["chunk_id"])) for c in self.b2_chunks))
        other_ids = {c["chunk_id"] for c in self.b1_chunks} | {c["chunk_id"] for c in hd.load_payload_chunks()}
        self.assertFalse(set(b2_ids) & other_ids)

    def test_b2_preserves_ids_source_and_pages(self):
        for chunk in self.b2_chunks:
            document = self.documents_by_id[chunk["document_id"]]
            with self.subTest(chunk=chunk["chunk_id"]):
                self.assertEqual(chunk["policy_id"], document["policy_id"])
                self.assertEqual(chunk["source_url"], document["source_url"])
                self.assertEqual(chunk["page_start"], chunk["page_end"])
                if chunk["source_type"] == "pdf":
                    self.assertIsNotNone(chunk["page_start"])
                else:
                    self.assertIsNone(chunk["page_start"])

    def test_b2_never_breaks_a_span_that_b1_kept_whole(self):
        # 병합은 청크를 넓히기만 하므로, B1에서 한 청크에 들어 있던 자격 필드·예외 줄은 B2에서도 그래야 한다.
        for document in self.documents:
            b1_ranges = [(c["char_start"], c["char_end"]) for c in self.b1_by_document[document["document_id"]]]
            b2_ranges = [(c["char_start"], c["char_end"]) for c in self.b2_by_document[document["document_id"]]]
            spans = ck.find_line_spans(document["text"], ck.EXCEPTION_LINE_PATTERN)
            if document["source_type"] == "api":
                spans += ck.find_api_field_spans(document["text"], ck.ELIGIBILITY_FIELDS)
            kept_by_b1 = [s for s in spans if ck.count_preserved_spans([s], b1_ranges)]
            with self.subTest(document=document["document_id"]):
                self.assertEqual(ck.count_preserved_spans(kept_by_b1, b2_ranges), len(kept_by_b1))

    def test_b2_reduces_short_chunks_and_is_deterministic(self):
        b1_short = sum(1 for c in self.b1_chunks if len(c["text"]) < ck.MIN_CHUNK_CHARS)
        b2_short = sum(1 for c in self.b2_chunks if len(c["text"]) < ck.MIN_CHUNK_CHARS)
        self.assertLess(b2_short, b1_short)
        again, _ = ck.chunk_documents(self.documents, strategy="B2")
        self.assertEqual(again, self.b2_chunks)

    def test_excluded_metadata_is_traceable(self):
        # 본문에서 뺀 [출처 URL]·[최종수정일시]도 원문 위치로 되찾을 수 있어야 한다.
        found_urls = 0
        for document, report in zip(self.documents, self.b2_reports):
            values = ck.get_excluded_metadata(document, report)
            for field_name, field_values in values.items():
                for value in field_values:
                    with self.subTest(document=document["document_id"], field=field_name):
                        self.assertTrue(value)
                        self.assertIn(value, document["text"])
            found_urls += len(values.get("출처 URL", []))
        self.assertEqual(found_urls, 232)


# ---------------------------------------------------------------------------
# 4) 구조 인식(analyze_structure) — 가상 예제 (sample_only: 실제 정책 내용이 아님)
# ---------------------------------------------------------------------------
def _sample_document(text: str, source_type: str = "html") -> dict:
    """구조 인식 테스트용 가상 문서 (sample_only)."""
    return {"document_id": "DOC_SAMPLE_ONLY", "policy_id": "POL_SAMPLE_ONLY", "source_type": source_type,
            "source_url": "https://example.invalid/sample_only", "title": "sample_only", "text": text}


def _heading_lines(analysis: dict) -> list[str]:
    return [heading["line"] for heading in analysis["headings"]]


def _reject_reason_of(analysis: dict, line: str) -> str | None:
    for candidate in analysis["rejected_candidates"]:
        if candidate["line"] == line:
            return candidate["reject_reason"]
    return None


class TestStructureSamples(unittest.TestCase):
    """제목 표기별 인식, 목록·목차·주의 문구 구분, 판정, 원문 보존 (모두 sample_only)."""

    BODY = ["만 19세 이상 가상 청년 (sample_only)", "월 최대 20만원 가상 지원 (sample_only)", "온라인으로 가상 신청 (sample_only)"]

    def _three_sections(self, titles: list[str]) -> str:
        return "\n".join(f"{title}\n{body}" for title, body in zip(titles, self.BODY))

    def test_bracket_angle_symbol_number_headings_are_detected(self):
        styles = {
            "bracket_square": ["[지원대상]", "[지원내용]", "[신청방법]"],
            "bracket_angle": ["<지원대상>", "<지원내용>", "<신청방법>"],
            "symbol_strong": ["■ 지원대상", "■ 지원내용", "■ 신청방법"],
            "numbered": ["1. 지원대상", "2. 지원내용", "3. 신청방법"],
        }
        for rule, titles in styles.items():
            with self.subTest(rule=rule):
                analysis = ck.analyze_structure(_sample_document(self._three_sections(titles)))
                self.assertEqual(_heading_lines(analysis), titles)
                self.assertEqual({heading["rule"] for heading in analysis["headings"]}, {rule})
                self.assertEqual([heading["group"] for heading in analysis["headings"]],
                                 [ck.GROUP_ELIGIBILITY, ck.GROUP_BENEFIT, ck.GROUP_APPLY])
                self.assertEqual(analysis["status"], ck.STATUS_STRUCTURED)
                score = ck.score_heading_detection(analysis, titles)
                self.assertEqual((score["precision"], score["recall"]), (1.0, 1.0))

    def test_heading_record_has_position_title_level_rule_and_evidence(self):
        text = self._three_sections(["■ 지원대상", "■ 지원내용", "■ 신청방법"])
        analysis = ck.analyze_structure(_sample_document(text))
        for heading in analysis["headings"]:
            self.assertEqual(text[heading["start"]:heading["end"]], heading["line"])
            self.assertEqual(heading["title"], heading["line"].lstrip("■ "))
            self.assertEqual(heading["level"], 2)
            self.assertIn("rule:symbol_strong", heading["evidence"])
            self.assertTrue(any(item.startswith("vocabulary:") for item in heading["evidence"]))

    def test_numbered_list_under_heading_is_not_a_section(self):
        # 마지막 항목 "3."은 뒤에 본문이 있어도, 앞 목록의 번호를 이어받으므로 목록의 끝(list_end)이다.
        text = ("■ 제출서류\n1. 신분증\n2. 주민등록등본\n3. 통장 사본\n가상 제출 안내 문장 (sample_only)\n"
                "■ 문의처\n가상 콜센터 안내 (sample_only)")
        analysis = ck.analyze_structure(_sample_document(text))
        self.assertEqual(_heading_lines(analysis), ["■ 제출서류", "■ 문의처"])
        self.assertEqual(_reject_reason_of(analysis, "1. 신분증"), "list_or_toc")
        self.assertEqual(_reject_reason_of(analysis, "2. 주민등록등본"), "list_or_toc")
        self.assertEqual(_reject_reason_of(analysis, "3. 통장 사본"), "list_end")

    def test_wrapped_list_items_are_sub_items_not_sections(self):
        # 항목이 두 줄로 넘어가면 "본문 줄"이 생기지만, 상위 제목(■) 아래의 번호 항목은 하위 항목으로 본다.
        text = ("■ 입주자격\n1. 무주택세대구성원으로서 가상 소득\n기준을 충족하는 사람 (sample_only)\n"
                "2. 가상 자산 기준을\n충족하는 사람 (sample_only)\n■ 문의처\n가상 콜센터 (sample_only)")
        analysis = ck.analyze_structure(_sample_document(text))
        self.assertEqual(_heading_lines(analysis), ["■ 입주자격", "■ 문의처"])
        self.assertEqual(_reject_reason_of(analysis, "1. 무주택세대구성원으로서 가상 소득"), "sub_item")

    def test_numbered_list_without_headings_is_unstructured(self):
        text = "가상 구비서류 안내 (sample_only)\n1. 신분증\n2. 주민등록등본\n3. 통장 사본\n위 서류를 제출합니다."
        analysis = ck.analyze_structure(_sample_document(text))
        self.assertEqual(analysis["headings"], [])
        self.assertEqual(analysis["status"], ck.STATUS_UNSTRUCTURED)

    def test_faq_numbers_are_headings_when_no_upper_heading_exists(self):
        text = ("1. 가상 질문 하나는 무엇인가요?\n가상 답변입니다. (sample_only)\n"
                "2. 가상 질문 둘은 무엇인가요?\n가상 답변입니다. (sample_only)")
        analysis = ck.analyze_structure(_sample_document(text, source_type="pdf"))
        self.assertEqual(len(analysis["headings"]), 2)
        self.assertEqual(analysis["status"], ck.STATUS_STRUCTURED)

    def test_notes_and_non_heading_lines_are_rejected_with_reason(self):
        lines = {
            "※ 신청 전 가상 기준을 확인하세요": "note_marker",
            "* 가상 참고 문구": "note_marker",
            "□": "empty_title",
            "[필수] 본인은 가상 개인정보 수집에 동의합니다": "label_with_body",
            "■ 신청은 방문 접수로만 가능합니다.": "sentence",
            "■ " + "가" * 41: "too_long",
            "[가상 신청 바로가기]": "ui_label",
            "1. 지원대상 ........ 3": "toc_entry",
        }
        text = "■ 지원대상\n" + "\n".join(f"{line}\n가상 본문 (sample_only)" for line in lines) + "\n■ 문의처\n가상 본문"
        analysis = ck.analyze_structure(_sample_document(text))
        self.assertEqual(_heading_lines(analysis), ["■ 지원대상", "■ 문의처"])
        for line, reason in lines.items():
            with self.subTest(line=line):
                self.assertEqual(_reject_reason_of(analysis, line), reason)

    def test_numbered_table_of_contents_is_not_confused_with_body(self):
        toc = "목차\n1. 지원대상\n2. 지원내용\n3. 신청방법\n"
        body = self._three_sections(["1. 지원대상", "2. 지원내용", "3. 신청방법"])
        text = toc + body
        analysis = ck.analyze_structure(_sample_document(text))
        self.assertEqual(_heading_lines(analysis), ["1. 지원대상", "2. 지원내용", "3. 신청방법"])
        self.assertTrue(all(heading["start"] >= len(toc) for heading in analysis["headings"]))
        self.assertEqual(sum(1 for c in analysis["rejected_candidates"] if c["reject_reason"] == "list_or_toc"), 3)

    def test_plain_table_of_contents_is_not_confused_with_body(self):
        toc = "지원대상\n지원내용\n신청방법\n"
        body = self._three_sections(["지원대상", "지원내용", "신청방법"])
        analysis = ck.analyze_structure(_sample_document(toc + body))
        self.assertEqual(len(analysis["headings"]), 3)
        self.assertTrue(all(heading["start"] >= len(toc) for heading in analysis["headings"]))
        self.assertEqual({heading["rule"] for heading in analysis["headings"]}, {"plain_vocabulary"})

    def test_repeated_title_keeps_the_longest_section(self):
        text = ("대출한도\n최대 가상 1억원\n대출기간\n가상 2년\n"
                "대출한도\n" + "가상 대출한도 상세 설명 (sample_only)\n" * 5)
        analysis = ck.analyze_structure(_sample_document(text))
        kept = [heading for heading in analysis["headings"] if heading["title"] == "대출한도"]
        self.assertEqual(len(kept), 1)
        self.assertGreater(kept[0]["start"], text.index("대출기간"))
        self.assertIn("duplicate_title", [c["reject_reason"] for c in analysis["rejected_candidates"]])

    def test_single_heading_document(self):
        short = ck.analyze_structure(_sample_document("■ 지원대상\n가상 청년 (sample_only)"))
        self.assertEqual(short["status"], ck.STATUS_UNSTRUCTURED)
        self.assertEqual(short["recommended_method"], "single_chunk")
        self.assertFalse(short["llm_needed"])

        long_text = "■ 지원대상\n" + "\n".join(f"가상 설명 문장 {i}번입니다 (sample_only)" for i in range(60))
        long = ck.analyze_structure(_sample_document(long_text))
        self.assertEqual(long["status"], ck.STATUS_UNSTRUCTURED)
        self.assertEqual(long["recommended_method"], "llm_sections")
        self.assertTrue(long["llm_needed"])
        self.assertEqual(long["llm_candidate_regions"][0]["title"], "(문서 전체)")

    def test_unclear_document_without_markers(self):
        text = "\n".join(f"가상 안내 문단 {i}번은 특별한 제목 없이 이어집니다 (sample_only)" for i in range(40))
        analysis = ck.analyze_structure(_sample_document(text))
        self.assertEqual(analysis["status"], ck.STATUS_UNSTRUCTURED)
        self.assertEqual(analysis["recommended_method"], "llm_sections")

    def test_partial_only_for_long_section_without_rule_split_point(self):
        one_long_line = "가상 문의처 목록 " * 60          # 줄바꿈 없는 600자 이상 한 줄
        # 첫 섹션은 MIN_PIECE_CHARS(30자) 이상이어야 다음 섹션에 합쳐지지 않는다.
        text = (f"■ 지원대상\n만 19세 이상 가상 청년 (sample_only) 설명 문장\n■ 문의처\n{one_long_line}{one_long_line}\n"
                "■ 신청방법\n가상 온라인 신청 (sample_only) 설명 문장입니다")
        analysis = ck.analyze_structure(_sample_document(text))
        self.assertEqual(analysis["status"], ck.STATUS_PARTIAL)
        self.assertEqual(analysis["recommended_method"], "rule_based_with_llm_regions")
        self.assertEqual([region["title"] for region in analysis["llm_candidate_regions"]], ["■ 문의처"])

        # 같은 길이라도 줄바꿈이 있으면 규칙으로 자를 수 있으므로 LLM 후보가 아니다.
        with_lines = text.replace("목록 가상", "목록\n가상")
        self.assertEqual(ck.analyze_structure(_sample_document(with_lines))["status"], ck.STATUS_STRUCTURED)

    def test_samples_have_no_missing_or_overlap_and_document_is_unchanged(self):
        samples = [
            self._three_sections(["■ 지원대상", "■ 지원내용", "■ 신청방법"]),
            "목차\n1. 지원대상\n2. 지원내용\n" + self._three_sections(["1. 지원대상", "2. 지원내용", "3. 신청방법"]),
            "--- [Page 1] ---\n1 개요\n■ 지원대상\n가상 청년\n--- [Page 2] ---\n■ 신청방법\n가상 온라인",
        ]
        for index, text in enumerate(samples):
            with self.subTest(sample=index):
                document = _sample_document(text, source_type="pdf" if "[Page" in text else "html")
                before = dict(document)
                analysis = ck.analyze_structure(document)
                self.assertEqual(document, before)
                self.assertEqual(analysis["coverage"]["missing_chars"], 0)
                self.assertEqual(analysis["coverage"]["unintended_overlap_chars"], 0)
                self.assertEqual(analysis["coverage"]["excluded_in_chunks"], 0)
                self.assertEqual(analysis["coverage"]["preserved_ratio"], 1.0)

    def test_score_heading_detection_counts_misses_and_false_alarms(self):
        analysis = {"headings": [{"line": "■ A"}, {"line": "■ 잘못"}]}
        score = ck.score_heading_detection(analysis, ["■ A", "■ B"])
        self.assertEqual((score["precision"], score["recall"]), (0.5, 0.5))


# ---------------------------------------------------------------------------
# 5) 구조 인식 — 실제 문서 246건 (정답 경계가 없으므로 정확도는 계산하지 않는다)
# ---------------------------------------------------------------------------
# B1/B2 출력 해시: 구조 인식 추가 전(2026-11-05)에 기록한 값. B1/B2를 일부러 바꿀 때만 갱신한다.
B1_OUTPUT_SHA256 = ("13b7927e8d0780098ca72367befe3dc824afefca324d12484c015e996055d2c7",
                    "91d9753889ba15af748bba2cc01bad2b5a6a10945883147d1bd8c3bbe3016fe1")
B2_OUTPUT_SHA256 = ("534d0275b29498998c84db7d7d1a86e4429acad17cc39221a0c1f86f9dfae67a",
                    "6dbf5cf2e3b9d5e10c8f2390c3fa99b39d8762ada0ec0071c38bea451c7e9916")
ALLOWED_STATUSES = {"structured", "partial", "unstructured"}
ALLOWED_RECOMMENDED = {"rule_based", "rule_based_with_llm_regions", "llm_sections", "single_chunk"}
ALLOWED_REJECT_REASONS = {"note_marker", "empty_title", "label_with_body", "toc_entry", "too_long", "sentence",
                          "ui_label", "list_or_toc", "list_end", "sub_item", "duplicate_title"}


def _output_sha256(chunks: list, reports: list) -> tuple[str, str]:
    import hashlib
    import json
    return tuple(hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                 for value in (chunks, reports))


class TestStructureRealDocuments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = hd.load_documents()
        cls.analyses = [ck.analyze_structure(document) for document in cls.documents]

    def test_b1_b2_outputs_unchanged(self):
        self.assertEqual(_output_sha256(*ck.chunk_documents(self.documents)), B1_OUTPUT_SHA256)
        self.assertEqual(_output_sha256(*ck.chunk_documents(self.documents, strategy="B2")), B2_OUTPUT_SHA256)

    def test_every_document_gets_a_valid_decision(self):
        self.assertEqual(len(self.analyses), hd.EXPECTED_DOCUMENT_COUNT)
        for analysis in self.analyses:
            with self.subTest(document=analysis["document_id"]):
                self.assertIn(analysis["status"], ALLOWED_STATUSES)
                self.assertIn(analysis["recommended_method"], ALLOWED_RECOMMENDED)
                self.assertTrue(analysis["status_reasons"])
                self.assertEqual(analysis["rule_set_version"], ck.STRUCTURE_RULE_SET_VERSION)
                self.assertEqual(analysis["llm_needed"], bool(analysis["llm_candidate_regions"]))

    def test_sections_preserve_original_without_missing_or_overlap(self):
        for document, analysis in zip(self.documents, self.analyses):
            with self.subTest(document=document["document_id"]):
                coverage = analysis["coverage"]
                self.assertEqual(coverage["missing_chars"], 0)
                self.assertEqual(coverage["unintended_overlap_chars"], 0)
                self.assertEqual(coverage["excluded_in_chunks"], 0)
                self.assertEqual(coverage["preserved_ratio"], 1.0)

    def test_heading_positions_point_to_original_lines(self):
        for document, analysis in zip(self.documents, self.analyses):
            starts = [heading["start"] for heading in analysis["headings"]]
            with self.subTest(document=document["document_id"]):
                self.assertEqual(starts, sorted(set(starts)))
                for item in analysis["headings"] + analysis["rejected_candidates"]:
                    self.assertEqual(document["text"][item["start"]:item["end"]], item["line"])
                for candidate in analysis["rejected_candidates"]:
                    self.assertIn(candidate["reject_reason"], ALLOWED_REJECT_REASONS)

    def test_api_documents_use_field_structure(self):
        for document, analysis in zip(self.documents, self.analyses):
            if document["source_type"] != "api":
                continue
            fields = [m.group(1) for m in ck.API_FIELD_PATTERN.finditer(document["text"])]
            with self.subTest(document=document["document_id"]):
                self.assertEqual(analysis["structure_source"], "api_field")
                self.assertEqual([h["title"] for h in analysis["headings"]],
                                 [name for name in fields if name not in ck.METADATA_FIELDS])

    def test_lh001_all_six_chapter_headings_recognized(self):
        # 장 제목 6개는 이전 단계에서 원문을 읽어 확인한 값이다. (수준 1 제목만 확인, 전체 정확도가 아님)
        analysis = next(a for a in self.analyses if a["document_id"] == "DOC_LH_001")
        chapters = [h["line"] for h in analysis["headings"] if h["rule"] == "chapter_line"]
        self.assertEqual(chapters, ["1 공급개요", "2 입주자격 및 입주자 선정기준", "3 공급일정 및 신청방법 등",
                                    "4 서류제출 대상자 제출서류 안내", "5 기타 유의사항", "6 문의처 및 서류제출 장소"])

    def test_notes_are_never_headings(self):
        for analysis in self.analyses:
            for heading in analysis["headings"]:
                with self.subTest(document=analysis["document_id"], line=heading["line"]):
                    self.assertIsNone(ck.NOTE_LINE_PATTERN.match(heading["line"]))

    def test_b1_length_fallback_html_pages_are_recognized_by_rules(self):
        # B1에서 제목을 못 찾은 주택도시기금·고용24 화면은 기호 없는 섹션 이름 줄로 구조가 인식되어 LLM이 필요 없다.
        by_id = {a["document_id"]: a for a in self.analyses}
        for document_id in ("DOC_NHUF_004", "DOC_NHUF_006", "DOC_NHUF_009", "DOC_NHUF_010", "DOC_WORK24_001"):
            with self.subTest(document=document_id):
                self.assertEqual(by_id[document_id]["status"], ck.STATUS_STRUCTURED)
                self.assertFalse(by_id[document_id]["llm_needed"])
                self.assertIn("plain_vocabulary", by_id[document_id]["metrics"]["rules_used"])

    def test_single_chunk_only_for_short_documents(self):
        for document, analysis in zip(self.documents, self.analyses):
            if analysis["recommended_method"] == "single_chunk":
                with self.subTest(document=document["document_id"]):
                    self.assertLessEqual(len(document["text"].strip()), ck.CHUNK_SIZE)

    def test_deterministic(self):
        again = [ck.analyze_structure(document) for document in self.documents]
        self.assertEqual(again, self.analyses)


if __name__ == "__main__":
    unittest.main(verbosity=2)
