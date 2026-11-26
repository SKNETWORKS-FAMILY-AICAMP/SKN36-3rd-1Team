"""공식 자격기준(#76) 확정 규칙 단위 테스트.

모든 정책·문서·값은 구조 검증용 가상 데이터(sample_only)이며 실제 정책 기준이 아니다.
"""

import re
import unittest

from backend.rag.official_requirements import official_requirements


def _flat(text):
    """공백·줄바꿈 차이만 없앤 비교용 문자열(이어 붙인 단서 줄 비교)."""
    return re.sub(r"\s+", " ", text).strip()

TEXT_A = (
    "[지원대상]\n만 19세 이상 34세 이하 가상 청년\n"
    "[선정기준]\n가구 기준 중위소득 150% 이하, 2026년 가상 기준\n"
    "[지원내용]\n월 최대 20만원(가상)\n"
)
TEXT_B = "[지원대상]\n만 65세 이상 가상 어르신\n[지원내용]\n연 1회 30만 원 지원(가상)\n"


def context(pid="POL_A", document_id="DOC_A", chunk_id="CHK_A1", text=TEXT_A, source_id=1):
    return {"source_id": source_id, "policy_id": pid, "document_id": document_id, "chunk_id": chunk_id,
            "url": "https://example.org/notice", "text": text}


def review(label="소득 기준", value="가구 기준 중위소득 150% 이하", pid="POL_A", document_id="DOC_A",
           chunk_id="CHK_A1", status="verified", checks=None):
    return {"policy_id": pid, "label": label, "value": value, "document_id": document_id, "chunk_id": chunk_id,
            "review_status": status,
            "checks": {"value": "verified", "unit": "verified", "year": "not_applicable"} if checks is None else checks}


class OfficialRequirementTests(unittest.TestCase):
    def test_tc01_verified_record_matching_selected_context_is_returned(self):
        rows = official_requirements("POL_A", [context()], [review()])
        self.assertEqual(rows, [{"label": "소득 기준", "value": "가구 기준 중위소득 150% 이하",
                                 "document_id": "DOC_A", "chunk_id": "CHK_A1"}])

    def test_tc02_unverified_or_incomplete_review_is_excluded(self):
        cases = {
            "검수 상태 없음": review(status=None),
            "추출값": review(status="extracted"),
            "검수 대기": review(status="needs_review"),
            "checks 없음": {**review(), "checks": None},
            "단위 검수 대기": review(checks={"value": "verified", "unit": "needs_review", "year": "verified"}),
            "연도 항목 누락": review(checks={"value": "verified", "unit": "verified"}),
            "값 해당없음 처리": review(checks={"value": "not_applicable", "unit": "verified", "year": "verified"}),
        }
        for name, item in cases.items():
            with self.subTest(name):
                self.assertIsNone(official_requirements("POL_A", [context()], [item]))

    def test_tc03_no_source_or_no_review_returns_none(self):
        self.assertIsNone(official_requirements("POL_A", [], [review()]))
        self.assertIsNone(official_requirements("POL_A", [context()], []))
        self.assertIsNone(official_requirements("POL_A", [context()], None))
        # 원문에 없는 값은 검수 기록이 verified라도 내보내지 않는다.
        self.assertIsNone(official_requirements("POL_A", [context()], [review(value="기준 중위소득 180% 이하")]))

    def test_tc04_amount_unit_is_kept_and_changed_unit_is_rejected(self):
        ctx = context(document_id="DOC_B", chunk_id="CHK_B1", pid="POL_B", text=TEXT_B)
        ok = review(label="지원 금액", value="연 1회 30만 원 지원(가상)", pid="POL_B", document_id="DOC_B",
                    chunk_id="CHK_B1")
        self.assertEqual(official_requirements("POL_B", [ctx], [ok])[0]["value"], "연 1회 30만 원 지원(가상)")
        for changed in ("연 1회 300,000원 지원(가상)", "연 1회 30만원 지원(가상)", "연 1회 30만 원"):
            with self.subTest(changed):
                rows = official_requirements("POL_B", [ctx], [{**ok, "value": changed}])
                # 마지막 값은 원문 일부라 통과한다. 단위·숫자 표기가 바뀐 값은 통과하지 않는다.
                self.assertEqual(rows is not None, changed == "연 1회 30만 원")

    def test_tc05_year_in_source_is_kept_verbatim(self):
        item = review(label="선정 기준", value="가구 기준 중위소득 150% 이하, 2026년 가상 기준",
                      checks={"value": "verified", "unit": "verified", "year": "verified"})
        rows = official_requirements("POL_A", [context()], [item])
        self.assertEqual(rows[0]["value"], "가구 기준 중위소득 150% 이하, 2026년 가상 기준")
        self.assertIsNone(official_requirements(
            "POL_A", [context()], [{**item, "value": "가구 기준 중위소득 150% 이하, 2025년 가상 기준"}]))

    def test_tc06_year_absent_in_source_is_never_added(self):
        ctx = context(text="[선정기준]\n가구 기준 중위소득 150% 이하\n")
        plain = review()
        rows = official_requirements("POL_A", [ctx], [plain])
        self.assertEqual(rows[0]["value"], "가구 기준 중위소득 150% 이하")
        self.assertNotIn("년", rows[0]["value"])
        # 검수 기록이 연도를 덧붙여도 원문에 없으므로 내보내지 않는다.
        self.assertIsNone(official_requirements("POL_A", [ctx], [{**plain, "value": "2026년 가구 기준 중위소득 150% 이하"}]))

    def test_tc07_duplicates_keep_first_in_context_order(self):
        second = context(chunk_id="CHK_A2", source_id=2)
        rows = official_requirements("POL_A", [context(), second], [
            review(chunk_id="CHK_A2"), review(), review(value="가구 기준  중위소득\n150% 이하"),
        ])
        self.assertEqual([(row["chunk_id"], row["value"]) for row in rows], [("CHK_A1", "가구 기준 중위소득 150% 이하")])

    def test_tc08_other_policy_records_and_contexts_are_never_mixed(self):
        contexts = [context(), context(pid="POL_B", document_id="DOC_B", chunk_id="CHK_B1", text=TEXT_B, source_id=2)]
        reviews = [review(), review(label="연령", value="만 65세 이상 가상 어르신", pid="POL_B",
                                    document_id="DOC_B", chunk_id="CHK_B1")]
        self.assertEqual([row["label"] for row in official_requirements("POL_A", contexts, reviews)], ["소득 기준"])
        self.assertEqual([row["label"] for row in official_requirements("POL_B", contexts, reviews)], ["연령"])
        # POL_A 기록이 POL_B 청크를 가리키거나, POL_B 원문 값을 POL_A 이름으로 적어도 연결하지 않는다.
        crossed = [review(label="연령", value="만 65세 이상 가상 어르신", document_id="DOC_B", chunk_id="CHK_B1")]
        self.assertIsNone(official_requirements("POL_A", contexts, crossed))

    def test_tc09_conflicting_values_for_same_label_are_not_chosen(self):
        text = "[선정기준]\n소득 기준 53% 이하(가상)\n[참고]\n소득 기준 53.3% 이하(가상)\n[지원대상]\n가상 청년\n"
        ctx = context(text=text)
        reviews = [review(value="소득 기준 53% 이하(가상)"), review(value="소득 기준 53.3% 이하(가상)"),
                   review(label="지원대상", value="가상 청년")]
        rows = official_requirements("POL_A", [ctx], reviews)
        self.assertEqual([row["label"] for row in rows], ["지원대상"])

    def test_tc10_document_or_chunk_mismatch_is_excluded(self):
        for name, item in {
            "다른 문서": review(document_id="DOC_OTHER"),
            "선택되지 않은 청크": review(chunk_id="CHK_NOT_SELECTED"),
            "청크 없음": review(chunk_id=None),
            "문서 없음": review(document_id=""),
        }.items():
            with self.subTest(name):
                self.assertIsNone(official_requirements("POL_A", [context()], [item]))

    def test_tc14_malformed_inputs_do_not_raise(self):
        contexts = [context(), {"policy_id": "POL_A"}, {"policy_id": "POL_A", "chunk_id": "X", "text": None}]
        reviews = [None, "문자열", 3, {}, {**review(), "label": " "}, {**review(), "value": 150},
                   {**review(), "checks": "verified"}, review()]
        rows = official_requirements("POL_A", contexts, reviews)
        self.assertEqual([row["value"] for row in rows], ["가구 기준 중위소득 150% 이하"])

    def test_review_value_must_match_whole_number_not_digit_suffix(self):
        # PR #133 리뷰: 원문 '150만원'에 검수 값 '50만원'이 일치로 인정되면 안 된다.
        ctx = context(text="[선정기준]\n월 소득 150만원 이하(가상), 재산 1,500만원 이하(가상)\n[문의처]\n가상\n")
        for value in ("50만원 이하(가상)", "500만원 이하(가상)", "0만원 이하(가상)"):
            with self.subTest(value=value):
                self.assertIsNone(official_requirements("POL_A", [ctx], [review(label="소득", value=value)]))
        for value in ("150만원 이하(가상)", "1,500만원 이하(가상)", "월 소득 150만원"):
            with self.subTest(value=value):
                rows = official_requirements("POL_A", [ctx], [review(label="소득", value=value)])
                self.assertEqual([row["value"] for row in rows], [value])

    def test_review_path_respects_source_age_error_mark(self):
        # PR #133 리뷰: 원문 연령 표기 오류 경고가 있으면 검수 기록 경로도 연령을 내보내지 않는다.
        flagged = {"warnings": [{"code": "SOURCE_AGE_ERROR_MARK", "affects": ["age"]}]}
        records = [review(label="연령 기준", value="만 19세 이상 34세 이하 가상 청년"),
                   review(label="나이", value="만 19세 이상 34세 이하 가상 청년"), review()]
        rows = official_requirements("POL_A", [context()], records, data_quality=flagged)
        self.assertEqual([row["label"] for row in rows], ["소득 기준"])
        unflagged = official_requirements("POL_A", [context()], records)
        self.assertEqual([row["label"] for row in unflagged], ["연령 기준", "나이", "소득 기준"])

    def test_malformed_check_and_warning_types_do_not_raise(self):
        # PR #133 리뷰: checks.value 등에 배열·객체가 오면 500이 아니라 그 기록만 건너뛴다.
        bad = [review(checks={"value": ["verified"], "unit": "verified", "year": "verified"}),
               review(checks={"value": "verified", "unit": {"a": 1}, "year": "verified"}),
               review(checks={"value": "verified", "unit": "verified", "year": ["x"]}),
               {**review(), "review_status": ["verified"]}, {**review(), "chunk_id": ["CHK_A1"]},
               {**review(), "label": ["소득"]}, {**review(), "policy_id": ["POL_A"]}]
        quality = [{"warnings": [{"code": ["SOURCE_AGE_ERROR_MARK"]}]}, {"warnings": "x"}, {"warnings": {"a": 1}},
                   {"warnings": [None, 3]}, "x", []]
        for item in bad:
            with self.subTest(review=repr(item)[:60]):
                self.assertIsNone(official_requirements("POL_A", [context()], [item]))
        for data_quality in quality:
            with self.subTest(data_quality=repr(data_quality)):
                rows = official_requirements("POL_A", [context()], [review()], data_quality=data_quality)
                self.assertEqual([row["label"] for row in rows], ["소득 기준"])
        for checks in (["MISMATCH"], "MISMATCH", {"DOC_A": ["MISMATCH"]}, {("DOC_A",): "MISMATCH"}):
            with self.subTest(document_checks=repr(checks)):
                self.assertIsNotNone(official_requirements("POL_A", [context()], [review()],
                                                           document_checks=checks))

    def test_mismatched_evidence_document_is_excluded(self):
        # PR #133 리뷰: evidence_document_check가 MISMATCH인 문서(그래프상 다른 정책 문서)는 쓰지 않는다.
        other = context(document_id="DOC_X", chunk_id="CHK_X1", source_id=2,
                        text="[지원대상]\n- 연령: 만 60세 이상(가상)\n[문의처]\n가상\n")
        records = [review(), review(label="연령", value="만 60세 이상(가상)", document_id="DOC_X", chunk_id="CHK_X1")]
        both = official_requirements("POL_A", [context(), other], records,
                                     document_checks={"DOC_A": "PASS", "DOC_X": "PASS"})
        self.assertEqual({row["document_id"] for row in both}, {"DOC_A", "DOC_X"})
        rows = official_requirements("POL_A", [context(), other], records,
                                     document_checks={"DOC_A": "PASS", "DOC_X": "MISMATCH"})
        self.assertEqual([(row["label"], row["document_id"]) for row in rows], [("소득 기준", "DOC_A")])
        # 원문 발췌 경로도 같다. NOT_TESTED(그래프 없음)는 빼지 않는다.
        self.assertIsNone(official_requirements("POL_A", [other], document_checks={"DOC_X": "MISMATCH"}))
        self.assertEqual([row["label"] for row in official_requirements(
            "POL_A", [other], document_checks={"DOC_X": "NOT_TESTED"})], ["연령"])

    def test_inputs_are_not_mutated(self):
        contexts, reviews = [context()], [review()]
        before = (repr(contexts), repr(reviews))
        official_requirements("POL_A", contexts, reviews)
        self.assertEqual((repr(contexts), repr(reviews)), before)


def src(text, pid="POL_A", document_id="DOC_A", chunk_id="CHK_A1", source_id=1):
    """원문 항목 발췌 테스트용 근거. 마지막 줄 잘림 규칙에 걸리지 않도록 뒤에 다른 블록을 둔다."""
    return context(pid=pid, document_id=document_id, chunk_id=chunk_id, source_id=source_id,
                   text=text + "\n[문의처]\n가상기관 (02-000-0000)\n")


def rows_of(text, **kwargs):
    return official_requirements(kwargs.pop("pid", "POL_A"), [src(text)], **kwargs)


class SourceRequirementTests(unittest.TestCase):
    """검수 기록 없이 자격 블록의 '항목: 값' 원문만 내보내는지 본다(sample_only 가상 원문)."""

    def test_age_requirement_is_extracted_verbatim_with_unit(self):
        rows = rows_of("[지원대상]\n- 연령: 만 19세 ~ 34세(가상)\n")
        self.assertEqual(rows, [{"label": "연령", "value": "만 19세 ~ 34세(가상)",
                                 "document_id": "DOC_A", "chunk_id": "CHK_A1"}])

    def test_income_requirement_keeps_ratio_unit_and_year_without_conversion(self):
        rows = rows_of("[선정기준]\nㅇ (소득) 2026년 기준 중위소득 150% 이하(가상)\n")
        self.assertEqual([(r["label"], r["value"]) for r in rows], [("소득", "2026년 기준 중위소득 150% 이하(가상)")])
        # 비율을 원화로 바꾸거나 연도를 덧붙이지 않는다.
        self.assertNotIn("원", rows[0]["value"])
        rows = rows_of("[선정기준]\n- 소득기준: 기준 중위소득 150% 이하(가상)\n")
        self.assertNotIn("년", rows[0]["value"])

    def test_multiple_items_keep_source_order(self):
        rows = rows_of("[지원대상 상세]\n가입연령 : 만 15세~39세(가상)\n가구소득 : 중위소득 50% 이하(가상)\n"
                       "- 거주지: 가상시 거주(가상)\n- 취업상태: 미취업자(가상)\n")
        self.assertEqual([r["label"] for r in rows], ["가입연령", "가구소득", "거주지", "취업상태"])

    def test_text_without_explicit_item_name_is_not_guessed(self):
        # 문장 안의 나이·소득은 정규식으로 추측하지 않는다(원문 블록 발췌 requirement_excerpts가 담당).
        self.assertIsNone(rows_of("[지원대상]\n만 19세 이상 34세 이하이면서 중위소득 150% 이하인 가상 청년\n"))

    def test_support_amount_period_and_method_blocks_are_never_requirements(self):
        for name, text in {
            "지원 금액": "[지원내용]\n- 소득: 월 100만원 지원(가상)\n- 지원금액: 월 100만원(가상)\n",
            "신청 기간": "[신청기간 및 신청방법]\n- 연령: 상시 신청(가상)\n- 신청기간: 상시\n",
            "구조화 요약": "[구조화 지원조건 요약]\n- 대상연령: 19세 ~ 34세\n",
            "수집기 템플릿": "[지원대상 및 자격요건]\n- 대상연령: 만 18~29세 (제한여부: N)\n- 소득요건: 무관\n",
        }.items():
            with self.subTest(name):
                self.assertIsNone(rows_of(text))

    def test_period_or_amount_items_inside_requirement_block_are_not_extracted(self):
        rows = rows_of("[지원대상]\n- 지원금액: 월 100만원(가상)\n- 신청기간: 상시\n- 특화요건: 가상 계층\n"
                       "- 소득: 신청기간 내 중위소득 100% 이하(가상)\n- 연령: 만 20세 이상(가상)\n")
        self.assertEqual([(r["label"], r["value"]) for r in rows], [("연령", "만 20세 이상(가상)")])

    def test_preference_and_exclusion_are_not_required_criteria(self):
        for text in (
            "[선정기준]\n- 소득: 중위소득 100% 이하 우대(가상)\n",
            "[선정기준]\n- 소득: 차상위 계층 우선 선정(가상)\n",
            "[선정기준]\n- 연령: 만 19세 이상, 가점 부여(가상)\n",
            "[지원대상]\n- 대상: 주택 소유자 제외(가상)\n",
            "[참여 제한대상]\n- 연령: 만 40세 이상(가상)\n",
        ):
            with self.subTest(text):
                self.assertIsNone(rows_of(text))

    def test_placeholders_and_collector_marks_stay_null_not_no_condition(self):
        for value in ("제한없음", "무관", "해당없음", "-", "미상 (원문 0~0세 표기 오류, 공고문 확인 필요)",
                      "만 0~0세", "무관 (0원 ~ 0원)", "만 18~29세 (제한여부: Y)", "공고문 참조"):
            with self.subTest(value):
                self.assertIsNone(rows_of(f"[지원대상]\n- 연령: {value}\n"))

    def test_line_referring_to_following_lines_is_not_a_criterion(self):
        self.assertIsNone(rows_of("[지원대상]\n- 지원대상: 아래 모든 조건을 충족하는 자\n1. 가상 조건\n"))
        self.assertIsNone(rows_of("[선정기준]\n- 소득인정액: 다음과 같이 계산합니다.\n"))

    def test_possibly_truncated_last_line_of_chunk_is_dropped(self):
        tail = context(text="[지원대상]\n- 연령: 만 19세 ~ 34세(가상)\n- 소득: 중위소득 150% 이")
        self.assertEqual([r["label"] for r in official_requirements("POL_A", [tail])], ["연령"])

    def test_requirements_are_not_mixed_between_policies(self):
        contexts = [src("[지원대상]\n- 연령: 만 19세 ~ 34세(가상)\n"),
                    src("[지원대상]\n- 연령: 만 65세 이상(가상)\n", pid="POL_B", document_id="DOC_B",
                        chunk_id="CHK_B1", source_id=2)]
        a, b = official_requirements("POL_A", contexts), official_requirements("POL_B", contexts)
        self.assertEqual([(r["value"], r["chunk_id"]) for r in a], [("만 19세 ~ 34세(가상)", "CHK_A1")])
        self.assertEqual([(r["value"], r["document_id"]) for r in b], [("만 65세 이상(가상)", "DOC_B")])
        self.assertIsNone(official_requirements("POL_C", contexts))

    def test_same_value_in_overlapping_chunks_keeps_first(self):
        text = "[지원대상]\n- 연령: 만 19세 ~ 34세(가상)\n"
        rows = official_requirements("POL_A", [src(text), src(text, chunk_id="CHK_A2", source_id=2)])
        self.assertEqual([r["chunk_id"] for r in rows], ["CHK_A1"])

    def test_source_age_error_flag_blocks_only_age(self):
        text = "[지원대상]\n- 연령: 만 19세 ~ 34세(가상)\n- 소득: 중위소득 150% 이하(가상)\n"
        flagged = {"warnings": [{"code": "SOURCE_AGE_ERROR_MARK", "affects": ["age"]}], "hold_auto_eligibility": True}
        self.assertEqual([r["label"] for r in rows_of(text, data_quality=flagged)], ["소득"])

    def test_auto_judgement_hold_does_not_erase_source_criteria(self):
        # 자동 판정 보류(그래프·Checker 쪽 경고)는 원문 기준이 없다는 뜻이 아니다.
        text = "[지원대상]\n- 연령: 만 19세 ~ 34세(가상)\n"
        hold = {"warnings": [{"code": "AGE_SOURCE_GRAPH_MISMATCH", "affects": ["age"],
                              "blocks_auto_eligibility": True}], "hold_auto_eligibility": True}
        self.assertEqual([r["label"] for r in rows_of(text, data_quality=hold)], ["연령"])

    def test_reviewed_value_takes_precedence_over_source_item_with_same_label(self):
        text = "[지원대상]\n- 연령: 만 19세 ~ 34세(가상)\n- 소득: 중위소득 150% 이하(가상)\n"
        record = review(label="소득", value="중위소득 150% 이하(가상)")
        rows = official_requirements("POL_A", [src(text)], [record])
        self.assertEqual([r["label"] for r in rows], ["소득", "연령"])

    def test_fee_or_copayment_tier_is_not_an_eligibility_criterion(self):
        # PR #133 리뷰(POL_BOK_061 형태): 120% 초과도 본인부담으로 이용 가능 → 신청 자격 소득기준으로 내보내지 않는다.
        text = ("[선정기준]\n(연령기준) 만 18세 미만(가상)\n"
                "(소득기준) 기준중위소득 120% 이하 무료, 소득 초과 가정은 이용료의 40% 본인부담(가상)\n"
                "(장애정도) 가상\n")
        self.assertEqual([r["label"] for r in rows_of(text)], ["연령기준"])
        for value in ("중위소득 100% 이하 이용료 감면(가상)", "자부담 10%(가상)",
                      "중위소득 50% 이하 무료(가상)", "본인 부담 없음(가상)"):
            with self.subTest(value=value):
                self.assertIsNone(rows_of(f"[지원대상]\n- 소득: {value}\n"))
        # 비용 문구가 아닌 경우: 사업 이름 '차상위본인부담', 지원액 차등 표기는 소득 상한(신청 자격)을 막지 않는다.
        for value in ("수급자 및 차상위 계층(차상위본인부담 포함, 가상)", "기준중위소득 120% 이하 (소득별 차등지원, 가상)"):
            with self.subTest(value=value):
                self.assertEqual([r["value"] for r in rows_of(f"[지원대상]\n- 소득: {value}\n")], [value])

    def test_unlabeled_next_line_is_kept_with_its_item(self):
        # PR #133 리뷰(POL_GOV_061 형태): 신청자격 바로 다음 줄의 단서(중복수혜 불가)를 빠뜨리지 않는다.
        text = ("[지원대상]\n< 지원대상 >\n - 신청자격 : 가상 이용권을 전액 사용한 자\n"
                "        ※ 단, 가상 장학금 중복수혜 불가\n - 지원규모 : 35명\n")
        rows = rows_of(text)
        self.assertEqual([(r["label"], r["value"]) for r in rows],
                         [("신청자격", "가상 이용권을 전액 사용한 자 ※ 단, 가상 장학금 중복수혜 불가")])
        self.assertIn(_flat(rows[0]["value"]), _flat(src(text)["text"]))

    def test_item_with_continuation_reaching_chunk_end_is_dropped_whole(self):
        # 단서 줄이 청크 끝에서 잘렸을 수 있으면 앞줄만 내보내지 않고 항목 전체를 뺀다.
        text = "[지원대상]\n- 연령: 만 19세 ~ 34세(가상)\n- 소득: 중위소득 150% 이하(가상)\n  ※ 단, 가구원 수에 따라"
        self.assertEqual([r["label"] for r in official_requirements("POL_A", [context(text=text)])], ["연령"])

    def test_continuation_with_other_meaning_drops_whole_item(self):
        # 이어진 줄이 제외·우대 문구나 뒤 목록을 가리키면 일부만 잘라 내보내지 않는다.
        for tail in ("다음에 해당하는 경우 지원대상에서 제외합니다.", "※ 저소득층 우대(가상)"):
            with self.subTest(tail=tail):
                text = f"[지원대상]\n- 연령: 만 19세 ~ 34세(가상)\n{tail}\n- 거주: 서울(가상)\n"
                self.assertEqual([r["label"] for r in rows_of(text)], ["거주"])

    def test_every_value_is_a_verbatim_substring_of_its_chunk(self):
        text = "[지원대상 상세]\n소득기준 : 기준중위소득  180% 이하(가상)\n(연령기준) 만 18세 미만(가상)\n"
        ctx = src(text)
        rows = official_requirements("POL_A", [ctx])
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertIn(row["value"], ctx["text"])


if __name__ == "__main__":
    unittest.main()
