"""실제 검색 반환 형태를 사용한 RAG 통합 테스트."""

from contextlib import nullcontext
import unittest
import os
import subprocess
import sys
from unittest.mock import Mock

from pydantic import ValidationError

from backend.api.schemas import AskRequest, UserProfile
from backend.rag.pipeline import AskError, _status_correction, run_ask_pipeline
from backend.llm.generator import LLMOutputError, LLMTimeoutError, LLMUnavailableError


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
    def test_pipeline_import_does_not_load_generator_or_dataset_env(self):
        env = os.environ.copy()
        env.pop("LIFEPOLICY_DATASET_DIR", None)
        code = (
            "import os,sys; import backend.rag.pipeline; "
            "assert 'backend.llm.generator' not in sys.modules; "
            "assert 'LIFEPOLICY_DATASET_DIR' not in os.environ"
        )
        result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_api_import_loads_dataset_env_before_search(self):
        env = os.environ.copy()
        env.pop("LIFEPOLICY_DATASET_DIR", None)
        code = (
            "import os,sys; from dotenv import dotenv_values; "
            "expected=dotenv_values('.env').get('LIFEPOLICY_DATASET_DIR'); "
            "import backend.api.main; "
            "assert 'backend.llm.generator' not in sys.modules; "
            "assert os.environ.get('LIFEPOLICY_DATASET_DIR') == (expected or None); "
            "from database import handoff_data as h; "
            "assert h.dataset_version()"
        )
        result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_api_import_reads_env_file_before_dataset_selection_without_real_env(self):
        # 실제 .env 없이도 검증한다: 임시 가상 .env에 데이터셋 경로만 적고, 검색 모듈 import 전에 읽히는지 확인.
        env = os.environ.copy()
        env.pop("LIFEPOLICY_DATASET_DIR", None)
        code = (
            "import os,sys,tempfile,pathlib; "
            "from database import embedding as e; "
            "dataset=(e.PROJECT_ROOT/'data'/'releases'/'v1.4.0-candidate').resolve(); "
            "tmp=pathlib.Path(tempfile.mkdtemp())/'.env'; "
            "tmp.write_text('LIFEPOLICY_DATASET_DIR='+str(dataset)+chr(10), encoding='utf-8'); "
            "e.DEFAULT_ENV_PATH=tmp; "
            "import backend.api.main; "
            "assert 'backend.llm.generator' not in sys.modules; "
            "assert os.environ['LIFEPOLICY_DATASET_DIR']==str(dataset); "
            "from database import handoff_data as h; "
            "assert h.HANDOFF_DIR==dataset, (h.HANDOFF_DIR, dataset)"
        )
        result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def setUp(self):
        self.search = Mock(return_value={"policies": [policy()]})
        self.check = Mock(side_effect=checker)
        self.generate = Mock(return_value=answer())

    def ask(self, region="서울", **changes):
        request = AskRequest(question=" 질문 ", profile={"region": region, "age": 25, "income": 1.5},
                             **{"history": [], **changes})
        return run_ask_pipeline(request, search_fn=self.search, check_fn=self.check, generate_fn=self.generate)

    def test_normalization_and_checker_reception_kept(self):
        response = self.ask(awaiting_field="child_age", history=[{"role": "user", "text": str(i)} for i in range(12)])
        self.search.assert_called_once_with("질문", filters={"region": "서울특별시"}, k=10)
        self.assertEqual(self.check.call_args.args[0], {"age": 25, "region": "서울특별시", "income": 1.5})
        self.assertEqual(len(self.generate.call_args.args[3]), 10)
        # 신청 상태는 Checker 단일 책임이다. application_period 문구("상시신청")로 덮어쓰지 않는다.
        self.assertEqual(response.policy_checks[0].reception_status, "open")
        self.assertEqual(response.citations[0].document_id, "DP")

    def test_normalized_profile_reaches_generator(self):
        cases = (
            ({"age": 25, "region": "서울", "income": 1.5}, {"age": 25, "region": "서울특별시", "income": 1.5}),
            ({"age": 25, "region": "서울", "income": 0}, {"age": 25, "region": "서울특별시", "income": 0}),
            ({"age": None, "region": "", "income": None}, {"age": None, "region": None, "income": None}),
            ({"age": 30, "region": "미확인시", "income": None}, {"age": 30, "region": None, "income": None}),
        )
        for supplied, expected in cases:
            with self.subTest(supplied=supplied):
                request = AskRequest(question="질문", profile=supplied, history=[])
                run_ask_pipeline(request, search_fn=self.search, check_fn=self.check, generate_fn=self.generate)
                self.assertEqual(self.generate.call_args.kwargs, {"profile": expected})
                self.assertEqual(self.check.call_args.args[0], expected)
                # Generator가 받은 profile을 바꿔도 Pipeline 내부 값에는 영향이 없어야 한다.
                self.assertIsNot(self.generate.call_args.kwargs["profile"], self.check.call_args.args[0])

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
        # 신청 상태 정렬을 위해 인용 가능한 후보는 모두 한 번씩 판정하고, 결과 카드는 채택된 정책만 남긴다.
        self.assertEqual(sorted(call.args[1]["policy_id"] for call in self.check.call_args_list), list("123456"))

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

    def test_does_not_ask_for_supplied_profile_value(self):
        self.generate.return_value = answer(
            "공식 조건 확인이 필요합니다 [1]",
            follow_up_questions=[
                {"kind": "ask_info", "text": "지역은?", "field": "region"},
                {"kind": "question", "text": "공고 원문은?"},
            ],
        )
        response = self.ask(region="서울")
        self.assertEqual([item.kind for item in response.follow_up_questions], ["question"])

    def with_reception(self, statuses):
        """정책 ID별 신청 상태를 돌려주는 Checker 대역. 날짜 계산은 Checker 책임이므로 결과만 흉내 낸다."""
        def check(profile, item):
            return {**checker(profile, item), "reception_status": statuses.get(item["policy_id"], "unknown")}
        self.check.side_effect = check

    def named_policies(self):
        # 검색 1위 A만 마감, 나머지 5개는 접수 중. 근거 자리는 5개라 이름 지정이 없으면 A가 밀려난다.
        names = {"A": "서울특별시 청년수당", "B": "청년 월세 지원", "C": "청년 일자리 도약", "D": "청년 마음건강",
                 "E": "청년 문화패스", "F": "청년 주거 상담"}
        self.search.return_value = {"policies": [policy(pid, rank, policy_name=names[pid])
                                                 for rank, pid in enumerate("ABCDEF", 1)]}
        self.with_reception({"A": "closed", **{pid: "open" for pid in "BCDEF"}})

    def ask_question(self, question):
        request = AskRequest(question=question, profile={"age": 25, "region": "", "income": None}, history=[])
        return run_ask_pipeline(request, search_fn=self.search, check_fn=self.check, generate_fn=self.generate)

    def test_closed_policy_asked_by_name_keeps_an_evidence_slot(self):
        self.named_policies()
        for question in ("서울특별시 청년수당 신청 자격 알려주세요", "서울특별시청년수당은 지금 받을 수 있나요?"):
            with self.subTest(question=question):
                response = self.ask_question(question)
                contexts = self.generate.call_args.args[1]
                # 이름을 지정한 마감 정책은 후순위에서 빠져 원래 검색 순서(1위) 자리를 유지하고, 마감 상태는 그대로 응답한다.
                self.assertEqual([c["policy_id"] for c in contexts], list("ABCDE"))
                self.assertEqual([c["source_id"] for c in contexts], [1, 2, 3, 4, 5])
                self.assertEqual((response.policy_checks[0].policy_id, response.policy_checks[0].reception_status),
                                 ("A", "closed"))
                self.assertEqual(response.citations[0].document_id, "DA")

    def test_unnamed_partial_or_short_name_keeps_closed_policy_deprioritized(self):
        self.named_policies()
        for question in ("청년 지원 정책 알려주세요", "청년수당 신청 자격", "서울 청년수당"):
            with self.subTest(question=question):
                self.ask_question(question)
                self.assertEqual([c["policy_id"] for c in self.generate.call_args.args[1]], list("BCDEF"))
        # 너무 짧은 정책명(공백·문장부호 제외 4자 미만)은 이름 지정으로 보지 않는다.
        self.search.return_value = {"policies": [policy("A", 1, policy_name="청년"),
                                                 *[policy(pid, rank) for rank, pid in enumerate("BCDEF", 2)]]}
        self.ask_question("청년 정책 알려주세요")
        self.assertNotIn("A", [c["policy_id"] for c in self.generate.call_args.args[1]])

    def conflict_case(self, reception, condition, text, policies=None, changed=True):
        # 소득 확정 판정은 Pipeline이 항상 needs_check로 낮추므로, 확정 상태가 유지되는 나이 조건으로 만든다.
        self.search.return_value = {"policies": policies or [policy()]}
        self.check.side_effect = lambda profile, item: {
            "policy_id": item["policy_id"], "reception_status": reception.get(item["policy_id"], "open")
            if isinstance(reception, dict) else reception,
            "conditions": [{"field": "age", "label": "나이", "reason": "가상 연령 판정", "status": condition}]}
        self.generate.return_value = answer(text)
        # 본문을 바꿀 때는 내용 없이 경고 로그만 남긴다.
        with self.assertLogs("backend.rag.pipeline", level="WARNING") if changed else nullcontext():
            return self.ask_question("신청할 수 있나요?")

    def test_answer_conflicting_with_checker_is_replaced_even_with_valid_citation(self):
        # 팀장 리뷰 사례: 유효한 [1]이 붙어 있어도 판정과 충돌하는 단정은 사용자에게 전달하지 않는다.
        cases = (
            ("closed", "eligible", "이 정책은 지금 신청할 수 있어요 [1].", "접수가 마감됐어요."),
            ("open", "needs_check", "모든 신청 자격을 충족했어요 [1].", "신청 자격은 공식 공고에서 추가 확인이 필요해요."),
            ("open", "ineligible", "신청 자격이 있어요 [1].", "맞지 않는 조건이 있어요"),
            ("before", "eligible", "현재 접수 중이에요 [1].", "아직 접수가 시작되지 않았어요."),
            ("unknown", "eligible", "지금 바로 신청 가능해요 [1].", "현재 접수 여부는 공식 공고에서 확인이 필요해요."),
        )
        for reception, condition, text, expected in cases:
            with self.subTest(text=text):
                response = self.conflict_case(reception, condition, text)
                self.assertEqual(response.status, "answered")
                self.assertIn(expected, response.answer)
                self.assertNotIn(text.split(" [")[0], response.answer)
                self.assertTrue(response.answer.endswith("[1]"))
                self.assertEqual([c.document_id for c in response.citations], ["DP"])
                self.assertEqual(response.notice.kind, "warn")

    def test_consistent_or_hedged_answers_are_kept(self):
        cases = (
            ("open", "eligible", "이 정책은 지금 신청할 수 있어요 [1]."),
            ("closed", "needs_check", "현재 접수 중인지 여부는 원문만으로 단정할 수 없습니다 [1]."),
            ("closed", "needs_check", "지금 신청 가능 여부는 공고에서 추가 확인이 필요해요 [1]."),
            ("closed", "needs_check", "연령 요건은 충족합니다 [1]."),
        )
        for reception, condition, text in cases:
            with self.subTest(text=text):
                self.search.return_value = {"policies": [policy()]}
                self.check.side_effect = lambda profile, item, r=reception, c=condition: {
                    "policy_id": item["policy_id"], "reception_status": r,
                    "conditions": [{"field": "age", "label": "나이", "reason": "가상 연령 판정", "status": c}]}
                self.generate.return_value = answer(text)
                self.assertEqual(self.ask_question("신청할 수 있나요?").answer, text)

    def test_only_sentence_citing_conflicting_policy_is_replaced(self):
        policies = [policy("A", 1, policy_name="접수 중 정책"), policy("B", 2, policy_name="마감 정책")]
        text = "접수 중 정책은 지금 신청할 수 있어요 [1]. 마감 정책도 지금 신청할 수 있어요 [2]. 자세한 서류는 공고를 보세요 [1]."
        response = self.conflict_case({"A": "open", "B": "closed"}, "eligible", text, policies)
        self.assertEqual(response.answer,
                         "접수 중 정책은 지금 신청할 수 있어요 [1]. 마감 정책: 접수가 마감됐어요. [2] 자세한 서류는 공고를 보세요 [1].")
        self.assertEqual([c.document_id for c in response.citations], ["DA", "DB"])

    def test_uncited_claim_is_replaced_only_when_every_policy_conflicts(self):
        text = "관련 원문이 있습니다 [1]. 지금 신청할 수 있어요."
        response = self.conflict_case("closed", "eligible", text)
        self.assertEqual(response.answer, "관련 원문이 있습니다 [1]. 접수가 마감됐어요.")
        policies = [policy("A", 1, policy_name="접수 중 정책"), policy("B", 2, policy_name="마감 정책")]
        self.assertEqual(self.conflict_case({"A": "open", "B": "closed"}, "eligible", text, policies,
                                            changed=False).answer, text)

    def test_hedged_clause_does_not_hide_conflicting_claim_in_same_sentence(self):
        # 서류에 대한 유보 안내는 남기고, 같은 문장의 '지금 신청할 수 있어요' 단정만 마감 안내로 바꾼다.
        response = self.conflict_case("closed", "eligible", "신청 서류는 추가 확인이 필요하지만, 지금 신청할 수 있어요 [1].")
        self.assertEqual(response.answer, "신청 서류는 추가 확인이 필요하지만, 접수가 마감됐어요. [1]")
        self.assertEqual([c.document_id for c in response.citations], ["DP"])
        # 유보 절만 있는 문장은 그대로 둔다.
        kept = "신청 서류는 추가 확인이 필요하고, 신청 가능 여부도 공고에서 확인해 주세요 [1]."
        self.assertEqual(self.conflict_case("closed", "eligible", kept, changed=False).answer, kept)

    def test_multi_policy_claim_keeps_each_policy_status_and_its_own_source(self):
        # A(마감)는 Context 후순위라 [2], B(접수 중)는 [1]. 각 정책 문장에는 그 정책의 출처 번호만 붙인다.
        policies = [policy("A", 1, policy_name="정책 A"), policy("B", 2, policy_name="정책 B")]
        response = self.conflict_case({"A": "closed", "B": "open"}, "eligible",
                                      "A와 B 모두 지금 신청할 수 있어요 [2][1].", policies)
        self.assertEqual(response.answer, "정책 A: 접수가 마감됐어요. [2] 정책 B: 접수 중이에요. [1]")
        self.assertEqual([c.document_id for c in response.citations], ["DB", "DA"])
        self.assertEqual({p.policy_id: p.reception_status for p in response.policy_checks}, {"A": "closed", "B": "open"})

    def test_full_eligibility_claim_is_never_confirmed_from_individual_conditions(self):
        # 확인된 개별 조건이 모두 eligible이어도 전체 신청 자격 보장 표현은 전달하지 않는다.
        response = self.conflict_case("open", "eligible", "모든 신청 자격을 충족했어요 [1].")
        self.assertEqual(response.answer,
                         "가상 정책: 확인된 조건은 충족하지만, 전체 신청 자격은 공식 공고에서 확인이 필요해요. [1]")
        self.assertEqual([c.status for c in response.policy_checks[0].conditions], ["eligible"])
        # 조건 목록이 비어 있어도 전체 자격을 확정하지 않는다.
        self.assertEqual(_status_correction("eligible", {"conditions": [], "reception_status": "open"}),
                         "신청 자격은 공식 공고에서 추가 확인이 필요해요.")

    def two_policy_case(self, text, names=("정책 가", "정책 나"), changed=True):
        # A는 마감이라 Context 후순위([2]), B는 접수 중([1]).
        policies = [policy("A", 1, policy_name=names[0]), policy("B", 2, policy_name=names[1])]
        return self.conflict_case({"A": "closed", "B": "open"}, "eligible", text, policies, changed=changed)

    def test_named_closed_policy_cannot_borrow_open_policy_citation(self):
        names = ("청년월세 특별지원", "청년 주거 상담")
        response = self.two_policy_case("청년월세 특별지원은 지금 신청할 수 있어요 [1].", names)
        self.assertIn("청년월세 특별지원", response.answer)
        self.assertIn("접수가 마감됐어요", response.answer)
        self.assertNotIn("신청할 수 있어요", response.answer)
        self.assertNotIn("[1]", response.answer)
        self.assertEqual(response.citations, [])
        self.assertEqual(response.status, "info")
        self.assertEqual({p.policy_id: p.reception_status for p in response.policy_checks},
                         {"A": "closed", "B": "open"})

    def test_wrong_policy_citation_removed_without_losing_valid_other_sentence(self):
        names = ("청년월세 특별지원", "청년 주거 상담")
        response = self.two_policy_case(
            "청년월세 특별지원은 지금 신청할 수 있어요 [1]. 청년 주거 상담은 제출 서류가 필요해요 [1].", names)
        self.assertIn("청년월세 특별지원: 접수가 마감됐어요", response.answer)
        self.assertIn("청년 주거 상담은 제출 서류가 필요해요 [1]", response.answer)
        self.assertEqual([c.document_id for c in response.citations], ["DB"])
        self.assertEqual(response.status, "answered")

    def test_wrong_citation_in_first_clause_keeps_correct_second_clause(self):
        names = ("청년월세 특별지원", "청년 주거 상담")
        response = self.two_policy_case(
            "청년월세 특별지원은 지금 신청할 수 있어요 [1], 청년 주거 상담은 지금 신청할 수 있어요 [1].", names)
        self.assertIn("청년월세 특별지원: 접수가 마감됐어요", response.answer)
        self.assertIn("청년 주거 상담은 지금 신청할 수 있어요 [1]", response.answer)
        self.assertEqual([c.document_id for c in response.citations], ["DB"])

    def test_remaining_second_source_is_renumbered_after_wrong_first_source_removed(self):
        names = ("청년월세 특별지원", "청년 주거 상담")
        response = self.two_policy_case(
            "청년월세 특별지원은 지금 신청할 수 있어요 [1]. 청년월세 특별지원 공고 원문은 [2]에서 확인해 주세요.",
            names)
        self.assertIn("접수가 마감됐어요", response.answer)
        self.assertNotIn("[2]", response.answer)
        self.assertIn("공고 원문은 [1]", response.answer)
        self.assertEqual([c.document_id for c in response.citations], ["DA"])

    def test_correct_policy_citations_remain_attached(self):
        names = ("청년월세 특별지원", "청년 주거 상담")
        corrected = self.two_policy_case("청년월세 특별지원은 지금 신청할 수 있어요 [2].", names)
        self.assertIn("접수가 마감됐어요", corrected.answer)
        self.assertEqual([c.document_id for c in corrected.citations], ["DA"])
        kept = self.two_policy_case("청년 주거 상담은 지금 신청할 수 있어요 [1].", names, changed=False)
        self.assertEqual(kept.answer, "청년 주거 상담은 지금 신청할 수 있어요 [1].")
        self.assertEqual([c.document_id for c in kept.citations], ["DB"])

    def test_uncertain_or_short_policy_name_does_not_choose_open_checker(self):
        for names, text in (
            (("청년월세 특별지원", "청년 주거 상담"), "지금 신청할 수 있어요 [1]."),
            (("청년월세 특별지원", "청년월세 특별지원 연장"), "청년월세는 지금 신청할 수 있어요 [1]."),
            (("정책 A", "정책 B"), "A는 지금 신청할 수 있어요 [1]."),
        ):
            with self.subTest(names=names, text=text):
                response = self.two_policy_case(text, names)
                self.assertNotIn("신청할 수 있어요", response.answer)
                self.assertEqual(response.citations, [])
                self.assertEqual(response.status, "info")

    def test_uncited_claim_about_other_policy_does_not_borrow_sentence_citation(self):
        # 팀장 리뷰 사례: 문장 끝 [1]은 B 출처다. 어느 정책인지 확실하지 않은 A의 신청 가능 안내는 빼고 B 안내·출처는 남긴다.
        response = self.two_policy_case("A는 지금 신청할 수 있어요, B는 제출 서류가 필요해요 [1].")
        self.assertEqual(response.answer, "B는 제출 서류가 필요해요 [1].")
        self.assertEqual([c.document_id for c in response.citations], ["DB"])
        # 순서가 바뀌어도 같다.
        response = self.two_policy_case("B는 제출 서류가 필요해요, A는 지금 신청할 수 있어요 [1].")
        self.assertEqual(response.answer, "B는 제출 서류가 필요해요 [1].")
        # 절마다 자기 출처를 달면 끝 출처는 마지막 절 자신의 출처다(정상 다정책 답변은 그대로).
        kept = "A는 접수가 마감됐어요 [2], B는 지금 신청할 수 있어요 [1]."
        self.assertEqual(self.two_policy_case(kept, changed=False).answer, kept)

    def test_uncited_claim_with_identifiable_policy_name_uses_that_policy(self):
        names = ("청년월세 특별지원", "청년 주거 상담")
        response = self.two_policy_case(
            "청년월세 특별지원은 지금 신청할 수 있어요, 청년 주거 상담은 제출 서류가 필요해요 [1].", names)
        self.assertEqual(response.answer, "청년월세 특별지원: 접수가 마감됐어요, 청년 주거 상담은 제출 서류가 필요해요 [1].")
        self.assertEqual([c.document_id for c in response.citations], ["DB"])

    def test_single_policy_sentence_with_one_trailing_citation_is_kept_or_corrected_by_that_source(self):
        # 한 정책만 설명하고 문장 끝에 출처를 한 번 붙인 정상 답변: 판정과 맞으면 그대로, 충돌하면 그 출처의 판정으로 교정.
        kept = "B는 지금 신청할 수 있고, 제출 서류도 필요해요 [1]."
        self.assertEqual(self.two_policy_case(kept, changed=False).answer, kept)
        self.assertEqual(self.two_policy_case("지금 신청할 수 있어요, 제출 서류도 필요해요 [1].", changed=False).answer,
                         "지금 신청할 수 있어요, 제출 서류도 필요해요 [1].")
        corrected = self.two_policy_case("A는 지금 신청할 수 있고, 제출 서류도 필요해요 [2].")
        self.assertEqual(corrected.answer, "접수가 마감됐어요, 제출 서류도 필요해요 [1].")
        self.assertEqual([c.document_id for c in corrected.citations], ["DA"])

    def test_huge_or_unknown_citation_in_claim_sentence_is_invalid_llm_output(self):
        for text in (f"지금 신청할 수 있어요 [{'9' * 5000}].", "지금 신청할 수 있어요 [7]."):
            with self.subTest(length=len(text)):
                self.search.return_value = {"policies": [policy()]}
                self.with_reception({"P": "closed"})
                self.generate.return_value = answer(text)
                with self.assertRaisesRegex(AskError, "LLM_OUTPUT_INVALID"):
                    self.ask_question("신청할 수 있나요?")

    def test_reception_status_comes_from_checker(self):
        # application_period 문구가 무엇이든 Checker의 판정을 그대로 쓴다(시작일만 있는 정책도 Checker가 판정).
        for period in ("상시신청", "접수기관 별 상이", " 상시 ", "20260101 ~ 20261231", None):
            for status in ("before", "open", "closed", "unknown"):
                with self.subTest(period=period, status=status):
                    graph = {"policy": {"application_period": period}, "eligibility": {}}
                    self.search.return_value = {"policies": [policy(graph=graph)]}
                    self.with_reception({"P": status})
                    self.assertEqual(self.ask().policy_checks[0].reception_status, status)

    # 팀장 결정(2026-11-10) 신청 기간 표. 날짜 판정은 Checker 책임이므로 Checker 대역이 기대 상태를 돌려주고,
    # Pipeline이 그 상태를 그대로 유지·정렬하는지만 검증한다. 날짜 값은 가상(sample_only)이다.
    RECEPTION_CASES = (
        ("F", {"application_start": "2099-01-01", "application_is_ongoing": True}, "before"),
        ("K", {"application_start": "2020-01-01", "application_end": "2020-12-31"}, "closed"),
        ("G", {"application_start": "2020-01-01", "application_is_ongoing": True}, "open"),
        ("H", {"application_is_ongoing": True}, "open"),
        ("I", {"application_start": "2020-01-01"}, "unknown"),
        ("J", {}, "unknown"),
        ("L", {"application_start": "2020-01-01", "application_end": "2099-12-31"}, "open"),
    )

    def test_final_reception_contract_cases_are_kept_and_only_closed_moves(self):
        policies = [policy(pid, rank, graph={"policy": fields, "eligibility": {}})
                    for rank, (pid, fields, _) in enumerate(self.RECEPTION_CASES, 1)]
        self.search.return_value = {"policies": policies}
        expected = {pid: status for pid, _, status in self.RECEPTION_CASES}
        self.with_reception(expected)
        request = AskRequest(question="질문", profile={"age": 25, "region": "", "income": None}, history=[])
        response = run_ask_pipeline(request, search_fn=self.search, check_fn=self.check, generate_fn=self.generate)
        # 7개 후보 중 Context 5개: 검색 2위인 마감(K)만 뒤로 밀려 빠지고, 나머지는 검색 순서를 유지한다.
        self.assertEqual([c["policy_id"] for c in self.generate.call_args.args[1]], list("FGHIJ"))
        self.assertEqual([(c.policy_id, c.reception_status) for c in response.policy_checks],
                         [(pid, expected[pid]) for pid in "FGHIJ"])
        # Checker에는 graph.policy 필드가 바뀌지 않은 채 전달된다.
        sent = {call.args[1]["policy_id"]: call.args[1]["graph"]["policy"] for call in self.check.call_args_list}
        self.assertEqual(sent, {pid: fields for pid, fields, _ in self.RECEPTION_CASES})

    def test_pipeline_does_not_interpret_dates_or_ongoing_flag(self):
        # 필드가 가리키는 상태와 반대로 Checker가 판정해도 Pipeline은 Checker 결과만 따른다.
        for fields, status in (
            ({"application_is_ongoing": True}, "unknown"),
            ({"application_start": "2020-01-01", "application_end": "2020-12-31"}, "open"),
            ({"application_start": "2099-01-01"}, "closed"),
            ({"application_period": "상시신청"}, "unknown"),
        ):
            with self.subTest(fields=fields, status=status):
                self.search.return_value = {"policies": [policy(graph={"policy": fields, "eligibility": {}})]}
                self.with_reception({"P": status})
                self.assertEqual(self.ask().policy_checks[0].reception_status, status)

    def test_missing_graph_reception_is_unknown_and_never_deprioritized(self):
        self.search.return_value = {"policies": [policy("A", 1, graph=None, graph_status="MISSING_IN_GRAPH"),
                                                 policy("B", 2)]}
        self.with_reception({"A": "closed", "B": "open"})
        response = self.ask()
        self.assertEqual([(c.policy_id, c.reception_status) for c in response.policy_checks],
                         [("A", "unknown"), ("B", "open")])

    def test_closed_policies_move_after_others_before_context_selection(self):
        # 검색 순서 A(closed) B(open) C(unknown) D(closed) E(before) → B C E A D
        self.search.return_value = {"policies": [policy(pid, rank) for rank, pid in enumerate("ABCDE", 1)]}
        self.with_reception({"A": "closed", "B": "open", "C": "unknown", "D": "closed", "E": "before"})
        self.generate.return_value = answer("마감 정책 [4], 접수 정책 [1]", ids=[1, 4])
        response = self.ask()
        contexts = self.generate.call_args.args[1]
        self.assertEqual([c["policy_id"] for c in contexts], list("BCEAD"))
        self.assertEqual([c["source_id"] for c in contexts], [1, 2, 3, 4, 5])
        self.assertEqual([c["policy_id"] for c in self.generate.call_args.args[2]], list("BCEAD"))
        self.assertEqual([c.policy_id for c in response.policy_checks], list("BCEAD"))
        # 본문 인용 번호는 새 Context 순서 기준으로 재번호되고, 출처 문서와 일치한다.
        self.assertEqual(response.answer, "마감 정책 [2], 접수 정책 [1]")
        self.assertEqual([c.document_id for c in response.citations], ["DB", "DA"])
        self.assertEqual(self.check.call_count, 5)

    def test_closed_policy_does_not_take_slots_when_others_have_enough_contexts(self):
        def many(pid, rank, count):
            return policy(pid, rank, evidence=[
                {"rank": rank * 10 + i, "chunk_id": f"C{pid}{i}", "document_id": f"D{pid}",
                 "text": f"원문 {pid}{i}", "source_url": "https://example.org/notice"} for i in range(count)])
        self.search.return_value = {"policies": [many("A", 1, 2), many("B", 2, 3), many("C", 3, 3)]}
        self.with_reception({"A": "closed", "B": "open", "C": "open"})
        self.ask()
        contexts = self.generate.call_args.args[1]
        self.assertEqual([c["chunk_id"] for c in contexts], ["CB0", "CC0", "CB1", "CB2", "CC1"])
        self.assertNotIn("A", [c["policy_id"] for c in self.generate.call_args.args[2]])
        # 채택되지 않은 마감 정책도 판정은 한 번만 했다(정책 삭제 아님, 카드에서만 빠짐).
        self.assertEqual(self.check.call_count, 3)

    def test_all_closed_keeps_original_order(self):
        self.search.return_value = {"policies": [policy(pid, rank) for rank, pid in enumerate("XYZ", 1)]}
        self.with_reception({"X": "closed", "Y": "closed", "Z": "closed"})
        response = self.ask()
        self.assertEqual([c.policy_id for c in response.policy_checks], list("XYZ"))
        self.assertTrue(all(c.reception_status == "closed" for c in response.policy_checks))

    def test_invalid_checker_output_for_unselected_candidate_is_not_used_for_ordering(self):
        self.search.return_value = {"policies": [policy(pid, rank) for rank, pid in enumerate("ABCDEF", 1)]}
        statuses = {"A": "closed"}
        def check(profile, item):
            if item["policy_id"] == "B":
                return {"policy_id": "B", "conditions": [], "reception_status": "closed"}  # 형식 오류
            return {**checker(profile, item), "reception_status": statuses.get(item["policy_id"], "open")}
        self.check.side_effect = check
        # 형식 오류 정책(B)은 후순위로 숨기지 않으므로 채택되고, 그 결과 오류가 드러난다.
        with self.assertRaisesRegex(AskError, "ELIGIBILITY_OUTPUT_INVALID"):
            self.ask()

    def test_checker_needs_check_reason_is_preserved(self):
        reasons = {
            "income": "소득 원문 확인 필요: 신청 유형과 연소득(일반형은 부부합산 5천만원 이하 등)을 공고에서 확인하세요.",
            "housing": "가구 원문 확인 필요: 가구원 수를 공고 기준으로 확인하세요.",
        }
        self.check.side_effect = lambda profile, item: {
            "policy_id": item["policy_id"], "reception_status": "unknown",
            "conditions": [{"field": f, "label": f, "reason": r, "status": "needs_check"} for f, r in reasons.items()]}
        for quality in ({}, {"hold_auto_eligibility": True, "affected_attributes": ["all"]},
                        {"hold_auto_eligibility": True, "affected_attributes": ["income"]}):
            with self.subTest(quality=quality):
                self.search.return_value = {"policies": [policy(data_quality=quality)]}
                response = self.ask()
                self.assertEqual([c.reason for c in response.policy_checks[0].conditions], list(reasons.values()))
                self.assertEqual([c["reason"] for c in self.generate.call_args.args[2][0]["conditions"]],
                                 list(reasons.values()))

    def test_downgraded_condition_keeps_checker_reason_and_adds_hold_reason(self):
        cases = (
            (policy(data_quality={"hold_auto_eligibility": True, "affected_attributes": ["all"]}), "데이터가 불확실"),
            (policy(graph={"eligibility": {"unknown_logic_groups": ["G"]}}), "데이터가 불확실"),
        )
        for item, hold in cases:
            with self.subTest(item=item):
                self.search.return_value = {"policies": [item]}
                conditions = self.ask().policy_checks[0].conditions
                self.assertEqual([c.status for c in conditions], ["needs_check", "needs_check"])
                self.assertTrue(conditions[0].reason.startswith("가상 조건 충족"))
                self.assertIn(hold, conditions[0].reason)

    def test_affected_attributes_scope(self):
        # all·목록 밖 값(target, education 등)은 전체 보류, 목록 안 값은 그 필드만 보류.
        cases = (
            (["all"], ["needs_check", "needs_check"]),
            (["target"], ["needs_check", "needs_check"]),
            (["education"], ["needs_check", "needs_check"]),
            (["region"], ["eligible", "needs_check"]),
            (["age"], ["needs_check", "ineligible"]),
        )
        for affected, expected in cases:
            with self.subTest(affected=affected):
                quality = {"hold_auto_eligibility": True, "affected_attributes": affected}
                self.search.return_value = {"policies": [policy(data_quality=quality)]}
                self.assertEqual([c.status for c in self.ask().policy_checks[0].conditions], expected)

    def test_display_fn_summarizes_response_only(self):
        income = {"field": "income", "label": "소득", "reason": "중위소득 기준 확인 필요", "status": "needs_check"}
        rows = [income, dict(income), dict(income), {**income, "reason": "가구 소득 기준 확인 필요"},
                {"field": "eligibility", "label": "종합", "reason": "OR 그룹 보류", "status": "needs_check"}]
        self.check.side_effect = lambda profile, item: {
            "policy_id": item["policy_id"], "reception_status": "unknown", "conditions": [dict(r) for r in rows]}

        def dedupe(conditions):
            shown = []
            for item in conditions:
                if item not in shown:
                    shown.append(item)
            return shown

        request = AskRequest(question="질문", profile={"age": 25, "region": "서울", "income": None}, history=[])
        response = run_ask_pipeline(request, search_fn=self.search, check_fn=self.check,
                                    generate_fn=self.generate, display_fn=dedupe)
        self.assertEqual([c.reason for c in response.policy_checks[0].conditions],
                         ["중위소득 기준 확인 필요", "가구 소득 기준 확인 필요", "OR 그룹 보류"])
        # LLM에는 요약 전 원본 조건 5개를 그대로 전달한다.
        self.assertEqual(len(self.generate.call_args.args[2][0]["conditions"]), 5)

    def test_display_fn_cannot_change_status_or_invent_rows(self):
        def bad_status(conditions):
            return [{**conditions[0], "status": "eligible"}]

        def drops_field(conditions):
            return conditions[:1]

        for display in (bad_status, drops_field, lambda conditions: []):
            with self.subTest(display=display):
                request = AskRequest(question="질문", profile={"age": 25, "region": "서울", "income": None}, history=[])
                with self.assertRaisesRegex(AskError, "ELIGIBILITY_OUTPUT_INVALID"):
                    run_ask_pipeline(request, search_fn=self.search, check_fn=self.check,
                                     generate_fn=self.generate, display_fn=display)

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

    def test_generator_defined_errors(self):
        for failure, code in (
            (LLMUnavailableError("private-value"), "LLM_UNAVAILABLE"),
            (LLMTimeoutError("private-value"), "LLM_TIMEOUT"),
            (LLMOutputError("private-value"), "LLM_OUTPUT_INVALID"),
        ):
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
