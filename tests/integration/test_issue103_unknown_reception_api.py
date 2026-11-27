"""동결 릴리스 + 가상 검색 세션/LLM으로 접수 미확인 합의 문구 보정을 API 수준에서 확인한다(DB·LLM 호출 없음)."""

from copy import deepcopy
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from backend.api.main import create_app
from test_issue103_guidance import SEJONG, release_policy

PHRASE = "현재 접수 여부는 공식 공고에서 확인해 주세요"


class UnknownReceptionApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = release_policy(SEJONG)

    def ask(self, answer):
        session = Mock()
        session.search_policy_context.side_effect = lambda *a, **kw: {"policies": [deepcopy(self.policy)]}
        generated = {"status": "answered", "answer": answer, "used_source_ids": [1], "follow_up_questions": []}
        with TestClient(create_app(session_factory=lambda: session, generate_fn=Mock(return_value=generated))) as client:
            response = client.post("/api/ask", json={
                "question": "세종시 위원회 청년참여 확대 신청 방법이랑 모집 일정 알려줘", "selected_policy_id": SEJONG,
                "profile": {"age": 27, "region": "", "income": None}, "history": []})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_omitted_phrase_is_added(self):
        body = self.ask("신청 방법은 공고문 참조로만 적혀 있어요 [1].")
        self.assertEqual(body["policy_checks"][0]["reception_status"], "unknown")
        self.assertIn(PHRASE, body["answer"])
        self.assertEqual(body["status"], "answered")
        self.assertEqual(len(body["citations"]), 1)

    def test_paraphrase_is_replaced(self):
        body = self.ask("신청 방법은 공고문 참조로만 적혀 있어요 [1]. 현재 접수 여부도 공식 원문에서 확인되지 않았습니다 [1].")
        self.assertNotIn("확인되지 않았습니다", body["answer"])
        self.assertEqual(body["answer"].count(PHRASE), 1)

    def test_agreed_phrase_is_kept_as_is(self):
        answer = f"신청 방법은 공고문 참조로만 적혀 있어요 [1]. {PHRASE}. [1]"
        body = self.ask(answer)
        self.assertEqual(body["answer"].count(PHRASE), 1)


if __name__ == "__main__":
    unittest.main()
