"""공식 자격기준(#76)을 실제 Pipeline·FastAPI 경로로 검증한다.

검색 결과·검수 기록은 가상 Fixture(sample_only)이고, 실제 정책 기준이 아니다.
Checker는 기존 테스트 대역(checker), Generator는 Mock을 쓰며 OpenAI를 호출하지 않는다.
SourceItemsApiTests는 검수 기록 없이 자격 블록의 "항목: 값" 원문이 API까지 오는지 본다(가상 원문).
마지막 클래스만 v1.4.0 후보 릴리스(R3D와 같은 manifest)의 실제 청크를 읽는다.
"""

import copy
import json
from importlib.util import find_spec
from pathlib import Path
import re
import unittest
from unittest.mock import Mock

from test_rag_pipeline import answer, checker, policy

from backend.api.schemas import AskResponse

if find_spec("fastapi") is not None and find_spec("httpx") is not None:
    from fastapi.testclient import TestClient

    from backend.api.main import create_app
else:
    TestClient = None

ROOT = Path(__file__).resolve().parents[2]
RELEASE_CHUNKS = ROOT / "data" / "releases" / "v1.4.0-candidate" / "qdrant" / "payload_chunks.jsonl"
REQUEST = {"question": "가상 정책 자격은?", "profile": {"age": 25, "region": "서울", "income": None}, "history": []}


def _flat(text):
    """공백·줄바꿈 차이만 없앤 비교용 문자열. 단서 줄을 이어 붙인 값은 원문 줄바꿈이 공백 하나가 된다."""
    return re.sub(r"\s+", " ", text).strip()


TEXT_A = ("[지원대상]\n만 19세 이상 34세 이하 가상 청년\n[선정기준]\n가구 기준 중위소득 150% 이하(가상)\n"
          "[문의처]\n가상기관A (02-000-0001)\n")
TEXT_B = "[지원대상]\n만 65세 이상 가상 어르신\n[구비서류]\n가상 신청서\n"
CHECKS = {"value": "verified", "unit": "verified", "year": "not_applicable"}
REVIEWS = (
    {"policy_id": "POL_A", "label": "소득 기준", "value": "가구 기준 중위소득 150% 이하(가상)",
     "document_id": "DOC_A", "chunk_id": "CHK_A", "review_status": "verified", "checks": CHECKS},
    {"policy_id": "POL_A", "label": "연령", "value": "만 19세 이상 34세 이하 가상 청년",
     "document_id": "DOC_A", "chunk_id": "CHK_A", "review_status": "needs_review", "checks": CHECKS},
    {"policy_id": "POL_B", "label": "연령", "value": "만 65세 이상 가상 어르신",
     "document_id": "DOC_B", "chunk_id": "CHK_B", "review_status": "verified", "checks": CHECKS},
)


def two_policies():
    return {"policies": [
        policy("POL_A", 1, evidence=[{"rank": 1, "chunk_id": "CHK_A", "document_id": "DOC_A", "text": TEXT_A,
                                       "source_url": "https://example.org/a"}]),
        policy("POL_B", 2, evidence=[{"rank": 2, "chunk_id": "CHK_B", "document_id": "DOC_B", "text": TEXT_B,
                                       "source_url": "https://example.org/b"}]),
    ]}


@unittest.skipIf(TestClient is None, "API 검증에는 fastapi와 httpx가 필요합니다.")
class OfficialRequirementsApiTests(unittest.TestCase):
    def post(self, reviews=REVIEWS, body=None, search=None, reply=None):
        session = Mock()
        session.search_policy_context.side_effect = lambda *a, **k: copy.deepcopy(search or two_policies())
        self.generator = Mock(return_value=reply or answer("가상 답변 [1] [2]", ids=[1, 2]))
        app = create_app(session_factory=Mock(return_value=session), check_fn=checker, generate_fn=self.generator,
                         catalog_factory=Mock, requirement_reviews=reviews)
        with TestClient(app) as client:
            response = client.post("/api/ask", json=body or REQUEST)
        self.assertEqual(response.status_code, 200)
        self.session = session
        return response.json()

    def test_verified_requirements_reach_api_per_policy_without_mixing(self):
        data = self.post()
        AskResponse.model_validate(data)
        by_pid = {check["policy_id"]: check for check in data["policy_checks"]}
        self.assertEqual(by_pid["POL_A"]["official_requirements"], [
            {"label": "소득 기준", "value": "가구 기준 중위소득 150% 이하(가상)", "document_id": "DOC_A", "chunk_id": "CHK_A"},
        ])
        self.assertEqual(by_pid["POL_B"]["official_requirements"], [
            {"label": "연령", "value": "만 65세 이상 가상 어르신", "document_id": "DOC_B", "chunk_id": "CHK_B"},
        ])
        # 공식 기준의 출처는 이번 응답 citations에 있는 같은 정책 문서다. 인용 번호 체계는 그대로다.
        self.assertEqual([c["document_id"] for c in data["citations"]], ["DOC_A", "DOC_B"])
        self.assertEqual(data["answer"], "가상 답변 [1] [2]")

    def test_existing_fields_are_unchanged_by_requirement_reviews(self):
        without, with_reviews = self.post(reviews=()), self.post()
        for before, after in zip(without["policy_checks"], with_reviews["policy_checks"]):
            self.assertIsNone(before["official_requirements"])
            self.assertEqual({k: v for k, v in before.items() if k != "official_requirements"},
                             {k: v for k, v in after.items() if k != "official_requirements"})
        self.assertEqual({k: v for k, v in without.items() if k != "policy_checks"},
                         {k: v for k, v in with_reviews.items() if k != "policy_checks"})
        a = with_reviews["policy_checks"][0]
        self.assertEqual([row["label"] for row in a["requirement_excerpts"]], ["지원대상", "선정기준"])
        self.assertEqual(a["contacts"][0]["agency"], "가상기관A")
        self.assertEqual(with_reviews["policy_checks"][1]["required_documents"][0]["text"], "가상 신청서")
        # Checker 판정(conditions·reception_status)은 그대로 전달된다.
        expected = checker({}, {"policy_id": "POL_A"})
        self.assertEqual(a["conditions"], expected["conditions"])
        self.assertEqual(a["reception_status"], expected["reception_status"])

    def test_generator_input_does_not_receive_official_requirements(self):
        self.post()
        _question, contexts, checks, _history = self.generator.call_args.args
        self.assertTrue(checks)
        self.assertTrue(all("official_requirements" not in check for check in checks))
        self.assertEqual([context["source_id"] for context in contexts], [1, 2])

    def test_no_verified_review_keeps_excerpts_and_returns_null(self):
        data = self.post(reviews=REVIEWS[1:2])
        check = data["policy_checks"][0]
        self.assertIsNone(check["official_requirements"])
        self.assertIsNotNone(check["requirement_excerpts"])

    def test_selected_policy_only_links_that_policy_requirements(self):
        data = self.post(body={**REQUEST, "selected_policy_id": "POL_B"}, reply=answer())
        self.assertEqual(self.session.search_policy_context.call_args.kwargs["filters"], {"policy_id": "POL_B"})
        self.assertEqual([check["policy_id"] for check in data["policy_checks"]], ["POL_B"])
        self.assertEqual([row["chunk_id"] for row in data["policy_checks"][0]["official_requirements"]], ["CHK_B"])

    def test_invalid_review_records_do_not_break_ask(self):
        broken = [None, {}, {"policy_id": "POL_A"}, {**REVIEWS[0], "chunk_id": "CHK_B"},
                  {**REVIEWS[0], "value": "가구 기준 중위소득 180% 이하(가상)"}, {**REVIEWS[0], "checks": []},
                  # PR #133 리뷰: checks.value에 배열 등 잘못된 형식 → 500이 아니라 그 기록만 건너뛴다.
                  {**REVIEWS[0], "checks": {**CHECKS, "value": ["verified"]}},
                  {**REVIEWS[0], "checks": {**CHECKS, "unit": {"x": 1}}}, {**REVIEWS[0], "label": ["소득"]},
                  {**REVIEWS[0], "value": "50% 이하(가상)"}]
        data = self.post(reviews=broken)
        self.assertTrue(all(check["official_requirements"] is None for check in data["policy_checks"]))

    def test_mismatched_evidence_document_is_not_used(self):
        # PR #133 리뷰: 검색 계층 evidence_document_check가 MISMATCH인 문서는 공식 기준에 쓰지 않는다.
        search = two_policies()
        search["policies"][0]["evidence_document_check"] = {"DOC_A": "MISMATCH"}
        search["policies"][1]["evidence_document_check"] = {"DOC_B": "PASS"}
        data = self.post(search=search)
        by_id = {check["policy_id"]: check for check in data["policy_checks"]}
        self.assertIsNone(by_id["POL_A"]["official_requirements"])
        self.assertEqual([row["chunk_id"] for row in by_id["POL_B"]["official_requirements"]], ["CHK_B"])

    def test_no_evidence_answer_has_no_policy_checks(self):
        session = Mock()
        session.search_policy_context.return_value = two_policies()
        generator = Mock(return_value={"status": "no_evidence", "answer": "근거가 없습니다.", "used_source_ids": [],
                                       "follow_up_questions": []})
        app = create_app(session_factory=Mock(return_value=session), check_fn=checker, generate_fn=generator,
                         catalog_factory=Mock, requirement_reviews=REVIEWS)
        with TestClient(app) as client:
            data = client.post("/api/ask", json=REQUEST).json()
        self.assertEqual(data["policy_checks"], [])


ITEM_TEXT_A = ("[지원대상]\n- 연령: 만 19세 ~ 34세(가상)\nㅇ (소득) 2026년 기준 중위소득 150% 이하(가상)\n"
               "- 특화요건: 가상 계층\n[지원내용]\n- 지원금액: 월 100만원(가상)\n[신청기간 및 신청방법]\n"
               "- 신청기간: 상시\n[문의처]\n가상기관A (02-000-0001)\n")
ITEM_TEXT_B = ("[선정기준]\n- 소득기준: 기준 중위소득 60% 이하(가상)\n- 연령: 제한없음\n"
               "[참여 제한대상]\n- 연령: 만 40세 이상(가상)\n[구비서류]\n가상 신청서\n")


def item_policies():
    return {"policies": [
        policy("POL_A", 1, evidence=[{"rank": 1, "chunk_id": "CHK_A", "document_id": "DOC_A", "text": ITEM_TEXT_A,
                                       "source_url": "https://example.org/a"}]),
        policy("POL_B", 2, evidence=[{"rank": 2, "chunk_id": "CHK_B", "document_id": "DOC_B", "text": ITEM_TEXT_B,
                                       "source_url": "https://example.org/b"}]),
    ]}


@unittest.skipIf(TestClient is None, "API 검증에는 fastapi와 httpx가 필요합니다.")
class SourceItemsApiTests(unittest.TestCase):
    """검색 결과 → Context Builder → Pipeline → /api/ask. 검수 기록 없음(API 기본값)."""

    def post(self, search):
        session = Mock()
        session.search_policy_context.side_effect = lambda *a, **k: copy.deepcopy(search)
        self.generator = Mock(return_value=answer("가상 답변 [1] [2]", ids=[1, 2]))
        app = create_app(session_factory=Mock(return_value=session), check_fn=checker, generate_fn=self.generator,
                         catalog_factory=Mock)
        with TestClient(app) as client:
            response = client.post("/api/ask", json=REQUEST)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        AskResponse.model_validate(data)
        return data

    def test_source_items_reach_api_per_policy_with_own_sources(self):
        data = self.post(item_policies())
        by_pid = {check["policy_id"]: check for check in data["policy_checks"]}
        self.assertEqual(by_pid["POL_A"]["official_requirements"], [
            {"label": "연령", "value": "만 19세 ~ 34세(가상)", "document_id": "DOC_A", "chunk_id": "CHK_A"},
            {"label": "소득", "value": "2026년 기준 중위소득 150% 이하(가상)", "document_id": "DOC_A", "chunk_id": "CHK_A"},
        ])
        # 지원금액·신청기간·특화요건·제한없음·참여 제한(제외) 줄은 공식 자격기준으로 옮기지 않는다.
        self.assertEqual(by_pid["POL_B"]["official_requirements"], [
            {"label": "소득기준", "value": "기준 중위소득 60% 이하(가상)", "document_id": "DOC_B", "chunk_id": "CHK_B"},
        ])
        # 공식 기준의 출처 문서는 이번 응답 citations에 있는 같은 정책 문서다.
        cited = {c["document_id"]: c for c in data["citations"]}
        for check in data["policy_checks"]:
            for row in check["official_requirements"]:
                self.assertIn(row["document_id"], cited)
        self.assertEqual([c["document_id"] for c in data["citations"]], ["DOC_A", "DOC_B"])

    def test_personal_checks_and_other_fields_are_unchanged(self):
        data = self.post(item_policies())
        expected = checker({}, {"policy_id": "POL_A"})
        for check in data["policy_checks"]:
            # 공식 기준을 찾았다고 개인 판정을 바꾸지 않는다(region은 Checker 대역대로 ineligible 유지).
            self.assertEqual(check["conditions"], expected["conditions"])
            self.assertEqual(check["reception_status"], expected["reception_status"])
            self.assertIsNone(check["exclusion_reasons"])
        a, b = data["policy_checks"]
        self.assertEqual(a["contacts"][0]["agency"], "가상기관A")
        self.assertEqual(b["required_documents"][0]["text"], "가상 신청서")
        self.assertEqual([row["label"] for row in b["requirement_excerpts"]], ["선정기준"])
        self.assertEqual(data["answer"], "가상 답변 [1] [2]")
        _question, contexts, checks, _history = self.generator.call_args.args
        self.assertTrue(all("official_requirements" not in check for check in checks))
        self.assertEqual([context["source_id"] for context in contexts], [1, 2])

    def test_source_text_age_error_flag_hides_only_that_item(self):
        text = "[지원대상]\n- 연령: 만 19세 ~ 34세(가상)\n- 소득: 중위소득 150% 이하(가상)\n[구비서류]\n가상\n"
        search = {"policies": [policy("POL_A", 1, data_quality={
            "warnings": [{"code": "SOURCE_AGE_ERROR_MARK", "affects": ["age"], "blocks_auto_eligibility": True}],
            "hold_auto_eligibility": True, "affected_attributes": ["age"]},
            evidence=[{"rank": 1, "chunk_id": "CHK_A", "document_id": "DOC_A", "text": text,
                       "source_url": "https://example.org/a"}])]}
        session = Mock()
        session.search_policy_context.return_value = search
        app = create_app(session_factory=Mock(return_value=session), check_fn=checker,
                         generate_fn=Mock(return_value=answer()), catalog_factory=Mock)
        with TestClient(app) as client:
            check = client.post("/api/ask", json=REQUEST).json()["policy_checks"][0]
        self.assertEqual([row["label"] for row in check["official_requirements"]], ["소득"])


def _release_by_policy():
    by_policy = {}
    with RELEASE_CHUNKS.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            by_policy.setdefault(row["policy_id"], []).append(row)
    return by_policy


@unittest.skipIf(TestClient is None, "API 검증에는 fastapi와 httpx가 필요합니다.")
@unittest.skipUnless(RELEASE_CHUNKS.exists(), "v1.4.0 후보 릴리스 청크가 없습니다.")
class ReleaseDataTests(unittest.TestCase):
    """실제 릴리스 청크(검수 기록 없음). 기대값은 릴리스 원문에서 직접 확인한 값만 쓴다."""

    @classmethod
    def setUpClass(cls):
        from backend.rag.official_requirements import official_requirements

        cls.by_policy = _release_by_policy()
        cls.results = {pid: official_requirements(pid, rows) for pid, rows in cls.by_policy.items()}

    def ask(self, pid):
        rows = self.by_policy[pid]
        evidence = [{"rank": i, "chunk_id": r["chunk_id"], "document_id": r["document_id"], "text": r["text"],
                     "source_url": r["source_url"]} for i, r in enumerate(rows, 1)]
        session = Mock()
        session.search_policy_context.return_value = {
            "policies": [policy(pid, 1, policy_name=rows[0]["policy_name"], evidence=evidence)]}
        app = create_app(session_factory=Mock(return_value=session), check_fn=checker,
                         generate_fn=Mock(return_value=answer()), catalog_factory=Mock)
        with TestClient(app) as client:
            response = client.post("/api/ask", json=REQUEST)
        self.assertEqual(response.status_code, 200)
        return response.json()["policy_checks"][0]

    def test_every_extracted_value_is_verbatim_from_same_policy_chunk(self):
        self.assertTrue(any(self.results.values()))
        for pid, rows in self.results.items():
            texts = {r["chunk_id"]: (r["document_id"], r["text"]) for r in self.by_policy[pid]}
            for row in rows or []:
                with self.subTest(policy_id=pid, label=row["label"]):
                    self.assertIn(row["chunk_id"], texts)
                    document_id, text = texts[row["chunk_id"]]
                    self.assertEqual(row["document_id"], document_id)
                    self.assertIn(_flat(row["value"]), _flat(text))
                    self.assertNotRegex(row["value"], r"상시|신청\s*기간|제한여부|0원\s*~\s*0원|미상|(?<!차상위)본인\s*부담|이용료")

    def test_representative_policies_through_api(self):
        # 릴리스 원문(v1.4.0-candidate payload_chunks)에서 직접 확인한 값.
        expected = {
            "POL_GOV_046": [("가입연령", "신청당시 만 15세~만 39세 이하")],          # 나이 기준
            "POL_BOK_045": [("소득기준", "기준중위소득 180% 이하")],                  # 소득 기준
            "POL_BOK_004": [("가입연령", "신청 당시 만 15세~ 39세 ※ 신청 월의 전월에 만 15세가 된 자 ~ 신청 월에 만 40세가 되는 자"),
                            ("근로·사업소득", "근로,사업소득이 월10만원 이상 발생"),
                            ("가구소득", "소득인정액 기준 중위소득 50% 이하")],      # 여러 기준
        }
        for pid, rows in expected.items():
            with self.subTest(policy_id=pid):
                check = self.ask(pid)
                self.assertEqual([(r["label"], r["value"]) for r in check["official_requirements"]], rows)
                own = {r["chunk_id"] for r in self.by_policy[pid]}
                self.assertTrue(all(r["chunk_id"] in own for r in check["official_requirements"]))
                self.assertEqual(check["conditions"], checker({}, {"policy_id": pid})["conditions"])

    def test_unknown_or_unconfirmed_policies_stay_null(self):
        # PR #113 신청일정 미확인 대출 4건, PR #118 신규 복지로 3건, 온통청년 수집기 템플릿만 있는 정책.
        for pid in ("POL_003", "POL_004", "POL_006", "POL_007",
                    "POL_BOK_099", "POL_BOK_101", "POL_BOK_104", "POL_YC_001"):
            with self.subTest(policy_id=pid):
                self.assertIn(pid, self.by_policy)
                check = self.ask(pid)
                self.assertIsNone(check["official_requirements"])
                own = {r["chunk_id"] for r in self.by_policy[pid]}
                self.assertTrue(all(row["chunk_id"] in own for row in check["requirement_excerpts"] or []))


@unittest.skipUnless(RELEASE_CHUNKS.exists(), "v1.4.0 후보 릴리스가 없습니다.")
class ReleaseB2ChunkTests(unittest.TestCase):
    """실제 검색 컬렉션(B2) 청크 경계 기준. 커밋된 릴리스 문서를 같은 청커로 다시 나눠 DB 없이 재현한다.

    기대값은 2026-11-26 R3D 실제 DB 상담(verify_issue76_r3d_readonly.py)이 돌려준 값·chunk_id와 같다.
    """

    @classmethod
    def setUpClass(cls):
        from database import chunking, handoff_data

        if handoff_data.dataset_version() != "1.4.0-candidate":
            raise unittest.SkipTest("v1.4.0 데이터셋 전용")
        wanted = {"POL_BOK_045", "POL_BOK_004", "POL_BOK_007", "POL_GOV_046", "POL_009", "POL_BOK_061",
                  "POL_GOV_061"}
        documents = [d for d in handoff_data.load_documents(verify_hash=False) if d.get("policy_id") in wanted]
        chunks, _ = chunking.chunk_documents(documents, strategy="B2")
        cls.by_policy = {}
        for chunk in chunks:
            cls.by_policy.setdefault(chunk["policy_id"], []).append(chunk)

    def rows(self, pid, chunk_ids=None):
        from backend.rag.official_requirements import official_requirements

        contexts = [{**c, "text": c["text"].strip()} for c in self.by_policy[pid]
                    if chunk_ids is None or c["chunk_id"] in chunk_ids]
        return official_requirements(pid, contexts)

    def test_values_and_chunk_ids_match_live_r3d_response(self):
        expected = {
            "POL_BOK_045": [("소득기준", "기준중위소득 180% 이하", "CHK_B2_DOC_BOK_045_C002")],
            "POL_BOK_004": [("가입연령", "신청 당시 만 15세~ 39세 ※ 신청 월의 전월에 만 15세가 된 자 ~ 신청 월에 만 40세가 되는 자",
                             "CHK_B2_DOC_BOK_004_C002"),
                            ("근로·사업소득", "근로,사업소득이 월10만원 이상 발생", "CHK_B2_DOC_BOK_004_C002"),
                            ("가구소득", "소득인정액 기준 중위소득 50% 이하", "CHK_B2_DOC_BOK_004_C002")],
            "POL_BOK_007": [("소득기준", "「국민기초생활보장법」에 따른 생계, 의료, 주거, 교육 급여 중 하나 이상의 급여를 받는 자",
                             "CHK_B2_DOC_BOK_007_C002")],
        }
        for pid, rows in expected.items():
            with self.subTest(policy_id=pid):
                self.assertEqual([(r["label"], r["value"], r["chunk_id"]) for r in self.rows(pid)], rows)

    def test_fee_tier_income_line_is_not_eligibility(self):
        # PR #133 리뷰: POL_BOK_061 '(소득기준) 기준중위소득 120% 이하 무료, 소득 초과 가정은 이용료의 40% 본인부담'은
        # 이용료 구간이다(초과해도 본인부담으로 이용 가능). 신청 자격 소득기준으로 내보내지 않는다. 연령기준은 유지.
        chunk = next(c for c in self.by_policy["POL_BOK_061"] if "120% 이하 무료" in c["text"])
        self.assertEqual(chunk["chunk_id"], "CHK_B2_DOC_BOK_061_C002")
        rows = self.rows("POL_BOK_061")
        self.assertEqual([(r["label"], r["chunk_id"]) for r in rows], [("연령기준", "CHK_B2_DOC_BOK_061_C002")])
        self.assertTrue(all("본인부담" not in r["value"] and "120%" not in r["value"] for r in rows))

    def test_next_line_condition_stays_with_requirement(self):
        # PR #133 리뷰: POL_GOV_061 '신청자격' 바로 다음 줄의 '※ 단, 소득연계형 국가장학금 중복수혜 불가'를 함께 담는다.
        rows = self.rows("POL_GOV_061")
        self.assertEqual([(r["label"], r["chunk_id"]) for r in rows], [("신청자격", "CHK_B2_DOC_GOV_061_C002")])
        self.assertTrue(rows[0]["value"].endswith("전액을 사용한 자 ※ 단, 소득연계형 국가장학금 중복수혜 불가"))
        chunk = next(c for c in self.by_policy["POL_GOV_061"] if c["chunk_id"] == rows[0]["chunk_id"])
        self.assertIn(_flat(rows[0]["value"]), _flat(chunk["text"]))

    def test_line_at_b2_chunk_end_is_conservatively_dropped(self):
        # POL_GOV_046의 '가입연령' 줄은 B2 청크 C002의 마지막 줄이라 잘림 규칙으로 내보내지 않는다(실제 DB 응답도 null).
        last = self.by_policy["POL_GOV_046"]
        self.assertTrue(any(c["text"].strip().endswith("가입연령 : 신청당시 만 15세~만 39세 이하") for c in last))
        self.assertIsNone(self.rows("POL_GOV_046"))

    def test_only_adopted_document_is_used(self):
        # POL_009: 기준 줄은 DOC_MOLIT_002에만 있다. DOC_MOLIT_001 청크만 채택되면 null이다(정책 내 문서도 섞지 않음).
        first_doc = {c["chunk_id"] for c in self.by_policy["POL_009"] if c["document_id"] == "DOC_MOLIT_001"}
        self.assertIsNone(self.rows("POL_009", first_doc))
        # 2026-11-26 R3D 실제 상담의 채택 청크(상위 5개). 기준 줄이 있는 DOC_MOLIT_002 C002·C004는 채택되지 않았다.
        live_adopted = {"CHK_B2_DOC_MOLIT_001_C002", "CHK_B2_DOC_MOLIT_001_C004", "CHK_B2_DOC_MOLIT_001_C005",
                        "CHK_B2_DOC_MOLIT_001_C007", "CHK_B2_DOC_MOLIT_002_C007"}
        self.assertIsNone(self.rows("POL_009", live_adopted))
        rows = self.rows("POL_009")
        self.assertTrue(rows and all(r["document_id"] == "DOC_MOLIT_002" for r in rows))


if __name__ == "__main__":
    unittest.main()
