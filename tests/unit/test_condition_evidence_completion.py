"""sample_only policies: conservative applicant-age evidence, no real policy IDs."""
from copy import deepcopy
import unittest

from backend.rag.context_builder import (
    _has_age_evidence, _line_interval, complete_condition_evidence,
    rank_citable_contexts, select_contexts,
)


def sample(pid="SAMPLE_A", value="65~120", excluded=True):
    condition = {"type": "AGE", "operator": "BETWEEN", "value": value,
                 "name": "연령 조건", "relationship": {"evidence_document_id": "DOC_" + pid}}
    return {"policy_id": pid, "policy_name": "가상 정책", "best_rank": 1,
            "graph_status": "FOUND", "data_quality": {},
            "graph": {"policy": {"application_period": "상시신청"}, "eligibility": {"condition_tree": [
                {"logic": "NOT" if excluded else "AND", "conditions": [condition], "groups": []}]}},
            "evidence_document_check": {"DOC_" + pid: "PASS"},
            "evidence": [{"rank": i, "chunk_id": f"{pid}_C{i}", "document_id": "DOC_" + pid,
                          "text": "가상 신청 절차", "source_url": "https://example.org/sample"} for i in range(1, 7)]}


def setup_inputs(text="지원 제외 대상:\n- 만 65세 이상인 사람", **kwargs):
    policy = sample(**kwargs)
    policy["evidence"][-1]["text"] = text
    ranked = rank_citable_contexts([policy])
    return [policy], ranked, select_contexts(ranked)


class CompletionTests(unittest.TestCase):
    def complete(self, policies, ranked, selected, question="나이 기준을 충족하나요?", age=70, limit=1):
        return complete_condition_evidence(selected, ranked, policies, question, {"age": age}, limit=limit)

    def test_append_six_preserve_every_byte_and_inputs(self):
        policies, ranked, selected = setup_inputs()
        before = deepcopy((policies, ranked, selected))
        result = self.complete(policies, ranked, selected)
        self.assertEqual(result[:5], selected)
        self.assertEqual((result[5]["source_id"], result[5]["chunk_id"]), (6, "SAMPLE_A_C6"))
        self.assertEqual((policies, ranked, selected), before)

    def test_existing_same_document_no_addition(self):
        p, r, s = setup_inputs()
        s[0] = {**s[0], "text": r[-1]["text"]}
        self.assertEqual(self.complete(p, r, s), s)
        p, r, s = setup_inputs("지원 대상: 65세 이상", excluded=False)
        s[0] = {**s[0], "text": "[선정기준]\n신청하는 달을 기준으로 65세 이상을 대상으로 합니다.(신청월 말일까지 65세가 되는 자 포함)"}
        self.assertEqual(self.complete(p, r, s), s)

    def test_triggers_and_missing_age(self):
        p, r, s = setup_inputs()
        for question, age, expected in [("나이 기준은?", None, 6), ("신청할 수 있나요?", None, 6),
                                         ("신청 방법을 알려줘", 70, 6), ("신청 방법", None, 5),
                                         ("문의 전화는?", 70, 5), ("지원 금액은?", 70, 5),
                                         ("부모 나이 기준은?", 70, 5), ("자녀 신청 자격은?", 70, 5),
                                         ("나이와 무관한 신청 방법", 70, 5)]:
            with self.subTest(question=question, age=age):
                self.assertEqual(len(self.complete(p, r, s, question, age)), expected)

    def test_unsupported_conditions_and_malformed_tree(self):
        for value in ("0~120", "", "65", "90~65", "121~122"):
            p, r, s = setup_inputs(value=value)
            self.assertEqual(self.complete(p, r, s), s)
        for tree in (None, {}, [None], [{"logic": [], "conditions": []}], [{"logic": "AND", "conditions": None}],
                     [{"logic": "NOT", "conditions": []}], [{"logic": "AND", "not_loaded": True}]):
            p, r, s = setup_inputs()
            p[0]["graph"]["eligibility"]["condition_tree"] = tree
            self.assertEqual(self.complete(p, r, s), s)
        p, r, s = setup_inputs()
        group = p[0]["graph"]["eligibility"]["condition_tree"][0]
        group["groups"].append(group)
        self.assertEqual(self.complete(p, r, s), s)
        for kind in ("INCOME", "EMPLOYMENT", "EDUCATION", "SPECIAL_TARGET"):
            p, r, s = setup_inputs()
            p[0]["graph"]["eligibility"]["condition_tree"][0]["conditions"][0]["type"] = kind
            self.assertEqual(self.complete(p, r, s), s)

    def test_document_and_policy_boundaries(self):
        for change in ({"policy_id": "OTHER"}, {"document_id": "OTHER"}, {"url": "not-url"}, {"text": " "}):
            p, r, s = setup_inputs()
            r[-1].update(change)
            self.assertEqual(self.complete(p, r, s), s)
        for status in ("MISMATCH", None):
            p, r, s = setup_inputs()
            p[0]["evidence_document_check"]["DOC_SAMPLE_A"] = status
            self.assertEqual(self.complete(p, r, s), s)
        p, r, s = setup_inputs()
        p[0]["graph"]["eligibility"]["condition_tree"][0]["conditions"][0]["relationship"] = {}
        self.assertEqual(self.complete(p, r, s), s)

    def test_existing_other_document_does_not_mask_missing_evidence(self):
        p, r, s = setup_inputs()
        s[0] = {**s[0], "text": r[-1]["text"], "document_id": "OTHER"}
        self.assertEqual(len(self.complete(p, r, s)), 6)

    def test_limit_and_duplicates(self):
        p, r, s = setup_inputs()
        self.assertEqual(self.complete(p, r, s, limit=0), s)
        r[-1]["chunk_id"] = s[0]["chunk_id"]
        self.assertEqual(self.complete(p, r, s), s)

    def test_wrong_direction_subject_and_purpose_fail_closed(self):
        for text in ("지원 제외 대상: 65세 미만", "지원 제외 대상: 65세 이하", "지원 제외 대상: 65세 초과",
                     "지원 대상: 65세 이상", "지원 제외 대상: 자녀 65세 이상", "지원 제외 대상: 부모 65세 이상",
                     "지원 제외 대상:\n자녀 연령\n65세 이상", "지원 제외 대상:\n65세 이상 우선 지원",
                     "지원 제외 대상:\n65세 이상 제출 서류", "지원 제외 대상:\n구비서류\n65세 이상",
                     "지원 제외 대상: 165세 이상", "지원 제외 대상: 65세 이상이 아닌 사람",
                     "지원 제외 대상: 65세 이상 또는 20세 미만", "65세 이상", "지원 제외 대상: 65세 이상 70세 이상",
                     "지원 제외 대상: 18.65세 이상"):
            with self.subTest(text=text):
                p, r, s = setup_inputs(text)
                self.assertEqual(self.complete(p, r, s), s)

    def test_precise_integer_range_equivalences(self):
        for value, text in (("19~34", "지원 대상: 만 19세 이상 34세 이하"),
                            ("19~34", "지원 대상: 만 19~34세"),
                            ("19~34", "지원 대상: 18세 초과 35세 미만"),
                            ("0~17", "지원 대상: 만 18세 미만")):
            with self.subTest(text=text):
                p, r, s = setup_inputs(text, value=value, excluded=False)
                self.assertEqual(len(self.complete(p, r, s)), 6)
        p, r, s = setup_inputs("지원 대상: 만 19세 이상 34세 미만", value="19~34", excluded=False)
        self.assertEqual(self.complete(p, r, s), s)
        for text in ("지원 대상: 만 19~34세 미만", "지원 대상: 만 19~34세이지만 신청 대상이 아닙니다",
                     "지원 대상: 만 19~34세 신청 불가"):
            p, r, s = setup_inputs(text, value="19~34", excluded=False)
            self.assertEqual(self.complete(p, r, s), s)
        p, r, s = setup_inputs("지원 제외 대상: 65세 이상 70세")
        self.assertEqual(self.complete(p, r, s), s)

    def test_multiple_policies_global_limit_and_no_new_policy(self):
        p, r, s = setup_inputs()
        other, other_ranked, _ = setup_inputs(pid="SAMPLE_B")
        # Both policies were already selected; even limit=99 adds only one.
        selected = [s[0], {**other_ranked[0], "source_id": 2}, *s[2:]]
        result = self.complete(p + other, r + other_ranked, selected, limit=99)
        self.assertEqual(len(result), 6)
        p[0]["evidence_document_check"] = {}
        self.assertEqual(self.complete(p + other, r + other_ranked, s), s)

    def test_review_converted_age_bounds(self):
        for text, expected in (("120세 초과", None), ("0세 미만", None),
                               ("119세 초과", (120, None)), ("120세 이상", (120, None)),
                               ("0세 초과", (1, None)), ("1세 미만", (0, 0)),
                               ("0세 이상", (0, None)), ("120세 이하", (0, 120)),
                               ("121세 이상", None), ("119세 초과 120세 이하", (120, 120))):
            with self.subTest(text=text):
                self.assertEqual(_line_interval(text), expected)

    def test_review_section_and_subject_leakage(self):
        for body in ("참고사항\n65세 이상", "기타 안내\n65세 이상",
                     "[별도 안내]\n65세 이상", "유의사항:\n65세 이상",
                     "65세 이상 전화 상담 대상", "동반인 65세 이상",
                     "가족 65세 이상", "65세 이상 안내 서비스 대상"):
            with self.subTest(body=body):
                text = "지원 제외 대상:\n" + body
                self.assertFalse(_has_age_evidence(text, (65, None), True))
                p, r, s = setup_inputs(text)
                self.assertEqual(self.complete(p, r, s), s)
        self.assertTrue(_has_age_evidence("지원 제외 대상:\n- 공무원\n- 65세 이상인 사람", (65, None), True))

    def test_review_mixed_ranges_and_ambiguous_directions(self):
        for text in ("19~34세, 별도 안내는 60세", "19세~34세, 동반인 60세",
                     "19~34세 또는 60~70세", "19~34세, 60세 이상"):
            with self.subTest(text=text):
                self.assertIsNone(_line_interval(text))
        for text in ("지원 대상: 19~34세 단 신청 자격이 없습니다",
                     "지원 대상: 19~34세인지 확인 필요", "지원 대상: 19~34세일 수 있습니다"):
            with self.subTest(text=text):
                self.assertFalse(_has_age_evidence(text, (19, 34), False))

    def test_review_closed_policy_priority(self):
        closed = sample(pid="SAMPLE_CLOSED")
        opened = sample(pid="SAMPLE_OPEN")
        for policy in (closed, opened):
            policy["evidence"] = [policy["evidence"][0], policy["evidence"][-1]]
            policy["evidence"][0]["rank"] = 1 if policy is closed else 2
            policy["evidence"][-1]["text"] = "지원 제외 대상: 65세 이상"
        ranked = rank_citable_contexts([closed, opened])
        # Helper boundary: both policies have missing candidates and the caller's
        # selected policy order must win over original search rank.
        for order, expected in (((opened, closed), "SAMPLE_OPEN"), ((closed, opened), "SAMPLE_CLOSED")):
            with self.subTest(first=expected):
                selected = [{**next(c for c in ranked if c["policy_id"] == p["policy_id"]), "source_id": i}
                            for i, p in enumerate(order, 1)]
                result = self.complete([closed, opened], ranked, selected)
                self.assertEqual(result[:2], selected)
                self.assertEqual(len(result), 3)
                self.assertEqual(result[-1]["policy_id"], expected)
                self.assertEqual(result[-1]["chunk_id"], expected + "_C6")
        ranked = rank_citable_contexts([closed])
        selected = select_contexts(ranked, max_contexts=1, deprioritized_policy_ids={"SAMPLE_CLOSED"})
        result = self.complete([closed], ranked, selected)
        self.assertEqual(result[-1]["chunk_id"], "SAMPLE_CLOSED_C6")


if __name__ == "__main__":
    unittest.main()
