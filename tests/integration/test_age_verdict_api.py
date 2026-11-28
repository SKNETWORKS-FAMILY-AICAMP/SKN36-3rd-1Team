"""동결 릴리스의 실제 조건 트리 + 가상 LLM 출력으로 나이 결론 보정을 API 수준에서 확인한다(DB·LLM 호출 없음)."""

import copy
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from backend.api.main import create_app
from test_issue79_18_90_verification import item


def ask(policy, age, answer, question, **profile):
    session = Mock()
    session.search_policy_context.side_effect = lambda *a, **kw: {"policies": [copy.deepcopy(policy)]}
    generated = {"status": "answered", "answer": answer, "used_source_ids": [1], "follow_up_questions": []}
    with TestClient(create_app(session_factory=lambda: session, generate_fn=Mock(return_value=generated))) as client:
        response = client.post("/api/ask", json={"question": question, "selected_policy_id": policy["policy_id"],
                                                 "profile": {"age": age, "region": "", "income": None, **profile},
                                                 "history": []})
    assert response.status_code == 200, response.text
    return response.json()


class IncheonAgeTests(unittest.TestCase):
    """POL_STG_ICN_001: DB 조건 19~39세(2026 공고·조례), 정부24 구조화 요약만 18~39세."""

    @classmethod
    def setUpClass(cls):
        cls.policy = item("POL_STG_ICN_001")

    def test_18_inclusion_claim_is_corrected(self):
        body = ask(self.policy, 18, "다만 입력하신 만 18세는 연령 기준에 포함되지만, 지역과 재직 요건은 추가 확인이 필요해요. [1]",
                   "중소기업 재직청년 복지공유제 지원 지원할 수 있나요?")
        age = next(c for c in body["policy_checks"][0]["conditions"] if c["field"] == "age")
        self.assertIn("포함되지 않습니다", age["reason"])
        self.assertIn("만 18세는 연령 기준(만 19~39세)에 포함되지 않고", body["answer"])
        self.assertEqual(body["notice"]["kind"], "warn")

    def test_19_inclusion_claim_is_kept(self):
        text = "입력하신 만 19세는 연령 기준에 포함되지만, 지역과 재직 요건은 추가 확인이 필요해요. [1]"
        body = ask(self.policy, 19, text, "중소기업 재직청년 복지공유제 지원 지원할 수 있나요?")
        # 나이 절은 그대로 두고, 인천 정책은 접수 unknown이라 합의 문구만 끝에 붙는다(#103·#147, 계약 §7 9번).
        self.assertEqual(body["answer"], text + "\n\n현재 접수 여부는 공식 공고에서 확인해 주세요.")


class EmploymentCardAgeTests(unittest.TestCase):
    """POL_005: 만 75세 이상은 NOT 아래 지원 제외 기준."""

    @classmethod
    def setUpClass(cls):
        cls.policy = item("POL_005")

    def test_76_inclusion_claim_is_corrected_when_exclusion_is_confirmed(self):
        # 동결 릴리스의 정적 플래그(EMPLOYMENT_NOT_MODELED)는 종합 판정을 보류해 제외 사유를 넘기지 않는다.
        # 실제 R3D 상담(issue122-befe2d0/ask-api.json, 76세 2/2)처럼 제외 사유가 확정 전달되는 상태를 명시적으로 만든다.
        policy = copy.deepcopy(self.policy)
        policy["data_quality"] = {**policy["data_quality"], "hold_auto_eligibility": False, "hold_reasons": [],
                                  "affected_attributes": []}
        body = ask(policy, 76, "나이 기준만 보면 만 76세도 포함되는 것으로 안내됩니다. 다른 제외 조건도 확인해 주세요 [1].",
                   "국민내일배움카드의 나이 기준을 충족하나요?")
        check = body["policy_checks"][0]
        self.assertTrue(any(r["field"] == "age" for r in check["exclusion_reasons"]))
        self.assertIn("입력하신 만 76세는 지원 제외 기준(만 75세 이상(상한 없음))에 해당돼요.", body["answer"])

    def test_76_is_unchanged_when_exclusion_is_not_confirmed(self):
        # 품질 보류로 제외 사유가 전달되지 않으면 나이 조건의 방향(요건/제외)을 단정하지 않고 그대로 둔다.
        text = "나이 기준만 보면 만 76세도 포함되는 것으로 안내됩니다. 다른 제외 조건도 확인해 주세요 [1]."
        body = ask(self.policy, 76, text, "국민내일배움카드의 나이 기준을 충족하나요?")
        self.assertIsNone(body["policy_checks"][0]["exclusion_reasons"])
        self.assertEqual(body["answer"], text)

    def test_74_answer_is_kept(self):
        text = "현재 입력된 만 74세는 만 75세 이상 제외 기준에는 해당하지 않는 것으로 처리되어 있습니다 [1]."
        body = ask(self.policy, 74, text, "국민내일배움카드의 나이 기준을 충족하나요?")
        self.assertEqual(body["answer"], text)


class ChildMonthTests(unittest.TestCase):
    """POL_GOV_050: 자녀 월령 24개월 이하(LTE 24, #39 합의). 원문은 '24개월 미만 아이'와 '생후 24개월까지'를 함께 쓴다."""

    @classmethod
    def setUpClass(cls):
        cls.policy = item("POL_GOV_050")

    def test_24_month_exclusion_claim_is_corrected(self):
        # dev b5617b2 실제 LLM 답변 문장(가상 프로필 32세·24개월).
        body = ask(self.policy, 32, "또한 자녀 월령 24개월은 원문 기준인 24개월 미만에 포함되지 않으므로, "
                                    "이 기준만 보면 대상에서 제외됩니다. [1]",
                   "장애인가정 양육비용 지원 받을 수 있나요?", child_age_months=24)
        months = next(c for c in body["policy_checks"][0]["conditions"] if c["field"] == "children")
        self.assertIn("입력한 24개월은 포함됩니다", months["reason"])
        self.assertIn("입력하신 자녀 월령 24개월은 월령 기준(24개월 이하)에 포함돼요.", body["answer"])
        self.assertNotIn("제외됩니다", body["answer"])

    def test_25_month_answer_is_kept(self):
        text = "자녀 월령이 25개월이라, 원문에 적힌 대상 요건과는 맞지 않습니다. [1]"
        body = ask(self.policy, 32, text, "장애인가정 양육비용 지원 받을 수 있나요?", child_age_months=25)
        self.assertTrue(body["answer"].startswith(text))


if __name__ == "__main__":
    unittest.main()
