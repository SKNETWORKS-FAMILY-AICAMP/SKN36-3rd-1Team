"""Mock 검색 세션으로 API 계약과 수명 주기를 검증한다."""

from importlib.util import find_spec
import unittest
from unittest.mock import Mock

from test_rag_pipeline import answer, checker, policy

from database.hybrid_search import HybridSearchBackendError, SearchProfileError

if find_spec("fastapi") is not None and find_spec("httpx") is not None:
    from fastapi.testclient import TestClient

    from backend.api.main import create_app
else:
    TestClient = None


REQUEST = {"question": "지원 정책은?", "profile": {"age": 25, "region": "서울", "income": None}, "history": []}


@unittest.skipIf(TestClient is None, "API 검증에는 fastapi와 httpx가 필요합니다.")
class AskApiTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.search_policy_context.return_value = {"policies": [policy()]}
        self.factory = Mock(return_value=self.session)
        self.generator = Mock(return_value=answer())
        self.client = TestClient(create_app(session_factory=self.factory, check_fn=checker, generate_fn=self.generator))

    def test_swagger_cors_success_and_session_lifespan(self):
        with self.client as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok", "ready": False})
            self.assertEqual(client.get("/docs").status_code, 200)
            self.assertIn("/api/ask", client.get("/openapi.json").json()["paths"])
            origin = "http://127.0.0.1:5173"
            response = client.post("/api/ask", json=REQUEST, headers={"Origin": origin})
            self.assertEqual(response.headers["access-control-allow-origin"], origin)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "answered")
            self.assertEqual(response.json()["citations"][0]["document_id"], "DP")
            self.assertTrue(client.get("/health").json()["ready"])
            self.assertEqual(client.post("/api/ask", json=REQUEST).status_code, 200)
            self.factory.assert_called_once()
            self.assertEqual(self.session.search_policy_context.call_count, 2)
        self.session.close.assert_called_once()

    def test_invalid_inputs_are_rejected_without_echoing_values(self):
        with self.client as client:
            profiles = [{"age": True}, {"age": 25.0}, {"age": "25"}, {"income": "10"}, {"income": True}]
            bad = [{"profile": {**REQUEST["profile"], **item}} for item in profiles]
            bad += [{"question": "  "}, {"question": "x" * 2001},
                    {"history": [{"role": "system", "text": "private-value"}]}]
            for changes in bad:
                with self.subTest(changes=changes):
                    response = client.post("/api/ask", json={**REQUEST, **changes})
                    self.assertEqual(response.status_code, 422)
                    self.assertNotIn("private-value", response.text)
            self.session.search_policy_context.assert_not_called()

    def test_search_failures_and_recovery(self):
        with self.client as client:
            for backend, kind, status, code in [
                ("qdrant", "auth", 503, "SEARCH_BACKEND_UNAVAILABLE"),
                ("neo4j", "unavailable", 503, "SEARCH_BACKEND_UNAVAILABLE"),
                ("ollama", "timeout", 504, "SEARCH_TIMEOUT"),
            ]:
                with self.subTest(backend=backend):
                    self.session.search_policy_context.side_effect = HybridSearchBackendError(
                        backend, "private-value", kind,
                    )
                    response = client.post("/api/ask", json=REQUEST)
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response.json()["detail"]["code"], code)
                    self.assertNotIn("private-value", response.text)
                    self.assertFalse(client.get("/health").json()["ready"])
            self.generator.assert_not_called()
            self.session.search_policy_context.side_effect = None
            self.session.search_policy_context.return_value = {"policies": []}
            response = client.post("/api/ask", json=REQUEST)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "no_evidence")
            self.assertFalse(client.get("/health").json()["ready"])
            self.generator.assert_not_called()
            self.session.search_policy_context.return_value = {"policies": [policy()]}
            self.assertEqual(client.post("/api/ask", json=REQUEST).status_code, 200)
            self.assertTrue(client.get("/health").json()["ready"])

    def test_startup_configuration_failure_keeps_health_live(self):
        self.factory.side_effect = SearchProfileError("private-value")
        with self.client as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok", "ready": False})
            response = client.post("/api/ask", json=REQUEST)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"]["code"], "SEARCH_BACKEND_UNAVAILABLE")
        self.session.close.assert_not_called()

    def test_llm_error_mapping(self):
        with self.client as client:
            self.generator.return_value = answer("없는 근거 [9]")
            response = client.post("/api/ask", json=REQUEST)
            self.assertEqual((response.status_code, response.json()["detail"]["code"]), (502, "LLM_OUTPUT_INVALID"))
            for exc, expected in [(TimeoutError, (504, "LLM_TIMEOUT")), (ConnectionError, (502, "LLM_UNAVAILABLE"))]:
                self.generator.side_effect = exc
                response = client.post("/api/ask", json=REQUEST)
                self.assertEqual((response.status_code, response.json()["detail"]["code"]), expected)
