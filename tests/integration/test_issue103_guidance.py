"""동결 릴리스 + 가상 검색 세션/LLM으로 #103 API 방어를 재현한다(DB E2E 아님)."""

from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.api.schemas import AskRequest
from backend.eligibility.checker import check_eligibility
from backend.llm import generator
from backend.rag.application_links import align_application_links
from backend.rag.pipeline import AskError, run_ask_pipeline
from database.handoff_data import load_neo4j_nodes, load_payload_chunks

ROOT = Path(__file__).resolve().parents[2]
SEJONG = "POL_YC_065"
NAME = "세종시 위원회 청년참여 확대"
SPELLINGS = ("K-패스", "K패스", "k패스", "k-패스", "모두의 카드", "모두의카드")
QUESTIONS = (
    "세종 청년위원회 신청 방법을 알려줘.", "세종 청년위원회 지금 신청할 수 있어?",
    "세종 청년위원회 신청 링크 알려줘.", "세종 청년위원회 모집 기간은 언제야?",
    "세종 청년위원회 관련 공식 홈페이지 알려줘.",
)
REQUEST = {"question": QUESTIONS[0], "profile": {"age": 27, "region": "세종", "income": None}, "history": []}


def release_policy(pid):
    nodes = load_neo4j_nodes()
    node = next(row for row in nodes["Policy"] if row["policy_id"] == pid)
    chunks = [row for row in load_payload_chunks() if row["policy_id"] == pid]
    return {
        "policy_id": pid, "policy_name": node["policy_name"], "best_rank": 1,
        # 실제 DB 조건 트리를 재구성했다고 주장하지 않는다. Checker는 미확인 조건으로 보류한다.
        "graph": {"policy": node, "eligibility": {}, "documents": nodes["Document"]},
        "graph_status": "FOUND", "data_quality": {},
        "evidence": [{**row, "rank": i} for i, row in enumerate(chunks, 1)],
    }


def output(text):
    return {"status": "answered", "answer": text, "used_source_ids": [1], "follow_up_questions": []}


class Issue103GuidanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = release_policy(SEJONG)

    def ask(self, text, question=QUESTIONS[0]):
        session = Mock()
        session.search_policy_context.return_value = {"policies": [deepcopy(self.policy)]}
        prompts = []

        def sdk(messages):
            prompts.append(messages)
            return json.dumps(output(text), ensure_ascii=False)

        with patch.object(generator, "_call_openai", side_effect=sdk) as call:
            with TestClient(create_app(session_factory=lambda: session, generate_fn=generator.generate_answer)) as client:
                response = client.post("/api/ask", json={**REQUEST, "question": question})
        call.assert_called_once()
        return response, prompts

    def test_actual_release_retains_conflicting_raw_label_and_null(self):
        self.assertIsNone(self.policy["graph"]["policy"]["official_apply_url"])
        self.assertIn("[신청 URL] https://www.sejong.go.kr", self.policy["evidence"][0]["text"])
        self.assertIn("공식 공고 확인 필요", self.policy["graph"]["policy"]["application_period"])

    def test_pr130_response_reproduces_without_guard_and_is_fixed(self):
        saved = json.loads((ROOT / "docs/reports/issue122-r3d-ask-20261125.json").read_text(encoding="utf-8"))
        original = next(row for row in saved["results"] if row["kind"] == "sejong")["answer"]
        # 과거 B2의 [1..3]를 릴리스 원본 단일 청크로 단순 대조하지 않는다.
        text = original.split("\n\n")[0]
        with patch("backend.rag.pipeline.align_application_links", side_effect=lambda a, *_: (a, False)):
            old, _ = self.ask(text)
        self.assertIn("신청 URL은 세종시 홈페이지", old.json()["answer"])
        response, prompts = self.ask(text)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("신청 URL은 세종시 홈페이지", body["answer"])
        self.assertIn("직접 신청 링크는 확인되지 않았어요", body["answer"])
        self.assertIsNone(body["policy_checks"][0]["official_apply_url"])
        self.assertEqual(body["policy_checks"][0]["reception_status"], "unknown")
        self.assertEqual(body["citations"][0]["document_id"], "DOC_YC_065")
        self.assertNotIn("chunk_id", body["citations"][0])
        self.assertIn('"official_apply_url": null', prompts[0][1]["content"])
        self.assertIn("같은 policy_id", prompts[0][0]["content"])

    def test_five_question_types_through_real_checker_generator_and_api(self):
        replies = (
            "세종시 홈페이지에서 신청할 수 있어요 [1].",
            "현재 신청할 수 있어요 [1].", "신청 링크: https://www.sejong.go.kr [1].",
            "상시 신청 가능합니다 [1].", "공식 홈페이지는 https://www.sejong.go.kr 입니다 [1].",
        )
        for question, text in zip(QUESTIONS, replies):
            with self.subTest(question=question):
                response, _ = self.ask(text, question)
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual([p["policy_id"] for p in body["policy_checks"]], [SEJONG])
                self.assertIsNone(body["policy_checks"][0]["official_apply_url"])
                self.assertEqual(body["policy_checks"][0]["reception_status"], "unknown")
                self.assertEqual(body["citations"][0]["document_id"], "DOC_YC_065")
                if question == QUESTIONS[-1]:
                    self.assertEqual(body["answer"], text)
                else:
                    self.assertNotEqual(body["answer"], text)

    def test_link_wording_variants_and_markdown(self):
        for text in (
            "신청 URL은 세종시 홈페이지로 제시되어 있습니다[1].",
            "신청 **주소**는 https://www.sejong.go.kr 입니다 [1].",
            "[신청하기](https://www.sejong.go.kr) [1].",
            "세종시 홈페이지를 통해 온라인으로 접수하세요 [1].",
            "온라인 신청이 가능합니다 [1].",
            "세종시 홈페이지에서 신청할 수 있고 공고도 확인하세요 [1].",
            "서류는 확인이 필요하며 신청 URL은 세종시 홈페이지입니다 [1].",
        ):
            with self.subTest(text=text):
                response, _ = self.ask(text)
                self.assertEqual(response.status_code, 200)
                self.assertIn("직접 신청 링크는 확인되지 않았어요", response.json()["answer"])

    def test_normal_homepage_source_and_offline_method_are_preserved(self):
        for text in (
            "공식 홈페이지는 https://www.sejong.go.kr 입니다 [1].",
            "세종시 홈페이지에서 신청 관련 공고를 확인하세요 [1].",
            "신청 공고는 세종시 홈페이지에서 확인할 수 있습니다 [1].",
            "신청 공고는 https://www.sejong.go.kr 에서 확인하세요 [1].",
            "전용 신청 주소는 확인되지 않았습니다 [1].",
            "신청 URL은 없으며 공식 공고 확인이 필요합니다 [1].",
            "공고문에 기재된 방문 신청 방법을 확인하세요 [1].",
        ):
            with self.subTest(text=text):
                response, _ = self.ask(text)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["answer"], text)

    def test_two_policies_keep_valid_application_and_citations(self):
        checks = [
            {"policy_id": SEJONG, "policy_name": NAME, "official_apply_url": None, "reception_status": "unknown"},
            {"policy_id": "POL_B", "policy_name": "가상 지원사업", "official_apply_url": "https://example.org/apply", "reception_status": "open"},
        ]
        contexts = [{"source_id": 1, "policy_id": SEJONG}, {"source_id": 2, "policy_id": "POL_B"}]
        valid = "가상 지원사업 신청 주소는 https://example.org/apply 입니다 [2]."
        for separator in ("\n", ", ", "; ", " 하지만 "):
            with self.subTest(separator=separator):
                text = f"{NAME} 신청 링크는 https://example.org/apply 입니다 [1]" + separator + valid
                fixed, changed = align_application_links(text, contexts, checks)
                self.assertTrue(changed)
                self.assertIn(valid, fixed)
                self.assertNotIn("https://example.org/apply", fixed.split(separator)[0])
                self.assertIn("[1]", fixed)
                self.assertIn("[2]", fixed)

    def test_wrong_policy_citation_is_not_borrowed(self):
        checks = [{"policy_id": SEJONG, "policy_name": NAME, "official_apply_url": None, "reception_status": "unknown"}]
        contexts = [{"source_id": 2, "policy_id": "POL_B"}]
        fixed, _ = align_application_links(f"{NAME} 신청 URL은 https://example.org/apply [2]", contexts, checks)
        self.assertNotIn("[2]", fixed)

    def test_ambiguous_multi_policy_link_is_rejected(self):
        checks = [{"policy_id": p, "policy_name": p, "official_apply_url": url, "reception_status": "unknown"}
                  for p, url in (("A", None), ("B", "https://example.org/apply"))]
        with self.assertRaises(AskError):
            align_application_links("신청 URL은 https://example.org 입니다", [], checks)

    def test_internal_chunk_id_survives_without_public_schema_change(self):
        original = deepcopy(self.policy)
        seen = []

        def generate(question, contexts, checks, history, **kwargs):
            seen.extend(contexts)
            return output("공식 공고 확인이 필요합니다 [1].")

        run_ask_pipeline(AskRequest(**REQUEST), search_fn=lambda *a, **k: {"policies": [self.policy]},
                         check_fn=check_eligibility, generate_fn=generate)
        self.assertEqual(seen[0]["chunk_id"], self.policy["evidence"][0]["chunk_id"])
        self.assertEqual(seen[0]["document_id"], "DOC_YC_065")
        self.assertEqual(self.policy, original)

    def test_kpass_six_spellings_via_http_catalog(self):
        with TestClient(create_app(session_factory=Mock, generate_fn=Mock())) as client:
            for query in SPELLINGS:
                with self.subTest(query=query):
                    response = client.get("/api/policies", params={"q": query})
                    self.assertEqual(response.status_code, 200)
                    ids = [p["policy_id"] for p in response.json()["items"]]
                    self.assertIn("POL_STG_MOLIT_001", ids)
                    if query.lower().startswith("k"):
                        self.assertEqual(ids, ["POL_STG_MOLIT_001"])

    def test_faq_same_release_chunk_document_policy_and_citation(self):
        for suffix in ("MOLIT", "NTS", "SEOUL"):
            pid = f"POL_STG_{suffix}_001"
            item = release_policy(pid)
            doc = f"DOC_STG_{suffix}_001_FAQ_2026"
            item["evidence"] = [hit for hit in item["evidence"] if hit["document_id"] == doc]
            seen = []

            def generate(question, contexts, checks, history, **kwargs):
                seen.extend(contexts)
                return output("이번 FAQ 원문에서 확인한 내용입니다 [1].")

            result = run_ask_pipeline(AskRequest(**REQUEST), search_fn=lambda *a, **k: {"policies": [item]},
                                      check_fn=check_eligibility, generate_fn=generate)
            self.assertEqual(result.citations[0].document_id, doc)
            for ctx in seen:
                self.assertEqual(ctx["policy_id"], pid)
                self.assertIn(ctx["chunk_id"], {hit["chunk_id"] for hit in item["evidence"]})
            self.assertEqual(result.policy_checks[0].policy_id, pid)


if __name__ == "__main__":
    unittest.main()
