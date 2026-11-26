"""Replay recorded R3D evidence and real LLM output without network or new LLM calls."""

from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.rag.application_links import align_application_links

FIXTURE = Path(__file__).parents[1] / "fixtures/issue103-r3d-mixed-url-response.json"


class ActualMixedResponseReplayTests(unittest.TestCase):
    def test_unknown_schedule_wording_for_four_loan_policies(self):
        from test_issue103_guidance import release_policy

        for pid in ("POL_003", "POL_004", "POL_006", "POL_007"):
            session = Mock()
            session.search_policy_context.side_effect = lambda *a, policy=release_policy(pid), **kw: {"policies": [deepcopy(policy)]}
            for text in ("상시 신청 가능합니다 [1].", "언제든지 신청하실 수 있습니다 [1].", "연중 모집합니다 [1]."):
                with self.subTest(policy=pid, claim=text):
                    generated = {"status": "answered", "answer": text, "used_source_ids": [1], "follow_up_questions": []}
                    with TestClient(create_app(session_factory=lambda: session, generate_fn=Mock(return_value=generated))) as client:
                        response = client.post("/api/ask", json={"question": "현재 신청 기간은?", "selected_policy_id": pid,
                                                                 "profile": {"age": 27, "region": "", "income": None}, "history": []})
                    self.assertEqual(response.status_code, 200)
                    body = response.json()
                    self.assertEqual(body["policy_checks"][0]["reception_status"], "unknown")
                    self.assertNotEqual(body["answer"], text)
                    self.assertIn("공식 공고", body["answer"])

    def test_real_mixed_policy_response_remains_http_200(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        session = Mock()
        session.search_policy_context.side_effect = lambda *a, **kw: deepcopy(data["search_result"])
        generate = Mock(return_value=deepcopy(data["generator_output"]))
        with TestClient(create_app(session_factory=lambda: session, generate_fn=generate)) as client:
            response = client.post("/api/ask", json=data["request"])
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        checks = {p["policy_id"]: p for p in body["policy_checks"]}
        self.assertEqual(set(checks), set(data["expected_policy_ids"]))
        self.assertIsNone(checks["POL_YC_065"]["official_apply_url"])
        self.assertTrue(checks["POL_STG_SEOUL_001"]["official_apply_url"])
        self.assertEqual(body["answer"], data["generator_output"]["answer"])
        self.assertEqual(len(body["citations"]), 3)
        self.assertTrue(all("chunk_id" not in c for c in body["citations"]))

    def test_paragraph_scope_corrects_uncited_bad_claim_without_touching_other_policy(self):
        contexts = [{"source_id": 1, "policy_id": "A"}, {"source_id": 2, "policy_id": "B"}]
        checks = [{"policy_id": "A", "policy_name": "가상위원회", "official_apply_url": None, "reception_status": "unknown"},
                  {"policy_id": "B", "policy_name": "가상수당", "official_apply_url": "https://example.org/apply", "reception_status": "closed"}]
        text = "가상위원회 안내입니다. 해당 홈페이지에서 신청할 수 있습니다. [1]\n\n가상수당 안내입니다. 신청 주소는 https://example.org/apply 입니다. [2]"
        fixed, changed = align_application_links(text, contexts, checks)
        self.assertTrue(changed)
        self.assertIn("전용 신청 주소는 없습니다", fixed)
        self.assertIn("신청 주소는 https://example.org/apply 입니다", fixed)
        self.assertIn("[1]", fixed)
        self.assertIn("[2]", fixed)

    def test_scope_does_not_cross_blank_paragraph(self):
        contexts = [{"source_id": 1, "policy_id": "A"}, {"source_id": 2, "policy_id": "B"}]
        checks = [{"policy_id": "A", "policy_name": "가상위원회", "official_apply_url": None, "reception_status": "unknown"},
                  {"policy_id": "B", "policy_name": "가상수당", "official_apply_url": "https://example.org/apply", "reception_status": "closed"}]
        from backend.rag.pipeline import AskError

        for separator in ("\n\n", "\r\n\r\n", "\n \n", "\r\n\t\r\n"):
            with self.subTest(separator=repr(separator)), self.assertRaises(AskError):
                align_application_links("가상수당" + separator + "신청 주소는 https://example.org/unknown 입니다.", contexts, checks)
