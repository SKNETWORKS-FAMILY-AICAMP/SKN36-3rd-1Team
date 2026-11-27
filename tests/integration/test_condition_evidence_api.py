"""sample_only six-source contract through the actual Generator and FastAPI."""
from copy import deepcopy
import json
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.api.schemas import AskRequest
from backend.llm import generator
from backend.rag import pipeline
from backend.rag.context_builder import rank_citable_contexts, select_contexts


def sample_policy(policy_id: str) -> dict:
    document_id = f"DOC_{policy_id}"
    condition = {
        "type": "AGE", "operator": "BETWEEN", "value": "65~120",
        "name": "연령 조건", "relationship": {"evidence_document_id": document_id},
    }
    return {
        "policy_id": policy_id,
        "policy_name": "가상 정책",
        "best_rank": 1,
        "graph_status": "FOUND",
        "data_quality": {},
        "graph": {
            "policy": {"application_period": "상시신청"},
            "eligibility": {"condition_tree": [
                {"logic": "NOT", "conditions": [condition], "groups": []},
            ]},
        },
        "evidence_document_check": {document_id: "PASS"},
        "evidence": [
            {
                "rank": rank, "chunk_id": f"{policy_id}_C{rank}", "document_id": document_id,
                "text": "가상 신청 절차", "source_url": "https://example.org/sample",
            }
            for rank in range(1, 7)
        ],
    }


def policy():
    result = sample_policy("POL_SAMPLE_A")
    result["data_quality"] = {"hold_auto_eligibility": True, "affected_attributes": ["employment"],
                              "warnings": [{"code": "SAMPLE_REVIEW", "affects": ["employment"],
                                            "blocks_auto_eligibility": True, "message": "가상 검수 보류"}]}
    for i, hit in enumerate(result["evidence"], 1):
        hit["source_url"] = f"https://example.org/sample/{i}"
    result["evidence"][-1]["text"] = "[지원대상]\n지원 제외 대상: 만 65세 이상\n[구비서류]\n가상 신청서\n[문의처]\n가상 담당부서 02-123-4567"
    return result


def ask(item, text, baseline=False, confirmed=False):
    session = Mock()
    session.search_policy_context.side_effect = lambda *a, **kw: {"policies": [deepcopy(item)]}
    messages = []
    def sdk(value):
        messages.extend(value)
        return json.dumps({"status": "answered", "answer": text,
                           "used_source_ids": [1, 6] if "[6]" in text else [1], "follow_up_questions": []})
    def check(profile, policy):
        return {"policy_id": policy["policy_id"], "conditions": [
            {"field": "age", "label": "나이", "status": "eligible", "reason": "가상 범위 비교"},
            {"field": "eligibility", "label": "자격 요건", "status": "ineligible" if confirmed else "needs_check", "reason": "가상 종합 판정"}],
            "reception_status": "open", "exclusion_reasons": [
                {"field": "age", "label": "나이", "reason": "가상 확정 제외 사유"}] if confirmed else None}
    original = pipeline.complete_condition_evidence
    with patch.object(pipeline, "complete_condition_evidence", side_effect=(lambda selected, *a, **kw: selected) if baseline else original), \
            patch.object(generator, "_call_openai", side_effect=sdk), \
            TestClient(create_app(session_factory=lambda: session, check_fn=check, generate_fn=generator.generate_answer)) as client:
        response = client.post("/api/ask", json={"question": "나이 기준은?", "selected_policy_id": item["policy_id"],
                                               "profile": {"age": 70, "region": "", "income": None}, "history": []})
    assert response.status_code == 200, response.text
    return response.json(), json.loads(messages[1]["content"].split("\n", 1)[1])


def test_sixth_source_generator_validation_citations_and_derived_fields():
    item = policy();before = deepcopy(item)
    baseline, base_prompt = ask(item, "검증 근거 [1]", baseline=True)
    result, prompt = ask(item, "추가 원문 [6], 기존 원문 [1]")
    assert prompt["official_evidence"][:5] == base_prompt["official_evidence"]
    assert prompt["official_evidence"][5]["source_id"] == 6
    assert result["answer"] == "추가 원문 [2], 기존 원문 [1]"
    assert [c["url"] for c in result["citations"]] == ["https://example.org/sample/1", "https://example.org/sample/6"]
    for field in ("conditions", "exclusion_reasons", "reception_status", "application_schedule"):
        assert result["policy_checks"][0][field] == baseline["policy_checks"][0][field]
    assert result["policy_checks"][0]["required_documents"][0]["chunk_id"] == "POL_SAMPLE_A_C6"
    assert result["policy_checks"][0]["requirement_excerpts"][0]["chunk_id"] == "POL_SAMPLE_A_C6"
    assert result["policy_checks"][0]["contacts"][0]["chunk_id"] == "POL_SAMPLE_A_C6"
    assert result["policy_checks"][0]["official_requirements"] == baseline["policy_checks"][0]["official_requirements"]
    assert item == before  # Includes quality flags, source content and condition tree.


def test_not_citing_extra_preserves_answer_and_existing_citation():
    item = policy()
    old, _ = ask(item, "원문 설명 [1]", baseline=True)
    new, prompt = ask(item, "원문 설명 [1]")
    assert len(prompt["official_evidence"]) == 6
    assert new["answer"] == old["answer"] and new["citations"] == old["citations"]


def test_document_failure_keeps_entire_previous_response():
    item = policy();item["evidence_document_check"] = {"DOC_POL_SAMPLE_A": "MISMATCH"}
    old, _ = ask(item, "원문 설명 [1]", baseline=True)
    new, prompt = ask(item, "원문 설명 [1]")
    assert len(prompt["official_evidence"]) == 5
    assert new == old


def test_confirmed_exclusion_is_preserved_with_added_evidence():
    item = policy();item["data_quality"] = {}
    old, _ = ask(item, "원문 설명 [1]", baseline=True, confirmed=True)
    new, prompt = ask(item, "원문 설명 [1]", confirmed=True)
    assert len(prompt["official_evidence"]) == 6
    assert new["policy_checks"][0]["exclusion_reasons"] == old["policy_checks"][0]["exclusion_reasons"]
    assert new["policy_checks"][0]["exclusion_reasons"] is not None
    assert new["policy_checks"][0]["conditions"] == old["policy_checks"][0]["conditions"]


def test_review_closed_priority_and_named_question_exception():
    closed = sample_policy("POL_SAMPLE_CLOSED")
    closed["policy_name"] = "가상 마감 정책"
    opened = sample_policy("POL_SAMPLE_OPEN")
    opened["policy_name"] = "가상 접수 정책"
    for item in (closed, opened):
        item["evidence"][-1]["text"] = "지원 제외 대상: 65세 이상"
    # Open has no matching age candidate: all of its candidates fit before the
    # closed policy. With an eligible open candidate, closed is not selected.
    opened["evidence"] = opened["evidence"][:3]
    for i, hit in enumerate(opened["evidence"], 7):
        hit["rank"] = i
    opened["best_rank"] = 7
    original = deepcopy((closed, opened))
    def check(profile, item):
        return {"policy_id": item["policy_id"], "conditions": [
            {"field": "eligibility", "label": "자격 요건", "status": "needs_check", "reason": "가상 검수"}],
            "reception_status": "closed" if item["policy_id"] == closed["policy_id"] else "open",
            "exclusion_reasons": None}
    for items, question, deprioritized in (
        ([closed, opened], "나이 기준은?", {closed["policy_id"]}),
        ([closed, opened], "가상 마감 정책의 나이 기준은?", set()),
        ([closed], "나이 기준은?", {closed["policy_id"]}),
    ):
        captured = []
        def generate(question, contexts, checks, history, profile=None):
            captured.extend(deepcopy(contexts))
            return {"status": "answered", "answer": "검증 원문 [1]", "used_source_ids": [1], "follow_up_questions": []}
        expected = select_contexts(rank_citable_contexts(items), deprioritized_policy_ids=deprioritized)
        response = pipeline.run_ask_pipeline(
            AskRequest.model_validate({"question": question, "profile": {"age": 70, "region": "", "income": None}, "history": []}),
            search_fn=lambda *a, **kw: {"policies": deepcopy(items)}, check_fn=check, generate_fn=generate,
        )
        assert captured[:5] == expected
        assert len(captured) == 6
        assert captured[-1]["chunk_id"] == "POL_SAMPLE_CLOSED_C6"
        assert captured[0]["policy_id"] == (opened["policy_id"] if deprioritized and len(items) > 1 else closed["policy_id"])
        assert next(c for c in response.policy_checks if c.policy_id == closed["policy_id"]).reception_status == "closed"
    assert (closed, opened) == original
