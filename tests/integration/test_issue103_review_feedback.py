"""PR #136 리뷰 피드백 회귀: 일반 마무리 문구 502, 방문·온라인 혼합 안내 누락.

동결 릴리스 원문과 기록된 R3D 응답을 재사용한다. 네트워크·DB·LLM 호출은 없다(DB E2E 아님).
"""

from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.rag.application_links import GENERIC_NOTICE, align_application_links
from backend.rag.pipeline import AskError
from test_issue103_guidance import SEJONG, release_policy

FIXTURE = Path(__file__).parents[1] / "fixtures/issue103-r3d-mixed-url-response.json"
VISIT_POLICY = "POL_GOV_020"  # 릴리스 원문: "거주지 관할 읍면동 주민센터에 방문 신청", official_apply_url=null
CLOSING = "자세한 내용은 각 신청 페이지에서 확인하세요."


def agreed(answer):
    """기록된 실제 답변의 '접수 여부도 …확인해 주세요'는 Pipeline이 합의 문구('여부는')로 맞춘다(#147)."""
    return answer.replace("현재 접수 여부도 공식 공고에서", "현재 접수 여부는 공식 공고에서")
MIXED = "주민센터 방문 또는 온라인으로 신청할 수 있습니다 [1]."

A = {"policy_id": "A", "policy_name": "가상위원회", "official_apply_url": None, "reception_status": "unknown"}
B = {"policy_id": "B", "policy_name": "가상수당", "official_apply_url": "https://example.org/apply", "reception_status": "open"}
AB_CONTEXTS = [{"source_id": 1, "policy_id": "A", "text": "가상 원문"}, {"source_id": 2, "policy_id": "B", "text": "가상 원문"}]


def post(search_result, generated, request):
    session = Mock()
    session.search_policy_context.side_effect = lambda *a, **kw: deepcopy(search_result)
    generate = Mock(return_value=generated)
    with TestClient(create_app(session_factory=lambda: session, generate_fn=generate)) as client:
        return client.post("/api/ask", json=request)


def answered(text, ids=(1,)):
    return {"status": "answered", "answer": text, "used_source_ids": list(ids), "follow_up_questions": []}


class ClosingPhraseTests(unittest.TestCase):
    """피드백 1: 정책명·주소·인용 없는 마무리 문구 때문에 정상 답변 전체가 502가 되지 않는다."""

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def ask_recorded(self, answer):
        generated = {**deepcopy(self.data["generator_output"]), "answer": answer}
        return post(self.data["search_result"], generated, self.data["request"])

    def test_seoul_sejong_answer_with_closing_phrase_returns_200(self):
        recorded = self.data["generator_output"]["answer"]
        original = agreed(recorded)
        for separator in ("\n\n", " ", "\n"):
            with self.subTest(separator=repr(separator)):
                response = self.ask_recorded(recorded + separator + CLOSING)
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                # 서울·세종 본문과 출처, 서울의 확인된 신청 주소는 그대로 남는다.
                self.assertTrue(body["answer"].startswith(original))
                self.assertNotIn("각 신청 페이지", body["answer"])
                self.assertIn(GENERIC_NOTICE, body["answer"])
                checks = {p["policy_id"]: p for p in body["policy_checks"]}
                self.assertIsNone(checks[SEJONG]["official_apply_url"])
                self.assertTrue(checks["POL_STG_SEOUL_001"]["official_apply_url"])
                self.assertEqual(len(body["citations"]), 3)
                self.assertTrue(all("chunk_id" not in c for c in body["citations"]))

    def test_generic_notice_requires_each_policy_source(self):
        # 근거 문맥이 없는 정책이 섞이면 "각 정책의 공식 안내"를 주장하지 않고 문구만 뺀다.
        fixed, changed = align_application_links("안내입니다 [2]. " + CLOSING, AB_CONTEXTS[1:], [A, B])
        self.assertTrue(changed)
        self.assertEqual(fixed, "안내입니다 [2].")

    def test_unnamed_direct_claim_with_url_is_still_rejected(self):
        for text in ("신청 페이지는 https://example.org/apply 입니다.", "[신청하기](https://example.org/apply)",
                     "https://example.org 에서 바로 신청하세요."):
            with self.subTest(text=text), self.assertRaises(AskError):
                align_application_links(text, AB_CONTEXTS, [A, B])

    def test_unnamed_direct_claim_without_url_is_neutralized_not_kept(self):
        fixed, changed = align_application_links("각 신청 페이지에서 바로 신청하세요.", AB_CONTEXTS, [A, B])
        self.assertTrue(changed)
        self.assertEqual(fixed, GENERIC_NOTICE)

    def test_multi_policy_citations_without_url_are_neutralized(self):
        # 2차 리뷰: 인용이 각자 올바른 정책을 가리키는 묶음 안내는 502가 아니라 그 절만 보정한다.
        fixed, changed = align_application_links("가상 안내입니다. 두 정책은 각 홈페이지에서 신청할 수 있습니다 [1][2].",
                                                 AB_CONTEXTS, [A, B])
        self.assertTrue(changed)
        self.assertEqual(fixed, "가상 안내입니다. " + GENERIC_NOTICE + " 가상위원회: 직접 신청 링크는 확인되지 않았어요. [1][2]")

    def test_named_multi_policy_without_url_is_neutralized(self):
        fixed, changed = align_application_links("가상위원회와 가상수당은 각 누리집에서 신청할 수 있습니다.", AB_CONTEXTS, [A, B])
        self.assertTrue(changed)
        self.assertEqual(fixed, GENERIC_NOTICE + " 가상위원회: 직접 신청 링크는 확인되지 않았어요.")
        self.assertNotIn("가상수당:", fixed)

    def test_named_or_cited_multi_policy_with_url_is_still_rejected(self):
        for text in ("가상위원회와 가상수당은 https://example.org/apply 에서 신청하세요.",
                     "신청 주소는 https://example.org/apply 입니다 [1][2]."):
            with self.subTest(text=text), self.assertRaises(AskError):
                align_application_links(text, AB_CONTEXTS, [A, B])

    def test_seoul_sejong_combined_application_sentence_returns_200(self):
        # 2차 리뷰 사례를 기록된 실제 R3D 서울·세종 응답 뒤에 붙여 API 전체로 확인한다.
        recorded = self.data["generator_output"]["answer"]
        original = agreed(recorded)
        sentence = "서울 정책과 세종 정책은 각 홈페이지에서 신청할 수 있습니다 [1][2]."
        response = self.ask_recorded(recorded + "\n\n" + sentence)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["answer"].startswith(original))
        tail = body["answer"][len(original):]
        self.assertNotIn("각 홈페이지에서 신청할 수 있습니다", tail)
        self.assertIn(GENERIC_NOTICE, tail)
        self.assertIn("세종시 위원회 청년참여 확대: 직접 신청 링크는 확인되지 않았어요.", tail)
        # 본문 세종 문단에 합의 문구가 이미 있으므로 끝에 다시 붙이지 않는다(#147).
        self.assertTrue(tail.endswith("[1][2]"))
        self.assertEqual(body["answer"].count("현재 접수 여부는 공식 공고에서 확인해 주세요"), 1)
        checks = {p["policy_id"]: p for p in body["policy_checks"]}
        self.assertIsNone(checks[SEJONG]["official_apply_url"])
        self.assertTrue(checks["POL_STG_SEOUL_001"]["official_apply_url"])
        self.assertEqual(len(body["citations"]), 3)


class HeadingCitationMismatchTests(unittest.TestCase):
    """3차 리뷰: 문단 제목의 정책과 다른 정책의 인용이 붙어도 제목 정책의 신청 URL을 검증한다."""

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_sejong_heading_with_seoul_citation_is_corrected(self):
        contexts = [{"source_id": 1, "policy_id": "A", "text": "t"}, {"source_id": 2, "policy_id": "B", "text": "t"}]
        for heading in ("### 가상위원회\n", "**가상위원회**\n", "가상위원회 안내입니다. "):
            with self.subTest(heading=heading):
                fixed, changed = align_application_links(
                    heading + "신청 URL은 https://example.org/unverified 입니다 [2].", contexts, [A, B])
                self.assertTrue(changed)
                self.assertNotIn("https://example.org/unverified", fixed)
                self.assertIn("가상위원회: 직접 신청 링크는 확인되지 않았어요.", fixed)
                self.assertNotIn("[2]", fixed)  # 다른 정책 근거를 빌리지 않는다.

    def test_seoul_heading_with_sejong_citation_is_corrected_for_cited_policy(self):
        contexts = [{"source_id": 1, "policy_id": "A", "text": "t"}, {"source_id": 2, "policy_id": "B", "text": "t"}]
        fixed, changed = align_application_links("### 가상수당\n신청 URL은 https://example.org/unverified 입니다 [1].",
                                                 contexts, [A, B])
        self.assertTrue(changed)
        self.assertNotIn("https://example.org/unverified", fixed)
        self.assertTrue(fixed.endswith("가상위원회: 직접 신청 링크는 확인되지 않았어요. 신청 방법은 공식 공고에서 확인해 주세요. "
                                       "현재 접수 여부는 공식 공고에서 확인해 주세요. [1]"))

    def test_matching_heading_and_citation_with_valid_url_is_unchanged(self):
        contexts = [{"source_id": 1, "policy_id": "A", "text": "t"}, {"source_id": 2, "policy_id": "B", "text": "t"}]
        text = "### 가상수당\n신청 주소는 https://example.org/apply 입니다 [2]."
        self.assertEqual(align_application_links(text, contexts, [A, B]), (text, False))

    def test_recorded_seoul_sejong_answer_with_mismatched_citation_via_api(self):
        recorded = self.data["generator_output"]["answer"]
        original = agreed(recorded)
        extra = "\n\n### 세종시 위원회 청년참여 확대\n신청 URL은 https://www.sejong.go.kr/apply 입니다 [2]."
        generated = {**deepcopy(self.data["generator_output"]), "answer": recorded + extra}
        response = post(self.data["search_result"], generated, self.data["request"])
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["answer"].startswith(original))
        tail = body["answer"][len(original):]
        self.assertNotIn("https://www.sejong.go.kr/apply", tail)
        self.assertIn("세종시 위원회 청년참여 확대: 직접 신청 링크는 확인되지 않았어요.", tail)
        self.assertNotIn("[2]", tail)
        checks = {p["policy_id"]: p for p in body["policy_checks"]}
        self.assertIsNone(checks[SEJONG]["official_apply_url"])
        self.assertTrue(checks["POL_STG_SEOUL_001"]["official_apply_url"])


class VisitNegationTests(unittest.TestCase):
    """3차 리뷰: 원문이 방문 신청을 받지 않는다고 하면 '방문 신청 방법은 확인되지만'으로 뒤집지 않는다."""

    def test_negative_visit_evidence_is_not_turned_into_visit_guidance(self):
        for evidence in ("주민센터 방문 신청은 받지 않습니다.", "주민센터 방문 신청 불가", "주민센터 방문 접수는 제외합니다.",
                         "주민센터를 방문하여 신청할 수 없습니다.", "주민센터 방문 신청은 하지 않습니다.",
                         "※ 주민센터 방문 신청 안 됨"):
            with self.subTest(evidence=evidence):
                contexts = [{"source_id": 1, "policy_id": "A", "text": "온라인 공고를 확인하세요.\n" + evidence}]
                fixed, changed = align_application_links(MIXED, contexts, [A])
                self.assertTrue(changed)
                self.assertNotIn("방문 신청 방법은 확인", fixed)
                self.assertIn("직접 신청 링크는 확인되지 않았어요", fixed)

    def test_negative_segment_does_not_hide_positive_segment_of_same_place(self):
        contexts = [{"source_id": 1, "policy_id": "A",
                     "text": "우편 접수는 받지 않습니다.\n- 신청방법: 거주지 관할 주민센터에 방문 신청"}]
        fixed, _ = align_application_links(MIXED, contexts, [A])
        self.assertIn("주민센터 방문 신청 방법은 확인되지만", fixed)

    def test_negative_answer_clause_is_not_turned_into_visit_guidance(self):
        contexts = [{"source_id": 1, "policy_id": "A", "text": "거주지 관할 주민센터에 방문 신청"}]
        fixed, changed = align_application_links("주민센터 방문 신청은 안 되고 온라인으로 신청할 수 있습니다 [1].", contexts, [A])
        self.assertTrue(changed)
        self.assertNotIn("방문 신청 방법은 확인", fixed)

    def test_negative_visit_evidence_via_api(self):
        policy = release_policy(VISIT_POLICY)
        # sample_only: 릴리스 원문의 방문 신청 문장을 부정 문장으로 바꾼 테스트 전용 입력(데이터 파일은 변경하지 않음).
        for hit in policy["evidence"]:
            hit["text"] = hit["text"].replace("주민센터에 방문 신청", "주민센터 방문 신청은 받지 않습니다.")
        self.assertFalse(any("주민센터에 방문 신청" in hit["text"] for hit in policy["evidence"]))
        request = {"question": "신청 방법 알려줘.", "selected_policy_id": VISIT_POLICY,
                   "profile": {"age": 30, "region": "", "income": None}, "history": []}
        response = post({"policies": [policy]}, answered(MIXED), request)
        self.assertEqual(response.status_code, 200, response.text)
        answer = response.json()["answer"]
        self.assertNotIn("방문 신청 방법은 확인", answer)
        self.assertIn("직접 신청 링크는 확인되지 않았어요", answer)


class NurijipSynonymTests(unittest.TestCase):
    """2차 리뷰: '누리집'도 '홈페이지'와 같은 웹 신청 경로 표현으로 보정한다."""

    def test_nurijip_application_claim_is_corrected_like_homepage(self):
        contexts = [{"source_id": 1, "policy_id": "A", "text": "가상 원문"}]
        for text in ("세종 정책은 시청 누리집을 통해 신청할 수 있습니다.", "세종 정책은 시청 홈페이지를 통해 신청할 수 있습니다.",
                     "누리집에서 온라인 접수하세요 [1].", "신청은 세종시 누리집에서 하시면 됩니다 [1].",
                     "세종시 웹 사이트에서 신청할 수 있습니다 [1]."):
            with self.subTest(text=text):
                fixed, changed = align_application_links(text, contexts, [A])
                self.assertTrue(changed)
                self.assertIn("직접 신청 링크는 확인되지 않았어요", fixed)

    def test_nurijip_notice_and_caution_wording_is_preserved(self):
        for text in ("세종시 누리집에서 신청 관련 공고를 확인하세요 [1].", "신청 공고는 세종시 누리집에서 확인할 수 있습니다 [1].",
                     "누리집에서는 신청할 수 없습니다 [1].", "공식 누리집은 https://www.sejong.go.kr 입니다 [1]."):
            with self.subTest(text=text):
                self.assertEqual(align_application_links(text, [{"source_id": 1, "policy_id": "A"}], [A]), (text, False))

    def test_nurijip_for_policy_with_valid_url_is_unchanged(self):
        text = "가상수당은 누리집에서 신청할 수 있습니다 [2]."
        self.assertEqual(align_application_links(text, AB_CONTEXTS, [A, B]), (text, False))

    def test_nurijip_through_api_with_real_sejong_release(self):
        policy = release_policy(SEJONG)
        request = {"question": "세종 청년위원회 신청 방법 알려줘.", "profile": {"age": 27, "region": "세종", "income": None}, "history": []}
        response = post({"policies": [policy]}, answered("세종 정책은 시청 누리집을 통해 신청할 수 있습니다 [1]."), request)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertNotIn("누리집을 통해 신청할 수 있습니다", body["answer"])
        self.assertIn("직접 신청 링크는 확인되지 않았어요", body["answer"])
        self.assertIsNone(body["policy_checks"][0]["official_apply_url"])
        self.assertEqual(body["citations"][0]["document_id"], "DOC_YC_065")

    def test_named_wrong_policy_url_is_corrected_without_borrowing(self):
        text = "가상위원회 신청 주소는 https://example.org/apply 입니다 [1]. 가상수당 신청 주소는 https://example.org/apply 입니다 [2]."
        fixed, changed = align_application_links(text, AB_CONTEXTS, [A, B])
        self.assertTrue(changed)
        first, second = fixed.split(" 가상수당 ")
        self.assertNotIn("https://example.org/apply", first)
        self.assertIn("[1]", first)
        self.assertIn("신청 주소는 https://example.org/apply 입니다 [2].", second)

    def test_closing_phrase_when_every_policy_has_url_is_unchanged(self):
        both = [B, {**B, "policy_id": "C", "policy_name": "가상장려금"}]
        text = "가상수당 [1]\n\n" + CLOSING
        self.assertEqual(align_application_links(text, [], both), (text, False))


class VisitGuidanceTests(unittest.TestCase):
    """피드백 2: 확인되지 않은 온라인 신청만 보정하고, 원문 근거가 있는 방문 신청은 유지한다."""

    def ask(self, pid, text):
        policy = release_policy(pid)
        request = {"question": "신청 방법 알려줘.", "selected_policy_id": pid,
                   "profile": {"age": 30, "region": "", "income": None}, "history": []}
        return post({"policies": [policy]}, answered(text), request), policy

    def test_release_evidence_preconditions(self):
        visit = release_policy(VISIT_POLICY)
        self.assertIsNone(visit["graph"]["policy"]["official_apply_url"])
        self.assertTrue(any("주민센터에 방문 신청" in hit["text"] for hit in visit["evidence"]))
        sejong = release_policy(SEJONG)
        self.assertFalse(any("주민센터" in hit["text"] for hit in sejong["evidence"]))

    def test_visit_and_online_in_one_sentence_keeps_verified_visit(self):
        response, policy = self.ask(VISIT_POLICY, MIXED)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("주민센터 방문 신청 방법은 확인되지만, 직접 신청 링크는 확인되지 않았어요.", body["answer"])
        self.assertNotIn("온라인으로 신청할 수 있습니다", body["answer"])
        self.assertTrue(body["answer"].endswith("[1]"))
        self.assertEqual(body["policy_checks"][0]["policy_id"], VISIT_POLICY)
        self.assertIsNone(body["policy_checks"][0]["official_apply_url"])
        self.assertEqual(body["citations"][0]["document_id"], policy["evidence"][0]["document_id"])

    def test_visit_only_sentence_is_unchanged(self):
        text = "거주지 관할 읍면동 주민센터에 방문 신청할 수 있습니다 [1]."
        response, _ = self.ask(VISIT_POLICY, text)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["answer"], text + "\n\n현재 접수 여부는 공식 공고에서 확인해 주세요.")

    def test_mixed_sentence_without_visit_evidence_does_not_invent_visit(self):
        response, _ = self.ask(SEJONG, MIXED)
        self.assertEqual(response.status_code, 200, response.text)
        answer = response.json()["answer"]
        self.assertNotIn("주민센터", answer)
        self.assertIn("직접 신청 링크는 확인되지 않았어요", answer)

    def test_homepage_visit_is_not_treated_as_offline_visit(self):
        contexts = [{"source_id": 1, "policy_id": "A", "text": "주민센터에 방문 신청"}]
        fixed, changed = align_application_links("홈페이지를 방문해 온라인으로 신청할 수 있습니다 [1].", contexts, [A])
        self.assertTrue(changed)
        self.assertNotIn("방문 신청 방법은 확인", fixed)

    def test_original_citation_number_is_kept(self):
        contexts = [{"source_id": 6, "policy_id": "A", "text": "거주지 관할 주민센터에 방문하여 신청"}]
        fixed, changed = align_application_links("주민센터 방문 또는 온라인으로 신청할 수 있습니다 [6]", contexts, [A])
        self.assertTrue(changed)
        self.assertIn("주민센터 방문 신청 방법은 확인되지만, 직접 신청 링크는 확인되지 않았어요.", fixed)
        self.assertTrue(fixed.endswith("[6]"))

    def test_visit_with_other_policys_citation_is_not_borrowed(self):
        contexts = [{"source_id": 6, "policy_id": "A", "text": "온라인 원문"}, {"source_id": 7, "policy_id": "B", "text": "주민센터에 방문 신청"}]
        fixed, _ = align_application_links("가상위원회는 주민센터 방문 또는 온라인으로 신청할 수 있습니다 [6]", contexts, [A, B])
        self.assertNotIn("주민센터", fixed)
        self.assertNotIn("[7]", fixed)

    def test_policy_with_valid_online_url_is_unchanged(self):
        text = "가상수당은 주민센터 방문 또는 온라인으로 신청할 수 있습니다 [2]."
        self.assertEqual(align_application_links(text, AB_CONTEXTS, [A, B]), (text, False))


# --- PR #136 4차 리뷰 회귀: 쉼표 분리, 겹치는 정책명, 합의 문구 통일, 하이픈 검색 ---

RELEASE_POLICIES = Path(__file__).parents[2] / "data/releases/v1.4.0-candidate/neo4j/nodes_policy.jsonl"
NULL_LINK = "직접 신청 링크는 확인되지 않았어요. 신청 방법은 공식 공고에서 확인해 주세요."


def release_names():
    rows = [json.loads(line) for line in RELEASE_POLICIES.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["policy_id"]: row["policy_name"] for row in rows}


def check(pid, name, url=None, reception="open"):
    return {"policy_id": pid, "policy_name": name, "official_apply_url": url, "reception_status": reception}


ONE = [{"source_id": 1, "policy_id": "X", "text": "가상 원문 (sample_only)"}]


class CommaSplitTests(unittest.TestCase):
    """필수 1: 숫자 사이 쉼표와 정책명 안의 쉼표에서 절이 잘리지 않는다."""

    C = [check("X", "가상노인수당")]  # sample_only

    def test_amount_comma_is_not_split_into_broken_text(self):
        fixed, changed = align_application_links("월 1,000원이며 홈페이지에서 신청하세요 [1].", ONE, self.C)
        self.assertTrue(changed)
        self.assertNotIn("1,가상노인수당", fixed)
        self.assertEqual(fixed, f"가상노인수당: {NULL_LINK} [1]")

    def test_amount_in_other_sentence_is_kept_intact(self):
        fixed, _ = align_application_links("가상노인수당은 월 1,000원을 지원해요 [1]. 홈페이지에서 신청하세요 [1].", ONE, self.C)
        self.assertEqual(fixed, f"가상노인수당은 월 1,000원을 지원해요 [1]. 가상노인수당: {NULL_LINK} [1]")

    def test_digits_around_spaced_comma_are_not_split(self):
        for text in ("월 1, 000원이며 홈페이지에서 신청하세요 [1].", "1,2차 모집은 홈페이지에서 신청하세요 [1]."):
            with self.subTest(text=text):
                fixed, _ = align_application_links(text, ONE, self.C)
                self.assertEqual(fixed, f"가상노인수당: {NULL_LINK} [1]")

    def test_spaced_comma_between_words_still_splits_clauses(self):
        # 쉼표 뒤 공백이 있으면 앞 절(서류 안내)은 그대로 두고 신청 경로 절만 보정한다.
        fixed, _ = align_application_links("가상노인수당은 신분증이 필요하고, 홈페이지에서 신청하세요 [1].", ONE, self.C)
        self.assertEqual(fixed, f"가상노인수당은 신분증이 필요하고, 가상노인수당: {NULL_LINK} [1]")

    def test_real_release_policy_names_with_commas_stay_whole(self):
        names = [name for name in release_names().values() if "," in name]
        self.assertEqual(len(names), 4)
        for name in names:
            with self.subTest(name=name):
                fixed, changed = align_application_links(f"{name}은 홈페이지에서 신청할 수 있어요 [1].", ONE, [check("X", name)])
                self.assertTrue(changed)
                self.assertEqual(fixed, f"{name}: {NULL_LINK} [1]")

    def test_comma_policy_name_with_valid_url_is_unchanged(self):
        name = "자활근로(기초, 차상위)"
        text = f"{name}은 홈페이지에서 신청할 수 있어요 [1]."
        fixed = align_application_links(text, ONE, [check("X", name, "https://example.org/apply")])
        self.assertEqual(fixed, (text, False))


class OverlappingNameTests(unittest.TestCase):
    """필수 2: 긴 정책명 안에 들어 있는 짧은 정책명을 별도 후보로 보지 않는다."""

    SHORT = check("S", "K-패스")  # sample_only, 신청 URL 없음
    LONG = check("L", "K-패스 청년", "https://example.org/apply")  # sample_only, 신청 URL 있음
    CONTEXTS = [{"source_id": 1, "policy_id": "S", "text": "가상 원문"}, {"source_id": 2, "policy_id": "L", "text": "가상 원문"}]

    def align(self, text):
        return align_application_links(text, self.CONTEXTS, [self.SHORT, self.LONG])

    def test_long_name_with_valid_url_is_not_rewritten_as_short_name(self):
        text = "K-패스 청년은 홈페이지에서 신청할 수 있어요 [2]."
        self.assertEqual(self.align(text), (text, False))

    def test_long_name_heading_keeps_its_own_policy(self):
        text = "### K-패스 청년\n신청 페이지에서 신청하세요 [2]."
        self.assertEqual(self.align(text), (text, False))

    def test_short_name_alone_is_still_corrected(self):
        fixed, changed = self.align("K-패스는 홈페이지에서 신청할 수 있어요 [1].")
        self.assertTrue(changed)
        self.assertEqual(fixed, f"K-패스: {NULL_LINK} [1]")

    def test_short_name_outside_long_name_is_kept_as_candidate(self):
        fixed, changed = self.align("K-패스와 K-패스 청년은 각 홈페이지에서 신청할 수 있어요 [1][2].")
        self.assertTrue(changed)
        self.assertEqual(fixed, GENERIC_NOTICE + " K-패스: 직접 신청 링크는 확인되지 않았어요. [1][2]")

    def test_real_release_overlapping_pairs(self):
        names = list(release_names().values())
        pairs = [(short, long) for short in names for long in names if short != long and short in long]
        self.assertEqual(len(pairs), 5)
        for short, long in pairs:
            with self.subTest(short=short, long=long):
                checks = [check("S", short), check("L", long, "https://example.org/apply")]
                text = f"{long}은 홈페이지에서 신청할 수 있어요 [2]."
                self.assertEqual(align_application_links(text, self.CONTEXTS, checks), (text, False))


class AgreedWordingTests(unittest.TestCase):
    """필수 3: official_apply_url=null 안내를 #103 합의 문구(해요체)로 통일한다."""

    def test_prompt_and_correction_use_agreed_phrase(self):
        from ai.prompts.system_prompt import SYSTEM_PROMPT
        from backend.rag.application_links import NO_APPLICATION_LINK

        self.assertIn("null이면 “직접 신청 링크는 확인되지 않았어요”라고 안내하세요.", SYSTEM_PROMPT)
        self.assertNotIn("전용 신청 주소가 없다고 안내", SYSTEM_PROMPT)
        self.assertEqual(NO_APPLICATION_LINK, NULL_LINK)

    def test_corrections_do_not_mix_hapnida_style(self):
        visit = [{"source_id": 1, "policy_id": "A", "text": "주민센터에 방문 신청 (sample_only)"}]
        cases = [
            align_application_links("가상위원회는 홈페이지에서 신청할 수 있어요 [1].", AB_CONTEXTS, [A, B])[0],
            align_application_links("가상위원회는 주민센터 방문 또는 온라인으로 신청할 수 있어요 [1].", visit, [A])[0],
            align_application_links("가상위원회와 가상수당은 각 홈페이지에서 신청할 수 있어요 [1][2].", AB_CONTEXTS,
                                    [A, {**B, "official_apply_url": None}])[0],
        ]
        for fixed in cases:
            with self.subTest(fixed=fixed):
                self.assertIn("직접 신청 링크는 확인되지 않았어요", fixed)
                self.assertNotIn("습니다", fixed)
        self.assertIn("현재 접수 여부는 공식 공고에서 확인해 주세요.", cases[0])  # A는 reception unknown
        self.assertIn("주민센터 방문 신청 방법은 확인되지만, 직접 신청 링크는 확인되지 않았어요.", cases[1])

    def test_correction_output_is_stable_when_applied_again(self):
        fixed, _ = align_application_links("가상위원회는 홈페이지에서 신청할 수 있어요 [1].", AB_CONTEXTS, [A, B])
        self.assertEqual(align_application_links(fixed, AB_CONTEXTS, [A, B]), (fixed, False))

    def test_pipeline_unknown_reception_notice_matches_agreed_phrase(self):
        from backend.rag.pipeline import RECEPTION_NOTICE

        self.assertEqual(RECEPTION_NOTICE["unknown"], "현재 접수 여부는 공식 공고에서 확인해 주세요.")


class HyphenOnlySearchTests(unittest.TestCase):
    """선택: '-'처럼 하이픈만 입력하면 전체 목록이 아니라 결과 없음이다(sample_only 패키지)."""

    def test_hyphen_only_query_returns_no_items(self):
        import tempfile

        from backend.api.policy_catalog import PolicyCatalog
        from test_policy_catalog_api import write_package

        with tempfile.TemporaryDirectory() as root:
            write_package(Path(root))
            catalog = PolicyCatalog(Path(root))
            self.assertEqual(catalog.list().total, 5)
            self.assertEqual(catalog.list(q="   ").total, 5)  # 공백만은 검색어 없음과 같다.
            for query in ("-", "--", " - ", "—", "－"):
                with self.subTest(query=query):
                    self.assertEqual(catalog.list(q=query).total, 0)
            self.assertEqual(catalog.list(q="P-OL_A").total, 1)  # 하이픈을 무시한 검색은 그대로 동작한다.


if __name__ == "__main__":
    unittest.main()
