"""Replay the five RQ031 answers against the selected release evidence."""

import json
from pathlib import Path

import pytest

from backend.llm import generator
from backend.llm.fishing_days_guard import ground_fishing_days


ROOT = Path(__file__).resolve().parents[2]
POLICY = "POL_GOV_006"
NOTICE = "DOC_GOV_006_MOF_NOTICE_2024"
SUMMARY = "DOC_GOV_006"


@pytest.fixture(scope="module")
def recorded_cases():
    report = ROOT / "docs/reports/issue97-rq031-r3d-retest-responses-20261125.json"
    return {case["id"]: case for case in json.loads(report.read_text(encoding="utf-8"))["runs"]}


@pytest.fixture(scope="module")
def contexts():
    release = ROOT / "data/releases/v1.4.0-candidate/qdrant/payload_chunks.jsonl"
    with release.open(encoding="utf-8") as rows:
        chunks = {
            chunk["document_id"]: chunk for line in rows
            if (chunk := json.loads(line))["chunk_id"] in {
                "DOC_GOV_006_C03", "DOC_GOV_006_MOF_NOTICE_2024_P01_C01",
            }
        }
    return [{**chunks[doc], "source_id": index} for index, doc in enumerate((NOTICE, SUMMARY), 1)]


@pytest.mark.parametrize(("case_id", "days", "meets_minimum"), [
    ("RQ031", None, None),
    ("RQ031_EXACT60", 60, True),
    ("BOUNDARY_59", 59, False),
    ("BOUNDARY_60", 60, True),
    ("BOUNDARY_61", 61, True),
])
def test_rq031_saved_answers(monkeypatch, recorded_cases, contexts, case_id, days, meets_minimum):
    case = recorded_cases[case_id]
    saved = case["response"]
    # The recorder saved final citations. Rebind them to the two policy
    # documents in this replay without changing the stored answer wording.
    ids = {citation["document_id"]: index for index, citation in enumerate(contexts, 1)}
    recorded_ids = {
        str(index): ids[citation["document_id"]]
        for index, citation in enumerate(saved["citations"], 1)
    }
    answer = generator.CITATION_PATTERN.sub(lambda m: f"[{recorded_ids[m[1]]}]", saved["answer"])
    payload = {"status": "answered", "answer": answer,
               "used_source_ids": sorted(set(recorded_ids.values())), "follow_up_questions": []}
    monkeypatch.setattr(generator, "_call_openai", lambda _: json.dumps(payload, ensure_ascii=False))
    checks = [{"policy_id": POLICY, "conditions": [{"field": "eligibility", "status": "needs_check"}]}]

    result = generator.generate_answer(case["request"]["question"], contexts, checks)

    assert result["status"] == "answered"
    assert "60일 이상" in result["answer"]
    assert "30일 이상" in result["answer"]
    assert checks[0]["conditions"][0]["status"] == "needs_check"
    if days is None:
        assert 1 in result["used_source_ids"]
        return
    expected = "충족합니다" if meets_minimum else "충족하지 않습니다"
    assert result["answer"].startswith(f"{days}일은 최소 조업일수 요건을 {expected}")
    membership = "포함됩니다" if days <= 60 else "포함되지 않습니다"
    assert f"‘60일 이하’에는 {days}일이 {membership} [2]." in result["answer"]
    assert "실제 지급제한과 최종 지급 자격은 별도 확인이 필요합니다" in result["answer"]
    assert result["used_source_ids"] == [1, 2]


@pytest.mark.parametrize(("question", "evidence", "expected"), [
    ("조업일수가 59일입니다. 최소 조업일수 기준을 충족하나요?", "all", "일반 기준에 미달하지만, TAC 예외가 적용되면"),
    ("조업일수가 30일이고 TAC 적용 어종을 주로 어획하는 어선입니다. 기준을 충족하나요?", "all", "30일은 최소 조업일수 요건을 충족합니다"),
    ("조업일수가 29일입니다. 기준을 충족하나요?", "all", "29일은 최소 조업일수 요건을 충족하지 않습니다"),
    ("TAC 적용 어종을 주로 어획하는 어선이 아닙니다. 조업일수가 59일이면 충족하나요?", "all", "59일은 최소 조업일수 요건을 충족하지 않습니다"),
    ("조업일수가 60일이면 기준을 충족하나요?", "summary_only", None),
    ("조업일수가 60일이면 기준을 충족하나요?", "other_policy", None),
    ("조업일수가 59일 또는 61일이면 어떻게 되나요?", "all", None),
    ("조업일수가 59 ~ 61일 사이면 어떻게 되나요?", "all", None),
    ("11월 27일 기준으로 최소 조업일수는 며칠인가요?", "all", "해양수산부 고시의 최소 조업일수는 해당 연도 60일 이상입니다"),
    ("조업일수가 59일인데 신청 서류를 알려주세요.", "all", None),
    ("조업일수가 60일 이상이라고 적혀 있나요?", "all", None),
])
def test_fishing_days_guard_respects_evidence_and_question_scope(contexts, question, evidence, expected):
    selected = contexts
    if evidence == "summary_only":
        selected = contexts[1:]
    elif evidence == "other_policy":
        selected = [*contexts, {**contexts[0], "policy_id": "SAMPLE_OTHER", "source_id": 3}]
    raw = {"status": "answered", "answer": "기존 답변 [2].", "used_source_ids": [2]}

    result = ground_fishing_days(raw, question, selected)

    if expected is None:
        assert result == raw
    else:
        assert expected in result["answer"]
