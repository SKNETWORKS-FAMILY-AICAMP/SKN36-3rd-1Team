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
        # 한 정책 답변이라 정책명 없이 안내한다(#122 후속).
        self.assertIn(f"{MISSING}. 신청 방법은 공식 공고에서 확인해 주세요. [2]\n\n", fixed)
        self.assertNotIn(f"{NAME}:", fixed)

    def test_answer_that_already_says_no_link_gets_no_second_notice(self):
        # 답변이 스스로 '신청 링크는 확인되지 않았어요'라고 밝혔으면 웹 신청 절만 빼고 보정 안내를 또 넣지 않는다(#122 후속).
        text = "만 19세는 연령 범위에 들어가요. 신청 URL은 정부24 페이지이며, 실제 신청 링크는 확인되지 않았어요. [1][2]"
        fixed = aligned(text)
        self.assertNotIn(".,", fixed)
        self.assertEqual(fixed.count("신청 링크는 확인되지 않았어요"), 1)
        self.assertNotIn("정부24 페이지", fixed)
        self.assertIn("만 19세는 연령 범위에 들어가요. 실제 신청 링크는 확인되지 않았어요. [1][2]", fixed)

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


class StatedMissingLinkTests(unittest.TestCase):
    """#122 1번 재검증(dev a58c516, R3D·gpt-5.4-mini)에서 잡은 실제 원본 답변을 줄인 형태. 정책은 가상(sample_only)."""

    OPEN = {**UNKNOWN, "reception_status": "open"}

    def test_online_clause_is_dropped_when_answer_already_says_no_link(self):
        # POL_005 형태: 온라인 신청 절만 빼고, '직접 신청 링크는 확인되지 않았어요'는 한 번만 남는다.
        text = ("가상 정책은 현재 접수 중이에요. 신청은 온라인으로 가상누리에서 신청서를 작성하거나, 관할 센터에 방문해서 할 수 있습니다. "
                "다만 직접 신청 링크는 확인되지 않았어요. [1][2]\n\n"
                "신청 방법은 원문에 확인됩니다. 가상누리에서 온라인 신청이 가능하고, 관할 센터 방문 신청도 가능합니다. [1]")
        fixed, changed = align_application_links(text, CONTEXTS, [self.OPEN])
        self.assertTrue(changed)
        self.assertEqual(fixed.count(MISSING), 1)
        self.assertNotIn("온라인", fixed)
        self.assertNotIn(".,", fixed)
        self.assertNotIn(f"{NAME}:", fixed)
        self.assertIn("현재 접수 중이에요. 관할 센터에 방문해서 할 수 있습니다.", fixed)
        self.assertIn("신청 방법은 원문에 확인됩니다. 관할 센터 방문 신청도 가능합니다. [1]", fixed)

    def test_negated_online_application_is_not_rewritten(self):
        # POL_GOV_016 형태: '온라인·우편 접수는 불가'는 웹 신청 단정이 아니다. 목적격 접수 문구도 이미 안내한 것으로 본다.
        text = ("가상 정책은 현재 접수 여부를 공식 공고에서 확인해 주세요. 신청은 가상구청에 방문 접수만 가능하고, "
                "온라인·우편 접수는 불가합니다. 다만 공식 신청 주소는 확인되지 않았어요. [1][2]")
        self.assertEqual(aligned(text), text)

    def test_heading_paragraph_notice_is_not_repeated_in_last_paragraph(self):
        # 인천 형태: 첫 문단에 '신청 주소는 확인되지 않았어요'가 있으면 마지막 문단의 홈페이지 접수 절만 빼고 안내를 반복하지 않는다.
        text = ("가상 정책은 현재 접수 여부는 공식 공고에서 확인해 주세요. 공식 신청 주소는 확인되지 않았어요. [2]\n\n"
                "신청 방법은 요약에 따르면 가상협의회 홈페이지 접수로 안내되어 있지만, 확인된 공식 신청 URL은 따로 제공되지 않았습니다. [2]")
        fixed = aligned(text)
        self.assertNotIn(MISSING, fixed)
        self.assertNotIn("홈페이지 접수", fixed)
        self.assertEqual(fixed.count(PHRASE), 1)
        # 마지막 문단의 '신청 URL은 따로 제공되지 않았습니다'는 첫 문단과 같은 안내라 빠진다(PR #161 리뷰 후속).
        self.assertNotIn("제공되지 않았습니다", fixed)
        # 인용만 남은 문단은 앞 문단에 붙이고, 같은 번호가 이어지면 하나로 합친다('[2] [2]' 방지).
        self.assertEqual(fixed, "가상 정책은 현재 접수 여부는 공식 공고에서 확인해 주세요. 공식 신청 주소는 확인되지 않았어요. [2]")

    def test_attached_citations_merge_without_losing_new_numbers(self):
        # 빠진 문장의 인용이 앞 묶음과 일부만 겹치면 새 번호만 덧붙인다.
        text = "공식 신청 주소는 확인되지 않았어요. [1][2]\n\n직접 신청 링크는 확인되지 않았어요. [2][3]"
        fixed, changed = align_application_links(text, CONTEXTS + [{"source_id": 3, "policy_id": "A", "text": "z"}], [self.OPEN])
        self.assertTrue(changed)
        self.assertEqual(fixed, "공식 신청 주소는 확인되지 않았어요. [1][2][3]")

    def test_online_method_verified_by_source_is_kept(self):
        # POL_005 형태: 원문에 온라인 신청서 작성 안내가 있으면 '온라인으로 신청'은 방법 설명이라 남긴다(#158 인계).
        contexts = [{"source_id": 1, "policy_id": "A", "text": "PC나 휴대폰으로 온라인(가상누리) 신청서를 작성합니다.\n관할 고용센터에 방문해서 신청할 수도 있습니다."}]
        text = "신청은 온라인으로 가상누리에서 하거나 관할 고용센터를 방문해서 할 수 있습니다. 다만 직접 신청 링크는 확인되지 않았어요. [1]"
        self.assertEqual(align_application_links(text, contexts, [self.OPEN]), (text, False))

    def test_online_claim_without_source_basis_is_corrected(self):
        # '홈페이지'라는 말이 없어도, 원문에 온라인 신청 근거가 없으면 웹 신청 단정으로 보고 보정한다.
        fixed, changed = align_application_links("신청은 온라인으로 가상누리에서 하면 됩니다 [1].", CONTEXTS, [self.OPEN])
        self.assertTrue(changed)
        self.assertEqual(fixed, f"{MISSING}. 신청 방법은 공식 공고에서 확인해 주세요. [1]")

    def test_homepage_claim_is_corrected_even_if_source_mentions_online(self):
        contexts = [{"source_id": 1, "policy_id": "A", "text": "온라인 신청서를 작성합니다."}]
        fixed, changed = align_application_links("가상누리 홈페이지에서 온라인으로 신청하세요 [1].", contexts, [self.OPEN])
        self.assertTrue(changed)
        self.assertIn(MISSING, fixed)

    def test_unverified_online_and_verified_job_center_visit_keeps_visit(self):
        contexts = [{"source_id": 1, "policy_id": "A", "text": "관할 고용센터에 방문해서 신청할 수 있습니다."}]
        fixed, _ = align_application_links("신청은 온라인으로 하거나 고용센터 방문으로 할 수 있어요 [1].", contexts, [self.OPEN])
        self.assertTrue(fixed.startswith(f"고용센터 방문 신청 방법은 확인되지만, {MISSING}"))

    def test_reception_phrase_with_hyeonjae_neun_is_not_appended_again(self):
        # 인천 형태: '접수 여부도 현재는 공식 공고에서 확인해 주세요'를 합의 문구로 맞추고 끝에 다시 붙이지 않는다.
        text = "가상 정책 자격은 추가 확인이 필요해요. 접수 여부도 현재는 공식 공고에서 확인해 주세요[1].\n\n공식 신청 주소는 확인되지 않았어요. [2]"
        fixed = aligned(text)
        self.assertEqual(fixed.count(PHRASE), 1)
        self.assertNotIn("현재는", fixed)

    def test_consecutive_llm_missing_link_sentences_keep_first_only(self):
        # 인천 형태: LLM이 '주소는 확인되지 않았어요'와 '링크는 확인되지 않았습니다'를 연달아 쓰면 첫 문장만 남긴다.
        for text in ("공식 신청 주소는 확인되지 않았어요. 판정 정보의 공식 신청 URL이 비어 있어 직접 신청 링크는 확인되지 않았습니다. [2]",
                     "공식 신청 주소는 확인되지 않았어요. 확인된 신청 URL은 제공되지 않았습니다[2]."):
            with self.subTest(text=text):
                fixed, changed = align_application_links(text, CONTEXTS, [self.OPEN])
                self.assertTrue(changed)
                self.assertEqual(fixed, "공식 신청 주소는 확인되지 않았어요. [2]")

    def test_mixed_missing_and_web_claim_clause_gets_notice(self):
        # PR #161 리뷰 필수 1: 미확인 문구와 웹 신청 단정이 한 절에 있으면 절이 통째로 사라지지 않고 보정 안내가 들어간다.
        text = "가상 정책은 만 19~34세 대상이에요 [1]. 공식 신청 주소는 확인되지 않았지만 가상 홈페이지에서 신청할 수 있어요 [2]."
        fixed, _ = align_application_links(text, CONTEXTS, [self.OPEN])
        self.assertEqual(fixed, f"가상 정책은 만 19~34세 대상이에요 [1]. {MISSING}. 신청 방법은 공식 공고에서 확인해 주세요. [2]")

    def test_unrelated_eopsi_is_not_a_missing_link_statement(self):
        # PR #161 리뷰 필수 2: '신청 주소 변경 없이'는 링크 미확인 안내가 아니다.
        fixed, _ = align_application_links("신청 주소 변경 없이 가상 홈페이지에서 신청할 수 있어요 [1].", CONTEXTS, [self.OPEN])
        self.assertEqual(fixed, f"{MISSING}. 신청 방법은 공식 공고에서 확인해 주세요. [1]")

    def test_negated_or_unrelated_online_sentences_are_kept(self):
        # PR #161 리뷰 필수 3: 온라인 신청을 부정하거나 신청 행위가 아닌 문장은 보정하지 않는다.
        for text in ("신청서는 온라인 제출이 아니라 우편으로 보내요 [1].",
                     "신청서는 온라인으로 제출할 수 없고 우편으로 보내야 해요 [1].",
                     "접수는 온라인이 아닌 방문으로만 받아요 [1].",
                     "접수는 온라인 대신 우편으로 해요 [1].",
                     "신청 온라인 설명회는 다음 달이에요 [1].",
                     "온라인으로는 신청이 안 됩니다 [1]."):
            with self.subTest(text=text):
                self.assertEqual(align_application_links(text, CONTEXTS, [self.OPEN]), (text, False))

    def test_online_negated_in_source_is_not_verified(self):
        # PR #161 리뷰 선택 1: 원문 어디에든 온라인 신청을 받지 않는다고 하면 온라인 신청 단정을 보정한다.
        contexts = [{"source_id": 1, "policy_id": "A",
                     "text": "방문 접수만 가능하며 온라인 신청은 받지 않습니다.\n선정 결과는 온라인 신청 시스템에서 조회합니다."}]
        fixed, changed = align_application_links("신청은 온라인으로 하면 돼요 [1].", contexts, [self.OPEN])
        self.assertTrue(changed)
        self.assertIn(MISSING, fixed)

    def test_removed_trailing_clause_keeps_sentence_whole(self):
        # PR #161 리뷰 선택 2: 뒷절이 빠져도 '필요하고,'로 끊기지 않고, 빠진 문단은 빈 줄을 남기지 않는다.
        stated = "신청 링크는 확인되지 않았어요."
        fixed, _ = align_application_links(f"{stated} 서류는 신분증이 필요하고, 홈페이지에서 신청하세요 [1]. 기간은 상시예요 [1].",
                                           CONTEXTS, [self.OPEN])
        self.assertEqual(fixed, f"{stated} 서류는 신분증이 필요하고, 신청 방법은 공식 공고에서 확인해 주세요. [1] 기간은 상시예요 [1].")
        fixed, _ = align_application_links(f"{stated} [1]\n\n홈페이지에서 신청하세요.\n\n서류는 신분증 [1].", CONTEXTS, [self.OPEN])
        self.assertEqual(fixed, f"{stated} [1]\n\n서류는 신분증 [1].")

    def test_repeated_missing_sentence_with_other_info_is_kept(self):
        # PR #161 재리뷰 필수 1: 링크 미확인 뒤에 방문·서류·일정 정보가 이어지는 문장은 지우지 않는다.
        for text in ("신청 링크는 확인되지 않았어요 [1]. 신청 주소는 없고 주민센터 방문으로만 접수해요 [1].",
                     "신청 주소는 확인되지 않았어요 [1]. 신청 링크는 없으며 신분증과 통장 사본이 필요해요 [2].",
                     "신청 링크는 없어요 [1]. 신청 주소는 아직 없고 3월에 공개될 예정이에요 [2]."):
            with self.subTest(text=text):
                self.assertEqual(align_application_links(text, CONTEXTS, [self.OPEN]), (text, False))

    def test_dropped_sentence_citation_moves_to_first_notice(self):
        # PR #161 재리뷰 선택 1: 뺀 문장의 인용은 같은 안내를 하는 첫 문장으로 옮긴다(무관한 앞 문장에 붙지 않음).
        contexts = CONTEXTS + [{"source_id": 3, "policy_id": "A", "text": "z"}]
        cases = {
            "**신청 링크는 확인되지 않았어요** [1]. 서류는 신분증. **신청 URL은 없습니다** [2].":
                "**신청 링크는 확인되지 않았어요** [1][2]. 서류는 신분증.",
            "지원금은 월 10만원이에요 [1]. 신청 링크는 없어요 [2]. 다만 신청 주소는 확인되지 않았어요 [3].":
                "지원금은 월 10만원이에요 [1]. 신청 링크는 없어요 [2][3].",
            "신청 링크는 확인되지 않았어요 [1].\n\n신청 주소는 따로 없어요. 서류는 신분증입니다 [1].":
                "신청 링크는 확인되지 않았어요 [1].\n\n서류는 신분증입니다 [1].",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(align_application_links(text, contexts, [self.OPEN])[0], expected)

    def test_removed_claim_sentence_citation_joins_previous_group(self):
        # 웹 신청 단정 문장 전체가 빠지면 그 인용은 앞 문장 인용 묶음으로 들어가고 같은 번호는 하나로 합친다.
        fixed, _ = align_application_links("신청 주소는 따로 없어요 [1]. 가상 홈페이지에서 신청할 수 있어요 [1].", CONTEXTS, [self.OPEN])
        self.assertEqual(fixed, "신청 주소는 따로 없어요 [1].")

    def test_removed_sentence_citation_goes_to_notice_sentence(self):
        # 빠진 웹 신청 문장의 인용은 링크 안내 문장의 인용 묶음으로 간다(무관한 '기간' 문장에 붙지 않음, PR #161 4차 리뷰).
        cases = {
            "신청 주소는 따로 없어요 [1]. 가상 홈페이지에서 신청할 수 있어요 [2]. 기간은 상시예요 [1].":
                "신청 주소는 따로 없어요 [1][2]. 기간은 상시예요 [1].",
            "신청 링크는 없어요 [1]. 기간은 상시예요 [1]. 홈페이지에서 신청하세요 [2].":
                "신청 링크는 없어요 [1][2]. 기간은 상시예요 [1].",
        }
        contexts = CONTEXTS + [{"source_id": 3, "policy_id": "A", "text": "z"}]
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(align_application_links(text, contexts, [self.OPEN])[0], expected)

    def test_citation_inside_sentence_keeps_policy_scope(self):
        # PR #161 4차 리뷰 필수 1: 인용 뒤 문장을 따로 나누지 않아 인용으로 정책을 정하는 기존 판정을 유지한다.
        valid = {"policy_id": "B", "policy_name": "가상 다른 정책", "official_apply_url": "https://example.org/apply",
                 "reception_status": "open"}
        contexts = CONTEXTS + [{"source_id": 3, "policy_id": "B", "text": "신청방법"}]
        text = "홈페이지에서 신청할 수 있어요 [3] 서류는 신분증이에요."
        self.assertEqual(align_application_links(text, contexts, [self.OPEN, valid]), (text, False))
        self.assertEqual(align_application_links("홈페이지에서 신청할 수 있어요 [9] 서류는 신분증이에요.", CONTEXTS, [self.OPEN]),
                         ("홈페이지에서 신청할 수 있어요 [9] 서류는 신분증이에요.", False))

    def test_paragraph_break_after_dropped_line_is_kept(self):
        # PR #161 4차 리뷰 필수 2: 앞이 줄바꿈, 뒤가 문단 구분이면 문단 구분을 남긴다.
        fixed, _ = align_application_links("신청 링크는 없어요 [1].\n- 기간: 상시\n- 홈페이지에서 신청하세요\n\n다음 문단 [1].",
                                           CONTEXTS, [self.OPEN])
        self.assertEqual(fixed, "신청 링크는 없어요 [1].\n- 기간: 상시\n\n다음 문단 [1].")
        fixed, _ = align_application_links("신청 링크는 확인되지 않았어요 [1].\n자격 설명입니다.\n신청 주소는 없어요.\n\n다음 문단 [1].",
                                           CONTEXTS, [self.OPEN])
        self.assertEqual(fixed, "신청 링크는 확인되지 않았어요 [1].\n자격 설명입니다.\n\n다음 문단 [1].")

    def test_mid_sentence_citation_still_corrected(self):
        # PR #161 3차 리뷰 필수 1: 문장 중간 인용 뒤에서 절을 나누지 않아 웹 신청 단정이 그대로 보정된다.
        for text in ("가상 정책은 홈페이지에서 [1] 신청할 수 있어요.", "온라인 [1] 신청이 가능해요."):
            with self.subTest(text=text):
                fixed, changed = align_application_links(text, CONTEXTS, [self.OPEN])
                self.assertTrue(changed)
                self.assertTrue(fixed.startswith(f"{MISSING}."))

    def test_multi_policy_citations_mid_sentence_keep_policy_scope(self):
        # PR #161 3차 리뷰 필수 2: '정책A [1] 정책B [3] 모두 …'는 두 정책 기준으로 보정하고, 주소를 직접 쓰면 거절한다.
        other = {"policy_id": "B", "policy_name": "가상 다른 정책", "official_apply_url": None, "reception_status": "open"}
        contexts = CONTEXTS + [{"source_id": 3, "policy_id": "B", "text": "신청방법"}]
        text = f"{NAME} [1] 가상 다른 정책 [3] 모두 홈페이지에서 신청할 수 있어요."
        fixed, _ = align_application_links(text, contexts, [self.OPEN, other])
        self.assertIn(f"{NAME}: {MISSING}", fixed)
        self.assertIn(f"가상 다른 정책: {MISSING}", fixed)
        from backend.rag.pipeline import AskError
        with self.assertRaises(AskError):
            align_application_links(f"{NAME} [1] 가상 다른 정책 [3] 모두 https://example.org 에서 신청하세요.", contexts, [self.OPEN, other])

    def test_unrelated_list_and_table_citations_are_not_moved(self):
        # PR #161 3차 리뷰 필수 3: 보정과 무관한 목록·표의 인용은 옮기지 않는다. 들여쓰기·인용문도 유지한다.
        text = ("신청 링크는 없어요 [1]. 홈페이지에서 신청하세요 [2].\n\n1. 서류 [1]. [2] 항목은 별도\n2. 기간\n\n"
                "| 서류 | [1]. [2] |\n    들여쓴 문단\n  > 인용문")
        fixed, _ = align_application_links(text, CONTEXTS, [self.OPEN])
        self.assertEqual(fixed, "신청 링크는 없어요 [1][2].\n\n1. 서류 [1]. [2] 항목은 별도\n2. 기간\n\n"
                                "| 서류 | [1]. [2] |\n    들여쓴 문단\n  > 인용문")

    def test_dropped_list_item_leaves_no_blank_line(self):
        # PR #161 3차 리뷰 선택 2: 목록에서 반복 안내 항목이 빠져도 빈 줄이 생기지 않는다.
        text = "- 신청 링크는 확인되지 않았어요 [1]\n- 신청 주소는 없어요 [2]\n- 서류: 신분증 [1]"
        fixed, _ = align_application_links(text, CONTEXTS, [self.OPEN])
        self.assertEqual(fixed, "- 신청 링크는 확인되지 않았어요 [1][2]\n- 서류: 신분증 [1]")

    def test_existing_notice_keeps_paraphrased_deadline_sentence(self):
        # PR #161 재리뷰 선택 3: 합의 문구(목적격)가 이미 있으면 '신청기한 미확인' 문장을 합의 문구로 또 바꾸지 않는다.
        text = "이 정책은 **현재 접수 여부를 공식 공고에서 확인해 주세요**. 원문에는 신청기한이 확인되지 않고, 접수 상태도 알 수 없습니다. [1]"
        fixed = aligned(text)
        self.assertIn("신청기한이 확인되지 않고", fixed)
        self.assertEqual(fixed.count("접수 여부"), 1)

    def test_multi_policy_answer_keeps_policy_name_label(self):
        other = {"policy_id": "B", "policy_name": "가상 다른 정책", "official_apply_url": None, "reception_status": "open"}
        contexts = CONTEXTS + [{"source_id": 3, "policy_id": "B", "text": "신청방법"}]
        fixed, _ = align_application_links(f"{NAME}은 홈페이지에서 신청할 수 있어요 [1].", contexts, [self.OPEN, other])
        self.assertTrue(fixed.startswith(f"{NAME}: {MISSING}."))


if __name__ == "__main__":
    unittest.main()
