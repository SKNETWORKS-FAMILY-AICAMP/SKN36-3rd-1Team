"""입력 나이 결론을 Checker 나이 판정에 맞추는 보정 단위 테스트. 가상 정책만 사용한다(sample_only).

Checker 사유 문구는 backend/eligibility/checker.py의 나이 비교 결과 형식을 그대로 쓴다.
"""

import unittest

from backend.rag.age_verdict import align_age_verdicts

HOLD = " 데이터 품질 경고로 자동 판정을 보류합니다."
AND = {"field": "eligibility", "label": "자격 요건", "status": "needs_check", "reason": "모든 조건을 충족해야 합니다(종합 결과)."}
OR = {"field": "eligibility", "label": "자격 요건", "status": "eligible", "reason": "조건 중 하나 이상 충족해야 합니다(종합 결과)."}
NOT = {"field": "eligibility", "label": "자격 요건", "status": "ineligible", "reason": "해당 조건이 충족되지 않아야 합니다(종합 결과)."}
ONE = [{"source_id": 1, "policy_id": "A"}, {"source_id": 2, "policy_id": "A"}]


def requirement(age, low=19, high=39):
    inside = low <= age <= high
    reason = f"만 {low}~{high}세 기준에 입력한 만 {age}세가 {'포함됩니다' if inside else '포함되지 않습니다'}." + HOLD
    return {"policy_id": "A", "policy_name": "가상 재직청년 지원",
            "conditions": [{"field": "age", "label": "나이", "status": "needs_check", "reason": reason}, AND],
            "exclusion_reasons": None}


def exclusion(age, low=75):
    inside = age >= low
    reason = f"만 {low}세 이상(상한 없음) 기준에 입력한 만 {age}세가 {'포함됩니다' if inside else '포함되지 않습니다'}."
    return {"policy_id": "A", "policy_name": "가상 훈련카드",
            "conditions": [{"field": "age", "label": "나이", "status": "eligible" if inside else "ineligible", "reason": reason},
                           OR, NOT, {**AND, "status": "ineligible" if inside else "needs_check"}],
            "exclusion_reasons": [{"field": "age", "label": "나이",
                                   "reason": reason + " 해당 조건은 지원 제외 기준이며, 입력하신 정보가 제외 대상에 해당합니다."}]
            if inside else None}


class RequirementAgeTests(unittest.TestCase):
    def test_wrong_inclusion_is_corrected_mid_sentence(self):
        # 실제 오답(dev 0cb19f6 인천 18세)을 가상 정책으로 축약했다.
        text = "대상연령은 18세~39세로 안내돼 있습니다. 다만 입력하신 만 18세는 연령 기준에 포함되지만, 지역 요건은 추가 확인이 필요해요. [1]"
        fixed, changed = align_age_verdicts(text, ONE, [requirement(18)], 18)
        self.assertTrue(changed)
        self.assertIn("다만 입력하신 만 18세는 연령 기준(만 19~39세)에 포함되지 않고, 지역 요건은", fixed)
        # 원문 연령 표기 설명과 인용 번호는 그대로 둔다.
        self.assertTrue(fixed.startswith("대상연령은 18세~39세로 안내돼 있습니다."))
        self.assertTrue(fixed.endswith("[1]"))

    def test_wrong_exclusion_is_corrected_at_sentence_end(self):
        fixed, changed = align_age_verdicts("만 27세는 연령 기준에 맞지 않습니다 [1].", ONE, [requirement(27)], 27)
        self.assertTrue(changed)
        self.assertEqual(fixed, "입력하신 만 27세는 연령 기준(만 19~39세)에 포함돼요. [1]")

    def test_consistent_answers_are_unchanged(self):
        for age, text in ((19, "입력하신 만 19세는 연령 범위에 들어가지만, 재직 요건은 확인이 필요해요 [1]."),
                          (40, "따라서 현재 입력된 만 40세는 연령 기준에 맞지 않아 지원 대상에서 제외됩니다 [1]."),
                          (18, "만 18세는 19~39세 기준에 포함되지 않습니다 [1].")):
            with self.subTest(age=age):
                self.assertEqual(align_age_verdicts(text, ONE, [requirement(age)], age), (text, False))

    def test_range_descriptions_and_hedges_are_unchanged(self):
        for text in ("대상연령: 18세~39세에 해당하는 청년 [1].", "18세 이상 39세 이하가 대상입니다 [1].",
                     "만 18세가 연령 기준에 포함되는지는 공고에서 확인해 주세요 [1].",
                     "나이·지역·재직 요건 충족 여부를 확정할 수 없어요 [1]."):
            with self.subTest(text=text):
                self.assertEqual(align_age_verdicts(text, ONE, [requirement(18)], 18), (text, False))

    def test_missing_age_or_unparsed_reason_is_unchanged(self):
        text = "만 18세는 연령 기준에 포함돼요 [1]."
        self.assertEqual(align_age_verdicts(text, ONE, [requirement(18)], None), (text, False))
        odd = requirement(18)
        odd["conditions"][0]["reason"] = "입력한 만 18세는 데이터에 저장된 연령 범위에 포함됩니다. 다만 실제 적용 연령 제한은 공식 공고로 확인해야 합니다."
        self.assertEqual(align_age_verdicts(text, ONE, [odd], 18), (text, False))

    def test_or_group_without_confirmed_exclusion_is_unchanged(self):
        check = requirement(18)
        check["conditions"].append(OR)
        text = "만 18세는 연령 기준에 포함돼요 [1]."
        self.assertEqual(align_age_verdicts(text, ONE, [check], 18), (text, False))


class ExclusionAgeTests(unittest.TestCase):
    def test_confirmed_exclusion_corrects_inclusion_wording(self):
        # 실제 오답(POL_005 76세 직접 질문 2/2)을 가상 정책으로 축약했다.
        for text in ("가상 훈련카드는 **나이 기준만 보면** 만 76세도 포함되는 것으로 안내됩니다. 다른 조건도 확인해 주세요 [1].",
                     "정리하면, **나이만으로는 제외되지 않지만** 다른 제외 사유에 해당하는지는 별도로 확인해야 합니다[1]."):
            with self.subTest(text=text):
                fixed, changed = align_age_verdicts(text, ONE, [exclusion(76)], 76)
                self.assertTrue(changed)
                self.assertIn("입력하신 만 76세는 지원 제외 기준(만 75세 이상(상한 없음))에 해당", fixed)
                self.assertNotIn("**", fixed.split("[1]")[0].split(".")[0])

    def test_correct_exclusion_wording_is_unchanged(self):
        text = "공식 원문에는 만 75세 이상이 제외 기준으로 제시되어 있고, 입력하신 만 80세가 그 기준에 포함됩니다. 따라서 나이 기준을 충족한다고 볼 수 없습니다 [1]."
        self.assertEqual(align_age_verdicts(text, ONE, [exclusion(80)], 80), (text, False))

    def test_age_below_exclusion_without_confirmed_reason_is_unchanged(self):
        # 74세: 나이 조건은 NOT 아래에서 '포함되지 않습니다'지만 제외 대상이 아니다. 방향을 단정하지 않는다.
        text = "현재 입력된 만 74세는, 제공된 판정 정보상 “만 75세 이상” 기준에는 해당하지 않는 것으로 처리되어 있습니다 [1]."
        self.assertEqual(align_age_verdicts(text, ONE, [exclusion(74)], 74), (text, False))


class ScopeTests(unittest.TestCase):
    def test_multi_policy_uses_citation_and_names_the_policy(self):
        other = {**requirement(30, 19, 34), "policy_id": "B", "policy_name": "가상 월세 지원"}
        a = requirement(30, 19, 29)
        contexts = [{"source_id": 1, "policy_id": "A"}, {"source_id": 2, "policy_id": "B"}]
        text = "입력하신 만 30세는 연령 기준에 포함돼요 [1]. 입력하신 만 30세는 연령 기준에 포함돼요 [2]."
        fixed, changed = align_age_verdicts(text, contexts, [a, other], 30)
        self.assertTrue(changed)
        self.assertIn("가상 재직청년 지원: 입력하신 만 30세는 연령 기준(만 19~29세)에 포함되지 않아요. [1]", fixed)
        self.assertTrue(fixed.endswith("입력하신 만 30세는 연령 기준에 포함돼요 [2]."))

    def test_multi_policy_without_citation_is_unchanged(self):
        contexts = [{"source_id": 1, "policy_id": "A"}, {"source_id": 2, "policy_id": "B"}]
        checks = [requirement(30, 19, 29), {**requirement(30), "policy_id": "B", "policy_name": "가상 월세 지원"}]
        text = "입력하신 만 30세는 연령 기준에 포함돼요."
        self.assertEqual(align_age_verdicts(text, contexts, checks, 30), (text, False))


if __name__ == "__main__":
    unittest.main()
