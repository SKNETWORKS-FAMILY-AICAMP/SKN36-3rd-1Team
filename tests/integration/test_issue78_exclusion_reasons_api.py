"""#78 후속: Checker exclusion_reasons → Pipeline → Generator 입력 → FastAPI 응답 전달 검증.

- 가상 정책(sample_only)은 실제 정책 기준이 아니다. 실제 Checker(check_eligibility)로 판정한다.
- POL_005 클래스만 v1.4.0 후보 릴리스의 실제 조건 트리·품질 플래그를 읽는다(DB 호출 없음).
  근거 청크는 가상 원문이며, "품질 보류 해제" 케이스는 동작 확인용 시뮬레이션이다(실제 데이터 아님).
- Generator는 Mock 또는 _call_openai 패치로 대체하며 OpenAI를 호출하지 않는다.
"""

from collections import defaultdict
import copy
import json
from importlib.util import find_spec
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from test_rag_pipeline import answer, policy

from backend.api.schemas import AskRequest, AskResponse, UserProfile
from backend.eligibility.checker import check_eligibility, summarize_conditions_for_display
from backend.rag.pipeline import AskError, run_ask_pipeline

if find_spec("fastapi") is not None and find_spec("httpx") is not None:
    from fastapi.testclient import TestClient

    from backend.api.main import create_app
else:
    TestClient = None

ROOT = Path(__file__).resolve().parents[2]
GRAPH = ROOT / "data" / "releases" / "v1.4.0-candidate" / "neo4j"
EVIDENCE_TEXT = ("[지원대상]\n만 75세 미만 가상 대상자(sample_only)\n"
                 "[문의처]\n가상기관 (02-000-0078)\n")


def age(value, name=None):
    return {"type": "AGE", "name": name or f"연령조건: {value}", "operator": "BETWEEN", "value": value, "unit": "세"}


UNKNOWN_LEAF = {"type": "SPECIAL_TARGET", "name": "제외: 가상 기타 대상", "value": "가상 기타 대상"}


def exclusion_policy(pid="POL_X78", rank=1, *, exclusions=(age("75~120", "연령조건: 75세 이상"),),
                     required=(), nested=None, quality=None):
    """sample_only: ROOT(AND) → NOT → OR → 제외 조건. nested가 있으면 NOT 아래 그룹을 직접 지정한다."""
    exclusion_group = nested or {"group_id": f"{pid}_OR", "logic": "OR", "conditions": list(exclusions), "groups": []}
    tree = {"group_id": f"{pid}_ROOT", "logic": "AND", "conditions": list(required),
            "groups": [{"group_id": f"{pid}_NOT", "logic": "NOT", "conditions": [], "groups": [exclusion_group]}]}
    return policy(pid, rank, policy_name=f"가상 제외 정책 {pid}", data_quality=quality or {},
                  graph={"policy": {"application_period": "상시신청", "eligibility": "가상 제외 조건"},
                         "eligibility": {"condition_tree": [tree], "unknown_logic_groups": [], "structure_issues": []}},
                  evidence=[{"rank": rank, "chunk_id": f"C_{pid}", "document_id": f"D_{pid}", "text": EVIDENCE_TEXT,
                             "source_url": f"https://example.org/{pid}"}])


def plain_policy(pid="POL_PLAIN", rank=2):
    """sample_only: 제외 조건이 없는 AND 정책(만 19~39세)."""
    tree = {"group_id": f"{pid}_ROOT", "logic": "AND", "conditions": [age("19~39")], "groups": []}
    return policy(pid, rank, policy_name="가상 일반 정책",
                  graph={"policy": {"application_period": "상시신청"},
                         "eligibility": {"condition_tree": [tree], "unknown_logic_groups": [], "structure_issues": []}},
                  evidence=[{"rank": rank, "chunk_id": f"C_{pid}", "document_id": f"D_{pid}", "text": "가상 일반 원문",
                             "source_url": f"https://example.org/{pid}"}])


def request(age_value, question="가상 정책 신청할 수 있나요?", region="서울"):
    return AskRequest(question=question, profile=UserProfile(age=age_value, region=region, income=None), history=[])


def direct(age_value, item, region="서울"):
    """Pipeline과 같은 정규화 프로필로 Checker를 직접 호출한 기대값."""
    return check_eligibility({"age": age_value, "region": region, "income": None}, copy.deepcopy(item))


class PipelineExclusionTests(unittest.TestCase):
    def ask(self, policies, age_value, check_fn=check_eligibility, **kwargs):
        self.generate = Mock(return_value=answer("가상 답변 [1]"))
        search = Mock(side_effect=lambda *a, **k: {"policies": copy.deepcopy(policies)})
        return run_ask_pipeline(request(age_value), search_fn=search, check_fn=check_fn,
                                generate_fn=self.generate, **kwargs)

    def generator_checks(self):
        return {check["policy_id"]: check for check in self.generate.call_args.args[2]}

    def test_age_boundaries_match_checker_exactly(self):
        item = exclusion_policy()
        for value, excluded in ((74, False), (75, True), (76, True), (80, True), (None, False)):
            with self.subTest(age=value):
                expected = direct(value, item)
                check = self.ask([item], value).policy_checks[0]
                reasons = [reason.model_dump() for reason in check.exclusion_reasons or []]
                self.assertEqual(reasons or None, expected["exclusion_reasons"])
                self.assertEqual(bool(reasons), excluded)
                # conditions·종합 판정은 Checker 그대로(재계산·뒤집기 없음).
                self.assertEqual([c.model_dump() for c in check.conditions], expected["conditions"])
                if excluded:
                    self.assertEqual(check.conditions[-1].status, "ineligible")
                    self.assertEqual([r["field"] for r in reasons], ["age"])
                    self.assertIn(f"만 {value}세", reasons[0]["reason"])
                    # Generator가 받은 같은 정책의 제외 사유가 API 값과 같다.
                    self.assertEqual(self.generator_checks()[item["policy_id"]]["exclusion_reasons"], reasons)

    def test_80_year_old_exclusion_leaf_is_not_presented_as_eligibility(self):
        check = self.ask([exclusion_policy()], 80).policy_checks[0]
        leaf = next(c for c in check.conditions if c.field == "age")
        # NOT 내부 개별 조건의 eligible은 '제외 조건에 해당'이라는 뜻이고, 사유 문구만으로는 제외 맥락이 없다.
        # 그래서 종합 판정(ineligible)과 함께 exclusion_reasons가 '지원 제외 기준'임을 명시해 전달돼야 한다.
        self.assertEqual(leaf.status, "eligible")
        self.assertNotIn("제외", leaf.reason)
        self.assertEqual(check.conditions[-1].status, "ineligible")
        self.assertEqual(len(check.exclusion_reasons), 1)
        self.assertIn("지원 제외 기준", check.exclusion_reasons[0].reason)
        self.assertIn("만 80세", check.exclusion_reasons[0].reason)

    def test_multiple_exclusions_keep_checker_order(self):
        item = exclusion_policy(exclusions=(age("65~120", "연령조건: 65세 이상"), age("75~120", "연령조건: 75세 이상")))
        expected = direct(80, item)["exclusion_reasons"]
        self.assertEqual(len(expected), 2)
        check = self.ask([item], 80).policy_checks[0]
        self.assertEqual([r.model_dump() for r in check.exclusion_reasons], expected)

    def test_partial_unknown_keeps_confirmed_reason_and_74_stays_unconfirmed(self):
        item = exclusion_policy(exclusions=(age("75~120"), UNKNOWN_LEAF))
        at_80 = self.ask([item], 80).policy_checks[0]
        self.assertEqual([r.model_dump() for r in at_80.exclusion_reasons], direct(80, item)["exclusion_reasons"])
        at_74 = self.ask([item], 74).policy_checks[0]
        self.assertIsNone(at_74.exclusion_reasons)
        self.assertEqual(at_74.conditions[-1].status, "needs_check")

    def test_all_unknown_exclusions_are_not_confirmed(self):
        check = self.ask([exclusion_policy(exclusions=(UNKNOWN_LEAF,))], 80).policy_checks[0]
        self.assertIsNone(check.exclusion_reasons)
        self.assertNotEqual(check.conditions[-1].status, "eligible")

    def test_unverified_full_age_range_is_not_exclusion(self):
        check = self.ask([exclusion_policy(exclusions=(age("0~120"),))], 80).policy_checks[0]
        self.assertIsNone(check.exclusion_reasons)

    def test_and_composite_and_nested_not(self):
        # NOT → AND(65세 이상, 75세 이상): 둘 다 해당해야 제외. 하나의 복합 사유로 유지된다.
        both = {"group_id": "AND", "logic": "AND", "conditions": [age("65~120"), age("75~120")], "groups": []}
        item = exclusion_policy(nested=both)
        for value in (70, 80):
            with self.subTest(age=value):
                check = self.ask([item], value).policy_checks[0]
                expected = direct(value, item)["exclusion_reasons"]
                self.assertEqual([r.model_dump() for r in check.exclusion_reasons or []] or None, expected)
        self.assertIsNone(direct(70, item)["exclusion_reasons"])
        self.assertEqual(len(direct(80, item)["exclusion_reasons"]), 1)
        # NOT → NOT → OR(75세 이상): 이중 부정은 75세 이상만 '제외 아님'. 80세는 제외 사유가 없다.
        double = {"group_id": "INNER_NOT", "logic": "NOT", "conditions": [], "groups": [
            {"group_id": "INNER_OR", "logic": "OR", "conditions": [age("75~120")], "groups": []}]}
        item = exclusion_policy(nested=double)
        for value in (74, 80):
            with self.subTest(nested_not_age=value):
                check = self.ask([item], value).policy_checks[0]
                expected = direct(value, item)["exclusion_reasons"]
                self.assertEqual([r.model_dump() for r in check.exclusion_reasons or []] or None, expected)

    def test_quality_warning_never_confirms_exclusion(self):
        for quality in ({"hold_auto_eligibility": True, "affected_attributes": ["age"]},
                        {"hold_auto_eligibility": True, "affected_attributes": []}):
            with self.subTest(quality=quality):
                check = self.ask([exclusion_policy(quality=quality)], 80).policy_checks[0]
                self.assertIsNone(check.exclusion_reasons)
                self.assertEqual(check.conditions[-1].status, "needs_check")

    def test_reasons_stay_with_their_own_policy(self):
        excluded = exclusion_policy("POL_EX", 1)
        normal = plain_policy("POL_PLAIN", 2)
        response = self.ask([excluded, normal], 80)
        by_pid = {check.policy_id: check for check in response.policy_checks}
        self.assertEqual(set(by_pid), {"POL_EX", "POL_PLAIN"})
        self.assertEqual([r.model_dump() for r in by_pid["POL_EX"].exclusion_reasons],
                         direct(80, excluded)["exclusion_reasons"])
        self.assertIsNone(by_pid["POL_PLAIN"].exclusion_reasons)
        self.assertEqual(by_pid["POL_EX"].source_url, "https://example.org/POL_EX")
        generated = self.generator_checks()
        self.assertIsNone(generated["POL_PLAIN"]["exclusion_reasons"])
        self.assertEqual(generated["POL_EX"]["exclusion_reasons"],
                         [r.model_dump() for r in by_pid["POL_EX"].exclusion_reasons])

    def test_policy_not_adopted_into_context_is_not_returned(self):
        # 근거가 없는(인용 불가) 정책은 판정 대상이 아니다. 그 정책의 제외 사유는 어디에도 섞이지 않는다.
        hidden = exclusion_policy("POL_HIDDEN", 1)
        hidden["evidence"] = []
        response = self.ask([hidden, plain_policy("POL_PLAIN", 2)], 80)
        self.assertEqual([c.policy_id for c in response.policy_checks], ["POL_PLAIN"])
        self.assertIsNone(response.policy_checks[0].exclusion_reasons)

    def test_display_summary_keeps_exclusion_reasons(self):
        item = exclusion_policy()
        check = self.ask([item], 80, display_fn=summarize_conditions_for_display).policy_checks[0]
        self.assertEqual([r.model_dump() for r in check.exclusion_reasons], direct(80, item)["exclusion_reasons"])

    def test_no_evidence_answer_returns_no_policy_checks(self):
        self.generate = Mock(return_value={"status": "no_evidence", "answer": "근거 없음", "used_source_ids": [],
                                           "follow_up_questions": []})
        response = run_ask_pipeline(request(80), search_fn=Mock(return_value={"policies": [exclusion_policy()]}),
                                    check_fn=check_eligibility, generate_fn=self.generate)
        self.assertEqual(response.policy_checks, [])


def stub_checker(reasons, root="ineligible", extra=()):
    def check(profile, item):
        return {"policy_id": item["policy_id"], "reception_status": "open", "exclusion_reasons": reasons,
                "conditions": [*extra,
                               {"field": "age", "label": "나이", "reason": "가상 제외 조건 해당", "status": "eligible"},
                               {"field": "eligibility", "label": "자격 요건",
                                "reason": "모든 조건을 충족해야 합니다(종합 결과).", "status": root}]}
    return check


AGE_REASON = {"field": "age", "label": "나이", "reason": "가상 연령 제외 사유입니다."}


class PipelineTrustBoundaryTests(unittest.TestCase):
    """Pipeline은 직렬화된 Checker 결과를 받으므로, 현재 판정과 어긋나는 제외 사유는 전달하지 않는다."""

    def ask(self, check_fn, item=None):
        generate = Mock(return_value=answer("가상 답변 [1]"))
        return run_ask_pipeline(request(80), search_fn=Mock(return_value={"policies": [item or policy()]}),
                                check_fn=check_fn, generate_fn=generate)

    def test_confirmed_reason_passes_through_unchanged(self):
        check = self.ask(stub_checker([AGE_REASON])).policy_checks[0]
        self.assertEqual([r.model_dump() for r in check.exclusion_reasons], [AGE_REASON])

    def test_missing_or_empty_reasons_stay_null(self):
        for reasons in (None, []):
            with self.subTest(reasons=reasons):
                self.assertIsNone(self.ask(stub_checker(reasons)).policy_checks[0].exclusion_reasons)
        legacy = lambda profile, item: {k: v for k, v in stub_checker(None)(profile, item).items()
                                        if k != "exclusion_reasons"}
        self.assertIsNone(self.ask(legacy).policy_checks[0].exclusion_reasons)

    def test_reason_without_ineligible_root_is_dropped(self):
        for root in ("needs_check", "eligible"):
            with self.subTest(root=root):
                self.assertIsNone(self.ask(stub_checker([AGE_REASON], root=root)).policy_checks[0].exclusion_reasons)

    def test_pipeline_quality_hold_drops_reasons(self):
        held = policy(data_quality={"hold_auto_eligibility": True, "affected_attributes": []})
        check = self.ask(stub_checker([AGE_REASON]), held).policy_checks[0]
        self.assertIsNone(check.exclusion_reasons)

    def test_reason_for_field_held_by_pipeline_is_dropped(self):
        # Pipeline은 소득 조건을 항상 확인 필요로 낮춘다. 같은 필드의 '확정 제외'는 전달하지 않는다.
        income_row = {"field": "income", "label": "소득", "reason": "가상 소득 제외 조건 해당", "status": "eligible"}
        income_reason = {"field": "income", "label": "소득", "reason": "가상 소득 제외 사유입니다."}
        composite = {"field": "eligibility", "label": "자격 요건 (복합 제외 조건)", "reason": "가상 복합 사유입니다."}
        check = self.ask(stub_checker([income_reason, AGE_REASON, composite], extra=[income_row])).policy_checks[0]
        self.assertEqual([r.model_dump() for r in check.exclusion_reasons], [AGE_REASON])

    def test_invalid_reason_shape_is_rejected(self):
        for reasons in ([{"field": "age", "label": "나이", "reason": " "}], [{"field": "age"}], "75세 이상", [1]):
            with self.subTest(reasons=reasons):
                with self.assertRaises(AskError) as caught:
                    self.ask(stub_checker(reasons))
                self.assertEqual(caught.exception.code, "ELIGIBILITY_OUTPUT_INVALID")

    def test_unrelated_field_hold_keeps_confirmed_reason(self):
        # 선택 정책 지역 보류는 region 행만 낮춘다. 무관한 나이 제외 사유는 남고, 복합 사유만 보수적으로 뺀다.
        region_row = {"field": "region", "label": "거주 지역", "reason": "가상 지역 조건", "status": "eligible"}
        composite = {"field": "eligibility", "label": "자격 요건 (복합 제외 조건)", "reason": "가상 복합 사유입니다."}
        item = policy("POL_SEL", graph={"policy": {"application_period": "상시신청"}, "eligibility": {},
                                        "regions": [{"name": "부산광역시"}]})
        ask = AskRequest(question="가상 정책 신청할 수 있나요?", selected_policy_id="POL_SEL",
                         profile=UserProfile(age=80, region="서울", income=None), history=[])
        response = run_ask_pipeline(ask, search_fn=Mock(return_value={"policies": [item]}),
                                    check_fn=stub_checker([AGE_REASON, composite], extra=[region_row]),
                                    generate_fn=Mock(return_value=answer("가상 답변 [1]")))
        check = response.policy_checks[0]
        self.assertEqual(next(c for c in check.conditions if c.field == "region").status, "needs_check")
        self.assertEqual(check.conditions[-1].status, "ineligible")
        self.assertEqual([r.model_dump() for r in check.exclusion_reasons], [AGE_REASON])

    def test_composite_reason_and_order_kept_without_any_hold(self):
        composite = {"field": "eligibility", "label": "자격 요건 (복합 제외 조건)", "reason": "가상 복합 사유입니다."}
        second = {"field": "age", "label": "나이", "reason": "가상 두 번째 연령 제외 사유입니다."}
        check = self.ask(stub_checker([composite, AGE_REASON, second])).policy_checks[0]
        self.assertEqual([r.model_dump() for r in check.exclusion_reasons], [composite, AGE_REASON, second])

    def test_checker_result_is_not_mutated(self):
        produced = []

        def recording(profile, item):
            produced.append(stub_checker([AGE_REASON])(profile, item))
            return produced[-1]

        self.ask(recording)
        self.assertEqual(produced[0], stub_checker([AGE_REASON])({}, {"policy_id": "P"}))

    def test_other_policy_or_incomplete_checker_result_is_rejected(self):
        other = lambda profile, item: {**stub_checker([AGE_REASON])(profile, item), "policy_id": "OTHER"}
        incomplete = lambda profile, item: {"policy_id": item["policy_id"], "exclusion_reasons": [AGE_REASON]}
        for check_fn in (other, incomplete):
            with self.assertRaises(AskError) as caught:
                self.ask(check_fn)
            self.assertEqual(caught.exception.code, "ELIGIBILITY_OUTPUT_INVALID")


class GeneratorInputTests(unittest.TestCase):
    def test_real_generator_prompt_carries_only_each_policys_reasons(self):
        from backend.llm import generator

        excluded, normal = exclusion_policy("POL_EX", 1), plain_policy("POL_PLAIN", 2)
        raw = json.dumps(answer("가상 답변 [1]"), ensure_ascii=False)
        with patch.object(generator, "_call_openai", return_value=raw) as sdk:
            response = run_ask_pipeline(request(80), search_fn=Mock(return_value={"policies": [excluded, normal]}),
                                        check_fn=check_eligibility, generate_fn=generator.generate_answer)
        sdk.assert_called_once()
        content = sdk.call_args.args[0][1]["content"]
        sent = json.loads(content[content.index("{"):])
        by_pid = {check["policy_id"]: check for check in sent["policy_checks"]}
        self.assertEqual(by_pid["POL_EX"]["exclusion_reasons"], direct(80, excluded)["exclusion_reasons"])
        self.assertIsNone(by_pid["POL_PLAIN"]["exclusion_reasons"])
        api = {check.policy_id: check for check in response.policy_checks}
        self.assertEqual([r.model_dump() for r in api["POL_EX"].exclusion_reasons], by_pid["POL_EX"]["exclusion_reasons"])
        # 시스템 프롬프트(#108)가 제외 사유 해석 규칙을 그대로 담고 있다.
        self.assertIn("exclusion_reasons", sdk.call_args.args[0][0]["content"])


@unittest.skipIf(TestClient is None, "API 검증에는 fastapi와 httpx가 필요합니다.")
class AskApiExclusionTests(unittest.TestCase):
    def post(self, body, policies, reply=None, generate=None):
        session = Mock()
        session.search_policy_context.side_effect = lambda *a, **k: {"policies": copy.deepcopy(policies)}
        self.generator = generate or Mock(return_value=reply or answer("가상 답변 [1] [2]", ids=[1, 2]))
        app = create_app(session_factory=Mock(return_value=session), generate_fn=self.generator, catalog_factory=Mock)
        with TestClient(app) as client:
            return client.post("/api/ask", json=body)

    def body(self, age_value):
        return {"question": "가상 정책 신청할 수 있나요?", "profile": {"age": age_value, "region": "서울", "income": None},
                "history": []}

    def test_http_response_carries_checker_reasons_per_policy(self):
        excluded, normal = exclusion_policy("POL_EX", 1), plain_policy("POL_PLAIN", 2)
        for value in (74, 75, 76, 80, None):
            with self.subTest(age=value):
                response = self.post(self.body(value), [excluded, normal])
                self.assertEqual(response.status_code, 200)
                data = response.json()
                AskResponse.model_validate(data)
                by_pid = {check["policy_id"]: check for check in data["policy_checks"]}
                expected = direct(value, excluded)
                self.assertEqual(by_pid["POL_EX"]["exclusion_reasons"], expected["exclusion_reasons"])
                self.assertEqual(by_pid["POL_EX"]["conditions"], expected["conditions"])
                self.assertIsNone(by_pid["POL_PLAIN"]["exclusion_reasons"])
                # 기존 필드 유지(하위 호환).
                for key in ("contacts", "official_requirements", "application_schedule", "requirement_excerpts",
                            "required_documents", "official_apply_url", "agency", "source_url", "reception_status"):
                    self.assertIn(key, by_pid["POL_EX"])
                self.assertEqual([(c["agency"], c["phone"]) for c in by_pid["POL_EX"]["contacts"]],
                                 [("가상기관", "02-000-0078")])
                self.assertEqual([c["url"] for c in data["citations"]],
                                 ["https://example.org/POL_EX", "https://example.org/POL_PLAIN"])
        self.assertIsNotNone(direct(80, excluded)["exclusion_reasons"])

    def test_error_responses_are_unchanged(self):
        response = self.post({"question": "질문", "profile": {"age": "80", "region": "서울", "income": None},
                              "history": []}, [exclusion_policy()])
        self.assertEqual(response.status_code, 422)
        from backend.llm.generator import LLMUnavailableError

        failing = Mock(side_effect=LLMUnavailableError("가상 장애"))
        response = self.post(self.body(80), [exclusion_policy()], generate=failing)
        self.assertGreaterEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"]["code"], "LLM_UNAVAILABLE")


def _rows(name):
    with open(GRAPH / name, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def release_policy(policy_id):
    """v1.4.0 후보 릴리스 파일로 Hybrid 검색과 같은 graph·data_quality를 만든다(DB 미사용). 근거 청크는 가상."""
    from database import data_quality_flags
    from database.hybrid_search import _build_condition_tree, assess_policy_data_quality

    groups = {g["group_id"]: g for g in _rows("nodes_condition_group.jsonl")}
    conditions = {c["condition_id"]: c for c in _rows("nodes_eligibility_condition.jsonl")}
    policies = {p["policy_id"]: p for p in _rows("nodes_policy.jsonl")}
    relations = defaultdict(list)
    for rel in _rows("relationships.jsonl"):
        relations[rel["source_id"]].append(rel)
    rows = {gid: {"group": group,
                  "child_groups": [{"group_id": e["target_id"], "relationship": e}
                                   for e in relations[gid] if e["relation"] == "CONTAINS_GROUP"],
                  "conditions": [{"condition": conditions[e["target_id"]], "relationship": e}
                                 for e in relations[gid] if e["relation"] == "CONTAINS_CONDITION"]}
            for gid, group in groups.items() if group["policy_id"] == policy_id}
    roots = [e for e in relations[policy_id] if e["relation"] == "HAS_ROOT_CONDITION_GROUP"]
    issues = []
    root = _build_condition_tree(policy_id, roots[0]["target_id"], rows, roots[0], issues)
    eligibility = {"condition_tree": [root], "unknown_logic_groups": [], "structure_issues": issues}
    flags = data_quality_flags.load_flags().get("flags_by_policy", {})
    quality = assess_policy_data_quality({"policy": policies[policy_id], "eligibility": eligibility},
                                         static_flags=flags.get(policy_id, []))
    return policy(policy_id, 1, policy_name=policies[policy_id]["policy_name"], data_quality=quality,
                  graph={"policy": policies[policy_id], "eligibility": eligibility},
                  evidence=[{"rank": 1, "chunk_id": f"C_{policy_id}", "document_id": f"D_{policy_id}",
                             "text": "가상 근거 원문(sample_only)", "source_url": "https://example.org/pol005"}])


@unittest.skipUnless((GRAPH / "nodes_policy.jsonl").exists(), "v1.4.0 후보 릴리스 파일이 필요합니다.")
class Pol005ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.item = release_policy("POL_005")

    def ask(self, item, value):
        return run_ask_pipeline(request(value, "국민내일배움카드 신청할 수 있나요?"),
                                search_fn=Mock(return_value={"policies": [copy.deepcopy(item)]}),
                                check_fn=check_eligibility, generate_fn=Mock(return_value=answer("가상 답변 [1]")))

    def test_structure_is_not_or_with_13_exclusions(self):
        root = self.item["graph"]["eligibility"]["condition_tree"][0]
        not_group = next(g for g in root["groups"] if g["logic"] == "NOT")
        or_group = not_group["groups"][0]
        self.assertEqual(or_group["logic"], "OR")
        self.assertEqual(len(or_group["conditions"]), 13)
        self.assertIn("COND_AGE_75_120", {c["condition_id"] for c in or_group["conditions"]})

    def test_current_release_quality_hold_keeps_reasons_null_for_every_age(self):
        # 실데이터: EXTRACTED_NOT_VERIFIED 등으로 hold_auto_eligibility=true → Checker가 종합 판정을 보류한다.
        self.assertTrue(self.item["data_quality"].get("hold_auto_eligibility"))
        for value in (74, 75, 76, 80, None):
            with self.subTest(age=value):
                expected = direct(value, self.item)
                check = self.ask(self.item, value).policy_checks[0]
                self.assertIsNone(expected["exclusion_reasons"])
                self.assertIsNone(check.exclusion_reasons)
                self.assertEqual(check.conditions[-1].status, "needs_check")
                self.assertNotIn("ineligible", {c.status for c in check.conditions if c.field == "eligibility"})

    def test_simulated_quality_release_delivers_age_exclusion(self):
        # 시뮬레이션(실제 데이터 아님): 품질 보류가 해제됐다고 가정했을 때 연결 경로가 Checker 사유를 그대로 전달한다.
        item = copy.deepcopy(self.item)
        item["data_quality"] = {}
        for value, excluded in ((74, False), (75, True), (76, True), (80, True), (None, False)):
            with self.subTest(age=value):
                expected = direct(value, item)
                check = self.ask(item, value).policy_checks[0]
                self.assertEqual([r.model_dump() for r in check.exclusion_reasons or []] or None,
                                 expected["exclusion_reasons"])
                self.assertEqual(bool(check.exclusion_reasons), excluded)
                if excluded:
                    self.assertEqual([r.field for r in check.exclusion_reasons], ["age"])
                    self.assertEqual(check.conditions[-1].status, "ineligible")


if __name__ == "__main__":
    unittest.main()
