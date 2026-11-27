"""최신 Checker·Generator(#35)를 실제로 거치는 FastAPI 통합 테스트.

외부 서비스는 호출하지 않는다: 검색은 가상 세션, OpenAI는 generator._call_openai만 대체한다.
정책·날짜·원문은 모두 가상 데이터(sample_only)다.
"""

from copy import deepcopy
from pathlib import Path
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.llm.generator as generator
from backend.api.main import create_app
from backend.llm.generator import LLMTimeoutError, LLMUnavailableError

AGE_TREE = [{"group_id": "G_AGE", "logic": "AND", "groups": [],
             "conditions": [{"condition_id": "C_AGE", "type": "AGE", "operator": "BETWEEN", "value": "19~34"}]}]


def sample_policy(pid: str, rank: int, policy_fields: dict) -> dict:
    return {
        "policy_id": pid, "policy_name": f"가상 정책 {pid}(sample_only)", "best_rank": rank,
        "graph": {"policy": policy_fields,
                  "eligibility": {"condition_tree": deepcopy(AGE_TREE), "unknown_logic_groups": [],
                                  "structure_issues": []}},
        "graph_status": "FOUND", "data_quality": {},
        "evidence": [{"rank": rank, "chunk_id": f"CH_{pid}", "document_id": f"D_{pid}", "text": "가상 공고 원문",
                      "source_url": "https://example.org/notice"}],
    }


# A: 기간이 지난 정책(closed), 검색 1위 / B: 상시 접수 확인(open) + 소득 원문 확인 필요(needs_review)
POLICIES = [
    sample_policy("A", 1, {"application_start": "2020-01-01", "application_end": "2020-12-31"}),
    sample_policy("B", 2, {"application_is_ongoing": True, "income_source_review": "needs_review",
                           "income_source_note": "가상 확인 내용", "income_check_point": "가상 확인 항목",
                           "income_followup": "check_notice"}),
]
REQUEST = {"question": "청년 지원 정책은?", "profile": {"age": 25, "region": "서울", "income": 0}, "history": []}
# 세종 행정인턴 정부24 본문의 신청기한 행(칸 이름 교정 후, #107 document_line_rewrites)
DOCUMENT_SUBMISSION_LINE = "- 신청기한(최종선발자 21명 서류 제출, 일반 모집 신청기간 아님): 2026.06.24~2026.06.26"


class FakeSession:
    profile = collection = "sample_only"

    def __init__(self, policies):
        self.policies = policies

    def search_policy_context(self, question, filters=None, k=10):
        return {"policies": self.policies}

    def close(self):
        pass


def llm_output(*follow_ups, answer="가상 원문 기준 안내입니다 [1]"):
    return {"status": "answered", "answer": answer, "used_source_ids": [1], "follow_up_questions": list(follow_ups)}


class LatestGeneratorApiTests(unittest.TestCase):
    def setUp(self):
        self.policies = deepcopy(POLICIES)
        self.prompts = []
        self.app = create_app(session_factory=lambda: FakeSession(self.policies))  # 실제 Checker·Generator

    def ask(self, output=None, request=REQUEST, error=None):
        def fake_openai(messages):
            self.prompts.append(json.loads(messages[1]["content"].split("\n", 1)[1]))
            if error is not None:
                raise error
            return json.dumps(output, ensure_ascii=False)

        with patch.object(generator, "_call_openai", side_effect=fake_openai), TestClient(self.app) as client:
            return client.post("/api/ask", json=request)

    def test_corrected_application_period_does_not_reach_official_evidence(self):
        package = Path(__file__).resolve().parents[2] / "data/releases/v1.4.0-candidate"
        policy_id = "POL_YC_005"
        policies = map(json.loads, (package / "neo4j/nodes_policy.jsonl").read_bytes().splitlines())
        node = next(row for row in policies if row["policy_id"] == policy_id)
        chunks = map(json.loads, (package / "qdrant/payload_chunks.jsonl").read_bytes().splitlines())
        evidence = next(row for row in chunks if row["chunk_id"] == "DOC_YC_005_C01")
        policy = sample_policy(policy_id, 1, node)
        policy["evidence"] = [{**evidence, "rank": 1}]
        self.policies[:] = [policy]
        response = self.ask(llm_output())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["policy_checks"][0]["reception_status"], "unknown")
        official = self.prompts[0]["official_evidence"]
        self.assertEqual(official[0]["policy_id"], policy_id)
        self.assertIn("신청방법", official[0]["text"])
        self.assertNotIn("20261101 ~ 20261130", official[0]["text"])

    def sejong_intern_prompt(self, document_order: list[str], policy_id: str = "POL_GOV_059") -> dict:
        """정책의 실제 B2 청크를 검색 결과로 넣고, 생성기 입력을 돌려준다(기본: 세종시 청년 행정인턴).

        검색 순위만 바꿔서 '모집 신청기간을 묻는 질문'과 '선발자 서류 제출을 묻는 질문'을 흉내 낸다.
        검색·LLM은 대체하지만 정책 노드·문서 제목·B2 청크는 후보 릴리스 그대로 쓴다.
        """
        from database import chunking
        from database.release_adapter import load_release_documents

        root = Path(__file__).resolve().parents[2]
        package = root / "data/releases/v1.4.0-candidate"
        node = next(row for row in map(json.loads, (package / "neo4j/nodes_policy.jsonl").read_bytes().splitlines())
                    if row["policy_id"] == policy_id)
        documents = [d for d in load_release_documents(package, root) if d["document_id"] in document_order]
        b2_chunks, _ = chunking.chunk_documents(documents, strategy="B2")
        # 문서마다 신청기간 안내가 있는 첫 청크를 검색 결과로 쓴다(정부24 문서는 '신청기한' 줄이 있는 청크).
        hits = []
        for rank, document_id in enumerate(document_order, 1):
            chunk = next(c for c in b2_chunks if c["document_id"] == document_id
                         and ("신청기간" in c["text"] or "신청기한" in c["text"]))
            hits.append({**chunk, "rank": rank})
        policy = sample_policy(policy_id, 1, node)
        policy["policy_name"] = node["policy_name"]
        policy["graph"]["documents"] = [{"document_id": d["document_id"], "title": d["title"]} for d in documents]
        policy["evidence"] = hits
        self.policies[:] = [policy]
        response = self.ask(llm_output())
        self.assertEqual(response.status_code, 200, response.text)
        self.last_response = response
        return self.prompts[-1]

    def test_sejong_recruitment_period_reaches_generator(self):
        # 모집 신청기간 질문: 세종시 모집공고 청크가 먼저 검색된 경우
        official = self.sejong_intern_prompt(["DOC_GOV_059_SEJONG_NOTICE_2026", "DOC_GOV_059"])["official_evidence"]
        # 생성기 입력(official_evidence)에는 문서 ID·제목이 없고 본문만 간다. 그래서 본문으로 두 근거를 구분한다.
        self.assertEqual(len(official), 2)
        notice, gov24 = official
        # 정정된 모집 신청기간(6.15~6.21)이 '모집접수' 공지 본문과 함께 생성기 입력에 들어간다.
        self.assertIn("2026년 하계 청년 행정인턴 모집접수(6.15~6.21)", notice["text"])
        self.assertIn("- 신청기간 : 2026. 6. 15.(월) ~ 2026. 6. 21.(일)", notice["text"])
        # 같은 입력의 정부24 근거는 6.24~26 날짜 바로 앞에 '최종선발자 서류 제출'이라고 적혀 있다.
        # (날짜 줄만 인용해도 일반 모집 신청기간으로 읽히지 않게 칸 이름을 교정했다.)
        self.assertIn(DOCUMENT_SUBMISSION_LINE, gov24["text"])
        self.assertNotIn("- 신청기한: 2026.06.24~2026.06.26", gov24["text"])
        self.assertIn("최종선발자(21명)에 한하여 접수", gov24["text"])
        self.assertNotIn("2026.06.24", notice["text"])

    def test_sejong_document_submission_period_is_kept(self):
        # 서류 제출 질문: 정부24 문서만 검색된 경우에도 원래 서류 접수 안내(6.24~26)는 지우지 않고 그대로 전달한다.
        official = self.sejong_intern_prompt(["DOC_GOV_059"])["official_evidence"]
        self.assertEqual(len(official), 1)
        self.assertIn(DOCUMENT_SUBMISSION_LINE, official[0]["text"])
        self.assertIn("최종선발자(21명)에 한하여 접수", official[0]["text"])

    def test_past_schedule_notice_does_not_reach_generator(self):
        # 대전 중구 장학금(POL_GOV_033): 2026년 일정을 물어도 지난 '10월 초 게시 예정' 예고는 생성기에 가지 않고,
        # 2025년 날짜는 '2025년 회차'로 표시된 채 남는다.
        official = self.sejong_intern_prompt(["DOC_GOV_033"], policy_id="POL_GOV_033")["official_evidence"]
        self.assertEqual(len(official), 1)
        self.assertIn("- 신청기한(2025년 회차): 2025.10.24~2025.11.7.", official[0]["text"])
        self.assertNotIn("10월 초 게시 예정", official[0]["text"])

    def test_unverified_period_is_not_sent_as_ongoing(self):
        # 전북 임대보증금(POL_YC_001): 신청기간이 미확인인 정책(수집 기본 문구 교체 대상, #107)
        prompt = self.sejong_intern_prompt(["DOC_YC_001"], policy_id="POL_YC_001")
        body = self.last_response.json()
        self.assertEqual(body["policy_checks"][0]["reception_status"], "unknown")
        self.assertEqual([p["reception_status"] for p in prompt["policy_checks"]], ["unknown"])
        sent = json.dumps(prompt, ensure_ascii=False)
        # 생성기 입력에 수집 기본 문구나 상시 접수 표현이 들어가지 않는다.
        for phrase in ("상시/공고참조", "상시/별도공고", "상시 또는 공고 확인", "상시 접수", "상시신청"):
            self.assertNotIn(phrase, sent)

    def test_profile_reception_reason_and_body_guidance_reach_http(self):
        response = self.ask(llm_output(
            {"kind": "ask_info", "field": "region", "text": "서울특별시의 어느 구에 거주하시나요?"},
            {"kind": "ask_info", "field": "household_size", "text": "가구원 수를 알려주세요."},
            {"kind": "ask_info", "field": "income", "text": "월 소득이 얼마인가요?"},
        ))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        prompt = self.prompts[0]
        # 정규화 프로필이 Generator Prompt까지 도달한다(서울→서울특별시, 소득 0 유지).
        # #39: 생략된 child_age_months는 정규화 프로필에 None으로 실려 Generator Prompt까지 간다.
        self.assertEqual(prompt["user_profile"],
                         {"age": 25, "region": "서울특별시", "income": 0, "child_age_months": None})
        # 마감(A)은 검색 1위여도 Context·출처·정책 카드에서 뒤로 간다.
        self.assertEqual([c["policy_id"] for c in prompt["official_evidence"]], ["B", "A"])
        self.assertEqual([(p["policy_id"], p["reception_status"]) for p in body["policy_checks"]],
                         [("B", "open"), ("A", "closed")])
        self.assertEqual([c["document_id"] for c in body["citations"]], ["D_B"])
        # 시군구·가구원 수는 버튼이 아니라 본문 안내, 이미 입력한 소득(0)은 다시 묻지 않는다.
        self.assertEqual(body["follow_up_questions"], [])
        self.assertIn("시·군·구", body["answer"])
        self.assertIn("가구원 수", body["answer"])
        # Checker가 만든 원문 확인 사유가 그대로 응답까지 유지된다.
        income = [c for c in body["policy_checks"][0]["conditions"] if c["field"] == "income"]
        self.assertEqual([c["status"] for c in income], ["needs_check"])
        self.assertIn("가상 확인 항목", income[0]["reason"])
        self.assertEqual(body["notice"]["kind"], "warn")
        # 입력 정책 객체는 바뀌지 않는다.
        self.assertEqual(self.policies, POLICIES)

    def test_only_user_to_ai_questions_remain_as_buttons(self):
        response = self.ask(llm_output(
            {"kind": "question", "text": "신청 서류도 알려드릴까요?"},
            {"kind": "question", "text": "신청 서류는 무엇이 필요한가요?"},
            {"kind": "ask_info", "field": "region", "text": "거주하시는 시도는 어디인가요?"},
        ))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["follow_up_questions"],
                         [{"kind": "question", "text": "신청 서류는 무엇이 필요한가요?", "field": None}])

    def test_child_months_and_redundant_profile_questions_are_not_buttons(self):
        response = self.ask(llm_output(
            {"kind": "ask_info", "field": "child_age_months", "text": "자녀의 개월 수를 알려주세요."},
            {"kind": "question", "text": "나이가 어떻게 되시나요?"},
            {"kind": "question", "text": "신청 기간은 언제까지인가요?"},
        ))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        # 자녀 월령은 허용 필드가 아니므로 버튼이 아니라 본문 안내, 이미 입력한 나이 재질문은 제거.
        self.assertIn("자녀", body["answer"])
        self.assertEqual(body["follow_up_questions"],
                         [{"kind": "question", "text": "신청 기간은 언제까지인가요?", "field": None}])
        self.assertEqual([c["document_id"] for c in body["citations"]], ["D_B"])
        self.assertIn("[1]", body["answer"])

    def test_real_generator_answer_conflicting_with_checker_is_corrected(self):
        # 실제 Generator 검증을 통과한 본문이라도 Checker 판정과 충돌하는 단정은 확인된 상태로 바뀐다.
        # Context 순서: [1]=B(open, 소득 needs_review), [2]=A(closed)
        response = self.ask(llm_output(answer=(
            "가상 정책 A(sample_only)는 지금 신청할 수 있어요 [2]. "
            "가상 정책 B(sample_only)는 모든 신청 자격을 충족했어요 [1].")))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "answered")
        self.assertNotIn("지금 신청할 수 있어요", body["answer"])
        self.assertNotIn("모든 신청 자격을 충족했어요", body["answer"])
        self.assertIn("가상 정책 A(sample_only): 접수가 마감됐어요.", body["answer"])
        self.assertIn("가상 정책 B(sample_only): 신청 자격은 공식 공고에서 추가 확인이 필요해요.", body["answer"])
        # 본문 인용 순서대로 재번호되고, 출처 문서 연결은 유지된다.
        self.assertEqual([c["document_id"] for c in body["citations"]], ["D_B", "D_A"])
        self.assertEqual([(p["policy_id"], p["reception_status"]) for p in body["policy_checks"]],
                         [("B", "open"), ("A", "closed")])

    def test_named_closed_policy_with_other_policy_source_becomes_uncited_info(self):
        response = self.ask(llm_output(answer="가상 정책 A(sample_only)는 지금 신청할 수 있어요 [1]."))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "info")
        self.assertIn("접수가 마감됐어요", body["answer"])
        self.assertNotIn("신청할 수 있어요", body["answer"])
        self.assertEqual(body["citations"], [])
        self.assertEqual([(p["policy_id"], p["reception_status"]) for p in body["policy_checks"]],
                         [("B", "open"), ("A", "closed")])

    def test_bad_a_citation_removed_b_document_claim_and_source_preserved(self):
        self.policies[1]["evidence"][0]["text"] = "제출 서류가 필요합니다."
        response = self.ask(llm_output(answer=(
            "가상 정책 A(sample_only)는 지금 신청할 수 있어요 [1]. "
            "가상 정책 B(sample_only)는 제출 서류가 필요해요 [1].")))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("접수가 마감됐어요", body["answer"])
        self.assertIn("가상 정책 B(sample_only)는 제출 서류가 필요해요 [1]", body["answer"])
        self.assertEqual([item["document_id"] for item in body["citations"]], ["D_B"])

    def test_missing_profile_value_can_still_be_asked(self):
        request = {**REQUEST, "profile": {"age": 25, "region": "서울", "income": None}}
        response = self.ask(llm_output({"kind": "ask_info", "field": "income", "text": "월 소득이 얼마인가요?"}),
                            request=request)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.prompts[0]["user_profile"]["income"], None)
        self.assertEqual(response.json()["follow_up_questions"],
                         [{"kind": "ask_info", "text": "월 소득이 얼마인가요?", "field": "income"}])

    def test_generator_errors_map_to_public_codes(self):
        cases = (
            ({"error": LLMTimeoutError("private-value")}, (504, "LLM_TIMEOUT")),
            ({"error": LLMUnavailableError("private-value")}, (502, "LLM_UNAVAILABLE")),
            ({"output": llm_output(answer="근거 없는 번호 [9]")}, (502, "LLM_OUTPUT_INVALID")),
            # 후속 질문은 최대 3개(계약 §5.1). 초과 출력은 잘못된 LLM 출력이다.
            ({"output": llm_output(*[{"kind": "question", "text": f"질문 {i}은 무엇인가요?"} for i in range(4)])},
             (502, "LLM_OUTPUT_INVALID")),
        )
        for kwargs, expected in cases:
            with self.subTest(expected=expected):
                response = self.ask(**kwargs)
                self.assertEqual((response.status_code, response.json()["detail"]["code"]), expected)
                self.assertNotIn("private-value", response.text)

    def test_child_age_months_reaches_generator_prompt_separately_from_age(self):
        # #39: 0개월은 미입력이 아니며, 신청자 age와 별개로 실제 Generator Prompt까지 그대로 간다.
        for months in (0, 24):
            with self.subTest(months=months):
                self.prompts.clear()
                request = {**REQUEST, "profile": {**REQUEST["profile"], "child_age_months": months}}
                self.assertEqual(self.ask(llm_output(), request=request).status_code, 200)
                user_profile = self.prompts[0]["user_profile"]
                self.assertEqual((user_profile["age"], user_profile["child_age_months"]), (25, months))


if __name__ == "__main__":
    unittest.main()
