"""
임베딩·Qdrant 최소 파이프라인 단위 테스트 — 실제 Ollama·Qdrant 서버에 접속하지 않는다.

실행 방법 (프로젝트 루트에서):
    python -m unittest tests/unit/test_vector_store.py -v

구성
1) 임베딩 입력 문자열·질의 문자열 (가상 청크, sample_only)
2) Ollama 호출 함수 — 가짜 HTTP 함수로 배치·응답 수·차원·오류 처리 확인 (네트워크 없음)
3) 정책 메타데이터 조인, point 생성(ID·payload) — 실제 문서 5건 + 가짜 벡터
4) 컬렉션 이름 보호 — 클라이언트 없이 거부되는지 확인
5) B1/B2 결과 불변
6) Qdrant 메모리 모드 — qdrant-client가 설치된 경우에만 실행, 없으면 건너뜀(skip = NOT_TESTED)
"""

import hashlib
import importlib.util
import json
import math
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import chunking as ck  # noqa: E402
from database import embedding as em  # noqa: E402
from database import handoff_data as hd  # noqa: E402
from database import vector_index as vi  # noqa: E402
from database.qdrant import store  # noqa: E402

QDRANT_CLIENT_AVAILABLE = importlib.util.find_spec("qdrant_client") is not None
TEST_COLLECTION = "exp_unittest_fake_v1"
FAKE_DIM = 16

# B1/B2 출력 해시 (test_chunking.py와 같은 값, 2026-11-05 기록)
B1_OUTPUT_SHA256 = ("13b7927e8d0780098ca72367befe3dc824afefca324d12484c015e996055d2c7",
                    "91d9753889ba15af748bba2cc01bad2b5a6a10945883147d1bd8c3bbe3016fe1")
B2_OUTPUT_SHA256 = ("534d0275b29498998c84db7d7d1a86e4429acad17cc39221a0c1f86f9dfae67a",
                    "6dbf5cf2e3b9d5e10c8f2390c3fa99b39d8762ada0ec0071c38bea451c7e9916")


def fake_embedder(dim: int = FAKE_DIM):
    """
    가짜 임베딩 함수 (sample_only). 같은 문자열 → 항상 같은 벡터. 의미를 반영하지 않으므로 검색 품질과 무관하다.
    sha256 바이트를 숫자로 바꿔 dim개를 만든 뒤 길이 1로 맞춘다.
    """
    def embed(texts):
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            raw = [(digest[i % len(digest)] - 127.5) / 127.5 + 0.001 * i for i in range(dim)]
            norm = math.sqrt(sum(value * value for value in raw))
            vectors.append([value / norm for value in raw])
        return vectors
    return embed


def fake_run_info(rule: str = em.RULE_POLICY_SECTION_BODY, dim: int = FAKE_DIM) -> dict:
    return vi.make_run_info(embedding_model="fake-embedding-sample-only", embedding_dim=dim,
                            embedding_input_rule=rule, index_run_id="RUN_UNITTEST_SAMPLE_ONLY")


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code, self._body, self.text = status_code, body, text

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


# ---------------------------------------------------------------------------
# 1) 임베딩 입력 문자열 (sample_only)
# ---------------------------------------------------------------------------
class TestEmbeddingInput(unittest.TestCase):
    CHUNK = {"chunk_id": "CHK_SAMPLE_ONLY", "section_title": "[지원내용]", "text": "[지원내용] 가상 월 10만원 (sample_only)"}
    META = {"policy_name": "가상 청년 정책 (sample_only)"}

    def test_policy_section_body_rule(self):
        text = em.build_embedding_text(self.CHUNK, self.META, em.RULE_POLICY_SECTION_BODY)
        self.assertEqual(text, "가상 청년 정책 (sample_only)\n[지원내용]\n[지원내용] 가상 월 10만원 (sample_only)")

    def test_body_only_rule_and_chunk_text_unchanged(self):
        before = dict(self.CHUNK)
        self.assertEqual(em.build_embedding_text(self.CHUNK, None, em.RULE_BODY_ONLY), self.CHUNK["text"])
        em.build_embedding_text(self.CHUNK, self.META, em.RULE_POLICY_SECTION_BODY)
        self.assertEqual(self.CHUNK, before)

    def test_empty_parts_are_skipped_and_errors(self):
        chunk = {**self.CHUNK, "section_title": None}
        self.assertEqual(em.build_embedding_text(chunk, {"policy_name": ""}, em.RULE_POLICY_SECTION_BODY), chunk["text"])
        with self.assertRaises(ValueError):
            em.build_embedding_text(self.CHUNK, None, em.RULE_POLICY_SECTION_BODY)
        with self.assertRaises(ValueError):
            em.build_embedding_text(self.CHUNK, self.META, "unknown_rule")

    def test_query_text_requires_explicit_choice(self):
        self.assertEqual(em.build_query_text("가상 질문?", None), "가상 질문?")
        self.assertEqual(em.build_query_text("가상 질문?", "Q: {question}"), "Q: 가상 질문?")
        with self.assertRaises(ValueError):
            em.build_query_text("가상 질문?", "지시문만 있음")
        with self.assertRaises(TypeError):
            vi.run_smoke_search([], client=None, collection=TEST_COLLECTION, embedder=fake_embedder())  # 템플릿 누락


class TestEmbeddingInputV2(unittest.TestCase):
    """policy_section_body_v2: 본문에 이미 있는 정책명·섹션명 줄을 덧붙이지 않는다. (가상 청크 sample_only)"""
    V2 = em.RULE_POLICY_SECTION_BODY_V2

    def _text(self, title, body, policy_name="가상 돌봄 정책"):
        chunk = {"chunk_id": "CHK_SAMPLE_ONLY", "section_title": title, "text": body}
        return em.build_embedding_text(chunk, {"policy_name": policy_name}, self.V2)

    def test_section_title_already_at_line_start_is_not_repeated(self):
        body = "[구비서류]\n- 가상 신청서 (sample_only)\n[문의처]\n가상 콜센터"
        self.assertEqual(self._text("[구비서류] + [문의처]", body), "가상 돌봄 정책\n" + body)

    def test_merged_title_keeps_only_elements_missing_from_body(self):
        # B2 병합·긴 필드 분할: 제목 [지원대상 상세] + [선정기준] 이지만 본문은 [선정기준]부터 시작하는 조각
        body = "[선정기준]\n가상 연령 기준 (sample_only)"
        self.assertEqual(self._text("[지원대상 상세] + [선정기준]", body), "가상 돌봄 정책\n[지원대상 상세]\n" + body)

    def test_title_path_keeps_parent_chapter(self):
        body = "■ 임대기간\n가상 2년 (sample_only)"
        self.assertEqual(self._text("1 공급개요 > ■ 임대기간", body), "가상 돌봄 정책\n1 공급개요\n" + body)

    def test_placeholder_and_continuation_titles(self):
        self.assertEqual(self._text("(첫 제목 이전)", "가상 머리글 (sample_only)"), "가상 돌봄 정책\n가상 머리글 (sample_only)")
        # (이어짐) 조각: 본문에 제목 줄이 없으므로 제목은 유지하되 "(이어짐)" 꼬리는 떼어 낸다.
        self.assertEqual(self._text("1 공급개요 > ■ 임대기간 (이어짐)", "가상 이어지는 내용"),
                         "가상 돌봄 정책\n1 공급개요 > ■ 임대기간\n가상 이어지는 내용")

    def test_multiline_title_and_line_start_helper(self):
        # B1 HTML 번호 제목은 줄바꿈을 포함할 수 있다. 예) "1.\n혼인 요건" (DOC_NHUF_001 실측 형태)
        body = "1.\n혼인 요건\n:\n가상 신혼가구 (sample_only)"
        self.assertEqual(self._text("1.\n혼인 요건", body), "가상 돌봄 정책\n" + body)
        self.assertTrue(em.starts_a_line("가상\n  ■ 제목", "■ 제목"))
        self.assertFalse(em.starts_a_line("가상 ■ 제목", "■ 제목"))

    def test_title_inside_sentence_is_not_treated_as_present(self):
        # 제목 낱말이 문장 중간에만 있으면 "제목 줄"이 아니므로 제목을 유지한다. (단순 부분 문자열로 판단하지 않음)
        body = "가상 안내: [구비서류] 항목을 참고하세요 (sample_only)"
        self.assertEqual(self._text("[구비서류]", body), "가상 돌봄 정책\n[구비서류]\n" + body)

    def test_policy_name_omitted_only_when_declared(self):
        declared = "[서비스명] 가상 돌봄 정책 (WLF_SAMPLE_ONLY)\n[지원내용]\n가상 지원"
        self.assertEqual(self._text("[서비스명] + [지원내용]", declared), declared)
        declared_yc = "[정책명] 가상 돌봄 정책 (정책번호: SAMPLE_ONLY)"
        self.assertEqual(self._text("[정책명]", declared_yc), declared_yc)
        mentioned = "[지원내용]\n가상 돌봄 정책 이용 시 가상 지원 (sample_only)"
        self.assertEqual(self._text("[지원내용]", mentioned), "가상 돌봄 정책\n" + mentioned)

    def test_v1_and_body_only_unchanged(self):
        chunk = {"chunk_id": "CHK_SAMPLE_ONLY", "section_title": "[구비서류]", "text": "[구비서류]\n가상 신청서"}
        meta = {"policy_name": "가상 돌봄 정책"}
        self.assertEqual(em.build_embedding_text(chunk, meta, em.RULE_POLICY_SECTION_BODY),
                         "가상 돌봄 정책\n[구비서류]\n[구비서류]\n가상 신청서")
        self.assertEqual(em.build_embedding_text(chunk, None, em.RULE_BODY_ONLY), chunk["text"])

    def test_describe_matches_build(self):
        chunk = {"chunk_id": "CHK_SAMPLE_ONLY", "section_title": "[구비서류] + [문의처]", "text": "[구비서류]\n가상"}
        meta = {"policy_name": "가상 돌봄 정책"}
        described = em.describe_embedding_input(chunk, meta, self.V2)
        self.assertEqual(described["text"], em.build_embedding_text(chunk, meta, self.V2))
        self.assertEqual(described["omitted_title_elements"], ["[구비서류]"])
        self.assertEqual(described["section_line"], "[문의처]")
        self.assertFalse(described["policy_name_omitted"])


class TestEmbeddingInputV2RealChunks(unittest.TestCase):
    """실제 A/B1/B2 청크 전체에 v2 규칙을 적용했을 때의 불변 조건."""

    @classmethod
    def setUpClass(cls):
        documents = hd.load_documents()
        payload_chunks = hd.load_payload_chunks()
        cls.meta = hd.load_policy_metadata(payload_chunks)
        cls.chunks = {strategy: vi.prepare_chunks(documents, strategy, payload_chunks) for strategy in vi.STRATEGIES}

    def test_body_is_kept_verbatim_at_the_end_and_chunk_unchanged(self):
        for strategy, chunks in self.chunks.items():
            for chunk in chunks:
                before = json.dumps(chunk, ensure_ascii=False, sort_keys=True)
                text = em.build_embedding_text(chunk, self.meta[chunk["policy_id"]], em.RULE_POLICY_SECTION_BODY_V2)
                with self.subTest(strategy=strategy, chunk=chunk["chunk_id"]):
                    self.assertTrue(text.endswith(chunk["text"]))
                    self.assertEqual(json.dumps(chunk, ensure_ascii=False, sort_keys=True), before)

    def test_v2_never_longer_than_v1_and_omits_only_header_parts(self):
        for strategy, chunks in self.chunks.items():
            for chunk in chunks:
                meta = self.meta[chunk["policy_id"]]
                v1 = em.describe_embedding_input(chunk, meta, em.RULE_POLICY_SECTION_BODY)
                v2 = em.describe_embedding_input(chunk, meta, em.RULE_POLICY_SECTION_BODY_V2)
                with self.subTest(strategy=strategy, chunk=chunk["chunk_id"]):
                    self.assertLessEqual(len(v2["text"]), len(v1["text"]))
                    if v2["policy_name_omitted"]:
                        self.assertTrue(em.policy_name_declared(chunk["text"], meta["policy_name"]))
                    else:
                        self.assertEqual(v2["policy_name_line"], v1["policy_name_line"])

    def test_b2_repeated_section_titles_are_removed(self):
        # 수정 전: B2 청크 899개(83.2%)에서 본문이 섹션 제목으로 시작해 제목이 두 번 들어갔다. (2026-11-05 측정)
        repeated = 0
        for chunk in self.chunks["B2"]:
            v2 = em.describe_embedding_input(chunk, self.meta[chunk["policy_id"]], em.RULE_POLICY_SECTION_BODY_V2)
            line = v2["section_line"]
            if line and any(part and chunk["text"].lstrip().startswith(part)
                            for part in line.replace(em.TITLE_PATH_SEPARATOR, em.TITLE_GROUP_SEPARATOR)
                            .split(em.TITLE_GROUP_SEPARATOR)):
                repeated += 1
        self.assertEqual(repeated, 0)


# ---------------------------------------------------------------------------
# 2) Ollama 호출 (가짜 HTTP 함수, 네트워크 없음)
# ---------------------------------------------------------------------------
class TestOllamaEmbedWithFakeHttp(unittest.TestCase):
    def _post_returning(self, dim=em.DEFAULT_EMBEDDING_DIM, extra=0, calls=None):
        def post(url, json, timeout):
            if calls is not None:
                calls.append({"url": url, "json": json, "timeout": timeout})
            return FakeResponse(body={"embeddings": [[0.1] * dim for _ in range(len(json["input"]) + extra)]})
        return post

    def test_batches_model_name_and_dimension(self):
        calls = []
        embed = em.make_ollama_embedder(model=" qwen3-embedding:0.6b ", base_url="http://ollama.invalid/",
                                        batch_size=2, http_post=self._post_returning(calls=calls))
        vectors = embed([f"가상 문장 {i}" for i in range(5)])
        self.assertEqual(len(vectors), 5)
        self.assertTrue(all(len(vector) == 1024 for vector in vectors))
        self.assertEqual([len(call["json"]["input"]) for call in calls], [2, 2, 1])
        self.assertEqual(calls[0]["url"], "http://ollama.invalid/api/embed")
        self.assertEqual(calls[0]["json"]["model"], "qwen3-embedding:0.6b")

    def test_request_sends_truncate_false(self):
        calls = []
        em.ollama_embed_batch(["가상 문장"], base_url="http://x.invalid", http_post=self._post_returning(calls=calls))
        self.assertIs(calls[0]["json"]["truncate"], False)
        self.assertEqual(set(calls[0]["json"]), {"model", "input", "truncate"})

    def test_metrics_sink_collects_ollama_measurements_without_changing_return_type(self):
        def post(url, json, timeout):
            return FakeResponse(body={"model": "qwen3-embedding:0.6b",
                                      "embeddings": [[0.1] * 1024 for _ in json["input"]],
                                      "total_duration": 2_000_000_000, "load_duration": 500_000_000,
                                      "prompt_eval_count": 7 * len(json["input"])})
        metrics = []
        vectors = em.make_ollama_embedder(base_url="http://x.invalid", batch_size=2, http_post=post,
                                          metrics_sink=metrics)(["a", "b", "c"])
        self.assertIsInstance(vectors, list)
        self.assertEqual(len(vectors), 3)
        self.assertEqual([item["input_count"] for item in metrics], [2, 1])
        summary = em.summarize_ollama_metrics(metrics)
        self.assertEqual(summary, {"requests": 2, "inputs": 3, "total_duration_seconds": 4.0,
                                   "load_duration_seconds": 1.0, "prompt_eval_count": 21})

    def test_missing_metrics_are_none_not_guessed(self):
        metrics = []
        em.ollama_embed_batch(["a"], base_url="http://x.invalid", http_post=self._post_returning(),
                              metrics_sink=metrics)
        self.assertIsNone(metrics[0]["total_duration"])
        self.assertIsNone(em.summarize_ollama_metrics(metrics)["total_duration_seconds"])

    def test_ollama_url_priority_argument_env_dotenv_default(self):
        # 실제 .env를 읽지 않도록 가짜 .env 경로만 쓴다. 환경 변수는 테스트 후 원래대로 돌아온다.
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ):
            os.environ.pop("OLLAMA_BASE_URL", None)
            env_path = Path(folder) / ".env"
            missing_path = Path(folder) / "none.env"
            # ④ 설정이 없으면 기존 기본값
            self.assertEqual(em.resolve_ollama_base_url(env_path=missing_path), em.DEFAULT_OLLAMA_BASE_URL)
            # ③ .env 값
            env_path.write_text("OLLAMA_BASE_URL=http://dotenv.sample-only.invalid:11434/\n", encoding="utf-8")
            self.assertEqual(em.resolve_ollama_base_url(env_path=env_path), "http://dotenv.sample-only.invalid:11434")
            # ② 이미 있는 환경 변수는 .env가 덮어쓰지 않는다
            os.environ["OLLAMA_BASE_URL"] = "http://system.sample-only.invalid:11434"
            self.assertEqual(em.resolve_ollama_base_url(env_path=env_path), "http://system.sample-only.invalid:11434")
            # ① 인자가 가장 우선
            self.assertEqual(em.resolve_ollama_base_url(" http://arg.invalid:1/ ", env_path=env_path), "http://arg.invalid:1")

    def test_embedder_without_base_url_uses_environment_at_call_time(self):
        calls = []
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ), \
                mock.patch.object(em, "DEFAULT_ENV_PATH", Path(folder) / "none.env"):
            embed = em.make_ollama_embedder(http_post=self._post_returning(calls=calls))   # base_url 생략
            os.environ["OLLAMA_BASE_URL"] = "http://later.sample-only.invalid:11434"     # 만든 뒤에 설정해도 반영
            embed(["가상 문장"])
        self.assertEqual(calls[0]["url"], "http://later.sample-only.invalid:11434/api/embed")

    def test_empty_input_makes_no_request(self):
        calls = []
        self.assertEqual(em.make_ollama_embedder(http_post=self._post_returning(calls=calls))([]), [])
        self.assertEqual(calls, [])

    def test_response_count_and_dimension_are_checked(self):
        with self.assertRaisesRegex(em.EmbeddingError, "개수 불일치"):
            em.ollama_embed_batch(["a", "b"], base_url="http://x.invalid", http_post=self._post_returning(extra=1))
        with self.assertRaisesRegex(em.EmbeddingError, "차원 불일치"):
            em.ollama_embed_batch(["a"], base_url="http://x.invalid", http_post=self._post_returning(dim=768))
        with self.assertRaisesRegex(em.EmbeddingError, "NaN"):
            em.validate_vectors([[float("nan")] * 3], expected_count=1, expected_dim=3)

    def test_timeout_connection_status_and_format_errors(self):
        def raise_(error):
            def post(url, json, timeout):
                raise error
            return post
        cases = [
            (raise_(requests.Timeout()), "시간 초과"),
            (raise_(requests.ConnectionError()), "연결 실패"),
            (lambda url, json, timeout: FakeResponse(status_code=404, text="model not found"), "응답 오류 404"),
            (lambda url, json, timeout: FakeResponse(body=ValueError("bad json")), "JSON이 아님"),
            (lambda url, json, timeout: FakeResponse(body={"error": "x"}), "embeddings 필드가 없음"),
        ]
        for post, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(em.EmbeddingError, message):
                    em.ollama_embed_batch(["a"], base_url="http://x.invalid", http_post=post)

    def test_fake_embedder_dimension_and_determinism(self):
        embed = fake_embedder()
        first, second = embed(["가상 문장"]), embed(["가상 문장"])
        self.assertEqual(first, second)
        self.assertEqual(len(first[0]), FAKE_DIM)
        self.assertAlmostEqual(em.vector_norm(first[0]), 1.0, places=6)
        em.validate_vectors(first, expected_count=1, expected_dim=FAKE_DIM)


# ---------------------------------------------------------------------------
# 3) 정책 메타데이터 조인·point 생성 (실제 문서 5건 + 가짜 벡터)
# ---------------------------------------------------------------------------
class TestPointsFromRealChunks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = hd.load_documents()
        cls.payload_chunks = hd.load_payload_chunks()
        cls.policy_metadata = hd.load_policy_metadata(cls.payload_chunks)
        cls.selected = vi.select_documents(cls.documents, vi.DEFAULT_SAMPLE_DOCUMENT_IDS)
        cls.by_id = {document["document_id"]: document for document in cls.selected}
        cls.hashes = {document["document_id"]: document["content_hash"] for document in cls.selected}
        cls.chunks = {strategy: vi.prepare_chunks(cls.selected, strategy, cls.payload_chunks)
                      for strategy in vi.STRATEGIES}

    def _points(self, strategy, rule=em.RULE_POLICY_SECTION_BODY):
        chunks = self.chunks[strategy]
        texts = [em.build_embedding_text(chunk, self.policy_metadata[chunk["policy_id"]], rule) for chunk in chunks]
        return store.build_points(chunks, fake_embedder()(texts), policy_metadata=self.policy_metadata,
                                  content_hashes=self.hashes, run_info=fake_run_info(rule))

    def test_policy_metadata_covers_all_documents_and_detects_conflict(self):
        self.assertEqual(len(self.policy_metadata), hd.EXPECTED_POLICY_COUNT)
        self.assertTrue(all(document["policy_id"] in self.policy_metadata for document in self.documents))
        conflict = [dict(self.payload_chunks[0]), {**self.payload_chunks[0], "category": "가상 다른 값 (sample_only)"}]
        with self.assertRaises(ValueError):
            hd.load_policy_metadata(conflict)

    def test_select_documents_rejects_unknown_id(self):
        with self.assertRaises(ValueError):
            vi.select_documents(self.documents, ["DOC_DOES_NOT_EXIST"])

    def test_chunk_counts_per_strategy(self):
        # 2026-11-05 계획 보고에서 측정한 값과 같아야 한다.
        self.assertEqual(len(self.chunks["A"]), 69)
        self.assertEqual(len(self.chunks["B1"]), 110)
        self.assertEqual(len(self.chunks["B2"]), 107)

    def test_payload_has_required_fields_and_joined_metadata(self):
        for strategy in vi.STRATEGIES:
            for point in self._points(strategy):
                payload = point["payload"]
                with self.subTest(strategy=strategy, chunk=payload["chunk_id"]):
                    self.assertEqual(set(store.REQUIRED_PAYLOAD_FIELDS) - set(payload), set())
                    meta = self.policy_metadata[payload["policy_id"]]
                    for field in hd.POLICY_METADATA_FIELDS:
                        self.assertEqual(payload[field], meta[field])
                    self.assertEqual(payload["content_hash"], self.hashes[payload["document_id"]])
                    self.assertEqual(payload["chunking_version"], strategy)
                    self.assertEqual(payload["embedding_dim"], FAKE_DIM)
                    self.assertEqual(len(point["vector"]), FAKE_DIM)

    def test_point_id_is_uuid5_of_chunk_id_unique_and_deterministic(self):
        all_ids = []
        for strategy in vi.STRATEGIES:
            points = self._points(strategy)
            ids = [point["id"] for point in points]
            with self.subTest(strategy=strategy):
                self.assertEqual(len(ids), len(set(ids)))
                for point in points:
                    self.assertEqual(point["id"], str(uuid.uuid5(uuid.NAMESPACE_DNS, point["payload"]["chunk_id"])))
                    self.assertEqual(point["id"], point["payload"]["point_id"])
                self.assertEqual(points, self._points(strategy))
            all_ids.extend(ids)
        self.assertEqual(len(all_ids), len(set(all_ids)), "A/B1/B2 point ID가 서로 겹치면 안 된다")

    def test_a_chunks_keep_handoff_point_id(self):
        for chunk in self.chunks["A"]:
            self.assertEqual(ck.make_point_id(chunk["chunk_id"]), chunk["point_id"])

    def test_payload_text_is_original_slice(self):
        for strategy in vi.STRATEGIES:
            for point in self._points(strategy):
                payload = point["payload"]
                with self.subTest(strategy=strategy, chunk=payload["chunk_id"]):
                    self.assertIsNotNone(payload["char_start"])
                    original = self.by_id[payload["document_id"]]["text"]
                    self.assertEqual(original[payload["char_start"]:payload["char_end"]], payload["text"])

    def test_embedding_input_rules_differ_but_payload_text_is_same(self):
        with_meta = self._points("B2", em.RULE_POLICY_SECTION_BODY)
        body_only = self._points("B2", em.RULE_BODY_ONLY)
        self.assertEqual([p["payload"]["text"] for p in with_meta], [p["payload"]["text"] for p in body_only])
        self.assertNotEqual([p["vector"] for p in with_meta], [p["vector"] for p in body_only])
        self.assertEqual({p["payload"]["embedding_input_rule"] for p in body_only}, {em.RULE_BODY_ONLY})

    def test_build_points_rejects_mismatches(self):
        chunks = self.chunks["B2"][:2]
        arguments = dict(policy_metadata=self.policy_metadata, content_hashes=self.hashes, run_info=fake_run_info())
        with self.assertRaisesRegex(ValueError, "수가 다릅니다"):
            store.build_points(chunks, fake_embedder()(["x"]), **arguments)
        with self.assertRaisesRegex(ValueError, "차원"):
            store.build_points(chunks, fake_embedder(dim=8)(["x", "y"]), **arguments)
        with self.assertRaisesRegex(ValueError, "중복"):
            store.build_points([chunks[0], chunks[0]], fake_embedder()(["x", "y"]), **arguments)
        with self.assertRaisesRegex(ValueError, "run_info"):
            store.build_point_payload(chunks[0], policy_meta=self.policy_metadata[chunks[0]["policy_id"]],
                                      content_hash="x", run_info={})


# ---------------------------------------------------------------------------
# 4) 컬렉션 이름 보호 (클라이언트 없이 확인)
# ---------------------------------------------------------------------------
class TestCollectionGuard(unittest.TestCase):
    def test_experiment_names_only(self):
        self.assertEqual(store.assert_experiment_collection("exp_b2_qwen3emb06b_v1"), "exp_b2_qwen3emb06b_v1")
        for name in ("lifepolicy_chunks", "advanced_rag", "exp_", "EXP_B2", "exp_b2-qwen", "", None):
            with self.subTest(name=name):
                with self.assertRaises(store.CollectionGuardError):
                    store.assert_experiment_collection(name)

    def test_every_db_function_rejects_protected_collection_before_using_client(self):
        # client=None 이므로, 이름 검사보다 먼저 클라이언트를 쓰면 AttributeError가 난다 → 순서를 함께 확인한다.
        calls = [
            lambda: store.ensure_collection(None, "lifepolicy_chunks", 1024),
            lambda: store.upsert_points(None, "lifepolicy_chunks", []),
            lambda: store.count_points(None, "lifepolicy_chunks"),
            lambda: store.list_point_ids(None, "lifepolicy_chunks"),
            lambda: store.search_chunks(None, "lifepolicy_chunks", [0.0] * 1024),
            lambda: vi.index_chunks([], client=None, collection="lifepolicy_chunks", embedder=fake_embedder(),
                                    policy_metadata={}, content_hashes={}, run_info=fake_run_info()),
            lambda: vi.run_minimal_index(["DOC_YC_003"], strategy="B2", collection="lifepolicy_chunks",
                                         client=None, embedder=fake_embedder(), run_info=fake_run_info(),
                                         documents=[], payload_chunks=[]),
            lambda: vi.run_smoke_search([], client=None, collection="lifepolicy_chunks",
                                        embedder=fake_embedder(), query_template=None),
        ]
        for index, call in enumerate(calls):
            with self.subTest(call=index):
                with self.assertRaises(store.CollectionGuardError):
                    call()

    def test_no_delete_function_exists(self):
        names = [name for name in dir(store) if "delete" in name.lower() or "drop" in name.lower()]
        self.assertEqual(names, [])


# ---------------------------------------------------------------------------
# 4-1) .env 자동 로딩 — 가짜 값만 사용 (실제 .env·비밀값을 읽지 않는다)
# ---------------------------------------------------------------------------
DOTENV_AVAILABLE = importlib.util.find_spec("dotenv") is not None
FAKE_URL = "http://qdrant.sample-only.invalid:6333"
FAKE_KEY = "fake-key-sample-only-0000"


@unittest.skipUnless(DOTENV_AVAILABLE, "python-dotenv 미설치 → NOT_TESTED")
class TestEnvLoading(unittest.TestCase):
    def setUp(self):
        # 테스트가 끝나면 os.environ이 원래대로 돌아온다. (load_dotenv가 넣은 값도 함께 사라진다)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in store.REQUIRED_CONNECTION_VARIABLES:
            os.environ.pop(name, None)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.env_path = Path(self.temp_dir.name) / ".env"

    def _write_env(self, url=FAKE_URL, key=FAKE_KEY):
        self.env_path.write_text(f"QDRANT_URL={url}\nQDRANT_API_KEY={key}\n", encoding="utf-8")

    def test_values_are_loaded_from_env_file(self):
        self._write_env()
        self.assertTrue(store.load_project_env(self.env_path))
        self.assertEqual(store.resolve_connection_settings(env_path=self.env_path), (FAKE_URL, FAKE_KEY))

    def test_existing_environment_variables_are_not_overwritten(self):
        self._write_env()
        os.environ["QDRANT_URL"] = "http://already-set.sample-only.invalid:6333"
        url, key = store.resolve_connection_settings(env_path=self.env_path)
        self.assertEqual(url, "http://already-set.sample-only.invalid:6333")
        self.assertEqual(key, FAKE_KEY)

    def test_function_arguments_take_priority(self):
        self._write_env()
        self.assertEqual(store.resolve_connection_settings("http://arg.invalid", "arg-key", env_path=self.env_path),
                         ("http://arg.invalid", "arg-key"))

    def test_missing_env_file_gives_clear_error(self):
        self.assertFalse(store.load_project_env(self.env_path))
        with self.assertRaisesRegex(store.ConnectionSettingsError, "파일이 없습니다"):
            store.resolve_connection_settings(env_path=self.env_path)

    def test_empty_value_gives_clear_error_without_secret(self):
        self._write_env(key="")
        with self.assertRaises(store.ConnectionSettingsError) as context:
            store.resolve_connection_settings(env_path=self.env_path)
        message = str(context.exception)
        self.assertIn("QDRANT_API_KEY", message)
        self.assertIn("비어 있습니다", message)
        self.assertNotIn(FAKE_URL, message)

    def test_error_message_never_contains_secret_value(self):
        self._write_env(url="")
        with self.assertRaises(store.ConnectionSettingsError) as context:
            store.resolve_connection_settings(env_path=self.env_path)
        self.assertNotIn(FAKE_KEY, str(context.exception))

    def test_memory_mode_does_not_need_env(self):
        if not QDRANT_CLIENT_AVAILABLE:
            self.skipTest("qdrant-client 미설치")
        with mock.patch.object(store, "load_project_env", side_effect=AssertionError("읽으면 안 됨")):
            store.get_client(location=":memory:")


# ---------------------------------------------------------------------------
# 5) B1/B2 결과 불변
# ---------------------------------------------------------------------------
class TestChunkingUnchanged(unittest.TestCase):
    def test_b1_b2_outputs_unchanged(self):
        documents = hd.load_documents()
        for strategy, expected in (("B1", B1_OUTPUT_SHA256), ("B2", B2_OUTPUT_SHA256)):
            chunks, reports = ck.chunk_documents(documents, strategy=strategy)
            actual = tuple(hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                           for value in (chunks, reports))
            with self.subTest(strategy=strategy):
                self.assertEqual(actual, expected)


# ---------------------------------------------------------------------------
# 6) Qdrant 메모리 모드 (qdrant-client 필요 — 설치 전에는 skip = NOT_TESTED)
# ---------------------------------------------------------------------------
@unittest.skipUnless(QDRANT_CLIENT_AVAILABLE, "qdrant-client 미설치 → NOT_TESTED (설치는 사용자 승인 후)")
class TestQdrantMemoryMode(unittest.TestCase):
    def setUp(self):
        self.client = store.get_client(location=":memory:")
        self.documents = hd.load_documents()
        self.payload_chunks = hd.load_payload_chunks()

    def _index(self, strategy="B2", document_ids=("DOC_YC_003", "DOC_BOK_001")):
        return vi.run_minimal_index(list(document_ids), strategy=strategy, collection=TEST_COLLECTION,
                                    client=self.client, embedder=fake_embedder(), run_info=fake_run_info(),
                                    documents=self.documents, payload_chunks=self.payload_chunks)

    def test_collection_created_then_exists_and_dimension_mismatch_is_refused(self):
        self.assertEqual(store.ensure_collection(self.client, TEST_COLLECTION, FAKE_DIM), "created")
        self.assertEqual(store.ensure_collection(self.client, TEST_COLLECTION, FAKE_DIM), "exists")
        with self.assertRaises(store.CollectionGuardError):
            store.ensure_collection(self.client, TEST_COLLECTION, 1024)

    def test_reindex_same_data_creates_no_duplicates(self):
        report = self._index()
        self.assertEqual(report["points_before"], 0)
        self.assertEqual(report["points_after_first"], report["chunk_count"])
        self.assertEqual(report["points_after_second"], report["chunk_count"])
        self.assertTrue(report["id_set_unchanged"])
        self.assertFalse(report["duplicates_found"])

    def test_search_returns_ids_score_and_original_text(self):
        self._index()
        # 가짜 임베딩은 같은 문자열이면 같은 벡터 → 적재한 임베딩 입력을 그대로 질문으로 쓰면 그 청크가 1위여야 한다.
        policy_metadata = hd.load_policy_metadata(self.payload_chunks)
        target = vi.prepare_chunks(vi.select_documents(self.documents, ["DOC_YC_003"]), "B2")[0]
        query = em.build_embedding_text(target, policy_metadata[target["policy_id"]], em.RULE_POLICY_SECTION_BODY)
        results = vi.run_smoke_search([{"question": query, "expected_document_id": "DOC_YC_003"}],
                                      client=self.client, collection=TEST_COLLECTION, embedder=fake_embedder(),
                                      query_template=None, documents=self.documents)
        hits = results[0]["hits"]
        self.assertEqual(len(hits), 5)
        self.assertEqual(hits[0]["chunk_id"], target["chunk_id"])
        self.assertTrue(results[0]["expected_in_top_k"])
        for hit in hits:
            self.assertTrue(hit["policy_id"] and hit["document_id"] and hit["chunk_id"])
            self.assertIsInstance(hit["score"], float)
            self.assertTrue(hit["text_matches_original"])

    def test_filter_by_document_id(self):
        self._index()
        hits = store.search_chunks(self.client, TEST_COLLECTION, fake_embedder()(["가상 질문"])[0], k=10,
                                   filters={"document_id": "DOC_YC_003"})
        self.assertTrue(hits)
        self.assertEqual({hit["document_id"] for hit in hits}, {"DOC_YC_003"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
