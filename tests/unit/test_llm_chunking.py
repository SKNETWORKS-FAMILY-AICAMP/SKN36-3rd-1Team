"""
LLM 보조 청킹(C1 후보) 단위 테스트 — 실제 LLM·네트워크 없이 가짜 proposer로 실행

실행 방법 (프로젝트 루트에서):
    .venv\\Scripts\\python.exe -m unittest tests/unit/test_llm_chunking.py -v

검사 내용
- 제안 검증: 원문에 없는 시작 문자열·제목, 반복 제목, 경계 역전, 줄 중간 시작, 페이지 표지·페이지 불일치
- 청크 본문은 항상 원문 슬라이스 (LLM 응답 문장이 본문에 들어가지 않음)
- 실패(예외·JSON 오류·검증 부족) 시 B3 결과로 대체하고 사유를 기록
- 실제 문서(DOC_NHUF_003·DOC_LH_001)에 "구조 인식 제목을 그대로 돌려주는 가짜 LLM"을 써서 원문 보존·페이지 매핑 확인
  (가짜 LLM 결과는 실제 LLM 품질을 뜻하지 않는다)
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import chunking as ck  # noqa: E402
from database import handoff_data as hd  # noqa: E402
from database import llm_chunking as lc  # noqa: E402

# 가상 문서 (sample_only: 실제 정책 내용이 아님)
SAMPLE_PDF = {
    "document_id": "DOC_SAMPLE_901", "policy_id": "POL_SAMPLE_901", "source_type": "pdf",
    "source_url": "https://example.invalid/sample", "title": "가상 공고문",
    "text": ("--- [Page 1] ---\n가상 공고문\n■ 신청자격\n가상 자격 조건 첫째 줄입니다.\n가상 자격 조건 둘째 줄입니다.\n"
             "--- [Page 2] ---\n■ 신청방법\n가상 신청 방법 설명 줄입니다.\n가상 접수처 안내 줄입니다.\n"
             "■ 유의사항\n가상 유의 사항 줄입니다.\n"),
}
SAMPLE_HTML = {**SAMPLE_PDF, "document_id": "DOC_SAMPLE_902", "source_type": "html",
               "text": "가상 안내\n지원대상\n가상 대상 설명 줄입니다.\n지원내용\n가상 내용 설명 줄입니다.\n"}


def _proposal(title, start_text, page=None, document=SAMPLE_PDF):
    return {"title": title, "start_text": start_text, "page": page,
            "window_start": 0, "window_end": len(document["text"])}


def heading_echo_proposer(document):
    """구조 인식(analyze_structure) 제목을 창별로 돌려주는 가짜 LLM. 프롬프트 안의 창 원문으로 창을 알아낸다."""
    headings = ck.analyze_structure(document)["headings"]
    markers = ck.find_page_markers(document["text"]) if document["source_type"] == "pdf" else []

    def propose(prompt: str) -> str:
        window_text = prompt.split("공고문:\n", 1)[1][:-1]
        window_start = document["text"].find(window_text)
        window_end = window_start + len(window_text)
        sections = []
        for heading in headings:
            if window_start <= heading["start"] < window_end:
                page = None
                for start, _end, number in markers:
                    if start <= heading["start"]:
                        page = number
                sections.append({"title": heading["title"], "start_text": heading["line"][:20], "page": page})
        return json.dumps({"sections": sections}, ensure_ascii=False)
    return propose


class TestValidateProposals(unittest.TestCase):
    def _issues(self, proposals, document=SAMPLE_PDF):
        return [item["issue"] for item in lc.validate_proposals(document, proposals)["issues"]]

    def test_valid_proposals_snap_to_line_start_and_keep_order(self):
        result = lc.validate_proposals(SAMPLE_PDF, [_proposal("신청자격", "신청자격", 1),
                                                    _proposal("신청방법", "■ 신청방법", 2)])
        self.assertEqual(result["issues"], [])
        text = SAMPLE_PDF["text"]
        self.assertEqual([text[position:position + 6] for position, _t, _l in result["starts"]], ["■ 신청자격", "■ 신청방법"])

    def test_each_problem_is_detected(self):
        cases = {
            "invalid_format": [{"title": 3, "start_text": None}],
            "start_text_too_short": [_proposal("신청자격", "■ ")],
            "start_text_not_found": [_proposal("신청자격", "신청 자격 요건 요약")],
            "title_not_in_source": [_proposal("소득 기준 요약", "■ 신청자격")],
            "mid_line_start": [_proposal("조건", "자격 조건 첫째")],
            "inside_excluded_span": [_proposal("Page", "--- [Page 2] ---")],
            "page_mismatch": [_proposal("신청방법", "■ 신청방법", 1)],
        }
        for issue, proposals in cases.items():
            with self.subTest(issue=issue):
                self.assertEqual(self._issues(proposals), [issue])

    def test_same_position_is_accepted_once(self):
        # 같은 원문 위치(같은 줄)를 다시 제안 → 제목이 같든 다르든 하나만 인정 (duplicate_start)
        self.assertEqual(self._issues([_proposal("신청방법", "■ 신청방법"), _proposal("신청방법", "■ 신청방법")]),
                         ["duplicate_start"])
        self.assertEqual(self._issues([_proposal("신청방법", "■ 신청방법"), _proposal("신청 방법", "신청방법")]),
                         ["duplicate_start"])
        same_line = {**SAMPLE_HTML, "text": "■ 1. 지원대상\n가상 줄입니다.\n"}
        self.assertEqual(self._issues([_proposal("지원대상", "■ 1. 지원", document=same_line),
                                       _proposal("1. 지원대상", "1. 지원대상", document=same_line)], same_line),
                         ["duplicate_start"])

    def test_order_inversion_is_still_blocked(self):
        self.assertEqual(self._issues([_proposal("유의사항", "■ 유의사항"), _proposal("신청자격", "■ 신청자격")]),
                         ["boundary_inversion"])

    def test_same_title_at_different_positions_is_accepted_each(self):
        repeated = {**SAMPLE_HTML, "text": "지원대상\n가상 줄입니다.\n지원대상\n가상 다른 줄입니다.\n"}
        result = lc.validate_proposals(repeated, [_proposal("지원대상", "지원대상", document=repeated),
                                                  _proposal("지원대상", "지원대상", document=repeated)])
        self.assertEqual(result["issues"], [])
        self.assertEqual([item["start"] for item in result["accepted"]], [0, repeated["text"].index("지원대상", 1)])
        self.assertEqual([item["repeated_title"] for item in result["accepted"]], [False, True])

    def test_summary_box_and_body_share_titles(self):
        # 요약 상자(대출대상·대출한도)와 본문(대출 대상·대출한도)에 같은 제목이 있는 화면 (sample_only)
        text = ("대출대상\n가상 요약 대상\n대출한도\n가상 요약 한도\n"
                "대출 대상\n0. (가상 조건) 첫째 조건 설명\n1. (가상 조건) 둘째 조건 설명\n"
                "대상주택\n가상 주택 설명\n대출한도\n1. 가상 한도 상세 설명\n")
        document = {**SAMPLE_HTML, "text": text}
        proposals = [_proposal(t, s, document=document) for t, s in
                     (("대출대상", "대출대상"), ("대출한도", "대출한도"), ("대출 대상", "대출 대상"),
                      ("대상주택", "대상주택"), ("대출한도", "대출한도"))]
        result = lc.validate_proposals(document, proposals)
        self.assertEqual(result["issues"], [])
        self.assertEqual([item["start"] for item in result["accepted"]],
                         [0, text.index("대출한도"), text.index("대출 대상"), text.index("대상주택"), text.rindex("대출한도")])
        chunks, report = lc.chunk_document_with_sections(document, proposals)
        self.assertFalse(report["used_fallback"])
        condition_chunk = [chunk for chunk in chunks if "첫째 조건" in chunk["text"]][0]
        self.assertTrue(condition_chunk["section_title"].startswith("대출 대상"))
        detail_chunk = [chunk for chunk in chunks if "한도 상세" in chunk["text"]][0]
        self.assertIn("대출한도", detail_chunk["section_title"])

    def test_repeated_start_text_skips_invalid_first_match(self):
        # 같은 start_text가 여러 번: 첫 일치는 줄 중간("…의 지원내용")이라 부적합 → 다음 줄 시작 일치를 고른다
        text = "가상 안내\n이 문서의 지원내용 요약입니다.\n지원대상\n가상 대상 줄\n지원내용\n가상 내용 줄\n"
        document = {**SAMPLE_HTML, "text": text}
        result = lc.validate_proposals(document, [_proposal("지원대상", "지원대상", document=document),
                                                  _proposal("지원내용", "지원내용", document=document)])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["accepted"][1]["start"], text.rindex("지원내용"))
        self.assertEqual(result["accepted"][1]["occurrences_in_window"], 2)
        # 줄 시작 일치가 하나도 없으면 여전히 거부한다
        only_mid = {**SAMPLE_HTML, "text": "가상 안내\n이 문서의 지원내용 요약입니다.\n"}
        self.assertEqual(self._issues([_proposal("지원내용", "지원내용", document=only_mid)], only_mid),
                         ["mid_line_start"])

    def test_page_selects_matching_occurrence_and_blocks_wrong_page(self):
        text = ("--- [Page 1] ---\n■ 유의사항\n가상 1쪽 유의 줄입니다.\n"
                "--- [Page 2] ---\n■ 유의사항\n가상 2쪽 유의 줄입니다.\n")
        document = {**SAMPLE_PDF, "text": text}
        result = lc.validate_proposals(document, [_proposal("유의사항", "■ 유의사항", 2, document)])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["accepted"][0]["start"], text.rindex("■ 유의사항"))
        self.assertEqual(result["accepted"][0]["page"], 2)
        self.assertEqual(self._issues([_proposal("유의사항", "■ 유의사항", 3, document)], document), ["page_mismatch"])

    def test_window_range_limits_matches(self):
        text = SAMPLE_PDF["text"]
        limited = {"title": "유의사항", "start_text": "■ 유의사항", "page": None,
                   "window_start": 0, "window_end": text.index("■ 유의사항")}
        self.assertEqual(self._issues([limited]), ["start_text_not_found"])


class TestChunkWithSections(unittest.TestCase):
    def test_chunks_are_original_slices_and_titles_are_metadata(self):
        proposals = [_proposal("신청자격", "■ 신청자격", 1), _proposal("신청방법", "■ 신청방법", 2),
                     _proposal("유의사항", "■ 유의사항", 2)]
        chunks, report = lc.chunk_document_with_sections(SAMPLE_PDF, proposals)
        self.assertFalse(report["used_fallback"], report["fallback_reasons"])
        self.assertEqual(report["chunking_version"], "C1")
        self.assertTrue(report["coverage"]["ok"])
        for chunk in chunks:
            self.assertEqual(chunk["text"], SAMPLE_PDF["text"][chunk["char_start"]:chunk["char_end"]])
            self.assertTrue(chunk["chunk_id"].startswith("CHK_C1_"))
            self.assertEqual(chunk["boundary_method"], "llm_section")
        self.assertEqual(sorted({chunk["page_start"] for chunk in chunks}), [1, 2])

    def test_same_proposals_give_same_chunks(self):
        proposals = [_proposal("신청자격", "■ 신청자격", 1), _proposal("신청방법", "■ 신청방법", 2)]
        self.assertEqual(lc.chunk_document_with_sections(SAMPLE_PDF, proposals),
                         lc.chunk_document_with_sections(SAMPLE_PDF, proposals))

    def test_too_few_valid_sections_falls_back_to_b3(self):
        chunks, report = lc.chunk_document_with_sections(SAMPLE_PDF, [_proposal("신청자격", "■ 신청자격", 1),
                                                                      _proposal("없는 제목", "없는 시작 문장", 2)])
        self.assertTrue(report["used_fallback"])
        self.assertEqual(report["chunking_version"], "B3")
        self.assertEqual(chunks, ck.chunk_document_b3(SAMPLE_PDF)[0])
        self.assertIn("start_text_not_found", [item["issue"] for item in report["issues"]])

    def test_api_document_is_not_a_target(self):
        api_document = {**SAMPLE_HTML, "source_type": "api", "text": "[지원대상]\n가상 대상\n[지원내용]\n가상 내용\n"}
        _chunks, report = lc.chunk_document_with_sections(api_document, [])
        self.assertTrue(report["used_fallback"])
        self.assertIn("대상 문서 유형 아님: api", report["fallback_reasons"])


class TestProposerFailures(unittest.TestCase):
    def test_exception_and_bad_json_fall_back_without_crashing(self):
        def broken(_prompt):
            raise ConnectionError("가상 접속 실패")
        for proposer, issue in ((broken, "proposer_failed"), (lambda _p: "not json", "proposer_failed"),
                                (lambda _p: json.dumps({"items": []}), "proposer_failed")):
            with self.subTest(issue=issue):
                chunks, report = lc.chunk_document_llm(SAMPLE_HTML, proposer)
                self.assertTrue(report["used_fallback"])
                self.assertEqual(report["issues"][0]["issue"], issue)
                self.assertEqual(chunks, ck.chunk_document_b3(SAMPLE_HTML)[0])
                self.assertIn("error", report["llm_calls"][0])

    def test_rewritten_text_from_llm_never_becomes_chunk_text(self):
        def rewriting(_prompt):
            return json.dumps({"sections": [{"title": "지원대상", "start_text": "지원 대상은 다음과 같다"},
                                            {"title": "지원내용", "start_text": "지원내용"}]}, ensure_ascii=False)
        chunks, report = lc.chunk_document_llm(SAMPLE_HTML, rewriting)
        self.assertTrue(report["used_fallback"])
        self.assertTrue(all("다음과 같다" not in chunk["text"] for chunk in chunks))


class TestWindowsAndPrompt(unittest.TestCase):
    def test_windows_cover_text_and_split_pdf_at_page_markers(self):
        documents = {document["document_id"]: document for document in hd.load_documents()}
        for document_id in ("DOC_LH_001", "DOC_NHUF_003", "DOC_NHUF_005"):
            document = documents[document_id]
            windows = lc.make_windows(document)
            with self.subTest(document=document_id):
                self.assertEqual(windows[0]["start"], 0)
                self.assertEqual(windows[-1]["end"], len(document["text"]))
                for left, right in zip(windows, windows[1:]):
                    self.assertEqual(left["end"], right["start"])
                if document["source_type"] == "pdf":
                    marker_starts = {start for start, _e, _p in ck.find_page_markers(document["text"])}
                    self.assertTrue(all(window["start"] in marker_starts for window in windows))
                    self.assertEqual(sum(len(window["pages"]) for window in windows), len(marker_starts))

    def test_prompt_contains_window_text_verbatim(self):
        prompt = lc.build_section_prompt(SAMPLE_PDF["text"])
        self.assertIn(SAMPLE_PDF["text"], prompt)
        self.assertIn("start_text", prompt)


class TestRealDocumentsWithEchoProposer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = {document["document_id"]: document for document in hd.load_documents()}

    def test_c1_pipeline_preserves_text_and_pages(self):
        for document_id in ("DOC_NHUF_003", "DOC_NHUF_005", "DOC_LH_001"):
            document = self.documents[document_id]
            chunks, report = lc.chunk_document_llm(document, heading_echo_proposer(document))
            with self.subTest(document=document_id):
                self.assertFalse(report["used_fallback"], report["fallback_reasons"])
                self.assertTrue(report["coverage"]["ok"], report["coverage"])
                self.assertEqual(len({chunk["chunk_id"] for chunk in chunks}), len(chunks))
                for chunk in chunks:
                    self.assertEqual(chunk["text"], document["text"][chunk["char_start"]:chunk["char_end"]])
                    self.assertLessEqual(len(chunk["text"]), ck.CHUNK_SIZE)
                if document["source_type"] == "pdf":
                    markers = ck.find_page_markers(document["text"])
                    for chunk in chunks:
                        prior = [page for start, _e, page in markers if start <= chunk["char_start"]]
                        self.assertEqual(chunk["page_start"], prior[-1])
                        self.assertFalse(any(chunk["char_start"] < start < chunk["char_end"] for start, _e, _p in markers))


class TestOllamaProposer(unittest.TestCase):
    def test_request_shape_without_network(self):
        sent = {}

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"model": "fake-model", "message": {"content": '{"sections": []}'},
                        "total_duration": 1, "prompt_eval_count": 2, "eval_count": 3}

        def fake_post(url, json, timeout):   # noqa: A002 - requests.post와 같은 인자 이름
            sent.update({"url": url, "json": json, "timeout": timeout})
            return FakeResponse()

        metrics = []
        propose = lc.make_ollama_section_proposer("fake-model", base_url="http://127.0.0.1:1",
                                                  http_post=fake_post, metrics_sink=metrics)
        self.assertEqual(propose("가상 프롬프트"), '{"sections": []}')
        self.assertEqual(sent["url"], "http://127.0.0.1:1/api/chat")
        self.assertEqual(sent["json"]["format"], lc.SECTION_JSON_SCHEMA)
        self.assertEqual(sent["json"]["options"]["temperature"], 0)
        self.assertNotIn("think", sent["json"])
        self.assertEqual(metrics[0]["prompt_eval_count"], 2)

    def test_model_name_is_required(self):
        with self.assertRaises(ValueError):
            lc.make_ollama_section_proposer("")


class FakeHttpResponse:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


FAKE_KEY = "sk-sample-only-not-a-real-key-0000"      # 가상 값 (실제 키 아님)
FAKE_PRICES = {"input": 2.0, "cached_input": 0.2, "output": 12.0}


def _completed(text, usage=None):
    return FakeHttpResponse(200, {
        "id": "resp_sample", "model": "sample-model", "status": "completed",
        "output": [{"type": "reasoning"}, {"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": usage or {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 0},
                           "output_tokens": 500, "output_tokens_details": {"reasoning_tokens": 300}}})


class TestOpenAIProposer(unittest.TestCase):
    def _proposer(self, responses, **kwargs):
        sent = []
        queue = list(responses)

        def fake_post(url, json, timeout, headers):   # noqa: A002
            sent.append({"url": url, "json": json, "timeout": timeout, "headers": headers})
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        log = []
        options = {"api_key": FAKE_KEY, "prices": FAKE_PRICES, "http_post": fake_post, "sleep": lambda _s: None,
                   "call_log": log, **kwargs}
        return lc.make_openai_section_proposer("sample-model", **options), sent, log

    def test_request_uses_strict_structured_output_and_limits(self):
        propose, sent, log = self._proposer([_completed('{"sections": []}')], max_output_tokens=1234)
        self.assertEqual(propose("가상 프롬프트"), '{"sections": []}')
        body = sent[0]["json"]
        self.assertTrue(sent[0]["url"].endswith("/responses"))
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertEqual(body["text"]["format"]["schema"], lc.OPENAI_SECTION_SCHEMA)
        self.assertEqual(body["max_output_tokens"], 1234)
        self.assertFalse(body["store"])
        self.assertEqual(body["reasoning"], {"effort": "low"})
        self.assertEqual(sent[0]["timeout"], 120)
        # 실제 비용: 1000×$2 + 500×$12 (/1M)
        self.assertAlmostEqual(propose.tracker.spent_usd, 0.008)
        self.assertEqual(log[0]["reasoning_tokens"], 300)
        self.assertNotIn(FAKE_KEY, json.dumps(log, ensure_ascii=False))

    def test_cached_tokens_use_cached_price(self):
        usage = {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 400}, "output_tokens": 0}
        propose, _sent, _log = self._proposer([_completed('{"sections": []}', usage)])
        propose("가상")
        self.assertAlmostEqual(propose.tracker.spent_usd, (600 * 2.0 + 400 * 0.2) / 1_000_000)

    def test_budget_and_call_limit_block_before_sending(self):
        propose, sent, _log = self._proposer([], budget_usd=0.001)
        with self.assertRaises(lc.BudgetExceededError):
            propose("가상 프롬프트")
        self.assertEqual(sent, [])
        propose, sent, _log = self._proposer([_completed('{"sections": []}')], max_calls=1)
        propose("첫 호출")
        with self.assertRaises(lc.CallLimitError):
            propose("두 번째 호출")
        self.assertEqual(len(sent), 1)

    def test_retry_only_retryable_errors_and_respect_limit(self):
        error_429 = FakeHttpResponse(429, {"error": {"code": "rate_limit_exceeded", "message": "가상"}})
        propose, sent, _log = self._proposer([error_429, _completed('{"sections": []}')], max_calls=2, max_retries=1)
        self.assertEqual(propose("가상"), '{"sections": []}')
        self.assertEqual(len(sent), 2)
        propose, sent, _log = self._proposer([error_429, error_429], max_calls=5, max_retries=1)
        with self.assertRaises(lc.OpenAIRequestError):
            propose("가상")
        self.assertEqual(len(sent), 2)
        error_401 = FakeHttpResponse(401, {"error": {"code": "invalid_api_key", "message": f"key {FAKE_KEY}"}})
        propose, sent, _log = self._proposer([error_401], max_calls=5, max_retries=3)
        with self.assertRaises(lc.OpenAIRequestError) as caught:
            propose("가상")
        self.assertEqual(len(sent), 1)                       # 인증 오류는 재시도하지 않는다
        self.assertNotIn(FAKE_KEY, str(caught.exception))    # 오류 메시지에 키·응답 본문 메시지를 넣지 않는다

    def test_unmeasured_failures_reserve_worst_case_and_stop(self):
        import requests
        # 연결 오류 2회(재시도 1회 포함): 사용량을 모르므로 최악 비용을 예약하고, 예약이 예산을 막으면 다음 호출을 하지 않는다
        propose, sent, log = self._proposer([requests.ConnectionError("가상"), requests.Timeout("가상")],
                                            max_calls=5, max_retries=1, max_output_tokens=1000)
        with self.assertRaises(lc.OpenAIRequestError):
            propose("가상")
        tracker = propose.tracker
        self.assertEqual(len(sent), 2)
        self.assertEqual(tracker.spent_usd, 0)
        self.assertEqual(len(tracker.unmeasured), 2)
        self.assertAlmostEqual(tracker.reserved_usd, 2 * tracker.worst_case_usd(lc.estimate_input_tokens("가상"), 1000),
                               places=6)
        self.assertTrue(all(entry["usage_unknown"] for entry in log))
        tight, sent2, _log = self._proposer([_completed('{"sections": []}')], max_calls=5,
                                            budget_usd=tracker.worst_case_usd(lc.estimate_input_tokens("가상"), 4000) * 1.5)
        tight.tracker.record_unmeasured(tight.tracker.worst_case_usd(lc.estimate_input_tokens("가상"), 4000), "가상")
        with self.assertRaises(lc.BudgetExceededError):
            tight("가상")
        self.assertEqual(sent2, [])

    def test_incomplete_refusal_and_empty_outputs_are_failures(self):
        incomplete = FakeHttpResponse(200, {"id": "r", "status": "incomplete", "output": [],
                                            "incomplete_details": {"reason": "max_output_tokens"},
                                            "usage": {"input_tokens": 10, "output_tokens": 4000}})
        refusal = FakeHttpResponse(200, {"id": "r", "status": "completed", "usage": {},
                                         "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "x"}]}]})
        empty = FakeHttpResponse(200, {"id": "r", "status": "completed", "usage": {}, "output": []})
        for response, kind in ((incomplete, "incomplete"), (refusal, "refusal"), (empty, "empty_output")):
            with self.subTest(kind=kind):
                propose, _sent, _log = self._proposer([response])
                with self.assertRaises(lc.OpenAIRequestError) as caught:
                    propose("가상")
                self.assertEqual(caught.exception.kind, kind)
        propose, _sent, _log = self._proposer([incomplete])
        try:
            propose("가상")
        except lc.OpenAIRequestError:
            pass
        self.assertGreater(propose.tracker.spent_usd, 0)      # 실패한 호출도 사용량이 있으면 비용에 넣는다

    def test_failure_inside_pipeline_falls_back_to_b3(self):
        error_500 = FakeHttpResponse(500, {"error": {"type": "server_error"}})
        propose, _sent, _log = self._proposer([error_500], max_retries=0)
        chunks, report = lc.chunk_document_llm(SAMPLE_HTML, propose)
        self.assertTrue(report["used_fallback"])
        self.assertEqual(chunks, ck.chunk_document_b3(SAMPLE_HTML)[0])
        self.assertIn("OpenAIRequestError", report["issues"][0]["error"])

    def test_unknown_model_price_and_missing_settings_are_rejected(self):
        from unittest import mock
        with self.assertRaises(ValueError):
            lc.make_openai_section_proposer("unknown-model", api_key=FAKE_KEY)      # 단가 없음 → 호출 불가
        # .env를 읽지 않게 하고 환경 변수를 비운 상태에서 키·모델 누락을 확인한다
        with mock.patch.object(lc.em, "load_env_file", lambda *a, **k: None), \
                mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                lc.make_openai_section_proposer("sample-model", prices=FAKE_PRICES)  # 키 없음
            with self.assertRaises(ValueError):
                lc.make_openai_section_proposer(None, api_key=FAKE_KEY, prices=FAKE_PRICES)  # 모델 없음

    def test_model_access_check_uses_models_endpoint(self):
        calls = []

        def fake_get(url, headers, timeout):
            calls.append(url)
            return FakeHttpResponse(200, {"id": "sample-model", "owned_by": "sample"})
        result = lc.check_openai_model_access("sample-model", api_key=FAKE_KEY, http_get=fake_get)
        self.assertTrue(result["accessible"])
        self.assertTrue(calls[0].endswith("/models/sample-model"))
        self.assertNotIn(FAKE_KEY, json.dumps(result))


if __name__ == "__main__":
    unittest.main(verbosity=2)
