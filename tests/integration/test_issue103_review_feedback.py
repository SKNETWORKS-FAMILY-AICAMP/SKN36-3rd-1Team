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
        original = self.data["generator_output"]["answer"]
        for separator in ("\n\n", " ", "\n"):
            with self.subTest(separator=repr(separator)):
                response = self.ask_recorded(original + separator + CLOSING)
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
        self.assertEqual(fixed, "가상 안내입니다. " + GENERIC_NOTICE + " 가상위원회: 현재 확인된 전용 신청 주소는 없습니다. [1][2]")

    def test_named_multi_policy_without_url_is_neutralized(self):
        fixed, changed = align_application_links("가상위원회와 가상수당은 각 누리집에서 신청할 수 있습니다.", AB_CONTEXTS, [A, B])
        self.assertTrue(changed)
        self.assertEqual(fixed, GENERIC_NOTICE + " 가상위원회: 현재 확인된 전용 신청 주소는 없습니다.")
        self.assertNotIn("가상수당:", fixed)

    def test_named_or_cited_multi_policy_with_url_is_still_rejected(self):
        for text in ("가상위원회와 가상수당은 https://example.org/apply 에서 신청하세요.",
                     "신청 주소는 https://example.org/apply 입니다 [1][2]."):
            with self.subTest(text=text), self.assertRaises(AskError):
                align_application_links(text, AB_CONTEXTS, [A, B])

    def test_seoul_sejong_combined_application_sentence_returns_200(self):
        # 2차 리뷰 사례를 기록된 실제 R3D 서울·세종 응답 뒤에 붙여 API 전체로 확인한다.
        original = self.data["generator_output"]["answer"]
        sentence = "서울 정책과 세종 정책은 각 홈페이지에서 신청할 수 있습니다 [1][2]."
        response = self.ask_recorded(original + "\n\n" + sentence)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["answer"].startswith(original))
        tail = body["answer"][len(original):]
        self.assertNotIn("각 홈페이지에서 신청할 수 있습니다", tail)
        self.assertIn(GENERIC_NOTICE, tail)
        self.assertIn("세종시 위원회 청년참여 확대: 현재 확인된 전용 신청 주소는 없습니다.", tail)
        self.assertTrue(tail.endswith("[1][2]"))
        checks = {p["policy_id"]: p for p in body["policy_checks"]}
        self.assertIsNone(checks[SEJONG]["official_apply_url"])
        self.assertTrue(checks["POL_STG_SEOUL_001"]["official_apply_url"])
        self.assertEqual(len(body["citations"]), 3)


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
                self.assertIn("전용 신청 주소는 없습니다", fixed)

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
        self.assertIn("전용 신청 주소는 없습니다", body["answer"])
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
        self.assertIn("주민센터 방문 신청 방법은 확인되지만, 전용 온라인 신청 주소는 확인되지 않았습니다.", body["answer"])
        self.assertNotIn("온라인으로 신청할 수 있습니다", body["answer"])
        self.assertTrue(body["answer"].endswith("[1]"))
        self.assertEqual(body["policy_checks"][0]["policy_id"], VISIT_POLICY)
        self.assertIsNone(body["policy_checks"][0]["official_apply_url"])
        self.assertEqual(body["citations"][0]["document_id"], policy["evidence"][0]["document_id"])

    def test_visit_only_sentence_is_unchanged(self):
        text = "거주지 관할 읍면동 주민센터에 방문 신청할 수 있습니다 [1]."
        response, _ = self.ask(VISIT_POLICY, text)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["answer"], text)

    def test_mixed_sentence_without_visit_evidence_does_not_invent_visit(self):
        response, _ = self.ask(SEJONG, MIXED)
        self.assertEqual(response.status_code, 200, response.text)
        answer = response.json()["answer"]
        self.assertNotIn("주민센터", answer)
        self.assertIn("전용 신청 주소는 없습니다", answer)

    def test_homepage_visit_is_not_treated_as_offline_visit(self):
        contexts = [{"source_id": 1, "policy_id": "A", "text": "주민센터에 방문 신청"}]
        fixed, changed = align_application_links("홈페이지를 방문해 온라인으로 신청할 수 있습니다 [1].", contexts, [A])
        self.assertTrue(changed)
        self.assertNotIn("방문 신청 방법은 확인", fixed)

    def test_original_citation_number_is_kept(self):
        contexts = [{"source_id": 6, "policy_id": "A", "text": "거주지 관할 주민센터에 방문하여 신청"}]
        fixed, changed = align_application_links("주민센터 방문 또는 온라인으로 신청할 수 있습니다 [6]", contexts, [A])
        self.assertTrue(changed)
        self.assertIn("주민센터 방문 신청 방법은 확인되지만, 전용 온라인 신청 주소는 확인되지 않았습니다.", fixed)
        self.assertTrue(fixed.endswith("[6]"))

    def test_visit_with_other_policys_citation_is_not_borrowed(self):
        contexts = [{"source_id": 6, "policy_id": "A", "text": "온라인 원문"}, {"source_id": 7, "policy_id": "B", "text": "주민센터에 방문 신청"}]
        fixed, _ = align_application_links("가상위원회는 주민센터 방문 또는 온라인으로 신청할 수 있습니다 [6]", contexts, [A, B])
        self.assertNotIn("주민센터", fixed)
        self.assertNotIn("[7]", fixed)

    def test_policy_with_valid_online_url_is_unchanged(self):
        text = "가상수당은 주민센터 방문 또는 온라인으로 신청할 수 있습니다 [2]."
        self.assertEqual(align_application_links(text, AB_CONTEXTS, [A, B]), (text, False))


if __name__ == "__main__":
    unittest.main()
