"""
C1(LLM 보조 청킹) 섹션 제목 보존 회귀 테스트 — DB·LLM·네트워크 없이 실행

실행 방법 (프로젝트 루트에서):
    .venv\\Scripts\\python.exe -m unittest tests/unit/test_c1_section_titles.py -v

배경 (2026-11-06)
- C1은 30자 미만 짧은 섹션을 다음 섹션 앞에 붙인다(MIN_PIECE_CHARS). 이전에는 앞 섹션 제목이 사라졌다.
  예) DOC_NHUF_003: "대출금리" 섹션이 "대출한도" 청크에 들어가 section_title이 "대출한도"만 남음
- 수정: C1 전용 short_piece_policy="forward_keep_titles" → 두 제목을 section_titles·section_title에 모두 남긴다.
  관련 없는 의미 그룹(예: 자격 + 지원내용)은 붙이지 않는다. B1·B2·B3는 기존 "forward" 그대로.
- 실제 OpenAI 응답(DOC_NHUF_003 창 0, gpt-5.6-terra, 2026-11-06)의 제안 21개를 고정 입력으로 쓴다.
  (제목·시작 문자열만이며, 본문은 원문에서 자른다. 응답 원본: docs/development/experiments/desktop_20261106/)
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import chunking as ck  # noqa: E402
from database import embedding as em  # noqa: E402
from database import handoff_data as hd  # noqa: E402
from database import llm_chunking as lc  # noqa: E402
from database.qdrant import store  # noqa: E402

# 실제 응답의 제안 21개 (title == start_text). 창 0 = 원문 [0, 5891)
NHUF_003_WINDOW_0 = (0, 5891)
NHUF_003_PROPOSAL_TITLES = (
    "대출대상", "대출금리", "대출한도", "대출기간", "대출 대상", "신청시기", "□ 보증 신청 기한", "대상주택",
    "대출한도", "대출금리", "금리우대", "추가우대금리(①,② 중복 적용 가능)", "이용기간", "상환방법", "담보취득",
    "보증 종류별 안내", "대출금지급방식", "대출취급영업점", "중도상환수수료", "대출계약 철회", "유의사항",
)
SAMPLE_HTML = {"document_id": "DOC_SAMPLE_903", "policy_id": "POL_SAMPLE_903", "source_type": "html",
               "source_url": "https://example.invalid/sample", "title": "가상 안내 화면"}


def _proposals(text: str, titles):
    return [{"title": t, "start_text": t, "page": None, "window_start": 0, "window_end": len(text)} for t in titles]


class TestShortSectionPolicySample(unittest.TestCase):
    """가상 예제 (sample_only: 실제 정책 내용이 아님)"""

    def test_short_section_titles_are_kept_together(self):
        text = "대출금리\n연 1.0%\n대출한도\n가상 한도는 일정 금액 이내이며 세부 기준은 별도로 정합니다.\n"
        document = {**SAMPLE_HTML, "text": text}
        chunks, report = lc.chunk_document_with_sections(document, _proposals(text, ("대출금리", "대출한도")))
        self.assertFalse(report["used_fallback"], report["fallback_reasons"])
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["section_titles"], ["대출금리", "대출한도"])
        self.assertEqual(chunks[0]["section_title"], "대출금리 + 대출한도")
        self.assertEqual(chunks[0]["text"], text[chunks[0]["char_start"]:chunks[0]["char_end"]])

    def test_unrelated_groups_are_not_merged(self):
        # 자격(대출대상) 짧은 섹션은 지원내용(대출금리) 섹션에 붙이지 않는다
        text = "대출대상\n가상 무주택자\n대출금리\n가상 금리는 연 일정 비율이며 세부 기준은 별도로 정합니다.\n"
        segments, _excluded = ck._segments_from_starts(
            text, [(0, "대출대상", 2), (text.index("대출금리"), "대출금리", 2)], [],
            short_piece_policy="forward_keep_titles")
        self.assertEqual([segment["titles"] for segment in segments], [["대출대상"], ["대출금리"]])
        self.assertFalse(ck._short_section_merge_allowed("대출대상", "대출금리"))
        self.assertTrue(ck._short_section_merge_allowed("대출금리", "대출한도"))
        self.assertTrue(ck._short_section_merge_allowed("상환방법", "담보취득"))       # 사전에 없음 → 허용, 두 제목 유지

    def test_untitled_short_piece_adds_no_title(self):
        text = ("--- [Page 1] ---\n■ 신청자격\n가상 자격 조건 설명 줄입니다. 추가 설명입니다.\n"
                "--- [Page 2] ---\n- 2 -\n■ 신청방법\n가상 신청 방법 설명 줄입니다. 추가 설명입니다.\n")
        markers = ck.find_page_markers(text)
        starts = [(text.index("■ 신청자격"), "■ 신청자격", 2), (text.index("■ 신청방법"), "■ 신청방법", 2)]
        segments, _ = ck._segments_from_starts(text, starts, markers, short_piece_policy="forward_keep_titles")
        page2 = [segment for segment in segments if segment["page"] == 2][0]
        self.assertEqual(page2["titles"], ["■ 신청방법"])                         # 쪽번호 조각의 "(이어짐)" 제목은 더하지 않음
        self.assertTrue(text[page2["start"]:page2["end"]].startswith("- 2 -"))

    def test_default_policy_is_unchanged_and_unknown_policy_rejected(self):
        text = "대출금리\n연 1.0%\n대출한도\n가상 한도는 일정 금액 이내입니다.\n"
        starts = [(0, "대출금리", 2), (text.index("대출한도"), "대출한도", 2)]
        segments, _ = ck._segments_from_starts(text, starts, [])
        self.assertEqual([segment["title"] for segment in segments], ["대출한도"])
        self.assertNotIn("titles", segments[0])
        with self.assertRaises(ValueError):
            ck._segments_from_starts(text, starts, [], short_piece_policy="merge_all")

    def test_b_versions_have_no_section_titles_key(self):
        document = {**SAMPLE_HTML, "text": "가상 안내\n대출대상\n가상 줄입니다.\n대출금리\n가상 줄입니다.\n"}
        for chunk in ck.chunk_document(document)[0] + ck.chunk_document_b3(document)[0]:
            self.assertNotIn("section_titles", chunk)


class TestFormHeaderAtPageStart(unittest.TestCase):
    """페이지 첫머리의 새 서식 머리말([별지 제N호서식])이 앞 페이지 섹션 제목을 물려받지 않는지 (sample_only)"""

    PAGE1 = "--- [Page 1] ---\n■ 가상 안내\n가상 안내 본문 첫째 줄입니다. 추가 설명 줄입니다.\n"
    FORM = ("--- [Page 2] ---\n- 2 -\n■  가상 시행규칙 [별지 제1호서식] <가상 개정>\n가상 정보 제공 동의서\n"
            "가상 동의서 본문 첫째 줄입니다. 추가 설명 줄입니다.\n")
    PLAIN = ("--- [Page 2] ---\n- 2 -\n가상 안내 본문이 이어지는 줄입니다. 계속되는 설명입니다.\n가상 정보 제공 동의서\n"
             "가상 동의서 본문 첫째 줄입니다. 추가 설명 줄입니다.\n")

    def _segments(self, text, policy="forward_keep_titles"):
        starts = [(text.index("■ 가상 안내"), "■ 가상 안내", 2), (text.index("가상 정보 제공 동의서"), "가상 정보 제공 동의서", 2)]
        segments, _ = ck._segments_from_starts(text, starts, ck.find_page_markers(text), short_piece_policy=policy)
        return [segment for segment in segments if segment["page"] == 2]

    def test_form_header_joins_next_section_without_inventing_title(self):
        text = self.PAGE1 + self.FORM
        page2 = self._segments(text)
        self.assertEqual(len(page2), 1)
        self.assertEqual(page2[0]["titles"], ["가상 정보 제공 동의서"])
        self.assertTrue(text[page2[0]["start"]:page2[0]["end"]].startswith("- 2 -"))

    def test_plain_continuation_keeps_previous_title(self):
        page2 = self._segments(self.PAGE1 + self.PLAIN)
        self.assertEqual(page2[0]["titles"], ["■ 가상 안내 (이어짐)"])   # 페이지가 바뀐 것만으로는 끊지 않는다

    def test_form_header_without_following_section_is_not_moved(self):
        text = self.PAGE1 + self.FORM.replace("가상 정보 제공 동의서\n", "")
        starts = [(text.index("■ 가상 안내"), "■ 가상 안내", 2)]
        segments, _ = ck._segments_from_starts(text, starts, ck.find_page_markers(text),
                                               short_piece_policy="forward_keep_titles")
        self.assertEqual([s["titles"] for s in segments if s["page"] == 2], [["■ 가상 안내 (이어짐)"]])

    def test_b_versions_keep_previous_behaviour(self):
        page2 = self._segments(self.PAGE1 + self.FORM, policy="forward")
        self.assertEqual(page2[0]["title"], "■ 가상 안내 (이어짐)")
        self.assertNotIn("titles", page2[0])

    def test_real_lh_001_page_22_form(self):
        document = {d["document_id"]: d for d in hd.load_documents()}["DOC_LH_001"]
        window = (29802, 35657)          # 실제 응답 창 6 (2026-11-06, gpt-5.6-terra)
        proposals = [
            {"title": "나. 개인정보를 제공받는 자의 개인정보 이용 목적, 제공항목, 보유·이용기간",
             "start_text": " 나. 개인정보를 제공받는 자의 개인정보 이용 목적, 제공항목, 보유·이용기간", "page": 21},
            {"title": "금융정보 등(금융·신용·보험정보) 제공 동의서", "start_text": "금융정보 등(금융·신용·보험정보) 제공 동의서",
             "page": 22}]
        proposals = [{**p, "window_start": window[0], "window_end": window[1]} for p in proposals]
        chunks, report = lc.chunk_document_with_sections(document, proposals)
        self.assertFalse(report["used_fallback"], report["fallback_reasons"])
        self.assertTrue(report["coverage"]["ok"])
        form = [chunk for chunk in chunks if "[별지 제4호서식]" in chunk["text"]][0]
        self.assertEqual(form["page_start"], 22)
        self.assertEqual(form["section_titles"][0], "금융정보 등(금융·신용·보험정보) 제공 동의서")
        self.assertFalse(any("(이어짐)" in title for title in form["section_titles"]))
        self.assertEqual(form["text"], document["text"][form["char_start"]:form["char_end"]])


class TestSavedOpenAIResponse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = {d["document_id"]: d for d in hd.load_documents()}["DOC_NHUF_003"]
        start, end = NHUF_003_WINDOW_0
        cls.proposals = [{"title": t, "start_text": t, "page": None, "window_start": start, "window_end": end}
                         for t in NHUF_003_PROPOSAL_TITLES]
        cls.chunks, cls.report = lc.chunk_document_with_sections(cls.document, cls.proposals)

    def _chunk_containing(self, phrase: str):
        return [chunk for chunk in self.chunks if phrase in chunk["text"]][0]

    def test_all_21_proposals_accepted_and_text_is_original(self):
        self.assertFalse(self.report["used_fallback"], self.report["fallback_reasons"])
        self.assertEqual(len(self.report["accepted_sections"]), 21)
        self.assertEqual(self.report["issues"], [])
        self.assertTrue(self.report["coverage"]["ok"], self.report["coverage"])
        for chunk in self.chunks:
            self.assertEqual(chunk["text"], self.document["text"][chunk["char_start"]:chunk["char_end"]])

    def test_merged_short_sections_keep_both_titles(self):
        expected = {"연 1.9%": ["대출금리", "대출한도"],
                    "일시상환 또는 혼합상환": ["상환방법", "담보취득"],
                    "14일 이내에 대출계약 철회": ["중도상환수수료", "대출계약 철회"]}
        for phrase, titles in expected.items():
            chunk = self._chunk_containing(phrase)
            with self.subTest(phrase=phrase):
                self.assertEqual(chunk["section_titles"], titles)
                self.assertEqual(chunk["section_title"], " + ".join(titles))

    def test_eligibility_body_stays_under_loan_target(self):
        for phrase in ("(중복대출 금지)", "사업소득자는 사업자등록증"):
            with self.subTest(phrase=phrase):
                self.assertEqual(self._chunk_containing(phrase)["section_titles"], ["대출 대상"])
        self.assertNotIn("대출기간", self._chunk_containing("(중복대출 금지)")["section_title"])

    def test_every_title_appears_in_its_chunk_or_section(self):
        # 단일 청크 섹션은 제목 줄이 본문 첫머리에 있어야 한다 (이어지는 청크는 overlap 때문에 제외)
        for chunk in self.chunks:
            if chunk["overlap_prev_chars"] or chunk["section_titles"] == ["(첫 제목 이전)"]:
                continue
            for title in chunk["section_titles"]:
                with self.subTest(chunk=chunk["chunk_id"], title=title):
                    self.assertTrue(em.starts_a_line(chunk["text"], title))

    def test_embedding_header_and_payload_keep_titles(self):
        chunk = self._chunk_containing("연 1.9%")
        info = em.describe_embedding_input(chunk, {"policy_name": "가상 정책명"}, "policy_section_body_v2")
        self.assertEqual(sorted(info["omitted_title_elements"]), ["대출금리", "대출한도"])   # 둘 다 본문 줄에 있음
        continuation = self._chunk_containing("사업소득자는 사업자등록증")
        info = em.describe_embedding_input(continuation, {"policy_name": "가상 정책명"}, "policy_section_body_v2")
        self.assertEqual(info["section_line"], "대출 대상")                                   # 본문에 없으면 머리말 유지
        policy_meta = hd.load_policy_metadata()[chunk["policy_id"]]
        payload = store.build_point_payload(
            chunk, policy_meta=policy_meta, content_hash=self.document["content_hash"],
            run_info={"embedding_model": "sample", "embedding_dim": 3, "embedding_input_rule": "policy_section_body_v2",
                      "index_run_id": "RUN_SAMPLE"})
        self.assertEqual(payload["section_titles"], ["대출금리", "대출한도"])
        self.assertEqual(payload["section_title"], "대출금리 + 대출한도")


if __name__ == "__main__":
    unittest.main(verbosity=2)
