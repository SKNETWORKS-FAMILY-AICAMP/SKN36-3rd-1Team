"""Check final citations and qualification holds with the real release data."""

import json
from unittest.mock import Mock

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.llm import generator
from test_issue79_18_90_verification import CHUNKS, item


def test_fishing_days_explanation_keeps_the_api_eligibility_hold(monkeypatch):
    policy_id = "POL_GOV_006"
    notice = "DOC_GOV_006_MOF_NOTICE_2024"
    chunks = [row for row in CHUNKS if row["chunk_id"] in {
        "DOC_GOV_006_C03", "DOC_GOV_006_MOF_NOTICE_2024_P01_C01",
    }]
    session = Mock()
    session.search_policy_context.return_value = {"policies": [item(policy_id, chunks=chunks)]}
    bad_output = {
        "status": "answered",
        "answer": "60일은 60일 이하에 해당하지 않으므로 지급이 확정됩니다 [1].",
        "used_source_ids": [1],
        "follow_up_questions": [],
    }
    monkeypatch.setattr(generator, "_call_openai", lambda _: json.dumps(bad_output, ensure_ascii=False))
    with TestClient(create_app(session_factory=lambda: session, generate_fn=generator.generate_answer)) as client:
        response = client.post("/api/ask", json={
            "question": "수산자원보호 직접지불제 조업일수가 정확히 60일이면 요건을 충족하나요?",
            "profile": {"age": None, "region": "", "income": None},
            "history": [],
            "selected_policy_id": policy_id,
        })

    assert response.status_code == 200, response.text
    result = response.json()
    assert "60일은 최소 조업일수 요건을 충족합니다" in result["answer"]
    assert "‘60일 이하’에는 60일이 포함됩니다" in result["answer"]
    assert "지급이 확정됩니다" not in result["answer"]
    assert {entry["document_id"] for entry in result["citations"]} == {"DOC_GOV_006", notice}
    [check] = result["policy_checks"]
    assert check["conditions"][-1]["status"] == "needs_check"
    assert check["exclusion_reasons"] is None
