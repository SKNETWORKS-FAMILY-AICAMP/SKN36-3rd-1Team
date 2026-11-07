"""실제 검색 반환 형태를 사용한 RAG 통합 테스트."""

import unittest
from unittest.mock import Mock

from pydantic import ValidationError

from backend.api.schemas import AskRequest, UserProfile
from backend.rag.pipeline import AskError, run_ask_pipeline


def policy(pid="P", rank=1, **changes):
    return {
        "policy_id": pid, "policy_name": "가상 정책", "best_rank": rank,
        "graph": {"policy": {"application_period": "상시신청"}, "eligibility": {}},
        "graph_status": "FOUND", "data_quality": {},
        "evidence": [{"rank": rank, "chunk_id": f"C{pid}", "document_id": f"D{pid}",
                      "text": "가상 공고 원문", "source_url": "https://example.org/notice"}],
        **changes,
    }


def checker(profile, item):
    return {
        "policy_id": item["policy_id"], "reception_status": "open",
        "conditions": [
            {"field": "age", "label": "나이", "reason": "가상 조건 충족", "status": "eligible"},
            {"field": "region", "label": "지역", "reason": "가상 조건 불일치", "status": "ineligible"},
        ],
    }


def answer(text="원문 [1]", ids=None, **changes):
    return {"status": "answered", "answer": text, "used_source_ids": [1] if ids is None else ids,
            "follow_up_questions": [], **changes}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.search = Mock(return_value={"policies": [policy()]})
        self.check = Mock(side_effect=checker)
        self.generate = Mock(return_value=answer())

    def ask(self, region="서울", **changes):
        request = AskRequest(question=" 질문 ", profile={"region": region, "age": 25, "income": 1.5},
                             **{"history": [], **changes})
        return run_ask_pipeline(request, search_fn=self.search, check_fn=self.check, generate_fn=self.generate)

    def test_normalization_and_unverified_reception(self):
        response = self.ask(awaiting_field="child_age", history=[{"role": "user", "text": str(i)} for i in range(12)])
        self.search.assert_called_once_with("질문", filters={"region": "서울특별시"}, k=10)
        self.assertEqual(self.check.call_args.args[0], {"age": 25, "region": "서울특별시", "income": 1.5})
        self.assertEqual(len(self.generate.call_args.args[3]), 10)
        self.assertEqual(response.policy_checks[0].reception_status, "unknown")
        self.assertEqual(response.citations[0].document_id, "DP")

    def test_missing_evidence_skips_other_dependencies(self):
        for policies in ([], [policy(evidence=[])]):
            with self.subTest(policies=policies):
                self.search.return_value = {"policies": policies}
                response = self.ask(region="")
                self.search.assert_called_with("질문", filters=None, k=10)
                self.assertEqual((response.status, response.citations, response.policy_checks), ("no_evidence", [], []))
        self.check.assert_not_called()
        self.generate.assert_not_called()

    def test_holds_affect_only_known_scope(self):
        cases = [
            (policy(graph=None, graph_status="MISSING_IN_GRAPH"), ["needs_check", "needs_check"]),
            (policy(data_quality={"hold_auto_eligibility": True, "affected_attributes": ["region"]}),
             ["eligible", "needs_check"]),
            (policy(data_quality={"hold_auto_eligibility": True}), ["needs_check", "needs_check"]),
            (policy(data_quality={"affected_attributes": ["all"]}), ["needs_check", "needs_check"]),
            (policy(graph={"eligibility": {"unknown_logic_groups": ["G"]}}), ["needs_check", "needs_check"]),
            (policy(graph={"eligibility": {"structure_issues": ["G"]}}), ["needs_check", "needs_check"]),
        ]
        for item, expected in cases:
            with self.subTest(item=item):
                self.search.return_value = {"policies": [item]}
                response = self.ask()
                self.assertEqual([c.status for c in response.policy_checks[0].conditions], expected)
                self.assertEqual(response.notice.kind, "warn")
                self.assertEqual([c["status"] for c in self.generate.call_args.args[2][0]["conditions"]], expected)

    def test_citations_use_body_and_only_selected_policies_are_checked(self):
        self.search.return_value = {"policies": [policy(str(i), i) for i in range(1, 7)]}
        self.generate.return_value = answer("신청 [3], 조건 [1], 반복 [3]", ids=[2])
        with self.assertLogs("backend.rag.pipeline", level="WARNING"):
            response = self.ask()
        self.assertEqual(response.answer, "신청 [2], 조건 [1], 반복 [2]")
        self.assertEqual([item.document_id for item in response.citations], ["D1", "D3"])
        self.assertEqual([check.policy_id for check in response.policy_checks], list("12345"))
        self.assertEqual(self.check.call_count, 5)

    def test_malformed_outputs_are_rejected(self):
        for raw in (answer("없는 근거 [9]"), answer("인용 누락"), {"answer": "불완전"},
                    answer(follow_up_questions=[{"kind": "ask_info", "text": "정보", "field": "child_age"}])):
            with self.subTest(raw=raw):
                self.generate.return_value = raw
                with self.assertRaisesRegex(AskError, "LLM_OUTPUT_INVALID"):
                    self.ask()
        self.check.side_effect = None
        self.check.return_value = {"policy_id": "P", "conditions": [], "reception_status": "unknown"}
        with self.assertRaisesRegex(AskError, "ELIGIBILITY_OUTPUT_INVALID"):
            self.ask()

    def test_unknown_region_and_follow_up(self):
        self.generate.return_value = answer("지역이 필요합니다.", ids=[], status="info",
                                           follow_up_questions=[{"kind": "ask_info", "text": "거주 지역은?", "field": "region"}])
        response = self.ask(region="미확인")
        self.assertIsNone(self.check.call_args.args[0]["region"])
        self.assertEqual(response.policy_checks[0].conditions[1].status, "needs_check")
        self.assertEqual(response.follow_up_questions[0].field, "region")

    def test_spaced_vague_period_is_unknown(self):
        # v1.4.0 실제 값은 "접수기관 별 상이"(띄어쓰기 포함)다.
        for period in ("접수기관 별 상이", "접수기관별 상이", " 상시 "):
            with self.subTest(period=period):
                self.search.return_value = {"policies": [policy(graph={"policy": {"application_period": period}})]}
                self.assertEqual(self.ask().policy_checks[0].reception_status, "unknown")
        self.search.return_value = {"policies": [policy(graph={"policy": {"application_period": "20260101 ~ 20261231"}})]}
        self.assertEqual(self.ask().policy_checks[0].reception_status, "open")

    def test_hold_reason_matches_cause(self):
        request = AskRequest(question="질문", profile={"region": "", "age": None, "income": 10}, history=[])
        self.check.side_effect = lambda profile, item: {
            "policy_id": item["policy_id"], "reception_status": "unknown",
            "conditions": [{"field": f, "label": f, "reason": "확정", "status": "eligible"}
                           for f in ("age", "income")]}
        response = run_ask_pipeline(request, search_fn=self.search, check_fn=self.check, generate_fn=self.generate)
        reasons = [c.reason for c in response.policy_checks[0].conditions]
        self.assertIn("입력한 정보가 없어", reasons[0])
        self.assertIn("소득 기준", reasons[1])

    def test_llm_timeout_and_unavailable(self):
        for failure, code in ((TimeoutError, "LLM_TIMEOUT"), (ConnectionError, "LLM_UNAVAILABLE")):
            with self.subTest(code=code):
                self.generate.side_effect = failure
                with self.assertRaisesRegex(AskError, code):
                    self.ask()


class RequestSchemaTests(unittest.TestCase):
    def test_numeric_input_rejects_coercion_and_nonfinite_values(self):
        base = {"age": 25, "region": "서울", "income": None}
        for updates in ({"age": True}, {"age": 25.0}, {"age": "25"}, {"age": 121},
                        {"income": True}, {"income": "10"}, {"income": float("nan")},
                        {"income": float("inf")}, {"income": -1}):
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                UserProfile.model_validate({**base, **updates})
        self.assertEqual(UserProfile.model_validate({**base, "income": 3.5}).income, 3.5)
