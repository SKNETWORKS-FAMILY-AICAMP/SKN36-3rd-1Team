"""
Hybrid 검색 인터페이스 단위 테스트 — 실제 Qdrant·Neo4j·Ollama 서버에 접속하지 않는다.

실행 방법 (프로젝트 루트에서):
    .venv\\Scripts\\python.exe -m unittest tests/unit/test_hybrid_search.py -v

구성 (모든 데이터는 가상, sample_only)
- Qdrant: qdrant-client 메모리 모드(QdrantClient(":memory:")) + 4차원 가상 벡터
- Neo4j : 쿼리 상수별로 가상 그래프 행을 돌려주는 가짜 실행기 FakeHybridRunner
- Ollama: 질문 문자열 → 미리 정한 벡터를 돌려주는 가짜 embedder
"""

import copy
import json
import sys
import unittest
import warnings
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import embedding as em  # noqa: E402
from database import hybrid_search as hs  # noqa: E402
from database.chunking import make_point_id  # noqa: E402
from database.qdrant import store  # noqa: E402

try:
    import qdrant_client  # noqa: F401
    HAS_QDRANT = True
except ImportError:
    HAS_QDRANT = False

DIM = 4
COLLECTION = "exp_unit_hybrid_sample"
RUN_INFO = {"embedding_model": em.DEFAULT_EMBEDDING_MODEL, "embedding_dim": DIM,
            "embedding_input_rule": "policy_section_body_v2", "index_run_id": "RUN_SAMPLE_ONLY"}
POLICY_META = {pid: {"policy_name": f"가상 정책 {pid} (sample_only)", "category": "가상분류", "region": "전국",
                     "life_stages": ["청년"], "target_groups": ["가상대상"], "eligibility_attributes": {},
                     "verification_status": "extracted"} for pid in ("POL_S1", "POL_S2", "POL_S3")}
# 청크 (sample_only): 벡터 방향으로 순위를 정한다
CHUNKS = [
    ("CHK_B2_DOC_S1_C001", "POL_S1", "DOC_S1", "가상 금리 안내 (sample_only)", [1.0, 0.0, 0.0, 0.0], 1),
    ("CHK_B2_DOC_S1_C002", "POL_S1", "DOC_S1", "가상 신청 방법 (sample_only)", [0.9, 0.1, 0.0, 0.0], 2),
    ("CHK_B2_DOC_S2_C001", "POL_S2", "DOC_S2", "가상 상담 전화 (sample_only)", [0.8, 0.3, 0.0, 0.0], None),
    ("CHK_B2_DOC_S3_C001", "POL_S3", "DOC_S3", "그래프에 없는 정책 (sample_only)", [0.0, 0.0, 1.0, 0.0], None),
]
QUERY_VECTORS = {"금리 질문": [1.0, 0.05, 0.0, 0.0], "그래프 없는 질문": [0.0, 0.0, 1.0, 0.0]}


def fake_embedder(texts):
    return [list(QUERY_VECTORS.get(text, [0.5, 0.5, 0.5, 0.5])) for text in texts]


def _chunk(chunk_id, policy_id, document_id, text, page):
    return {"chunk_id": chunk_id, "policy_id": policy_id, "document_id": document_id, "text": text,
            "section_title": "[가상 섹션]", "source_url": f"https://example.invalid/{document_id}",
            "char_start": 0, "char_end": len(text), "page_start": page, "page_end": page,
            "chunking_version": "B2", "source_type": "pdf" if page else "api"}


def make_memory_client(chunks=CHUNKS, content_hashes=None, embedding_model=None):
    warnings.filterwarnings("ignore", message="Payload indexes have no effect")
    client = store.get_client(location=":memory:")
    store.ensure_collection(client, COLLECTION, DIM)
    info = dict(RUN_INFO, embedding_model=embedding_model or RUN_INFO["embedding_model"])
    hashes = content_hashes or {"DOC_S1": "hash_s1", "DOC_S2": "hash_s2", "DOC_S3": "hash_s3"}
    points = store.build_points([_chunk(c[0], c[1], c[2], c[3], c[5]) for c in chunks], [c[4] for c in chunks],
                                policy_metadata=POLICY_META, content_hashes=hashes, run_info=info)
    store.upsert_points(client, COLLECTION, points)
    return client


class FakeHybridRunner:
    """
    hybrid_search의 Cypher 상수별로 가상 그래프 행을 돌려주는 가짜 Neo4j 실행기 (sample_only, 읽기만).
    - POL_S1: 루트 AND 그룹 + 하위 OR 그룹 + 하위 NOT 그룹 (계층·논리 보존 확인용)
    - POL_S2: 루트 UNKNOWN 그룹, 조건 없음
    - POL_S3: 그래프에 없음
    """

    def __init__(self):
        self.calls = []
        self.policies = {"POL_S1": {"policy_id": "POL_S1", "policy_name": "가상 정책 POL_S1 (sample_only)"},
                         "POL_S2": {"policy_id": "POL_S2", "policy_name": "가상 정책 POL_S2 (sample_only)"}}
        self.documents = {"DOC_S1": {"document_id": "DOC_S1", "policy_id": "POL_S1", "content_hash": "hash_s1"},
                          "DOC_S2": {"document_id": "DOC_S2", "policy_id": "POL_S2", "content_hash": "hash_s2"}}
        self.supported_by = {"DOC_S1": ["POL_S1"], "DOC_S2": ["POL_S2"]}
        rel = {"review_status": "verified", "evidence_location": "가상 위치"}
        self.neighbors = {
            "POL_S1": {"organizations": [{"node": {"agency_id": "AGENCY_S1", "name": "가상 기관"}, "relationship": rel}],
                       "regions": [{"node": {"region_id": "REGION_S", "name": "전국"}, "relationship": rel}],
                       "documents": [{"node": self.documents["DOC_S1"], "relationship": rel}]},
            "POL_S2": {"documents": [{"node": self.documents["DOC_S2"], "relationship": rel}]},
        }
        self.roots = {"POL_S1": ["GRP_S1_ROOT"], "POL_S2": ["GRP_S2_ROOT"]}
        self.requires = {"POL_S1": ["COND_S_AGE", "COND_S_INC_A", "COND_S_INC_B", "COND_S_EXCL"], "POL_S2": []}

        def cond(cid, review="verified"):
            return {"condition": {"condition_id": cid, "name": f"가상 조건 {cid}"},
                    "relationship": {"review_status": review, "evidence_location": "가상 근거"}}
        self.groups = {
            "GRP_S1_ROOT": {"group": {"group_id": "GRP_S1_ROOT", "policy_id": "POL_S1", "logic": "AND", "is_root": True},
                            "child_groups": [{"group_id": "GRP_S1_OR", "relationship": {}},
                                             {"group_id": "GRP_S1_NOT", "relationship": {}}],
                            "conditions": [cond("COND_S_AGE")]},
            "GRP_S1_OR": {"group": {"group_id": "GRP_S1_OR", "policy_id": "POL_S1", "logic": "OR", "is_root": False},
                          "child_groups": [], "conditions": [cond("COND_S_INC_B", "extracted"), cond("COND_S_INC_A")]},
            "GRP_S1_NOT": {"group": {"group_id": "GRP_S1_NOT", "policy_id": "POL_S1", "logic": "NOT", "is_root": False},
                           "child_groups": [], "conditions": [cond("COND_S_EXCL")]},
            "GRP_S2_ROOT": {"group": {"group_id": "GRP_S2_ROOT", "policy_id": "POL_S2", "logic": "UNKNOWN",
                                      "is_root": True}, "child_groups": [], "conditions": []},
        }
        self.fail_with = None

    def read_rows(self, query, parameters=None):
        self.calls.append((query, parameters))
        if self.fail_with is not None:
            raise self.fail_with
        if query == hs.POLICY_CONTEXT_QUERY:
            return [self._policy_row(pid) for pid in parameters["policy_ids"] if pid in self.policies]
        if query == hs.CONDITION_TREE_QUERY:
            rows = []
            for pid in parameters["policy_ids"]:
                for root in self.roots.get(pid, []):
                    seen, frontier = set(), [root]
                    while frontier:
                        gid = frontier.pop()
                        if gid in seen or gid not in self.groups:
                            continue
                        seen.add(gid)
                        group = self.groups[gid]
                        rows.append({"policy_id": pid, "root_group_id": root, "group_id": gid,
                                     "group": dict(group["group"]), "child_groups": list(group["child_groups"]),
                                     "conditions": list(group["conditions"])})
                        frontier.extend(child["group_id"] for child in group["child_groups"])
            return rows
        if query == hs.CROSS_POLICY_QUERY:
            return [{"policy_id": pid, "exists": pid in self.policies} for pid in parameters["policy_ids"]]
        if query == hs.CROSS_DOCUMENT_QUERY:
            rows = []
            for did in parameters["document_ids"]:
                doc = self.documents.get(did)
                rows.append({"document_id": did, "exists": doc is not None,
                             "content_hash": doc and doc["content_hash"], "policy_id": doc and doc["policy_id"],
                             "supported_by": list(self.supported_by.get(did, []))})
            return rows
        raise AssertionError(f"예상하지 못한 쿼리: {query[:60]}")

    def _policy_row(self, pid):
        neighbors = self.neighbors.get(pid, {})
        row = {"policy_id": pid, "policy": dict(self.policies[pid]), "modifies_condition_of": [],
               "condition_modified_by": [], "requires_condition_ids": list(self.requires.get(pid, [])),
               "root_groups": [{"group_id": gid, "relationship": {"review_status": "verified"}}
                               for gid in self.roots.get(pid, [])]}
        for key, _relation in hs.POLICY_NEIGHBOR_SPECS:
            row[key] = list(neighbors.get(key, []))
        return row

    # 쓰기 메서드는 없다 → hybrid_search가 쓰기를 시도하면 AttributeError로 드러난다


# ---------------------------------------------------------------------------
# 1) 입력 검증
# ---------------------------------------------------------------------------
class TestInputValidation(unittest.TestCase):
    def test_query_and_k(self):
        self.assertEqual(hs.validate_query("  질문  "), "질문")
        for bad in ("", "   ", None, 3, "가" * (hs.MAX_QUERY_CHARS + 1)):
            with self.subTest(query=bad), self.assertRaises(hs.HybridSearchInputError):
                hs.validate_query(bad)
        for bad in (0, -1, hs.MAX_K + 1, True, 2.0, "5"):
            with self.subTest(k=bad), self.assertRaises(hs.HybridSearchInputError):
                hs.validate_k(bad)
        self.assertEqual(hs.validate_k(hs.MAX_K), hs.MAX_K)

    def test_filters_only_allowed_fields_and_string_values(self):
        self.assertIsNone(hs.validate_filters(None))
        self.assertIsNone(hs.validate_filters({}))
        self.assertEqual(hs.validate_filters({"document_id": "DOC_S1", "policy_id": ["POL_S1", "POL_S2"]}),
                         {"document_id": "DOC_S1", "policy_id": ["POL_S1", "POL_S2"]})
        bad_filters = [{"text": "금리"}, {"document_id) OR (1=1": "x"}, {"policy_id": []}, {"policy_id": [1]},
                       {"document_id": ""}, {"document_id": None}, ["document_id"], "document_id=DOC_S1",
                       {"policy_id": ["POL_S1"] * (hs.MAX_FILTER_VALUES + 1)}]
        for bad in bad_filters:
            with self.subTest(filters=bad), self.assertRaises(hs.HybridSearchInputError):
                hs.validate_filters(bad)

    @unittest.skipUnless(HAS_QDRANT, "qdrant-client 미설치 → NOT_TESTED")
    def test_filter_value_is_passed_as_literal_value_not_expression(self):
        tricky = "DOC_S1\" OR document_id != \""
        built = store._to_filter(hs.validate_filters({"document_id": tricky}))
        condition = built.must[0]
        self.assertEqual((condition.key, condition.match.value), ("document_id", tricky))

    def test_policy_ids_format_dedupe_and_limit(self):
        self.assertEqual(hs.validate_policy_ids(["POL_S2", "POL_S1", "POL_S2"]), ["POL_S2", "POL_S1"])
        for bad in ("POL_S1", ["pol_s1"], ["POL_S1' OR 1=1 //"], [None], ["DOC_S1"],
                    [f"POL_{i}" for i in range(hs.MAX_POLICY_IDS + 1)]):
            with self.subTest(policy_ids=str(bad)[:40]), self.assertRaises(hs.HybridSearchInputError):
                hs.validate_policy_ids(bad)


# ---------------------------------------------------------------------------
# 2) Qdrant 근거 검색 (메모리 모드)
# ---------------------------------------------------------------------------
@unittest.skipUnless(HAS_QDRANT, "qdrant-client 미설치 → NOT_TESTED")
class TestSearchPolicyEvidence(unittest.TestCase):
    def setUp(self):
        self.client = make_memory_client()

    def _search(self, query="금리 질문", filters=None, k=5, **kwargs):
        return hs.search_policy_evidence(query, filters, k, client=self.client, embedder=fake_embedder,
                                         collection=COLLECTION, **kwargs)

    def test_hits_are_ranked_and_carry_evidence_fields(self):
        result = self._search(k=3)
        hits = result["hits"]
        self.assertEqual([hit["chunk_id"] for hit in hits],
                         ["CHK_B2_DOC_S1_C001", "CHK_B2_DOC_S1_C002", "CHK_B2_DOC_S2_C001"])
        self.assertEqual([hit["rank"] for hit in hits], [1, 2, 3])
        self.assertTrue(all(a["score"] >= b["score"] for a, b in zip(hits, hits[1:])))
        first = hits[0]
        for field in hs.HIT_PAYLOAD_FIELDS:
            self.assertIn(field, first)
        self.assertEqual((first["text"], first["char_start"], first["char_end"], first["page_start"]),
                         ("가상 금리 안내 (sample_only)", 0, len("가상 금리 안내 (sample_only)"), 1))
        self.assertEqual(first["point_id"], make_point_id("CHK_B2_DOC_S1_C001"))
        self.assertEqual(result["warnings"], [])
        self.assertIsNone(result["query_embedding_model"])        # 가짜 embedder → 모델 이름을 단정하지 않는다

    def test_document_and_policy_filters(self):
        only_s2 = self._search(filters={"document_id": "DOC_S2"})
        self.assertEqual([hit["document_id"] for hit in only_s2["hits"]], ["DOC_S2"])
        two = self._search(filters={"policy_id": ["POL_S2", "POL_S3"]})
        self.assertEqual({hit["policy_id"] for hit in two["hits"]}, {"POL_S2", "POL_S3"})
        none = self._search(filters={"document_id": "DOC_NOT_IN_INDEX"})
        self.assertEqual(none["hits"], [])

    def test_invalid_input_never_calls_backends(self):
        calls = []

        def counting_embedder(texts):
            calls.append(texts)
            return fake_embedder(texts)
        for kwargs in ({"query": ""}, {"filters": {"text": "x"}}, {"k": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(hs.HybridSearchInputError):
                hs.search_policy_evidence(kwargs.get("query", "금리 질문"), kwargs.get("filters"), kwargs.get("k", 5),
                                          client=self.client, embedder=counting_embedder, collection=COLLECTION)
        self.assertEqual(calls, [])

    def test_protected_collection_is_refused(self):
        with self.assertRaises(store.CollectionGuardError):
            hs.search_policy_evidence("금리 질문", client=self.client, embedder=fake_embedder,
                                      collection="lifepolicy_chunks")

    def test_backend_failures_are_labeled(self):
        def broken_embedder(texts):
            raise em.EmbeddingError("가상 Ollama 장애 (sample_only)")
        with self.assertRaises(hs.HybridSearchBackendError) as context:
            hs.search_policy_evidence("금리 질문", client=self.client, embedder=broken_embedder, collection=COLLECTION)
        self.assertEqual(context.exception.backend, "ollama")

        class BrokenClient:
            def query_points(self, **kwargs):
                raise ConnectionError("가상 Qdrant 장애 (sample_only)")
        with self.assertRaises(hs.HybridSearchBackendError) as context:
            hs.search_policy_evidence("금리 질문", client=BrokenClient(), embedder=fake_embedder, collection=COLLECTION)
        self.assertEqual(context.exception.backend, "qdrant")

    def test_index_model_mismatch_is_warned(self):
        client = make_memory_client(embedding_model="other-embedding-model")
        result = hs.search_policy_evidence("금리 질문", client=client, collection=COLLECTION, embedder=fake_embedder)
        self.assertEqual(result["warnings"], [])   # 가짜 embedder는 모델을 모르므로 비교하지 않는다
        original = hs.em.make_ollama_embedder
        hs.em.make_ollama_embedder = lambda **kwargs: fake_embedder   # 기본 embedder 자리에 가짜를 넣는다
        try:
            result = hs.search_policy_evidence("금리 질문", client=client, collection=COLLECTION)
        finally:
            hs.em.make_ollama_embedder = original
        self.assertEqual(result["query_embedding_model"], em.DEFAULT_EMBEDDING_MODEL)
        self.assertEqual(len(result["warnings"]), 1)


# ---------------------------------------------------------------------------
# 3) Neo4j 정책 문맥 (가짜 실행기)
# ---------------------------------------------------------------------------
class TestPolicyGraphContext(unittest.TestCase):
    def setUp(self):
        self.runner = FakeHybridRunner()

    def test_condition_tree_keeps_hierarchy_and_logic(self):
        context = hs.get_policy_graph_context(["POL_S1"], runner=self.runner)["policies"]["POL_S1"]
        eligibility = context["eligibility"]
        self.assertFalse(eligibility["evaluated"])
        root = eligibility["condition_tree"][0]
        self.assertEqual((root["group_id"], root["logic"], root["is_root"]), ("GRP_S1_ROOT", "AND", True))
        self.assertEqual([c["condition_id"] for c in root["conditions"]], ["COND_S_AGE"])
        self.assertEqual([(g["group_id"], g["logic"]) for g in root["groups"]],
                         [("GRP_S1_NOT", "NOT"), ("GRP_S1_OR", "OR")])
        or_group = root["groups"][1]
        self.assertEqual([c["condition_id"] for c in or_group["conditions"]], ["COND_S_INC_A", "COND_S_INC_B"])
        # 근거 상태(review_status)는 바꾸지 않고 그대로 전달한다
        self.assertEqual(or_group["conditions"][1]["relationship"]["review_status"], "extracted")
        self.assertTrue(eligibility["tree_matches_requires_condition"])
        self.assertEqual(eligibility["structure_issues"], [])
        # 충족 여부를 나타내는 값을 만들지 않는다 (조건 항목에 원본에 없는 키가 붙지 않음)
        self.assertEqual(set(or_group["conditions"][0]), {"condition_id", "name", "relationship"})

    def test_unknown_logic_is_preserved_not_converted(self):
        context = hs.get_policy_graph_context(["POL_S2"], runner=self.runner)["policies"]["POL_S2"]
        tree = context["eligibility"]["condition_tree"][0]
        self.assertEqual((tree["logic"], tree["conditions"], tree["groups"]), ("UNKNOWN", [], []))
        self.assertEqual(context["eligibility"]["unknown_logic_groups"], ["GRP_S2_ROOT"])

    def test_neighbors_and_documents_are_returned(self):
        context = hs.get_policy_graph_context(["POL_S1"], runner=self.runner)["policies"]["POL_S1"]
        self.assertEqual([o["agency_id"] for o in context["organizations"]], ["AGENCY_S1"])
        self.assertEqual(context["organizations"][0]["relationship"]["review_status"], "verified")
        self.assertEqual([d["document_id"] for d in context["documents"]], ["DOC_S1"])
        for key, _relation in hs.POLICY_NEIGHBOR_SPECS:
            self.assertIn(key, context)

    def test_missing_policy_is_reported_not_created(self):
        result = hs.get_policy_graph_context(["POL_S3", "POL_S1"], runner=self.runner)
        self.assertEqual(result["missing_policy_ids"], ["POL_S3"])
        self.assertEqual(sorted(result["policies"]), ["POL_S1"])

    def test_empty_input_does_not_query(self):
        self.assertEqual(hs.get_policy_graph_context([], runner=self.runner)["policies"], {})
        self.assertEqual(self.runner.calls, [])

    def test_ids_are_parameters_and_queries_are_read_only(self):
        hs.get_policy_graph_context(["POL_S1", "POL_S2"], runner=self.runner)
        for query, parameters in self.runner.calls:
            self.assertEqual(parameters, {"policy_ids": ["POL_S1", "POL_S2"]})
            self.assertNotIn("POL_S1", query)
        for query in (hs.POLICY_CONTEXT_QUERY, hs.CONDITION_TREE_QUERY, hs.CROSS_POLICY_QUERY, hs.CROSS_DOCUMENT_QUERY):
            with self.subTest(query=query[:40]):
                self.assertNotRegex(query.upper(), r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|CALL)\b")
                self.assertRegex(query, r"\$(policy_ids|document_ids)")

    def test_neighbor_specs_follow_relationship_allowlist(self):
        for key, relation in hs.POLICY_NEIGHBOR_SPECS:
            target_label = hs.lg.RELATIONSHIP_SPECS[relation][1]
            self.assertIn(f"[(p)-[r:{relation}]->(n:{target_label})", hs.POLICY_CONTEXT_QUERY)

    def test_structure_issues_are_reported(self):
        runner = FakeHybridRunner()
        runner.groups["GRP_S1_OR"]["child_groups"] = [{"group_id": "GRP_S1_ROOT", "relationship": {}},     # 순환
                                                      {"group_id": "GRP_S1_GONE", "relationship": {}}]     # 미조회
        runner.groups["GRP_S1_NOT"]["group"]["policy_id"] = "POL_S2"                                      # 소속 불일치
        runner.requires["POL_S1"].append("COND_S_ONLY_FLAT")                                              # 평탄화 목록과 불일치
        eligibility = hs.get_policy_graph_context(["POL_S1"], runner=runner)["policies"]["POL_S1"]["eligibility"]
        issues = {issue["issue"] for issue in eligibility["structure_issues"]}
        self.assertEqual(issues, {"cycle", "child_group_not_loaded", "group_policy_mismatch"})
        self.assertFalse(eligibility["tree_matches_requires_condition"])

    def test_policy_without_root_group_is_flagged(self):
        runner = FakeHybridRunner()
        runner.roots["POL_S2"] = []
        eligibility = hs.get_policy_graph_context(["POL_S2"], runner=runner)["policies"]["POL_S2"]["eligibility"]
        self.assertEqual(eligibility["condition_tree"], [])
        self.assertEqual(eligibility["structure_issues"], [{"issue": "no_root_condition_group"}])

    def test_neo4j_failure_is_labeled(self):
        self.runner.fail_with = ConnectionError("가상 Neo4j 장애 (sample_only)")
        with self.assertRaises(hs.HybridSearchBackendError) as context:
            hs.get_policy_graph_context(["POL_S1"], runner=self.runner)
        self.assertEqual(context.exception.backend, "neo4j")

    def test_invalid_policy_id_never_reaches_neo4j(self):
        with self.assertRaises(hs.HybridSearchInputError):
            hs.get_policy_graph_context(["POL_S1", "POL_S1}) DETACH DELETE p //"], runner=self.runner)
        self.assertEqual(self.runner.calls, [])


# ---------------------------------------------------------------------------
# 4) 결합
# ---------------------------------------------------------------------------
def _hit(rank, score, chunk_id, policy_id, document_id):
    return {"rank": rank, "score": score, "chunk_id": chunk_id, "policy_id": policy_id, "document_id": document_id,
            "policy_name": f"가상 {policy_id}", "text": "sample_only"}


class TestCombinePolicyEvidence(unittest.TestCase):
    def setUp(self):
        self.context = hs.get_policy_graph_context(["POL_S1", "POL_S2", "POL_S3"], runner=FakeHybridRunner())

    def test_groups_by_policy_sorted_by_best_rank_and_dedupes(self):
        search = {"query": "q", "hits": [
            _hit(1, 0.9, "CHK_B2_DOC_S2_C001", "POL_S2", "DOC_S2"),
            _hit(2, 0.8, "CHK_B2_DOC_S1_C001", "POL_S1", "DOC_S1"),
            _hit(3, 0.7, "CHK_B2_DOC_S2_C002", "POL_S2", "DOC_S2"),
            _hit(4, 0.6, "CHK_B2_DOC_S2_C001", "POL_S2", "DOC_S2"),   # 같은 청크 중복
        ]}
        combined = hs.combine_policy_evidence(search, self.context)
        self.assertEqual([p["policy_id"] for p in combined["policies"]], ["POL_S2", "POL_S1"])
        s2 = combined["policies"][0]
        self.assertEqual([h["chunk_id"] for h in s2["evidence"]], ["CHK_B2_DOC_S2_C001", "CHK_B2_DOC_S2_C002"])
        self.assertEqual((s2["best_rank"], s2["best_score"], s2["graph_status"]), (1, 0.9, "FOUND"))
        self.assertIn("evidence", s2)
        self.assertIn("graph", s2)
        self.assertIn("자격", combined["notes"]["score"])

    def test_policy_missing_in_graph_is_not_invented(self):
        search = {"hits": [_hit(1, 0.9, "CHK_B2_DOC_S3_C001", "POL_S3", "DOC_S3")]}
        combined = hs.combine_policy_evidence(search, self.context)
        entry = combined["policies"][0]
        self.assertEqual((entry["graph_status"], entry["graph"]), ("MISSING_IN_GRAPH", None))
        self.assertEqual(entry["evidence_document_check"], {"DOC_S3": hs.NOT_TESTED})
        self.assertEqual(combined["missing_policy_ids"], ["POL_S3"])

    def test_evidence_document_not_linked_in_graph_is_mismatch(self):
        search = {"hits": [_hit(1, 0.9, "CHK_B2_DOC_S2_C001", "POL_S1", "DOC_S2")]}   # 청크가 다른 정책 문서
        entry = hs.combine_policy_evidence(search, self.context)["policies"][0]
        self.assertEqual(entry["evidence_document_check"], {"DOC_S2": hs.MISMATCH})

    def test_hits_without_policy_id_are_kept_separately(self):
        combined = hs.combine_policy_evidence({"hits": [_hit(1, 0.5, "CHK_X", None, "DOC_X")]}, self.context)
        self.assertEqual(combined["policies"], [])
        self.assertEqual(len(combined["unlinked_hits"]), 1)

    def test_empty_search(self):
        combined = hs.combine_policy_evidence({"hits": []}, {"policies": {}})
        self.assertEqual((combined["policies"], combined["unlinked_hits"]), ([], []))


# ---------------------------------------------------------------------------
# 5) 통합 진입 함수
# ---------------------------------------------------------------------------
@unittest.skipUnless(HAS_QDRANT, "qdrant-client 미설치 → NOT_TESTED")
class TestSearchPolicyContext(unittest.TestCase):
    def test_end_to_end_with_fakes(self):
        runner = FakeHybridRunner()
        result = hs.search_policy_context("금리 질문", k=4, client=make_memory_client(), runner=runner,
                                          embedder=fake_embedder, collection=COLLECTION)
        self.assertEqual([p["policy_id"] for p in result["policies"]], ["POL_S1", "POL_S2", "POL_S3"])
        self.assertEqual([p["graph_status"] for p in result["policies"]], ["FOUND", "FOUND", "MISSING_IN_GRAPH"])
        self.assertEqual(runner.calls[0][1], {"policy_ids": ["POL_S1", "POL_S2", "POL_S3"]})   # 검색 순위 순서
        self.assertIn("neo4j_context", result["latency_seconds"])
        self.assertEqual(result["invalid_policy_ids"], [])

    def test_payload_policy_id_with_bad_format_is_not_sent_to_neo4j(self):
        chunks = [("CHK_B2_DOC_S1_C001", "POL_S1", "DOC_S1", "정상 (sample_only)", [1.0, 0, 0, 0], None)]
        client = make_memory_client(chunks)
        point = store.build_points([_chunk("CHK_B2_DOC_BAD_C001", "bad_policy", "DOC_BAD", "형식 오류 (sample_only)",
                                           None)], [[0.99, 0.01, 0, 0]],
                                   policy_metadata={"bad_policy": POLICY_META["POL_S1"]},
                                   content_hashes={"DOC_BAD": "h"}, run_info=RUN_INFO)
        store.upsert_points(client, COLLECTION, point)
        runner = FakeHybridRunner()
        result = hs.search_policy_context("금리 질문", k=2, client=client, runner=runner, embedder=fake_embedder,
                                          collection=COLLECTION)
        self.assertEqual(result["invalid_policy_ids"], ["bad_policy"])
        self.assertEqual(runner.calls[0][1], {"policy_ids": ["POL_S1"]})


# ---------------------------------------------------------------------------
# 6) Qdrant ↔ Neo4j 교차 검증
# ---------------------------------------------------------------------------
@unittest.skipUnless(HAS_QDRANT, "qdrant-client 미설치 → NOT_TESTED")
class TestVerifyCrossDb(unittest.TestCase):
    def _verify(self, client, runner):
        return hs.verify_cross_db(client=client, runner=runner, collection=COLLECTION)

    def test_consistent_sample_passes(self):
        client = make_memory_client(CHUNKS[:3])
        report = self._verify(client, FakeHybridRunner())
        self.assertEqual(report["status"], hs.PASS, report["failures"])
        self.assertEqual((report["qdrant_points"], report["qdrant_count"], report["all_points_read"]), (3, 3, True))
        self.assertEqual(report["summary"]["supported_by"][hs.PASS], 3)

    def test_missing_policy_and_document_mark_dependents_not_tested(self):
        report = self._verify(make_memory_client(), FakeHybridRunner())    # POL_S3 / DOC_S3는 그래프에 없음
        self.assertEqual(report["status"], hs.MISMATCH)
        failure = [f for f in report["failures"] if f["policy_id"] == "POL_S3"][0]["checks"]
        self.assertEqual(failure, {"policy_exists": hs.MISSING, "document_exists": hs.MISSING,
                                   "supported_by": hs.NOT_TESTED, "document_policy_id": hs.NOT_TESTED,
                                   "content_hash": hs.NOT_TESTED})

    def test_chunk_linked_to_wrong_policy_is_mismatch(self):
        runner = FakeHybridRunner()
        runner.supported_by["DOC_S2"] = ["POL_S1"]          # 그래프에서는 DOC_S2를 POL_S1이 근거로 가짐
        runner.documents["DOC_S2"]["policy_id"] = "POL_S1"
        report = self._verify(make_memory_client(CHUNKS[:3]), runner)
        checks = [f for f in report["failures"] if f["document_id"] == "DOC_S2"][0]["checks"]
        self.assertEqual(checks, {"supported_by": hs.MISMATCH, "document_policy_id": hs.MISMATCH})

    def test_missing_supported_by_and_hash_mismatch(self):
        runner = FakeHybridRunner()
        runner.supported_by["DOC_S1"] = []
        runner.documents["DOC_S1"]["content_hash"] = "different_hash"
        report = self._verify(make_memory_client(CHUNKS[:1]), runner)
        self.assertEqual(report["failures"][0]["checks"], {"supported_by": hs.MISSING, "content_hash": hs.MISMATCH})

    def test_point_id_not_uuid5_of_chunk_id_is_mismatch(self):
        from qdrant_client import models
        client = make_memory_client(CHUNKS[:1])
        payload = client.scroll(COLLECTION, limit=1, with_payload=True)[0][0].payload
        wrong = "00000000-0000-0000-0000-000000000001"
        client.upsert(COLLECTION, [models.PointStruct(id=wrong, vector=[0.1, 0.2, 0.3, 0.4],
                                                      payload={**payload, "chunk_id": "CHK_B2_DOC_S1_C099"})])
        report = self._verify(client, FakeHybridRunner())
        bad = [f for f in report["failures"] if f["chunk_id"] == "CHK_B2_DOC_S1_C099"][0]["checks"]
        self.assertEqual(bad, {"point_id": hs.MISMATCH})

    def test_chunk_id_document_mismatch(self):
        chunks = [("CHK_B2_DOC_S2_C001", "POL_S1", "DOC_S1", "문서 ID가 다른 청크 (sample_only)", [1.0, 0, 0, 0], None)]
        report = self._verify(make_memory_client(chunks), FakeHybridRunner())
        self.assertEqual(report["failures"][0]["checks"], {"chunk_id_document": hs.MISMATCH})

    def test_empty_collection_is_not_tested(self):
        client = make_memory_client([])
        report = self._verify(client, FakeHybridRunner())
        self.assertEqual(report["status"], hs.NOT_TESTED)


# ---------------------------------------------------------------------------
# 7) 연결 관리 (내부 생성 리소스만 닫는다) — 가짜 연결, sample_only
# ---------------------------------------------------------------------------
class TrackingClient:
    """메모리 Qdrant client를 감싸 close 호출 수를 센다."""

    def __init__(self, inner=None, fail_with=None):
        self.inner, self.fail_with, self.close_calls = inner, fail_with, 0

    def query_points(self, **kwargs):
        if self.fail_with is not None:
            raise self.fail_with
        return self.inner.query_points(**kwargs)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def close(self):
        self.close_calls += 1


class TrackingDriver:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


def _patched_resources(client_factory, runner=None):
    """내부 생성 경로(make_qdrant_client·_make_neo4j_driver·ReadOnlyTimedRunner)를 가짜로 바꾼다."""
    created = {"clients": [], "drivers": []}

    def make_client(*args, **kwargs):
        client = client_factory()
        created["clients"].append(client)
        return client

    def make_driver(*args, **kwargs):
        driver = TrackingDriver()
        created["drivers"].append(driver)
        return driver
    patches = [mock.patch.object(hs, "make_qdrant_client", make_client),
               mock.patch.object(hs, "_make_neo4j_driver", make_driver),
               mock.patch.object(hs, "ReadOnlyTimedRunner", lambda driver, **kwargs: runner or FakeHybridRunner())]
    return created, patches


@unittest.skipUnless(HAS_QDRANT, "qdrant-client 미설치 → NOT_TESTED")
class TestConnectionOwnership(unittest.TestCase):
    def setUp(self):
        self.memory = make_memory_client()

    def _run_with(self, client_factory, runner=None, call=None):
        created, patches = _patched_resources(client_factory, runner)
        for patch in patches:
            patch.start()
        try:
            result = call()
        finally:
            for patch in patches:
                patch.stop()
        return created, result

    def test_internal_resources_are_closed_after_success(self):
        created, result = self._run_with(lambda: TrackingClient(self.memory), call=lambda: hs.search_policy_context(
            "금리 질문", k=3, embedder=fake_embedder, collection=COLLECTION))
        self.assertEqual([p["policy_id"] for p in result["policies"]], ["POL_S1", "POL_S2"])
        self.assertEqual([c.close_calls for c in created["clients"]], [1])
        self.assertEqual([d.close_calls for d in created["drivers"]], [1])

    def test_internal_resources_are_closed_after_exception(self):
        def broken(texts):
            raise em.EmbeddingError("가상 Ollama 장애 (sample_only)")
        created, patches = _patched_resources(lambda: TrackingClient(self.memory))
        for patch in patches:
            patch.start()
        try:
            with self.assertRaises(hs.HybridSearchBackendError):
                hs.search_policy_context("금리 질문", embedder=broken, collection=COLLECTION)
            failing_runner = FakeHybridRunner()
            failing_runner.fail_with = ConnectionError("가상 Neo4j 장애 (sample_only)")
            with mock.patch.object(hs, "ReadOnlyTimedRunner", lambda driver, **kwargs: failing_runner):
                with self.assertRaises(hs.HybridSearchBackendError) as context:
                    hs.search_policy_context("금리 질문", embedder=fake_embedder, collection=COLLECTION)
            self.assertEqual(context.exception.backend, "neo4j")
        finally:
            for patch in patches:
                patch.stop()
        self.assertEqual([c.close_calls for c in created["clients"]], [1, 1])
        self.assertEqual([d.close_calls for d in created["drivers"]], [1])   # 첫 호출은 Neo4j까지 가지 않음

    def test_search_policy_evidence_closes_internal_client_only(self):
        created, _ = self._run_with(lambda: TrackingClient(self.memory), call=lambda: hs.search_policy_evidence(
            "금리 질문", embedder=fake_embedder, collection=COLLECTION))
        self.assertEqual(created["clients"][0].close_calls, 1)
        external = TrackingClient(self.memory)
        hs.search_policy_evidence("금리 질문", client=external, embedder=fake_embedder, collection=COLLECTION)
        self.assertEqual(external.close_calls, 0)

    def test_external_client_and_runner_are_not_closed(self):
        external_client, external_runner = TrackingClient(self.memory), FakeHybridRunner()
        external_runner.close = mock.Mock()
        with hs.HybridSearchSession(client=external_client, runner=external_runner, embedder=fake_embedder,
                                    collection=COLLECTION) as session:
            session.search_policy_context("금리 질문")
            self.assertEqual(session.owned_resources, {"qdrant_client": False, "neo4j_driver": False})
        self.assertEqual(external_client.close_calls, 0)
        external_runner.close.assert_not_called()

    def test_session_reuses_connections_and_closes_once(self):
        created, patches = _patched_resources(lambda: TrackingClient(self.memory))
        for patch in patches:
            patch.start()
        try:
            with hs.HybridSearchSession(embedder=fake_embedder, collection=COLLECTION) as session:
                self.assertEqual(session.owned_resources, {"qdrant_client": False, "neo4j_driver": False})   # 지연 생성
                first = session.search_policy_context("금리 질문", k=3)
                second = session.search_policy_context("금리 질문", k=3)
                self.assertEqual(session.owned_resources, {"qdrant_client": True, "neo4j_driver": True})
            self.assertTrue(session.closed)
            with self.assertRaises(RuntimeError):
                session.search_policy_context("금리 질문")
            session.close()                                                    # 두 번 닫아도 안전
        finally:
            for patch in patches:
                patch.stop()
        self.assertEqual((len(created["clients"]), len(created["drivers"])), (1, 1))
        self.assertEqual((created["clients"][0].close_calls, created["drivers"][0].close_calls), (1, 1))
        self.assertEqual([p["policy_id"] for p in first["policies"]], [p["policy_id"] for p in second["policies"]])

    def test_session_closes_on_exception_inside_with(self):
        created, patches = _patched_resources(lambda: TrackingClient(self.memory))
        for patch in patches:
            patch.start()
        try:
            with self.assertRaises(ZeroDivisionError):
                with hs.HybridSearchSession(embedder=fake_embedder, collection=COLLECTION) as session:
                    session.search_policy_context("금리 질문")
                    1 / 0
        finally:
            for patch in patches:
                patch.stop()
        self.assertEqual((created["clients"][0].close_calls, created["drivers"][0].close_calls), (1, 1))

    def test_close_error_is_recorded_not_raised(self):
        class BadClose(TrackingClient):
            def close(self):
                raise OSError("가상 닫기 실패 (sample_only)")
        created, patches = _patched_resources(lambda: BadClose(self.memory))
        for patch in patches:
            patch.start()
        try:
            session = hs.HybridSearchSession(embedder=fake_embedder, collection=COLLECTION)
            session.search_policy_evidence("금리 질문")
            session.close()
        finally:
            for patch in patches:
                patch.stop()
        self.assertEqual(len(session.close_errors), 1)

    def test_invalid_input_creates_no_connection(self):
        created, patches = _patched_resources(lambda: TrackingClient(self.memory))
        for patch in patches:
            patch.start()
        try:
            for bad in ({"query": ""}, {"filters": {"text": "x"}}):
                with self.subTest(bad=bad), self.assertRaises(hs.HybridSearchInputError):
                    hs.search_policy_context(bad.get("query", "금리 질문"), bad.get("filters"), collection=COLLECTION)
        finally:
            for patch in patches:
                patch.stop()
        self.assertEqual(created, {"clients": [], "drivers": []})


# ---------------------------------------------------------------------------
# 8) 타임아웃·오류 분류 (서버 장애 vs 코드 버그)
# ---------------------------------------------------------------------------
@unittest.skipUnless(HAS_QDRANT, "qdrant-client 미설치 → NOT_TESTED")
class TestTimeoutsAndErrorClassification(unittest.TestCase):
    def _qdrant_failure(self, error):
        client = TrackingClient(make_memory_client(), fail_with=error)
        return hs.search_policy_evidence("금리 질문", client=client, embedder=fake_embedder, collection=COLLECTION)

    def test_qdrant_timeout_and_unavailable_and_auth(self):
        import httpx
        from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
        cases = [(ResponseHandlingException(httpx.ReadTimeout("가상 시간 초과")), "timeout"),
                 (ResponseHandlingException(httpx.ConnectError("가상 접속 거부")), "unavailable"),
                 (UnexpectedResponse(401, "Unauthorized", b"", httpx.Headers()), "auth"),
                 (UnexpectedResponse(500, "Server Error", b"", httpx.Headers()), "server"),
                 (TimeoutError("가상"), "timeout")]
        for error, kind in cases:
            with self.subTest(error=type(error).__name__, kind=kind):
                with self.assertRaises(hs.HybridSearchBackendError) as context:
                    self._qdrant_failure(error)
                self.assertEqual((context.exception.backend, context.exception.kind), ("qdrant", kind))

    def test_qdrant_code_bugs_are_not_wrapped(self):
        for error in (TypeError("가상 코드 버그"), KeyError("payload"), AttributeError("x")):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                self._qdrant_failure(error)

    def test_make_qdrant_client_passes_timeout_and_hides_settings(self):
        captured = {}

        class FakeQdrantClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)
        with mock.patch.object(store, "resolve_connection_settings", return_value=("http://127.0.0.1:6333", "sample_key")), \
                mock.patch("qdrant_client.QdrantClient", FakeQdrantClient):
            hs.make_qdrant_client(timeout=3)
        self.assertEqual(captured["timeout"], 3)
        self.assertEqual(hs.QDRANT_TIMEOUT_SECONDS, 10)
        with mock.patch.object(store, "resolve_connection_settings",
                               side_effect=store.ConnectionSettingsError("QDRANT_URL 누락 (sample_only)")):
            with self.assertRaises(hs.HybridSearchBackendError) as context:
                hs.make_qdrant_client()
        self.assertEqual(context.exception.kind, "config")


class TestNeo4jTimeoutsAndErrors(unittest.TestCase):
    def _hydrate(self, code):
        from neo4j import exceptions as ne
        return ne.Neo4jError._hydrate_neo4j(code=code, message="sample_only")

    def test_neo4j_errors_are_classified(self):
        from neo4j import exceptions as ne
        cases = [(self._hydrate("Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration"), "timeout"),
                 (ne.ServiceUnavailable("가상 접속 불가"), "unavailable"),
                 (self._hydrate("Neo.ClientError.Security.Unauthorized"), "auth"),
                 (self._hydrate("Neo.TransientError.General.MemoryPoolOutOfMemoryError"), "server"),
                 (ConnectionError("가상"), "unavailable")]
        for error, kind in cases:
            runner = FakeHybridRunner()
            runner.fail_with = error
            with self.subTest(kind=kind), self.assertRaises(hs.HybridSearchBackendError) as context:
                hs.get_policy_graph_context(["POL_S1"], runner=runner)
            self.assertEqual((context.exception.backend, context.exception.kind), ("neo4j", kind))

    def test_neo4j_code_bugs_are_not_wrapped(self):
        from neo4j import exceptions as ne
        for error in (self._hydrate("Neo.ClientError.Statement.SyntaxError"), KeyError("policy_id"), TypeError("x")):
            runner = FakeHybridRunner()
            runner.fail_with = error
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                hs.get_policy_graph_context(["POL_S1"], runner=runner)
        self.assertTrue(issubclass(ne.CypherSyntaxError, ne.ClientError))

    def test_read_only_runner_applies_query_timeout_and_refuses_writes(self):
        from neo4j import Query
        seen = []

        class FakeResult:
            def __iter__(self):
                return iter([])

            def consume(self):
                return None

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def run(self, query, parameters):
                seen.append((query, parameters))
                return FakeResult()

        class FakeDriver:
            def session(self, **kwargs):
                seen.append(kwargs)
                return FakeSession()
        runner = hs.ReadOnlyTimedRunner(FakeDriver(), query_timeout=2.5)
        runner.read_rows(hs.POLICY_CONTEXT_QUERY, {"policy_ids": ["POL_S1"]})
        self.assertEqual(seen[0]["default_access_mode"], "READ")
        query, parameters = seen[1]
        self.assertIsInstance(query, Query)
        self.assertEqual((query.timeout, query.text, parameters), (2.5, hs.POLICY_CONTEXT_QUERY, {"policy_ids": ["POL_S1"]}))
        self.assertEqual(hs.NEO4J_QUERY_TIMEOUT_SECONDS, 10.0)
        with self.assertRaises(PermissionError):
            runner.write_batch("UNWIND $rows AS row RETURN 1 AS merged", [], expected=0)
        with self.assertRaises(PermissionError):
            runner.run_schema("CREATE INDEX x IF NOT EXISTS FOR (n:Policy) ON (n.category)")

    def test_notifications_are_capped(self):
        class Summary:
            summary_notifications = [type("N", (), {"code": "Neo.ClientNotification.Statement.UnknownLabelWarning",
                                                    "title": "sample_only", "severity_level": None, "category": None})()] * 3

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def run(self, query, parameters):
                return type("R", (), {"__iter__": lambda self: iter([]), "consume": lambda self: Summary()})()
        driver = type("D", (), {"session": lambda self, **kwargs: FakeSession()})()
        runner = hs.ReadOnlyTimedRunner(driver, max_notifications=5)
        for _ in range(3):
            runner.read_rows("MATCH (n:Unknown) RETURN count(n) AS count")
        self.assertEqual((len(runner.notifications), runner.dropped_notifications), (5, 4))

    def test_driver_creation_failure_is_config_error_without_secrets(self):
        secret = "sample_only_secret_pw"
        env = {"NEO4J_URI": f"neo4j://neo4j:{secret}@bad host", "NEO4J_USER": "neo4j", "NEO4J_PASSWORD": secret}
        with mock.patch.dict("os.environ", env), \
                mock.patch("neo4j.GraphDatabase.driver", side_effect=ValueError(f"Invalid URI neo4j://neo4j:{secret}@bad")):
            with self.assertRaises(hs.HybridSearchBackendError) as context:
                with hs.open_neo4j_runner():
                    pass
        self.assertEqual((context.exception.backend, context.exception.kind), ("neo4j", "config"))
        self.assertNotIn(secret, str(context.exception))

    def test_connectivity_failure_closes_driver(self):
        from neo4j import exceptions as ne
        driver = mock.Mock()
        driver.verify_connectivity.side_effect = ne.ServiceUnavailable("가상 접속 불가 (sample_only)")
        env = {"NEO4J_URI": "bolt://127.0.0.1:1", "NEO4J_USER": "neo4j", "NEO4J_PASSWORD": "sample_only_pw"}
        with mock.patch.dict("os.environ", env), mock.patch("neo4j.GraphDatabase.driver", return_value=driver):
            with self.assertRaises(hs.HybridSearchBackendError) as context:
                with hs.open_neo4j_runner():
                    pass
        self.assertEqual(context.exception.kind, "unavailable")
        driver.close.assert_called_once()

    def test_safe_message_redacts_secrets_and_url_userinfo(self):
        with mock.patch.dict("os.environ", {"QDRANT_API_KEY": "sample_only_api_key"}):
            text = hs._safe_message(RuntimeError("key=sample_only_api_key url=http://user:pw@127.0.0.1:6333/x"))
        self.assertNotIn("sample_only_api_key", text)
        self.assertNotIn("user:pw", text)


# ---------------------------------------------------------------------------
# 9) 데이터 품질 경고 (판정·순위를 바꾸지 않음)
# ---------------------------------------------------------------------------
def _context_with_tree(tree, policy=None):
    return {"policy_id": "POL_Q", "policy": policy or {}, "documents": [],
            "eligibility": {"evaluated": False, "condition_tree": [tree], "unknown_logic_groups": [],
                            "structure_issues": []}}


def _cond(cid, review="verified"):
    return {"condition_id": cid, "relationship": {"review_status": review}}


class TestDataQualityWarnings(unittest.TestCase):
    def setUp(self):
        self.context = hs.get_policy_graph_context(["POL_S1", "POL_S2"], runner=FakeHybridRunner())["policies"]

    def _codes(self, quality):
        return [warning["code"] for warning in quality["warnings"]]

    def test_unknown_and_extracted_warnings(self):
        unknown = hs.assess_policy_data_quality(self.context["POL_S2"])
        self.assertIn(hs.QUALITY_UNKNOWN_CONDITION, self._codes(unknown))
        self.assertIn("제한 없음", unknown["warnings"][0]["message"])
        extracted = hs.assess_policy_data_quality(self.context["POL_S1"])
        warning = [w for w in extracted["warnings"] if w["code"] == hs.QUALITY_EXTRACTED_NOT_VERIFIED][0]
        self.assertEqual(warning["count"], 1)
        self.assertNotIn(hs.QUALITY_HOMELESS_PROMOTED, self._codes(extracted))   # 정상 샘플은 의심 패턴 없음
        self.assertEqual(extracted["review_status_counts"]["extracted"], 1)

    def test_suspected_gov24_patterns_are_flagged_not_judged(self):
        tree = {"group_id": "GRP_Q_ROOT", "logic": "AND", "relationship": {"review_status": "extracted"},
                "conditions": [_cond("COND_AGE_19_120", "extracted"), _cond(hs.HOMELESS_CONDITION_ID, "extracted")],
                "groups": [{"group_id": "GRP_Q_OR_HH", "logic": "OR", "relationship": {}, "groups": [],
                            "conditions": [_cond("COND_HH_1인가구", "extracted"), _cond("COND_HH_다자녀가구", "extracted")]},
                           {"group_id": "GRP_Q_OR_INC", "logic": "OR", "relationship": {}, "groups": [],
                            "conditions": [_cond(cid, "extracted") for cid in sorted(hs.INCOME_BAND_CONDITION_IDS)]}]}
        context = _context_with_tree(tree, policy={"eligibility": "가상 추출 텍스트 (sample_only)"})
        before = copy.deepcopy(context)
        quality = hs.assess_policy_data_quality(context)
        self.assertEqual(context, before)                                          # 입력을 바꾸지 않는다
        codes = self._codes(quality)
        self.assertIn(hs.QUALITY_HOMELESS_PROMOTED, codes)
        self.assertIn(hs.QUALITY_INCOME_ALL_BANDS, codes)
        for warning in quality["warnings"]:
            if warning["code"].startswith("SUSPECTED"):
                self.assertEqual(warning["level"], "suspected")
                self.assertIn("확정 아님", warning["message"])
        self.assertEqual(quality["graph_text_fields"], ["eligibility"])
        self.assertNotIn("VERIFIED", json.dumps(quality, ensure_ascii=False).upper().replace("NOT_VERIFIED", ""))

    def test_homeless_alone_or_partial_income_is_not_flagged(self):
        tree = {"group_id": "GRP_Q_ROOT", "logic": "AND", "relationship": {},
                "conditions": [_cond(hs.HOMELESS_CONDITION_ID)],
                "groups": [{"group_id": "GRP_Q_OR_INC", "logic": "OR", "relationship": {}, "groups": [],
                            "conditions": [_cond(cid) for cid in sorted(hs.INCOME_BAND_CONDITION_IDS)[:4]]}]}
        self.assertEqual(hs.assess_policy_data_quality(_context_with_tree(tree))["warnings"], [])

    def test_missing_graph_policy_warning(self):
        self.assertEqual(self._codes(hs.assess_policy_data_quality(None)), [hs.QUALITY_GRAPH_MISSING])

    def test_combine_keeps_existing_keys_order_and_adds_quality(self):
        search = {"query": "q", "hits": [_hit(1, 0.9, "CHK_B2_DOC_S2_C001", "POL_S2", "DOC_S2"),
                                         _hit(2, 0.8, "CHK_B2_DOC_S1_C001", "POL_S1", "DOC_S1"),
                                         _hit(3, 0.7, "CHK_B2_DOC_S3_C001", "POL_S3", "DOC_S3")]}
        context = hs.get_policy_graph_context(["POL_S1", "POL_S2", "POL_S3"], runner=FakeHybridRunner())
        combined = hs.combine_policy_evidence(search, context)
        old_top_keys = {"query", "filters", "k", "collection", "query_template", "policies", "unlinked_hits",
                        "missing_policy_ids", "warnings", "latency_seconds", "notes"}
        old_policy_keys = {"policy_id", "policy_name", "best_rank", "best_score", "evidence", "graph_status", "graph",
                           "evidence_document_check"}
        self.assertLessEqual(old_top_keys, set(combined))
        self.assertEqual([p["policy_id"] for p in combined["policies"]], ["POL_S2", "POL_S1", "POL_S3"])
        for entry in combined["policies"]:
            self.assertLessEqual(old_policy_keys, set(entry))
            self.assertIn("data_quality", entry)
            if entry["graph"]:
                self.assertFalse(entry["graph"]["eligibility"]["evaluated"])
        self.assertEqual(combined["data_quality_summary"][hs.QUALITY_GRAPH_MISSING], 1)
        self.assertIn("unknown", combined["notes"])
        self.assertIn("score", combined["notes"])
        json.dumps(combined, ensure_ascii=False)      # 직렬화 가능


if __name__ == "__main__":
    unittest.main(verbosity=2)
