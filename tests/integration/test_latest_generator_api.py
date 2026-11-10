"""최신 Checker·Generator(#35)를 실제로 거치는 FastAPI 통합 테스트.

외부 서비스는 호출하지 않는다: 검색은 가상 세션, OpenAI는 generator._call_openai만 대체한다.
정책·날짜·원문은 모두 가상 데이터(sample_only)다.
"""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.llm.generator as generator
from backend.api.main import create_app
from backend.llm.generator import LLMTimeoutError, LLMUnavailableError

AGE_TREE = [{"group_id": "G_AGE", "logic": "AND", "groups": [],
             "conditions": [{"condition_id": "C_AGE", "type": "AGE", "operator": "BETWEEN", "value": "19~34"}]}]


def sample_policy(pid: str, rank: int, policy_fields: dict) -> dict:
    return {
        "policy_id": pid, "policy_name": f"가상 정책 {pid}(sample_only)", "best_rank": rank,
        "graph": {"policy": policy_fields,
                  "eligibility": {"condition_tree": deepcopy(AGE_TREE), "unknown_logic_groups": [],
                                  "structure_issues": []}},
        "graph_status": "FOUND", "data_quality": {},
        "evidence": [{"rank": rank, "chunk_id": f"CH_{pid}", "document_id": f"D_{pid}", "text": "가상 공고 원문",
                      "source_url": "https://example.org/notice"}],
    }


# A: 기간이 지난 정책(closed), 검색 1위 / B: 상시 접수 확인(open) + 소득 원문 확인 필요(needs_review)
POLICIES = [
    sample_policy("A", 1, {"application_start": "2020-01-01", "application_end": "2020-12-31"}),
    sample_policy("B", 2, {"application_is_ongoing": True, "income_source_review": "needs_review",
                           "income_source_note": "가상 확인 내용", "income_check_point": "가상 확인 항목",
                           "income_followup": "check_notice"}),
]
REQUEST = {"question": "청년 지원 정책은?", "profile": {"age": 25, "region": "서울", "income": 0}, "history": []}


class FakeSession:
    profile = collection = "sample_only"

    def __init__(self, policies):
        self.policies = policies

    def search_policy_context(self, question, filters=None, k=10):
        return {"policies": self.policies}

    def close(self):
        pass


def llm_output(*follow_ups, answer="가상 원문 기준 안내입니다 [1]"):
    return {"status": "answered", "answer": answer, "used_source_ids": [1], "follow_up_questions": list(follow_ups)}


class LatestGeneratorApiTests(unittest.TestCase):
    def setUp(self):
        self.policies = deepcopy(POLICIES)
        self.prompts = []
        self.app = create_app(session_factory=lambda: FakeSession(self.policies))  # 실제 Checker·Generator

    def ask(self, output=None, request=REQUEST, error=None):
        def fake_openai(messages):
            self.prompts.append(json.loads(messages[1]["content"].split("\n", 1)[1]))
            if error is not None:
                raise error
            return json.dumps(output, ensure_ascii=False)

        with patch.object(generator, "_call_openai", side_effect=fake_openai), TestClient(self.app) as client:
            return client.post("/api/ask", json=request)

    def test_profile_reception_reason_and_body_guidance_reach_http(self):
        response = self.ask(llm_output(
            {"kind": "ask_info", "field": "region", "text": "서울특별시의 어느 구에 거주하시나요?"},
            {"kind": "ask_info", "field": "household_size", "text": "가구원 수를 알려주세요."},
            {"kind": "ask_info", "field": "income", "text": "월 소득이 얼마인가요?"},
        ))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        prompt = self.prompts[0]
        # 정규화 프로필이 Generator Prompt까지 도달한다(서울→서울특별시, 소득 0 유지).
        self.assertEqual(prompt["user_profile"], {"age": 25, "region": "서울특별시", "income": 0})
        # 마감(A)은 검색 1위여도 Context·출처·정책 카드에서 뒤로 간다.
        self.assertEqual([c["policy_id"] for c in prompt["official_evidence"]], ["B", "A"])
        self.assertEqual([(p["policy_id"], p["reception_status"]) for p in body["policy_checks"]],
                         [("B", "open"), ("A", "closed")])
        self.assertEqual([c["document_id"] for c in body["citations"]], ["D_B"])
        # 시군구·가구원 수는 버튼이 아니라 본문 안내, 이미 입력한 소득(0)은 다시 묻지 않는다.
        self.assertEqual(body["follow_up_questions"], [])
        self.assertIn("시·군·구", body["answer"])
        self.assertIn("가구원 수", body["answer"])
        # Checker가 만든 원문 확인 사유가 그대로 응답까지 유지된다.
        income = [c for c in body["policy_checks"][0]["conditions"] if c["field"] == "income"]
        self.assertEqual([c["status"] for c in income], ["needs_check"])
        self.assertIn("가상 확인 항목", income[0]["reason"])
        self.assertEqual(body["notice"]["kind"], "warn")
        # 입력 정책 객체는 바뀌지 않는다.
        self.assertEqual(self.policies, POLICIES)

    def test_only_user_to_ai_questions_remain_as_buttons(self):
        response = self.ask(llm_output(
            {"kind": "question", "text": "신청 서류도 알려드릴까요?"},
            {"kind": "question", "text": "신청 서류는 무엇이 필요한가요?"},
            {"kind": "ask_info", "field": "region", "text": "거주하시는 시도는 어디인가요?"},
        ))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["follow_up_questions"],
                         [{"kind": "question", "text": "신청 서류는 무엇이 필요한가요?", "field": None}])

    def test_child_months_and_redundant_profile_questions_are_not_buttons(self):
        response = self.ask(llm_output(
            {"kind": "ask_info", "field": "child_age_months", "text": "자녀의 개월 수를 알려주세요."},
            {"kind": "question", "text": "나이가 어떻게 되시나요?"},
            {"kind": "question", "text": "신청 기간은 언제까지인가요?"},
        ))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        # 자녀 월령은 허용 필드가 아니므로 버튼이 아니라 본문 안내, 이미 입력한 나이 재질문은 제거.
        self.assertIn("자녀", body["answer"])
        self.assertEqual(body["follow_up_questions"],
                         [{"kind": "question", "text": "신청 기간은 언제까지인가요?", "field": None}])
        self.assertEqual([c["document_id"] for c in body["citations"]], ["D_B"])
        self.assertIn("[1]", body["answer"])

    def test_real_generator_answer_conflicting_with_checker_is_corrected(self):
        # 실제 Generator 검증을 통과한 본문이라도 Checker 판정과 충돌하는 단정은 확인된 상태로 바뀐다.
        # Context 순서: [1]=B(open, 소득 needs_review), [2]=A(closed)
        response = self.ask(llm_output(answer=(
            "가상 정책 A(sample_only)는 지금 신청할 수 있어요 [2]. "
            "가상 정책 B(sample_only)는 모든 신청 자격을 충족했어요 [1].")))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "answered")
        self.assertNotIn("지금 신청할 수 있어요", body["answer"])
        self.assertNotIn("모든 신청 자격을 충족했어요", body["answer"])
        self.assertIn("가상 정책 A(sample_only): 접수가 마감됐어요.", body["answer"])
        self.assertIn("가상 정책 B(sample_only): 신청 자격은 공식 공고에서 추가 확인이 필요해요.", body["answer"])
        # 본문 인용 순서대로 재번호되고, 출처 문서 연결은 유지된다.
        self.assertEqual([c["document_id"] for c in body["citations"]], ["D_B", "D_A"])
        self.assertEqual([(p["policy_id"], p["reception_status"]) for p in body["policy_checks"]],
                         [("B", "open"), ("A", "closed")])

    def test_named_closed_policy_with_other_policy_source_becomes_uncited_info(self):
        response = self.ask(llm_output(answer="가상 정책 A(sample_only)는 지금 신청할 수 있어요 [1]."))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "info")
        self.assertIn("접수가 마감됐어요", body["answer"])
        self.assertNotIn("신청할 수 있어요", body["answer"])
        self.assertEqual(body["citations"], [])
        self.assertEqual([(p["policy_id"], p["reception_status"]) for p in body["policy_checks"]],
                         [("B", "open"), ("A", "closed")])

    def test_bad_a_citation_removed_b_document_claim_and_source_preserved(self):
        self.policies[1]["evidence"][0]["text"] = "제출 서류가 필요합니다."
        response = self.ask(llm_output(answer=(
            "가상 정책 A(sample_only)는 지금 신청할 수 있어요 [1]. "
            "가상 정책 B(sample_only)는 제출 서류가 필요해요 [1].")))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("접수가 마감됐어요", body["answer"])
        self.assertIn("가상 정책 B(sample_only)는 제출 서류가 필요해요 [1]", body["answer"])
        self.assertEqual([item["document_id"] for item in body["citations"]], ["D_B"])

    def test_missing_profile_value_can_still_be_asked(self):
        request = {**REQUEST, "profile": {"age": 25, "region": "서울", "income": None}}
        response = self.ask(llm_output({"kind": "ask_info", "field": "income", "text": "월 소득이 얼마인가요?"}),
                            request=request)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.prompts[0]["user_profile"]["income"], None)
        self.assertEqual(response.json()["follow_up_questions"],
                         [{"kind": "ask_info", "text": "월 소득이 얼마인가요?", "field": "income"}])

    def test_generator_errors_map_to_public_codes(self):
        cases = (
            ({"error": LLMTimeoutError("private-value")}, (504, "LLM_TIMEOUT")),
            ({"error": LLMUnavailableError("private-value")}, (502, "LLM_UNAVAILABLE")),
            ({"output": llm_output(answer="근거 없는 번호 [9]")}, (502, "LLM_OUTPUT_INVALID")),
            # 후속 질문은 최대 3개(계약 §5.1). 초과 출력은 잘못된 LLM 출력이다.
            ({"output": llm_output(*[{"kind": "question", "text": f"질문 {i}은 무엇인가요?"} for i in range(4)])},
             (502, "LLM_OUTPUT_INVALID")),
        )
        for kwargs, expected in cases:
            with self.subTest(expected=expected):
                response = self.ask(**kwargs)
                self.assertEqual((response.status_code, response.json()["detail"]["code"]), expected)
                self.assertNotIn("private-value", response.text)


if __name__ == "__main__":
    unittest.main()
