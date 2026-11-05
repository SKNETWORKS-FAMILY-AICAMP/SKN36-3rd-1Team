"""
1단계: 인계 데이터 오프라인 검증 테스트 (DB 서버 없이 실행)

실행 방법 (프로젝트 루트에서):
    python -m unittest tests/unit/test_handoff_data.py -v
pytest가 설치되어 있다면 아래도 동일하게 동작한다.
    pytest tests/unit/test_handoff_data.py -v

무엇을 확인하는가?
1) 인계 파일이 manifest와 같은 내용인지 (체크섬)
2) Qdrant 청크 557건의 ID·필수 필드·Point ID 규칙
3) Neo4j 노드 1,034개 / 관계 3,615개가 "레이블 + 고유 ID" 규칙으로 1:1 연결되는지
4) Qdrant 청크와 Neo4j 노드가 같은 policy_id·document_id로 이어지는지 (하이브리드 검색의 전제)
5) 비정상 청크 27건 선택 제외, 페이지 번호 규칙, 인접 청크 조회 가능 여부
"""

import re
import sys
import unittest
import uuid
from collections import Counter, defaultdict
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가한다.
# (tests/ 폴더에 __init__.py가 없어서, 그대로 두면 database 모듈을 찾지 못할 수 있다.)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import handoff_data as hd  # noqa: E402  (경로 추가 후 import 해야 함)

# data/handoff/README.md 에 적힌 레이블별·관계별 기대 건수
EXPECTED_NODES_BY_LABEL = {
    "Policy": 237,
    "Document": 246,
    "Organization": 116,
    "Region": 18,
    "Category": 5,
    "LifeStage": 6,
    "TargetGroup": 11,
    "ConditionGroup": 343,
    "EligibilityCondition": 52,
}
EXPECTED_RELATIONSHIPS_BY_TYPE = {
    "REQUIRES_CONDITION": 735,
    "CONTAINS_CONDITION": 735,
    "TARGETS": 424,
    "APPLIES_TO_LIFESTAGE": 419,
    "SUPPORTED_BY": 246,
    "AVAILABLE_IN": 238,
    "ADMINISTERED_BY": 237,
    "HAS_CATEGORY": 237,
    "HAS_ROOT_CONDITION_GROUP": 237,
    "CONTAINS_GROUP": 106,
    "MODIFIES_CONDITION_OF": 1,
}

# data/handoff/data_contract.md 의 Chunk 필수 필드
REQUIRED_CHUNK_FIELDS = [
    "chunk_id", "policy_id", "document_id", "policy_name", "category", "region",
    "life_stages", "section_title", "text", "source_url", "verification_status",
]
CHUNK_ID_CONTRACT = re.compile(r"^(DOC_[A-Za-z0-9_]+_(P[0-9]+_)?C[0-9]+|CHK_[A-Za-z0-9_]+)$")


class TestManifest(unittest.TestCase):
    def test_all_files_match_manifest_checksum(self):
        results = hd.verify_manifest_checksums()
        failed = [r["file"] for r in results if not r["matched"]]
        self.assertEqual(failed, [], f"체크섬 불일치 파일: {failed}")


class TestPayloadChunks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chunks = hd.load_payload_chunks()

    def test_chunk_count(self):
        self.assertEqual(len(self.chunks), hd.EXPECTED_CHUNK_COUNT)

    def test_chunk_ids_unique_and_follow_contract(self):
        chunk_ids = [c["chunk_id"] for c in self.chunks]
        self.assertEqual(len(set(chunk_ids)), len(chunk_ids), "중복 chunk_id 존재")
        bad = [cid for cid in chunk_ids if not CHUNK_ID_CONTRACT.match(cid)]
        self.assertEqual(bad, [], f"계약과 다른 chunk_id: {bad[:5]}")

    def test_point_id_is_uuid5_of_chunk_id(self):
        # 같은 chunk_id는 항상 같은 point_id → 다시 적재해도 같은 포인트를 덮어쓴다(중복 방지).
        wrong = [
            c["chunk_id"] for c in self.chunks
            if c["point_id"] != str(uuid.uuid5(uuid.NAMESPACE_DNS, c["chunk_id"]))
        ]
        self.assertEqual(wrong, [])
        self.assertEqual(len({c["point_id"] for c in self.chunks}), len(self.chunks))

    def test_required_fields_present(self):
        missing = [
            (c["chunk_id"], field) for c in self.chunks for field in REQUIRED_CHUNK_FIELDS
            if c.get(field) in (None, "", [])
        ]
        self.assertEqual(missing, [], f"필수 필드 누락: {missing[:5]}")

    def test_id_prefixes(self):
        self.assertTrue(all(c["policy_id"].startswith("POL_") for c in self.chunks))
        self.assertTrue(all(c["document_id"].startswith("DOC_") for c in self.chunks))

    def test_counts_of_linked_policies_and_documents(self):
        self.assertEqual(len({c["policy_id"] for c in self.chunks}), hd.EXPECTED_POLICY_COUNT)
        self.assertEqual(len({c["document_id"] for c in self.chunks}), hd.EXPECTED_DOCUMENT_COUNT)


class TestNeo4jHandoff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = hd.load_neo4j_nodes()
        cls.relationships = hd.load_neo4j_relationships()

    def test_node_counts_by_label(self):
        actual = {label: len(nodes) for label, nodes in self.nodes.items()}
        self.assertEqual(actual, EXPECTED_NODES_BY_LABEL)
        self.assertEqual(sum(actual.values()), hd.EXPECTED_NODE_COUNT)

    def test_node_ids_unique_within_label(self):
        for label, nodes in self.nodes.items():
            ids = [n[hd.LABEL_ID_FIELDS[label]] for n in nodes]
            with self.subTest(label=label):
                self.assertEqual(len(set(ids)), len(ids), f"{label} 고유 ID 중복")

    def test_node_properties_are_neo4j_compatible(self):
        # Neo4j 속성에는 dict·중첩 리스트·null을 저장할 수 없다. (SET n += row 실패 방지)
        problems = []
        for label, nodes in self.nodes.items():
            for node in nodes:
                for key, value in node.items():
                    if value is None or isinstance(value, dict):
                        problems.append((label, key))
                    elif isinstance(value, list) and any(isinstance(x, (dict, list)) for x in value):
                        problems.append((label, key))
        self.assertEqual(problems, [])

    def test_relationship_count(self):
        self.assertEqual(len(self.relationships), hd.EXPECTED_RELATIONSHIP_COUNT)

    def test_relationships_match_one_to_one_with_label_specs(self):
        # 수정 로더 규칙(레이블 + 고유 ID)으로 매칭하면 3,615건이 모두 정확히 연결돼야 한다.
        report = hd.match_relationships_offline(self.nodes, self.relationships)
        self.assertEqual(report["unknown_relation"], [])
        self.assertEqual(report["missing_source"], [])
        self.assertEqual(report["missing_target"], [])
        self.assertEqual(report["duplicate_triples"], 0)
        self.assertEqual(report["matched"], hd.EXPECTED_RELATIONSHIP_COUNT)
        self.assertEqual(report["matched_by_relation"], EXPECTED_RELATIONSHIPS_BY_TYPE)

    def test_legacy_label_less_matching_is_unsafe(self):
        """
        인계 로더의 '레이블 없는 OR 매칭'을 그대로 흉내 내어, 왜 실행하면 안 되는지 기록한다.
        이 테스트가 PASS라는 것은 "기존 로더는 관계를 과다 생성한다"는 결함이 재현됐다는 뜻이다.
        """
        legacy_source_fields = ["policy_id", "document_id", "group_id", "agency_id", "region_id"]
        legacy_target_fields = legacy_source_fields + ["category_id", "stage_id", "condition_id"]
        all_nodes = [node for nodes in self.nodes.values() for node in nodes]

        def legacy_match_count(value, fields):
            return sum(1 for node in all_nodes if any(node.get(f) == value for f in fields))

        legacy_edges = sum(
            legacy_match_count(r["source_id"], legacy_source_fields)
            * legacy_match_count(r["target_id"], legacy_target_fields)
            for r in self.relationships
        )
        self.assertGreater(legacy_edges, hd.EXPECTED_RELATIONSHIP_COUNT)


class TestCrossDatabaseLinks(unittest.TestCase):
    """Qdrant 청크와 Neo4j 노드가 같은 ID로 이어지는지 확인한다. 하이브리드 검색의 전제 조건이다."""

    @classmethod
    def setUpClass(cls):
        cls.chunks = hd.load_payload_chunks()
        nodes = hd.load_neo4j_nodes()
        cls.policies = {p["policy_id"]: p for p in nodes["Policy"]}
        cls.documents = {d["document_id"]: d for d in nodes["Document"]}

    def test_every_chunk_policy_exists_in_graph(self):
        missing = {c["policy_id"] for c in self.chunks} - set(self.policies)
        self.assertEqual(missing, set())

    def test_every_chunk_document_exists_in_graph(self):
        missing = {c["document_id"] for c in self.chunks} - set(self.documents)
        self.assertEqual(missing, set())

    def test_chunk_and_document_agree_on_policy_and_url(self):
        policy_mismatch = [
            c["chunk_id"] for c in self.chunks
            if self.documents[c["document_id"]]["policy_id"] != c["policy_id"]
        ]
        url_mismatch = [
            c["chunk_id"] for c in self.chunks
            if self.documents[c["document_id"]]["source_url"] != c["source_url"]
        ]
        self.assertEqual(policy_mismatch, [])
        self.assertEqual(url_mismatch, [])

    def test_every_graph_policy_and_document_has_chunks(self):
        self.assertEqual(set(self.policies) - {c["policy_id"] for c in self.chunks}, set())
        self.assertEqual(set(self.documents) - {c["document_id"] for c in self.chunks}, set())


class TestNoiseChunkSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chunks = hd.load_payload_chunks()

    def test_short_candidates_equal_reviewed_list(self):
        # 데이터가 바뀌어 후보가 달라지면 FAIL → 사람이 다시 검토해야 한다는 신호.
        candidates = hd.find_short_chunk_candidates(self.chunks)
        self.assertEqual(candidates, sorted(hd.REVIEWED_NOISE_CHUNKS))
        self.assertEqual(len(candidates), 27)

    def test_select_with_exclusion(self):
        selected, excluded = hd.select_chunks_for_embedding(self.chunks, exclude_noise=True)
        self.assertEqual(len(selected), 530)
        self.assertEqual(len(excluded), 27)
        self.assertEqual(len(selected) + len(excluded), len(self.chunks))

    def test_select_without_exclusion_keeps_all(self):
        selected, excluded = hd.select_chunks_for_embedding(self.chunks, exclude_noise=False)
        self.assertEqual(len(selected), hd.EXPECTED_CHUNK_COUNT)
        self.assertEqual(excluded, [])

    def test_selection_does_not_modify_source(self):
        before = [dict(c) for c in self.chunks]
        hd.select_chunks_for_embedding(self.chunks, exclude_noise=True)
        self.assertEqual(self.chunks, before)


class TestPageNumbers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chunks = hd.load_payload_chunks()
        processed_path = hd.PROJECT_ROOT / "data" / "processed" / "chunks.jsonl"
        cls.processed_pages = {c["chunk_id"]: c["page_number"] for c in hd.read_jsonl(processed_path)}

    def test_verified_pdf_pages_match_processed_file(self):
        # chunk_id의 _P03_ 과, 반석님 정제 파일(processed/chunks.jsonl)의 page_number가 같아야 한다.
        checked = 0
        for chunk in self.chunks:
            page = hd.get_verified_page_number(chunk)
            if page is None:
                continue
            checked += 1
            self.assertEqual(page, self.processed_pages[chunk["chunk_id"]], chunk["chunk_id"])
        self.assertEqual(checked, 48)  # DOC_LH_001 46건 + DOC_NHUF_002 2건

    def test_unverified_documents_have_no_page(self):
        lh_002 = [c for c in self.chunks if c["document_id"] == "DOC_LH_002"]
        self.assertTrue(lh_002)
        self.assertTrue(all(hd.get_verified_page_number(c) is None for c in lh_002))
        api_chunk = next(c for c in self.chunks if c["document_id"].startswith("DOC_GOV_"))
        self.assertIsNone(hd.get_verified_page_number(api_chunk))


class TestNeighborChunks(unittest.TestCase):
    def test_chunk_sequence_is_contiguous_per_document_page(self):
        # 순번이 1, 2, 3 ... 으로 빠짐없이 이어져야 "앞·뒤 청크"를 chunk_id로 찾을 수 있다.
        # (조건·예외 문장이 청크 경계에서 잘린 경우 인접 청크를 함께 조회하기 위한 전제)
        sequences = defaultdict(list)
        for chunk in hd.load_payload_chunks():
            parsed = hd.parse_chunk_id(chunk["chunk_id"])
            self.assertEqual(parsed["document_id"], chunk["document_id"], chunk["chunk_id"])
            sequences[(parsed["document_id"], parsed["page_in_id"])].append(parsed["seq"])
        gaps = {key: sorted(v) for key, v in sequences.items() if sorted(v) != list(range(1, len(v) + 1))}
        self.assertEqual(gaps, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
