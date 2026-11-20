"""가상 근거로 생성 지침 전달과 계약 보존을 검사한다. 실제 모델 개선 판정은 아니다."""
import json
from copy import deepcopy

import pytest

from backend.llm import generator


@pytest.mark.parametrize(("question", "answer"), [
    ("가상 제외대상도 신청할 수 있나요?", "가상 제외대상은 지원에서 제외됩니다. [1]"),
    ("가상 제외대상은 신청할 수 없나요?", "가상 제외대상은 지원에서 제외됩니다. [1]"),
    ("제 나이로 신청할 수 있나요?", "나이 정보가 없어 연령 조건을 확인해야 합니다. [1]"),
    ("두 가상 정책에서 제외되나요?", "가상 A는 제외됩니다. 가상 B는 공식 기준이 확인되지 않습니다. [1][2]"),
    ("경계값이면 가능한가요?", "기본 기준은 확인되지만 경계의 적용은 원문 확인이 필요합니다. [1]"),
])
def test_conclusion_guidance_is_sent_and_evidence_is_preserved(monkeypatch, question, answer):
    contexts = [
        {"source_id": 1, "policy_id": "SAMPLE_A", "policy_name": "가상 A",
         "text": "가상 제외대상은 제외. 만 19세 이상.", "url": "https://example.org/a"},
        {"source_id": 2, "policy_id": "SAMPLE_B", "policy_name": "가상 B",
         "text": "가상 B의 기준은 미확인.", "url": "https://example.org/b"},
    ]
    calls = []
    def fake(messages):
        calls.append(messages)
        return json.dumps({"status": "answered", "answer": answer,
                           "used_source_ids": [1, 2] if "[2]" in answer else [1],
                           "follow_up_questions": []}, ensure_ascii=False)
    monkeypatch.setattr(generator, "_call_openai", fake)
    checks = [
        {"policy_id": "SAMPLE_A", "conditions": [{"field": "eligibility", "status": "needs_check",
          "reason": "가상 UNKNOWN 그룹의 자동 판정 보류"}], "reception_status": "unknown"},
        {"policy_id": "SAMPLE_B", "conditions": [{"field": "age", "status": "needs_check",
          "reason": "가상 입력 부족"}], "reception_status": "open"},
    ]
    before = deepcopy((contexts, checks))
    result = generator.generate_answer(question, contexts, checks)
    assert len(calls) == 1
    system = calls[0][0]["content"]
    assert "독립된 '네/아니요'로 시작하지 말고" in system
    assert "질문에서 이미 명시한 사실" in system
    assert "수치 경계·예외·적용 연도가 충돌" in system
    # #97(RQ031): 경계값은 첫 문장에서 유보하고, 이상·이하가 그 값을 포함한다는 규칙을 전달한다.
    assert "첫 문장에서 그 경계값은 원문끼리 결론이 달라 확정할 수 없다" in system
    assert "'이상'·'이하'는 그 값을 포함하고 '초과'·'미만'은 포함하지 않습니다" in system
    # 굵은 글씨는 정책 수와 관계없이 답변 전체에서 2곳 이하.
    assert "답변 전체에서 최대 2곳" in system
    payload = json.loads(calls[0][1]["content"].split("\n", 1)[1])
    assert payload["question"] == question
    assert payload["policy_checks"] == checks
    assert [item["policy_id"] for item in payload["official_evidence"]] == ["SAMPLE_A", "SAMPLE_B"]
    assert (contexts, checks) == before
    assert result["answer"] == answer
    assert contexts[0]["text"] == "가상 제외대상은 제외. 만 19세 이상."
