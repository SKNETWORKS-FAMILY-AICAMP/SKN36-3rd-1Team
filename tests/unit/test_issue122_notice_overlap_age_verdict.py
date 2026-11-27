"""#103·#122 후속: 신청 링크 보정 문구 겹침·반복과 입력 나이 결론 보정 단위 테스트.

문장은 dev 0cb19f6 인천 실제 상담(검증표 §13-2)에서 나온 형태를 줄인 것이고, 정책·판정은 가상 값이다(sample_only).
"""

import unittest

from backend.rag.age_verdict import align_age_verdicts
from backend.rag.application_links import align_application_links
from backend.rag.reception_notice import ensure_unknown_reception_notice

NAME = "가상 재직청년 복지 지원"
MISSING = "직접 신청 링크는 확인되지 않았어요"
PHRASE = "현재 접수 여부는 공식 공고에서 확인해 주세요"
CONTEXTS = [{"source_id": 1, "policy_id": "A", "text": "지원대상"}, {"source_id": 2, "policy_id": "A", "text": "신청방법"}]
UNKNOWN = {"policy_id": "A", "policy_name": NAME, "official_apply_url": None, "reception_status": "unknown"}


def aligned(text, checks=(UNKNOWN,)):
    fixed, _ = align_application_links(text, CONTEXTS, list(checks))
    return ensure_unknown_reception_notice(fixed, CONTEXTS, list(checks))[0]


def age_check(reason, *extra):
    conditions = [{"field": "age", "status": "needs_check", "reason": reason}]
    conditions += [{"field": "eligibility", "status": "needs_check", "reason": text} for text in extra]
    return {"policy_id": "A", "policy_name": NAME, "conditions": conditions, "exclusion_reasons": None}


HOLD = " 데이터 품질 경고로 자동 판정을 보류합니다."
AND_GROUP = "모든 조건을 충족해야 합니다(종합 결과)." + HOLD


class NoticeOverlapTests(unittest.TestCase):
    def test_two_comma_clauses_get_one_notice_without_comma_after_period(self):
        text = (f"신청 URL은 정부24 상세 페이지로 안내되어 있고, 홈페이지에서 온라인으로 신청할 수 있습니다 [2].\n\n"
                "접수 여부는 공식 공고에서 확인해 주세요. [1]")
        fixed = aligned(text)
        self.assertEqual(fixed.count(MISSING), 1)
        self.assertEqual(fixed.count(PHRASE), 1)
        self.assertNotIn(".,", fixed)
        self.assertIn(f"{NAME}: {MISSING}. 신청 방법은 공식 공고에서 확인해 주세요. [2]\n\n", fixed)

    def test_comma_after_notice_becomes_space(self):
        text = "만 19세는 연령 범위에 들어가요. 신청 URL은 정부24 페이지이며, 실제 신청 링크는 확인되지 않았어요. [1][2]"
        fixed = aligned(text)
        self.assertNotIn(".,", fixed)
        self.assertIn(f"{PHRASE}. 실제 신청 링크는 확인되지 않았어요. [1][2]", fixed)

    def test_paragraph_break_after_notice_is_kept(self):
        text = "신청 URL은 정부24 상세 페이지입니다 [2].\n\n서류는 가상 확인서입니다 [1]."
        fixed = aligned(text)
        self.assertIn("[2]\n\n서류는 가상 확인서입니다 [1].", fixed)
        self.assertEqual(fixed.count(PHRASE), 1)

    def test_reordered_reception_phrase_is_not_appended_again(self):
        # '접수 여부는 현재 공식 공고에서'(어순만 다름)는 그 자리에서 합의 문구로 맞추고 끝에 다시 붙이지 않는다.
        text = "서류는 가상 확인서입니다 [1].\n\n다만 접수 여부는 현재 공식 공고에서 확인해 주세요 [2]."
        fixed, changed = ensure_unknown_reception_notice(text, CONTEXTS, [UNKNOWN])
        self.assertTrue(changed)
        self.assertEqual(fixed.count(PHRASE), 1)
        self.assertTrue(fixed.endswith(f"다만 {PHRASE} [2]."))

    def test_other_policies_still_get_their_own_notice(self):
        # 두 정책이 같은 답변에 나오면 정책마다 한 번씩은 안내한다.
        contexts = CONTEXTS + [{"source_id": 3, "policy_id": "B", "text": "신청"}]
        other = {**UNKNOWN, "policy_id": "B", "policy_name": "가상 두번째 지원"}
        text = (f"{NAME}은 홈페이지에서 온라인으로 신청할 수 있습니다 [1].\n\n"
                "가상 두번째 지원은 홈페이지에서 온라인으로 신청할 수 있습니다 [3].")
        fixed, _ = align_application_links(text, contexts, [UNKNOWN, other])
        self.assertEqual(fixed.count(MISSING), 2)
        self.assertNotIn(".,", fixed)


class AgeVerdictTests(unittest.TestCase):
    def test_contradicting_age_claim_follows_checker(self):
        # 실제 18세 답변: 정부24 요약(18~39세)을 따라 '포함'으로 안내했지만 Checker는 19~39세 기준 미포함.
        text = ("대상연령은 18세~39세로 안내돼 있습니다. 다만 입력하신 만 18세는 연령 기준에 포함되지만, "
                "지역과 재직 요건을 함께 충족해야 해서 신청 가능 여부는 추가 확인이 필요해요. [1]")
        check = age_check("만 19~39세 기준에 입력한 만 18세가 포함되지 않습니다." + HOLD, AND_GROUP)
        fixed, changed = align_age_verdicts(text, CONTEXTS, [check], 18)
        self.assertTrue(changed)
        self.assertIn("입력하신 만 18세는 연령 기준(만 19~39세)에 포함되지 않고", fixed)
        self.assertNotIn("연령 기준에 포함되지만", fixed)
        # 원문 연령 표기 설명과 유보 절은 그대로 둔다.
        self.assertIn("대상연령은 18세~39세로 안내돼 있습니다.", fixed)
        self.assertIn("신청 가능 여부는 추가 확인이 필요해요. [1]", fixed)

    def test_matching_claims_are_unchanged(self):
        cases = [
            (19, "만 19~39세 기준에 입력한 만 19세가 포함됩니다.", "사용자가 만 19세이므로 연령 범위에는 들어가요 [1]."),
            (40, "만 19~39세 기준에 입력한 만 40세가 포함되지 않습니다.",
             "따라서 현재 입력된 만 40세는 연령 기준에 맞지 않아 지원 대상에서 제외됩니다 [1]."),
        ]
        for age, reason, text in cases:
            with self.subTest(age=age):
                self.assertEqual(align_age_verdicts(text, CONTEXTS, [age_check(reason + HOLD, AND_GROUP)], age),
                                 (text, False))

    def test_no_age_or_unclear_structure_is_unchanged(self):
        text = "입력하신 만 18세는 연령 기준에 포함돼요 [1]."
        reason = "만 19~39세 기준에 입력한 만 18세가 포함되지 않습니다."
        self.assertEqual(align_age_verdicts(text, CONTEXTS, [age_check(reason)], None), (text, False))
        # OR 그룹이 있으면 나이 하나로 방향을 정할 수 없다.
        with_or = age_check(reason, "조건 중 하나 이상 충족해야 합니다(종합 결과).")
        self.assertEqual(align_age_verdicts(text, CONTEXTS, [with_or], 18), (text, False))
        # 나이 조건이 둘이면 건드리지 않는다.
        two = age_check(reason)
        two["conditions"].append({"field": "age", "status": "ok", "reason": "만 18~39세 기준에 입력한 만 18세가 포함됩니다."})
        self.assertEqual(align_age_verdicts(text, CONTEXTS, [two], 18), (text, False))

    def test_range_description_and_hedge_are_unchanged(self):
        check = age_check("만 19~39세 기준에 입력한 만 18세가 포함되지 않습니다.")
        for text in ("대상연령은 18세~39세로 정리돼 있어요 [1].",
                     "만 18세가 연령 기준에 포함되는지는 공식 공고에서 확인이 필요해요 [1]."):
            with self.subTest(text=text):
                self.assertEqual(align_age_verdicts(text, CONTEXTS, [check], 18), (text, False))


if __name__ == "__main__":
    unittest.main()
