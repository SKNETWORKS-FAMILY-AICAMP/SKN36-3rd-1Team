"""#122 후속(#40): Checker → Pipeline(Generator 입력) → FastAPI 판정 전달 일관성 추가 검증.

PR #148(실제 R3D·LLM)에서 미검증으로 남은 시나리오만 다룬다. 기존 #40·#78 단위 회귀를 중복하지 않는다.

검증 계층(보고서와 같은 이름으로 구분한다)
- [F] Fixture: 가상 정책(sample_only). 실제 정책 기준이 아니다. 판정은 실제 check_eligibility로 한다.
- [R] 릴리스 파일 재생: data/releases/v1.4.0-candidate의 실제 조건 트리·정적 품질 플래그를 읽는다.
  Neo4j·Qdrant·Ollama를 호출하지 않으며 근거 청크는 가상이다. 실제 R3D DB 검증이 아니다.

모든 요청은 FastAPI TestClient → run_ask_pipeline → (실제 Checker) → Generator 대역으로 흐른다.
Generator 대역은 Pipeline이 넘긴 policy_checks·contexts를 그대로 기록한다(= Pipeline 단계 관측값).
OpenAI·DB를 호출하지 않는다.
"""

from collections import defaultdict
import copy
from importlib.util import find_spec
import json
from pathlib import Path
import re
import unittest
from unittest.mock import Mock

from issue122_followup_oracles import CITATION, overall, rows, semantic_mismatch
from test_rag_pipeline import answer, policy

from backend.eligibility.checker import check_eligibility
from backend.rag.pipeline import ELIGIBILITY_NOTICE, HOLD_NOTICE_TEXT, INELIGIBLE_NOTICE

if find_spec("fastapi") is not None and find_spec("httpx") is not None:
    from fastapi.testclient import TestClient

    from backend.api.main import create_app
else:  # pragma: no cover - 의존성 없는 환경
    TestClient = None

ROOT = Path(__file__).resolve().parents[2]
GRAPH = ROOT / "data" / "releases" / "v1.4.0-candidate" / "neo4j"


# ---------------------------------------------------------------------------
# [F] sample_only 조건·정책 생성기
# ---------------------------------------------------------------------------
def age(value, name=None):
    return {"type": "AGE", "name": name or f"연령조건: {value}", "operator": "BETWEEN", "value": value, "unit": "세"}


def target(text):
    return {"type": "SPECIAL_TARGET", "name": text, "operator": "IN", "value": text}


def income(value):
    return {"type": "INCOME", "name": f"소득조건: {value}", "operator": "IN_RANGE", "value": value}


def group(gid, logic, conditions=(), groups=()):
    return {"group_id": gid, "logic": logic, "conditions": list(conditions), "groups": list(groups)}


def unknown_income(pid):
    return group(f"{pid}_UNK_INC", "UNKNOWN", [income("중위소득_0_50_"), income("중위소득_51_100_")])


# 소득 UNKNOWN 단일 경고 + 요약이 서로 맞는 경우만 #40 범위 축소(income)가 허용된다.
SCOPED_INCOME = {
    "hold_auto_eligibility": True, "affected_attributes": ["income"], "hold_reasons": ["UNKNOWN_CONDITION_GROUP"],
    "warnings": [{"code": "UNKNOWN_CONDITION_GROUP", "affects": ["income"], "blocks_auto_eligibility": True}],
}
# 전체 데이터 품질 보류(affects=all).
GLOBAL_HOLD = {
    "hold_auto_eligibility": True, "affected_attributes": ["all"], "hold_reasons": ["EXTRACTED_NOT_VERIFIED"],
    "warnings": [{"code": "EXTRACTED_NOT_VERIFIED", "affects": ["all"], "blocks_auto_eligibility": True}],
}
# 소득 UNKNOWN에 전체 보류 경고가 함께 있으면 범위 축소가 금지된다.
SCOPED_PLUS_GLOBAL = {
    "hold_auto_eligibility": True, "affected_attributes": ["all"],
    "hold_reasons": ["UNKNOWN_CONDITION_GROUP", "EXTRACTED_NOT_VERIFIED"],
    "warnings": [
        {"code": "UNKNOWN_CONDITION_GROUP", "affects": ["income"], "blocks_auto_eligibility": True},
        {"code": "EXTRACTED_NOT_VERIFIED", "affects": ["all"], "blocks_auto_eligibility": True},
    ],
}


def sample_policy(pid, name, tree, *, quality=None, rank=1):
    unknown = sorted(_group_ids(tree, "UNKNOWN"))
    eligibility = {"condition_tree": [tree], "unknown_logic_groups": unknown, "structure_issues": []}
    return policy(
        pid, rank, policy_name=name, data_quality=copy.deepcopy(quality or {}),
        graph={"policy": {"application_period": "상시신청", "policy_name": name}, "eligibility": eligibility},
        evidence=[{"rank": rank, "chunk_id": f"C_{pid}", "document_id": f"D_{pid}",
                   "text": f"[지원대상]\n{name} 가상 원문(sample_only)\n", "source_url": f"https://example.org/{pid}"}],
    )


def _group_ids(tree, logic):
    found = {tree["group_id"]} if tree.get("logic") == logic else set()
    for child in tree.get("groups", []):
        found |= _group_ids(child, logic)
    return found


# 시나리오별 sample_only 정책. 이름은 정책명 직접 언급 판별(4자 이상, 공백 제거)에 쓰인다.
def required_and():
    return sample_policy("POL_F122_AND", "가상필수나이정책", group("F_AND", "AND", [age("19~39")]))


def optional_or_partial():
    tree = group("F_ORP", "AND", [age("19~39")],
                 [group("F_ORP_OR", "OR", [age("19~24"), target("가상 특수대상")])])
    return sample_policy("POL_F122_ORP", "가상선택경로정책", tree)


def income_unknown_optional_or_fail():
    pid = "POL_F122_UOR"
    tree = group("F_UOR", "AND", [age("19~64")],
                 [unknown_income(pid), group("F_UOR_OR", "OR", [age("19~24"), age("60~64")])])
    return sample_policy(pid, "가상소득선택정책", tree, quality=SCOPED_INCOME)


def income_unknown_required_age(quality=SCOPED_INCOME):
    pid = "POL_F122_UAG"
    tree = group("F_UAG", "AND", [age("19~39")], [unknown_income(pid)])
    return sample_policy(pid, "가상소득나이정책", tree, quality=quality)


def not_exclusion(quality=None, *, lower=75, pid="POL_F122_NOT", name="가상제외조건정책", rank=1):
    tree = group(f"{pid}_ROOT", "AND", [age("19~120")],
                 [group(f"{pid}_NOT", "NOT", [], [group(f"{pid}_OR", "OR", [age(f"{lower}~120", f"연령조건: {lower}세 이상")])])])
    return sample_policy(pid, name, tree, quality=quality, rank=rank)


def not_with_unknown_leaf():
    pid = "POL_F122_NUK"
    tree = group(f"{pid}_ROOT", "AND", [age("19~120")],
                 [group(f"{pid}_NOT", "NOT", [], [group(f"{pid}_OR", "OR", [age("75~120"), target("가상 기타 제외대상")])])])
    return sample_policy(pid, "가상미확정제외정책", tree)


def weak_basis():
    tree = group("F_WEAK", "AND", [{"type": "AGE", "name": "연령조건: 청년", "operator": "EQUAL", "value": "청년"}])
    return sample_policy("POL_F122_WEAK", "가상근거부족정책", tree)


def with_rank(item, rank):
    item = copy.deepcopy(item)
    item["best_rank"] = rank
    for chunk in item["evidence"]:
        chunk["rank"] = rank
    return item


# ---------------------------------------------------------------------------
# [R] 릴리스 파일 재생(DB 미사용). database.hybrid_search와 같은 트리·품질 계산을 쓴다.
# ---------------------------------------------------------------------------
def _rows(name):
    with open(GRAPH / name, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


_RELEASE_CACHE = {}


def release_policy(policy_id, rank=1):
    from database import data_quality_flags
    from database.hybrid_search import _build_condition_tree, _tree_groups, assess_policy_data_quality

    if not _RELEASE_CACHE:
        _RELEASE_CACHE["groups"] = {g["group_id"]: g for g in _rows("nodes_condition_group.jsonl")}
        _RELEASE_CACHE["conditions"] = {c["condition_id"]: c for c in _rows("nodes_eligibility_condition.jsonl")}
        _RELEASE_CACHE["policies"] = {p["policy_id"]: p for p in _rows("nodes_policy.jsonl")}
        relations = defaultdict(list)
        for rel in _rows("relationships.jsonl"):
            relations[rel["source_id"]].append(rel)
        _RELEASE_CACHE["relations"] = relations
        _RELEASE_CACHE["flags"] = data_quality_flags.load_flags().get("flags_by_policy", {})
    groups, conditions = _RELEASE_CACHE["groups"], _RELEASE_CACHE["conditions"]
    policies, relations = _RELEASE_CACHE["policies"], _RELEASE_CACHE["relations"]
    group_rows = {
        gid: {"group": item,
              "child_groups": [{"group_id": e["target_id"], "relationship": e}
                               for e in relations[gid] if e["relation"] == "CONTAINS_GROUP"],
              "conditions": [{"condition": conditions[e["target_id"]], "relationship": e}
                             for e in relations[gid] if e["relation"] == "CONTAINS_CONDITION"]}
        for gid, item in groups.items() if item["policy_id"] == policy_id
    }
    roots = [e for e in relations[policy_id] if e["relation"] == "HAS_ROOT_CONDITION_GROUP"]
    issues = []
    trees = [_build_condition_tree(policy_id, root["target_id"], group_rows, root, issues) for root in roots]
    eligibility = {
        "condition_tree": trees,
        "unknown_logic_groups": sorted(g["group_id"] for tree in trees for g in _tree_groups(tree)
                                       if g.get("logic") == "UNKNOWN"),
        "structure_issues": issues,
    }
    node = policies[policy_id]
    quality = assess_policy_data_quality({"policy": node, "eligibility": eligibility},
                                         static_flags=_RELEASE_CACHE["flags"].get(policy_id, []))
    return policy(policy_id, rank, policy_name=node["policy_name"], data_quality=quality,
                  graph={"policy": node, "eligibility": eligibility},
                  evidence=[{"rank": rank, "chunk_id": f"C_{policy_id}", "document_id": f"D_{policy_id}",
                             "text": "가상 근거 원문(sample_only)", "source_url": f"https://example.org/{policy_id}"}])


# ---------------------------------------------------------------------------
# 단계별 관측
# ---------------------------------------------------------------------------
class Trace:
    def __init__(self, response, checker, pipeline, contexts):
        self.http = response.status_code
        self.body = response.json()
        self.checker = checker            # policy_id → Checker 원본 결과(deepcopy)
        self.pipeline = pipeline          # policy_id → Generator가 받은 policy_check(deepcopy)
        self.contexts = contexts          # Generator가 받은 contexts 요약
        self.api = {c["policy_id"]: c for c in self.body.get("policy_checks", [])}


@unittest.skipIf(TestClient is None, "API 검증에는 fastapi와 httpx가 필요합니다.")
class ConsistencyBase(unittest.TestCase):
    maxDiff = None

    def ask(self, policies, *, age_value, region="", question="이 정책 신청할 수 있나요?",
            reply=None, check_fn=check_eligibility, selected=None):
        captured_checker, captured = {}, {}

        def checker(profile, item):
            result = check_fn(profile, item)
            captured_checker[item["policy_id"]] = copy.deepcopy(result)
            return result

        def generator(question_text, contexts, checks, history, profile=None):
            captured["checks"] = copy.deepcopy(checks)
            captured["contexts"] = [{k: c.get(k) for k in ("source_id", "policy_id", "document_id", "chunk_id")}
                                    for c in contexts]
            if callable(reply):
                return reply(contexts)
            if reply is not None:
                return reply
            first = contexts[0]["source_id"]
            return answer(f"검증용 고정 응답 [{first}]", ids=[first])

        session = Mock()
        session.search_policy_context.side_effect = lambda *a, **k: {"policies": copy.deepcopy(policies)}
        app = create_app(session_factory=Mock(return_value=session), check_fn=checker,
                         generate_fn=generator, catalog_factory=Mock)
        body = {"question": question, "profile": {"age": age_value, "region": region, "income": None}, "history": []}
        if selected:
            body["selected_policy_id"] = selected
        with TestClient(app) as client:
            response = client.post("/api/ask", json=body)
        return Trace(response, captured_checker,
                     {c["policy_id"]: c for c in captured.get("checks", [])}, captured.get("contexts", []))

    def assert_stage_consistent(self, trace, pid, expected_overall):
        """Policy ID·개별 행·그룹 행·종합·사유·제외 사유가 Checker→Pipeline→API에서 같은 의미인지 확인한다."""
        self.assertEqual(trace.http, 200, trace.body)
        self.assertIn(pid, trace.checker)
        self.assertIn(pid, trace.pipeline)
        self.assertIn(pid, trace.api)
        checker, pipeline, api = trace.checker[pid], trace.pipeline[pid], trace.api[pid]
        self.assertEqual(checker["policy_id"], pid)
        self.assertEqual(pipeline["policy_id"], pid)
        self.assertEqual(api["policy_id"], pid)
        # 개별 조건·조건 그룹 행: 필드·상태·순서·사유 원문이 세 단계에서 동일(이번 시나리오는 Pipeline 보류 대상이 아님).
        self.assertEqual(rows(pipeline["conditions"]), rows(checker["conditions"]))
        self.assertEqual(rows(api["conditions"]), rows(pipeline["conditions"]))
        self.assertEqual([c["reason"] for c in api["conditions"]], [c["reason"] for c in checker["conditions"]])
        # 종합 판정.
        for stage in (checker, pipeline, api):
            self.assertEqual(overall(stage["conditions"]), expected_overall)
        # 제외 사유(#78): Pipeline=API, 종합 ineligible이 아니면 반드시 null.
        self.assertEqual(api.get("exclusion_reasons"), pipeline.get("exclusion_reasons"))
        if expected_overall != "ineligible":
            self.assertIsNone(api.get("exclusion_reasons"))
            self.assertIsNone(pipeline.get("exclusion_reasons"))
        else:
            self.assertEqual(api.get("exclusion_reasons"), checker.get("exclusion_reasons"))
        # 보류 안내: needs_check 행이 하나라도 있으면 공통 보류 안내가 붙는다.
        if any(c["status"] == "needs_check" for c in api["conditions"]):
            self.assertEqual((trace.body.get("notice") or {}).get("text"), HOLD_NOTICE_TEXT)
        # 인용: 응답 citations 문서는 Generator가 받은 contexts 문서에 속한다(정책 간 혼용 방지는 별도 시나리오).
        context_docs = {c["document_id"] for c in trace.contexts}
        self.assertTrue({c["document_id"] for c in trace.body["citations"]} <= context_docs)
        return checker, pipeline, api


# ---------------------------------------------------------------------------
# 시나리오 1: 필수 AND 조건의 명확한 불충족 / 명확한 충족 양성 사례
# ---------------------------------------------------------------------------
class RequiredAndTests(ConsistencyBase):
    def test_f1_required_and_clear_failure(self):
        trace = self.ask([required_and()], age_value=45)
        checker, _, api = self.assert_stage_consistent(trace, "POL_F122_AND", "ineligible")
        self.assertEqual(rows(api["conditions"]), [("age", "ineligible"), ("eligibility", "ineligible")])
        self.assertIn("포함되지 않습니다", api["conditions"][0]["reason"])
        # NOT 제외 조건이 없으므로 확정 제외 사유도 없다(null ≠ 지원 가능).
        self.assertIsNone(checker["exclusion_reasons"])
        self.assertIsNone(api["exclusion_reasons"])

    def test_f1_required_and_clear_success_is_not_hold(self):
        trace = self.ask([required_and()], age_value=30)
        _, _, api = self.assert_stage_consistent(trace, "POL_F122_AND", "eligible")
        self.assertIsNone(api["exclusion_reasons"])
        # needs_check 행이 없으므로 판정 보류 안내는 붙지 않는다(접수 unknown 안내는 #103 별도 규칙).
        self.assertNotEqual((trace.body.get("notice") or {}).get("text"), HOLD_NOTICE_TEXT)


# ---------------------------------------------------------------------------
# 시나리오 2: 선택적 OR 경로 불충족만으로 종합 ineligible을 확정하지 않는다
# ---------------------------------------------------------------------------
class OptionalOrTests(ConsistencyBase):
    def test_f2_optional_or_leaf_failure_keeps_needs_check(self):
        trace = self.ask([optional_or_partial()], age_value=30)
        _, _, api = self.assert_stage_consistent(trace, "POL_F122_ORP", "needs_check")
        # OR 하위 '만 19~24세' 행 자체는 불충족으로 보존되지만 종합은 보류다.
        self.assertIn(("age", "ineligible"), rows(api["conditions"]))
        self.assertIsNone(api["exclusion_reasons"])

    def test_f2_income_unknown_with_fully_failed_optional_or_is_not_preserved(self):
        # OR 두 경로가 모두 불충족이라 원래 루트는 ineligible이지만, 확정 근거가 필수 AND 직접 조건이 아니다.
        # #40 예외는 필수 AND 경로의 직접 조건 불충족만 보존하므로 소득 UNKNOWN과 함께면 종합 needs_check여야 한다.
        trace = self.ask([income_unknown_optional_or_fail()], age_value=30)
        _, _, api = self.assert_stage_consistent(trace, "POL_F122_UOR", "needs_check")
        self.assertIn(("income", "needs_check"), rows(api["conditions"]))
        self.assertIsNone(api["exclusion_reasons"])
        # 대조: 같은 OR 불충족이 직접 Checker에서 ineligible 행으로는 남아 있다(종합으로 승격되지 않음).
        self.assertEqual(sum(1 for f, s in rows(api["conditions"]) if f == "age" and s == "ineligible"), 2)


# ---------------------------------------------------------------------------
# 시나리오 3: 소득 UNKNOWN + 명확한 나이 불충족(필수 AND) 혼합
# ---------------------------------------------------------------------------
class IncomeUnknownTests(ConsistencyBase):
    def test_f3_income_unknown_and_required_age_failure_preserves_ineligible(self):
        trace = self.ask([income_unknown_required_age()], age_value=45)
        checker, pipeline, api = self.assert_stage_consistent(trace, "POL_F122_UAG", "ineligible")
        # 소득 자체는 needs_check로 남는다(UNKNOWN 보류 유지).
        self.assertIn(("income", "needs_check"), rows(api["conditions"]))
        self.assertIn(("age", "ineligible"), rows(api["conditions"]))
        self.assertIsNone(api["exclusion_reasons"])

    def test_f3_income_unknown_with_age_satisfied_is_needs_check(self):
        trace = self.ask([income_unknown_required_age()], age_value=30)
        _, _, api = self.assert_stage_consistent(trace, "POL_F122_UAG", "needs_check")
        self.assertIsNone(api["exclusion_reasons"])


# ---------------------------------------------------------------------------
# 시나리오 4: 전체 데이터 품질 보류 → 확정 판정·제외 사유 금지
# ---------------------------------------------------------------------------
class GlobalHoldTests(ConsistencyBase):
    def test_f4_global_hold_blocks_confirmed_exclusion(self):
        control = self.ask([not_exclusion(lower=40)], age_value=45)
        self.assert_stage_consistent(control, "POL_F122_NOT", "ineligible")
        self.assertTrue(control.api["POL_F122_NOT"]["exclusion_reasons"])  # 대조군: 보류가 없으면 확정 제외

        trace = self.ask([not_exclusion(GLOBAL_HOLD, lower=40)], age_value=45)
        _, _, api = self.assert_stage_consistent(trace, "POL_F122_NOT", "needs_check")
        self.assertNotIn("ineligible", {s for _, s in rows(api["conditions"])})
        self.assertNotIn("eligible", {s for _, s in rows(api["conditions"])})
        self.assertIsNone(api["exclusion_reasons"])

    def test_f4_income_unknown_plus_global_warning_disables_income_exception(self):
        trace = self.ask([income_unknown_required_age(SCOPED_PLUS_GLOBAL)], age_value=45)
        _, _, api = self.assert_stage_consistent(trace, "POL_F122_UAG", "needs_check")
        self.assertNotIn("ineligible", {s for _, s in rows(api["conditions"])})
        self.assertIsNone(api["exclusion_reasons"])

    def test_f4_pipeline_does_not_trust_serialized_ineligible_under_global_hold(self):
        # Checker 결과는 Pipeline 경계에서 스키마만 검증된 값이다. 보류 정책에 확정 결과가 들어오면 Pipeline이 낮춘다.
        unheld = not_exclusion(lower=40)
        held = not_exclusion(GLOBAL_HOLD, lower=40)
        forged = check_eligibility({"age": 45, "region": None, "income": None, "child_age_months": None},
                                   copy.deepcopy(unheld))
        self.assertTrue(forged["exclusion_reasons"])
        trace = self.ask([held], age_value=45, check_fn=lambda profile, item: copy.deepcopy(forged))
        self.assertEqual(trace.http, 200)
        pipeline, api = trace.pipeline["POL_F122_NOT"], trace.api["POL_F122_NOT"]
        self.assertEqual(overall(trace.checker["POL_F122_NOT"]["conditions"]), "ineligible")
        self.assertEqual(overall(pipeline["conditions"]), "needs_check")
        self.assertEqual(overall(api["conditions"]), "needs_check")
        self.assertIsNone(pipeline["exclusion_reasons"])
        self.assertIsNone(api["exclusion_reasons"])
        self.assertEqual(rows(api["conditions"]), rows(pipeline["conditions"]))


# ---------------------------------------------------------------------------
# 시나리오 5·6: NOT 제외 조건 / 종합 needs_check + exclusion_reasons=null
# ---------------------------------------------------------------------------
class NotExclusionTests(ConsistencyBase):
    def test_f5_not_exclusion_reason_is_delivered_identically(self):
        trace = self.ask([not_exclusion()], age_value=80)
        checker, pipeline, api = self.assert_stage_consistent(trace, "POL_F122_NOT", "ineligible")
        self.assertEqual(len(api["exclusion_reasons"]), 1)
        self.assertEqual(api["exclusion_reasons"], checker["exclusion_reasons"])
        self.assertEqual(api["exclusion_reasons"], pipeline["exclusion_reasons"])
        self.assertEqual(api["exclusion_reasons"][0]["field"], "age")
        self.assertIn("지원 제외 기준", api["exclusion_reasons"][0]["reason"])

    def test_f5_not_exclusion_not_matched_is_eligible_without_reasons(self):
        trace = self.ask([not_exclusion()], age_value=30)
        _, _, api = self.assert_stage_consistent(trace, "POL_F122_NOT", "eligible")
        self.assertIsNone(api["exclusion_reasons"])

    def test_f6_needs_check_keeps_exclusion_reasons_null(self):
        trace = self.ask([not_with_unknown_leaf()], age_value=30)
        _, _, api = self.assert_stage_consistent(trace, "POL_F122_NUK", "needs_check")
        self.assertIsNone(api["exclusion_reasons"])
        self.assertIn("exclusion_reasons", api)  # 키는 유지되고 값만 null(지원 가능 의미 아님)


# ---------------------------------------------------------------------------
# 시나리오 7·8: 여러 정책 동시 검색 / 정책 간 Citation 혼용 방지
# ---------------------------------------------------------------------------
class MultiPolicyTests(ConsistencyBase):
    def policies(self):
        return [
            with_rank(not_exclusion(), 1),                                         # 45세 → eligible
            with_rank(required_and(), 2),                                          # 45세 → ineligible(제외 사유 없음)
            with_rank(income_unknown_required_age(), 3),                           # 45세 → ineligible(#40 보존)
            with_rank(not_with_unknown_leaf(), 4),                                 # 45세 → needs_check
            with_rank(not_exclusion(lower=40, pid="POL_F122_EXC", name="가상사십제외정책"), 5),  # 45세 → 제외 확정
        ]

    EXPECTED = {
        "POL_F122_NOT": "eligible", "POL_F122_AND": "ineligible", "POL_F122_UAG": "ineligible",
        "POL_F122_NUK": "needs_check", "POL_F122_EXC": "ineligible",
    }

    def test_f7_each_policy_keeps_its_own_verdict_and_exclusion(self):
        trace = self.ask(self.policies(), age_value=45, question="가상 정책들 중 신청할 수 있는 것 알려줘")
        self.assertEqual(set(trace.api), set(self.EXPECTED))
        self.assertEqual(set(trace.pipeline), set(self.EXPECTED))
        for pid, expected in self.EXPECTED.items():
            with self.subTest(policy=pid):
                self.assert_stage_consistent(trace, pid, expected)
        with_reasons = {pid for pid, check in trace.api.items() if check["exclusion_reasons"]}
        self.assertEqual(with_reasons, {"POL_F122_EXC"})
        # 다른 정책의 제외 사유가 섞이지 않는다(문구에 해당 정책의 기준 40세만 등장).
        reason = trace.api["POL_F122_EXC"]["exclusion_reasons"][0]["reason"]
        self.assertIn("만 40세 이상", reason)
        self.assertNotIn("75", reason)
        self.assertEqual(trace.api["POL_F122_EXC"]["exclusion_reasons"], trace.checker["POL_F122_EXC"]["exclusion_reasons"])

    def test_f8_citations_and_contacts_stay_within_their_policy(self):
        def reply(contexts):
            numbers = [c["source_id"] for c in contexts]
            text = " ".join(f"{c['policy_id']} 원문 요약 [{c['source_id']}]." for c in contexts)
            return answer(text, ids=numbers)

        trace = self.ask(self.policies(), age_value=45, reply=reply)
        self.assertEqual(trace.http, 200, trace.body)
        doc_policy = {c["document_id"]: c["policy_id"] for c in trace.contexts}
        cited = CITATION.findall(trace.body["answer"])
        self.assertEqual(len(cited), len(trace.body["citations"]))
        # 재번호 후 [n]이 가리키는 citation 문서가, 같은 문장에서 언급한 정책 문서와 일치한다.
        pairs = re.findall(r"(POL_F122_[A-Z]+) 원문 요약 \[(\d+)\]", trace.body["answer"])
        self.assertEqual({pid for pid, _ in pairs}, set(self.EXPECTED))
        for pid, number in pairs:
            self.assertEqual(doc_policy[trace.body["citations"][int(number) - 1]["document_id"]], pid)
        for pid, check in trace.api.items():
            self.assertEqual(check["source_url"], f"https://example.org/{pid}")
            for contact in check.get("contacts") or []:
                if contact.get("document_id"):
                    self.assertEqual(doc_policy[contact["document_id"]], pid)

    def test_f8_claim_with_other_policys_citation_uses_named_policy_verdict(self):
        # 본문은 '가상필수나이정책'(45세 ineligible)을 지칭하면서 eligible 정책(가상제외조건정책)의 출처를 붙였다.
        def reply(contexts):
            other = next(c["source_id"] for c in contexts if c["policy_id"] == "POL_F122_NOT")
            return answer(f"가상필수나이정책은 신청 자격이 있습니다 [{other}]", ids=[other])

        trace = self.ask(self.policies(), age_value=45, reply=reply)
        self.assertEqual(trace.http, 200, trace.body)
        self.assertNotIn("신청 자격이 있습니다", trace.body["answer"])
        self.assertIn("가상필수나이정책", trace.body["answer"])
        self.assertIn(INELIGIBLE_NOTICE, trace.body["answer"])
        self.assertIsNone(semantic_mismatch("ineligible", trace.body["answer"]))


# ---------------------------------------------------------------------------
# 시나리오 9·10: Checker 확정 판정과 LLM 안내 일치 / 근거 부족 조건의 보수적 안내
# ---------------------------------------------------------------------------
class AnswerSemanticTests(ConsistencyBase):
    def test_f9_consistent_ineligible_answer_is_kept(self):
        text = "입력하신 만 45세는 만 19~39세 기준에 포함되지 않아 신청 대상이 아닙니다 [1]"
        trace = self.ask([required_and()], age_value=45, reply=answer(text))
        self.assert_stage_consistent(trace, "POL_F122_AND", "ineligible")
        self.assertIsNone(semantic_mismatch("ineligible", trace.body["answer"]))

    def test_f9_eligible_claim_on_ineligible_policy_is_corrected(self):
        trace = self.ask([required_and()], age_value=45, reply=answer("신청 자격이 있습니다 [1]"))
        self.assert_stage_consistent(trace, "POL_F122_AND", "ineligible")
        self.assertIn(INELIGIBLE_NOTICE, trace.body["answer"])
        self.assertIsNone(semantic_mismatch("ineligible", trace.body["answer"]))

    @unittest.expectedFailure
    def test_f9_known_gap_ineligible_must_not_be_presented_as_undetermined(self):
        """알려진 결함(PR #148 GOV042/45세와 같은 유형). 기대값은 느슨하게 바꾸지 않는다.

        Checker·Pipeline·API 종합 ineligible(소득 UNKNOWN + 필수 AND 나이 불충족)인데 LLM이
        '전체 신청 자격은 확정할 수 없다'고 안내하면 현재 Pipeline은 유보 표현이라 고치지 않는다.
        서비스 수정 후 이 테스트가 통과하면 expectedFailure를 제거한다.
        """
        text = ("입력하신 만 45세는 만 19~39세 기준에 포함되지 않습니다 [1]. "
                "다만 소득 기준은 추가 확인이 필요해 전체 신청 자격은 확정할 수 없습니다 [1]")
        trace = self.ask([income_unknown_required_age()], age_value=45, reply=answer(text))
        self.assert_stage_consistent(trace, "POL_F122_UAG", "ineligible")
        self.assertIsNone(semantic_mismatch("ineligible", trace.body["answer"]))

    def test_f10_weak_basis_condition_is_needs_check_and_eligible_claim_corrected(self):
        trace = self.ask([weak_basis()], age_value=30, reply=answer("신청 자격이 있습니다 [1]"))
        _, _, api = self.assert_stage_consistent(trace, "POL_F122_WEAK", "needs_check")
        self.assertEqual(api["conditions"][0]["status"], "needs_check")
        self.assertIn("원문 확인", api["conditions"][0]["reason"])
        self.assertIn(ELIGIBILITY_NOTICE, trace.body["answer"])
        self.assertIsNone(semantic_mismatch("needs_check", trace.body["answer"]))

    def test_f10_oracle_flags_mismatch_types(self):
        # 오라클 자체 검사: 조건 설명(원문 기준 안내)은 자격 판정으로 분류하지 않는다.
        self.assertIsNone(semantic_mismatch("ineligible", "이 정책은 만 19~39세 청년이 대상입니다 [1]"))
        self.assertEqual(semantic_mismatch("ineligible", "전체 신청 자격은 확정할 수 없습니다 [1]"),
                         "ineligible_presented_as_undetermined")
        self.assertEqual(semantic_mismatch("needs_check", "신청 자격이 있습니다 [1]"),
                         "needs_check_presented_as_eligible")
        self.assertIsNone(semantic_mismatch("eligible", "전체 신청 자격은 공식 공고에서 확인이 필요해요."))


# ---------------------------------------------------------------------------
# [R] 릴리스 파일 재생(실제 조건 트리·품질 플래그, DB·LLM 미사용)
# ---------------------------------------------------------------------------
@unittest.skipUnless((GRAPH / "nodes_policy.jsonl").exists(), "v1.4.0 후보 릴리스 파일이 필요합니다.")
class ReleaseReplayTests(ConsistencyBase):
    """릴리스 파일의 정적 품질 플래그만 반영한다. 실제 검색이 붙이는 동적 경고는 없다.

    실제 R3D 검색(2026-11-27)에서는 POL_GOV_024·POL_GOV_030에 SEARCH_REGION_REQUIRES_REVIEW·
    SUSPECTED_REGION_GRANULARITY_LOST(affects=region)가 붙어 종합이 needs_check가 된다.
    따라서 아래 ineligible·eligible 기대값은 '정적 데이터 기준 전달 일관성'이며 실제 서비스 응답의 종합값이 아니다.
    """
    def test_r1_gov024_required_and_failure_and_success(self):
        # POL_GOV_024: 루트 AND [AGE 19~39], 품질 보류 없음.
        item = release_policy("POL_GOV_024")
        self.assertFalse(item["data_quality"].get("hold_auto_eligibility"))
        failed = self.ask([item], age_value=45)
        self.assert_stage_consistent(failed, "POL_GOV_024", "ineligible")
        self.assertIsNone(failed.api["POL_GOV_024"]["exclusion_reasons"])
        passed = self.ask([item], age_value=30)
        self.assert_stage_consistent(passed, "POL_GOV_024", "eligible")

    def test_r2_bok004_global_quality_hold(self):
        # POL_BOK_004: 루트 AND [AGE 15~40], affected_attributes에 all → 45세여도 확정 불충족 금지.
        item = release_policy("POL_BOK_004")
        self.assertIn("all", item["data_quality"].get("affected_attributes") or [])
        trace = self.ask([item], age_value=45)
        _, _, api = self.assert_stage_consistent(trace, "POL_BOK_004", "needs_check")
        self.assertNotIn("ineligible", {s for _, s in rows(api["conditions"])})
        self.assertIsNone(api["exclusion_reasons"])

    def test_r3_gov030_or_income_is_hold_but_required_age_failure_is_ineligible(self):
        # POL_GOV_030: AND [AGE 9~24] + OR [INCOME…], 품질 보류 없음.
        item = release_policy("POL_GOV_030")
        self.assertFalse(item["data_quality"].get("hold_auto_eligibility"))
        hold = self.ask([item], age_value=20)
        self.assert_stage_consistent(hold, "POL_GOV_030", "needs_check")
        failed = self.ask([item], age_value=30)
        self.assert_stage_consistent(failed, "POL_GOV_030", "ineligible")
        self.assertIsNone(failed.api["POL_GOV_030"]["exclusion_reasons"])

    def test_r4_multiple_release_policies_keep_separate_verdicts(self):
        expected = {"POL_GOV_024": "ineligible", "POL_GOV_042": "ineligible",
                    "POL_BOK_004": "needs_check", "POL_GOV_030": "ineligible"}
        items = [release_policy(pid, rank) for rank, pid in enumerate(expected, 1)]
        trace = self.ask(items, age_value=45, question="여러 정책 자격 알려줘")
        self.assertEqual(set(trace.api), set(expected))
        for pid, status in expected.items():
            with self.subTest(policy=pid):
                self.assert_stage_consistent(trace, pid, status)
        self.assertEqual({pid for pid, c in trace.api.items() if c["exclusion_reasons"]}, set())


if __name__ == "__main__":
    unittest.main()
