"""Hybrid 검색 경계 검증. 모든 정책·문서·청크 데이터는 가상이다."""

from copy import deepcopy
from json import loads
import unittest
from unittest.mock import Mock, patch

from httpx import ConnectError, ReadTimeout

from backend.api.schemas import AskRequest
from backend.rag.context_builder import build_contexts
from backend.rag.pipeline import AskError, run_ask_pipeline
from database.hybrid_search import combine_policy_evidence
from database.region_search import describe_hit_region, plan_region_search
from test_rag_pipeline import answer, checker


def hybrid_fixture() -> dict:
    """DB 없이 가상 Graph/Qdrant 결과를 실제 검색 조립 함수에 전달한다."""
    specs = [
        ("PARTIAL", "A", 1, "서울특별시 동대문구"),
        ("PARTIAL", "B", 2, "서울특별시 동대문구"),
        ("UNKNOWN", "A", 3, "서울특별시"),
        ("MISSING", "A", 4, "전국"),
        ("PARTIAL", "A", 5, "서울특별시 동대문구"),
        ("PARTIAL", "C", 6, "서울특별시 동대문구"),
    ]
    filters = {"region": "서울특별시"}
    plan = plan_region_search(filters, [{"policy_id": pid, "region": region} for pid, _, _, region in specs])
    hits = []
    for pid, suffix, rank, region in specs:
        hits.append({
            "policy_id": pid, "policy_name": f"가상 정책 {pid}", "document_id": f"D_{pid}",
            "chunk_id": f"C_{pid}_{suffix}", "rank": rank, "score": 1 - rank / 100,
            "text": f"가상 원문 {pid} {suffix}", "source_url": f"https://example.org/{pid}" if rank != 6 else None,
            "region": region, "region_match": describe_hit_region(region, plan),
            "page_start": 2 if rank == 1 else None, "page_end": 2 if rank == 1 else None,
            "char_start": 0, "char_end": len(f"가상 원문 {pid} {suffix}"),
        })

    graphs = {}
    for pid, logic in (("PARTIAL", "AND"), ("UNKNOWN", "UNKNOWN")):
        group_id = f"G_{pid}"
        condition_id = f"COND_{pid}"
        graphs[pid] = {
            "policy_id": pid, "policy": {"policy_id": pid, "application_period": "상시신청"},
            "documents": [{"document_id": f"D_{pid}", "title": f"가상 공고 {pid}", "relationship": {}}],
            "eligibility": {
                "evaluated": False,
                "condition_tree": [{
                    "group_id": group_id, "logic": logic, "is_root": True, "description": None,
                    "relationship": {}, "conditions": [{
                        "condition_id": condition_id, "type": "AGE", "value": "19~35", "relationship": {},
                    }], "groups": [],
                }],
                "requires_condition_ids": [condition_id], "tree_condition_ids": [condition_id],
                "tree_matches_requires_condition": True, "structure_issues": [],
                "unknown_logic_groups": [group_id] if logic == "UNKNOWN" else [],
            },
        }
    with patch("database.hybrid_search.dqf.load_flags", return_value={"status": "applied", "flags_by_policy": {}}):
        return combine_policy_evidence(
            {"query": "가상 정책 질문", "filters": filters, "k": 10, "hits": hits, "region_search": plan},
            {"policies": graphs, "missing_policy_ids": ["MISSING"]},
        )


class HybridAskFixtureTests(unittest.TestCase):
    def setUp(self):
        self.result = hybrid_fixture()
        self.generate = Mock(return_value=answer())
        self.check = Mock(side_effect=checker)

    def ask(self):
        return run_ask_pipeline(
            AskRequest(question="가상 정책 질문", profile={"region": "서울", "age": 25, "income": None}, history=[]),
            search_fn=Mock(return_value=self.result), check_fn=self.check, generate_fn=self.generate,
        )

    def test_fixture_matches_hybrid_result(self):
        partial, unknown, missing = self.result["policies"]
        self.assertEqual([item["best_rank"] for item in self.result["policies"]], [1, 3, 4])
        self.assertEqual([(hit["rank"], hit["chunk_id"]) for hit in partial["evidence"]],
                         [(1, "C_PARTIAL_A"), (2, "C_PARTIAL_B"), (6, "C_PARTIAL_C")])
        self.assertEqual(partial["evidence"][0]["source_url"], "https://example.org/PARTIAL")
        region = partial["evidence"][0]["region_match"]
        self.assertEqual(region["match_basis"], "partial")
        self.assertTrue(region["review_required"])
        self.assertFalse(region["is_eligibility_decision"])
        self.assertTrue(partial["region_search"]["review_required"])
        self.assertTrue(partial["data_quality"]["hold_auto_eligibility"])
        self.assertEqual(partial["data_quality"]["affected_attributes"], ["region"])
        self.assertTrue(any(w["affects"] == ["region"] and w["blocks_auto_eligibility"]
                            for w in partial["data_quality"]["warnings"]))
        self.assertEqual(unknown["graph_status"], "FOUND")
        eligibility = unknown["graph"]["eligibility"]
        self.assertEqual(eligibility["condition_tree"][0]["logic"], "UNKNOWN")
        self.assertEqual(eligibility["unknown_logic_groups"], ["G_UNKNOWN"])
        self.assertEqual(unknown["data_quality"]["affected_attributes"], ["all"])
        self.assertEqual(missing["graph_status"], "MISSING_IN_GRAPH")
        self.assertIsNone(missing["graph"])
        self.assertEqual(self.result["missing_policy_ids"], ["MISSING"])

    def test_context_uses_ranked_unique_chunks_and_skips_missing_values(self):
        partial, _, missing = self.result["policies"]
        # 조립 이후 중복 청크가 유입되어도 Context에서 다시 제거한다.
        partial["evidence"].append({**partial["evidence"][0], "rank": 8, "text": "중복 가상 원문"})
        partial["evidence"].append({**partial["evidence"][0], "rank": 9, "chunk_id": "C_NO_TEXT", "text": None})
        original = deepcopy(self.result)
        contexts = build_contexts(self.result["policies"])
        self.assertEqual([(c["source_id"], c["chunk_id"]) for c in contexts],
                         [(1, "C_PARTIAL_A"), (2, "C_UNKNOWN_A"), (3, "C_MISSING_A"), (4, "C_PARTIAL_B")])
        self.assertEqual(contexts[0]["text"], partial["evidence"][0]["text"])
        self.assertEqual(contexts[0]["title"], "가상 공고 PARTIAL")
        self.assertEqual(contexts[0]["page_number"], 2)
        self.assertEqual(contexts[2]["title"], missing["policy_name"])
        self.assertIsNone(contexts[2]["page_number"])
        self.assertEqual(contexts[0]["quality_warnings"], [w["message"] for w in partial["data_quality"]["warnings"]])
        self.assertEqual(self.result, original)

    def test_pipeline_passes_original_graph_and_holds_only_affected_conditions(self):
        response = self.ask()
        self.assertEqual([call.args[1] for call in self.check.call_args_list], self.result["policies"])
        expected = [["eligible", "needs_check"], ["needs_check", "needs_check"], ["needs_check", "needs_check"]]
        self.assertEqual([[c.status for c in p.conditions] for p in response.policy_checks], expected)
        self.assertEqual([[c["status"] for c in p["conditions"]] for p in self.generate.call_args.args[2]], expected)
        self.assertEqual(response.notice.kind, "warn")

    def test_llm_citations_follow_body_with_hybrid_sources(self):
        self.generate.return_value = answer("가상 원문 [3], [1], [3]", ids=[2])
        with self.assertLogs("backend.rag.pipeline", level="WARNING"):
            response = self.ask()
        self.assertEqual(response.answer, "가상 원문 [2], [1], [2]")
        self.assertEqual([c.document_id for c in response.citations], ["D_PARTIAL", "D_MISSING"])
        self.generate.return_value = answer("없는 가상 원문 [5]", ids=[5])
        with self.assertRaisesRegex(AskError, "LLM_OUTPUT_INVALID"):
            self.ask()

    def test_generator_json_decode_error_is_invalid_output(self):
        self.generate.side_effect = lambda *_, **__: loads("{")
        with self.assertRaisesRegex(AskError, "LLM_OUTPUT_INVALID"):
            self.ask()

    def test_builtin_generator_timeout_has_distinct_code(self):
        self.generate.side_effect = TimeoutError("가상 지연")
        with self.assertRaisesRegex(AskError, "LLM_TIMEOUT"):
            self.ask()

    def test_unconfirmed_condition_field_is_held_without_renaming(self):
        def checker_with_alias(profile, policy):
            result = checker(profile, policy)
            result["conditions"][1]["field"] = "residence_region"
            return result

        self.check.side_effect = checker_with_alias
        age, condition = self.ask().policy_checks[0].conditions
        self.assertEqual(age.status, "eligible")
        self.assertEqual((condition.field, condition.status), ("residence_region", "needs_check"))
        llm_conditions = self.generate.call_args.args[2][0]["conditions"]
        self.assertEqual(llm_conditions[1]["status"], "needs_check")

    def test_unrecognized_affected_attribute_holds_all_conditions(self):
        self.result["policies"][0]["data_quality"]["affected_attributes"] = ["unconfirmed_scope"]
        conditions = self.ask().policy_checks[0].conditions
        self.assertEqual([condition.status for condition in conditions], ["needs_check", "needs_check"])

    def test_external_transport_exceptions_have_distinct_codes(self):
        # 예외 객체만 주입하며 HTTP 요청은 발생하지 않는다.
        for error, code in ((ReadTimeout("가상 SDK 읽기 지연"), "LLM_TIMEOUT"),
                            (ConnectError("가상 SDK 연결 실패"), "LLM_UNAVAILABLE")):
            with self.subTest(error=type(error).__name__):
                self.generate.side_effect = error
                with self.assertRaisesRegex(AskError, code):
                    self.ask()
