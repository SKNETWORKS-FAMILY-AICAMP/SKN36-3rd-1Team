"""#39 후속: 답변의 자녀 월령 결론을 Checker 판정에 맞춘다.

dev b5617b2 실제 LLM(POL_GOV_050 24개월)이 원문 '24개월 미만'을 따라 "24개월은 포함되지 않으므로, 이 기준만 보면
대상에서 제외됩니다"로 답했다. Checker는 #39 합의(LTE 24)대로 '포함'이었다. 정책·문장은 가상 값이다(sample_only).
"""

import unittest

from backend.rag.age_verdict import align_child_month_verdicts

HOLD = " 데이터 품질 경고로 자동 판정을 보류합니다."
CONTEXTS = [{"source_id": 1, "policy_id": "A", "text": "24개월 미만 아이 (sample_only)"},
            {"source_id": 3, "policy_id": "A", "text": "생후 24개월까지 (sample_only)"}]


def check(months, included, *, policy_id="A", name="가상 양육비 지원", extra=()):
    verb = "포함됩니다." if included else "포함되지 않습니다."
    return {"policy_id": policy_id, "policy_name": name, "conditions": [
        {"field": "children", "status": "needs_check",
         "reason": f"자녀 월령 24개월 이하 기준에 입력한 {months}개월은 {verb}{HOLD}"},
        {"field": "eligibility", "status": "needs_check", "reason": "모든 조건을 충족해야 합니다(종합 결과)." + HOLD},
        *extra]}


def align(text, months, included, checks=None, contexts=CONTEXTS):
    return align_child_month_verdicts(text, contexts, checks or [check(months, included)], months)


class ChildMonthVerdictTests(unittest.TestCase):
    def test_recorded_24_month_exclusion_is_aligned_with_checker(self):
        text = ("다만 입력하신 거주지는 서울특별시라서 전주시 관내 거주 요건과는 맞지 않아 보입니다. "
                "또한 자녀 월령 24개월은 원문 기준인 24개월 미만에 포함되지 않으므로, 이 기준만 보면 대상에서 제외됩니다. [1][3]")
        fixed, changed = align(text, 24, True)
        self.assertTrue(changed)
        self.assertIn("또한 입력하신 자녀 월령 24개월은 월령 기준(24개월 이하)에 포함돼요. [1][3]", fixed)
        self.assertNotIn("제외됩니다", fixed)
        # 다른 조건(거주지) 문장은 그대로 둔다.
        self.assertIn("거주 요건과는 맞지 않아 보입니다.", fixed)

    def test_wrong_inclusion_is_aligned_to_not_included(self):
        fixed, changed = align("입력하신 자녀 월령 25개월은 기준에 포함됩니다. [1]", 25, False)
        self.assertTrue(changed)
        self.assertEqual(fixed, "입력하신 자녀 월령 25개월은 월령 기준(24개월 이하)에 포함되지 않아요. [1]")

    def test_answers_matching_checker_are_unchanged(self):
        for text, months, included in (
            ("자녀 월령 10개월은 24개월 미만 기준에는 들어갑니다. [1]", 10, True),
            ("자녀 월령이 25개월이라, 원문에 적힌 대상 요건과는 맞지 않습니다. [1]", 25, False),
            # 월령은 맞고 다른 조건이 안 맞는 문장(절이 나뉘어 각각 본다).
            ("자녀 월령 10개월은 기준에 들어가지만, 거주지는 맞지 않습니다. [1]", 10, True),
        ):
            with self.subTest(text=text):
                self.assertEqual(align(text, months, included), (text, False))

    def test_rule_description_and_hedges_are_unchanged(self):
        for text in ("지원대상은 24개월 미만 아이를 양육하는 가정입니다. [1]",
                     "자녀 월령 24개월이 기준에 맞는지는 공식 공고에서 확인이 필요합니다. [1]"):
            with self.subTest(text=text):
                self.assertEqual(align(text, 24, True), (text, False))

    def test_missing_months_or_other_condition_shapes_are_unchanged(self):
        text = "자녀 월령 24개월은 대상에서 제외됩니다. [1]"
        self.assertEqual(align_child_month_verdicts(text, CONTEXTS, [check(24, True)], None), (text, False))
        not_group = check(24, True, extra=({"field": "eligibility", "status": "needs_check",
                                             "reason": "해당 조건이 충족되지 않아야 합니다(종합 결과)."},))
        self.assertEqual(align(text, 24, True, [not_group]), (text, False))

    def test_multi_policy_needs_one_target_and_gets_policy_name(self):
        other = check(24, True, policy_id="B", name="가상 돌봄 지원")
        contexts = CONTEXTS + [{"source_id": 2, "policy_id": "B", "text": "x (sample_only)"}]
        # 인용으로 대상이 하나면 그 정책 이름을 붙인다.
        fixed, changed = align("자녀 월령 24개월은 포함되지 않습니다 [1].", 24, True, [check(24, True), other], contexts)
        self.assertTrue(changed)
        self.assertTrue(fixed.startswith("가상 양육비 지원: 입력하신 자녀 월령 24개월은"), fixed)
        # 대상을 하나로 정할 수 없으면 바꾸지 않는다.
        text = "자녀 월령 24개월은 포함되지 않습니다."
        self.assertEqual(align(text, 24, True, [check(24, True), other], contexts), (text, False))


if __name__ == "__main__":
    unittest.main()
