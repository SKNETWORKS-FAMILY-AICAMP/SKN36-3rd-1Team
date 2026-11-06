"""
Hybrid 검색 프로필·데이터 품질 안전장치 단위 테스트 — Qdrant 메모리 모드, 실제 서버·Ollama 없이 실행

실행 방법 (프로젝트 루트에서):
    .venv\\Scripts\\python.exe -m unittest tests/unit/test_hybrid_profiles.py -v

검사 내용
- 프로필 값이 색인 구성(full_index)·평가 지시문(retrieval_run)과 같은지, 기본 프로필(0.6B 전체 색인)
- embedder 계약: 모델·차원 표시가 없거나 다르면 거부, 표시를 속여도 실제 벡터 차원 검사로 검색 전에 거부
- 지시문 강제, 컬렉션 차원 불일치 시 임베딩·검색 전에 거부, profile+collection 동시 지정 거부
- 새 경고(빈 NOT 그룹, 지역 단위 손실 의심, 검토 상태 과대 표기 의심)와 정적 목록(적용/stale/missing)
- 판정 보류(hold_auto_eligibility)는 판정이 아니며 eligibility.evaluated=False는 그대로
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import data_quality_flags as dqf  # noqa: E402
from database import full_index as fi  # noqa: E402
from database import hybrid_search as hs  # noqa: E402
from database import retrieval_run as rr  # noqa: E402
from database.chunking import make_point_id  # noqa: E402
from database.qdrant import store  # noqa: E402


def setUpModule():
    import logging
    logging.getLogger().setLevel(logging.ERROR)


def make_embedder(model, dim, *, declare=True, actual_dim=None, calls=None):
    """가짜 embedder (sample_only). declare=False면 모델·차원 표시가 없다. actual_dim으로 실제 차원을 속일 수 있다."""
    def embed(texts):
        if calls is not None:
            calls.append(list(texts))
        vector = [0.0] * (actual_dim or dim)
        vector[0] = 1.0
        return [vector for _ in texts]
    if declare:
        embed.embedding_model, embed.embedding_dim = model, dim
    return embed


def memory_collection(client, name, dim):
    store.ensure_collection(client, name, dim)
    vector = [0.0] * dim
    vector[0] = 1.0
    store.upsert_points(client, name, [{"id": make_point_id("CHK_B2_DOC_X_C001"), "vector": vector, "payload": {
        "chunk_id": "CHK_B2_DOC_X_C001", "document_id": "DOC_X", "policy_id": "POL_X", "text": "가상 (sample_only)",
        "char_start": 0, "char_end": 2, "embedding_model": "sample"}}])


A, B = "b2_06b_baseline", "b2_4b_instruct"


class TestProfileDefinitions(unittest.TestCase):
    def test_profiles_match_index_configs_and_evaluated_instruction(self):
        a, b = hs.resolve_search_profile(A), hs.resolve_search_profile(B)
        self.assertEqual((a["collection"], a["embedding_model"], a["embedding_dim"], a["query_template"]),
                         ("exp_b2_qwen3emb06b_full_v1", "qwen3-embedding:0.6b", 1024, None))
        self.assertEqual((b["collection"], b["embedding_model"], b["embedding_dim"]),
                         ("exp_b2_qwen3emb4b_full_v1", "qwen3-embedding:4b", 2560))
        self.assertEqual(b["query_template"], rr.QUERY_TEMPLATES["instruct_v1"])        # 평가에 쓴 문자열 그대로
        self.assertEqual(hs.DEFAULT_PROFILE, A)
        for name in (A, B):
            config = fi.get_config(hs.SEARCH_PROFILES[name]["index_config"])
            self.assertEqual(hs.resolve_search_profile(name)["collection"], config["collection"])

    def test_unknown_profile_and_profile_with_collection_rejected(self):
        with self.assertRaises(hs.SearchProfileError):
            hs.resolve_search_profile("nope")
        with self.assertRaises(hs.SearchProfileError):
            hs.resolve_search_settings(profile=A, collection="exp_b2_qwen3emb06b_v1")
        with self.assertRaises(hs.SearchProfileError):                  # 세션도 연결을 열기 전에 거부
            hs.HybridSearchSession(profile="nope")


class TestProfileSearch(unittest.TestCase):
    def setUp(self):
        self.client = store.get_client(location=":memory:")
        memory_collection(self.client, "exp_b2_qwen3emb06b_full_v1", 1024)
        memory_collection(self.client, "exp_b2_qwen3emb4b_full_v1", 2560)

    def test_default_call_uses_baseline_profile(self):
        result = hs.search_policy_evidence("가상 질문", client=self.client,
                                           embedder=make_embedder("qwen3-embedding:0.6b", 1024))
        meta = result["search_profile"]
        self.assertEqual((meta["mode"], meta["name"], meta["collection"], meta["collection_vector_size"]),
                         ("profile", A, "exp_b2_qwen3emb06b_full_v1", 1024))
        self.assertEqual(result["collection"], "exp_b2_qwen3emb06b_full_v1")
        self.assertEqual(result["query_embedding_model"], "qwen3-embedding:0.6b")

    def test_4b_profile_forces_instruction(self):
        calls = []
        result = hs.search_policy_evidence("가상 질문", client=self.client, profile=B,
                                           embedder=make_embedder("qwen3-embedding:4b", 2560, calls=calls))
        self.assertEqual(calls[0], [rr.QUERY_TEMPLATES["instruct_v1"].format(question="가상 질문")])
        self.assertEqual(result["search_profile"]["query_template_name"], "instruct_v1")
        self.assertEqual(result["query_template"], rr.QUERY_TEMPLATES["instruct_v1"])
        with self.assertRaises(hs.SearchProfileError):                  # 다른 지시문(또는 None)은 거부
            hs.search_policy_evidence("가상 질문", client=self.client, profile=B, query_template=None,
                                      embedder=make_embedder("qwen3-embedding:4b", 2560))

    def test_embedder_contract_cannot_be_bypassed(self):
        for embedder in (make_embedder("qwen3-embedding:4b", 2560, declare=False),       # 표시 없음
                         make_embedder("qwen3-embedding:0.6b", 1024),                     # 다른 모델
                         make_embedder("qwen3-embedding:4b", 1024)):                      # 다른 차원 표시
            with self.subTest(embedder=getattr(embedder, "embedding_model", None)), \
                    self.assertRaises(hs.SearchProfileError):
                hs.search_policy_evidence("가상 질문", client=self.client, profile=B, embedder=embedder)

    def test_lying_embedder_stopped_before_search(self):
        liar = make_embedder("qwen3-embedding:4b", 2560, actual_dim=1024)              # 표시는 맞지만 실제 1024
        with mock.patch.object(hs.store, "search_chunks", wraps=hs.store.search_chunks) as spy:
            with self.assertRaises(hs.SearchProfileError):
                hs.search_policy_evidence("가상 질문", client=self.client, profile=B, embedder=liar)
            self.assertEqual(spy.call_count, 0)

    def test_collection_dimension_mismatch_stops_before_embedding(self):
        client = store.get_client(location=":memory:")
        memory_collection(client, "exp_b2_qwen3emb4b_full_v1", 1024)                   # 4B 이름인데 1024차원
        calls = []
        with self.assertRaises(hs.SearchProfileError):
            hs.search_policy_evidence("가상 질문", client=client, profile=B,
                                      embedder=make_embedder("qwen3-embedding:4b", 2560, calls=calls))
        self.assertEqual(calls, [])

    def test_custom_collection_mode_keeps_old_contract(self):
        result = hs.search_policy_evidence("가상 질문", client=self.client, collection="exp_b2_qwen3emb06b_full_v1",
                                           embedder=make_embedder("x", 1024, declare=False))
        self.assertEqual(result["search_profile"]["mode"], "custom")
        self.assertIsNone(result["query_embedding_model"])                           # 표시 없는 가짜 → 단정 안 함
        with self.assertRaises(hs.SearchProfileError):                                 # 실제 차원 불일치는 검색 전 거부
            hs.search_policy_evidence("가상 질문", client=self.client, collection="exp_b2_qwen3emb4b_full_v1",
                                      embedder=make_embedder("x", 1024, declare=False))

    def test_same_profile_is_reproducible(self):
        first = hs.search_policy_evidence("가상 질문", client=self.client, embedder=make_embedder("qwen3-embedding:0.6b", 1024))
        second = hs.search_policy_evidence("가상 질문", client=self.client, embedder=make_embedder("qwen3-embedding:0.6b", 1024))
        self.assertEqual([h["chunk_id"] for h in first["hits"]], [h["chunk_id"] for h in second["hits"]])
        self.assertEqual(first["search_profile"], second["search_profile"])

    def test_make_ollama_embedder_declares_model_and_dim(self):
        embedder = hs.em.make_ollama_embedder(model="qwen3-embedding:4b", expected_dim=2560)
        self.assertEqual((embedder.embedding_model, embedder.embedding_dim), ("qwen3-embedding:4b", 2560))
        hs.resolve_search_settings(profile=B, embedder=embedder)                       # 계약 통과 (호출 안 함)


def _context(policy=None, regions=None, trees=None, unknown=None):
    return {"policy_id": "POL_S", "policy": policy or {}, "regions": regions or [],
            "eligibility": {"evaluated": False, "condition_tree": trees or [], "unknown_logic_groups": unknown or [],
                            "structure_issues": []}}


class TestQualityWarnings(unittest.TestCase):
    def test_empty_not_group_region_and_hold(self):
        tree = {"group_id": "G_ROOT", "logic": "AND", "conditions": [],
                "groups": [{"group_id": "G_NOT", "logic": "NOT", "conditions": [], "groups": []}]}
        quality = hs.assess_policy_data_quality(_context(policy={"organization": "충청남도 천안시"},
                                                         regions=[{"name": "충청남도"}], trees=[tree]))
        codes = {w["code"] for w in quality["warnings"]}
        self.assertLessEqual({hs.QUALITY_EMPTY_NOT_GROUP, hs.QUALITY_REGION_GRANULARITY}, codes)
        self.assertTrue(quality["hold_auto_eligibility"])
        self.assertIn("region", quality["affected_attributes"])
        for warning in quality["warnings"]:
            self.assertFalse(warning["official_source_checked"])
            self.assertIn(warning["severity"], ("high", "medium", "low"))

    def test_national_or_province_agency_is_not_flagged(self):
        for organization, regions in (("고용노동부", [{"name": "전국"}]), ("경기도 도시주택실", [{"name": "경기도"}]),
                                      ("부산광역시", [{"name": "부산광역시"}])):
            quality = hs.assess_policy_data_quality(_context(policy={"organization": organization}, regions=regions))
            with self.subTest(organization=organization):
                self.assertNotIn(hs.QUALITY_REGION_GRANULARITY, {w["code"] for w in quality["warnings"]})

    def test_unknown_is_hold_not_eligible(self):
        tree = {"group_id": "G_ROOT", "logic": "UNKNOWN", "conditions": [], "groups": []}
        context = _context(trees=[tree], unknown=["G_ROOT"])
        quality = hs.assess_policy_data_quality(context)
        self.assertTrue(quality["hold_auto_eligibility"])
        self.assertIn(hs.QUALITY_UNKNOWN_CONDITION, quality["hold_reasons"])
        self.assertFalse(context["eligibility"]["evaluated"])                          # 판정하지 않는다

    def test_static_flags_add_codes_and_enrich_without_confirming(self):
        tree = {"group_id": "G_ROOT", "logic": "UNKNOWN", "conditions": [], "groups": []}
        flags = [{"check": "S1_UNKNOWN_WITH_CONDITION_TEXT", "enrich": "UNKNOWN_CONDITION_GROUP",
                  "evidence_level": "keyword_detected", "handoff_semantic_match": "not_modeled",
                  "evidence": {"age": [{"line": "만 19~34세 (sample_only)"}]}},
                 {"check": "S6_EMPLOYMENT_MISSING", "code": "EMPLOYMENT_NOT_MODELED", "severity": "medium",
                  "evidence_level": "handoff_attribute", "handoff_semantic_match": "mismatch", "affects": ["employment"],
                  "evidence": {"eligibility_attributes.employment_status": ["구직자 (sample_only)"]}}]
        quality = hs.assess_policy_data_quality(_context(trees=[tree], unknown=["G_ROOT"]), flags)
        by_code = {w["code"]: w for w in quality["warnings"]}
        self.assertEqual(by_code["UNKNOWN_CONDITION_GROUP"]["handoff_checks"][0]["evidence_level"], "keyword_detected")
        employment = by_code["EMPLOYMENT_NOT_MODELED"]
        self.assertEqual((employment["level"], employment["verification"], employment["source"]),
                         ("suspected", "mismatch", "static_flags"))
        self.assertIn("확정 아님", employment["message"])

    def test_review_status_overclaim_only_with_suspected_pattern(self):
        rel = {"review_status": "verified"}
        hh_or = {"group_id": "G_HH", "logic": "OR", "groups": [],
                 "conditions": [{"condition_id": "COND_HH_1인가구", "relationship": rel}]}
        tree = {"group_id": "G_ROOT", "logic": "AND", "groups": [hh_or],
                "conditions": [{"condition_id": hs.HOMELESS_CONDITION_ID, "relationship": rel}]}
        codes = {w["code"] for w in hs.assess_policy_data_quality(_context(trees=[tree]))["warnings"]}
        self.assertLessEqual({hs.QUALITY_HOMELESS_PROMOTED, hs.QUALITY_REVIEW_STATUS_OVERCLAIM}, codes)


class TestStaticFlagsFile(unittest.TestCase):
    def tearDown(self):
        dqf._CACHE.clear()

    def test_real_flags_file_matches_current_manifest(self):
        loaded = dqf.load_flags()
        self.assertEqual(loaded["status"], "applied")
        self.assertEqual(loaded["manifest_sha256"], fi.manifest_sha256())
        raw = json.loads(dqf.FLAGS_PATH.read_text(encoding="utf-8"))
        self.assertIn("확정된 정책 오류가 아니며", raw["disclaimer"])
        for flags in raw["flags_by_policy"].values():
            for flag in flags:
                self.assertFalse(flag["official_source_checked"])
                for lines in flag["evidence"].values():
                    for line in lines if isinstance(lines, list) else []:
                        if isinstance(line, dict):
                            self.assertLessEqual(len(line.get("line", "")), 150)

    def test_stale_and_missing_flags_are_not_applied(self):
        with tempfile.TemporaryDirectory() as folder:
            stale = Path(folder) / "stale.json"
            stale.write_text(json.dumps({"manifest_sha256": "0" * 64, "flags_by_policy": {"POL_X": [{"code": "X"}]}}),
                             encoding="utf-8")
            self.assertEqual((dqf.load_flags(stale)["status"], dqf.load_flags(stale)["flags_by_policy"]), ("stale", {}))
            self.assertEqual(dqf.load_flags(Path(folder) / "none.json")["status"], "missing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
