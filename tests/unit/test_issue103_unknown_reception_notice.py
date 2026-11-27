"""#103·#147 접수 미확인(unknown) 합의 문구 보정 단위 테스트. 가상 정책만 사용한다(sample_only)."""

import unittest

from backend.rag.reception_notice import UNKNOWN_RECEPTION, ensure_unknown_reception_notice

PHRASE = "현재 접수 여부는 공식 공고에서 확인해 주세요"


def check(pid, name, reception):
    return {"policy_id": pid, "policy_name": name, "official_apply_url": None, "reception_status": reception}


ONE = [{"source_id": 1, "policy_id": "A"}, {"source_id": 2, "policy_id": "A"}]
TWO = [{"source_id": 1, "policy_id": "A"}, {"source_id": 2, "policy_id": "B"}]
A_UNKNOWN = check("A", "가상위원회 참여", "unknown")
B_CLOSED = check("B", "가상 행정인턴", "closed")
B_UNKNOWN = check("B", "가상 감면", "unknown")


class UnknownReceptionNoticeTests(unittest.TestCase):
    def test_paraphrase_is_replaced_with_agreed_phrase(self):
        # 실제 LLM 바꿔 쓰기(dev 4708009, 세종 지역 공란 2회차)를 축약한 가상 문장.
        text = "모집 일정은 원문에 없어요 [1]. 현재 접수 여부도 공식 원문에서 확인되지 않았습니다 [1].\n\n대상은 만 19~39세예요 [2]."
        fixed, changed = ensure_unknown_reception_notice(text, ONE, [A_UNKNOWN])
        self.assertTrue(changed)
        self.assertNotIn("확인되지 않았습니다 [1]", fixed)
        self.assertIn(f"{UNKNOWN_RECEPTION} [1]", fixed)
        self.assertEqual(fixed.count(PHRASE), 1)
        self.assertIn("대상은 만 19~39세예요 [2].", fixed)

    def test_other_paraphrases(self):
        for sentence in ("접수 상태는 확인할 수 없어요 [1].", "접수 여부는 원문에 명시되어 있지 않습니다 [1].",
                         "현재 접수 가능 여부는 알 수 없습니다 [1]."):
            with self.subTest(sentence=sentence):
                fixed, changed = ensure_unknown_reception_notice(sentence, ONE, [A_UNKNOWN])
                self.assertTrue(changed)
                self.assertEqual(fixed, f"{UNKNOWN_RECEPTION} [1]")

    def test_missing_phrase_is_appended_for_single_policy(self):
        text = "2011년 이전 취업자는 감면 대상이 아닙니다 [1]."
        fixed, changed = ensure_unknown_reception_notice(text, ONE, [A_UNKNOWN])
        self.assertTrue(changed)
        self.assertEqual(fixed, f"{text}\n\n{UNKNOWN_RECEPTION}")

    def test_existing_phrase_is_untouched(self):
        text = f"신청 방법은 공고문 참조예요 [1]. {UNKNOWN_RECEPTION} [1]"
        self.assertEqual(ensure_unknown_reception_notice(text, ONE, [A_UNKNOWN]), (text, False))

    def test_non_unknown_policies_are_untouched(self):
        for reception in ("open", "closed", "before"):
            with self.subTest(reception=reception):
                text = "접수 여부는 확인되지 않았습니다 [1]."
                result = ensure_unknown_reception_notice(text, ONE, [check("A", "가상위원회 참여", reception)])
                self.assertEqual(result, (text, False))

    def test_uncited_unknown_policy_is_ignored(self):
        # 검색에는 있었지만 답변이 인용하지 않은 unknown 정책은 안내 대상이 아니다.
        text = "가상 행정인턴은 접수가 마감됐어요 [2]."
        self.assertEqual(ensure_unknown_reception_notice(text, TWO, [A_UNKNOWN, B_CLOSED]), (text, False))

    def test_multi_policy_answer_names_the_unknown_policy(self):
        text = "가상위원회 참여는 공고문 참조예요 [1].\n\n가상 행정인턴은 접수가 마감됐어요 [2]."
        fixed, changed = ensure_unknown_reception_notice(text, TWO, [A_UNKNOWN, B_CLOSED])
        self.assertTrue(changed)
        self.assertTrue(fixed.endswith(f"가상위원회 참여: {UNKNOWN_RECEPTION}"))
        self.assertNotIn("가상 행정인턴:", fixed)

    def test_paraphrase_citing_other_status_policy_is_untouched(self):
        text = "가상위원회 참여 안내예요 [1]. 접수 여부는 확인되지 않았습니다 [1][2]."
        fixed, _ = ensure_unknown_reception_notice(text, TWO, [A_UNKNOWN, B_CLOSED])
        self.assertIn("접수 여부는 확인되지 않았습니다 [1][2].", fixed)
        self.assertTrue(fixed.endswith(f"가상위원회 참여: {UNKNOWN_RECEPTION}"))

    def test_each_unknown_policy_needs_its_own_phrase(self):
        text = f"가상위원회 참여는 공고문 참조예요. {UNKNOWN_RECEPTION} [1]\n\n가상 감면은 2012년 이후 취업자만 대상이에요 [2]."
        fixed, changed = ensure_unknown_reception_notice(text, TWO, [A_UNKNOWN, B_UNKNOWN])
        self.assertTrue(changed)
        self.assertTrue(fixed.endswith(f"가상 감면: {UNKNOWN_RECEPTION}"))
        self.assertNotIn(f"가상위원회 참여: {UNKNOWN_RECEPTION}", fixed)

    def test_long_mixed_sentence_is_kept_and_phrase_appended(self):
        text = ("다만 이번 근거에는 신청 자격의 세부 기준이나 모집 일정이 없고 접수 여부도 확인되지 않아, "
                "신청 가능 여부와 접수 시점은 공고문에서 추가 확인이 필요하며 지원 내용도 공고별로 다를 수 있습니다 [1].")
        fixed, changed = ensure_unknown_reception_notice(text, ONE, [A_UNKNOWN])
        self.assertTrue(changed)
        self.assertTrue(fixed.startswith(text))
        self.assertTrue(fixed.endswith(UNKNOWN_RECEPTION))

    def test_particle_variant_is_normalized_in_place(self):
        # 실제 LLM(수정 후 재확인, 세종 선택 상담 2회차)이 '는' 대신 '도'를 쓴 문장을 축약했다.
        text = "거주 요건은 다시 확인이 필요하고, 현재 접수 여부도 공식 공고에서 확인해 주세요. [2][1]"
        fixed, changed = ensure_unknown_reception_notice(text, ONE, [A_UNKNOWN])
        self.assertTrue(changed)
        self.assertEqual(fixed, "거주 요건은 다시 확인이 필요하고, 현재 접수 여부는 공식 공고에서 확인해 주세요. [2][1]")

    def test_schedule_wording_is_not_treated_as_reception_paraphrase(self):
        text = "모집 일정은 원문에 명시되어 있지 않습니다 [1]."
        fixed, _ = ensure_unknown_reception_notice(text, ONE, [A_UNKNOWN])
        self.assertTrue(fixed.startswith(text))


if __name__ == "__main__":
    unittest.main()
