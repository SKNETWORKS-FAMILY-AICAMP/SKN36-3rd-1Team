"""#44 나이 조건 미추출 안내가 실제 Checker → Pipeline → FastAPI 응답까지 유지되는지 검증한다.

외부 서비스는 호출하지 않는다: 검색은 가상 세션, Generator는 대역, 정책·문구는 가상 데이터(sample_only)다.
"""

from copy import deepcopy
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from backend.api.main import create_app

NOTICE = "이 정책의 나이 기준이 데이터에 정리되지 않아 공식 원문에서 확인이 필요합니다."
EDUCATION = {"type": "EDUCATION", "name": "학력 요건"}
AGE_18_29 = {"type": "AGE", "operator": "BETWEEN", "value": "18~29"}
CHILD_UNDER_24M = {"type": "SPECIAL_TARGET", "operator": "LT", "value": "24", "unit": "개월"}


def sample_policy(pid, description, conditions, logic="AND"):
    return {
        "policy_id": pid, "policy_name": f"가상 정책 {pid}(sample_only)", "best_rank": 1,
        "graph": {"policy": {"eligibility": description},
                  "eligibility": {"condition_tree": [{"group_id": f"ROOT_{pid}", "logic": logic,
                                                      "conditions": conditions, "groups": []}],
                                  "unknown_logic_groups": [], "structure_issues": []}},
        "graph_status": "FOUND", "data_quality": {"hold_auto_eligibility": False, "affected_attributes": []},
        "evidence": [{"rank": 1, "chunk_id": f"CH_{pid}", "document_id": f"D_{pid}", "text": "가상 공고 원문",
                      "source_url": "https://example.org/notice"}],
    }


class FakeSession:
    profile = collection = "sample_only"

    def __init__(self, policy):
        self.policy = policy

    def search_policy_context(self, question, filters=None, k=10):
        return {"policies": [self.policy]}

    def close(self):
        pass


class MissingAgeNoticeApiTests(unittest.TestCase):
    def ask(self, policy, age=40):
        original = deepcopy(policy)
        generate = Mock(return_value={"status": "answered", "answer": "가상 원문 안내 [1]",
                                      "used_source_ids": [1], "follow_up_questions": []})
        app = create_app(session_factory=lambda: FakeSession(policy), generate_fn=generate)  # 실제 Checker
        with TestClient(app) as client:
            response = client.post("/api/ask", json={"question": "지원 대상은?", "history": [],
                                                     "profile": {"age": age, "region": "", "income": None}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(policy, original)  # 입력 정책 불변
        llm_conditions = generate.call_args.args[2][0]["conditions"]
        api_conditions = response.json()["policy_checks"][0]["conditions"]
        return api_conditions, llm_conditions

    @staticmethod
    def rows(conditions, field):
        return [(c["status"], c["reason"]) for c in conditions if c["field"] == field]

    def test_missing_age_notice_reaches_llm_and_response(self):
        for description in ("지원 연령: 만 18~29세", "연령: 0~0세 (API 오류; 공식 확인 필요)"):
            with self.subTest(description=description):
                api, llm = self.ask(sample_policy("NOTICE", description, [EDUCATION]))
                self.assertEqual(self.rows(api, "age"), [("needs_check", NOTICE)])
                self.assertEqual(self.rows(llm, "age"), [("needs_check", NOTICE)])
                # 나이 기준이 없으니 종합 판정을 eligible로 확정하지 않는다.
                self.assertNotIn("eligible", [c["status"] for c in api if c["field"] == "eligibility"])

    def test_existing_age_condition_keeps_original_judgement_without_notice(self):
        for age, status in ((25, "eligible"), (40, "ineligible")):
            with self.subTest(age=age):
                api, _ = self.ask(sample_policy("AGE", "지원 연령: 만 18~29세", [AGE_18_29]), age=age)
                self.assertEqual([s for s, _ in self.rows(api, "age")], [status])
                self.assertNotIn(NOTICE, [c["reason"] for c in api])

    def test_no_notice_without_applicant_age_or_for_child_month_condition(self):
        cases = (
            sample_policy("NOLIMIT", "원문 검증 완료: 별도 나이 제한 없음", [EDUCATION]),
            sample_policy("CHILD", "지원 대상: 만 24개월 미만 자녀", [CHILD_UNDER_24M]),
        )
        for item in cases:
            with self.subTest(policy=item["policy_id"]):
                api, _ = self.ask(item)
                self.assertEqual(self.rows(api, "age"), [])

    def test_unknown_group_keeps_full_hold_without_duplicate_age_notice(self):
        api, _ = self.ask(sample_policy("UNK", "지원 연령: 만 18~29세", [EDUCATION], logic="UNKNOWN"))
        self.assertTrue(all(c["status"] == "needs_check" for c in api))
        self.assertNotIn(NOTICE, [c["reason"] for c in api])


if __name__ == "__main__":
    unittest.main()
