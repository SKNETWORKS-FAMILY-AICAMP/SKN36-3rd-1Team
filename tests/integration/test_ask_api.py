"""Mock 검색 세션으로 API 계약과 수명 주기를 검증한다."""

from importlib.util import find_spec
import unittest
from unittest.mock import Mock, patch

from test_rag_pipeline import answer, checker, policy

from database.hybrid_search import HybridSearchBackendError, SearchProfileError
from backend.llm.generator import LLMOutputError, LLMTimeoutError, LLMUnavailableError

if find_spec("fastapi") is not None and find_spec("httpx") is not None:
    from fastapi.testclient import TestClient
    from httpx import ConnectError, ReadTimeout

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

    def test_generator_defined_errors_are_publicly_mapped(self):
        with self.client as client:
            for error, expected in (
                (LLMUnavailableError("private-value"), (502, "LLM_UNAVAILABLE")),
                (LLMTimeoutError("private-value"), (504, "LLM_TIMEOUT")),
                (LLMOutputError("private-value"), (502, "LLM_OUTPUT_INVALID")),
            ):
                with self.subTest(error=type(error).__name__):
                    self.generator.side_effect = error
                    response = client.post("/api/ask", json=REQUEST)
                    self.assertEqual((response.status_code, response.json()["detail"]["code"]), expected)
                    self.assertNotIn("private-value", response.text)


    def test_default_generator_wrapper_receives_normalized_profile(self):
        # 실제 Generator 동작은 대역으로 바꾸고, main._generate_answer가 profile을 키워드로 넘기는지만 확인한다.
        app = create_app(session_factory=self.factory, check_fn=checker)
        with patch("backend.llm.generator.generate_answer", return_value=answer()) as generate, TestClient(app) as client:
            request = {**REQUEST, "profile": {"age": 25, "region": "서울", "income": 0}}
            self.assertEqual(client.post("/api/ask", json=request).status_code, 200)
        args, kwargs = generate.call_args
        self.assertEqual(len(args), 4)
        self.assertEqual(kwargs, {"profile": {"age": 25, "region": "서울특별시", "income": 0}})

    def test_closed_policy_is_listed_after_open_policy(self):
        self.session.search_policy_context.return_value = {"policies": [policy("A", 1), policy("B", 2)]}

        def check(profile, item):
            return {**checker(profile, item), "reception_status": {"A": "closed", "B": "open"}[item["policy_id"]]}

        app = create_app(session_factory=self.factory, check_fn=check, generate_fn=self.generator)
        with TestClient(app) as client:
            body = client.post("/api/ask", json=REQUEST).json()
        self.assertEqual([(p["policy_id"], p["reception_status"]) for p in body["policy_checks"]],
                         [("B", "open"), ("A", "closed")])
        self.assertEqual(body["citations"][0]["document_id"], "DB")

    def test_display_fn_and_response_contract(self):
        def check(profile, item):
            row = {"field": "income", "label": "소득", "reason": "공고 확인", "status": "needs_check",
                   "followup": "check_notice"}
            return {"policy_id": item["policy_id"], "reception_status": "unknown", "conditions": [row, dict(row)]}

        def dedupe(conditions):
            return [item for index, item in enumerate(conditions) if item not in conditions[:index]]

        app = create_app(session_factory=self.factory, check_fn=check, generate_fn=self.generator, display_fn=dedupe)
        with TestClient(app) as client:
            body = client.post("/api/ask", json=REQUEST).json()
        self.assertEqual(sorted(body), ["answer", "checked_at", "citations", "follow_up_questions", "is_sample",
                                        "notice", "policy_checks", "status"])
        conditions = body["policy_checks"][0]["conditions"]
        self.assertEqual(len(conditions), 1)
        # 현재 계약(ConditionResult)에는 followup 키가 없어 응답에 노출되지 않는다. 안내는 reason으로 전달한다.
        self.assertEqual(conditions[0], {"field": "income", "label": "소득", "reason": "공고 확인",
                                         "status": "needs_check"})
        self.assertEqual(len(self.generator.call_args.args[2][0]["conditions"]), 2)

    def test_httpx_failures_map_to_public_errors(self):
        with self.client as client:
            for error, expected in (
                (ReadTimeout("private-value"), (504, "LLM_TIMEOUT")),
                (ConnectError("private-value"), (502, "LLM_UNAVAILABLE")),
                (ValueError("private-value"), (500, "INTERNAL_ERROR")),
            ):
                with self.subTest(error=type(error).__name__):
                    self.generator.side_effect = error
                    response = client.post("/api/ask", json=REQUEST)
                    self.assertEqual((response.status_code, response.json()["detail"]["code"]), expected)
                    self.assertNotIn("private-value", response.text)
