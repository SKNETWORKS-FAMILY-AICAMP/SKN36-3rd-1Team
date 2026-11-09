# -*- coding: utf-8 -*-
"""원문 확인 결과(*_source_review, *_followup)와 자녀 개월 수 조건 판정 테스트."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.eligibility.checker import CHECK, NO, OK, check_eligibility  # noqa: E402


def _policy(extra=None, conditions=None):
    tree = {"group_id": "G", "logic": "AND", "conditions": conditions or [], "groups": []}
    return {"policy_id": "POL_T", "graph": {"policy": dict(extra or {}), "eligibility": {"condition_tree": [tree]}}, "data_quality": {}}


CHILD = {"type": "SPECIAL_TARGET", "name": "대상 자녀 연령: 24개월 미만", "operator": "LT", "value": "24", "unit": "개월"}


def _items(result, field):
    return [item for item in result["conditions"] if item["field"] == field]


class SourceReviewTests(unittest.TestCase):
    def test_not_specified_adds_no_question(self):
        result = check_eligibility({"age": 30}, _policy({"income_source_review": "not_specified", "income_source_note": "기준 없음",
                                                         "household_source_review": "not_specified"}))
        self.assertEqual(_items(result, "income"), [])
        self.assertEqual(_items(result, "housing"), [])

    def test_not_reviewed_adds_nothing(self):
        self.assertEqual(_items(check_eligibility({}, _policy({"income_source_review": "not_reviewed"})), "income"), [])

    def test_needs_review_ask_user(self):
        item = _items(check_eligibility({}, _policy({"income_source_review": "needs_review", "income_followup": "ask_user",
                                                     "income_source_note": "연소득 기준", "income_check_point": "연소득"})), "income")[0]
        self.assertEqual((item["status"], item["followup"]), (CHECK, "ask_user"))
        self.assertIn("연소득 기준", item["reason"])
        self.assertIn("추가로 확인할 내용: 연소득", item["reason"])

    def test_needs_review_check_notice_for_household(self):
        item = _items(check_eligibility({}, _policy({"household_source_review": "needs_review", "household_followup": "check_notice",
                                                     "household_check_point": "가구 기준"})), "housing")[0]
        self.assertEqual(item["followup"], "check_notice")
        self.assertIn("공식 공고에서 확인할 내용", item["reason"])

    def test_unknown_followup_does_not_ask_user(self):
        item = _items(check_eligibility({}, _policy({"income_source_review": "needs_review", "income_followup": "x"})), "income")[0]
        self.assertEqual(item["followup"], "check_notice")


class ChildAgeTests(unittest.TestCase):
    def _status(self, profile):
        return _items(check_eligibility(profile, _policy(conditions=[CHILD])), "children")[0]

    def test_missing_months_needs_check(self):
        item = self._status({"age": 35})
        self.assertEqual(item["status"], CHECK)
        self.assertIn("개월 수", item["reason"])

    def test_under_limit(self):
        self.assertEqual(self._status({"child_age_months": 23})["status"], OK)

    def test_at_limit_is_not_included(self):
        self.assertEqual(self._status({"child_age_months": 24})["status"], NO)

    def test_invalid_months_needs_check(self):
        for value in (True, -1, "23"):
            with self.subTest(value=value):
                self.assertEqual(self._status({"child_age_months": value})["status"], CHECK)

    def test_applicant_age_is_not_used(self):
        self.assertEqual(self._status({"age": 40, "child_age_months": 3})["status"], OK)


if __name__ == "__main__":
    unittest.main()
